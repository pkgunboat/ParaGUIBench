"""Qwen GUI worker 与 GUI-only Agent System 的共享执行路径测试。"""

from __future__ import annotations

from typing import Any

import pytest

from paraguibench.agents.systems.gui_only import QwenGUIOnlyAgentSystem
from paraguibench.agents.workers.gui import GUIAction, GUIActionRejectedError
from paraguibench.agents.workers.qwen import QwenGUIWorker


class _Model:
    """先点击后完成的无网络 Qwen action model 替身。"""

    def __init__(self) -> None:
        """初始化请求记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls: list[dict[str, Any]] = []

    def next_action(self, **request: Any) -> GUIAction:
        """记录请求并按调用次数返回 click 或 finished。

        输入参数：
            request：instruction、截图、步号和动作名历史。
        输出返回值：
            可由公共 GUI compiler 消费的动作。
        """

        self.calls.append(request)
        if len(self.calls) == 1:
            return GUIAction("click", {"point": "<point>500 500</point>"})
        return GUIAction("finished", {"content": "<answer>paper3</answer>"})


class _Controller:
    """模拟截图与 argv-only guest execute 的 OSWorld controller。"""

    def __init__(self) -> None:
        """初始化命令记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.commands: list[tuple[str, ...]] = []

    def get_screenshot(self) -> bytes:
        """返回由注入尺寸读取器处理的合成截图。

        输入参数：
            无。
        输出返回值：
            固定非空 bytes。
        """

        return b"synthetic-screenshot"

    def execute(self, command: tuple[str, ...]) -> Any:
        """记录 shell-free argv 并返回成功退出码。

        输入参数：
            command：公共动作 compiler 生成的 argv tuple。
        输出返回值：
            具有 returncode=0 的测试对象。
        """

        self.commands.append(command)
        return type("Result", (), {"returncode": 0})()


def test_qwen_worker_is_reused_by_gui_only_agent_system() -> None:
    """验证 GUI-only 外层只做 task/result 映射，不复制 Qwen 执行循环。

    输入参数：
        无；注入合成模型、controller、图片尺寸和无阻塞 sleep。
    输出返回值：
        无；两步完成，且只有一个 click 以 argv 形式进入 guest。
    """

    model = _Model()
    controller = _Controller()
    waits: list[float] = []
    worker = QwenGUIWorker(
        model=model,
        max_steps=4,
        post_action_delay=0.25,
        sleep_fn=waits.append,
        image_size_reader=lambda _: (1920, 1080),
    )
    agent = QwenGUIOnlyAgentSystem(worker=worker)

    result = agent.run(
        {"task_id": "synthetic", "instruction": "Inspect the folder."},
        type("Environment", (), {"controller": controller})(),
    )

    assert result.final_output == "<answer>paper3</answer>"
    assert result.step_count == 2
    assert result.termination == "finished"
    assert controller.commands[0][:2] == ("python3", "-c")
    assert model.calls[0]["screenshot_history"] == ()
    assert model.calls[1]["action_history"] == ("click",)
    assert model.calls[1]["screenshot_history"] == (b"synthetic-screenshot",)
    assert waits == [0.25]


def test_qwen_worker_keeps_default_two_screenshots_oldest_to_newest() -> None:
    """验证默认视觉历史是最近两张，且不把当前图重复放入历史。

    输入参数：
        无；注入 6 张可区分的合成截图和先点击 5 次再完成的模型。
    输出返回值：
        无；每步历史按旧到新滑动，第 6 步只看到第 4、5 张。
    """

    class SlidingModel(_Model):
        """产生多个成功点击后终止的模型替身。"""

        def next_action(self, **request: Any) -> GUIAction:
            """记录每步截图历史并返回预定动作。

            输入参数：
                request：GUI 循环的当前截图、历史截图和步号。
            输出返回值：
                前 5 步返回点击，第 6 步返回完成。
            """

            self.calls.append(request)
            if len(self.calls) <= 5:
                return GUIAction(
                    "click",
                    {"point": "<point>500 500</point>"},
                )
            return GUIAction("finished", {"content": "done"})

    class SequenceController(_Controller):
        """每步返回不同合成截图的 controller 替身。"""

        def __init__(self) -> None:
            """初始化命令记录和截图序号。

            输入参数：
                无。
            输出返回值：
                无。
            """

            super().__init__()
            self.screenshot_index = 0

        def get_screenshot(self) -> bytes:
            """返回当前步唯一的合成截图 bytes。

            输入参数：
                无。
            输出返回值：
                ``screenshot-N`` 形式的非空 bytes。
            """

            self.screenshot_index += 1
            return f"screenshot-{self.screenshot_index}".encode()

    model = SlidingModel()
    worker = QwenGUIWorker(
        model=model,
        max_steps=6,
        post_action_delay=0,
        image_size_reader=lambda _: (1920, 1080),
    )

    result = worker.run(
        "Compare the visible documents.",
        type("Environment", (), {"controller": SequenceController()})(),
    )

    assert result.termination == "finished"
    assert [call["screenshot_history"] for call in model.calls] == [
        (),
        (b"screenshot-1",),
        (b"screenshot-1", b"screenshot-2"),
        (b"screenshot-2", b"screenshot-3"),
        (b"screenshot-3", b"screenshot-4"),
        (b"screenshot-4", b"screenshot-5"),
    ]


