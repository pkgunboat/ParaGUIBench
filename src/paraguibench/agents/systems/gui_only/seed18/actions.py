"""把受限 Seed18 工具动作编译为 shell-free guest argv。"""

from __future__ import annotations

from dataclasses import dataclass
import json
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
_FORBIDDEN_LAUNCHER_HOTKEYS = frozenset(
    {
        ("command", "space"),
        ("ctrl", "alt", "t"),
        ("win", "r"),
    }
)


class SeedActionError(ValueError):
    """表示模型动作名称、参数或坐标不能被安全执行。"""


@dataclass(frozen=True)
class SeedAction:
    """保存模型返回的一个工具名称与结构化参数。"""

    name: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class CompiledSeedAction:
    """保存动作编译后的执行类别与互斥 payload。"""

    kind: Literal["guest_command", "wait", "terminal"]
    command: tuple[str, ...] | None = None
    wait_seconds: float | None = None
    terminal_name: str | None = None
    terminal_content: str | None = None


def compile_seed_action(
    action: SeedAction,
    *,
    image_width: int,
    image_height: int,
) -> CompiledSeedAction:
    """把一个 Seed18 动作编译为 guest argv、等待或终止结果。

    输入参数：
        action：模型适配器已解析出的工具名称与参数 Mapping。
        image_width：当前截图像素宽度。
        image_height：当前截图像素高度。
    输出返回值：
        三种互斥结果之一；guest command 始终为 ``python -c`` argv，
        调用方不得再经 shell 拼接。
    异常：
        SeedActionError：动作、参数、坐标或截图尺寸不满足安全契约。
    """

    if (
        not isinstance(image_width, int)
        or not isinstance(image_height, int)
        or image_width <= 0
        or image_height <= 0
    ):
        raise SeedActionError("截图尺寸必须是正整数")
    if not isinstance(action, SeedAction):
        raise SeedActionError("动作必须是 SeedAction")
    if action.name in _TERMINAL_ACTIONS:
        content = action.parameters.get("content", "")
        if not isinstance(content, str):
            raise SeedActionError("terminal content 必须是字符串")
        return CompiledSeedAction(
            kind="terminal",
            terminal_name=action.name,
            terminal_content=content,
        )
    if action.name == "click":
        x, y = _scaled_point(
            action.parameters.get("point"),
            image_width=image_width,
            image_height=image_height,
        )
        return _python_action(f"pyautogui.click({x}, {y})")
    if action.name in {"left_double", "right_single"}:
        x, y = _scaled_point(
            action.parameters.get("point"),
            image_width=image_width,
            image_height=image_height,
        )
        method = (
            "doubleClick"
            if action.name == "left_double"
            else "rightClick"
        )
        return _python_action(f"pyautogui.{method}({x}, {y})")
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
    if action.name == "scroll":
        x, y = _scaled_point(
            action.parameters.get("point"),
            image_width=image_width,
            image_height=image_height,
        )
        direction = action.parameters.get("direction")
        if not isinstance(direction, str) or direction not in _SCROLL_STATEMENTS:
            raise SeedActionError("scroll direction 必须是 up/down/left/right")
        statement = (
            f"pyautogui.moveTo({x}, {y}); "
            f"{_SCROLL_STATEMENTS[direction]}"
        )
        return _python_action(statement)
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
            raise SeedActionError("type content 必须是长度不超过 10000 的字符串")
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
        raw_seconds = action.parameters.get("time")
        if (
            isinstance(raw_seconds, bool)
            or not isinstance(raw_seconds, (int, float))
            or not 0 <= raw_seconds <= 30
        ):
            raise SeedActionError("wait time 必须是 0–30 秒的有限数值")
        seconds = float(raw_seconds)
        if seconds != seconds or seconds in {float("inf"), float("-inf")}:
            raise SeedActionError("wait time 必须是 0–30 秒的有限数值")
        return CompiledSeedAction(kind="wait", wait_seconds=seconds)
    raise SeedActionError("当前 Seed18 动作尚未被允许")


def _scaled_point(
    raw_point: Any,
    *,
    image_width: int,
    image_height: int,
) -> tuple[int, int]:
    """解析 0–1000 相对坐标并缩放到截图像素。

    输入参数：
        raw_point：``<point>x y</point>`` 或 ``x y`` 字符串。
        image_width：截图像素宽度。
        image_height：截图像素高度。
    输出返回值：
        限制在有效像素索引内的 ``(x, y)``。
    异常：
        SeedActionError：格式无效或相对坐标越界。
    """

    if not isinstance(raw_point, str):
        raise SeedActionError("point 必须是字符串")
    match = _POINT_PATTERN.fullmatch(raw_point.strip())
    if match is None:
        raise SeedActionError("point 格式无效")
    relative_x = int(match.group("x"))
    relative_y = int(match.group("y"))
    if not 0 <= relative_x <= 1000 or not 0 <= relative_y <= 1000:
        raise SeedActionError("point 必须位于 0–1000 范围")
    pixel_x = min(image_width - 1, round(relative_x * image_width / 1000))
    pixel_y = min(image_height - 1, round(relative_y * image_height / 1000))
    return pixel_x, pixel_y


def _python_action(statement: str) -> CompiledSeedAction:
    """把内部生成的单条 pyautogui statement 包装为 argv。

    输入参数：
        statement：仅由本模块固定模板生成的 Python 语句。
    输出返回值：
        ``guest_command`` 类型的不可变结果。
    """

    code = (
        "import pyautogui; "
        f"{statement}"
    )
    return CompiledSeedAction(
        kind="guest_command",
        command=("python", "-c", code),
    )


def _validated_keys(raw_key: Any, *, allow_multiple: bool) -> tuple[str, ...]:
    """校验按键字符串并转换为 pyautogui 固定键名序列。

    输入参数：
        raw_key：模型返回的单个键名或以空格分隔的组合键。
        allow_multiple：是否允许最多四个组合键；press 必须为单键。
    输出返回值：
        经过小写规范化、仅包含白名单键名的不可变序列。
    异常：
        SeedActionError：输入类型、键数或任一键名不符合安全契约。
    """

    if not isinstance(raw_key, str):
        raise SeedActionError("key 必须是字符串")
    keys = tuple(part.lower() for part in raw_key.split())
    maximum = 4 if allow_multiple else 1
    if not keys or len(keys) > maximum:
        raise SeedActionError(f"key 必须包含 1–{maximum} 个键")
    for key in keys:
        is_single_character = len(key) == 1 and key.isascii() and key.isalnum()
        is_function_key = (
            key.startswith("f")
            and key[1:].isdigit()
            and 1 <= int(key[1:]) <= 12
        )
        if key not in _NAMED_KEYS and not is_single_character and not is_function_key:
            raise SeedActionError("key 包含未允许的键名")
    if allow_multiple and keys in _FORBIDDEN_LAUNCHER_HOTKEYS:
        raise SeedActionError("GUI-only 策略禁止命令启动器快捷键")
    return keys
