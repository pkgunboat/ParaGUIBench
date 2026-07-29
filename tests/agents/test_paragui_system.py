"""ParaGUI Agent System 与通用 framework 的装配契约测试。"""

from __future__ import annotations

from typing import Any

from paraguibench.agents.systems.paragui import ParaGUIAgentSystem
from paraguibench.framework import (
    ExecutionPlan,
    SubtaskResult,
    SubtaskSpec,
    SubtaskStatus,
)


def test_paragui_plans_executes_dependencies_and_synthesizes_answer() -> None:
    """验证 ParaGUI 将 planner、并发 worker 与最终聚合串成 Agent System。

    输入参数：
        无；使用两个检索节点和一个依赖二者的整合节点。
    输出返回值：
        无；最终答案、总 worker 步数、终止原因和依赖结果均符合契约。
    """

    class Planner:
        """提供固定 DAG 并记录聚合输入的合成 planner。"""

        def __init__(self) -> None:
            """初始化调用记录。

            输入参数：
                无。
            输出返回值：
                无。
            """

            self.plan_task_view: dict[str, Any] | None = None
            self.synthesis_results: tuple[SubtaskResult, ...] = ()

        def plan(self, task_view: dict[str, Any]) -> ExecutionPlan:
            """为合成任务返回三节点计划。

            输入参数：
                task_view：不含 gold 的 Agent 可见 task。
            输出返回值：
                依赖闭合的 ``ExecutionPlan``。
            """

            self.plan_task_view = task_view
            return ExecutionPlan(
                subtasks=(
                    SubtaskSpec("source-a", "Inspect A."),
                    SubtaskSpec("source-b", "Inspect B."),
                    SubtaskSpec(
                        "combine",
                        "Combine evidence.",
                        depends_on=("source-a", "source-b"),
                    ),
                )
            )

        def synthesize(
            self,
            task_view: dict[str, Any],
            results: tuple[SubtaskResult, ...],
        ) -> str:
            """根据稳定顺序结果返回最终答案。

            输入参数：
                task_view：原始 Agent task view 的独立副本。
                results：按 plan 顺序排列的全部 subtask 终态。
            输出返回值：
                evaluator 可消费的最终文本答案。
            """

            assert task_view["instruction"] == "Find both facts."
            self.synthesis_results = results
            return "<answer>combined</answer>"

    class Worker:
        """记录依赖并返回固定成功结果的合成 worker。"""

        def __init__(self) -> None:
            """初始化依赖记录。

            输入参数：
                无。
            输出返回值：
                无。
            """

            self.dependencies: dict[str, tuple[str, ...]] = {}

        def run_subtask(
            self,
            subtask: SubtaskSpec,
            dependency_results: tuple[SubtaskResult, ...],
            environment: Any,
        ) -> SubtaskResult:
            """执行 subtask 并保留依赖顺序。

            输入参数：
                subtask：当前 plan 节点。
                dependency_results：当前节点的成功前置结果。
                environment：AttemptRunner 提供的环境或环境池。
            输出返回值：
                三步成功结果。
            """

            assert environment is synthetic_environment
            self.dependencies[subtask.subtask_id] = tuple(
                item.subtask_id for item in dependency_results
            )
            return SubtaskResult(
                subtask_id=subtask.subtask_id,
                status=SubtaskStatus.SUCCEEDED,
                output=f"evidence:{subtask.subtask_id}",
                step_count=3,
            )

    planner = Planner()
    worker = Worker()
    synthetic_environment = object()
    agent = ParaGUIAgentSystem(
        planner=planner,
        worker=worker,
        max_workers=2,
    )

    result = agent.run(
        {
            "task_id": "synthetic-paragui",
            "instruction": "Find both facts.",
        },
        synthetic_environment,
    )

    assert result.final_output == "<answer>combined</answer>"
    assert result.step_count == 9
    assert result.termination == "finished"
    assert worker.dependencies["combine"] == ("source-a", "source-b")
    assert tuple(
        item.subtask_id for item in planner.synthesis_results
    ) == ("source-a", "source-b", "combine")


def test_paragui_returns_partial_after_failed_subtask_and_never_runs_dependant() -> None:
    """验证 worker 失败保留 partial evidence，且系统终态不伪装成功。

    输入参数：
        无；根节点失败，依赖节点由 framework 阻塞。
    输出返回值：
        无；planner 仍可聚合脱敏结果，termination 为 ``partial``。
    """

    class Planner:
        """提供失败 DAG 并从终态列表合成保守答案。"""

        def plan(self, task_view: dict[str, Any]) -> ExecutionPlan:
            """构造根节点及其下游。

            输入参数：
                task_view：Agent 可见 task。
            输出返回值：
                两节点 DAG。
            """

            del task_view
            return ExecutionPlan(
                subtasks=(
                    SubtaskSpec("root", "Inspect."),
                    SubtaskSpec(
                        "dependent",
                        "Use evidence.",
                        depends_on=("root",),
                    ),
                )
            )

        def synthesize(
            self,
            task_view: dict[str, Any],
            results: tuple[SubtaskResult, ...],
        ) -> str:
            """返回保守的不可完成标记。

            输入参数：
                task_view：Agent task view。
                results：失败和阻塞结果。
            输出返回值：
                一个不含异常原文的文本。
            """

            del task_view
            assert tuple(item.status for item in results) == (
                SubtaskStatus.FAILED,
                SubtaskStatus.BLOCKED,
            )
            return "<answer>incomplete</answer>"

    class Worker:
        """只允许根节点执行的失败 worker。"""

        def __init__(self) -> None:
            """初始化执行记录。

            输入参数：
                无。
            输出返回值：
                无。
            """

            self.executed: list[str] = []

        def run_subtask(
            self,
            subtask: SubtaskSpec,
            dependency_results: tuple[SubtaskResult, ...],
            environment: Any,
        ) -> SubtaskResult:
            """返回固定失败终态。

            输入参数：
                subtask：当前根节点。
                dependency_results：空依赖结果。
                environment：测试环境。
            输出返回值：
                仅含失败类型和 partial evidence 的结果。
            """

            del dependency_results, environment
            self.executed.append(subtask.subtask_id)
            return SubtaskResult(
                subtask_id=subtask.subtask_id,
                status=SubtaskStatus.FAILED,
                output="partial evidence",
                step_count=4,
                failure_type="WorkerTimeout",
            )

    worker = Worker()
    result = ParaGUIAgentSystem(
        planner=Planner(),
        worker=worker,
        max_workers=2,
    ).run(
        {
            "task_id": "synthetic-paragui-failure",
            "instruction": "Inspect safely.",
        },
        object(),
    )

    assert worker.executed == ["root"]
    assert result.final_output == "<answer>incomplete</answer>"
    assert result.step_count == 4
    assert result.termination == "partial"
