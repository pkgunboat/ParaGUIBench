"""不依赖具体 Agent、任务或 evaluator 的有界 DAG 并发调度器。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .contracts import (
    ExecutionPlan,
    ScheduleResult,
    SubtaskResult,
    SubtaskSpec,
    SubtaskStatus,
)

SubtaskExecutor = Callable[
    [SubtaskSpec, tuple[SubtaskResult, ...], Any],
    SubtaskResult,
]


class DAGScheduler:
    """并发执行 ready subtask，并显式阻塞失败节点的下游。"""

    def __init__(self, *, max_workers: int) -> None:
        """构造有界 scheduler。

        输入参数：
            max_workers：同一时刻最多执行的 worker 数量，范围 1–64。
        输出返回值：
            无；构造阶段不创建线程或外部资源。
        """

        if (
            not isinstance(max_workers, int)
            or isinstance(max_workers, bool)
            or not 1 <= max_workers <= 64
        ):
            raise ValueError("max_workers 必须是 1–64 的整数")
        self._max_workers = max_workers

    def run(
        self,
        *,
        plan: ExecutionPlan,
        execute: SubtaskExecutor,
        runtime_context: Any,
    ) -> ScheduleResult:
        """按 DAG wave 并发执行节点并返回稳定顺序终态。

        输入参数：
            plan：已完成闭包和无环校验的 execution plan。
            execute：具体 Agent System 注入的 worker 执行函数。
            runtime_context：原样传给 worker 的运行上下文；framework 不读取。
        输出返回值：
            包含每个节点成功、失败或阻塞终态的 ``ScheduleResult``。
        """

        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan 必须是 ExecutionPlan")
        if not callable(execute):
            raise TypeError("execute 必须可调用")

        pending = {item.subtask_id for item in plan.subtasks}
        completed: dict[str, SubtaskResult] = {}

        while pending:
            self._mark_blocked_dependants(
                plan=plan,
                pending=pending,
                completed=completed,
            )
            ready = [
                item
                for item in plan.subtasks
                if item.subtask_id in pending
                and all(
                    dependency_id in completed
                    and completed[dependency_id].status
                    is SubtaskStatus.SUCCEEDED
                    for dependency_id in item.depends_on
                )
            ]
            if not ready:
                if pending:
                    raise RuntimeError("validated DAG scheduler made no progress")
                break

            with ThreadPoolExecutor(
                max_workers=min(self._max_workers, len(ready))
            ) as executor_pool:
                futures = {
                    executor_pool.submit(
                        _execute_safely,
                        execute,
                        subtask,
                        tuple(
                            completed[dependency_id]
                            for dependency_id in subtask.depends_on
                        ),
                        runtime_context,
                    ): subtask
                    for subtask in ready
                }
                for future in as_completed(futures):
                    subtask = futures[future]
                    result = future.result()
                    if result.subtask_id != subtask.subtask_id:
                        result = SubtaskResult(
                            subtask_id=subtask.subtask_id,
                            status=SubtaskStatus.FAILED,
                            output="",
                            step_count=0,
                            failure_type="result_identity_mismatch",
                        )
                    completed[subtask.subtask_id] = result
                    pending.remove(subtask.subtask_id)

        return ScheduleResult(
            results=tuple(completed[item.subtask_id] for item in plan.subtasks)
        )

    @staticmethod
    def _mark_blocked_dependants(
        *,
        plan: ExecutionPlan,
        pending: set[str],
        completed: dict[str, SubtaskResult],
    ) -> None:
        """把已有失败依赖的节点递归标记为 BLOCKED。

        输入参数：
            plan：原始稳定顺序 plan。
            pending：尚未产生终态的 subtask 标识集合。
            completed：已经产生终态的结果映射。
        输出返回值：
            无；原地更新 ``pending`` 与 ``completed``，不会调用 worker。
        """

        while True:
            newly_blocked = [
                item
                for item in plan.subtasks
                if item.subtask_id in pending
                and any(
                    dependency_id in completed
                    and completed[dependency_id].status
                    is not SubtaskStatus.SUCCEEDED
                    for dependency_id in item.depends_on
                )
            ]
            if not newly_blocked:
                return
            for subtask in newly_blocked:
                completed[subtask.subtask_id] = SubtaskResult(
                    subtask_id=subtask.subtask_id,
                    status=SubtaskStatus.BLOCKED,
                    output="",
                    step_count=0,
                    failure_type="dependency_failed",
                )
                pending.remove(subtask.subtask_id)


def _execute_safely(
    execute: SubtaskExecutor,
    subtask: SubtaskSpec,
    dependency_results: tuple[SubtaskResult, ...],
    runtime_context: Any,
) -> SubtaskResult:
    """执行单个 worker，并把异常折叠为仅含类型的失败结果。

    输入参数：
        execute：具体 Agent System 提供的 worker 函数。
        subtask：当前节点 specification。
        dependency_results：按 ``depends_on`` 排序的成功依赖结果。
        runtime_context：framework 不读取的运行上下文。
    输出返回值：
        合法 worker 结果；异常或返回类型错误时为脱敏 ``FAILED`` 结果。
    """

    try:
        result = execute(subtask, dependency_results, runtime_context)
        if not isinstance(result, SubtaskResult):
            raise TypeError("worker must return SubtaskResult")
        return result
    except BaseException as error:
        return SubtaskResult(
            subtask_id=subtask.subtask_id,
            status=SubtaskStatus.FAILED,
            output="",
            step_count=0,
            failure_type=type(error).__name__,
        )
