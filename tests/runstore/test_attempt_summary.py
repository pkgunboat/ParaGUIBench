"""RunStore Attempt 终态语义测试。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from paraguibench.runstore import (
    AttemptFailureStage,
    EvaluationOutcome,
    ExecutionOutcome,
    RunProvenanceStatus,
    RunStore,
)
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


def test_attempt_inspection_exposes_only_allowlisted_diagnostics(
    tmp_path: Path,
) -> None:
    """验证公开诊断投影不会返回 summary details 或模型/异常原文。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；诊断只包含枚举终态、得分、严格 failure stage 与 Run 版本向量，
        任意 details sentinel 都不能进入返回对象。
    """

    sentinel = "private-exception-and-model-output"
    store = RunStore(tmp_path)
    vector = synthetic_run_version_vector()
    store.start_run(
        run_id="run-inspection-001",
        run_record={"test": True},
        version_vector=vector,
    )
    attempt = store.start_attempt(
        run_id="run-inspection-001",
        task_id="synthetic-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("synthetic-task"),
    )
    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.FAILED,
        evaluation_outcome=EvaluationOutcome.NOT_REQUESTED,
        score=None,
        failure_stage=AttemptFailureStage.AGENT_RUN,
        details={
            "exception_message": sentinel,
            "raw_output": sentinel,
        },
    )

    inspection = store.inspect_attempt(
        run_id="run-inspection-001",
        task_id="synthetic-task",
        attempt_id="attempt-001",
    )

    assert inspection.execution_outcome is ExecutionOutcome.FAILED
    assert inspection.evaluation_outcome is EvaluationOutcome.NOT_REQUESTED
    assert inspection.score is None
    assert inspection.failure_stage is AttemptFailureStage.AGENT_RUN
    assert inspection.provenance_status is RunProvenanceStatus.VERSIONED
    assert inspection.version_vector == vector
    assert sentinel not in repr(asdict(inspection))


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
    store.start_run(
        run_id="run-summary-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
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

    summary = json.loads((attempt.path / "summary.json").read_text(encoding="utf-8"))
    assert summary["execution"]["outcome"] == "SUCCEEDED"
    assert summary["evaluation"]["outcome"] == "UNAVAILABLE"
    assert summary["evaluation"]["score"] is None


@pytest.mark.parametrize(
    "corruption",
    [
        "missing-run-manifest",
        "missing-attempt-identity",
        "mismatched-summary-identity",
    ],
)
def test_attempt_inspection_rejects_orphaned_or_mismatched_records(
    tmp_path: Path,
    corruption: str,
) -> None:
    """验证诊断读取会交叉核对 Run、Attempt 与 summary 三层身份。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
        corruption：本用例删除或错写的持久化身份记录类型。
    输出返回值：
        无；孤立 summary、缺少 Attempt 身份或错配身份均须失败关闭，不能
        被投影为合法的 legacy 运行结果。
    """

    store = RunStore(tmp_path)
    run = store.start_run(
        run_id="run-inspection-corrupt",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id=run.run_id,
        task_id="synthetic-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("synthetic-task"),
    )
    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        evaluation_outcome=EvaluationOutcome.PASSED,
        score=1.0,
    )

    if corruption == "missing-run-manifest":
        (run.path / "run.json").unlink()
    elif corruption == "missing-attempt-identity":
        (attempt.path / "attempt.json").unlink()
    else:
        summary_path = attempt.path / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["task_id"] = "another-task"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises((TypeError, ValueError), match="identity|manifest"):
        store.inspect_attempt(
            run_id=run.run_id,
            task_id="synthetic-task",
            attempt_id="attempt-001",
        )


def test_scored_outcome_requires_score_and_inspection_rechecks_contract(
    tmp_path: Path,
) -> None:
    """验证写入端与读取端共同执行 outcome/score 交叉约束。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；PASSED/FAILED 缺 score 时不能落盘，手工损坏为非评分状态携带
        score 的 summary 也不能通过只读诊断。
    """

    store = RunStore(tmp_path)
    run = store.start_run(
        run_id="run-score-contract",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id=run.run_id,
        task_id="synthetic-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("synthetic-task"),
    )

    with pytest.raises(ValueError, match="score"):
        store.finish_attempt(
            attempt=attempt,
            execution_outcome=ExecutionOutcome.SUCCEEDED,
            evaluation_outcome=EvaluationOutcome.PASSED,
            score=None,
        )

    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        evaluation_outcome=EvaluationOutcome.NOT_REQUESTED,
        score=None,
    )
    summary_path = attempt.path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["evaluation"]["score"] = 1.0
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(ValueError, match="score"):
        store.inspect_attempt(
            run_id=run.run_id,
            task_id="synthetic-task",
            attempt_id="attempt-001",
        )


def test_evaluator_details_cannot_forge_failure_stage(tmp_path: Path) -> None:
    """验证自由格式 evaluator details 不能控制保留的生命周期诊断字段。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；成功执行即使 details 含同名键，公开诊断仍显示 ``not_failed``。
    """

    store = RunStore(tmp_path)
    run = store.start_run(
        run_id="run-stage-contract",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id=run.run_id,
        task_id="synthetic-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("synthetic-task"),
    )
    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        evaluation_outcome=EvaluationOutcome.PASSED,
        score=1.0,
        details={"failure_stage": "agent.run"},
    )

    inspection = store.inspect_attempt(
        run_id=run.run_id,
        task_id="synthetic-task",
        attempt_id="attempt-001",
    )

    assert inspection.failure_stage is AttemptFailureStage.NOT_FAILED


@pytest.mark.parametrize(
    ("execution_outcome", "evaluation_outcome"),
    [
        (ExecutionOutcome.RUNNING, EvaluationOutcome.NOT_REQUESTED),
        (ExecutionOutcome.SUCCEEDED, EvaluationOutcome.RUNNING),
    ],
)
def test_finish_attempt_rejects_nonterminal_outcomes(
    tmp_path: Path,
    execution_outcome: ExecutionOutcome,
    evaluation_outcome: EvaluationOutcome,
) -> None:
    """验证不可变 summary 只能保存执行与评价终态。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根目录。
        execution_outcome：待拒绝的执行过程态或其搭配终态。
        evaluation_outcome：待拒绝的评价过程态或其搭配终态。
    输出返回值：
        无；任一过程态都不得落盘为 ``summary.json``。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-terminal-contract",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-terminal-contract",
        task_id="synthetic-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("synthetic-task"),
    )

    with pytest.raises(ValueError, match="terminal"):
        store.finish_attempt(
            attempt=attempt,
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
            score=None,
        )

    assert not (attempt.path / "summary.json").exists()


def test_inspection_rejects_manually_persisted_nonterminal_summary(
    tmp_path: Path,
) -> None:
    """验证只读诊断也会拒绝手工注入的过程态 summary。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根目录。
    输出返回值：
        无；诊断不能把 ``RUNNING`` 投影成已完成 Attempt。
    """

    store = RunStore(tmp_path)
    run = store.start_run(
        run_id="run-terminal-inspection",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id=run.run_id,
        task_id="synthetic-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("synthetic-task"),
    )
    (attempt.path / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "run_id": run.run_id,
                "task_id": "synthetic-task",
                "attempt_id": "attempt-001",
                "execution": {"outcome": "RUNNING"},
                "evaluation": {"outcome": "NOT_REQUESTED", "score": None},
                "failure_stage": "not_failed",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="terminal"):
        store.inspect_attempt(
            run_id=run.run_id,
            task_id="synthetic-task",
            attempt_id="attempt-001",
        )
