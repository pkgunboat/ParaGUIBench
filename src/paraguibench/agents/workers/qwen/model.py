"""通过 OpenAI-compatible API 把 Qwen 3.7 输出转换为唯一安全 GUIAction。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit

from paraguibench.agents.workers.gui import GUIAction, GUIActionRejectedError
from paraguibench.agents.workers.qwen.images import (
    PreparedQwenScreenshot,
    QwenImageError,
    prepare_qwen_screenshot,
)
from paraguibench.agents.workers.qwen.parser import (
    QwenActionParseError,
    computer_use_arguments_to_action,
    parse_osworld_xml_action,
)
from paraguibench.agents.workers.qwen.prompts import (
    QWEN_COMPUTER_TOOLS,
    build_qwen_step_messages,
)
from paraguibench.integrations.model_endpoint import validate_model_base_url
from paraguibench.integrations.qwen import (
    create_openai_compatible_qwen_client,
)

_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{1,127}")
_ACTION_NAME_PATTERN = re.compile(r"[a-z_]{1,32}")
_MAX_SCREENSHOT_BYTES = 25 * 1024 * 1024
_MAX_HISTORY_IMAGE_PIXELS = 1_048_576


class QwenModelError(RuntimeError):
    """表示 Qwen 配置、凭据引用、请求、图片或响应契约异常。"""


class QwenActionRejectedError(QwenModelError, GUIActionRejectedError):
    """表示 Qwen 响应结构偏差；GUI 循环可在剩余步数预算内重试。"""


@dataclass(frozen=True)
class QwenModelConfig:
    """保存 Qwen Flash 的非敏感 OpenAI-compatible 调用配置。

    ``model`` 默认使用开发别名 ``qwen3.7-flash``。正式 benchmark 应显式
    固定可复现快照，例如 ``qwen3.7-flash-2026-07-15``。
    """

    base_url: str
    model: str = "qwen3.7-flash"
    api_key_env: str = "DASHSCOPE_API_KEY"
    max_output_tokens: int = 1024
    max_image_pixels: int = 4_194_304
    max_history_image_pixels: int = 1_048_576
    temperature: float = 0.0
    top_p: float = 0.9
    enable_thinking: bool | None = False
    request_timeout_seconds: float = 130.0
    history_limit: int = 20
    tool_protocol: str = "native"

    def __post_init__(self) -> None:
        """验证配置不携带 secret，且 endpoint 与成本边界明确。

        输入参数：
            无；读取 dataclass 已初始化字段。
        输出返回值：
            无；合法配置正常返回。
        异常：
            ValueError：模型名、key 引用、endpoint 或任一预算字段无效。
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
        validate_model_base_url(self.base_url)
        if (
            not isinstance(self.max_output_tokens, int)
            or isinstance(self.max_output_tokens, bool)
            or not 1 <= self.max_output_tokens <= 4096
        ):
            raise ValueError("max_output_tokens 必须是 1–4096 的整数")
        if (
            not isinstance(self.max_image_pixels, int)
            or isinstance(self.max_image_pixels, bool)
            or not 1024 <= self.max_image_pixels <= 16_000_000
        ):
            raise ValueError("max_image_pixels 必须是 1024–16000000 的整数")
        if (
            not isinstance(self.max_history_image_pixels, int)
            or isinstance(self.max_history_image_pixels, bool)
            or not 1024 <= self.max_history_image_pixels <= _MAX_HISTORY_IMAGE_PIXELS
        ):
            raise ValueError("max_history_image_pixels 必须是 1024–1048576 的整数")
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError("temperature 必须是 0–2 的数值")
        if (
            isinstance(self.top_p, bool)
            or not isinstance(self.top_p, (int, float))
            or not 0 < self.top_p <= 1
        ):
            raise ValueError("top_p 必须是 0–1 的正数")
        if self.enable_thinking is not None and not isinstance(
            self.enable_thinking,
            bool,
        ):
            raise ValueError("enable_thinking 必须是 bool 或 None")
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not isinstance(self.request_timeout_seconds, (int, float))
            or not 1 <= self.request_timeout_seconds <= 600
        ):
            raise ValueError("request_timeout_seconds 必须是 1–600 秒")
        if (
            not isinstance(self.history_limit, int)
            or isinstance(self.history_limit, bool)
            or not 0 <= self.history_limit <= 100
        ):
            raise ValueError("history_limit 必须是 0–100 的整数")
        if self.tool_protocol not in {"native", "osworld_xml"}:
            raise ValueError("tool_protocol 必须是 native 或 osworld_xml")


