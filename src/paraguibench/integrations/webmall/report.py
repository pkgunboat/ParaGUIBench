"""把 Agent 最终文本中的 WebMall runtime URL 转成脱敏 logical URL。"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from paraguibench.integrations.webmall.registry import (
    WebMallURLRegistry,
    WebMallURLRegistryError,
)

INVALID_REPORTED_LOGICAL_URL = "invalid://reported-url"
_MAX_REPORT_LENGTH = 1_000_000
_URL_PATTERN = re.compile(r"(?:https?|webmall)://[^\s<>\"']+", re.IGNORECASE)


def extract_reported_logical_product_urls(
    final_output: str,
    registry: WebMallURLRegistry,
) -> tuple[str, ...]:
    """提取报告 URL、保留多集合并移除部署 origin 与未知值。

    输入参数：
        final_output：Agent 最终文本；允许 ``###``、空白或说明文字包围 URL。
        registry：本 Attempt 固定的 logical store/runtime origin 注册表。
    输出返回值：
        按报告出现顺序排列的 logical URL 元组；重复项完整保留。未知
        origin/store 只产生固定非法标记，不把原 URL、query 或 endpoint 交给
        evaluator 结果。
    异常：
        TypeError：输入不是字符串或 registry 类型无效。
    """

    if not isinstance(final_output, str):
        raise TypeError("WebMall final output 必须是字符串")
    if not isinstance(registry, WebMallURLRegistry):
        raise TypeError("WebMall report parser 需要 URL registry")
    if len(final_output) > _MAX_REPORT_LENGTH:
        return (INVALID_REPORTED_LOGICAL_URL,)

    # 先按任务规定的分隔符切开，避免 URL 正则把 ``###`` 误当 fragment。
    raw_urls = tuple(
        match.group(0)
        for segment in final_output.split("###")
        for match in _URL_PATTERN.finditer(segment)
    )
    logical_urls: list[str] = []
    for raw_url in raw_urls:
        try:
            scheme = urlsplit(raw_url).scheme.lower()
            if scheme == "webmall":
                registry.materialize_url(raw_url)
                logical_url = raw_url
            else:
                logical_url = registry.canonicalize_url(raw_url)
        except (ValueError, WebMallURLRegistryError):
            logical_urls.append(INVALID_REPORTED_LOGICAL_URL)
            continue
        logical_urls.append(logical_url)
    return tuple(logical_urls)
