"""Holo3Agent —— 多步 predict 版本。

每次 predict() 都会：
  1) 把当前截图作为新的 <observation> 追加到 ChatHistory；
  2) 调模型；
  3) 解析 Step 并把它作为 assistant 消息追加；
  4) 返回可执行 actions + 解析后的 Step + 像素坐标。

instruction 在第一次 predict 时写入 system 提示；后续 predict 默认沿用上次的
instruction，调用方也可在每轮通过参数显式重申，提示词体里就会再贴一遍。

调用方负责执行 actions（pyautogui 代码 / DONE / FAIL / WAIT / ANSWER:…）。
若需要把执行结果反馈给下一轮，调用方可以在下一次 predict 前调用
`agent.feed_tool_output(tool_name, result_text)`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image
from pydantic import ValidationError

from .client import HoloClient, HoloResponse
from .history import ChatHistory
from .parser import step_to_pixel, step_to_pyautogui
from .prompts import render_prompt
from .tools import Step

logger = logging.getLogger(__name__)


@dataclass
class PredictResult:
    """单轮预测结果。

    字段:
        response:   人类可读摘要（[tool_name@px=...] thought）。
        actions:    pyautogui 代码列表或 sentinel（DONE/FAIL/WAIT/ANSWER:...）。
        step:       已校验的 Step。
        pixel_xy:   主操作的像素坐标，没有就 None。
        raw:        服务端原始响应。
        round_index: 自 reset() 起的轮次编号（从 1 开始）。
    """

    response: str
    actions: list[str]
    step: Step
    pixel_xy: tuple[int, int] | None
    raw: HoloResponse
    round_index: int


class Holo3Agent:
    """支持多步交互的 Holo3 agent。

    生命周期:
        agent = Holo3Agent(base_url=..., model=...)
        agent.reset()                                # 必须在每个任务开头调一次
        for round in range(max_rounds):
            r = agent.predict(instruction, {"screenshot": png_bytes})
            ...  # 调用方执行 r.actions
            if needs_feedback:
                agent.feed_tool_output(r.step.tool_call.tool_name, "result text")

    obs 接口: dict 至少含 key "screenshot"，值是 PNG / JPEG 字节。
    """

    def __init__(
        self,
        *,
        base_url: str = "http://10.1.110.114:8000/v1",
        model: str = "Holo3-35B-A3B.Q4_K_S.gguf",
        api_key: str = "EMPTY",
        platform: str = "Ubuntu",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        enable_thinking: bool = False,
        timeout: float = 900.0,
        max_screenshots: int = 3,
        seed: int | None = None,
    ):
        self.platform = platform
        self.enable_thinking = enable_thinking
        self.max_screenshots = max_screenshots
        self.client = HoloClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            seed=seed,
        )
        self.history: ChatHistory | None = None
        self.round_index = 0
        self.current_instruction: str | None = None
        # 缓存第一帧截图的尺寸，作为 system 提示里 screen_w/screen_h 的依据。
        # 后续帧若分辨率有变化（VM 几乎不会变），仍按首帧标定。
        self._screen_w: int | None = None
        self._screen_h: int | None = None

    # ------------------------------------------------------------------ public
    def reset(self, _logger: Any = None) -> None:
        """每个任务开始前调用：清空对话历史与轮次计数。"""
        self.history = None
        self.round_index = 0
        self.current_instruction = None
        self._screen_w = None
        self._screen_h = None

    def feed_tool_output(self, tool_name: str, result: str) -> None:
        """把上一轮 action 的执行结果作为下一轮的工具反馈写入历史。

        非必需。即使不调用，下一轮的新截图本身就是隐式反馈。
        """
        if self.history is None:
            raise RuntimeError("尚未调用 reset() 或 predict() 初始化 history")
        self.history.add_tool_output(tool_name, result)

    def predict(
        self,
        instruction: str,
        obs: dict[str, Any],
    ) -> PredictResult:
        """跑一步预测。

        参数:
            instruction: 任务文本。第 1 轮用来构造 system 提示；之后每轮也会
                         在 <observation> 上方贴一份，避免长对话里目标漂移。
            obs:         至少含 "screenshot" 键，值是 PNG / JPEG 字节流。

        返回:
            PredictResult。

        异常:
            ValidationError —— 模型输出不符合 Step schema。
        """
        screenshot: bytes = obs["screenshot"]
        if not isinstance(screenshot, (bytes, bytearray)):
            raise TypeError("obs['screenshot'] 必须是 PNG/JPEG 字节流")

        w, h = self._image_size(screenshot)

        # 首轮：建 history、确定屏幕尺寸用于 system 提示
        if self.history is None:
            self._screen_w, self._screen_h = w, h
            self.current_instruction = instruction
            sys_prompt = self._render_system_prompt(instruction, w, h)
            self.history = ChatHistory(
                system_prompt=sys_prompt,
                max_screenshots=self.max_screenshots,
            )

        self.round_index += 1
        logger.info(
            "Holo3Agent.predict round=%d image=%dx%d bytes=%d",
            self.round_index,
            w,
            h,
            len(screenshot),
        )

        # 严格遵循 Holo3 协议：user 消息只允许 <observation> 或 <tool_output>。
        # instruction 只通过 system prompt 传递一次（_render_system_prompt 已把
        # "# Current task\n{instruction}" 贴在系统提示末尾），不再每轮重贴，
        # 避免 prompt 膨胀和偏离训练分布。
        self.history.add_observation(screenshot)

        messages = self.history.messages_for_request()
        schema = Step.model_json_schema()

        logger.info("Holo3Agent.predict: calling model ...")
        resp = self.client.call(
            messages,
            schema=schema,
            enable_thinking=self.enable_thinking,
        )
        logger.info(
            "Holo3Agent.predict: returned mode=%s content_len=%d reasoning_len=%s",
            resp.mode,
            len(resp.content or ""),
            len(resp.reasoning_content or "") if resp.reasoning_content else "None",
        )
        try:
            step = Step.model_validate_json(resp.json_text)
        except ValidationError:
            logger.error(
                "Step 解析失败。mode=%s raw_content=%r json_text=%r",
                resp.mode,
                (resp.content or "")[:500],
                (resp.json_text or "")[:500],
            )
            raise

        # 把 Step 追回历史，供下一轮上下文
        self.history.add_assistant_step(step)

        actions = step_to_pyautogui(step, w, h)
        pixel = step_to_pixel(step, w, h)
        response_text = self._summarize(step, pixel)
        return PredictResult(
            response=response_text,
            actions=actions,
            step=step,
            pixel_xy=pixel,
            raw=resp,
            round_index=self.round_index,
        )

    # ----------------------------------------------------------------- helpers
    def _render_system_prompt(self, instruction: str, w: int, h: int) -> str:
        base = render_prompt(platform=self.platform, screen_w=w, screen_h=h)
        # 在系统提示尾部补一句任务上下文 hint
        return (
            f"{base}\n\n"
            f"# Current task\n"
            f"{instruction}\n"
        )

    @staticmethod
    def _image_size(png_bytes: bytes) -> tuple[int, int]:
        with Image.open(BytesIO(png_bytes)) as im:
            return im.size

    @staticmethod
    def _summarize(step: Step, pixel: tuple[int, int] | None) -> str:
        name = step.tool_call.tool_name
        thought = step.thought
        if pixel is not None:
            return f"[{name}@px={pixel}] {thought}"
        return f"[{name}] {thought}"
