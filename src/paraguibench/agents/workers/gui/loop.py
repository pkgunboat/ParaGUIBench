"""执行截图—结构化动作—shell-free guest argv 的有界 GUI worker 循环。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from io import BytesIO
import time
from typing import Any, Protocol

from paraguibench.agents.workers.contracts import GUIWorkerResult
from paraguibench.agents.workers.gui.actions import (
    GUIAction,
    GUIActionError,
    compile_gui_action,
)

_MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024


class GUIWorkerError(RuntimeError):
    """表示 GUI worker 生命周期、截图、动作或 guest 执行异常。"""


class GUIActionRejectedError(RuntimeError):
    """表示模型响应无法形成唯一安全动作，可在步数预算内重试。"""


class GUIActionModel(Protocol):
    """定义 provider-neutral GUI 循环所需的单步动作模型接口。"""

    def next_action(
        self,
        *,
        instruction: str,
        screenshot: bytes,
        step_index: int,
        action_history: Sequence[str],
        screenshot_history: Sequence[bytes],
    ) -> GUIAction:
        """根据当前截图和脱敏动作名历史返回唯一结构化动作。

        输入参数：
            instruction：当前完整任务或 ParaGUI 子任务说明。
            screenshot：controller 返回的当前 PNG/JPEG 截图。
            step_index：从 1 开始的当前动作步号。
            action_history：此前动作名称，不含参数、截图或模型推理。
            screenshot_history：按旧到新排列的有界历史截图；只用于
                需要跨视图记忆的多模态模型。
        输出返回值：
            只能由白名单编译器消费的 ``GUIAction``。
        """


class GUIActionLoop:
    """把单步动作模型运行成可复用、可审计的 GUI worker。"""

    def __init__(
        self,
        *,
        model: GUIActionModel,
        max_steps: int = 18,
        post_action_delay: float = 1.0,
        screenshot_history_limit: int = 0,
        sleep_fn: Callable[[float], None] = time.sleep,
        image_size_reader: Callable[[bytes], tuple[int, int]] | None = None,
    ) -> None:
        """注入模型和有界时间、图片依赖，不触碰凭据或环境。

        输入参数：
            model：每次仅返回一个 ``GUIAction`` 的 provider adapter。
            max_steps：最多模型动作次数，范围 1–100。
            post_action_delay：每个 guest GUI 命令后的稳定等待秒数。
            screenshot_history_limit：传给模型的历史截图数，范围 0–4；
                通用循环默认不发送历史，具体 worker 可显式开启。
            sleep_fn：等待函数；测试可注入无阻塞记录器。
            image_size_reader：截图尺寸读取器；默认延迟导入 Pillow。
        输出返回值：
            无；构造阶段不会调用模型、读取 API key 或访问 controller。
        """

        if not hasattr(model, "next_action"):
            raise TypeError("model 缺少 next_action")
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
        if (
            not isinstance(screenshot_history_limit, int)
            or isinstance(screenshot_history_limit, bool)
            or not 0 <= screenshot_history_limit <= 4
        ):
            raise ValueError("screenshot_history_limit 必须是 0–4 的整数")
        self._model = model
        self._max_steps = max_steps
        self._post_action_delay = float(post_action_delay)
        self._screenshot_history_limit = screenshot_history_limit
        self._sleep = sleep_fn
        self._image_size_reader = image_size_reader or _read_image_size

    def run(self, instruction: str, environment: Any) -> GUIWorkerResult:
        """在调用方保证独占的环境中执行最多 max_steps 次 GUI 动作。

        输入参数：
            instruction：长度不超过 20000 的非空完整任务或子任务说明。
            environment：必须公开 ``controller``，支持截图与 argv-only execute。
        输出返回值：
            terminal 动作携带的文本、实际步数和终止类型；达到上限时返回
            ``max_steps``，由上层按完整任务或 subtask 语义映射。
        异常：
            GUIWorkerError：instruction、controller、截图或 guest 命令异常；
            不回显截图、模型输出、stdout 或 stderr。
        """

        _validate_instruction(instruction)
        controller = getattr(environment, "controller", None)
        if controller is None:
            raise GUIWorkerError("environment 缺少 GUI controller")
        history: list[str] = []
        screenshot_history: list[bytes] = []
        for step_index in range(1, self._max_steps + 1):
            screenshot = controller.get_screenshot()
            if not isinstance(screenshot, bytes) or not screenshot:
                raise GUIWorkerError("controller 返回空或非 bytes 截图")
            if len(screenshot) > _MAX_SCREENSHOT_BYTES:
                raise GUIWorkerError("controller 截图大小超过 25 MiB 上限")
            try:
                image_width, image_height = self._image_size_reader(screenshot)
            except Exception as error:
                raise GUIWorkerError(
                    f"无法读取截图尺寸：{type(error).__name__}"
                ) from None
            try:
                action = self._model.next_action(
                    instruction=instruction,
                    screenshot=screenshot,
                    step_index=step_index,
                    action_history=tuple(history),
                    screenshot_history=tuple(screenshot_history),
                )
            except GUIActionRejectedError:
                # 只有已分类的结构化响应偏差可重试；网络、凭据、
                # provider 和未知异常仍传播给上层，避免隐藏真实故障。
                history.append("rejected_action")
                continue
            try:
                compiled = compile_gui_action(
                    action,
                    image_width=image_width,
                    image_height=image_height,
                )
            except GUIActionError:
                # 违反动作契约的输出保持 fail-closed，不会进入 guest。
                history.append("rejected_action")
                continue
            if compiled.kind == "terminal":
                return GUIWorkerResult(
                    final_output=compiled.terminal_content or "",
                    step_count=step_index,
                    termination=compiled.terminal_name or "terminal",
                )
            history.append(action.name)
            if self._screenshot_history_limit:
                screenshot_history.append(screenshot)
                del screenshot_history[: -self._screenshot_history_limit]
            if compiled.kind == "wait":
                self._sleep(compiled.wait_seconds or 0.0)
                continue
            if compiled.command is None:
                raise GUIWorkerError("guest_command 缺少 argv")
            result = controller.execute(compiled.command)
            if not hasattr(result, "returncode") or result.returncode != 0:
                raise GUIWorkerError("guest GUI 命令执行失败")
            if self._post_action_delay > 0:
                self._sleep(self._post_action_delay)
        return GUIWorkerResult(
            final_output="",
            step_count=self._max_steps,
            termination="max_steps",
        )


def _validate_instruction(instruction: str) -> None:
    """验证 worker instruction 的类型和内存边界。

    输入参数：
        instruction：待发送给 GUI 模型的完整任务或子任务说明。
    输出返回值：
        无；合法输入正常返回。
    异常：
        GUIWorkerError：instruction 为空、类型错误或超过 20000 字符。
    """

    if not isinstance(instruction, str) or not instruction or len(instruction) > 20_000:
        raise GUIWorkerError("instruction 必须是长度 1–20000 的字符串")


def _read_image_size(screenshot: bytes) -> tuple[int, int]:
    """使用延迟导入的 Pillow 读取并约束截图尺寸。

    输入参数：
        screenshot：controller 返回的 PNG/JPEG bytes。
    输出返回值：
        正整数 ``(width, height)``，每边不超过 20000 像素。
    异常：
        GUIWorkerError：图片无法解析或尺寸超界。
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
        raise GUIWorkerError("截图尺寸超出允许范围")
    return width, height
