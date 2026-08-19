"""LLM 确定性调用约束。

为了保证消融实验结果可复现，所有 LLM API 调用必须使用：
- temperature = 0.0
- seed = 42

此模块导出常量与断言函数，任何包装 API 调用的代码路径都应：
    from parallel_benchmark.utils.llm_determinism import (
        LLM_TEMPERATURE, LLM_SEED, assert_deterministic,
    )
    kwargs = dict(model=..., messages=..., temperature=LLM_TEMPERATURE, seed=LLM_SEED, ...)
    assert_deterministic(kwargs)
    client.chat.completions.create(**kwargs)

对于不支持 seed 参数的 SDK（如 volcenginesdkarkruntime.Ark），
请将调用切换到 OpenAI 兼容端点（client = openai.OpenAI(base_url=...)）。
"""

from __future__ import annotations

import os
import time
from typing import Callable, Mapping, TypeVar

# 全局确定性参数
LLM_TEMPERATURE: float = 0.0
LLM_SEED: int = 42
_ResponseT = TypeVar("_ResponseT")


def assert_deterministic(kwargs: Mapping[str, object]) -> None:
    """校验 API 调用 kwargs 是否满足确定性约束。

    输入：
        kwargs: 传给 client.chat.completions.create / responses.create 的参数字典

    输出：
        无返回值。若校验失败抛出 AssertionError。

    说明：
        - 必须显式传入 ``temperature``，且值等于 ``LLM_TEMPERATURE``（0.0）
        - 必须显式传入 ``seed``，且值等于 ``LLM_SEED``（42）
        - Responses API 的 SDK 方法不接受顶层 ``seed`` 参数时，允许通过
          ``extra_body={"seed": LLM_SEED}`` 发送到底层请求体
    """
    assert "temperature" in kwargs, (
        "LLM call missing 'temperature'; expected temperature=LLM_TEMPERATURE (0.0)"
    )
    actual_temp = kwargs["temperature"]
    assert actual_temp == LLM_TEMPERATURE, (
        f"LLM call temperature={actual_temp!r}; expected LLM_TEMPERATURE={LLM_TEMPERATURE!r}"
    )
    has_top_level_seed = "seed" in kwargs
    extra_body = kwargs.get("extra_body")
    has_extra_body_seed = isinstance(extra_body, Mapping) and "seed" in extra_body
    assert has_top_level_seed or has_extra_body_seed, (
        "LLM call missing 'seed'; expected seed=LLM_SEED (42)"
    )
    actual_seed = kwargs["seed"] if has_top_level_seed else extra_body["seed"]
    assert actual_seed == LLM_SEED, (
        f"LLM call seed={actual_seed!r}; expected LLM_SEED={LLM_SEED!r}"
    )


def assert_seeded(kwargs: Mapping[str, object]) -> None:
    """校验调用仍保留固定 seed。

    某些 OpenAI-compatible 网关会在部分请求上拒绝 ``temperature`` 参数。
    这类请求会先按 ``temperature=0.0`` 发起；只有服务端明确返回
    unsupported temperature 时，调用方才会移除 temperature 后重试。
    重试路径仍必须带 ``seed=42``。
    """
    has_top_level_seed = "seed" in kwargs
    extra_body = kwargs.get("extra_body")
    has_extra_body_seed = isinstance(extra_body, Mapping) and "seed" in extra_body
    assert has_top_level_seed or has_extra_body_seed, (
        "LLM call missing 'seed'; expected seed=LLM_SEED (42)"
    )
    actual_seed = kwargs["seed"] if has_top_level_seed else extra_body["seed"]
    assert actual_seed == LLM_SEED, (
        f"LLM call seed={actual_seed!r}; expected LLM_SEED={LLM_SEED!r}"
    )


def is_unsupported_temperature_error(exc: BaseException) -> bool:
    """判断异常是否为服务端拒绝 temperature 参数。"""
    text = str(exc).lower()
    return "unsupported parameter" in text and "temperature" in text


def strip_temperature_for_retry(kwargs: Mapping[str, object]) -> dict:
    """复制 kwargs 并移除 temperature，用于 unsupported temperature 重试。"""
    retry_kwargs = dict(kwargs)
    retry_kwargs.pop("temperature", None)
    return retry_kwargs


def is_transient_llm_error(exc: BaseException) -> bool:
    """判断异常是否适合按固定退避重试。"""
    response = getattr(exc, "response", None)
    status_code = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
    if status_code in {408, 409, 429, 500, 502, 503, 504}:
        return True

    text = str(exc).lower()
    transient_markers = (
        "rate limit",
        "too many requests",
        "upstream rate limit",
        "temporarily unavailable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "read timeout",
        "connection error",
    )
    return any(marker in text for marker in transient_markers)


def create_with_transient_retries(
    create_fn: Callable[..., _ResponseT],
    request_kwargs: Mapping[str, object],
    *,
    label: str = "LLM API",
) -> _ResponseT:
    """调用 OpenAI-compatible create，并对 429/5xx 做有限固定退避重试。"""
    attempts_text = os.getenv("LLM_TRANSIENT_RETRY_ATTEMPTS", "8")
    try:
        attempts = max(1, int(attempts_text))
    except ValueError:
        attempts = 8

    for attempt in range(1, attempts + 1):
        try:
            return create_fn(**request_kwargs)
        except Exception as exc:
            if attempt >= attempts or not is_transient_llm_error(exc):
                raise
            delay = min(60, 5 * (2 ** (attempt - 1)))
            print(
                f"[WARN] {label} transient error on attempt {attempt}/{attempts}: "
                f"{exc}; retrying in {delay}s"
            )
            time.sleep(delay)

    raise RuntimeError("unreachable retry state")


__all__ = [
    "LLM_TEMPERATURE",
    "LLM_SEED",
    "assert_deterministic",
    "assert_seeded",
    "create_with_transient_retries",
    "is_unsupported_temperature_error",
    "is_transient_llm_error",
    "strip_temperature_for_retry",
]
