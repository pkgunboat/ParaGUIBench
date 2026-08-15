"""Qwen OpenAI-compatible 模型适配器的凭据、请求和响应契约测试。"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest

from paraguibench.agents.workers.qwen.images import PreparedQwenScreenshot
from paraguibench.agents.workers.qwen.model import (
    QwenActionRejectedError,
    QwenModelConfig,
    QwenModelError,
    QwenOpenAIModel,
)


class _Completions:
    """记录请求并返回预设 chat completion 的测试替身。"""

    def __init__(self, response: Any) -> None:
        """保存响应和请求记录。

        输入参数：
            response：create 调用应返回的 SDK 形状对象。
        输出返回值：
            无。
        """

        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        """记录完整非 header 请求并返回预设响应。

        输入参数：
            request：模型、消息、工具和成本预算。
        输出返回值：
            构造时注入的响应。
        """

        self.requests.append(request)
        return self.response


def _prepared(_: bytes, *, max_pixels: int) -> PreparedQwenScreenshot:
    """返回固定 PNG 形状以隔离 Pillow，仅记录参数可被调用。

    输入参数：
        _：原始合成截图，不在测试中解码。
        max_pixels：配置传入的图片预算。
    输出返回值：
        固定的已准备截图。
    """

    assert max_pixels == 4_194_304
    return PreparedQwenScreenshot(b"processed", 1920, 1088)


def _native_response(
    *,
    arguments: str = '{"action":"wait","time":1}',
    content: str | None = None,
) -> dict[str, Any]:
    """构造含唯一原生 computer_use tool call 的测试响应。

    输入参数：
        arguments：待解析的 JSON 工具参数字符串。
        content：可选模型正文，用于验证不会被回放。
    输出返回值：
        OpenAI-compatible chat completion 的最小 Mapping。
    """

    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "computer_use",
                                "arguments": arguments,
                            }
                        }
                    ],
                    "content": content,
                }
            }
        ]
    }


def test_qwen_model_lazily_reads_secret_and_uses_native_function_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 API key 延迟读取，且 Qwen 请求默认关闭 thinking。

    输入参数：
        monkeypatch：pytest 环境变量隔离 fixture。
    输出返回值：
        无；请求使用唯一 computer_use tool，解析为 click 动作。
    """

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name="computer_use",
                                arguments=(
                                    '{"action":"left_click","coordinate":[500,500]}'
                                ),
                            )
                        )
                    ],
                    content=None,
                )
            )
        ]
    )
    completions = _Completions(response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    factory_calls: list[dict[str, Any]] = []

    def factory(**arguments: Any) -> Any:
        """记录 client 初始化参数并返回测试 client。

        输入参数：
            arguments：api_key、base_url、timeout 和 max_retries。
        输出返回值：
            具有 chat.completions.create 的测试对象。
        """

        factory_calls.append(arguments)
        return client

    monkeypatch.delenv("TEST_QWEN_API_KEY", raising=False)
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            api_key_env="TEST_QWEN_API_KEY",
        ),
        client_factory=factory,
        image_preparer=_prepared,
    )
    assert factory_calls == []
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")

    action = model.next_action(
        instruction="Inspect the page.",
        screenshot=b"synthetic",
        step_index=1,
        action_history=(),
    )

    assert action.name == "click"
    assert factory_calls[0]["api_key"] == "secret-value"
    request = completions.requests[0]
    assert request["model"] == "qwen3.7-flash"
    assert request["tool_choice"] == {
        "type": "function",
        "function": {"name": "computer_use"},
    }
    assert request["parallel_tool_calls"] is False
    assert request["stream"] is False
    assert request["extra_body"] == {"enable_thinking": False}
    assert request["tools"][0]["function"]["name"] == "computer_use"


def test_qwen_model_supports_explicit_osworld_xml_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证代理未返回 native tool_calls 时仍可解析固定 OSWorld XML。

    输入参数：
        monkeypatch：提供测试 API key。
    输出返回值：
        无；terminate success 被映射为 finished，而非执行任意文本。
    """

    xml = """Action: finish.
