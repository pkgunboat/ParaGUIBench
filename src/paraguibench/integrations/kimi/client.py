"""创建 Kimi planner 使用的 OpenAI-compatible SDK client。"""

from __future__ import annotations

from typing import Any


def create_openai_compatible_kimi_client(
    *,
    api_key: str,
    base_url: str,
    timeout: float,
    max_retries: int,
) -> Any:
    """延迟导入 OpenAI SDK 并建立有界 Kimi client。

    输入参数：
        api_key：调用进程从指定环境变量读取的 secret。
        base_url：Agent 层已校验的模型 endpoint；公网 HTTPS，
            回环地址允许 HTTP。
        timeout：单次 SDK 请求超时秒数。
        max_retries：SDK 对临时网络错误的自动重试次数。
    输出返回值：
        OpenAI-compatible client；本 integration 不记录请求、响应或凭据。
    """

    from openai import OpenAI

    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
    )
