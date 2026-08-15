"""WebMall Cart component receipt 物理闭集与当前身份门禁。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartStore,
    ObservedCartWorker,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceCaptureProof,
    build_webmall_cart_reference_receipt,
)
from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest_with_sha256,
)
from paraguibench.runtime.webmall_cart_component_receipts import (
    WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH,
    WEBMALL_CART_COMPONENT_RECEIPT_ROOT,
    WEBMALL_CART_REFERENCE_COMPONENT_ID,
    WebMallCartComponentReceiptError,
    derive_webmall_cart_component_identity,
    has_current_webmall_cart_component_receipt,
    load_trusted_webmall_cart_reference_receipt,
)
from paraguibench.runtime import webmall_cart_component_receipts as receipts_module


_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPONENT_RECEIPT_SCHEMA_PATH = (
    _REPO_ROOT
    / "benchmark/schemas/webmall-cart-reference-component-receipt-v1.schema.json"
)


def _copy_component_identity_repository(destination: Path) -> None:
    """复制足以派生 Cart task/environment/component 身份的闭集。

    输入参数：destination 为 pytest 隔离仓库根。
    输出返回值：无；仅复制正式身份算法声明的输入。
    """

    shutil.copytree(
        _REPO_ROOT / "src" / "paraguibench",
        destination / "src" / "paraguibench",
    )
    shutil.copytree(
        _REPO_ROOT / "benchmark" / "schemas",
        destination / "benchmark" / "schemas",
    )
    shutil.copytree(
        _REPO_ROOT / "benchmark" / "tasks",
        destination / "benchmark" / "tasks",
    )
    for relative_path in (
        Path("pyproject.toml"),
        Path("scripts/benchmark/runtime_support_manifest.py"),
        Path("benchmark/manifests/release-v1.json"),
        Path("benchmark/manifests/runtime-support-v1.json"),
        Path("environments/webmall/environment-manifest.json"),
        Path("environments/webmall/wp-order-evidence.php"),
        Path("environments/osworld/image-manifest.json"),
    ):
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REPO_ROOT / relative_path, target)


def _write_current_component_receipt(root: Path) -> dict[str, object]:
    """在隔离仓库写入与当前三层身份精确绑定的合成 receipt。

    输入参数：root 为已复制 component 身份闭集的仓库根。
    输出返回值：写入 receipt 的完整脱敏字段字典。
    """

    browser_path = root / "environments/osworld/image-manifest.json"
    browser_raw = json.loads(browser_path.read_text(encoding="utf-8"))
    browser_raw["extracted_image"]["sha256"] = "1" * 64
    browser_raw["extracted_image"]["status"] = "verified_reproducible_materialization"
    browser_raw["materialization"]["output_sha256"] = "1" * 64
    browser_payload = (json.dumps(browser_raw, sort_keys=True) + "\n").encode()
    browser_path.write_bytes(browser_payload)

    webmall_path = root / "environments/webmall/environment-manifest.json"
    webmall_raw = json.loads(webmall_path.read_text(encoding="utf-8"))
    webmall_raw["browser_runtime"]["image_manifest_sha256"] = hashlib.sha256(
        browser_payload
    ).hexdigest()
    # 候选验证必须从正式 ``pending`` manifest 直接产出可晋升证据；
    # 不能要求先无证据地把 manifest 声明为 live，否则 receipt 会因
    # manifest/task/environment/component 摘要变化而在写入 allowlist 前失效。
    webmall_raw["cart_reader"]["reference_live_validation_status"] = "pending"
    webmall_path.write_text(
        json.dumps(webmall_raw, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest, webmall_sha256 = load_webmall_environment_manifest_with_sha256(
        webmall_path
    )
    browser = load_osworld_image_manifest(browser_path)
    identity = derive_webmall_cart_component_identity(root)
    sweep = ("store-1", "store-2", "store-3", "store-4")
    receipt = build_webmall_cart_reference_receipt(
        manifest=manifest,
        browser_image=browser,
        webmall_manifest_sha256=webmall_sha256,
        component_revision=identity.component_identity_sha256,
        observation=CartObservationBatch(
            complete=True,
            workers=(
                ObservedCartWorker(
                    worker_id="private-candidate-worker",
                    complete=True,
                    stores=tuple(
                        ObservedCartStore(
                            logical_store_id=store_id,
                            complete=True,
                            items=(),
                        )
                        for store_id in sweep
                    ),
                ),
            ),
        ),
        capture_proof=WebMallCartReferenceCaptureProof(
            browser_context_continuity_verified=True,
            sweep_store_ids=(sweep, sweep),
            normalized_universe_match=True,
        ),
    ).to_dict()
    receipt_payload = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    receipt_root = root / WEBMALL_CART_COMPONENT_RECEIPT_ROOT
    receipt_root.mkdir(parents=True)
    (receipt_root / f"{WEBMALL_CART_REFERENCE_COMPONENT_ID}.json").write_bytes(
        receipt_payload
    )
    allowlist = {
        "schema_version": 1,
        "receipts": {
            WEBMALL_CART_REFERENCE_COMPONENT_ID: {
                "receipt_sha256": hashlib.sha256(receipt_payload).hexdigest(),
                "task_identity_sha256": identity.task_identity_sha256,
                "environment_identity_sha256": (identity.environment_identity_sha256),
                "component_identity_sha256": identity.component_identity_sha256,
            }
        },
    }
    allowlist_path = root / WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _replace_receipt_payload_and_allowlist_sha(
    root: Path,
    payload: bytes,
) -> None:
    """替换隔离 receipt 字节并同步其 allowlist 外置摘要。

    输入参数：root 为隔离仓库；payload 为对抗性候选字节。
    输出返回值：无；receipt 与 allowlist receipt SHA 一起更新，
        使测试可以绕过单纯文件摘要层并直达目标门禁。
    """

    receipt_path = (
        root
        / WEBMALL_CART_COMPONENT_RECEIPT_ROOT
        / (f"{WEBMALL_CART_REFERENCE_COMPONENT_ID}.json")
    )
    receipt_path.write_bytes(payload)
    allowlist_path = root / WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowlist["receipts"][WEBMALL_CART_REFERENCE_COMPONENT_ID]["receipt_sha256"] = (
        hashlib.sha256(payload).hexdigest()
    )
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_empty_component_allowlist_fails_closed_without_receipt_root() -> None:
    """验证初始空 allowlist 不会虚构 Cart component 实测。

    输入参数：无；读取仓库正式空 allowlist 与 receipt 根。
    输出返回值：无；数据闭集精确为空、物理目录不存在时，
        公开门禁必须返回 ``False`` 而不创建任何文件。
    """

    allowlist_path = _REPO_ROOT / WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH
    receipt_root = _REPO_ROOT / WEBMALL_CART_COMPONENT_RECEIPT_ROOT

    assert json.loads(allowlist_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "receipts": {},
    }
    assert not receipt_root.exists()
    assert has_current_webmall_cart_component_receipt(_REPO_ROOT) is False
    assert not receipt_root.exists()


def test_component_identity_excludes_generated_runtime_support_output(
    tmp_path: Path,
) -> None:
    """验证 Cart component receipt 不与它将改变的活性输出自引用。

    输入参数：tmp_path 提供可单独篡改派生清单的仓库副本。
    输出返回值：无；仅修改
        ``runtime-support-v1.json`` 不得改变三类 Cart 身份摘要。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    before = derive_webmall_cart_component_identity(root)

    (root / "benchmark/manifests/runtime-support-v1.json").write_bytes(
        b'{"synthetic":"derived-output-drift"}\n'
    )
    after = derive_webmall_cart_component_identity(root)

    assert after == before


