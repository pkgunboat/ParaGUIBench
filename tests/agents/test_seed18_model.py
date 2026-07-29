"""Seed18 OpenAI-compatible 模型适配器的凭据与工具响应契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from paraguibench.agents.systems.gui_only.seed18.model import (
    Seed18ModelConfig,
    Seed18ModelError,
    Seed18OpenAIModel,
)
from paraguibench.agents.systems.gui_only.seed18.prompts import (
    build_step_messages,
)


class _Completions:
    """模拟 OpenAI chat.completions endpoint 并记录请求。"""

    def __init__(self, response: Any) -> None:
        """保存待返回响应。

        输入参数：
            response：测试指定的 OpenAI-compatible 响应对象。
        输出返回值：
            无。
        """

        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        """记录结构化请求并返回预设响应。

        输入参数：
            request：模型适配器传入的关键字参数。
        输出返回值：
            构造阶段保存的响应对象。
        """

        self.requests.append(request)
        return self.response


def _tool_response(name: str, arguments: str) -> Any:
    """构造一个仅含单次工具调用的合成响应。

    输入参数：
        name：工具名称。
        arguments：JSON 字符串参数。
    输出返回值：
        具有 OpenAI chat completion 属性层次的 SimpleNamespace。
    """

    function = SimpleNamespace(name=name, arguments=arguments)
    tool_call = SimpleNamespace(function=function)
    message = SimpleNamespace(tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_model_lazily_resolves_secret_and_returns_structured_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证构造阶段不取 key，首个请求才按 env 引用创建客户端。

    输入参数：
        monkeypatch：pytest 环境变量隔离工具。
    输出返回值：
        无；key 仅传给 client factory，模型结果为结构化 click 动作。
    """

    secret = "-".join(("synthetic", "secret", "value"))
    env_name = "PARAGUIBENCH_TEST_API_KEY"
    monkeypatch.setenv(env_name, secret)
    completions = _Completions(
        _tool_response("click", '{"point":"<point>500 500</point>"}')
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    factory_calls: list[dict[str, str]] = []

    def client_factory(**kwargs: str) -> Any:
        """记录 client 初始化参数并返回 fake。

        输入参数：
            kwargs：只应包含 api_key 与 base_url。
        输出返回值：
            合成 OpenAI-compatible client。
        """

        factory_calls.append(dict(kwargs))
        return client

    model = Seed18OpenAIModel(
        Seed18ModelConfig(
            model="synthetic-model",
            api_key_env=env_name,
            base_url="https://api.example.test/v1",
        ),
        client_factory=client_factory,
    )

    assert factory_calls == []
    action = model.next_action(
        instruction="Inspect the shared folder.",
        screenshot=b"\x89PNG\r\n\x1a\nsynthetic",
        step_index=1,
        action_history=(),
    )

    assert action.name == "click"
    assert action.parameters == {"point": "<point>500 500</point>"}
    assert factory_calls == [
        {
            "api_key": secret,
            "base_url": "https://api.example.test/v1",
        }
    ]
    assert completions.requests[0]["tool_choice"] == "required"
    assert completions.requests[0]["parallel_tool_calls"] is False
    request_text = repr(completions.requests[0])
    assert secret not in request_text
    assert "Inspect the shared folder." in request_text


def test_model_contract_error_does_not_repeat_raw_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证畸形工具参数报错不回显可能含敏感内容的原始响应。

    输入参数：
        monkeypatch：pytest 环境变量隔离工具。
    输出返回值：
        无；错误只描述契约类型，不包含模型原始 arguments。
    """

    env_name = "PARAGUIBENCH_TEST_API_KEY"
    monkeypatch.setenv(env_name, "synthetic-secret-value")
    raw_private_fragment = "private-response-fragment"
    completions = _Completions(
        _tool_response("type", raw_private_fragment)
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    model = Seed18OpenAIModel(
        Seed18ModelConfig(
            model="synthetic-model",
            api_key_env=env_name,
            base_url="https://api.example.test/v1",
        ),
        client_factory=lambda **_: client,
    )

    with pytest.raises(Seed18ModelError) as raised:
        model.next_action(
            instruction="Inspect the shared folder.",
            screenshot=b"\x89PNG\r\n\x1a\nsynthetic",
            step_index=1,
            action_history=(),
        )

    assert raw_private_fragment not in str(raised.value)


def test_prompt_requires_one_concise_visible_filename_for_file_identity_tasks() -> None:
    """验证文件识别任务不会把文件名、标题和解释拼成一个 exact 答案。

    输入参数：
        无；构造一个合成首步多模态消息。
    输出返回值：
        无；system policy 明确要求优先返回单个可见文件名或 stem。
    """

    messages = build_step_messages(
        instruction="Which paper matches the diagram?",
        screenshot=b"\x89PNG\r\n\x1a\nsynthetic",
        media_type="image/png",
        step_index=1,
        action_history=(),
    )

    assert "single visible filename or filename stem" in messages[0]["content"]
