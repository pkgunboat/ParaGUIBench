"""RunStore 并发身份与 canonical task snapshot 测试。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from paraguibench.runstore import RunStore, RunStoreConflictError
from tests.runstore._audit import synthetic_task_audit


def test_concurrent_conflicting_task_snapshots_have_one_winner(
    tmp_path: Path,
) -> None:
    """验证并发冲突只提交一个 canonical task snapshot。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；两个并发调用使用不同 task 定义时，一个成功、一个明确冲突，
        最终只存在一个已启动 Attempt。
    """

    store = RunStore(tmp_path)
    store.start_run(run_id="run-race-001", run_record={"test": True})
    barrier = Barrier(2)

    def start(attempt_id: str, instruction: str) -> str:
        """等待并发起跑后尝试建立一个任务 Attempt。

        输入参数：
            attempt_id：本线程使用的唯一 Attempt 标识。
            instruction：与另一个线程冲突的任务定义。
        输出返回值：
            成功时返回 ``success``，检测到 canonical snapshot 冲突时返回
            ``conflict``。
        """

        barrier.wait()
        try:
            store.start_attempt(
                run_id="run-race-001",
                task_id="InformationRetrieval-FileSearch-Readonly-001",
                attempt_id=attempt_id,
                task_record=synthetic_task_audit(
                    "InformationRetrieval-FileSearch-Readonly-001",
                    task_tag=instruction,
                ),
            )
        except RunStoreConflictError:
            return "conflict"
        return "success"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda arguments: start(*arguments),
                [
                    ("attempt-001", "First immutable instruction."),
                    ("attempt-002", "Second conflicting instruction."),
                ],
            )
        )

    assert sorted(results) == ["conflict", "success"]

    task_path = (
        tmp_path
        / "run-race-001"
        / "tasks"
        / "InformationRetrieval-FileSearch-Readonly-001"
    )
    task_snapshot = json.loads(
        (task_path / "task.json").read_text(encoding="utf-8")
    )
    assert task_snapshot["task"]["task_tag"] in {
        "First immutable instruction.",
        "Second conflicting instruction.",
    }
    assert len(list(task_path.glob("attempts/*/attempt.json"))) == 1
