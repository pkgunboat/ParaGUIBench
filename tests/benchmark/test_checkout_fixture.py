"""WebMall checkout 合成 fixture 的发布与任务引用回归测试。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "benchmark" / "manifests" / "release-v1.json"
VALIDATOR_PATH = REPO_ROOT / "scripts" / "benchmark" / "validate_release.py"
FIXTURE_ID = "webmall.checkout-profile.synthetic-public.v1"
CHECKOUT_TASK_GLOBS = (
    "Operation-OnlineShopping-Checkout-*.json",
    "Operation-OnlineShopping-EndToEnd-*.json",
)
FIXTURE_SCHEMA_PATH = (
    REPO_ROOT / "benchmark" / "schemas" / "webmall-checkout-fixture-v1.schema.json"
)
TASK_SCHEMA_PATH = (
    REPO_ROOT / "benchmark" / "schemas" / "webmall-checkout-task-v1.schema.json"
)


def _load_json(path: Path) -> dict[str, object]:
    """读取测试所需的 JSON object。

    输入参数：
        path：仓库中的 JSON 文件路径。
    输出返回值：
        已解析的 JSON object；根节点不是 object 时断言失败。
    """

    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    """计算文件内容的 SHA-256。

    输入参数：
        path：待校验的 fixture 文件路径。
    输出返回值：
        小写十六进制 SHA-256 字符串。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_validator() -> ModuleType:
    """加载独立 release validator 脚本作为公开校验接口。

    输入参数：
        无；脚本路径由仓库根目录推导。
    输出返回值：
        已加载且可调用 ``validate_release`` 的模块。
    """

    spec = importlib.util.spec_from_file_location(
        "paraguibench_checkout_fixture_validator",
        VALIDATOR_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 release validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_pins_one_versioned_public_reference_only_checkout_fixture() -> None:
    """验证发布清单固定唯一、公开合成且仅供引用的 checkout fixture。

    输入参数：
        无；读取正式 release-v1 清单及其 fixture 引用。
    输出返回值：
        无；fixture 身份、分类、策略、版本与摘要必须全部可验证。
    """

    manifest = _load_json(MANIFEST_PATH)
    checkout_entries = [
        entry
        for entry in manifest["fixtures"]
        if entry["fixture_id"] == FIXTURE_ID
    ]
    assert len(checkout_entries) == 1

    entry = checkout_entries[0]
    fixture_path = REPO_ROOT / entry["path"]
    fixture = _load_json(fixture_path)

    assert fixture["fixture_id"] == FIXTURE_ID
    assert fixture["schema_version"] == 1
    assert fixture["data_classification"] == "synthetic_public_test_data"
    assert fixture["task_storage_policy"] == "reference_only"
    assert entry["sha256"] == _sha256(fixture_path)


def test_checkout_tasks_reference_fixture_without_embedding_profile() -> None:
    """验证 16 个任务只保存模板与 fixture 引用，不内嵌 profile。

    输入参数：
        无；读取正式 checkout 与 end-to-end canonical task。
    输出返回值：
        无；任务数量、模板 token、引用身份和禁止字段必须符合协议。
    """

    task_root = REPO_ROOT / "benchmark" / "tasks"
    task_paths = sorted(
        path
        for pattern in CHECKOUT_TASK_GLOBS
        for path in task_root.glob(pattern)
    )
    assert len(task_paths) == 16

    fixture = _load_json(
        REPO_ROOT / "benchmark" / "fixtures" / "webmall" / "checkout-profile-v1.json"
    )
    profile = fixture["profile"]
    forbidden_fragments = {
        profile["shipping_address"]["name"],
        profile["shipping_address"]["email"],
        profile["payment_method"]["card_number"],
        profile["payment_method"]["cvv"],
        profile["payment_method"]["expiry_date"],
    }

    for task_path in task_paths:
        task = _load_json(task_path)
        assert "instruction" not in task
        assert "user_details" not in task
        assert "payment_info" not in task
        assert task["instruction_template"].count("{{checkout_profile}}") == 1
        assert task["fixture_ref"] == {
            "binding": "checkout_profile",
            "fixture_id": FIXTURE_ID,
        }
        serialized_task = json.dumps(task, ensure_ascii=False)
        assert all(
            fragment not in serialized_task for fragment in forbidden_fragments
        )


def test_validator_rejects_fixture_without_public_synthetic_classification(
    tmp_path: Path,
) -> None:
    """验证哈希同步也不能绕过 fixture 数据分类门禁。

    输入参数：
        tmp_path：pytest 提供的临时仓库根目录。
    输出返回值：
        无；分类不是 ``synthetic_public_test_data`` 时必须拒绝发布。
    """

    shutil.copytree(REPO_ROOT / "benchmark", tmp_path / "benchmark")
    fixture_path = (
        tmp_path
        / "benchmark"
        / "fixtures"
        / "webmall"
        / "checkout-profile-v1.json"
    )
    fixture = _load_json(fixture_path)
    fixture["data_classification"] = "unclassified"
    fixture_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )
    manifest = _load_json(manifest_path)
    manifest["fixtures"][0]["sha256"] = _sha256(fixture_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    validator = _load_validator()
    result = validator.validate_release(tmp_path, manifest_path)

    assert not result.ok
    assert any(
        "synthetic_public_test_data" in error for error in result.errors
    )


def test_validator_rejects_checkout_task_with_embedded_payment_data(
    tmp_path: Path,
) -> None:
    """验证任务哈希同步也不能重新引入内嵌 payment data。

    输入参数：
        tmp_path：pytest 提供的临时仓库根目录。
    输出返回值：
        无；checkout task 出现 ``payment_info`` 时必须拒绝发布。
    """

    shutil.copytree(REPO_ROOT / "benchmark", tmp_path / "benchmark")
    task_id = "Operation-OnlineShopping-Checkout-001"
    task_path = tmp_path / "benchmark" / "tasks" / f"{task_id}.json"
    task = _load_json(task_path)
    task["payment_info"] = {"card_number": "synthetic-inline-value"}
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )
    manifest = _load_json(manifest_path)
    task_entry = next(
        entry for entry in manifest["tasks"] if entry["task_id"] == task_id
    )
    task_entry["sha256"] = _sha256(task_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    validator = _load_validator()
    result = validator.validate_release(tmp_path, manifest_path)

    assert not result.ok
    assert any("不得内嵌" in error for error in result.errors)


def test_validator_rejects_fixture_without_reference_only_policy(
    tmp_path: Path,
) -> None:
    """验证 fixture 必须声明 task 侧仅保存引用。

    输入参数：
        tmp_path：pytest 提供的临时仓库根目录。
    输出返回值：
        无；``task_storage_policy`` 不是 ``reference_only`` 时拒绝发布。
    """

    shutil.copytree(REPO_ROOT / "benchmark", tmp_path / "benchmark")
    fixture_path = (
        tmp_path
        / "benchmark"
        / "fixtures"
        / "webmall"
        / "checkout-profile-v1.json"
    )
    fixture = _load_json(fixture_path)
    fixture["task_storage_policy"] = "embedded_allowed"
    fixture_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )
    manifest = _load_json(manifest_path)
    manifest["fixtures"][0]["sha256"] = _sha256(fixture_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    validator = _load_validator()
    result = validator.validate_release(tmp_path, manifest_path)

    assert not result.ok
    assert any("reference_only" in error for error in result.errors)


def test_checkout_fixture_and_task_reference_have_versioned_json_schemas() -> None:
    """验证 fixture 与任务引用协议均由版本化 JSON Schema 描述。

    输入参数：
        无；读取 fixture 的 ``$schema`` 及公开 task schema。
    输出返回值：
        无；schema 必须固定分类、存储策略、模板与 fixture 身份。
    """

    fixture_path = (
        REPO_ROOT
        / "benchmark"
        / "fixtures"
        / "webmall"
        / "checkout-profile-v1.json"
    )
    fixture = _load_json(fixture_path)
    resolved_schema_path = (fixture_path.parent / fixture["$schema"]).resolve()
    assert resolved_schema_path == FIXTURE_SCHEMA_PATH.resolve()

    fixture_schema = _load_json(FIXTURE_SCHEMA_PATH)
    fixture_properties = fixture_schema["properties"]
    assert fixture_schema["$schema"].endswith("draft/2020-12/schema")
    assert fixture_properties["schema_version"]["const"] == 1
    assert fixture_properties["fixture_id"]["const"] == FIXTURE_ID
    assert (
        fixture_properties["data_classification"]["const"]
        == "synthetic_public_test_data"
    )
    assert (
        fixture_properties["task_storage_policy"]["const"]
        == "reference_only"
    )

    task_schema = _load_json(TASK_SCHEMA_PATH)
    assert set(task_schema["required"]) == {
        "instruction_template",
        "fixture_ref",
    }
    fixture_ref_properties = task_schema["properties"]["fixture_ref"][
        "properties"
    ]
    assert fixture_ref_properties["binding"]["const"] == "checkout_profile"
    assert fixture_ref_properties["fixture_id"]["const"] == FIXTURE_ID


def test_validator_rejects_incomplete_checkout_fixture_profile(
    tmp_path: Path,
) -> None:
    """验证 fixture 缺少支付结构时不能进入正式发布。

    输入参数：
        tmp_path：pytest 提供的临时仓库根目录。
    输出返回值：
        无；即使同步文件摘要，缺少 ``payment_method`` 也必须失败。
    """

    shutil.copytree(REPO_ROOT / "benchmark", tmp_path / "benchmark")
    fixture_path = (
        tmp_path
        / "benchmark"
        / "fixtures"
        / "webmall"
        / "checkout-profile-v1.json"
    )
    fixture = _load_json(fixture_path)
    del fixture["profile"]["payment_method"]
    fixture_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )
    manifest = _load_json(manifest_path)
    manifest["fixtures"][0]["sha256"] = _sha256(fixture_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    validator = _load_validator()
    result = validator.validate_release(tmp_path, manifest_path)

    assert not result.ok
    assert any("payment_method" in error for error in result.errors)


def test_validator_rejects_checkout_fixture_with_deliverable_email(
    tmp_path: Path,
) -> None:
    """验证公开合成 fixture 只能使用保留的不可投递邮箱域。

    输入参数：
        tmp_path：pytest 提供的临时仓库根目录。
    输出返回值：
        无；非 ``example.invalid`` 邮箱即使同步摘要也必须失败。
    """

    shutil.copytree(REPO_ROOT / "benchmark", tmp_path / "benchmark")
    fixture_path = (
        tmp_path
        / "benchmark"
        / "fixtures"
        / "webmall"
        / "checkout-profile-v1.json"
    )
    fixture = _load_json(fixture_path)
    fixture["profile"]["shipping_address"]["email"] = (
        "synthetic-user@example.com"
    )
    fixture_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )
    manifest = _load_json(manifest_path)
    manifest["fixtures"][0]["sha256"] = _sha256(fixture_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    validator = _load_validator()
    result = validator.validate_release(tmp_path, manifest_path)

    assert not result.ok
    assert any("example.invalid" in error for error in result.errors)


def test_validator_rejects_unknown_checkout_payment_number(
    tmp_path: Path,
) -> None:
    """验证 v1 fixture 不能接受任意数字串作为测试支付号。

    输入参数：
        tmp_path：pytest 提供的临时仓库根目录。
    输出返回值：
        无；偏离固定公开测试号时即使同步摘要也必须失败。
    """

    shutil.copytree(REPO_ROOT / "benchmark", tmp_path / "benchmark")
    fixture_path = (
        tmp_path
        / "benchmark"
        / "fixtures"
        / "webmall"
        / "checkout-profile-v1.json"
    )
    fixture = _load_json(fixture_path)
    fixture["profile"]["payment_method"]["card_number"] = "4000000000000000"
    fixture_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )
    manifest = _load_json(manifest_path)
    manifest["fixtures"][0]["sha256"] = _sha256(fixture_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    validator = _load_validator()
    result = validator.validate_release(tmp_path, manifest_path)

    assert not result.ok
    assert any("固定公开测试号" in error for error in result.errors)


def test_validator_rejects_release_missing_checkout_task_schema(
    tmp_path: Path,
) -> None:
    """验证统一 release validator 会检查 task schema 资产。

    输入参数：
        tmp_path：pytest 提供的临时仓库根目录。
    输出返回值：
        无；删除版本化 schema 后，即使任务和 fixture 未变也必须失败。
    """

    shutil.copytree(REPO_ROOT / "benchmark", tmp_path / "benchmark")
    (
        tmp_path
        / "benchmark"
        / "schemas"
        / "webmall-checkout-task-v1.schema.json"
    ).unlink()
    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )

    validator = _load_validator()
    result = validator.validate_release(tmp_path, manifest_path)

    assert not result.ok
    assert any(
        "webmall-checkout-task-v1.schema.json" in error
        for error in result.errors
    )
