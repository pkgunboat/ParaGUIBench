"""固定发布清单中的单任务安全加载测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paraguibench.benchmark.release import (
    ReleaseTaskError,
    load_release_task,
)


def _write_synthetic_release(root: Path) -> None:
    """建立一个只含单任务及固定摘要的合成发布目录。

    输入参数：
        root：pytest 临时仓库根目录。
    输出返回值：
        无；写入 task 与 release-v1 manifest。
    """

    task_root = root / "benchmark" / "tasks"
    task_root.mkdir(parents=True)
    task_path = task_root / "synthetic-task.json"
    task = {
        "task_id": "synthetic-task",
        "instruction": "Inspect the shared folder.",
        "answer": "paper3",
    }
    task_path.write_text(
        json.dumps(task, sort_keys=True),
        encoding="utf-8",
    )
    digest = hashlib.sha256(task_path.read_bytes()).hexdigest()
    manifest_root = root / "benchmark" / "manifests"
    manifest_root.mkdir()
    (manifest_root / "release-v1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "release_id": "release-v1",
                "tasks": [
                    {
                        "task_id": "synthetic-task",
                        "path": "benchmark/tasks/synthetic-task.json",
                        "sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_release_loader_verifies_path_hash_and_task_identity(
    tmp_path: Path,
) -> None:
    """验证单任务只从 release entry 加载且摘要与内部身份一致。

    输入参数：
        tmp_path：pytest 提供的合成仓库根目录。
    输出返回值：
        无；合法任务可读，篡改后 fail closed。
    """

    _write_synthetic_release(tmp_path)

    task = load_release_task(tmp_path, "synthetic-task")
    assert task["instruction"] == "Inspect the shared folder."

    task_path = tmp_path / "benchmark" / "tasks" / "synthetic-task.json"
    task_path.write_text('{"task_id":"synthetic-task"}', encoding="utf-8")
    with pytest.raises(ReleaseTaskError, match="摘要"):
        load_release_task(tmp_path, "synthetic-task")
