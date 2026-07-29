"""执行截图—结构化动作—guest argv 的有界 Seed18 GUI-only 循环。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from io import BytesIO
import time
from typing import Any, Protocol

from paraguibench.agents.systems.gui_only.seed18.actions import (
    SeedAction,
    SeedActionError,
    compile_seed_action,
)
from paraguibench.agents import AgentRunResult

_MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024


class Seed18AgentError(RuntimeError):
    """表示 Agent 生命周期、截图、模型动作或 guest 执行异常。"""


class Seed18ActionModel(Protocol):
    """定义 Seed18 runner 所需的最小截图动作模型接口。"""

    def next_action(
        self,
        *,
        instruction: str,
        screenshot: bytes,
        step_index: int,
        action_history: tuple[str, ...],
    ) -> SeedAction:
        """根据当前截图返回一个结构化动作。"""


class Seed18AgentSystem:
    """把 Seed18 模型作为 AttemptRunner 可调用的 GUI-only Agent System。"""

    def __init__(
        self,
        *,
        model: Seed18ActionModel,
        max_steps: int = 18,
        post_action_delay: float = 1.0,
        sleep_fn: Callable[[float], None] = time.sleep,
        image_size_reader: Callable[[bytes], tuple[int, int]] | None = None,
    ) -> None:
        """构造有界 GUI 循环并注入可测试的时间/图片依赖。

        输入参数：
            model：只返回 SeedAction 的截图动作模型。
            max_steps：最多模型动作次数，范围 1–100。
            post_action_delay：每个 guest GUI 命令后的稳定等待秒数。
            sleep_fn：等待函数；测试可注入无阻塞记录器。
            image_size_reader：截图尺寸读取器；默认延迟导入 Pillow。
        输出返回值：
            无；构造阶段不读取凭据、截图或环境。
        """

        if (
            not isinstance(max_steps, int)
            or isinstance(max_steps, bool)
            or not 1 <= max_steps <= 100
        ):
            raise ValueError("max_steps 必须是 1–100 的整数")
        if (
            isinstance(post_action_delay, bool)
            or not isinstance(post_action_delay, (int, float))
            or not 0 <= post_action_delay <= 30
        ):
            raise ValueError("post_action_delay 必须是 0–30 秒数值")
        self._model = model
        self._max_steps = max_steps
        self._post_action_delay = float(post_action_delay)
        self._sleep = sleep_fn
        self._image_size_reader = image_size_reader or _read_image_size

    def run(
        self,
        task_view: dict[str, Any],
        environment: Any,
    ) -> AgentRunResult:
        """在已准备环境中执行最多 max_steps 次可审计 GUI 动作。

        输入参数：
            task_view：AttemptRunner 提供的 gold-free Agent task view。
            environment：必须公开 ``controller``，支持截图与 shell-free execute。
        输出返回值：
            terminal 动作携带的最终输出、实际步数和终止类型；达到上限时
            返回空输出与 ``max_steps``，交由 evaluator 正常判失败。
        异常：
            Seed18AgentError：task、controller、图片尺寸或 guest 命令失败；
            不回显截图、模型输出、stdout 或 stderr。
        """

        instruction = _read_instruction(task_view)
        controller = getattr(environment, "controller", None)
        if controller is None:
            raise Seed18AgentError("environment 缺少 GUI controller")
        history: list[str] = []
        for step_index in range(1, self._max_steps + 1):
            screenshot = controller.get_screenshot()
            if not isinstance(screenshot, bytes) or not screenshot:
                raise Seed18AgentError("controller 返回空或非 bytes 截图")
            if len(screenshot) > _MAX_SCREENSHOT_BYTES:
                raise Seed18AgentError("controller 截图大小超过 25 MiB 上限")
            try:
                image_width, image_height = self._image_size_reader(screenshot)
            except Exception as error:
                raise Seed18AgentError(
                    f"无法读取截图尺寸：{type(error).__name__}"
                ) from None
            action = self._model.next_action(
                instruction=instruction,
                screenshot=screenshot,
                step_index=step_index,
                action_history=tuple(history),
            )
            try:
                compiled = compile_seed_action(
                    action,
                    image_width=image_width,
                    image_height=image_height,
                )
            except SeedActionError:
                # 单步模型输出可以偶发违反动作契约。该动作必须保持 fail-closed：
                # 不发送给 guest，也不扩充总步数预算；仅把脱敏状态反馈给下一步。
                history.append("rejected_action")
                continue
            if compiled.kind == "terminal":
                return AgentRunResult(
                    final_output=compiled.terminal_content or "",
                    step_count=step_index,
                    termination=compiled.terminal_name or "terminal",
                )
            history.append(action.name)
            if compiled.kind == "wait":
                self._sleep(compiled.wait_seconds or 0.0)
                continue
            if compiled.command is None:
                raise Seed18AgentError("guest_command 缺少 argv")
            result = controller.execute(compiled.command)
            if (
                not hasattr(result, "returncode")
                or result.returncode != 0
            ):
                raise Seed18AgentError("guest GUI 命令执行失败")
            if self._post_action_delay > 0:
                self._sleep(self._post_action_delay)
        return AgentRunResult(
            final_output="",
            step_count=self._max_steps,
            termination="max_steps",
        )


def _read_instruction(task_view: Mapping[str, Any]) -> str:
    """读取 gold-free task view 中的有界 instruction。

    输入参数：
        task_view：Agent 可见字段 Mapping。
    输出返回值：
        非空 instruction。
    异常：
        Seed18AgentError：task view 或 instruction 类型、长度无效。
    """

    if not isinstance(task_view, Mapping):
        raise Seed18AgentError("task_view 必须是 Mapping")
    instruction = task_view.get("instruction")
    if (
        not isinstance(instruction, str)
        or not instruction
        or len(instruction) > 20_000
    ):
        raise Seed18AgentError("task_view 缺少有界非空 instruction")
    return instruction


def _read_image_size(screenshot: bytes) -> tuple[int, int]:
    """使用延迟导入的 Pillow 读取并约束截图尺寸。

    输入参数：
        screenshot：controller 返回的 PNG/JPEG bytes。
    输出返回值：
        正整数 ``(width, height)``，每边不超过 20000 像素。
    异常：
        Seed18AgentError：图片无法解析或尺寸超界。
    """

    from PIL import Image

    with Image.open(BytesIO(screenshot)) as image:
        width, height = image.size
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or not 1 <= width <= 20_000
        or not 1 <= height <= 20_000
    ):
        raise Seed18AgentError("截图尺寸超出允许范围")
    return width, height
