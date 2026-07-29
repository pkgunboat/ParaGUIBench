"""将 environment、Agent、evaluator 与 RunStore 串成单任务纵向生命周期。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import PreparedTask
from paraguibench.runstore import (
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
    TaskAttempt,
)

@dataclass(frozen=True)
class RuntimeEvaluation:
    """保存 evaluator 返回给 runtime 的统一评分结果。"""

    passed: bool
    score: float
    details: Mapping[str, Any]


@dataclass(frozen=True)
class RuntimeAttemptResult:
    """保存一次已持久化 Attempt 的执行与评价终态。"""

    execution_outcome: ExecutionOutcome
    evaluation_outcome: EvaluationOutcome
    score: float | None


class TaskEnvironment(Protocol):
    """定义 AttemptRunner 所需的最小环境生命周期。"""

    def start(self) -> None:
        """启动本 Attempt 独占的环境资源。"""

    def prepare(self, task: dict[str, Any]) -> None:
        """使用可信 canonical task 准备环境与外部资产。"""

    def close(self) -> None:
        """仅清理当前环境实例拥有的资源。"""


class AgentSystem(Protocol):
    """定义可运行 Agent System 的最小接口。"""

    def run(
        self,
        task_view: dict[str, Any],
        environment: TaskEnvironment,
    ) -> AgentRunResult:
        """在已准备环境中执行不含 gold 的 Agent task view。"""


class TaskEvaluator(Protocol):
    """定义可信 evaluator 的最小接口。"""

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: TaskEnvironment,
    ) -> RuntimeEvaluation:
        """使用可信 task、Agent 输出和仍存活环境产生评价。"""


class AttemptRunner:
    """执行单任务生命周期，并把独立 execution/evaluation 终态落盘。"""

    def __init__(self, store: RunStore) -> None:
        """绑定一个任务级 RunStore。

        输入参数：
            store：已经由调用方选择 repo 外根目录的 RunStore。
        输出返回值：
            无；构造阶段不创建 Attempt 或外部环境。
        """

        self._store = store

    def run(
        self,
        *,
        attempt: TaskAttempt,
        prepared_task: PreparedTask,
        environment: TaskEnvironment,
        agent: AgentSystem,
        evaluator: TaskEvaluator,
    ) -> RuntimeAttemptResult:
        """依序 start、prepare、Agent、evaluate、close，并提交终态。

        输入参数：
            attempt：调用方已通过 RunStore 建立的 Attempt。
            prepared_task：benchmark preparation 生成的 trusted/agent/audit
                三投影；runtime 不再从完整 task 临时猜测可见字段。
            environment：只管理本 Attempt 自有资源的环境实例。
            agent：只接收 gold-free task view 的 Agent System。
            evaluator：执行 Agent 产出与 canonical gold 比较的评价器。
        输出返回值：
            已持久化的 execution/evaluation outcome 与可空 score。
        异常：
            任一阶段异常在记录类型与阶段、完成 owned cleanup 和终态持久化后
            原样重新抛出；异常消息不写入 RunStore。
        """

        if not isinstance(prepared_task, PreparedTask):
            raise TypeError("prepared_task 必须是 PreparedTask")
        canonical_task = deepcopy(prepared_task.trusted_task)
        task_view = deepcopy(prepared_task.agent_task)
        if (
            canonical_task.get("task_id") != attempt.task_id
            or task_view.get("task_id") != attempt.task_id
        ):
            raise ValueError("PreparedTask identity 与 Attempt 不一致")
        runtime_events = self._store.open_event_stream(
            attempt=attempt,
            producer_kind="runtime",
            producer_id="attempt-runner",
        )
        environment_events = self._store.open_event_stream(
            attempt=attempt,
            producer_kind="environment",
            producer_id="task-environment",
        )
        worker_events = self._store.open_event_stream(
            attempt=attempt,
            producer_kind="worker",
            producer_id="agent-system",
        )
        evaluator_events = self._store.open_event_stream(
            attempt=attempt,
            producer_kind="evaluator",
            producer_id="task-evaluator",
        )

        execution_outcome = ExecutionOutcome.INFRA_ERROR
        evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
        score: float | None = None
        summary_details: dict[str, Any] = {}
        primary_error: BaseException | None = None
        stage = "environment.start"
        agent_result: AgentRunResult | None = None

        runtime_events.append(
            event_type="attempt.started",
            data={"agent_view_fields": sorted(task_view)},
        )
        try:
            environment_events.append(
                event_type="environment.starting",
                data={},
            )
            environment.start()
            environment_events.append(
                event_type="environment.started",
                data={},
            )

            stage = "environment.prepare"
            environment.prepare(canonical_task)
            environment_events.append(
                event_type="environment.prepared",
                data={},
            )

            stage = "agent.run"
            execution_outcome = ExecutionOutcome.RUNNING
            worker_events.append(event_type="agent.started", data={})
            agent_result = agent.run(task_view, environment)
            _validate_agent_result(agent_result)
            execution_outcome = ExecutionOutcome.SUCCEEDED
            worker_events.append(
                event_type="agent.completed",
                data={
                    "step_count": agent_result.step_count,
                    "termination": agent_result.termination,
                },
            )

            stage = "evaluator.evaluate"
            evaluation_outcome = EvaluationOutcome.RUNNING
            evaluator_events.append(event_type="evaluation.started", data={})
            evaluation = evaluator.evaluate(
                canonical_task,
                agent_result.final_output,
                environment,
            )
            _validate_runtime_evaluation(evaluation)
            score = float(evaluation.score)
            evaluation_outcome = (
                EvaluationOutcome.PASSED
                if evaluation.passed
                else EvaluationOutcome.FAILED
            )
            summary_details.update(dict(evaluation.details))
            summary_details["step_count"] = agent_result.step_count
            summary_details["termination"] = agent_result.termination
            evaluator_events.append(
                event_type="evaluation.completed",
                data={
                    "outcome": evaluation_outcome.value,
                    "score": score,
                    "details": dict(evaluation.details),
                },
            )
        except BaseException as error:
            primary_error = error
            summary_details = {
                "failure_stage": stage,
                "exception_type": type(error).__name__,
            }
            if stage == "agent.run":
                execution_outcome = ExecutionOutcome.FAILED
                evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
            elif stage == "evaluator.evaluate":
                execution_outcome = ExecutionOutcome.SUCCEEDED
                evaluation_outcome = EvaluationOutcome.ERROR
            else:
                execution_outcome = ExecutionOutcome.INFRA_ERROR
                evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
            score = None
            runtime_events.append(
                event_type="attempt.stage_failed",
                data=summary_details,
                level="ERROR",
            )
        finally:
            try:
                environment.close()
                environment_events.append(
                    event_type="environment.closed",
                    data={},
                )
            except BaseException as cleanup_error:
                environment_events.append(
                    event_type="environment.cleanup_failed",
                    data={"exception_type": type(cleanup_error).__name__},
                    level="ERROR",
                )
                execution_outcome = ExecutionOutcome.INFRA_ERROR
                if primary_error is None:
                    primary_error = cleanup_error
                    summary_details = {
                        "failure_stage": "environment.close",
                        "exception_type": type(cleanup_error).__name__,
                    }

        self._store.finish_attempt(
            attempt=attempt,
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
            score=score,
            details=summary_details,
        )
        runtime_events.append(
            event_type="attempt.finished",
            data={
                "execution_outcome": execution_outcome.value,
                "evaluation_outcome": evaluation_outcome.value,
                "score": score,
            },
        )
        if primary_error is not None:
            raise primary_error
        return RuntimeAttemptResult(
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
            score=score,
        )


def _validate_agent_result(result: AgentRunResult) -> None:
    """验证 Agent System 返回统一且可持久化的结果。

    输入参数：
        result：Agent 的公开结果。
    输出返回值：
        无；合法时正常返回。
    异常：
        TypeError/ValueError：类型、步数或终止原因无效。
    """

    if not isinstance(result, AgentRunResult):
        raise TypeError("Agent System 必须返回 AgentRunResult")
    if not isinstance(result.final_output, str):
        raise TypeError("Agent final_output 必须是字符串")
    if (
        not isinstance(result.step_count, int)
        or isinstance(result.step_count, bool)
        or result.step_count < 0
    ):
        raise ValueError("Agent step_count 必须是非负整数")
    if not isinstance(result.termination, str) or not result.termination:
        raise ValueError("Agent termination 必须是非空字符串")


def _validate_runtime_evaluation(evaluation: RuntimeEvaluation) -> None:
    """验证 evaluator 结果不会产生非法或越界 score。

    输入参数：
        evaluation：evaluator 返回的统一结果。
    输出返回值：
        无；合法时正常返回。
    异常：
        TypeError/ValueError：结果类型、通过标志或 score 不合规。
    """

    if not isinstance(evaluation, RuntimeEvaluation):
        raise TypeError("evaluator 必须返回 RuntimeEvaluation")
    if not isinstance(evaluation.passed, bool):
        raise TypeError("evaluation passed 必须是 bool")
    if (
        not isinstance(evaluation.score, (int, float))
        or isinstance(evaluation.score, bool)
        or not 0.0 <= float(evaluation.score) <= 1.0
    ):
        raise ValueError("evaluation score 必须在 [0, 1] 范围内")
    if not isinstance(evaluation.details, Mapping):
        raise TypeError("evaluation details 必须是 Mapping")
