"""Step → pyautogui 代码 / 像素坐标转换。

负责两件事：
1. 把模型输出的 0-1000 归一化坐标按当前截图分辨率还原为像素。
2. 把每个工具调用映射到一行可由 OSWorld 执行器直接 exec 的 pyautogui 语句。

`screenshot` / `terminate` / `answer` 没有可执行的 GUI 动作，分别返回 sentinel
字符串 "WAIT" / "DONE" / "FAIL"，让上层 agent 做相应处理。
"""

from __future__ import annotations

from typing import Optional

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


def _denorm(coord: int, axis_pixels: int) -> int:
    """0-1000 归一化值按某一轴的像素长度反归一化为整数像素。

    保持 [0, axis_pixels-1] 边界，避免 pyautogui 超出屏幕。
    """
    px = int(round(coord / 1000.0 * axis_pixels))
    return max(0, min(axis_pixels - 1, px))


def step_to_pixel(step: Step, w: int, h: int) -> Optional[tuple[int, int]]:
    """如果 step 的 tool_call 含有主要 (x, y)，返回其像素坐标，否则返回 None。

    用于测试时把模型预测的位置画到截图上做可视化校验。
    `drag` 取起点；`screenshot/terminate/answer/wait/key/write` 没有点。
    """
    tc = step.tool_call
    if isinstance(tc, (ClickArgs, DoubleClickArgs, MoveMouseArgs, ScrollArgs)):
        return _denorm(tc.x, w), _denorm(tc.y, h)
    if isinstance(tc, DragArgs):
        return _denorm(tc.x1, w), _denorm(tc.y1, h)
    return None


def step_to_pyautogui(step: Step, w: int, h: int) -> list[str]:
    """把 Step 转成可执行字符串列表。

    参数:
        step: 已解析的 Step。
        w, h: 当前截图像素分辨率。

    返回:
        - 常规动作: ["import pyautogui; pyautogui.xxx(...)"]
        - terminate(success): ["DONE"]
        - terminate(failure): ["FAIL"]
        - screenshot: ["WAIT"]
        - answer: ["ANSWER:<content>"]
    """
    tc = step.tool_call

    if isinstance(tc, ClickArgs):
        x, y = _denorm(tc.x, w), _denorm(tc.y, h)
        return [f"import pyautogui; pyautogui.click({x}, {y}, button='{tc.button}')"]

    if isinstance(tc, DoubleClickArgs):
        x, y = _denorm(tc.x, w), _denorm(tc.y, h)
        return [f"import pyautogui; pyautogui.doubleClick({x}, {y})"]

    if isinstance(tc, MoveMouseArgs):
        x, y = _denorm(tc.x, w), _denorm(tc.y, h)
        return [f"import pyautogui; pyautogui.moveTo({x}, {y})"]

    if isinstance(tc, DragArgs):
        x1, y1 = _denorm(tc.x1, w), _denorm(tc.y1, h)
        x2, y2 = _denorm(tc.x2, w), _denorm(tc.y2, h)
        return [
            f"import pyautogui; pyautogui.moveTo({x1}, {y1}); "
            f"pyautogui.dragTo({x2}, {y2}, button='left')"
        ]

    if isinstance(tc, WriteArgs):
        lines = ["import pyautogui"]
        if tc.overwrite:
            # macOS / Linux 通用：直接清空更稳妥的做法是 select all + delete
            lines.append("pyautogui.hotkey('ctrl', 'a'); pyautogui.press('delete')")
        # 用 repr 防止引号 / 反斜杠注入
        lines.append(f"pyautogui.typewrite({tc.content!r}, interval=0.02)")
        if tc.press_enter:
            lines.append("pyautogui.press('enter')")
        return ["; ".join(lines)]

    if isinstance(tc, KeyArgs):
        keys_repr = ", ".join(repr(k) for k in tc.keys)
        if len(tc.keys) == 1:
            return [f"import pyautogui; pyautogui.press({keys_repr})"]
        return [f"import pyautogui; pyautogui.hotkey({keys_repr})"]

    if isinstance(tc, ScrollArgs):
        x, y = _denorm(tc.x, w), _denorm(tc.y, h)
        # pyautogui.scroll: 正数向上、负数向下；hscroll 用于左右
        if tc.direction in ("up", "down"):
            sign = 1 if tc.direction == "up" else -1
            return [
                f"import pyautogui; "
                f"pyautogui.scroll({sign * tc.amount}, x={x}, y={y})"
            ]
        sign = 1 if tc.direction == "right" else -1
        return [
            f"import pyautogui; "
            f"pyautogui.hscroll({sign * tc.amount}, x={x}, y={y})"
        ]

    if isinstance(tc, WaitArgs):
        return [f"import time; time.sleep({tc.seconds})"]

    if isinstance(tc, ScreenshotArgs):
        return ["WAIT"]

    if isinstance(tc, TerminateArgs):
        return ["DONE"] if tc.status == "success" else ["FAIL"]

    if isinstance(tc, AnswerArgs):
        return [f"ANSWER:{tc.content}"]

    raise ValueError(f"Unknown tool_call type: {type(tc).__name__}")
