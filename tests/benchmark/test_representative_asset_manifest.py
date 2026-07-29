"""首个 E2E 代表任务的固定版本资产清单测试。"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "InformationRetrieval-FileSearch-Readonly-001"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def test_representative_task_uses_download_only_pinned_asset_manifest() -> None:
    """验证代表任务不依赖 mutable main，且外部资产不可误打包进 Git。

    输入参数：
        无；读取 canonical task 及其仓库相对资产清单。
    输出返回值：
        无；revision、逐文件大小/哈希、路径安全和许可状态均须明确。
    """

    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{TASK_ID}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))

    assert "prepare_script_path" not in task
    manifest_path = REPO_ROOT / task["asset_manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["asset_set_id"] == TASK_ID
    assert REVISION_PATTERN.fullmatch(manifest["source"]["revision"])
    assert manifest["source"]["license_status"] == "unverified"
    assert manifest["distribution_policy"] == "download_only"
    assert len(manifest["files"]) == 4
    assert sum(item["size"] for item in manifest["files"]) == 4_822_379
    for item in manifest["files"]:
        relative_path = PurePosixPath(item["path"])
        assert not relative_path.is_absolute()
        assert ".." not in relative_path.parts
        assert SHA256_PATTERN.fullmatch(item["sha256"])