<tool_call><function=computer_use>
<parameter=action>terminate</parameter>
<parameter=status>success</parameter>
<parameter=text>&lt;answer&gt;paper3&lt;/answer&gt;</parameter>
</function></tool_call>"""
    response = {"choices": [{"message": {"tool_calls": None, "content": xml}}]}
    completions = _Completions(response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://workspace.example/compatible-mode/v1",
            api_key_env="TEST_QWEN_API_KEY",
            tool_protocol="osworld_xml",
        ),
        client_factory=lambda **_: client,
        image_preparer=lambda data, max_pixels: PreparedQwenScreenshot(
            b"processed-" + data,
            1920,
            1088,
        ),
    )

    action = model.next_action(
        instruction="Return one answer.",
        screenshot=b"synthetic",
        step_index=1,
        action_history=(),
        screenshot_history=(b"historical",),
    )

    assert action.name == "finished"
    assert action.parameters["content"] == "<answer>paper3</answer>"
    request = completions.requests[0]
    assert "tools" not in request
    assert "tool_choice" not in request
    assert "extra_body" not in request
    system_prompt = request["messages"][0]["content"]
    assert "<function=computer_use>" in system_prompt
    assert "<parameter=action>" in system_prompt
    image_parts = [
        part
        for part in request["messages"][1]["content"]
        if part["type"] == "image_url"
    ]
    assert len(image_parts) == 2
    assert image_parts[-1]["image_url"]["url"].endswith(
        base64.b64encode(b"processed-synthetic").decode("ascii")
    )


def test_qwen_model_sends_visual_history_oldest_to_newest_with_low_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证历史图独立缩放，按旧到新排列，当前图始终最后。

    输入参数：
        monkeypatch：提供合成 API key 环境变量。
    输出返回值：
        无；当前图使用 4194304 像素，两张历史图各使用
        1048576 像素，最终消息含按标签排列的三张图。
    """

    prepared_calls: list[tuple[bytes, int]] = []

    def prepare(
        data: bytes,
        *,
        max_pixels: int,
    ) -> PreparedQwenScreenshot:
        """记录图像和像素预算并返回可区分的合成 PNG。

        输入参数：
            data：当前或历史截图 bytes。
            max_pixels：该图在本次请求中的像素上限。
        输出返回值：
            内容为 ``prepared-<data>`` 的合成截图。
        """

        prepared_calls.append((data, max_pixels))
        return PreparedQwenScreenshot(b"prepared-" + data, 64, 64)

    completions = _Completions(_native_response())
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://gateway.example/v1",
            api_key_env="TEST_QWEN_API_KEY",
        ),
        client_factory=lambda **_: client,
        image_preparer=prepare,
    )

    model.next_action(
        instruction="Compare the documents.",
        screenshot=b"current",
        step_index=3,
        action_history=("click", "hotkey"),
        screenshot_history=(b"oldest", b"newest"),
    )

    assert prepared_calls == [
        (b"current", 4_194_304),
        (b"oldest", 1_048_576),
        (b"newest", 1_048_576),
    ]
    content = completions.requests[0]["messages"][1]["content"]
    text_parts = [part["text"] for part in content if part["type"] == "text"]
    assert "Historical screenshot 1/2 (context only):" in text_parts
    assert "Historical screenshot 2/2 (context only):" in text_parts
    assert text_parts[-1] == "Current screenshot (act on this image):"
    image_parts = [
        part["image_url"]["url"] for part in content if part["type"] == "image_url"
    ]
    assert [base64.b64decode(url.split(",", 1)[1]) for url in image_parts] == [
        b"prepared-oldest",
        b"prepared-newest",
        b"prepared-current",
    ]


