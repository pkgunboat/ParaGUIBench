"""Holo3 多步对话历史管理。

按 Holo3 协议要求：
- system 消息只 1 条，含 <output_format> 块；
- user 消息有两种：
    a) 观察消息: list[chunk]，含 "<observation>" + image_url + "</observation>"
    b) 工具结果: 文本字符串，形如 '<tool_output tool="click">...</tool_output>'
- assistant 消息：只放 Step.model_dump_json()，绝不要把 reasoning_content 拼回。
- 图片预算：最近 max_screenshots 张保留 image_url，老的替换为
  {"type":"text","text":"[screenshot evicted]"}，但 <observation> 包装保留。
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from .tools import Step

logger = logging.getLogger(__name__)


class ChatHistory:
    """维护 Holo3 多步对话的消息列表。

    用法:
        h = ChatHistory(system_prompt="...", max_screenshots=3)
        h.add_observation(png_bytes)              # 第 1 轮
        h.add_assistant_step(step1)
        h.add_tool_output("click", "ok")
        h.add_observation(png_bytes2)             # 第 2 轮
        ...
        msgs = h.messages_for_request()
    """

    def __init__(self, system_prompt: str, max_screenshots: int = 3):
        if max_screenshots < 1:
            raise ValueError("max_screenshots 必须 >= 1")
        self.max_screenshots = max_screenshots
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

    # ----------------------------------------------------------------- helpers
    def _make_image_chunk(self, png_bytes: bytes, mime: str = "image/png") -> dict[str, Any]:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        }

    # ------------------------------------------------------------------ public
    def add_observation(self, png_bytes: bytes, mime: str = "image/png") -> None:
        """追加一个观察消息（带截图），并在追加后维护图片预算。"""
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "<observation>"},
                    self._make_image_chunk(png_bytes, mime=mime),
                    {"type": "text", "text": "</observation>"},
                ],
            }
        )
        self._trim_screenshots()

    def add_assistant_step(self, step: Step) -> None:
        """把已校验的 Step 作为 assistant 消息追加。

        重要: 这里写的是 step.model_dump_json()，绝对不要把模型的
        reasoning_content / <think>...</think> 拼回历史 —— 那样会让下一轮
        chat template 把推理链丢掉时产生噪声。
        """
        self.messages.append(
            {
                "role": "assistant",
                "content": step.model_dump_json(),
            }
        )

    def add_tool_output(self, tool_name: str, result: str) -> None:
        """把工具执行结果以 user role 追加，遵循 <tool_output> 包装。"""
        self.messages.append(
            {
                "role": "user",
                "content": f'<tool_output tool="{tool_name}">\n{result}\n</tool_output>',
            }
        )

    def messages_for_request(self) -> list[dict[str, Any]]:
        """返回当前 messages 的浅拷贝，供 client 调用使用。"""
        return list(self.messages)

    # ------------------------------------------------------------- internal
    def _trim_screenshots(self) -> None:
        """保证最近 max_screenshots 张图片为 image_url，其余替换为占位文本。

        遍历顺序: 从新到旧。计数到 max_screenshots 之前的保留，之后的把
        chunk 改成 {"type":"text","text":"[screenshot evicted]"}。
        <observation> 包装本身（前后的两个 text chunk）始终保留。
        """
        seen = 0
        for msg in reversed(self.messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for chunk in msg["content"]:
                if not isinstance(chunk, dict) or chunk.get("type") != "image_url":
                    continue
                seen += 1
                if seen > self.max_screenshots:
                    chunk.clear()
                    chunk["type"] = "text"
                    chunk["text"] = "[screenshot evicted]"
