"""可复用 planner–worker DAG 调度内核的契约测试。"""

from __future__ import annotations

from threading import Barrier, Lock
from typing import Any

from paraguibench.framework import (
    DAGScheduler,
    ExecutionPlan,
    SubtaskResult,
    SubtaskSpec,
    SubtaskStatus,
)


def test_scheduler_runs_independent_subtasks_concurrently_then_dependency() -> None:
    """验证独立节点并发执行，依赖节点只在前置结果完整后启动。

    输入参数：
        无；使用含两个并行根节点和一个汇总节点的合成 DAG。
    输出返回值：
        无；Barrier 证明两个根节点同时占用 worker，汇总节点收到稳定排序的
        前置输出，最终结果按 plan 顺序返回。
    """

    roots_started = Barrier(2, timeout=2)
    calls: list[tuple[str, tuple[str, ...]]] = []
    calls_lock = Lock()

    def execute(
        subtask: SubtaskSpec,
        dependency_results: tuple[SubtaskResult, ...],
        runtime_context: Any,
    ) -> SubtaskResult:
        """执行合成 subtask 并记录其可见依赖。

        输入参数：
            subtask：当前 DAG 节点。
            dependency_results：已成功完成且按 depends_on 排序的结果。
            runtime_context：调用方透传的非共享合成上下文。
        输出返回值：
            与当前 subtask 对应的成功结果。
        """

        assert runtime_context == {"scope": "synthetic"}
        dependency_ids = tuple(item.subtask_id for item in dependency_results)
        with calls_lock:
            calls.append((subtask.subtask_id, dependency_ids))
        if subtask.subtask_id in {"search-a", "search-b"}:
            roots_started.wait()
        return SubtaskResult(
            subtask_id=subtask.subtask_id,
            status=SubtaskStatus.SUCCEEDED,
            output=f"result:{subtask.subtask_id}",
            step_count=1,
        )

    plan = ExecutionPlan(
        subtasks=(
            SubtaskSpec("search-a", "Search source A."),
            SubtaskSpec("search-b", "Search source B."),
            SubtaskSpec(
                "synthesize",
                "Synthesize both sources.",
                depends_on=("search-a", "search-b"),
            ),
        )
    )

    result = DAGScheduler(max_workers=2).run(
        plan=plan,
        execute=execute,
        runtime_context={"scope": "synthetic"},
    )

    assert tuple(item.subtask_id for item in result.results) == (
        "search-a",
        "search-b",
        "synthesize",
    )
    assert result.succeeded is True
    assert calls[-1] == ("synthesize", ("search-a", "search-b"))


def test_scheduler_blocks_dependants_after_worker_failure_without_executing_them() -> None:
    """验证失败节点的下游被显式 BLOCKED，且不会误执行。

    输入参数：
        无；首节点返回失败，第二节点依赖首节点。
    输出返回值：
        无；worker 只被调用一次，下游有稳定的 BLOCKED 结果。
    """

    executed: list[str] = []

    def execute(
        subtask: SubtaskSpec,
        dependency_results: tuple[SubtaskResult, ...],
        runtime_context: Any,
    ) -> SubtaskResult:
        """返回一个不包含异常消息的合成失败结果。

        输入参数：
            subtask：当前 DAG 节点。
            dependency_results：当前节点的成功依赖结果。
            runtime_context：本测试未使用的上下文。
        输出返回值：
            首节点的失败终态。
        """

        del dependency_results, runtime_context
        executed.append(subtask.subtask_id)
        return SubtaskResult(
            subtask_id=subtask.subtask_id,
            status=SubtaskStatus.FAILED,
            output="",
            step_count=2,
            failure_type="SyntheticWorkerError",
        )

    plan = ExecutionPlan(
        subtasks=(
            SubtaskSpec("root", "Fail safely."),
            SubtaskSpec("dependent", "Must not run.", depends_on=("root",)),
        )
    )

    result = DAGScheduler(max_workers=2).run(
        plan=plan,
        execute=execute,
        runtime_context=None,
    )

    assert executed == ["root"]
    assert result.succeeded is False
    assert result.results[0].status is SubtaskStatus.FAILED
    assert result.results[1].status is SubtaskStatus.BLOCKED
    assert result.results[1].failure_type == "dependency_failed"


def test_execution_plan_rejects_unknown_dependency_and_cycle() -> None:
    """验证 malformed planner 输出在启动 worker 前 fail closed。

    输入参数：
        无；分别构造未知依赖与循环依赖。
    输出返回值：
        无；两类非法 DAG 都在 ``ExecutionPlan`` 构造阶段被拒绝。
    """

    try:
        ExecutionPlan(
            subtasks=(
                SubtaskSpec("node-a", "A", depends_on=("missing",)),
            )
        )
    except ValueError as error:
        assert "unknown dependency" in str(error)
    else:
        raise AssertionError("unknown dependency must be rejected")

    try:
        ExecutionPlan(
            subtasks=(
                SubtaskSpec("node-a", "A", depends_on=("node-b",)),
                SubtaskSpec("node-b", "B", depends_on=("node-a",)),
            )
        )
    except ValueError as error:
        assert "cycle" in str(error)
    else:
        raise AssertionError("cycle must be rejected")
