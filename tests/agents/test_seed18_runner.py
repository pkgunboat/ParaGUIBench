"""Seed18 GUI-only 循环的截图、动作执行和终止测试。"""

from __future__ import annotations

from typing import Any

import pytest

from paraguibench.agents.systems.gui_only.seed18.actions import SeedAction
from paraguibench.agents.systems.gui_only.seed18.runner import (
    Seed18AgentError,
    Seed18AgentSystem,
)


class _Model:
    """按顺序返回 click 与 finished 的合成模型。"""

    def __init__(self) -> None:
        """初始化模型调用记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls: list[dict[str, Any]] = []

    def next_action(self, **request: Any) -> SeedAction:
        """记录 gold-free 请求，并在第二步终止。

        输入参数：
            request：runner 传入的 instruction、截图、步号和动作历史。
        输出返回值：
            第一步 click，第二步 finished。
        """

        self.calls.append(request)
        if len(self.calls) == 1:
            return SeedAction("click", {"point": "<point>500 500</point>"})
        return SeedAction("finished", {"content": "<answer>paper3</answer>"})


class _Controller:
    """模拟 OSWorld screenshot 与 shell-free execute 接口。"""

    def __init__(self) -> None:
        """初始化执行记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.commands: list[tuple[str, ...]] = []

    def get_screenshot(self) -> bytes:
        """返回不需要真实解码的合成截图。

        输入参数：
            无。
        输出返回值：
            固定截图字节。
        """

        return b"synthetic-screenshot"

    def execute(self, command: tuple[str, ...]) -> Any:
        """记录 argv 并返回成功结果。

        输入参数：
            command：动作编译器生成的 shell-free argv。
        输出返回值：
            具有 returncode 的合成结果。
        """

        self.commands.append(command)
        return type("Result", (), {"returncode": 0})()


def test_runner_executes_bounded_actions_and_returns_terminal_output() -> None:
    """验证每步重新截图，动作只经 argv 执行，最终输出不写入循环日志。

    输入参数：
        无；注入合成 model、controller、尺寸读取器和 sleep。
    输出返回值：
        无；两步完成且只执行一次 click command。
    """

    model = _Model()
    controller = _Controller()
    environment = type("Environment", (), {"controller": controller})()
    waits: list[float] = []
    agent = Seed18AgentSystem(
        model=model,
        max_steps=4,
        post_action_delay=0.25,
        sleep_fn=waits.append,
        image_size_reader=lambda _: (1920, 1080),
    )
    task_view = {
        "task_id": "synthetic-task",
        "instruction": "Inspect the shared folder.",
    }

    result = agent.run(task_view, environment)

    assert result.final_output == "<answer>paper3</answer>"
    assert result.step_count == 2
    assert result.termination == "finished"
    assert len(controller.commands) == 1
    assert controller.commands[0][:2] == ("python3", "-c")
    assert waits == [0.25]
    assert model.calls[0]["action_history"] == ()
    assert model.calls[1]["action_history"] == ("click",)
    assert all("answer" not in call for call in model.calls)


def test_runner_rejects_oversized_screenshot_before_image_decoder() -> None:
    """验证 guest 超大响应在 Pillow 或自定义解码器之前被拒绝。

    输入参数：
        无；controller 返回超过 25 MiB 的合成截图。
    输出返回值：
        无；尺寸读取器不得被调用。
    """

    controller = _Controller()
    controller.get_screenshot = lambda: b"x" * (25 * 1024 * 1024 + 1)
    environment = type("Environment", (), {"controller": controller})()
    decoder_called = False

    def image_size_reader(_: bytes) -> tuple[int, int]:
        """标记不应发生的图片解码。

        输入参数：
            _：超大截图。
        输出返回值：
            合成尺寸；本测试要求函数不被调用。
        """

        nonlocal decoder_called
        decoder_called = True
        return 1920, 1080

    agent = Seed18AgentSystem(
        model=_Model(),
        image_size_reader=image_size_reader,
    )

    with pytest.raises(Seed18AgentError, match="截图大小"):
        agent.run(
            {"task_id": "synthetic-task", "instruction": "Inspect."},
            environment,
        )

    assert decoder_called is False


def test_runner_retries_rejected_action_within_existing_step_budget() -> None:
    """验证单个不安全动作被拒绝但不会把整个 Attempt 误判为 infra failure。

    输入参数：
        无；模型先返回被策略禁止的终端启动热键，再返回 finished。
    输出返回值：
        无；危险动作不执行，第二步收到 ``rejected_action`` 并正常终止。
    """

    class RecoveringModel:
        """按顺序返回一个被拒动作和一个合法终止动作。"""

        def __init__(self) -> None:
            """初始化请求记录。

            输入参数：
                无。
            输出返回值：
                无。
            """

            self.calls: list[dict[str, Any]] = []

        def next_action(self, **request: Any) -> SeedAction:
            """首步返回禁用热键，次步提交 exact answer。

            输入参数：
                request：runner 当前截图与动作历史。
            输出返回值：
                合成 SeedAction。
            """

            self.calls.append(request)
            if len(self.calls) == 1:
                return SeedAction("hotkey", {"key": "ctrl alt t"})
            return SeedAction(
                "finished",
                {"content": "<answer>paper3</answer>"},
            )

    model = RecoveringModel()
    controller = _Controller()
    environment = type("Environment", (), {"controller": controller})()
    agent = Seed18AgentSystem(
        model=model,
        max_steps=3,
        post_action_delay=0,
        image_size_reader=lambda _: (1920, 1080),
    )

    result = agent.run(
        {"task_id": "synthetic-task", "instruction": "Inspect."},
        environment,
    )

    assert result.termination == "finished"
    assert result.step_count == 2
    assert controller.commands == []
    assert model.calls[1]["action_history"] == ("rejected_action",)
