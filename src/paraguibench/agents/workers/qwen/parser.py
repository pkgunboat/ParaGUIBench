"""把 Qwen 原生 function call 或 OSWorld XML 兼容输出转成安全 GUIAction。"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
import json
import math
import re
from typing import Any
from xml.sax.saxutils import unescape

from paraguibench.agents.workers.gui import GUIAction

_MAX_RESPONSE_CHARACTERS = 100_000
_ALLOWED_ARGUMENTS = frozenset(
    {"action", "keys", "text", "coordinate", "pixels", "time", "status"}
)
_TOOL_CALL_PATTERN = re.compile(
    r"<tool_call>\s*(?P<body>.*?)\s*</tool_call>",
    re.DOTALL,
)
_FUNCTION_PATTERN = re.compile(
    r"<function=(?P<name>[A-Za-z_][A-Za-z0-9_]*)>"
    r"(?P<body>.*?)"
    r"</function>",
    re.DOTALL,
)
_PARAMETER_PATTERN = re.compile(
    r"<parameter=(?P<name>[a-z_][a-z0-9_]*)>"
    r"(?P<value>.*?)"
    r"</parameter>",
    re.DOTALL,
)


class QwenActionParseError(ValueError):
    """表示 Qwen 动作响应不能被唯一、严格地解释。"""


def computer_use_arguments_to_action(arguments: Mapping[str, Any]) -> GUIAction:
    """把 Qwen ``computer_use`` arguments 映射为 provider-neutral 动作。

    输入参数：
        arguments：原生 function call 或兼容 XML 中解析出的 JSON object。
    输出返回值：
        只含白名单动作名与有界参数的 ``GUIAction``。
    异常：
        QwenActionParseError：字段、动作名、坐标、按键或终止状态无效。
    """

    if not isinstance(arguments, Mapping):
        raise QwenActionParseError("computer_use arguments 必须是 object")
    unknown = set(arguments) - _ALLOWED_ARGUMENTS
    if unknown:
        raise QwenActionParseError("computer_use arguments 含未知字段")
    provider_action = arguments.get("action")
    if not isinstance(provider_action, str):
        raise QwenActionParseError("computer_use action 必须是字符串")
    allowed_by_action = {
        "key": {"keys"},
        "type": {"text"},
        "mouse_move": {"coordinate"},
        "left_click": {"coordinate"},
        "left_click_drag": {"coordinate"},
        "right_click": {"coordinate"},
        "middle_click": {"coordinate"},
        "double_click": {"coordinate"},
        "scroll": {"pixels"},
        "hscroll": {"pixels"},
        "wait": {"time"},
        "terminate": {"status", "text"},
        "answer": {"text"},
        "call_user": {"text"},
    }
    allowed_fields = allowed_by_action.get(provider_action)
    if allowed_fields is None:
        raise QwenActionParseError("computer_use action 不在允许列表")
    if (set(arguments) - {"action"}) - allowed_fields:
        raise QwenActionParseError("computer_use action 含不适用字段")

    point_actions = {
        "mouse_move": "move",
        "left_click": "click",
        "left_click_drag": "drag_to",
        "right_click": "right_single",
        "middle_click": "middle_single",
        "double_click": "left_double",
    }
    if provider_action in point_actions:
        return GUIAction(
            point_actions[provider_action],
            {"point": _relative_point(arguments.get("coordinate"))},
        )
    if provider_action == "key":
        keys = _validated_keys(arguments.get("keys"))
        action_name = "press" if len(keys) == 1 else "hotkey"
        return GUIAction(action_name, {"key": " ".join(keys)})
    if provider_action == "type":
        return GUIAction(
            "type",
            {"content": _validated_text(arguments.get("text"), required=True)},
        )
    if provider_action in {"scroll", "hscroll"}:
        amount = _validated_number(
            arguments.get("pixels"),
            minimum=-2000,
            maximum=2000,
            field_name="pixels",
        )
        return GUIAction(
            "scroll_amount",
            {
                "amount": amount,
                "axis": ("vertical" if provider_action == "scroll" else "horizontal"),
            },
        )
    if provider_action == "wait":
        seconds = _validated_number(
            arguments.get("time"),
            minimum=0,
            maximum=30,
            field_name="time",
        )
        return GUIAction("wait", {"time": seconds})
    if provider_action == "terminate":
        status = arguments.get("status")
        if not isinstance(status, str):
            raise QwenActionParseError("terminate status 必须是字符串")
        normalized_status = status.strip().lower()
        failure_statuses = {"fail", "failed", "failure", "error", "infeasible"}
        if normalized_status not in {"success"} | failure_statuses:
            raise QwenActionParseError("terminate status 不在允许列表")
        content = _validated_text(arguments.get("text", ""), required=False)
        return GUIAction(
            "finished" if normalized_status == "success" else "infeasible",
            {"content": content},
        )
    if provider_action == "answer":
        return GUIAction(
            "finished",
            {"content": _validated_text(arguments.get("text"), required=True)},
        )
    if provider_action == "call_user":
        return GUIAction(
            "call_user",
            {"content": _validated_text(arguments.get("text"), required=True)},
        )
    raise QwenActionParseError("computer_use action 不在允许列表")


def parse_osworld_xml_action(content: str) -> GUIAction:
    """解析 OSWorld Qwen 使用的单个 XML tool-call 兼容格式。

    输入参数：
        content：OpenAI-compatible endpoint 放在 assistant content 中的文本。
            允许 tool call 前带 ``Action:`` 或 thinking 文本，但只解析唯一的
            ``<tool_call>`` block。
    输出返回值：
        经过同一白名单映射的 ``GUIAction``。
    异常：
        QwenActionParseError：文本无界、tool call 数量不为一、XML/JSON
        格式无效或函数名不是 ``computer_use``。
    """

    if (
        not isinstance(content, str)
        or not content
        or len(content) > _MAX_RESPONSE_CHARACTERS
    ):
        raise QwenActionParseError("模型文本响应为空或超过边界")
    blocks = list(_TOOL_CALL_PATTERN.finditer(content))
    if len(blocks) != 1:
        raise QwenActionParseError("模型文本必须包含唯一 tool_call")
    body = blocks[0].group("body").strip()
    arguments = _parse_tool_call_body(body)
    return computer_use_arguments_to_action(arguments)


def _parse_tool_call_body(body: str) -> Mapping[str, Any]:
    """兼容 JSON block 与 OSWorld nested-parameter XML 两种 body。

    输入参数：
        body：不含外层 ``tool_call`` 标签的文本。
    输出返回值：
        ``computer_use`` 的参数 Mapping。
    异常：
        QwenActionParseError：函数名、参数对象或 XML 覆盖范围无效。
    """

    if body.startswith("{"):
        try:
            payload = json.loads(body)
            if payload.get("name") != "computer_use":
                raise ValueError
            arguments = payload.get("arguments")
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, Mapping):
                raise ValueError
            return arguments
        except Exception:
            raise QwenActionParseError("JSON tool_call 格式无效") from None

    function_match = _FUNCTION_PATTERN.fullmatch(body)
    if function_match is None or function_match.group("name") != "computer_use":
        raise QwenActionParseError("XML tool_call 函数无效")
    function_body = function_match.group("body")
    parameters: dict[str, Any] = {}
    cursor = 0
    for match in _PARAMETER_PATTERN.finditer(function_body):
        if function_body[cursor : match.start()].strip():
            raise QwenActionParseError("XML tool_call 含未识别内容")
        name = match.group("name")
        if name in parameters:
            raise QwenActionParseError("XML tool_call 含重复参数")
        parameters[name] = _decode_xml_value(name, match.group("value").strip())
        cursor = match.end()
    if function_body[cursor:].strip() or not parameters:
        raise QwenActionParseError("XML tool_call 参数格式无效")
    return parameters


def _decode_xml_value(name: str, value: str) -> Any:
    """按参数语义解码 OSWorld XML 中没有类型标记的文本。

    输入参数：
        name：parameter 标签中的字段名。
        value：标签内去除首尾空白的文本。
    输出返回值：
        数组、数值或普通字符串。
    异常：
        QwenActionParseError：数组或数值字段不是合法 JSON/数字。
    """

    decoded_value = _unescape_xml_text(value)
    if name in {"coordinate", "keys"}:
        try:
            return json.loads(decoded_value)
        except Exception:
            try:
                decoded = ast.literal_eval(decoded_value)
                if isinstance(decoded, (list, tuple)):
                    return decoded
            except Exception:
                pass
            if name == "keys" and decoded_value:
                return decoded_value
            raise QwenActionParseError("XML 数组参数格式无效") from None
    if name in {"pixels", "time"}:
        try:
            return float(decoded_value)
        except Exception:
            raise QwenActionParseError("XML 数值参数格式无效") from None
    if name == "text" and decoded_value.startswith('"'):
        try:
            decoded = json.loads(decoded_value)
            if isinstance(decoded, str):
                return decoded
        except Exception:
            pass
    return decoded_value


def _unescape_xml_text(value: str) -> str:
    """只解码 XML 标准实体，保留未知或畸形的实体文本。

    输入参数：
        value：nested-parameter XML 标签内已去除首尾空白的文本。
    输出返回值：
        解码 ``lt``、``gt``、``amp``、``quot`` 和 ``apos`` 后的字符串；
        不执行 HTML 宽松实体规则，也不进行二次递归解码。
    """

    return unescape(
        value,
        {
            "&quot;": '"',
            "&apos;": "'",
        },
    )


def _relative_point(raw_coordinate: Any) -> str:
    """把 Qwen 0–999 数组坐标精确换算到公共 0–1000 字符串坐标。

    输入参数：
        raw_coordinate：长度为 2 的有限数值 sequence。
    输出返回值：
        ``<point>x y</point>``，999 会映射到 1000，避免右/下边界越界。
    异常：
        QwenActionParseError：坐标类型、长度或范围无效。
    """

    if (
        isinstance(raw_coordinate, (str, bytes))
        or not isinstance(raw_coordinate, Sequence)
        or len(raw_coordinate) != 2
    ):
        raise QwenActionParseError("coordinate 必须是长度为 2 的数组")
    coordinates: list[int] = []
    for value in raw_coordinate:
        number = _validated_number(
            value,
            minimum=0,
            maximum=999,
            field_name="coordinate",
        )
        coordinates.append(round(number * 1000 / 999))
    return f"<point>{coordinates[0]} {coordinates[1]}</point>"


def _validated_keys(raw_keys: Any) -> tuple[str, ...]:
    """验证 Qwen key action 的按键数组并规范化为小写。

    输入参数：
        raw_keys：1–4 个非空短字符串构成的 sequence。
    输出返回值：
        小写、去除首尾空格的按键元组；最终白名单由公共 compiler 复核。
    异常：
        QwenActionParseError：数组类型、长度或元素边界无效。
    """

    if isinstance(raw_keys, str):
        raw_keys = (raw_keys,)
    if (
        isinstance(raw_keys, bytes)
        or not isinstance(raw_keys, Sequence)
        or not 1 <= len(raw_keys) <= 4
    ):
        raise QwenActionParseError("keys 必须是包含 1–4 项的数组")
    keys: list[str] = []
    for value in raw_keys:
        if not isinstance(value, str) or not value.strip() or len(value) > 32:
            raise QwenActionParseError("keys 含无效键名")
        parts = tuple(part.strip().lower() for part in value.split("+"))
        if not all(parts):
            raise QwenActionParseError("keys 含无效键名")
        keys.extend(parts)
    if not 1 <= len(keys) <= 4:
        raise QwenActionParseError("keys 展开后必须包含 1–4 个键")
    return tuple(keys)


def _validated_text(raw_text: Any, *, required: bool) -> str:
    """验证模型返回的输入、答案或用户请求文本边界。

    输入参数：
        raw_text：待验证字段。
        required：是否禁止空字符串。
    输出返回值：
        长度不超过 10000 的字符串。
    异常：
        QwenActionParseError：字段类型、长度或必填约束无效。
    """

    if (
        not isinstance(raw_text, str)
        or len(raw_text) > 10_000
        or (required and not raw_text)
    ):
        raise QwenActionParseError("text 必须是有界字符串")
    return raw_text


def _validated_number(
    raw_value: Any,
    *,
    minimum: float,
    maximum: float,
    field_name: str,
) -> float:
    """验证模型数值字段为给定闭区间内的有限数。

    输入参数：
        raw_value：待验证值。
        minimum：允许的最小值。
        maximum：允许的最大值。
        field_name：仅用于稳定、无原值的错误类别描述。
    输出返回值：
        规范化后的浮点数。
    异常：
        QwenActionParseError：类型、有限性或范围无效。
    """

    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise QwenActionParseError(f"{field_name} 必须是数值")
    value = float(raw_value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise QwenActionParseError(f"{field_name} 超出允许范围")
    return value