def test_qwen_history_budget_never_exceeds_current_image_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证历史图预算还受当前图预算二次限制。

    输入参数：
        monkeypatch：提供合成 API key。
    输出返回值：
        无；当前预算为 500000 时，历史图也只获得 500000。
    """

    budgets: list[int] = []

    def prepare(
        data: bytes,
        *,
        max_pixels: int,
    ) -> PreparedQwenScreenshot:
        """记录每张图的实际预算。

        输入参数：
            data：未解码的合成截图。
            max_pixels：模型 adapter 选定的像素上限。
        输出返回值：
            非空的合成 PNG 形状对象。
        """

        budgets.append(max_pixels)
        return PreparedQwenScreenshot(b"prepared-" + data, 64, 64)

    completions = _Completions(_native_response())
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://gateway.example/v1",
            api_key_env="TEST_QWEN_API_KEY",
            max_image_pixels=500_000,
            max_history_image_pixels=1_000_000,
        ),
        client_factory=lambda **_: client,
        image_preparer=prepare,
    )

    model.next_action(
        instruction="Inspect.",
        screenshot=b"current",
        step_index=1,
        action_history=(),
        screenshot_history=(b"historical",),
    )

    assert budgets == [500_000, 500_000]


@pytest.mark.parametrize(
    "screenshot_history",
    [
        (b"one", b"two", b"three", b"four", b"five"),
        (b"",),
        b"not-a-sequence-of-images",
    ],
)
def test_qwen_model_rejects_invalid_visual_history_before_request(
    screenshot_history: object,
) -> None:
    """验证超过四张、空图和裸 bytes 历史在网络请求前失败。

    输入参数：
        screenshot_history：参数化的非法历史输入。
    输出返回值：
        无；每项均抛出 ``QwenModelError``。
    """

    model = QwenOpenAIModel(
        QwenModelConfig(base_url="https://gateway.example/v1"),
        client_factory=lambda **_: pytest.fail("invalid input reached client"),
        image_preparer=_prepared,
    )

    with pytest.raises(QwenModelError):
        model.next_action(
            instruction="Inspect.",
            screenshot=b"current",
            step_index=1,
            action_history=(),
            screenshot_history=screenshot_history,  # type: ignore[arg-type]
        )


def test_qwen_model_does_not_replay_prior_raw_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证后续请求不包含上一轮模型正文或隐藏推理。

    输入参数：
        monkeypatch：提供合成 API key。
    输出返回值：
        无；第二轮只含指令、动作名和图像，不含 sentinel。
    """

    sentinel = "private-prior-model-response"
    completions = _Completions(_native_response(content=sentinel))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://gateway.example/v1",
            api_key_env="TEST_QWEN_API_KEY",
        ),
        client_factory=lambda **_: client,
        image_preparer=lambda data, max_pixels: PreparedQwenScreenshot(
            b"processed-" + data,
            64,
            64,
        ),
    )

    model.next_action(
        instruction="Inspect.",
        screenshot=b"first",
        step_index=1,
        action_history=(),
    )
    model.next_action(
        instruction="Inspect.",
        screenshot=b"second",
        step_index=2,
        action_history=("wait",),
        screenshot_history=(b"first",),
    )

    second_request = json.dumps(
        completions.requests[1],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert sentinel not in second_request


def test_qwen_model_caps_configured_history_pixels_at_one_megapixel() -> None:
    """验证历史图像素预算只能从硬上限向下调整。

    输入参数：
        无；构造超过 1048576 像素的历史图配置。
    输出返回值：
        无；构造必须抛出 ``ValueError``。
    """

    with pytest.raises(ValueError):
        QwenModelConfig(
            base_url="https://gateway.example/v1",
            max_history_image_pixels=1_048_577,
        )


def test_qwen_model_parse_error_does_not_echo_raw_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证畸形模型回复中的敏感文本不会进入异常消息。

    输入参数：
        monkeypatch：提供测试 API key。
    输出返回值：
        无；异常只暴露稳定契约类别。
    """

    marker = "private-response-marker"
    response = {"choices": [{"message": {"content": marker}}]}
    completions = _Completions(response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://workspace.example/compatible-mode/v1",
            api_key_env="TEST_QWEN_API_KEY",
        ),
        client_factory=lambda **_: client,
        image_preparer=_prepared,
    )

    with pytest.raises(QwenActionRejectedError) as raised:
        model.next_action(
            instruction="Inspect.",
            screenshot=b"synthetic",
            step_index=1,
            action_history=(),
        )

    assert marker not in str(raised.value)


@pytest.mark.parametrize(
    ("enable_thinking", "expected_tool_choice"),
    [
        (
            False,
            {
                "type": "function",
                "function": {"name": "computer_use"},
            },
        ),
        (None, "auto"),
    ],
)
def test_qwen_generic_endpoint_omits_provider_thinking_extension(
    monkeypatch: pytest.MonkeyPatch,
    enable_thinking: bool | None,
    expected_tool_choice: object,
) -> None:
    """验证通用 endpoint 不接收 DashScope 专用的关闭 thinking 参数。

    输入参数：
        monkeypatch：提供合成 API key 环境变量。
        enable_thinking：显式关闭或完全省略 provider thinking 控制。
        expected_tool_choice：对应的强制函数或 ``auto`` 选择。
    输出返回值：
        无；请求不含 ``extra_body``，且 tool choice 与 thinking 语义一致。
    """

    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "computer_use",
                                "arguments": '{"action":"wait","time":1}',
                            }
                        }
                    ]
                }
            }
        ]
    }
    completions = _Completions(response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://gateway.example/v1",
            api_key_env="TEST_QWEN_API_KEY",
            enable_thinking=enable_thinking,
        ),
        client_factory=lambda **_: client,
        image_preparer=_prepared,
    )

    model.next_action(
        instruction="Wait for the page.",
        screenshot=b"synthetic",
        step_index=1,
        action_history=(),
    )

    request = completions.requests[0]
    assert request["tool_choice"] == expected_tool_choice
    assert "extra_body" not in request


def test_qwen_thinking_uses_auto_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证深度思考模式不发送官方不支持的强制 tool choice。

    输入参数：
        monkeypatch：提供测试 API key。
    输出返回值：
        无；请求使用 auto、关闭并行工具调用，并显式开启 thinking。
    """

    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "computer_use",
                                "arguments": '{"action":"wait","time":1}',
                            }
                        }
                    ]
                }
            }
        ]
    }
    completions = _Completions(response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_QWEN_API_KEY", "secret-value")
    model = QwenOpenAIModel(
        QwenModelConfig(
            base_url="https://gateway.example/v1",
            api_key_env="TEST_QWEN_API_KEY",
            enable_thinking=True,
        ),
        client_factory=lambda **_: client,
        image_preparer=_prepared,
    )

    action = model.next_action(
        instruction="Wait for the page.",
        screenshot=b"synthetic",
        step_index=1,
        action_history=(),
    )

    assert action.name == "wait"
    request = completions.requests[0]
    assert request["tool_choice"] == "auto"
    assert request["parallel_tool_calls"] is False
    assert request["extra_body"] == {"enable_thinking": True}
