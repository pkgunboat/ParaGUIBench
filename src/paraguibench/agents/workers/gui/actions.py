"""把 provider-neutral 的受限 GUI 动作编译为 shell-free guest argv。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any, Literal, Mapping

_POINT_PATTERN = re.compile(
    r"(?:<point>)?\s*(?P<x>\d{1,4})\s+(?P<y>\d{1,4})\s*(?:</point>)?"
)
_TERMINAL_ACTIONS = frozenset({"finished", "infeasible", "call_user"})
_NAMED_KEYS = frozenset(
    {
        "alt",
        "backspace",
        "command",
        "ctrl",
        "delete",
        "down",
        "end",
        "enter",
        "esc",
        "home",
        "left",
        "pagedown",
        "pageup",
        "right",
        "shift",
        "space",
        "tab",
        "up",
        "win",
    }
)
_SCROLL_STATEMENTS = {
    "up": "pyautogui.scroll(5)",
    "down": "pyautogui.scroll(-5)",
    "left": "pyautogui.hscroll(-5)",
    "right": "pyautogui.hscroll(5)",
}
_FORBIDDEN_HOTKEY_SUBSETS = frozenset(
    {
        frozenset({"alt", "f1"}),
        frozenset({"alt", "f2"}),
        frozenset({"command", "space"}),
        frozenset({"ctrl", "alt", "t"}),
        frozenset({"ctrl", "shift", "c"}),
        frozenset({"ctrl", "shift", "i"}),
        frozenset({"ctrl", "shift", "j"}),
        frozenset({"ctrl", "shift", "k"}),
        frozenset({"win", "r"}),
    }
)
_FORBIDDEN_LAUNCHER_KEYS = frozenset({"command", "win"})
_FORBIDDEN_SINGLE_KEYS = frozenset({"f12"})


class GUIActionError(ValueError):
    """表示模型动作名称、参数或坐标不能被安全执行。"""


@dataclass(frozen=True)
class GUIAction:
    """保存模型 adapter 返回的一个 provider-neutral 结构化动作。"""

    name: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class CompiledGUIAction:
    """保存动作编译后的执行类别与互斥 payload。"""

    kind: Literal["guest_command", "wait", "terminal"]
    command: tuple[str, ...] | None = None
    wait_seconds: float | None = None
    terminal_name: str | None = None
    terminal_content: str | None = None


def compile_gui_action(
    action: GUIAction,
    *,
    image_width: int,
    image_height: int,
) -> CompiledGUIAction:
    """把一个白名单 GUI 动作编译为 guest argv、等待或终止结果。

    输入参数：
        action：provider adapter 已解析并转换出的结构化 GUIAction。
        image_width：当前原始截图像素宽度。
        image_height：当前原始截图像素高度。
    输出返回值：
        三种互斥结果之一；guest command 始终为 ``python -c`` argv，代码只
        来自本模块的固定模板，调用方不得再经 shell 拼接。
    异常：
        GUIActionError：动作、参数、坐标或截图尺寸不满足安全契约。
    """

    if (
        not isinstance(image_width, int)
        or not isinstance(image_height, int)
        or image_width <= 0
        or image_height <= 0
    ):
        raise GUIActionError("截图尺寸必须是正整数")
    if not isinstance(action, GUIAction):
        raise GUIActionError("动作必须是 GUIAction")
    if action.name in _TERMINAL_ACTIONS:
        content = action.parameters.get("content", "")
        if not isinstance(content, str) or len(content) > 20_000:
            raise GUIActionError("terminal content 必须是长度不超过 20000 的字符串")
        return CompiledGUIAction(
            kind="terminal",
            terminal_name=action.name,
            terminal_content=content,
        )
    if action.name in {"click", "left_double", "right_single", "middle_single"}:
        x, y = _scaled_point(
            action.parameters.get("point"),
            image_width=image_width,
            image_height=image_height,
        )
        method = {
            "click": "click",
            "left_double": "doubleClick",
            "right_single": "rightClick",
            "middle_single": "middleClick",
        }[action.name]
        return _python_action(f"pyautogui.{method}({x}, {y})")
    if action.name == "move":
        x, y = _scaled_point(
            action.parameters.get("point"),
            image_width=image_width,
            image_height=image_height,
        )
        return _python_action(f"pyautogui.moveTo({x}, {y})")
    if action.name == "drag":
        start_x, start_y = _scaled_point(
            action.parameters.get("start_point"),
            image_width=image_width,
            image_height=image_height,
        )
        end_x, end_y = _scaled_point(
            action.parameters.get("end_point"),
            image_width=image_width,
            image_height=image_height,
        )
        statement = (
            f"pyautogui.moveTo({start_x}, {start_y}); "
            f"pyautogui.dragTo({end_x}, {end_y}, duration=1.0, "
            "button='left')"
        )
        return _python_action(statement)
    if action.name == "drag_to":
        x, y = _scaled_point(
            action.parameters.get("point"),
            image_width=image_width,
            image_height=image_height,
        )
        return _python_action(
            f"pyautogui.dragTo({x}, {y}, duration=1.0, button='left')"
        )
    if action.name == "scroll":
        x, y = _scaled_point(
            action.parameters.get("point"),
            image_width=image_width,
            image_height=image_height,
        )
        direction = action.parameters.get("direction")
        if not isinstance(direction, str) or direction not in _SCROLL_STATEMENTS:
            raise GUIActionError("scroll direction 必须是 up/down/left/right")
        statement = f"pyautogui.moveTo({x}, {y}); {_SCROLL_STATEMENTS[direction]}"
        return _python_action(statement)
    if action.name == "scroll_amount":
        amount = _validated_scroll_amount(action.parameters.get("amount"))
        axis = action.parameters.get("axis", "vertical")
        if axis not in {"vertical", "horizontal"}:
            raise GUIActionError("scroll axis 必须是 vertical 或 horizontal")
        method = "scroll" if axis == "vertical" else "hscroll"
        return _python_action(f"pyautogui.{method}({amount})")
    if action.name in {"hotkey", "press"}:
        keys = _validated_keys(
            action.parameters.get("key"),
            allow_multiple=action.name == "hotkey",
        )
        literals = ", ".join(json.dumps(key) for key in keys)
        method = "hotkey" if action.name == "hotkey" else "press"
        return _python_action(f"pyautogui.{method}({literals})")
    if action.name == "type":
        content = action.parameters.get("content")
        if not isinstance(content, str) or len(content) > 10_000:
            raise GUIActionError("type content 必须是长度不超过 10000 的字符串")
        submit = content.endswith("\n")
        clipboard_content = content[:-1] if submit else content
        literal = json.dumps(clipboard_content, ensure_ascii=False)
        statement = (
            "import pyperclip; "
            f"pyperclip.copy({literal}); "
            "pyautogui.hotkey('ctrl', 'v')"
        )
        if submit:
            statement += "; pyautogui.press('enter')"
        return _python_action(statement)
    if action.name == "wait":
        seconds = _validated_wait(action.parameters.get("time"))
        return CompiledGUIAction(kind="wait", wait_seconds=seconds)
    raise GUIActionError("当前 GUI 动作尚未被允许")


def _scaled_point(
    raw_point: Any,
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int]:
    """解析 0–1000 相对坐标并缩放、限制到原始截图像素。

    输入参数：
        raw_point：``<point>x y</point>`` 或 ``x y`` 字符串。
        image_width：截图像素宽度。
        image_height：截图像素高度。
    输出返回值：
        限制在有效像素索引内的 ``(x, y)``。
    异常：
        GUIActionError：格式无效或相对坐标越界。
    """

    if not isinstance(raw_point, str):
        raise GUIActionError("point 必须是字符串")
    match = _POINT_PATTERN.fullmatch(raw_point.strip())
    if match is None:
        raise GUIActionError("point 格式无效")
    relative_x = int(match.group("x"))
    relative_y = int(match.group("y"))
    if not 0 <= relative_x <= 1000 or not 0 <= relative_y <= 1000:
        raise GUIActionError("point 必须位于 0–1000 范围")
    pixel_x = min(image_width - 1, round(relative_x * image_width / 1000))
    pixel_y = min(image_height - 1, round(relative_y * image_height / 1000))
    return pixel_x, pixel_y


def _python_action(statement: str) -> CompiledGUIAction:
    """把内部固定模板生成的 pyautogui statement 包装为 argv。

    输入参数：
        statement：仅由本模块固定分支和 JSON 字面量生成的 Python 语句。
    输出返回值：
        ``guest_command`` 类型的不可变结果。
    """

    return CompiledGUIAction(
        kind="guest_command",
        command=("python3", "-c", f"import pyautogui; {statement}"),
    )


def _validated_keys(raw_key: Any, *, allow_multiple: bool) -> tuple[str, ...]:
    """校验按键字符串并转换为 pyautogui 固定键名序列。

    输入参数：
        raw_key：单个键名或以空格分隔的组合键。
        allow_multiple：是否允许最多四个组合键；press 必须为单键。
    输出返回值：
        经过小写规范化、仅包含白名单键名的不可变序列。
    异常：
        GUIActionError：输入类型、键数或任一键名不符合安全契约。
    """

    if not isinstance(raw_key, str):
        raise GUIActionError("key 必须是字符串")
    keys = tuple(part.lower() for part in raw_key.split())
    maximum = 4 if allow_multiple else 1
    if not keys or len(keys) > maximum:
        raise GUIActionError(f"key 必须包含 1–{maximum} 个键")
    for key in keys:
        is_single_character = len(key) == 1 and key.isascii() and key.isalnum()
        is_function_key = (
            key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12
        )
        if key not in _NAMED_KEYS and not is_single_character and not is_function_key:
            raise GUIActionError("key 包含未允许的键名")
    if any(key in _FORBIDDEN_LAUNCHER_KEYS for key in keys):
        raise GUIActionError("GUI-only 策略禁止系统启动器按键")
    if any(key in _FORBIDDEN_SINGLE_KEYS for key in keys):
        raise GUIActionError("GUI-only 策略禁止开发者工具按键")
    key_set = frozenset(keys)
    opens_tty = {"ctrl", "alt"}.issubset(key_set) and any(
        key.startswith("f") and key[1:].isdigit() for key in key_set
    )
    if allow_multiple and (
        opens_tty
        or any(pattern.issubset(key_set) for pattern in _FORBIDDEN_HOTKEY_SUBSETS)
    ):
        raise GUIActionError("GUI-only 策略禁止终端、启动器或开发者工具快捷键")
    return keys


def _validated_scroll_amount(raw_amount: Any) -> int:
    """把滚动量约束为 pyautogui 可接受的有界整数。

    输入参数：
        raw_amount：模型返回的垂直或水平滚动量。
    输出返回值：
        四舍五入后位于 -2000–2000 的整数。
    异常：
        GUIActionError：值不是有限数值或超出边界。
    """

    if isinstance(raw_amount, bool) or not isinstance(raw_amount, (int, float)):
        raise GUIActionError("scroll amount 必须是数值")
    amount = float(raw_amount)
    if not math.isfinite(amount) or not -2000 <= amount <= 2000:
        raise GUIActionError("scroll amount 必须位于 -2000–2000")
    return round(amount)


def _validated_wait(raw_seconds: Any) -> float:
    """校验模型请求的等待时间并规范化为有限浮点数。

    输入参数：
        raw_seconds：模型返回的等待秒数。
    输出返回值：
        位于 0–30 秒的浮点数。
    异常：
        GUIActionError：值不是有限数值或超出边界。
    """

    if isinstance(raw_seconds, bool) or not isinstance(raw_seconds, (int, float)):
        raise GUIActionError("wait time 必须是 0–30 秒的有限数值")
    seconds = float(raw_seconds)
    if not math.isfinite(seconds) or not 0 <= seconds <= 30:
        raise GUIActionError("wait time 必须是 0–30 秒的有限数值")
    return seconds
