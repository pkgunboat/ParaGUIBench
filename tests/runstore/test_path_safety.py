"""RunStore 目录标识与 symlink 逃逸测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from paraguibench.runstore import (
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
    TaskAttempt,
)
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


def test_task_path_cannot_escape_runstore_through_symlink(
    tmp_path: Path,
) -> None:
    """验证预置 symlink 不能把任务记录重定向到 RunStore 外部。

    输入参数：
        tmp_path：pytest 提供的临时目录，同时承载 RunStore 和外部诱饵目录。
    输出返回值：
        无；发现 task 路径为 symlink 时必须拒绝启动，外部目录保持为空。
    """

    root = tmp_path / "runs"
    outside = tmp_path / "outside"
    outside.mkdir()

    store = RunStore(root)
    store.start_run(
        run_id="run-symlink-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    task_parent = root / "run-symlink-001" / "tasks"
    task_parent.mkdir(parents=True)
    (task_parent / "InformationRetrieval-FileSearch-Readonly-001").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(ValueError, match="symlink|outside"):
        store.start_attempt(
            run_id="run-symlink-001",
            task_id="InformationRetrieval-FileSearch-Readonly-001",
            attempt_id="attempt-001",
            task_record=synthetic_task_audit(
                "InformationRetrieval-FileSearch-Readonly-001"
            ),
        )

    assert list(outside.iterdir()) == []


def test_forged_attempt_handle_cannot_write_another_attempt(
    tmp_path: Path,
) -> None:
    """验证 handle 的三层身份和规范路径必须与落盘记录一致。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根目录。
    输出返回值：
        无；伪造 ID 但指向受害 Attempt 的 handle 必须失败，且不得
        创建或覆盖受害 Attempt 的 summary。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-handle-integrity",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    victim = store.start_attempt(
        run_id="run-handle-integrity",
        task_id="victim-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("victim-task"),
    )
    forged = TaskAttempt(
        path=victim.path,
        run_id="run-handle-integrity",
        task_id="another-task",
        attempt_id="attempt-999",
    )

    with pytest.raises(ValueError, match="identity|path"):
        store.finish_attempt(
            attempt=forged,
            execution_outcome=ExecutionOutcome.SUCCEEDED,
            evaluation_outcome=EvaluationOutcome.PASSED,
            score=1.0,
        )

    assert not (victim.path / "summary.json").exists()
