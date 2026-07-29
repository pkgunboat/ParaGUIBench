"""从 canonical task 构造不含 gold 的 Agent 可见投影。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

_AGENT_VISIBLE_FIELDS = (
    "task_id",
    "task_uid",
    "task_type",
    "task_source",
    "task_tag",
    "instruction",
    "agent_start_context",
)


def build_agent_task_view(task: Mapping[str, Any]) -> dict[str, Any]:
    """复制显式允许的 task 输入字段，并隔离全部评价与 gold 字段。

    输入参数：
        task：已完成非敏感环境物化的 canonical task。
    输出返回值：
        只包含任务身份、instruction 与可选启动上下文的深层副本；字段顺序
        固定，不包含 answer、accepted_answers、expected_urls 或 evaluator。
    异常：
        ValueError：task 缺少 Agent 执行必需的 ``task_id`` 或 instruction。
    """

    task_id = task.get("task_id")
    instruction = task.get("instruction")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Agent task view 需要非空 task_id")
    if not isinstance(instruction, str) or not instruction:
        raise ValueError("Agent task view 需要非空 instruction")
    return {
        field: deepcopy(task[field])
        for field in _AGENT_VISIBLE_FIELDS
        if field in task
    }
