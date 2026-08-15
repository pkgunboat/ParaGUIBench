"""把 Qwen 单步模型装配成 GUI-only 与 ParaGUI 可共享的 GUI worker。"""

from __future__ import annotations

from collections.abc import Callable

from paraguibench.agents.workers.gui import GUIActionLoop, GUIActionModel
from paraguibench.agents.workers.qwen.model import (
    QwenModelConfig,
    QwenOpenAIModel,
)


class QwenGUIWorker(GUIActionLoop):
    """使用 Qwen computer-use adapter 的有界、shell-free GUI worker。"""

    def __init__(
        self,
        *,
        config: QwenModelConfig | None = None,
        model: GUIActionModel | None = None,
        max_steps: int = 18,
        post_action_delay: float = 1.0,
        screenshot_history_limit: int = 2,
        sleep_fn: Callable[[float], None] | None = None,
        image_size_reader: Callable[[bytes], tuple[int, int]] | None = None,
    ) -> None:
        """由真实 Qwen 配置或测试模型构造一个 worker。

        输入参数：
            config：真实运行使用的 Qwen 非敏感配置；与 ``model`` 二选一。
            model：已实现 ``next_action`` 的注入模型；测试或自定义 provider
                使用，与 ``config`` 二选一。
            max_steps：当前完整任务或 subtask 的最大动作步数。
            post_action_delay：每个 guest GUI 命令后的稳定等待秒数。
            screenshot_history_limit：按旧到新发送给 Qwen 的历史截图数，
                范围 0–4，默认 2；不包含历史模型原文或动作参数。
            sleep_fn：可选等待函数；省略时使用通用循环默认实现。
            image_size_reader：可选截图尺寸读取器。
        输出返回值：
            无；config 路径构造 Qwen adapter，但 API key 仍延迟到首个请求。
        """

        if (config is None) == (model is None):
            raise ValueError("config 与 model 必须且只能提供一个")
        resolved_model = (
            model if model is not None else QwenOpenAIModel(config)  # type: ignore[arg-type]
        )
        arguments = {
            "model": resolved_model,
            "max_steps": max_steps,
            "post_action_delay": post_action_delay,
            "screenshot_history_limit": screenshot_history_limit,
            "image_size_reader": image_size_reader,
        }
        if sleep_fn is not None:
            arguments["sleep_fn"] = sleep_fn
        super().__init__(**arguments)
