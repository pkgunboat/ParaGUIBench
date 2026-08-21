"""WebMall 评价器共享的 URL 与商品身份规则。

该模块仅依赖 Python 标准库，集中解决三个评价入口曾经各自实现的
URL 归一化与商品 slug 判定，避免 String/Cart/Checkout 对同一答案
产生不一致结果。
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from typing import Dict, List, Sequence, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit


def deep_html_unescape(text: str) -> str:
    """循环解码可能被多层转义的 HTML 文本。

    功能：重复调用 ``html.unescape`` 直到内容稳定，用于处理 AT 中的
    ``&amp;amp;`` 等多重编码。
    输入参数：text 为原始商品名或 URL 文本。
    输出返回值：完全解码后的字符串。
    """
    current = str(text)
    previous = None
    while current != previous:
        previous = current
        current = html.unescape(current)
    return current


def product_identity_tokens(value: str) -> Tuple[str, ...]:
    """将商品名或 slug 转换为可比较的词元序列。

    功能：深度解码 HTML，将 ``&`` 显式表示为 ``amp``，保留数字型号与
    词元顺序，并将标点、空格和连字符统一为分隔符。
    输入参数：value 为 WooCommerce slug 或 AT 中的商品名。
    输出返回值：小写、顺序敏感的商品身份词元元组。
    """
    text = deep_html_unescape(unquote(str(value))).casefold()
    text = unicodedata.normalize("NFKC", text).replace("&", " amp ")
    return tuple(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def product_identity_matches(expected_slug: str, observed_value: str) -> bool:
    """严格比较期望 slug 与观测商品身份。

    功能：要求全部词元及数字型号完全相等；唯一允许的兼容差异是
    WooCommerce 对 ``&`` 可能保留 ``amp`` 词元或直接丢弃。不使用前缀、
    子串或模糊匹配，因此额外的数字型号后缀会失败。
    输入参数：expected_slug 为任务 gold URL 的 slug；observed_value 为实际 slug 或商品名。
    输出返回值：两者代表同一完整商品时返回 True。
    """
    expected = product_identity_tokens(expected_slug)
    observed = product_identity_tokens(observed_value)
    if not expected or not observed:
        return False
    if expected == observed:
        return True
    expected_without_amp = tuple(token for token in expected if token != "amp")
    observed_without_amp = tuple(token for token in observed if token != "amp")
    return expected_without_amp == observed_without_amp


def slugify_product_name(name: str, *, keep_amp: bool = True) -> str:
    """将 AT 商品名转换为稳定的调试 slug。

    功能：复用共享词元规则生成连字符 slug；``keep_amp=False``
    可用于兼容会丢弃 ``&`` 的历史 WooCommerce 数据。
    输入参数：name 为商品显示名；keep_amp 控制是否保留 amp 词元。
    输出返回值：以连字符分隔的小写 slug。
    """
    tokens = product_identity_tokens(name)
    if not keep_amp:
        tokens = tuple(token for token in tokens if token != "amp")
    return "-".join(tokens)


def normalize_http_url(url: str) -> str:
    """将 HTTP(S) URL 归一化为保留主机身份的比较键。

    功能：仅移除完整的 http/https scheme 差异，小写 hostname，规范默认
    端口、尾随斜杠及 query 顺序；绝不使用 ``lstrip`` 字符集语义。
    输入参数：url 为原始提交或期望 URL。
    输出返回值：可做精确比较的 ``host[:port]/path?query#fragment`` 字符串；
    不可解析时返回去除首尾空白的原文，以便失败关闭。
    """
    candidate = deep_html_unescape(str(url)).strip()
    if not candidate:
        return ""
    parse_candidate = candidate if "://" in candidate else f"http://{candidate}"
    try:
        parsed = urlsplit(parse_candidate)
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return candidate
    if parsed.scheme.casefold() not in {"http", "https"} or not host:
        return candidate
    if parsed.username is not None or parsed.password is not None:
        return candidate

    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    authority = host if port in (None, default_port) else f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    normalized = f"{authority}{path}"
    if query:
        normalized += f"?{query}"
    if parsed.fragment:
        normalized += f"#{parsed.fragment}"
    return normalized


def compare_url_lists(
    expected_urls: Sequence[str],
    submitted_urls: Sequence[str],
) -> Dict[str, List[str]]:
    """对期望 URL 与提交 URL 执行一对一精确多集合比较。

    功能：使用安全 URL 比较键计数，每个期望项最多消费一个提交项；
    额外、重复或错主机 URL 进入 wrong，未覆盖的 gold 进入 missing。
    输入参数：expected_urls 为 gold URL 序列；submitted_urls 为 Agent 提交序列。
    输出返回值：包含 matched、wrong 和 missing 原始 URL 列表的字典。
    """
    remaining = Counter(normalize_http_url(url) for url in expected_urls)
    expected_by_key: Dict[str, List[str]] = {}
    for url in expected_urls:
        expected_by_key.setdefault(normalize_http_url(url), []).append(url)

    matched: List[str] = []
    wrong: List[str] = []
    for submitted in submitted_urls:
        key = normalize_http_url(submitted)
        if key and remaining[key] > 0:
            remaining[key] -= 1
            matched.append(submitted)
        else:
            wrong.append(submitted)

    missing: List[str] = []
    for key, count in remaining.items():
        if count > 0:
            missing.extend(expected_by_key.get(key, [])[:count])
    return {"matched": matched, "wrong": wrong, "missing": missing}
