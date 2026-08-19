"""Holo3 GUI Agent — 基于 H Company Holo3-35B-A3B 的 OSWorld 兼容 agent。

仅实现单步定位所需的最小子集，遵循 Holo3 官方协议：
- 输出统一为扁平 JSON `{note, thought, tool_call}`
- 坐标使用 0-1000 整数归一化
- 通过 Pydantic discriminated union 约束工具调用
"""

from .tools import (
    AnswerArgs,
    ClickArgs,
    DoubleClickArgs,
    DragArgs,
    KeyArgs,
    MoveMouseArgs,
    ScreenshotArgs,
    ScrollArgs,
    Step,
    TerminateArgs,
    WaitArgs,
    WriteArgs,
)

__all__ = [
    "Step",
    "ClickArgs",
    "DoubleClickArgs",
    "MoveMouseArgs",
    "DragArgs",
    "WriteArgs",
    "KeyArgs",
    "ScrollArgs",
    "WaitArgs",
    "ScreenshotArgs",
    "TerminateArgs",
    "AnswerArgs",
]