class QwenOpenAIModel:
    """懒加载凭据并请求一个 Qwen 原生或 OSWorld 兼容的 computer_use 动作。"""

    def __init__(
        self,
        config: QwenModelConfig,
        *,
        client_factory: Callable[..., Any] | None = None,
        image_preparer: Callable[..., PreparedQwenScreenshot] | None = None,
    ) -> None:
        """保存非敏感配置并注入可测试依赖。

        输入参数：
            config：只含模型名、已校验 endpoint、key 环境变量名和预算的配置。
            client_factory：可选 OpenAI-compatible client 工厂。
            image_preparer：可选截图缩放函数；测试可注入无 Pillow 实现。
        输出返回值：
            无；构造阶段不读取 API key、不导入 OpenAI SDK，也不处理图片。
        """

        if not isinstance(config, QwenModelConfig):
            raise TypeError("config 必须是 QwenModelConfig")
        self._config = config
        self._client_factory = client_factory
        self._image_preparer = image_preparer or prepare_qwen_screenshot
        self._client: Any | None = None

    def next_action(
        self,
        *,
        instruction: str,
        screenshot: bytes,
        step_index: int,
        action_history: Sequence[str],
        screenshot_history: Sequence[bytes] = (),
    ) -> GUIAction:
        """请求并解析当前截图对应的唯一原子 GUI 动作。

        输入参数：
            instruction：gold-free 完整任务或 ParaGUI 子任务说明。
            screenshot：当前 PNG/JPEG 截图，最多 25 MiB。
            step_index：从 1 开始的当前步号。
            action_history：此前动作名称，不包含参数或模型推理。
            screenshot_history：按旧到新排列的 0–4 张历史截图；
                历史图使用独立的低像素预算。
        输出返回值：
            provider-neutral ``GUIAction``，随后仍需公共白名单 compiler 校验。
        异常：
            QwenActionRejectedError：响应不能形成唯一结构化动作，调用循环
                可在步数预算内重试。
            QwenModelError：输入、图片、凭据、网络或 provider 异常。
                两类错误都不会回显 secret、原始响应、截图或模型 arguments。
        """

        _validate_step_input(
            instruction=instruction,
            screenshot=screenshot,
            step_index=step_index,
            action_history=action_history,
            screenshot_history=screenshot_history,
        )
        try:
            prepared = self._image_preparer(
                screenshot,
                max_pixels=self._config.max_image_pixels,
            )
        except QwenImageError:
            raise QwenModelError("Qwen 截图处理失败：QwenImageError") from None
        except Exception as error:
            raise QwenModelError(f"Qwen 截图处理失败：{type(error).__name__}") from None
        prepared_history: list[PreparedQwenScreenshot] = []
        history_pixel_budget = min(
            self._config.max_image_pixels,
            self._config.max_history_image_pixels,
        )
        for historical_screenshot in screenshot_history:
            try:
                prepared_history.append(
                    self._image_preparer(
                        historical_screenshot,
                        max_pixels=history_pixel_budget,
                    )
                )
            except QwenImageError:
                raise QwenModelError("Qwen 历史截图处理失败：QwenImageError") from None
            except Exception as error:
                raise QwenModelError(
                    f"Qwen 历史截图处理失败：{type(error).__name__}"
                ) from None
        history = (
            tuple(action_history[-self._config.history_limit :])
            if self._config.history_limit
            else ()
        )
        messages = build_qwen_step_messages(
            instruction=instruction,
            screenshot=prepared.data,
            media_type=prepared.media_type,
            step_index=step_index,
            action_history=history,
            screenshot_history=tuple(item.data for item in prepared_history),
            tool_protocol=self._config.tool_protocol,
        )
        request: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "max_tokens": self._config.max_output_tokens,
            "temperature": float(self._config.temperature),
            "top_p": float(self._config.top_p),
            "stream": False,
        }
        if self._config.tool_protocol == "native":
            request["tools"] = list(QWEN_COMPUTER_TOOLS)
            request["tool_choice"] = (
                {
                    "type": "function",
                    "function": {"name": "computer_use"},
                }
                if self._config.enable_thinking is False
                else "auto"
            )
            request["parallel_tool_calls"] = False
        if _should_send_thinking_control(self._config):
            request["extra_body"] = {"enable_thinking": self._config.enable_thinking}
        try:
            response = self._get_client().chat.completions.create(**request)
        except Exception as error:
            raise QwenModelError(f"Qwen 模型请求失败：{type(error).__name__}") from None
        try:
            return _parse_response_action(
                response,
                tool_protocol=self._config.tool_protocol,
            )
        except QwenActionParseError:
            raise QwenActionRejectedError("Qwen 响应不符合唯一结构化动作契约") from None
        except Exception as error:
            raise QwenModelError(f"Qwen 响应解析失败：{type(error).__name__}") from None

    def _get_client(self) -> Any:
        """在首个请求时解析 key 引用并创建、缓存 SDK client。

        输入参数：
            无。
        输出返回值：
            缓存的 OpenAI-compatible client。
        异常：
            QwenModelError：环境变量缺失或 client 初始化失败；消息只含
            环境变量名和异常类型，不包含凭据值。
        """

        if self._client is not None:
            return self._client
        api_key = os.environ.get(self._config.api_key_env)
        if not api_key:
            raise QwenModelError(f"缺少 API key 环境变量：{self._config.api_key_env}")
        factory = self._client_factory or create_openai_compatible_qwen_client
        try:
            self._client = factory(
                api_key=api_key,
                base_url=self._config.base_url,
                timeout=float(self._config.request_timeout_seconds),
                max_retries=2,
            )
        except Exception as error:
            raise QwenModelError(
                f"Qwen client 初始化失败：{type(error).__name__}"
            ) from None
        return self._client


