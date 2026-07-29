"""RunStore Attempt 终态语义测试。"""

from __future__ import annotations

import json
from pathlib import Path

from paraguibench.runstore import (
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
)
from tests.runstore._audit import synthetic_task_audit


def test_execution_and_evaluation_outcomes_are_persisted_separately(
    tmp_path: Path,
) -> None:
    """验证执行成功但评价不可用时不会被记录成零分失败。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；读取公开生成的 ``summary.json``，断言两类 outcome 与可空
        score 的语义互不混淆。
    """

    store = RunStore(tmp_path)
    store.start_run(run_id="run-summary-001", run_record={"test": True})
    attempt = store.start_attempt(
        run_id="run-summary-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )

    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        evaluation_outcome=EvaluationOutcome.UNAVAILABLE,
        score=None,
        details={"reason": "evaluator dependency was unavailable"},
    )

    summary = json.loads(
        (attempt.path / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["execution"]["outcome"] == "SUCCEEDED"
    assert summary["evaluation"]["outcome"] == "UNAVAILABLE"
    assert summary["evaluation"]["score"] is None
