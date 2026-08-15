"""Seed18 GUI 动作编译的坐标、终止与注入安全测试。"""

from __future__ import annotations

import ast

import pytest

from paraguibench.agents.systems.gui_only.seed18.actions import (
    SeedAction,
    compile_seed_action,
)


def test_click_coordinates_are_scaled_to_guest_pixels() -> None:
    """验证 0–1000 相对坐标按实际截图尺寸转换为安全 argv。

    输入参数：
        无；合成一个中心偏上的 click 动作和 1920×1080 截图。
    输出返回值：
        无；编译结果只能是 shell-free Python argv，像素坐标准确。
    """

    compiled = compile_seed_action(
        SeedAction("click", {"point": "<point>500 250</point>"}),
        image_width=1920,
        image_height=1080,
    )

    assert compiled.kind == "guest_command"
    assert compiled.command is not None
    assert compiled.command[:2] == ("python", "-c")
    assert "pyautogui.click(960, 270)" in compiled.command[2]


def test_finished_action_returns_content_without_guest_command() -> None:
    """验证 finished 只返回最终内容，不再执行任何 guest 代码。

    输入参数：
        无；合成带 exact answer 标签的 terminal action。
    输出返回值：
        无；结果类型为 terminal，command 必须为空。
    """

    compiled = compile_seed_action(
        SeedAction(
            "finished",
            {"content": "<answer>paper</answer>"},
        ),
        image_width=1920,
        image_height=1080,
    )

    assert compiled.kind == "terminal"
    assert compiled.command is None
    assert compiled.terminal_content == "<answer>paper</answer>"


def test_type_content_is_embedded_as_data_not_executable_python() -> None:
    """验证模型提供的输入文本只能成为字符串常量，不能注入 guest 代码。

    输入参数：
        无；content 包含看似 Python 调用的合成恶意片段。
    输出返回值：
        无；生成代码可解析，片段仅存在于 Constant，AST 不含 import 调用。
    """

    content = "hello'); __import__('os').system('id') #"
    compiled = compile_seed_action(
        SeedAction("type", {"content": content}),
        image_width=1920,
        image_height=1080,
    )

    assert compiled.command is not None
    tree = ast.parse(compiled.command[2])
    constants = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    called_names = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert content in constants
    assert "__import__" not in called_names


@pytest.mark.parametrize(
    ("action", "expected_kind"),
    [
        (
            SeedAction(
                "left_double",
                {"point": "<point>100 200</point>"},
            ),
            "guest_command",
        ),
        (
            SeedAction(
                "right_single",
                {"point": "<point>100 200</point>"},
            ),
            "guest_command",
        ),
        (
            SeedAction(
                "drag",
                {
                    "start_point": "<point>100 200</point>",
                    "end_point": "<point>800 700</point>",
                },
            ),
            "guest_command",
        ),
        (
            SeedAction(
                "scroll",
                {
                    "point": "<point>500 500</point>",
                    "direction": "down",
                },
            ),
            "guest_command",
        ),
        (SeedAction("hotkey", {"key": "ctrl a"}), "guest_command"),
        (SeedAction("press", {"key": "enter"}), "guest_command"),
        (SeedAction("wait", {"time": 2}), "wait"),
    ],
)
def test_supported_gui_actions_compile_to_bounded_execution(
    action: SeedAction,
    expected_kind: str,
) -> None:
    """验证首个 GUI-only loop 所需动作均编译为受限结果。

    输入参数：
        action：参数化的合法 Seed18 动作。
        expected_kind：预期 guest command 或 host wait 类型。
    输出返回值：
        无；动作不得退化为自由 shell 字符串。
    """

    compiled = compile_seed_action(
        action,
        image_width=1920,
        image_height=1080,
    )

    assert compiled.kind == expected_kind
    if compiled.command is not None:
        assert compiled.command[:2] == ("python", "-c")


@pytest.mark.parametrize(
    "launcher_hotkey",
    [
        "ctrl alt t",
        "ctrl alt f1",
        "ctrl alt f12",
        "ctrl shift i",
        "ctrl shift j",
        "win r",
        "command space",
    ],
)
def test_gui_only_policy_rejects_common_command_launcher_hotkeys(
    launcher_hotkey: str,
) -> None:
    """验证常见终端或命令运行器快捷键不能绕过 GUI-only 策略。

    输入参数：
        launcher_hotkey：Linux、Windows 或 macOS 常见命令入口热键。
    输出返回值：
        无；动作编译必须 fail closed。
    """

    with pytest.raises(ValueError):
        compile_seed_action(
            SeedAction("hotkey", {"key": launcher_hotkey}),
            image_width=1920,
            image_height=1080,
        )


def test_gui_only_policy_rejects_f12_devtools_key() -> None:
    """验证单独 F12 不能直接打开浏览器开发者工具。

    输入参数：
        无；构造 Seed18 ``press f12`` 动作。
    输出返回值：
        无；动作在 guest 执行前 fail-closed。
    """

    with pytest.raises(ValueError):
        compile_seed_action(
            SeedAction("press", {"key": "f12"}),
            image_width=1920,
            image_height=1080,
        )


def test_guest_command_preserves_pyautogui_failsafe() -> None:
    """验证动作代码不主动关闭 PyAutoGUI 的人工紧急中止保护。

    输入参数：
        无；编译一个普通 click。
    输出返回值：
        无；生成代码不得设置 ``FAILSAFE = False``。
    """

    compiled = compile_seed_action(
        SeedAction("click", {"point": "<point>500 500</point>"}),
        image_width=1920,
        image_height=1080,
    )

    assert compiled.command is not None
    assert "FAILSAFE = False" not in compiled.command[2]
