"""把 OpenAI-compatible 单次工具调用转换为受限 SeedAction。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit

from paraguibench.agents.systems.gui_only.seed18.actions import SeedAction
from paraguibench.agents.systems.gui_only.seed18.prompts import (
    SEED18_TOOLS,
    build_step_messages,
)

_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{1,127}")
_MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024


class Seed18ModelError(RuntimeError):
    """表示模型配置、凭据引用、请求或工具响应契约异常。"""


@dataclass(frozen=True)
class Seed18ModelConfig:
    """保存非敏感模型配置和 API key 环境变量引用。"""

    model: str
    api_key_env: str
    base_url: str
    max_output_tokens: int = 512

    def __post_init__(self) -> None:
        """验证配置不携带 secret 且 endpoint 使用 HTTPS。

        输入参数：
            无；读取 dataclass 已初始化字段。
        输出返回值：
            无；合法配置正常返回。
        异常：
            ValueError：模型名、环境变量名、URL 或输出上限不安全。
        """

        if (
            not isinstance(self.model, str)
            or not self.model.strip()
            or len(self.model) > 256
        ):
            raise ValueError("model 必须是长度不超过 256 的非空字符串")
        if (
            not isinstance(self.api_key_env, str)
            or _ENV_NAME_PATTERN.fullmatch(self.api_key_env) is None
        ):
            raise ValueError("api_key_env 必须是大写环境变量名")
        parts = urlsplit(self.base_url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError("base_url 必须是无凭据、query 和 fragment 的 HTTPS URL")
        if (
            not isinstance(self.max_output_tokens, int)
            or isinstance(self.max_output_tokens, bool)
            or not 1 <= self.max_output_tokens <= 4096
        ):
            raise ValueError("max_output_tokens 必须是 1–4096 的整数")


class Seed18OpenAIModel:
    """懒加载凭据并请求一个标准 OpenAI-compatible 工具调用。"""

    def __init__(
        self,
        config: Seed18ModelConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        """保存非敏感配置，不在构造阶段读取 API key。

        输入参数：
            config：只含模型名、HTTPS endpoint 和 key 环境变量名的配置。
            client_factory：可选 client 工厂；测试可注入 fake。
        输出返回值：
            无；API key 与 OpenAI SDK 均延迟到首个请求才读取。
        """

        if not isinstance(config, Seed18ModelConfig):
            raise TypeError("config 必须是 Seed18ModelConfig")
        self._config = config
        self._client_factory = client_factory
        self._client: Any | None = None

    def next_action(
        self,
        *,
        instruction: str,
        screenshot: bytes,
        step_index: int,
        action_history: Sequence[str],
    ) -> SeedAction:
        """请求并解析当前截图对应的唯一结构化动作。

        输入参数：
            instruction：gold-free Agent task instruction。
            screenshot：当前 PNG/JPEG 截图，最多 25 MiB。
            step_index：从 1 开始的当前步号。
            action_history：此前动作名称序列，不包含参数或推理文本。
        输出返回值：
            仅含工具名和 JSON object 参数的 SeedAction。
        异常：
            Seed18ModelError：输入、凭据、请求或响应契约无效；不会回显
            secret、原始响应或模型 arguments。
        """

        _validate_step_input(
            instruction=instruction,
            screenshot=screenshot,
            step_index=step_index,
            action_history=action_history,
        )
        media_type = _detect_media_type(screenshot)
        messages = build_step_messages(
            instruction=instruction,
            screenshot=screenshot,
            media_type=media_type,
            step_index=step_index,
            action_history=action_history,
        )
        client = self._get_client()
        try:
            response = client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                tools=list(SEED18_TOOLS),
                tool_choice="required",
                parallel_tool_calls=False,
                max_tokens=self._config.max_output_tokens,
            )
        except Exception as error:
            raise Seed18ModelError(
                f"模型请求失败：{type(error).__name__}"
            ) from None
        return _parse_action(response)

    def _get_client(self) -> Any:
        """在首个请求时解析 key 引用并创建 SDK client。

        输入参数：
            无。
        输出返回值：
            缓存的 OpenAI-compatible client。
        异常：
            Seed18ModelError：环境变量缺失或 client 初始化失败；消息仅包含
            环境变量名和异常类型，不包含凭据值。
        """

        if self._client is not None:
            return self._client
        api_key = os.environ.get(self._config.api_key_env)
        if not api_key:
            raise Seed18ModelError(
                f"缺少 API key 环境变量：{self._config.api_key_env}"
            )
        factory = self._client_factory or _create_openai_client
        try:
            self._client = factory(
                api_key=api_key,
                base_url=self._config.base_url,
            )
        except Exception as error:
            raise Seed18ModelError(
                f"模型 client 初始化失败：{type(error).__name__}"
            ) from None
        return self._client


def _create_openai_client(*, api_key: str, base_url: str) -> Any:
    """延迟导入 OpenAI SDK 并创建 client。

    输入参数：
        api_key：仅来自调用进程环境变量的 secret。
        base_url：已验证的无凭据 HTTPS endpoint。
    输出返回值：
        OpenAI SDK client；调用者仅在内存中缓存。
    """

    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url)


def _validate_step_input(
    *,
    instruction: str,
    screenshot: bytes,
    step_index: int,
    action_history: Sequence[str],
) -> None:
    """验证单步模型输入具有边界且历史不携带动作参数。

    输入参数：
        instruction：Agent-visible instruction。
        screenshot：当前图片 bytes。
        step_index：从 1 开始的步号。
        action_history：只允许短动作名称的序列。
    输出返回值：
        无；合法时正常返回。
    异常：
        Seed18ModelError：任一字段类型或边界无效。
    """

    if (
        not isinstance(instruction, str)
        or not instruction
        or len(instruction) > 20_000
    ):
        raise Seed18ModelError("instruction 必须是有界非空字符串")
    if (
        not isinstance(screenshot, bytes)
        or not screenshot
        or len(screenshot) > _MAX_SCREENSHOT_BYTES
    ):
        raise Seed18ModelError("screenshot 必须是最大 25 MiB 的非空 bytes")
    if (
        not isinstance(step_index, int)
        or isinstance(step_index, bool)
        or step_index < 1
    ):
        raise Seed18ModelError("step_index 必须是正整数")
    if isinstance(action_history, (str, bytes)) or len(action_history) > 100:
        raise Seed18ModelError("action_history 必须是最多 100 项的动作名序列")
    for name in action_history:
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z_]{1,32}", name) is None
        ):
            raise Seed18ModelError("action_history 含无效动作名")


def _detect_media_type(screenshot: bytes) -> str:
    """通过魔数识别模型允许的截图媒体类型。

    输入参数：
        screenshot：已通过大小边界检查的截图 bytes。
    输出返回值：
        ``image/png`` 或 ``image/jpeg``。
    异常：
        Seed18ModelError：字节不具有受支持的图片魔数。
    """

    if screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if screenshot.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise Seed18ModelError("screenshot 不是受支持的 PNG/JPEG")


def _parse_action(response: Any) -> SeedAction:
    """从响应中提取唯一工具调用，不在异常中回显原始对象。

    输入参数：
        response：OpenAI-compatible chat completion 响应。
    输出返回值：
        工具名和 JSON object 参数构成的 SeedAction。
    异常：
        Seed18ModelError：choices、tool_calls、函数名或 arguments 无效。
    """

    try:
        choices = response.choices
        if len(choices) != 1:
            raise ValueError
        tool_calls = choices[0].message.tool_calls
        if len(tool_calls) != 1:
            raise ValueError
        function = tool_calls[0].function
        name = function.name
        raw_arguments = function.arguments
        if not isinstance(name, str) or not name:
            raise ValueError
        if not isinstance(raw_arguments, str):
            raise ValueError
        parameters = json.loads(raw_arguments)
        if not isinstance(parameters, dict):
            raise ValueError
    except Exception:
        raise Seed18ModelError("模型响应不符合唯一结构化工具调用契约") from None
    return SeedAction(name=name, parameters=parameters)