def test_pending_manifest_candidate_receipt_promotes_current_three_layer_identity(
    tmp_path: Path,
) -> None:
    """验证 pending manifest 上生成的 candidate receipt 可直接晋升。

    输入参数：tmp_path 为隔离仓库根。
    输出返回值：无；可信 loader 不需要先把 manifest
        无证据地改成 ``live_validated``，而是返回经当前三层身份
        重新验证的脱敏 receipt，布尔门禁同时为真。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    expected = _write_current_component_receipt(root)
    manifest = json.loads(
        (root / "environments/webmall/environment-manifest.json").read_text(
            encoding="utf-8"
        )
    )

    loaded = load_trusted_webmall_cart_reference_receipt(root)

    assert manifest["cart_reader"]["reference_live_validation_status"] == "pending"
    assert loaded is not None
    assert loaded.to_dict() == expected
    assert has_current_webmall_cart_component_receipt(root) is True


def test_component_receipt_schema_closes_only_sanitized_proof_fields() -> None:
    """验证独立 component receipt schema 闭合且不允许运行时私有字段。

    输入参数：无；读取公开 Cart component receipt schema。
    输出返回值：无；字段集与脱敏 receipt 完全一致，
        且不出现 worker、origin、Cart 内容或 Agent final text 字段。
    """

    schema = json.loads(_COMPONENT_RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version",
        "receipt_kind",
        "outcome",
        "component_revision",
        "webmall_manifest_id",
        "webmall_manifest_sha256",
        "webmall_environment_id",
        "store_universe_id",
        "browser_environment_id",
        "browser_image_manifest_sha256",
        "browser_extracted_sha256",
        "browser_container_image",
        "cart_reader_protocol_id",
        "cart_evidence_protocol_id",
        "browser_context_continuity_verified",
        "sweep_store_ids",
        "normalized_universe_match",
    }

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == expected_fields
    assert set(schema["properties"]) == expected_fields
    rendered = json.dumps(schema, sort_keys=True).lower()
    for forbidden in ("worker_id", "origin", "cart_items", "final_text", "api_key"):
        assert forbidden not in rendered


def test_component_receipt_loader_rechecks_directory_closure_after_post_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 receipt 单文件 post-read 之后仍需重验物理目录闭集。

    输入参数：tmp_path 为隔离仓库；monkeypatch 在最后一次
        receipt 稳定读完后注入未授权额外文件。
    输出返回值：无；即使 allowlist 与 receipt 字节未变，
        loader 仍因最终目录闭集漂移而固定失败。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    _write_current_component_receipt(root)
    receipt_relative = WEBMALL_CART_COMPONENT_RECEIPT_ROOT / (
        f"{WEBMALL_CART_REFERENCE_COMPONENT_ID}.json"
    )
    original_read = receipts_module._read_repository_file
    receipt_read_count = 0

    def read_and_inject_extra_file(
        repo_root: Path,
        relative_path: Path,
        *,
        label: str,
        maximum_bytes: int = 16 * 1024 * 1024,
    ) -> bytes:
        """委托真实安全读，并在 receipt 第二次读后注入额外节点。

        输入参数：与生产 ``_read_repository_file`` 相同。
        输出返回值：原函数已稳定读取的字节。
        """

        nonlocal receipt_read_count
        payload = original_read(
            repo_root,
            relative_path,
            label=label,
            maximum_bytes=maximum_bytes,
        )
        if relative_path == receipt_relative:
            receipt_read_count += 1
            if receipt_read_count == 2:
                (
                    root / WEBMALL_CART_COMPONENT_RECEIPT_ROOT / "unexpected.json"
                ).write_text(
                    "{}\n",
                    encoding="utf-8",
                )
        return payload

    monkeypatch.setattr(
        receipts_module,
        "_read_repository_file",
        read_and_inject_extra_file,
    )

    with pytest.raises(WebMallCartComponentReceiptError):
        load_trusted_webmall_cart_reference_receipt(root)


def test_component_receipt_binds_validation_to_same_environment_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 receipt validator 实际使用的 manifest 字节必须匹配 allowlist 环境身份。

    输入参数：tmp_path 为隔离仓库；monkeypatch 模拟三层身份
        首尾均看到 A，而中间 manifest/receipt validator 看到 B 的 ABA。
    输出返回值：无；即使 receipt SHA 与首尾 component identity
        自洽，中间 B 快照也不得被 A 的 allowlist 授权。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    receipt = _write_current_component_receipt(root)
    identity_a = derive_webmall_cart_component_identity(root)

    browser_path = root / "environments/osworld/image-manifest.json"
    browser_raw = json.loads(browser_path.read_text(encoding="utf-8"))
    browser_raw["extracted_image"]["sha256"] = "2" * 64
    browser_raw["materialization"]["output_sha256"] = "2" * 64
    browser_payload = (json.dumps(browser_raw, sort_keys=True) + "\n").encode()
    browser_path.write_bytes(browser_payload)
    webmall_path = root / "environments/webmall/environment-manifest.json"
    webmall_raw = json.loads(webmall_path.read_text(encoding="utf-8"))
    webmall_raw["browser_runtime"]["image_manifest_sha256"] = hashlib.sha256(
        browser_payload
    ).hexdigest()
    webmall_path.write_text(
        json.dumps(webmall_raw, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_b, webmall_sha256_b = load_webmall_environment_manifest_with_sha256(
        webmall_path
    )
    browser_b = load_osworld_image_manifest(browser_path)
    receipt["webmall_manifest_sha256"] = webmall_sha256_b
    receipt["browser_image_manifest_sha256"] = (
        manifest_b.browser_runtime.image_manifest_sha256
    )
    receipt["browser_extracted_sha256"] = browser_b.extracted_sha256
    receipt_payload_b = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    receipt_path = (
        root
        / WEBMALL_CART_COMPONENT_RECEIPT_ROOT
        / (f"{WEBMALL_CART_REFERENCE_COMPONENT_ID}.json")
    )
    receipt_path.write_bytes(receipt_payload_b)
    allowlist_path = root / WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowlist["receipts"][WEBMALL_CART_REFERENCE_COMPONENT_ID]["receipt_sha256"] = (
        hashlib.sha256(receipt_payload_b).hexdigest()
    )
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        receipts_module,
        "derive_webmall_cart_component_identity",
        lambda _repo_root: identity_a,
    )

    with pytest.raises(WebMallCartComponentReceiptError):
        load_trusted_webmall_cart_reference_receipt(root)


def test_allowlisted_component_receipt_missing_fails_closed(tmp_path: Path) -> None:
    """验证 allowlist 非空时 receipt 缺失不会降级为 pending。

    输入参数：tmp_path 为删除已授权 receipt 的隔离仓库。
    输出返回值：无；loader 抛固定错误而不返回伪或空证明。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    _write_current_component_receipt(root)
    (
        root
        / WEBMALL_CART_COMPONENT_RECEIPT_ROOT
        / (f"{WEBMALL_CART_REFERENCE_COMPONENT_ID}.json")
    ).unlink()

    with pytest.raises(WebMallCartComponentReceiptError):
        load_trusted_webmall_cart_reference_receipt(root)


