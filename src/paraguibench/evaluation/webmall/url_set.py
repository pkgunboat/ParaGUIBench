"""WebMall string 任务的 logical URL 精确集合评价器。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
import html
from urllib.parse import parse_qsl, urlencode, urlsplit

from paraguibench.integrations.webmall.registry import (
    WebMallURLRegistry,
    WebMallURLRegistryError,
)


_INVALID_REPORTED_URL = "invalid://reported-url"
_MAX_QUERY_FIELDS = 1_000
URL_MULTISET_PROTOCOL_ID = "paraguibench.webmall.url-multiset.v1"


@dataclass(frozen=True)
class WebMallURLSetEvaluation:
    """描述一次 WebMall logical URL 集合评价结果。"""

    protocol_id: str
    passed: bool
    score: float
    matched: tuple[str, ...]
    wrong: tuple[str, ...]
    missing: tuple[str, ...]
    precision: float
    recall: float
    f1: float


def evaluate_webmall_url_set(
    expected_urls: Sequence[str],
    submitted_runtime_urls: Sequence[str],
    registry: WebMallURLRegistry,
) -> WebMallURLSetEvaluation:
    """按 store 身份和 URL 路径精确评价 Agent 提交的商品 URL。

    输入参数：
        expected_urls：canonical task 中的 ``webmall://`` logical URL。
        submitted_runtime_urls：Agent 在当前部署中提交的 HTTP(S) URL。
        registry：本次运行的 store ID 与 runtime origin 双向注册表。
    输出返回值：
        包含是否通过、召回得分、正确项、错误项和缺失项的不可变结果。
    异常：
        WebMallURLRegistryError：gold 或提交 URL 引用了未配置的 store/origin；
        调用方应将其记录为 evaluator error，而非普通任务失败。
    """

    if (
        isinstance(expected_urls, (str, bytes))
        or not isinstance(expected_urls, Sequence)
        or isinstance(submitted_runtime_urls, (str, bytes))
        or not isinstance(submitted_runtime_urls, Sequence)
        or not isinstance(registry, WebMallURLRegistry)
        or any(not isinstance(url, str) for url in expected_urls)
        or any(not isinstance(url, str) for url in submitted_runtime_urls)
    ):
        raise TypeError("WebMall URL set evaluator 入参无效")

    expected_items = tuple(
        (
            logical_url,
            _normalize_http_url(registry.materialize_url(logical_url)),
        )
        for logical_url in expected_urls
    )
    if any(not comparison_key for _, comparison_key in expected_items):
        raise WebMallURLRegistryError("WebMall gold URL 无法规范化")

    remaining = Counter(key for _, key in expected_items)
    expected_by_key: dict[str, list[str]] = {}
    for logical_url, comparison_key in expected_items:
        expected_by_key.setdefault(comparison_key, []).append(logical_url)

    matched_items: list[str] = []
    wrong_items: list[str] = []
    for submitted_url in submitted_runtime_urls:
        comparison_key = _submitted_comparison_key(submitted_url, registry)
        if comparison_key and remaining[comparison_key] > 0:
            remaining[comparison_key] -= 1
            matched_index = len(expected_by_key[comparison_key]) - (
                remaining[comparison_key] + 1
            )
            matched_items.append(expected_by_key[comparison_key][matched_index])
        else:
            wrong_items.append(_INVALID_REPORTED_URL)

    missing_items: list[str] = []
    for comparison_key, count in remaining.items():
        if count > 0:
            missing_items.extend(expected_by_key[comparison_key][-count:])

    expected_count = len(expected_items)
    submitted_count = len(submitted_runtime_urls)
    matched_count = len(matched_items)
    recall = matched_count / expected_count if expected_count else 0.0
    precision = matched_count / submitted_count if submitted_count else 0.0
    f1 = (
        2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    )
    passed = bool(expected_count) and not wrong_items and not missing_items
    return WebMallURLSetEvaluation(
        protocol_id=URL_MULTISET_PROTOCOL_ID,
        passed=passed,
        score=recall,
        matched=tuple(matched_items),
        wrong=tuple(wrong_items),
        missing=tuple(missing_items),
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _submitted_comparison_key(
    submitted_url: str,
    registry: WebMallURLRegistry,
) -> str | None:
    """把 runtime 或 logical 提交转换为旧最终版的安全比较键。

    输入参数：
        submitted_url：Agent 报告的 HTTP(S) 或 ``webmall://`` URL。
        registry：用于物化 logical store 的本次部署注册表。
    输出返回值：
        可与物化 gold 比较的键；未知 store 或无效 URL 返回 ``None``，
        由调用方计入安全 wrong 标记而不是 evaluator error。
    """

    candidate = _deep_html_unescape(submitted_url).strip()
    try:
        parts = urlsplit(candidate)
        if parts.scheme.casefold() == "webmall":
            candidate = registry.materialize_url(candidate)
    except (TypeError, ValueError, WebMallURLRegistryError):
        return None
    return _normalize_http_url(candidate)


def _normalize_http_url(url: str) -> str | None:
    """复刻最终旧 evaluator 的 HTTP URL 规范化语义。

    输入参数：
        url：物化 gold 或 Agent 提交 URL。
    输出返回值：
        忽略 HTTP/HTTPS scheme 差异、规范默认端口、尾斜杠、深层
        HTML 转义和 query 顺序的比较键；无效 URL 返回 ``None``。
    """

    candidate = _deep_html_unescape(url).strip()
    if not candidate:
        return None
    parse_candidate = candidate if "://" in candidate else f"http://{candidate}"
    try:
        parts = urlsplit(parse_candidate)
        scheme = parts.scheme.casefold()
        hostname = (parts.hostname or "").casefold().rstrip(".")
        port = parts.port
        query_items = parse_qsl(
            parts.query,
            keep_blank_values=True,
            max_num_fields=_MAX_QUERY_FIELDS,
        )
    except (TypeError, ValueError):
        return None
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None

    default_port = 443 if scheme == "https" else 80
    safe_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = safe_host if port in {None, default_port} else f"{safe_host}:{port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(query_items), doseq=True)
    normalized = f"{authority}{path}"
    if query:
        normalized += f"?{query}"
    if parts.fragment:
        normalized += f"#{parts.fragment}"
    return normalized


def _deep_html_unescape(value: str) -> str:
    """有界地解码旧日志与模型报告中的多层 HTML entity。

    输入参数：
        value：待规范化的 URL 文本。
    输出返回值：
        最多 32 轮后稳定的文本；上限避免异常构造造成无界工作。
    """

    current = value
    for _ in range(32):
        decoded = html.unescape(current)
        if decoded == current:
            break
        current = decoded
    return current
