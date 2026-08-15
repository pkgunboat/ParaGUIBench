"""Qwen computer_use 到公共 GUI 动作的严格解析与安全测试。"""

from __future__ import annotations

import pytest

from paraguibench.agents.workers.gui import GUIAction, compile_gui_action
from paraguibench.agents.workers.qwen.parser import (
    QwenActionParseError,
    computer_use_arguments_to_action,
    parse_osworld_xml_action,
)
from paraguibench.evaluation.answers import extract_last_complete_answer


def test_qwen_relative_coordinate_maps_999_to_last_original_pixel() -> None:
    """验证 OSWorld 的 0–999 坐标不会在右下边界越界。

    输入参数：
        无；构造右下角 Qwen left_click。
    输出返回值：
        无；公共 compiler 最终点击原图最后一个有效像素。
    """

    action = computer_use_arguments_to_action(
        {"action": "left_click", "coordinate": [999, 999]}
    )
    compiled = compile_gui_action(
        action,
        image_width=1920,
        image_height=1080,
    )

    assert action.name == "click"
    assert action.parameters["point"] == "<point>1000 1000</point>"
    assert compiled.command is not None
    assert "pyautogui.click(1919, 1079)" in compiled.command[2]


def test_qwen_parses_current_osworld_nested_xml_format() -> None:
    """验证兼容上游 Qwen 的 nested-parameter XML，而不执行文本代码。

    输入参数：
        无；构造一条带 Action 前缀的 OSWorld XML hotkey。
    输出返回值：
        无；解析结果是公共 hotkey 动作。
    """

    content = """Action: select all visible text.
<tool_call>
<function=computer_use>
<parameter=action>
key
</parameter>
<parameter=keys>
["CTRL", "A"]
</parameter>
</function>
</tool_call>"""

    action = parse_osworld_xml_action(content)

    assert action.name == "hotkey"
    assert action.parameters == {"key": "ctrl a"}


def test_qwen_xml_unescapes_tagged_answer_for_exact_evaluator() -> None:
    """验证 OSWorld XML 实体转义的 answer 能进入现有评价链。

    输入参数：
        无；构造 terminate success 与转义的 ``answer`` 标签。
    输出返回值：
        无；解析后保留完整标签，exact evaluator 可提取最终答案。
    """

    content = """<tool_call><function=computer_use>
<parameter=action>terminate</parameter>
<parameter=status>success</parameter>
<parameter=text>&lt;answer&gt;paper3&lt;/answer&gt;</parameter>
</function></tool_call>"""

    action = parse_osworld_xml_action(content)

    assert action.parameters["content"] == "<answer>paper3</answer>"
    assert extract_last_complete_answer(action.parameters["content"]) == "paper3"


def test_qwen_xml_unescapes_only_standard_entities_once() -> None:
    """验证标准 XML 实体被单次解码，未知实体保留为文本。

    输入参数：
        无；组合 ``amp``、双重转义 ``lt`` 和未知实体。
    输出返回值：
        无；返回稳定文本，不进行 HTML 宽松或递归解码。
    """

    content = """<tool_call><function=computer_use>
<parameter=action>answer</parameter>
<parameter=text>A &amp; B &amp;lt;tag&gt; &unknown;</parameter>
</function></tool_call>"""

    action = parse_osworld_xml_action(content)

    assert action.parameters["content"] == "A & B &lt;tag> &unknown;"


@pytest.mark.parametrize(
    ("raw_keys", "expected"),
    [
        ("['ctrl', 's']", {"key": "ctrl s"}),
        ("ctrl+shift+t", {"key": "ctrl shift t"}),
    ],
)
def test_qwen_xml_accepts_upstream_safe_key_encodings(
    raw_keys: str,
    expected: dict[str, str],
) -> None:
    """验证兼容 OSWorld 已覆盖的 Python list 与加号分隔按键格式。

    输入参数：
        raw_keys：XML parameter 中的上游兼容表示。
        expected：规范化后的公共 key 参数。
    输出返回值：
        无；两种表示均解析为 hotkey，仍由公共键白名单复核。
    """

    content = f"""<tool_call><function=computer_use>
<parameter=action>key</parameter>
<parameter=keys>{raw_keys}</parameter>
</function></tool_call>"""

    action = parse_osworld_xml_action(content)

    assert action.name == "hotkey"
    assert action.parameters == expected


def test_qwen_key_array_flattens_upstream_plus_encoded_items() -> None:
    """验证 OSWorld 使用的数组内加号编码会递归展开。

    输入参数：
        无；构造 native ``keys=["CTRL+A"]`` 参数。
    输出返回值：
        无；解析结果为可由公共 compiler 执行的 ``ctrl a`` hotkey。
    """

    action = computer_use_arguments_to_action({"action": "key", "keys": ["CTRL+A"]})

    assert action.name == "hotkey"
    assert action.parameters == {"key": "ctrl a"}


@pytest.mark.parametrize(
    "action",
    [
        GUIAction("press", {"key": "f12"}),
        GUIAction("hotkey", {"key": "ctrl shift i"}),
        GUIAction("hotkey", {"key": "ctrl shift j"}),
        GUIAction("hotkey", {"key": "ctrl alt f1"}),
        GUIAction("hotkey", {"key": "ctrl alt f12"}),
    ],
)
def test_qwen_gui_compiler_rejects_devtools_and_all_tty_hotkeys(
    action: GUIAction,
) -> None:
    """验证常见 DevTools 与全范围 Linux TTY 快捷键不进入 guest。

    输入参数：
        action：参数化的单键或组合键 GUI 动作。
    输出返回值：
        无；公共 compiler 对每个动作 fail-closed。
    """

    with pytest.raises(ValueError):
        compile_gui_action(action, image_width=1920, image_height=1080)


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "left_click", "coordinate": [1000, 1]},
        {"action": "key", "keys": ["ctrl", "alt", "t", "extra", "x"]},
        {"action": "scroll", "pixels": float("inf")},
        {"action": "terminate", "status": "maybe"},
        {"action": "left_click", "coordinate": [1, 2], "code": "rm -rf /"},
        {"action": "left_click", "coordinate": [1, 2], "status": "success"},
        {"action": "type", "text": "hello", "coordinate": [1, 2]},
        {"action": "answer"},
        {"action": "call_user", "text": ""},
    ],
)
def test_qwen_rejects_out_of_contract_arguments(arguments: dict[str, object]) -> None:
    """验证越界、非有限和未知字段均在 guest 执行前失败。

    输入参数：
        arguments：参数化的畸形 computer_use object。
    输出返回值：
        无；解析器统一抛出不含原始值的 QwenActionParseError。
    """

    with pytest.raises(QwenActionParseError):
        computer_use_arguments_to_action(arguments)


def test_qwen_never_treats_unparsed_natural_language_as_done() -> None:
    """验证无 tool call 的自然语言不会被误判为任务完成。

    输入参数：
        无；使用上游旧实现可能默认 DONE 的普通文本。
    输出返回值：
        无；兼容解析路径 fail-closed。
    """

    with pytest.raises(QwenActionParseError, match="唯一 tool_call"):
        parse_osworld_xml_action("I think the task is complete.")