def _should_send_thinking_control(config: QwenModelConfig) -> bool:
    """判断 endpoint 是否应收到 DashScope 专用 thinking 扩展。

    输入参数：
        config：已经完成 endpoint 与 enable_thinking 类型校验的 Qwen 配置。
    输出返回值：
        显式开启 thinking 时始终为 True；显式关闭时仅对阿里云/DashScope
        endpoint 为 True；``None`` 表示完全省略 provider 扩展。
    """

    if config.enable_thinking is None:
        return False
    if config.enable_thinking is True:
        return True
    hostname = (urlsplit(config.base_url).hostname or "").lower()
    return "dashscope" in hostname or hostname.endswith(".aliyuncs.com")


def _validate_step_input(
    *,
    instruction: str,
    screenshot: bytes,
    step_index: int,
    action_history: Sequence[str],
    screenshot_history: Sequence[bytes],
) -> None:
    """验证单步请求边界，确保历史不携带动作参数或任意模型文本。

    输入参数：
        instruction：完整任务或 subtask 指令。
        screenshot：当前截图 bytes。
        step_index：从 1 开始的步号。
        action_history：仅允许短动作名称的 sequence。
        screenshot_history：按旧到新排列的最多 4 张截图 bytes。
    输出返回值：
        无；合法输入正常返回。
    异常：
        QwenModelError：任一字段类型或边界无效。
    """

    if not isinstance(instruction, str) or not instruction or len(instruction) > 20_000:
        raise QwenModelError("instruction 必须是有界非空字符串")
    if (
        not isinstance(screenshot, bytes)
        or not screenshot
        or len(screenshot) > _MAX_SCREENSHOT_BYTES
    ):
        raise QwenModelError("screenshot 必须是最大 25 MiB 的非空 bytes")
    if (
        not isinstance(step_index, int)
        or isinstance(step_index, bool)
        or step_index < 1
    ):
        raise QwenModelError("step_index 必须是正整数")
    if isinstance(action_history, (str, bytes)) or len(action_history) > 100:
        raise QwenModelError("action_history 必须是最多 100 项的动作名序列")
    for name in action_history:
        if not isinstance(name, str) or _ACTION_NAME_PATTERN.fullmatch(name) is None:
            raise QwenModelError("action_history 含无效动作名")
    if (
        isinstance(screenshot_history, (str, bytes))
        or not isinstance(screenshot_history, Sequence)
        or len(screenshot_history) > 4
    ):
        raise QwenModelError("screenshot_history 必须是最多 4 张截图的序列")
    for historical_screenshot in screenshot_history:
        if (
            not isinstance(historical_screenshot, bytes)
            or not historical_screenshot
            or len(historical_screenshot) > _MAX_SCREENSHOT_BYTES
        ):
            raise QwenModelError("screenshot_history 含无效截图")


