"""模型 endpoint 最小防泄露与本地 HTTP 约定测试。"""

from __future__ import annotations

import pytest

from paraguibench.agents.systems.gui_only.seed18.model import Seed18ModelConfig
from paraguibench.agents.systems.paragui.kimi import KimiPlannerConfig
from paraguibench.agents.workers.qwen.model import QwenModelConfig
from paraguibench.integrations.model_endpoint import (
    is_allowed_model_base_url,
    validate_model_base_url,
)


@pytest.mark.parametrize(
    "url",
    (
        "https://api.example.test/v1",
        "http://127.0.0.1:8000/v1",
        "http://localhost/v1",
        "http://[::1]:8000/v1",
        "https://127.0.0.1/v1",
    ),
)
def test_allowed_model_endpoints_accept_https_and_loopback_http(url: str) -> None:
    """验证公网 HTTPS 与回环 HTTP 可通过校验。"""

    assert is_allowed_model_base_url(url) is True
    assert validate_model_base_url(url) == url


@pytest.mark.parametrize(
    "url",
    (
        "http://api.example.test/v1",
        "https://user:pass@api.example.test/v1",
        "https://api.example.test/v1?key=1",
        "https://api.example.test/v1#frag",
        "http://192.0.2.10/v1",
        "ftp://127.0.0.1/v1",
        "http://localhost:notaport/v1",
        "https://api.example.test:70000/v1",
        " http://127.0.0.1:8000/v1",
        "http://127.0.0.1:8000/v1 ",
        "http://127.0.0.1:0/v1",
        "",
    ),
)
def test_disallowed_model_endpoints_are_rejected(url: str) -> None:
    """验证公网 HTTP、userinfo、query/fragment 和非回环地址被拒绝。"""

    assert is_allowed_model_base_url(url) is False
    with pytest.raises(ValueError, match="公网仅允许 HTTPS") as raised:
        validate_model_base_url(url)
    if url:
        assert url not in str(raised.value)


def test_qwen_seed18_kimi_accept_localhost_http() -> None:
    """验证三条生产模型配置都接受本地 HTTP endpoint。"""

    QwenModelConfig(base_url="http://127.0.0.1:8000/v1")
    Seed18ModelConfig(
        model="seed-1.8",
        api_key_env="PARAGUIBENCH_MODEL_API_KEY",
        base_url="http://localhost:8000/v1",
    )
    KimiPlannerConfig(base_url="http://127.0.0.1:8000/v1")


def test_qwen_still_rejects_public_http() -> None:
    """验证公网 HTTP 仍不能构造 Qwen 配置。"""

    with pytest.raises(ValueError, match="公网仅允许 HTTPS"):
        QwenModelConfig(base_url="http://api.example.test/v1")
