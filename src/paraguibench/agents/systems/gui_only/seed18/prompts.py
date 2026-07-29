"""Seed18 GUI-only 模型的公开提示词与结构化工具定义。"""

from __future__ import annotations

from collections.abc import Sequence
import base64
from typing import Any

SEED18_SYSTEM_PROMPT = """\
You are a GUI-only desktop agent. Complete the user's task by inspecting the
current screenshot and selecting exactly one provided tool per turn.

All point coordinates use a 0-1000 relative coordinate system and must be
formatted as "<point>X Y</point>". Use only visible GUI interactions. Do not
open a terminal, run shell commands, use developer tools, or ask for passwords.
When the task is complete, call finished and follow the requested answer format
exactly. If the task is impossible, call infeasible. Do not reveal hidden
reasoning in any tool argument.

For a task that asks which visible file or paper matches evidence, prefer the
single visible filename or filename stem as the answer. Do not concatenate a
filename, document title, and explanation unless the task explicitly requests
all of them.
"""

SEED18_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Single left click at a visible point.",
            "parameters": {
                "type": "object",
                "properties": {"point": {"type": "string"}},
                "required": ["point"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "left_double",
            "description": "Double left click at a visible point.",
            "parameters": {
                "type": "object",
                "properties": {"point": {"type": "string"}},
                "required": ["point"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "right_single",
            "description": "Single right click at a visible point.",
            "parameters": {
                "type": "object",
                "properties": {"point": {"type": "string"}},
                "required": ["point"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drag",
            "description": "Drag from one visible point to another.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_point": {"type": "string"},
                    "end_point": {"type": "string"},
                },
                "required": ["start_point", "end_point"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll at a visible point in one direction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                    },
                },
                "required": ["point", "direction"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "description": "Type text into the focused GUI control.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "description": "Press a safe space-separated key combination.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press",
            "description": "Press one safe keyboard key.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait briefly for the visible desktop to update.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 30,
                    }
                },
                "required": ["time"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finished",
            "description": "Return the final answer after completing the task.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "infeasible",
            "description": "Stop because the visible task cannot be completed.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_user",
            "description": "Stop only when essential user input is required.",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
)


def build_step_messages(
    *,
    instruction: str,
    screenshot: bytes,
    media_type: str,
    step_index: int,
    action_history: Sequence[str],
) -> list[dict[str, Any]]:
    """构造一次无 gold、无凭据的多模态模型消息。

    输入参数：
        instruction：Agent 可见的任务说明。
        screenshot：当前 guest 截图原始字节。
        media_type：经魔数校验的 ``image/png`` 或 ``image/jpeg``。
        step_index：从 1 开始的当前动作步号。
        action_history：此前已成功编译的动作名称，不含参数和模型推理。
    输出返回值：
        可直接交给 OpenAI-compatible chat completions 的消息列表。
    """

    history_text = ", ".join(action_history) if action_history else "(none)"
    step_text = (
        f"Task:\n{instruction}\n\n"
        f"Current step: {step_index}\n"
        f"Prior action names: {history_text}\n"
        "Select exactly one tool using the current screenshot."
    )
    encoded = base64.b64encode(screenshot).decode("ascii")
    return [
        {"role": "system", "content": SEED18_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": step_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{encoded}"
                    },
                },
            ],
        },
    ]
