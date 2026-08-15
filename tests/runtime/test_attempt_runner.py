"""单任务 runtime 纵向生命周期与 gold 隔离测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from paraguibench.benchmark import PreparedTask, build_agent_task_view
from paraguibench.runstore import RunStore
from paraguibench.runtime.attempt_runner import (
    AgentRunResult,
    AttemptRunner,
    RuntimeEvaluation,
)
from tests.runstore._audit import synthetic_run_version_vector


class _Environment:
    """记录 start/prepare/close 顺序的合成环境。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录。

        输入参数：
            calls：测试用于观察生命周期顺序的列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def start(self) -> None:
        """记录环境启动。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls.append("environment.start")

    def prepare(self, task: dict[str, Any]) -> None:
        """记录 canonical task 准备阶段。

        输入参数：
            task：runtime 可信侧可访问的完整 task。
        输出返回值：
            无。
        """

        assert "answer" in task
        self.calls.append("environment.prepare")

    def close(self) -> None:
        """记录 owned resource 清理。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls.append("environment.close")


class _Agent:
    """验证 Agent 只收到无 gold view 的合成系统。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录。

        输入参数：
            calls：生命周期记录列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def run(
        self,
        task_view: dict[str, Any],
        environment: _Environment,
    ) -> AgentRunResult:
        """返回带完整 answer 标签的合成结果。

        输入参数：
            task_view：必须不含任何评价或 gold 字段。
            environment：已启动并准备完成的环境。
        输出返回值：
            一步完成的 AgentRunResult。
        """

        assert "answer" not in task_view
        assert "accepted_answers" not in task_view
        assert "evaluator_path" not in task_view
        self.calls.append("agent.run")
        return AgentRunResult(
            final_output="<answer>private-gold</answer>",
            step_count=1,
            termination="finished",
        )


class _Evaluator:
    """记录评价发生在 Agent 完成后的合成 evaluator。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录。

        输入参数：
            calls：生命周期记录列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: _Environment,
    ) -> RuntimeEvaluation:
        """验证 canonical gold 与 Agent 输出均只在 evaluator 相遇。

        输入参数：
            task：可信 evaluator 可见的 canonical task。
            final_output：Agent 最终输出。
            environment：尚未关闭、可供状态评价的当前任务环境。
        输出返回值：
            通过且得分 1.0 的结构化评价。
        """

        assert task["answer"] in final_output
        assert isinstance(environment, _Environment)
        self.calls.append("evaluator.evaluate")
        return RuntimeEvaluation(
            passed=True,
            score=1.0,
            details={"match_type": "strict_exact"},
        )


class _FailingAgent:
    """模拟模型或动作循环异常的 Agent System。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录。

        输入参数：
            calls：生命周期记录列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def run(
        self,
        task_view: dict[str, Any],
        environment: _Environment,
    ) -> AgentRunResult:
        """在记录调用后抛出不含敏感值的合成异常。

        输入参数：
            task_view：gold-free Agent task view。
            environment：已准备环境。
        输出返回值：
            不返回；始终抛出 ``RuntimeError``。
        """

        self.calls.append("agent.run")
        raise RuntimeError("synthetic agent failure")


class _FailingEvaluator:
    """模拟订单证据或评价契约异常的可信 evaluator。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录。

        输入参数：
            calls：生命周期记录列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: _Environment,
    ) -> RuntimeEvaluation:
        """在评价阶段抛出含敏感哨兵值的合成异常。

        输入参数：
            task：可信 canonical task。
            final_output：Agent 最终文本。
            environment：仍存活的任务环境。
        输出返回值：
            不返回；始终抛出 ``RuntimeError``。
        """

        del task, final_output, environment
        self.calls.append("evaluator.evaluate")
        raise RuntimeError("private-evidence-sentinel")


def _prepare_synthetic_task(task: dict[str, Any]) -> PreparedTask:
    """为 runtime 单元测试构造显式三投影。

    输入参数：
        task：含 instruction 与可选 gold 的可信合成 task。
    输出返回值：
        Agent view 不含 gold、audit 不含 instruction 的 ``PreparedTask``。
    """

    return PreparedTask(
        trusted_task=dict(task),
        agent_task=build_agent_task_view(task),
        audit_metadata={
            "release_id": "synthetic-release",
            "canonical_task_sha256": "0" * 64,
            "task_id": task["task_id"],
            "materialization": {
                "schema_version": 1,
                "environment_binding_names": [],
                "fixture_refs": [],
            },
        },
    )


