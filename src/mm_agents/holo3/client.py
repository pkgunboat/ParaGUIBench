"""OpenAI 兼容客户端封装（含三级降级）。

不同后端对结构化输出的支持差异较大：
    vLLM         → extra_body.structured_outputs.json  (Holo3 训练时的格式)
    llama.cpp    → response_format = {"type": "json_object"}  通常可用
    最坏情况      → 纯文本 + 正则提取 JSON

`HoloClient.call` 会按这个顺序尝试，前一档失败 / 报参数错误时自动降级，并把
最终使用的模式记录在 last_mode 字段供日志查看。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from openai import BadRequestError, OpenAI

logger = logging.getLogger(__name__)


@dataclass
class HoloResponse:
    """模型一次调用的结构化结果。

    字段:
        content:           message.content 原文（可能含 <think>...</think>）。
        reasoning_content: message.reasoning_content（若服务端给出）。
        mode:              实际生效的结构化输出模式: "structured" / "json_object" / "plain"
        json_text:         去掉思考链后的纯 JSON 字符串，供 Step.model_validate_json 使用。
    """

    content: str
    reasoning_content: str | None
    mode: str
    json_text: str
    raw: Any = field(default=None, repr=False)


_THINK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_RAW_JSON_RE = re.compile(r"\{[^{}]*?\"tool_call\".*\}", re.DOTALL)


def _strip_thoughts(text: str) -> str:
    """剥离 <think>...</think>，兼容部分服务端未把推理放到 reasoning_content 的情况。"""
    return _THINK_RE.sub("", text).strip()


def _extract_json(text: str) -> str:
    """从模型输出中抽出第一段看起来像目标 JSON 的内容。

    优先 ```json``` 围栏；其次匹配含 "tool_call" 的花括号块；都失败则原样返回，
    交给上层抛出 pydantic 的 ValidationError 让问题暴露。
    """
    text = _strip_thoughts(text)
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # 找一个最大的、包含 tool_call 的 JSON 块
    candidates = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                blk = text[start : i + 1]
                if '"tool_call"' in blk:
                    candidates.append(blk)
                start = -1
    if candidates:
        return max(candidates, key=len).strip()
    return text


class HoloClient:
    """对 OpenAI SDK 的极薄封装，处理 Holo3 协议要求的两种结构化输出 + 降级。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "EMPTY",
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout: float = 120.0,
        seed: int | None = None,
    ):
        # max_retries=0：模式降级由我们自己管，避免每次 500 都被 SDK 默认重试 2 次
        self.client = OpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0
        )
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # 固定 sampling 随机性，让结果可复现；None 时让 vllm 默认（每次随机）
        self.seed = seed
        # 缓存当前服务端支持的模式，避免每次都从 structured 起步
        self._preferred_mode: str | None = None

    # ----------------------------------------------------------------- public
    def call(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any],
        enable_thinking: bool = False,
    ) -> HoloResponse:
        """发起一次 chat completion。

        参数:
            messages:         OpenAI 标准消息列表。
            schema:           Step.model_json_schema()；用于 structured / json_object 两档。
            enable_thinking:  Qwen3 chat template 的 thinking 开关。Holo3 是 native
                              reasoning model，本地单步定位时关掉更稳。

        返回:
            HoloResponse，json_text 字段已剥除思考链、可直接喂给 Step.model_validate_json。
        """
        modes_to_try = self._mode_order()
        last_err: Exception | None = None
        for mode in modes_to_try:
            try:
                resp = self._call_one(messages, schema, enable_thinking, mode)
                self._preferred_mode = mode  # 记下成功的模式
                return resp
            except BadRequestError as e:
                logger.warning("Holo3 client: mode=%s rejected by server: %s", mode, e)
                last_err = e
            except Exception as e:  # noqa: BLE001  保留兜底，确保会降级到 plain
                logger.warning("Holo3 client: mode=%s raised %s: %s", mode, type(e).__name__, e)
                last_err = e
        assert last_err is not None
        raise last_err

    # ---------------------------------------------------------------- private
    def _mode_order(self) -> list[str]:
        if self._preferred_mode == "json_object":
            return ["json_object", "plain"]
        if self._preferred_mode == "plain":
            return ["plain"]
        return ["structured", "json_object", "plain"]

    def _call_one(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        enable_thinking: bool,
        mode: str,
    ) -> HoloResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.seed is not None:
            kwargs["seed"] = self.seed
        extra_body: dict[str, Any] = {
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        if mode == "structured":
            extra_body["structured_outputs"] = {"json": schema}
        elif mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        kwargs["extra_body"] = extra_body

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        content = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", None)
        json_text = _extract_json(content)
        return HoloResponse(
            content=content,
            reasoning_content=reasoning,
            mode=mode,
            json_text=json_text,
            raw=resp,
        )
