"""GUI-only 与 planner–worker Agent System 共享的 worker 契约。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol

_TERMINATION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class GUIWorkerResult:
    """保存一次独立 GUI worker 执行的模型无关结果。

    输入参数：
        final_output：任务完成、失败或请求用户时产生的最终文本，仅在内存中
            传给上层 Agent System，不由 worker 自行持久化。
        step_count：实际消耗的模型动作步数，必须是非负整数。
        termination：稳定终止类型，例如 ``finished``、``infeasible``、
            ``call_user`` 或 ``max_steps``，不得包含模型原文或异常消息。
    输出返回值：
        不可变的 worker 结果，可分别映射到完整任务或 ParaGUI subtask 契约。
    """

    final_output: str
    step_count: int
    termination: str

    def __post_init__(self) -> None:
        """校验 worker 结果不会携带无界或不稳定的结构字段。

        输入参数：
            无；读取当前 dataclass 字段。
        输出返回值：
            无；合法结果正常返回，非法结果抛出 ``TypeError`` 或
            ``ValueError``。
        """

        if not isinstance(self.final_output, str):
            raise TypeError("worker final_output 必须是字符串")
        if (
            not isinstance(self.step_count, int)
            or isinstance(self.step_count, bool)
            or self.step_count < 0
        ):
            raise ValueError("worker step_count 必须是非负整数")
        if (
            not isinstance(self.termination, str)
            or _TERMINATION_PATTERN.fullmatch(self.termination) is None
        ):
            raise ValueError("worker termination 必须是稳定的小写标识符")


class GUIWorker(Protocol):
    """定义可被 GUI-only 或 ParaGUI adapter 复用的最小 worker 接口。"""

    def run(self, instruction: str, environment: Any) -> GUIWorkerResult:
        """在一个调用期内独占的桌面环境中执行 GUI 指令。

        输入参数：
            instruction：不含 gold answer 的自包含任务或子任务说明。
            environment：具有 GUI controller 的已准备、独占环境。
        输出返回值：
            模型无关的 ``GUIWorkerResult``。
        """
