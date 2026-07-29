"""所有可运行 Agent System 共享的最小结果契约。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """保存 Agent System 的模型无关执行结果。

    输入参数：
        final_output：交给可信 evaluator 的最终文本；RunStore 默认不持久化。
        step_count：本 Agent System 消耗的非负动作步数。
        termination：稳定终止类型，例如 ``finished``、``partial`` 或
            ``max_steps``，不得包含异常消息或模型原文。
    输出返回值：
        不可变的 Agent 执行结果，供 runtime 在环境关闭前评价。
    """

    final_output: str
    step_count: int
    termination: str
