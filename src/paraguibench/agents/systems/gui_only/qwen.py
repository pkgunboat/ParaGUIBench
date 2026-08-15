"""把共享 Qwen GUI worker 映射为 AttemptRunner 可调用的 GUI-only 系统。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from paraguibench.agents import AgentRunResult
from paraguibench.agents.workers import GUIWorker


class QwenGUIOnlyAgentError(RuntimeError):
    """表示 Qwen GUI-only task view 或 worker 装配不满足契约。"""


class QwenGUIOnlyAgentSystem:
    """使用一个 Qwen GUI worker 顺序完成整个 benchmark task。"""

    def __init__(self, *, worker: GUIWorker) -> None:
        """注入共享 worker，不在 Agent System 层重复模型或动作逻辑。

        输入参数：
            worker：实现 ``run(instruction, environment)`` 的 Qwen GUI worker。
        输出返回值：
            无；构造阶段不读取 task、凭据或访问环境。
        """

        if not hasattr(worker, "run"):
            raise TypeError("worker 缺少 run")
        self._worker = worker

    def run(
        self,
        task_view: dict[str, Any],
        environment: Any,
    ) -> AgentRunResult:
        """把 gold-free task instruction 交给单个 worker 并映射结果。

        输入参数：
            task_view：AttemptRunner 产生的 Agent allowlist 投影。
            environment：当前完整任务独占的已准备桌面环境。
        输出返回值：
            与 worker 文本、步数和终止类型一一对应的 ``AgentRunResult``。
        异常：
            QwenGUIOnlyAgentError：task view 或 instruction 类型、长度无效。
        """

        instruction = _read_instruction(task_view)
        result = self._worker.run(instruction, environment)
        return AgentRunResult(
            final_output=result.final_output,
            step_count=result.step_count,
            termination=result.termination,
        )


def _read_instruction(task_view: Mapping[str, Any]) -> str:
    """读取 GUI-only task view 中的有界 instruction。

    输入参数：
        task_view：Agent 可见字段 Mapping。
    输出返回值：
        长度 1–20000 的非空 instruction。
    异常：
        QwenGUIOnlyAgentError：task view 或 instruction 无效。
    """

    if not isinstance(task_view, Mapping):
        raise QwenGUIOnlyAgentError("task_view 必须是 Mapping")
    instruction = task_view.get("instruction")
    if not isinstance(instruction, str) or not instruction or len(instruction) > 20_000:
        raise QwenGUIOnlyAgentError("task_view 缺少有界非空 instruction")
    return instruction
