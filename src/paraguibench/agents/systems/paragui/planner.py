"""将 provider-neutral JSON backend 输出转为严格 ParaGUI DAG。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Protocol

from paraguibench.framework import (
    ExecutionPlan,
    SubtaskResult,
    SubtaskSpec,
)

_PLAN_FIELDS = frozenset({"subtasks"})
_SUBTASK_FIELDS = frozenset(
    {"id", "instruction", "depends_on", "worker_role"}
)


class StructuredPlanningBackend(Protocol):
    """定义 provider integration 向 ParaGUI planner 提供的结构化接口。"""

    def create_plan(self, task_view: dict[str, Any]) -> Mapping[str, Any]:
        """返回 JSON-compatible plan object。"""

    def create_answer(
        self,
        task_view: dict[str, Any],
        result_view: tuple[dict[str, Any], ...],
    ) -> str:
        """根据 allowlist subtask 结果生成最终文本。"""


class StructuredParaGUIPlanner:
    """严格解析 backend 计划并构造通用 framework contracts。"""

    def __init__(self, *, backend: StructuredPlanningBackend) -> None:
        """绑定一个不被 planner 直接持久化的 provider backend。

        输入参数：
            backend：实现 ``create_plan`` 与 ``create_answer`` 的结构化 backend。
                API key、endpoint 和 SDK 生命周期由 integration 层管理。
        输出返回值：
            无；构造阶段不解析凭据、不调用 provider。
        """

        if not hasattr(backend, "create_plan") or not hasattr(
            backend, "create_answer"
        ):
            raise TypeError("planning backend 缺少结构化接口")
        self._backend = backend

    def plan(self, task_view: dict[str, Any]) -> ExecutionPlan:
        """调用 backend，并把严格 JSON object 转成无环 ExecutionPlan。

        输入参数：
            task_view：AttemptRunner 构造的 gold-free Agent task view。
        输出返回值：
            已完成字段、标识、依赖闭包与无环校验的 execution plan。
        异常：
            TypeError/ValueError：backend 返回形状无效；异常不回显未知字段值。
        """

        if not isinstance(task_view, dict):
            raise TypeError("planner task_view 必须是 dict")
        raw_plan = self._backend.create_plan(deepcopy(task_view))
        if not isinstance(raw_plan, Mapping):
            raise TypeError("planner backend 必须返回 object")
        if set(raw_plan) != _PLAN_FIELDS:
            raise ValueError("planner top-level fields 不符合契约")
        raw_subtasks = raw_plan.get("subtasks")
        if not isinstance(raw_subtasks, list):
            raise TypeError("planner subtasks 必须是 array")

        subtasks = tuple(
            _parse_subtask(raw_subtask)
            for raw_subtask in raw_subtasks
        )
        return ExecutionPlan(subtasks=subtasks)

    def synthesize(
        self,
        task_view: dict[str, Any],
        results: tuple[SubtaskResult, ...],
    ) -> str:
        """把稳定 allowlist 结果投影交给 backend 生成最终答案。

        输入参数：
            task_view：gold-free Agent task view。
            results：按原 plan 顺序排列的全部 subtask 终态。
        输出返回值：
            backend 生成的字符串；不会由 planner 或 framework 自动落盘。
        """

        if not isinstance(task_view, dict):
            raise TypeError("planner task_view 必须是 dict")
        if not isinstance(results, tuple) or not all(
            isinstance(item, SubtaskResult) for item in results
        ):
            raise TypeError("planner results 必须是 SubtaskResult tuple")
        result_view = tuple(_project_result(item) for item in results)
        final_output = self._backend.create_answer(
            deepcopy(task_view),
            result_view,
        )
        if not isinstance(final_output, str):
            raise TypeError("planning backend answer 必须是字符串")
        return final_output


def _parse_subtask(raw_subtask: Any) -> SubtaskSpec:
    """把单个 JSON object 转为 SubtaskSpec。

    输入参数：
        raw_subtask：backend 返回的一个 subtask JSON 值。
    输出返回值：
        经过字段和基础类型校验的 ``SubtaskSpec``。
    异常：
        TypeError/ValueError：字段缺失、额外字段或字段类型不合规；不回显值。
    """

    if not isinstance(raw_subtask, Mapping):
        raise TypeError("planner subtask 必须是 object")
    fields = set(raw_subtask)
    required_fields = {"id", "instruction"}
    if not required_fields.issubset(fields) or not fields.issubset(
        _SUBTASK_FIELDS
    ):
        raise ValueError("planner subtask fields 不符合契约")

    depends_on = raw_subtask.get("depends_on", [])
    if not isinstance(depends_on, list) or not all(
        isinstance(item, str) for item in depends_on
    ):
        raise TypeError("planner depends_on 必须是字符串 array")
    worker_role = raw_subtask.get("worker_role", "gui")
    if not isinstance(worker_role, str):
        raise TypeError("planner worker_role 必须是字符串")
    return SubtaskSpec(
        subtask_id=raw_subtask["id"],
        instruction=raw_subtask["instruction"],
        depends_on=tuple(depends_on),
        worker_role=worker_role,
    )


def _project_result(result: SubtaskResult) -> dict[str, Any]:
    """构造 synthesis backend 可见的显式结果投影。

    输入参数：
        result：framework 返回的不可变 subtask 终态。
    输出返回值：
        仅含身份、状态、输出、步数和稳定失败类型的 JSON-compatible 字典。
    """

    return {
        "subtask_id": result.subtask_id,
        "status": result.status.value,
        "output": result.output,
        "step_count": result.step_count,
        "failure_type": result.failure_type,
    }