def test_attempt_runner_orders_lifecycle_and_persists_separate_outcomes(
    tmp_path: Path,
) -> None:
    """验证成功纵向切片按顺序清理并提交执行/评价终态。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根目录。
    输出返回值：
        无；summary 为 SUCCEEDED/PASSED/1.0，worker 事件不含 gold。
    """

    task = {
        "task_id": "synthetic-runtime-task",
        "task_uid": "synthetic-uid",
        "task_type": "QA",
        "task_source": "self",
        "task_tag": "FileSearch",
        "instruction": "Inspect the shared folder.",
        "answer": "private-gold",
        "accepted_answers": [],
        "answer_match_mode": "exact",
        "evaluator_path": "hidden/evaluator.py",
    }
    prepared_task = _prepare_synthetic_task(task)
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-runtime-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-runtime-001",
        task_id=task["task_id"],
        attempt_id="attempt-001",
        task_record=prepared_task.audit_metadata,
    )
    calls: list[str] = []
    runner = AttemptRunner(store)

    result = runner.run(
        attempt=attempt,
        prepared_task=prepared_task,
        environment=_Environment(calls),
        agent=_Agent(calls),
        evaluator=_Evaluator(calls),
    )

    assert calls == [
        "environment.start",
        "environment.prepare",
        "agent.run",
        "evaluator.evaluate",
        "environment.close",
    ]
    assert result.score == 1.0
    summary = json.loads((attempt.path / "summary.json").read_text(encoding="utf-8"))
    assert summary["execution"]["outcome"] == "SUCCEEDED"
    assert summary["evaluation"]["outcome"] == "PASSED"
    assert summary["evaluation"]["score"] == 1.0
    worker_events = (
        attempt.path / "workers" / "agent-system" / "events-00001.jsonl"
    ).read_text(encoding="utf-8")
    assert "private-gold" not in worker_events


def test_attempt_runner_cleans_environment_and_marks_agent_failure(
    tmp_path: Path,
) -> None:
    """验证 Agent 异常仍清理环境，且未评价不伪装成零分。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根目录。
    输出返回值：
        无；异常重新抛出，summary 为 FAILED/NOT_REQUESTED/null。
    """

    task = {
        "task_id": "synthetic-agent-failure",
        "instruction": "Inspect the shared folder.",
        "answer": "gold",
        "accepted_answers": [],
        "answer_match_mode": "exact",
    }
    prepared_task = _prepare_synthetic_task(task)
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-failure-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-failure-001",
        task_id=task["task_id"],
        attempt_id="attempt-001",
        task_record=prepared_task.audit_metadata,
    )
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="synthetic agent failure"):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=prepared_task,
            environment=_Environment(calls),
            agent=_FailingAgent(calls),
            evaluator=_Evaluator(calls),
        )

    assert calls == [
        "environment.start",
        "environment.prepare",
        "agent.run",
        "environment.close",
    ]
    summary = json.loads((attempt.path / "summary.json").read_text(encoding="utf-8"))
    assert summary["execution"]["outcome"] == "FAILED"
    assert summary["evaluation"]["outcome"] == "NOT_REQUESTED"
    assert summary["evaluation"]["score"] is None
    assert "synthetic agent failure" not in json.dumps(summary)


def test_attempt_runner_maps_evaluator_error_without_zero_score_or_message(
    tmp_path: Path,
) -> None:
    """验证证据/evaluator 异常落为 ERROR/null，且不泄漏异常消息。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根目录。
    输出返回值：
        无；Agent execution 保持 SUCCEEDED，评价为 ERROR、score 为 null，
        环境仍被关闭，summary 只保留异常类型。
    """

    task = {
        "task_id": "synthetic-evaluator-failure",
        "instruction": "Complete the task.",
        "answer": "private-gold",
        "accepted_answers": [],
        "answer_match_mode": "exact",
    }
    prepared_task = _prepare_synthetic_task(task)
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-evaluator-failure-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-evaluator-failure-001",
        task_id=task["task_id"],
        attempt_id="attempt-001",
        task_record=prepared_task.audit_metadata,
    )
    calls: list[str] = []

    with pytest.raises(RuntimeError, match="private-evidence-sentinel"):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=prepared_task,
            environment=_Environment(calls),
            agent=_Agent(calls),
            evaluator=_FailingEvaluator(calls),
        )

    assert calls == [
        "environment.start",
        "environment.prepare",
        "agent.run",
        "evaluator.evaluate",
        "environment.close",
    ]
    summary_text = (attempt.path / "summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert summary["execution"]["outcome"] == "SUCCEEDED"
    assert summary["evaluation"]["outcome"] == "ERROR"
    assert summary["evaluation"]["score"] is None
    assert summary["failure_stage"] == "evaluator.evaluate"
    assert "private-evidence-sentinel" not in summary_text