def _parse_response_action(
    response: Any,
    *,
    tool_protocol: str,
) -> GUIAction:
    """按配置的单一 wire protocol 解析响应，拒绝跨协议降级。

    输入参数：
        response：OpenAI-compatible chat completion 响应对象或 Mapping。
        tool_protocol：``native`` 只接受 SDK tool_calls；``osworld_xml`` 只
            接受 assistant content 中的唯一 XML tool call。
    输出返回值：
        唯一且已完成 provider 参数映射的 ``GUIAction``。
    异常：
        QwenActionParseError：choices、tool_calls、函数名或 arguments 无效。
    """

    choices = _field(response, "choices")
    if (
        isinstance(choices, (str, bytes))
        or not isinstance(choices, Sequence)
        or len(choices) != 1
    ):
        raise QwenActionParseError("响应 choices 无效")
    message = _field(choices[0], "message")
    if message is None:
        raise QwenActionParseError("响应 message 缺失")
    tool_calls = _field(message, "tool_calls")
    if tool_protocol == "native":
        if (
            isinstance(tool_calls, (str, bytes))
            or not isinstance(tool_calls, Sequence)
            or len(tool_calls) != 1
        ):
            raise QwenActionParseError("响应必须包含唯一 tool_call")
        function = _field(tool_calls[0], "function")
        if _field(function, "name") != "computer_use":
            raise QwenActionParseError("tool_call 函数名无效")
        arguments = _field(function, "arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                raise QwenActionParseError("tool arguments JSON 无效") from None
        if not isinstance(arguments, Mapping):
            raise QwenActionParseError("tool arguments 必须是 object")
        return computer_use_arguments_to_action(arguments)
    if tool_protocol == "osworld_xml":
        if tool_calls:
            raise QwenActionParseError("XML 模式不接受 native tool_calls")
        return parse_osworld_xml_action(_content_text(_field(message, "content")))
    raise QwenActionParseError("未知 tool protocol")


def _field(value: Any, field_name: str) -> Any:
    """从 SDK 对象或 Mapping 中读取字段，不触发对象字符串化。

    输入参数：
        value：OpenAI SDK model、测试替身或 Mapping。
        field_name：待读取属性名。
    输出返回值：
        字段值；不存在时返回 ``None``。
    """

    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _content_text(content: Any) -> str:
    """合并 OpenAI-compatible content 字符串或文本分片。

    输入参数：
        content：字符串、SDK content parts 或 Mapping parts。
    输出返回值：
        仅由 ``text`` 字段拼接的字符串；未知类型返回空串。
    """

    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for item in content:
            text = _field(item, "text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""