def test_allowlisted_component_receipt_symlink_fails_closed(tmp_path: Path) -> None:
    """验证 allowlist 不能授权指向合法字节副本的 receipt symlink。

    输入参数：tmp_path 为构造链接替换的隔离仓库。
    输出返回值：无；物理闭集在 JSON 解析前失败。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    _write_current_component_receipt(root)
    receipt_path = (
        root
        / WEBMALL_CART_COMPONENT_RECEIPT_ROOT
        / (f"{WEBMALL_CART_REFERENCE_COMPONENT_ID}.json")
    )
    external = tmp_path / "external-receipt.json"
    external.write_bytes(receipt_path.read_bytes())
    receipt_path.unlink()
    receipt_path.symlink_to(external)

    with pytest.raises(WebMallCartComponentReceiptError):
        load_trusted_webmall_cart_reference_receipt(root)


def test_allowlisted_component_receipt_oversize_fails_closed(tmp_path: Path) -> None:
    """验证 receipt 大小上限无法通过同步 allowlist SHA 绕过。

    输入参数：tmp_path 为写入超过 64 KiB receipt 的隔离仓库。
    输出返回值：无；安全读在 JSON 解析前固定失败。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    _write_current_component_receipt(root)
    _replace_receipt_payload_and_allowlist_sha(root, b"{" + b" " * 65_536 + b"}")

    with pytest.raises(WebMallCartComponentReceiptError):
        load_trusted_webmall_cart_reference_receipt(root)