@pytest.mark.parametrize("history_limit", [0, 1, 4])
def test_qwen_worker_accepts_bounded_visual_history_limits(
    history_limit: int,
) -> None:
    """验证 Qwen worker 接受全部公开的合法历史窗口。

    输入参数：
        history_limit：参数化的 0、1 或 4 张历史截图。
    输出返回值：
        无；构造不抛出异常。
    """

    QwenGUIWorker(
        model=_Model(),
        screenshot_history_limit=history_limit,
        image_size_reader=lambda _: (1920, 1080),
    )


@pytest.mark.parametrize("history_limit", [-1, 5, True])
def test_qwen_worker_rejects_invalid_visual_history_limits(
    history_limit: object,
) -> None:
    """验证负数、超上限数和布尔值在构造阶段被拒绝。

    输入参数：
        history_limit：参数化的非法历史窗口。
    输出返回值：
        无；必须抛出 ``ValueError``。
    """

    with pytest.raises(ValueError):
        QwenGUIWorker(
            model=_Model(),
            screenshot_history_limit=history_limit,  # type: ignore[arg-type]
            image_size_reader=lambda _: (1920, 1080),
        )


def test_qwen_worker_rejects_unsafe_launcher_hotkey_and_recovers() -> None:
    """验证模型请求终端快捷键时 fail-closed，并允许下一步纠正。

    输入参数：
        无；首步返回 ctrl-alt-t，次步返回正常终止。
    输出返回值：
        无；guest 未收到危险命令，历史标记 rejected_action。
    """

    class RecoveringModel(_Model):
        """产生一个不安全动作后立即结束的模型替身。"""

        def next_action(self, **request: Any) -> GUIAction:
            """记录请求并返回测试动作序列。

            输入参数：
                request：GUI 循环单步请求。
            输出返回值：
                第一步禁用 hotkey，第二步 finished。
            """

            self.calls.append(request)
            if len(self.calls) == 1:
                return GUIAction("hotkey", {"key": "ctrl alt t"})
            return GUIAction("finished", {"content": "done"})

    model = RecoveringModel()
    controller = _Controller()
    worker = QwenGUIWorker(
        model=model,
        max_steps=3,
        post_action_delay=0,
        image_size_reader=lambda _: (1920, 1080),
    )

    result = worker.run(
        "Inspect safely.",
        type("Environment", (), {"controller": controller})(),
    )

    assert result.termination == "finished"
    assert controller.commands == []
    assert model.calls[1]["action_history"] == ("rejected_action",)
    assert model.calls[1]["screenshot_history"] == ()


def test_qwen_worker_retries_only_classified_action_response_rejection() -> None:
    """验证模型结构化响应偏差可重试，而不伪造 guest 动作。

    输入参数：
        无；首步抛出 provider-neutral 拒绝，次步正常完成。
    输出返回值：
        无；循环在剩余步数内恢复，历史只包含脱敏拒绝标记。
    """

    class RejectingOnceModel(_Model):
        """首次拒绝、第二次返回终态的模型替身。"""

        def next_action(self, **request: Any) -> GUIAction:
            """记录请求并产生一次可重试拒绝。

            输入参数：
                request：GUI 循环的有界单步请求。
            输出返回值：
                第二次调用返回 ``finished``；首次抛出可重试拒绝。
            """

            self.calls.append(request)
            if len(self.calls) == 1:
                raise GUIActionRejectedError("synthetic-rejection")
            return GUIAction("finished", {"content": "done"})

    model = RejectingOnceModel()
    worker = QwenGUIWorker(
        model=model,
        max_steps=3,
        post_action_delay=0,
        image_size_reader=lambda _: (1920, 1080),
    )

    result = worker.run(
        "Inspect safely.",
        type("Environment", (), {"controller": _Controller()})(),
    )

    assert result.termination == "finished"
    assert result.step_count == 2
    assert model.calls[1]["action_history"] == ("rejected_action",)
    assert model.calls[1]["screenshot_history"] == ()


def test_qwen_worker_accepts_falsey_injected_model() -> None:
    """验证注入模型的真值不会被误用作 config/model 选择条件。

    输入参数：
        无；构造 ``__bool__`` 返回 False 的合法 action model。
    输出返回值：
        无；worker 使用该实例完成，不尝试创建真实 Qwen client。
    """

    class FalseyModel(_Model):
        """布尔值为 False，但完整实现 action model 的替身。"""

        def __bool__(self) -> bool:
            """返回 False 以覆盖注入实例回归路径。

            输入参数：
                无。
            输出返回值：
                始终为 False。
            """

            return False

        def next_action(self, **request: Any) -> GUIAction:
            """记录请求并立即返回完成动作。

            输入参数：
                request：GUI 循环单步请求。
            输出返回值：
                一个 ``finished`` GUIAction。
            """

            self.calls.append(request)
            return GUIAction("finished", {"content": "done"})

    model = FalseyModel()
    worker = QwenGUIWorker(
        model=model,
        max_steps=1,
        post_action_delay=0,
        image_size_reader=lambda _: (1920, 1080),
    )

    result = worker.run(
        "Inspect safely.",
        type("Environment", (), {"controller": _Controller()})(),
    )

    assert result.termination == "finished"
    assert len(model.calls) == 1
