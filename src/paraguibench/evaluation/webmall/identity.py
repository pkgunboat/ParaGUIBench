"""WebMall 商品名与 logical URL slug 的共享严格身份规则。"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote


def deep_html_unescape(text: str) -> str:
    """循环解码可能被多层转义的 WebMall 文本。

    输入参数：
        text：AT 商品名或 logical URL slug 中的原始字符串。
    输出返回值：
        重复调用 ``html.unescape`` 直到稳定后的文本。
    """

    current = str(text)
    previous: str | None = None
    while current != previous:
        previous = current
        current = html.unescape(current)
    return current


def product_identity_tokens(value: str) -> tuple[str, ...]:
    """把 URL slug 或商品显示名转换为顺序敏感的完整身份词元。

    输入参数：
        value：percent/HTML 编码的 slug，或 AT 中的完整商品显示名。
    输出返回值：
        NFKC/casefold 后的 Unicode 字母数字词元；``&`` 显式成为 ``amp``，
        数字型号和全部词元顺序均保留。
    """

    try:
        decoded = unquote(str(value), encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return ()
    text = deep_html_unescape(decoded)
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("&", " amp ")
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def product_identity_matches(expected_slug: str, observed_label: str) -> bool:
    """严格比较期望 slug 与观测商品名，仅兼容 ``&``/``amp`` 差异。

    输入参数：
        expected_slug：canonical logical product URL 的完整 slug。
        observed_label：订单证据中的完整商品显示名或等价 slug。
    输出返回值：
        全部词元和数字型号一致时返回 ``True``；唯一兼容例外是 WebMall
        历史数据可能保留或丢弃独立 ``amp`` 词元。
    """

    expected = product_identity_tokens(expected_slug)
    observed = product_identity_tokens(observed_label)
    if not expected or not observed:
        return False
    if expected == observed:
        return True
    return tuple(token for token in expected if token != "amp") == tuple(
        token for token in observed if token != "amp"
    )