def test_component_receipt_rejects_extra_sensitive_field(tmp_path: Path) -> None:
    """验证 receipt 即使重签 SHA 也不能带入 worker/Cart 私有字段。

    输入参数：tmp_path 为注入额外敏感字段的隔离仓库。
    输出返回值：无；validator 依据字段闭集拒绝，异常不回显值。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    receipt = _write_current_component_receipt(root)
    receipt["worker_id"] = "private-worker"
    payload = (json.dumps(receipt, sort_keys=True) + "\n").encode()
    _replace_receipt_payload_and_allowlist_sha(root, payload)

    with pytest.raises(WebMallCartComponentReceiptError) as captured:
        load_trusted_webmall_cart_reference_receipt(root)

    assert "private-worker" not in repr(captured.value)


def test_component_receipt_rejects_stale_component_vector(tmp_path: Path) -> None:
    """验证安全依赖变化会使旧 component allowlist 立即失效。

    输入参数：tmp_path 为修改正式 Cart 源码的隔离仓库。
    输出返回值：无；只改代码且不更新三层 allowlist 时
        loader 固定失败。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    _write_current_component_receipt(root)
    component_path = root / "src/paraguibench/runtime/webmall_doctor.py"
    component_path.write_bytes(component_path.read_bytes() + b"\n# stale-vector\n")

    with pytest.raises(WebMallCartComponentReceiptError):
        load_trusted_webmall_cart_reference_receipt(root)


