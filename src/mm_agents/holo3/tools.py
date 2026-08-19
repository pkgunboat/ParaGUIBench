"""Holo3 工具集定义（Pydantic discriminated union）。

每个工具是一个独立的 Pydantic Model，通过 `tool_name: Literal[...]` 作为判别字段。
`Step` 是顶层包装，承载 note/thought/tool_call 三个字段，对应 Holo3 协议规定的
扁平 JSON 输出格式。

坐标约定：所有 x/y 字段都是 [0, 1000] 闭区间的整数，原点在屏幕左上角，由 agent 端
按当前截图分辨率换算回像素。
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 单点操作：click / double_click / move_mouse                                  #
# --------------------------------------------------------------------------- #
class ClickArgs(BaseModel):
    """在 (x, y) 处单击。

    参数:
        tool_name: 固定 "click"，discriminator。
        element:   目标 UI 元素的自然语言描述，用于审计与日志。
        x, y:      [0, 1000] 归一化坐标。
        button:    鼠标按键，默认左键。
    """

    tool_name: Literal["click"]
    element: str = Field(description="Detailed description of the target UI element")
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)
    button: Literal["left", "right", "middle"] = "left"


class DoubleClickArgs(BaseModel):
    """双击 (x, y)。常用于打开应用、文件。"""

    tool_name: Literal["double_click"]
    element: str = Field(description="Detailed description of the target UI element")
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)


class MoveMouseArgs(BaseModel):
    """把鼠标移动到 (x, y) 但不点击。用于触发 hover 效果。"""

    tool_name: Literal["move_mouse"]
    element: str = Field(description="Detailed description of the target UI element")
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)


# --------------------------------------------------------------------------- #
# 拖拽                                                                        #
# --------------------------------------------------------------------------- #
class DragArgs(BaseModel):
    """从 (x1, y1) 拖到 (x2, y2)。"""

    tool_name: Literal["drag"]
    element: str = Field(description="Description of what is being dragged")
    x1: int = Field(ge=0, le=1000)
    y1: int = Field(ge=0, le=1000)
    x2: int = Field(ge=0, le=1000)
    y2: int = Field(ge=0, le=1000)


# --------------------------------------------------------------------------- #
# 键盘输入                                                                     #
# --------------------------------------------------------------------------- #
class WriteArgs(BaseModel):
    """向当前焦点元素写入文本，不会先点击。

    参数:
        content:     要写入的文本。
        press_enter: 写入后是否回车提交。
        overwrite:   是否先全选清空再写入（实现上对应 Cmd+A / Ctrl+A 再 type）。
    """

    tool_name: Literal["write"]
    content: str
    press_enter: bool = False
    overwrite: bool = False


class KeyArgs(BaseModel):
    """按下一组按键（hotkey 组合）。

    单键传 ["enter"]、组合键传 ["cmd", "c"] 或 ["ctrl", "shift", "t"]。
    """

    tool_name: Literal["key"]
    keys: list[str] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 滚动                                                                        #
# --------------------------------------------------------------------------- #
class ScrollArgs(BaseModel):
    """在 (x, y) 位置朝指定方向滚动 amount 格。"""

    tool_name: Literal["scroll"]
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)
    direction: Literal["up", "down", "left", "right"]
    amount: int = Field(ge=1, default=3)


# --------------------------------------------------------------------------- #
# 流程控制                                                                     #
# --------------------------------------------------------------------------- #
class WaitArgs(BaseModel):
    """等待 seconds 秒。"""

    tool_name: Literal["wait"]
    seconds: float = Field(ge=0.0, default=1.0)


class ScreenshotArgs(BaseModel):
    """强制下一轮重新截图，本步不做任何操作。"""

    tool_name: Literal["screenshot"]


class TerminateArgs(BaseModel):
    """结束当前任务。

    status="success" → agent 报 DONE；
    status="failure" → agent 报 FAIL（不可达成 / 卡死）。
    """

    tool_name: Literal["terminate"]
    status: Literal["success", "failure"]


class AnswerArgs(BaseModel):
    """对 QA 类任务给出最终文本答案。"""

    tool_name: Literal["answer"]
    content: str


# --------------------------------------------------------------------------- #
# 顶层 Step                                                                    #
# --------------------------------------------------------------------------- #
ToolCall = Annotated[
    Union[
        ClickArgs,
        DoubleClickArgs,
        MoveMouseArgs,
        DragArgs,
        WriteArgs,
        KeyArgs,
        ScrollArgs,
        WaitArgs,
        ScreenshotArgs,
        TerminateArgs,
        AnswerArgs,
    ],
    Field(discriminator="tool_name"),
]


class Step(BaseModel):
    """Holo3 单步输出的扁平 JSON。

    字段:
        note:      跨步骤持久记忆。无需记录时设为 null。
        thought:   一句话计划。
        tool_call: 本步要执行的工具调用，schema 由 discriminated union 约束。
    """

    note: str | None = Field(
        default=None,
        description=(
            "Durable memory across steps. Set to null when nothing new is worth "
            "recording. Past reasoning is dropped between turns; only `note` and "
            "`thought` persist."
        ),
    )
    thought: str = Field(description="One-line plan for the next action")
    tool_call: ToolCall


ALL_TOOL_NAMES: tuple[str, ...] = (
    "click",
    "double_click",
    "move_mouse",
    "drag",
    "write",
    "key",
    "scroll",
    "wait",
    "screenshot",
    "terminate",
    "answer",
)
