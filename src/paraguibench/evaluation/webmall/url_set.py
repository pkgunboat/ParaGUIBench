"""WebMall string 任务的 logical URL 精确集合评价器。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from paraguibench.integrations.webmall import WebMallURLRegistry


@dataclass(frozen=True)
class WebMallURLSetEvaluation:
    """描述一次 WebMall logical URL 集合评价结果。"""

    passed: bool
    score: float
    matched: tuple[str, ...]
    wrong: tuple[str, ...]
    missing: tuple[str, ...]


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

    normalized_expected = [
        _normalize_logical_url(url, registry) for url in expected_urls
    ]
    normalized_submitted = [
        _normalize_logical_url(registry.canonicalize_url(url), registry)
        for url in submitted_runtime_urls
    ]
    expected_set = set(normalized_expected)
    submitted_set = set(normalized_submitted)
    matched = tuple(
        url for url in normalized_submitted if url in expected_set
    )
    wrong = tuple(
        url for url in normalized_submitted if url not in expected_set
    )
    missing = tuple(
        original
        for original, normalized in zip(
            expected_urls, normalized_expected, strict=True
        )
        if normalized not in submitted_set
    )
    score = (
        len(expected_set & submitted_set) / len(expected_set)
        if expected_set
        else 0.0
    )
    passed = bool(expected_set) and submitted_set == expected_set
    return WebMallURLSetEvaluation(
        passed=passed,
        score=score,
        matched=matched,
        wrong=wrong,
        missing=missing,
    )


def _normalize_logical_url(
    logical_url: str,
    registry: WebMallURLRegistry,
) -> str:
    """验证并规范化 logical URL 的可选末尾斜杠。

    输入参数：
        logical_url：待验证的 canonical WebMall URL。
        registry：用于确认 store 已在本次部署中配置的注册表。
    输出返回值：
        保留编码路径、查询和 fragment，仅移除非根路径末尾斜杠的 URL。
    """

    registry.materialize_url(logical_url)
    parts = urlsplit(logical_url)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme, parts.netloc, path, parts.query, parts.fragment)
    )