def test_component_identity_requires_exact_eight_cart_tasks(tmp_path: Path) -> None:
    """验证 task identity 不能把少于精确八个的 Cart 集合当成当前闭集。

    输入参数：tmp_path 为把一个 Cart task 改为非 Cart 且
        同步 release 文件摘要的隔离仓库。
    输出返回值：无；即使 release 字节自洽，八任务语义闭集
        仍固定失败。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    release_path = root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    target_entry = next(
        entry
        for entry in release["tasks"]
        if entry["task_id"] == "Operation-OnlineShopping-AddToCart-001"
    )
    task_path = root / target_entry["path"]
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["answer_type"] = "text"
    task_payload = (json.dumps(task, sort_keys=True) + "\n").encode()
    task_path.write_bytes(task_payload)
    target_entry["sha256"] = hashlib.sha256(task_payload).hexdigest()
    release_path.write_text(
        json.dumps(release, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(WebMallCartComponentReceiptError):
        derive_webmall_cart_component_identity(root)


@pytest.mark.parametrize("target", ("allowlist", "receipt"))
def test_component_receipt_documents_reject_float_schema_version(
    tmp_path: Path,
    target: str,
) -> None:
    """验证 JSON ``1.0`` 不能借 Python 数值相等冒充 integer schema version。

    输入参数：tmp_path 为隔离仓库；target 分别选择
        component allowlist 或脱敏 receipt 的 ``schema_version``。
    输出返回值：无；两个独立字段闭集都必须要求非 bool
        的真正 ``int``，并对 1.0 固定失败。
    """

    root = tmp_path / "repo"
    _copy_component_identity_repository(root)
    receipt = _write_current_component_receipt(root)
    if target == "allowlist":
        allowlist_path = root / WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH
        allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
        allowlist["schema_version"] = 1.0
        allowlist_path.write_text(
            json.dumps(allowlist, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    else:
        receipt["schema_version"] = 1.0
        _replace_receipt_payload_and_allowlist_sha(
            root,
            (json.dumps(receipt, sort_keys=True) + "\n").encode(),
        )

    with pytest.raises(WebMallCartComponentReceiptError):
        load_trusted_webmall_cart_reference_receipt(root)
