"""验证 GitHub Pages 公共数据集的确定性生成契约。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts/site/generate_site_data.py"
PUBLIC_TASK_FIELDS = {
    "task_id",
    "category",
    "benchmark_group",
    "source",
    "tag",
    "type",
    "environment_protocol",
    "evaluation_protocol",
    "asset_status",
    "support_status",
    "blocker_codes",
}
FORBIDDEN_KEYS = {
    "instruction",
    "instruction_template",
    "answer",
    "accepted_answers",
    "fixture",
    "fixture_ref",
    "profile",
    "url",
    "path",
    "api_key",
    "credential",
    "model",
}


def _load_generator_module():
    """从脚本路径加载生成器，避免要求仓库脚本成为安装包。

    输入参数：
        无。
    输出返回值：
        已执行的 ``generate_site_data`` 模块。
    """

    spec = importlib.util.spec_from_file_location(
        "generate_site_data",
        GENERATOR,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载站点数据生成器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_site_data = _load_generator_module().build_site_data


def test_build_site_data_preserves_release_and_support_totals() -> None:
    """生成结果必须覆盖完整 release，并准确反映支持状态汇总。"""

    data = build_site_data(REPO_ROOT)

    assert data["summary"]["task_count"] == 233
    assert data["summary"]["support_status_counts"] == {
        "blocked": 232,
        "live_validated": 1,
    }
    assert len(data["tasks"]) == 233


def test_benchmark_groups_match_the_six_paper_categories() -> None:
    """论文六类分组必须覆盖全部任务，并优先抽取 SearchAndWrite。"""

    data = build_site_data(REPO_ROOT)
    assert data["summary"]["benchmark_group_counts"] == {
        "FileOperation": 42,
        "FileSearch": 12,
        "OnlineShopping": 91,
        "SearchAndWrite": 10,
        "WebNavigation": 13,
        "WebSearch": 65,
    }
    task_groups = {
        task["task_id"]: task["benchmark_group"] for task in data["tasks"]
    }
    assert (
        task_groups["Operation-FileOperate-SearchAndWrite-001"]
        == "SearchAndWrite"
    )
    assert (
        task_groups["Operation-WebOperate-SearchAndWrite-001"]
        == "SearchAndWrite"
    )
    assert (
        task_groups["InformationRetrieval-VisualSearch-Video-001"]
        == "WebSearch"
    )
    assert (
        task_groups["InformationRetrieval-FileSearch-Readonly-001"]
        == "FileSearch"
    )
    assert (
        task_groups["Operation-OnlineShopping-AddToCart-001"]
        == "OnlineShopping"
    )
    assert (
        task_groups["Operation-FileOperate-Settings-001"]
        == "FileOperation"
    )
    assert (
        task_groups["Operation-WebOperate-Settings-001"]
        == "WebNavigation"
    )


def test_public_dataset_has_a_closed_safe_field_set_and_bilingual_labels() -> None:
    """每条任务只能暴露白名单元数据，所有分类值都必须有双语标签。"""

    data = build_site_data(REPO_ROOT)

    assert all(set(task) == PUBLIC_TASK_FIELDS for task in data["tasks"])
    assert not (_collect_keys(data) & FORBIDDEN_KEYS)
    serialized = json.dumps(data, ensure_ascii=False)
    assert not re.search(r"(?:https?|file)://", serialized, re.IGNORECASE)
    private_address = ".".join(("10", "1", "110", "114"))
    macos_home_root = "".join(("/", "Users", "/"))
    linux_home_root = "".join(("/", "home", "/"))
    assert private_address not in serialized
    assert macos_home_root not in serialized
    assert linux_home_root not in serialized

    labels = data["labels"]
    assert labels["fields"]["task_id"] == {
        "en": "Task ID",
        "zh-CN": "任务 ID",
    }
    dimensions = {
        "category": {task["category"] for task in data["tasks"]},
        "benchmark_group": {
            task["benchmark_group"] for task in data["tasks"]
        },
        "source": {task["source"] for task in data["tasks"]},
        "tag": {task["tag"] for task in data["tasks"]},
        "type": {task["type"] for task in data["tasks"]},
        "environment_protocol": {
            task["environment_protocol"] for task in data["tasks"]
        },
        "evaluation_protocol": {
            task["evaluation_protocol"] for task in data["tasks"]
        },
        "asset_status": {task["asset_status"] for task in data["tasks"]},
        "support_status": {
            task["support_status"] for task in data["tasks"]
        },
        "blocker_codes": {
            code for task in data["tasks"] for code in task["blocker_codes"]
        },
    }
    for dimension, values in dimensions.items():
        assert set(labels["values"][dimension]) == values
        assert all(
            set(label) == {"en", "zh-CN"} and all(label.values())
            for label in labels["values"][dimension].values()
        )


def test_cli_writes_deterministic_data_and_check_detects_output_drift(
    tmp_path: Path,
) -> None:
    """CLI 必须稳定落盘，并由 --check 拒绝被修改的派生文件。"""

    output_path = tmp_path / "site-data.json"
    generate = _run_generator("--output", str(output_path))

    assert generate.returncode == 0, generate.stderr
    first_bytes = output_path.read_bytes()
    data = json.loads(first_bytes)
    assert "generated_at" not in data
    assert data["input_manifests"] == {
        "release": {
            "id": "release-v1",
            "sha256": _sha256(
                REPO_ROOT / "benchmark/manifests/release-v1.json"
            ),
            "task_count": 233,
        },
        "runtime_support": {
            "id": "runtime-support-v1",
            "sha256": _sha256(
                REPO_ROOT
                / "benchmark/manifests/runtime-support-v1.json"
            ),
            "task_count": 233,
        },
    }

    regenerate = _run_generator("--output", str(output_path))
    assert regenerate.returncode == 0, regenerate.stderr
    assert output_path.read_bytes() == first_bytes
    assert _run_generator(
        "--output",
        str(output_path),
        "--check",
    ).returncode == 0

    output_path.write_text("{}\n", encoding="utf-8")
    stale_bytes = output_path.read_bytes()
    stale_check = _run_generator(
        "--output",
        str(output_path),
        "--check",
    )
    assert stale_check.returncode != 0
    assert output_path.read_bytes() == stale_bytes


def test_check_rejects_canonical_source_drift_before_projection(
    tmp_path: Path,
) -> None:
    """canonical 文件偏离 release 摘要时必须作为来源错误拒绝且不回显内容。"""

    isolated_root = tmp_path / "isolated-repo"
    _copy_site_sources(isolated_root)
    output_path = isolated_root / "website/public/data/site-data.json"
    generate = _run_generator_for_root(
        isolated_root,
        "--output",
        str(output_path),
    )
    assert generate.returncode == 0, generate.stderr
    original_output = output_path.read_bytes()

    task_path = (
        isolated_root
        / "benchmark/tasks/InformationRetrieval-FileSearch-Readonly-001.json"
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    sentinel = "PRIVATE_SOURCE_DRIFT_SENTINEL"
    task["task_source"] = sentinel
    task_path.write_text(
        json.dumps(task, ensure_ascii=False),
        encoding="utf-8",
    )

    check = _run_generator_for_root(
        isolated_root,
        "--output",
        str(output_path),
        "--check",
    )
    assert check.returncode == 2
    assert sentinel not in f"{check.stdout}\n{check.stderr}"
    assert output_path.read_bytes() == original_output


def test_generator_rejects_sensitive_metadata_even_with_updated_hashes(
    tmp_path: Path,
) -> None:
    """同步更新摘要也不能把 URL 伪装成公开 source 写入页面数据。"""

    isolated_root = tmp_path / "isolated-repo"
    _copy_site_sources(isolated_root)
    task_path = (
        isolated_root
        / "benchmark/tasks/InformationRetrieval-FileSearch-Readonly-001.json"
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    sensitive_value = "https://" + "private.example.invalid/resource"
    task["task_source"] = sensitive_value
    task_path.write_text(
        json.dumps(task, ensure_ascii=False),
        encoding="utf-8",
    )

    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["tasks"][0]["sha256"] = _sha256(task_path)
    release_path.write_text(
        json.dumps(release, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime_path = (
        isolated_root / "benchmark/manifests/runtime-support-v1.json"
    )
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["release_manifest_sha256"] = _sha256(release_path)
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False),
        encoding="utf-8",
    )

    output_path = isolated_root / "website/public/data/site-data.json"
    generate = _run_generator_for_root(
        isolated_root,
        "--output",
        str(output_path),
    )
    assert generate.returncode == 2
    assert sensitive_value not in f"{generate.stdout}\n{generate.stderr}"
    assert not output_path.exists()


def _collect_keys(value: object) -> set[str]:
    """递归收集 JSON 结构的全部 object 字段名。

    输入参数：
        value：任意可序列化 JSON 结构。
    输出返回值：
        所有层级 object key 的集合。
    """

    if isinstance(value, dict):
        return set(value) | {
            key
            for child in value.values()
            for key in _collect_keys(child)
        }
    if isinstance(value, list):
        return {
            key
            for child in value
            for key in _collect_keys(child)
        }
    return set()


def _run_generator(*arguments: str) -> subprocess.CompletedProcess[str]:
    """在真实 CLI 边界运行站点数据生成器。

    输入参数：
        arguments：传给生成脚本的附加命令行参数。
    输出返回值：
        捕获标准输出与错误输出的子进程结果。
    """

    return _run_generator_for_root(REPO_ROOT, *arguments)


def _run_generator_for_root(
    repo_root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """针对指定仓库根目录运行真实生成器 CLI。

    输入参数：
        repo_root：包含最小 benchmark 来源的仓库根目录。
        arguments：传给生成脚本的附加命令行参数。
    输出返回值：
        捕获标准输出与错误输出的子进程结果。
    """

    return subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--repo-root",
            str(repo_root),
            *arguments,
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )


def _copy_site_sources(destination: Path) -> None:
    """复制站点生成器允许读取的三类输入到隔离测试仓库。

    输入参数：
        destination：待创建的隔离仓库根目录。
    输出返回值：
        无；复制 release、runtime-support 与 canonical tasks。
    """

    manifest_root = destination / "benchmark/manifests"
    manifest_root.mkdir(parents=True)
    for name in ("release-v1.json", "runtime-support-v1.json"):
        shutil.copyfile(
            REPO_ROOT / "benchmark/manifests" / name,
            manifest_root / name,
        )
    shutil.copytree(
        REPO_ROOT / "benchmark/tasks",
        destination / "benchmark/tasks",
    )


def _sha256(path: Path) -> str:
    """计算测试输入文件的 SHA-256。

    输入参数：
        path：待读取文件。
    输出返回值：
        小写十六进制 SHA-256。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()
