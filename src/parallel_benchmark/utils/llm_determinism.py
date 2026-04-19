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

from typing import Mapping

# 全局确定性参数
LLM_TEMPERATURE: float = 0.0
LLM_SEED: int = 42


def assert_deterministic(kwargs: Mapping[str, object]) -> None:
    """校验 API 调用 kwargs 是否满足确定性约束。

    输入：
        kwargs: 传给 client.chat.completions.create / responses.create 的参数字典

    输出：
        无返回值。若校验失败抛出 AssertionError。

    说明：
        - 必须显式传入 ``temperature``，且值等于 ``LLM_TEMPERATURE``（0.0）
        - 必须显式传入 ``seed``，且值等于 ``LLM_SEED``（42）
    """
    assert "temperature" in kwargs, (
        "LLM call missing 'temperature'; expected temperature=LLM_TEMPERATURE (0.0)"
    )
    actual_temp = kwargs["temperature"]
    assert actual_temp == LLM_TEMPERATURE, (
        f"LLM call temperature={actual_temp!r}; expected LLM_TEMPERATURE={LLM_TEMPERATURE!r}"
    )
    assert "seed" in kwargs, (
        "LLM call missing 'seed'; expected seed=LLM_SEED (42)"
    )
    actual_seed = kwargs["seed"]
    assert actual_seed == LLM_SEED, (
        f"LLM call seed={actual_seed!r}; expected LLM_SEED={LLM_SEED!r}"
    )


__all__ = ["LLM_TEMPERATURE", "LLM_SEED", "assert_deterministic"]
