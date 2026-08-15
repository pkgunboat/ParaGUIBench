"""Settings-003 Bookmark 启动上下文的固定 PDF 资产回归。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "Operation-WebOperate-Settings-003"
ASSET_MANIFEST_REFERENCE = (
    "benchmark/assets/manifests/Operation-WebOperate-Settings-003.json"
)


def test_settings_003_uses_pinned_pdf_without_runtime_path_binding() -> None:
    """验证 Settings-003 公开任务只引用固定 PDF 与相对启动上下文。

    输入参数：
        无；读取 canonical task、release-v1 和仓库内资产 manifest。
    输出返回值：
        无；断言任务不再携带 mutable URL 或运行前无法物化的
        guest 绝对路径，且 PDF 大小、摘要、revision 和许可精确固定。
    """

    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{TASK_ID}.json"
    task_bytes = task_path.read_bytes()
    task = json.loads(task_bytes)
    release = json.loads(
        (REPO_ROOT / "benchmark" / "manifests" / "release-v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert "prepare_script_path" not in task
    assert "required_environment_bindings" not in task
    assert "${" not in task["instruction"]
    assert task["asset_manifest"] == ASSET_MANIFEST_REFERENCE
    assert task["agent_start_context"] == {
        "type": "local_pdf",
        "asset_relative_path": "2206.08853.pdf",
        "open_with": "chrome",
        "target": "all_vms",
    }
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == TASK_ID
    )
    assert release_entry["sha256"] == hashlib.sha256(task_bytes).hexdigest()

    manifest = json.loads(
        (REPO_ROOT / ASSET_MANIFEST_REFERENCE).read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema_version": 1,
        "asset_set_id": TASK_ID,
        "source": {
            "provider": "huggingface_dataset",
            "repository": "xlangai/ubuntu_osworld_file_cache",
            "revision": "711e0811642364e7aa8f10a8918367d0b626d578",
            "base_path": ("multi_apps/a82b78bb-7fde-4cb3-94a4-035baf10bcf0"),
            "license_status": "apache-2.0",
        },
        "distribution_policy": "download_only",
        "files": [
            {
                "path": "2206.08853.pdf",
                "size": 9_765_032,
                "sha256": (
                    "68743684c375a3832f89031433cf310912d15c0464378f6095903000870b3f59"
                ),
            }
        ],
    }
