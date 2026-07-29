"""把 ParaGUI planner、通用 DAG scheduler 与 worker 装配为 Agent System。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Protocol

from paraguibench.agents import AgentRunResult
from paraguibench.framework import (
    DAGScheduler,
    ExecutionPlan,
    SubtaskResult,
    SubtaskSpec,
)


class ParaGUIPlanner(Protocol):
    """定义 ParaGUI 具体 planner adapter 必须实现的最小接口。"""

    def plan(self, task_view: dict[str, Any]) -> ExecutionPlan:
        """根据 gold-free task view 产生已验证 DAG。"""

    def synthesize(
        self,
        task_view: dict[str, Any],
        results: tuple[SubtaskResult, ...],
    ) -> str:
        """根据全部 subtask 终态生成 evaluator 可消费的最终文本。"""


class ParaGUIWorker(Protocol):
    """定义 ParaGUI worker adapter 的模型与环境无关接口。"""

    def run_subtask(
        self,
        subtask: SubtaskSpec,
        dependency_results: tuple[SubtaskResult, ...],
        environment: Any,
    ) -> SubtaskResult:
        """在调用方环境或环境池中执行一个 subtask。"""


class ParaGUIAgentSystem:
    """运行 planner–parallel workers–synthesis 的完整 ParaGUI 系统。"""

    def __init__(
        self,
        *,
        planner: ParaGUIPlanner,
        worker: ParaGUIWorker,
        max_workers: int,
    ) -> None:
        """注入具体 planner/worker，并建立有界通用 scheduler。

        输入参数：
            planner：只负责计划与结果聚合的 ParaGUI planner adapter。
            worker：执行单个 subtask 的 worker adapter；可自行从 environment
                pool 租用独占 VM。
            max_workers：同一 DAG wave 的最大并发数，范围由 ``DAGScheduler``
                统一校验。
        输出返回值：
            无；构造阶段不调用模型、不创建线程，也不启动环境。
        """

        if not hasattr(planner, "plan") or not hasattr(planner, "synthesize"):
            raise TypeError("planner 缺少 plan 或 synthesize")
        if not hasattr(worker, "run_subtask"):
            raise TypeError("worker 缺少 run_subtask")
        self._planner = planner
        self._worker = worker
        self._scheduler = DAGScheduler(max_workers=max_workers)

    def run(
        self,
        task_view: dict[str, Any],
        environment: Any,
    ) -> AgentRunResult:
        """计划、并发执行并聚合一个 gold-free benchmark task。

        输入参数：
            task_view：AttemptRunner 产生的 Agent allowlist 投影。
            environment：单环境或多 worker 环境池；framework 不读取，其资源
                分配策略由 worker adapter 实现。
        输出返回值：
            最终合成文本、所有实际 worker 步数之和和 finished/partial 终态。
        """

        if not isinstance(task_view, dict):
            raise TypeError("ParaGUI task_view 必须是 dict")
        planner_view = deepcopy(task_view)
        plan = self._planner.plan(planner_view)
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("ParaGUI planner 必须返回 ExecutionPlan")

        schedule = self._scheduler.run(
            plan=plan,
            execute=self._run_worker,
            runtime_context=environment,
        )
        final_output = self._planner.synthesize(
            deepcopy(task_view),
            schedule.results,
        )
        if not isinstance(final_output, str):
            raise TypeError("ParaGUI planner synthesis 必须返回字符串")
        return AgentRunResult(
            final_output=final_output,
            step_count=sum(item.step_count for item in schedule.results),
            termination="finished" if schedule.succeeded else "partial",
        )

    def _run_worker(
        self,
        subtask: SubtaskSpec,
        dependency_results: tuple[SubtaskResult, ...],
        environment: Any,
    ) -> SubtaskResult:
        """把 framework executor 调用委派给具体 ParaGUI worker。

        输入参数：
            subtask：当前已验证 DAG 节点。
            dependency_results：按依赖声明顺序排列的成功前置结果。
            environment：Attempt runtime 提供的环境或环境池。
        输出返回值：
            worker 的 ``SubtaskResult``；异常由 scheduler 折叠为脱敏失败。
        """

        return self._worker.run_subtask(
            subtask,
            dependency_results,
            environment,
        )
