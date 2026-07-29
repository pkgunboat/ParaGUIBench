"""QA 答案的保守 canonical 归一化。"""

from __future__ import annotations

import re

_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u00ab": '"',
        "\u00bb": '"',
        "\u2039": "'",
        "\u203a": "'",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
    }
)


def canonical_exact_answer(text: str | None) -> str:
    """生成保留文件扩展名与答案结构的 exact canonical 文本。

    输入参数：
        text：gold、别名或模型最终答案；``None`` 视为空字符串。
    输出返回值：
        统一大小写、排版引号/横线、一个成对外引号、冒号与结构分隔符空格、
        MeV/GeV 的 ``c²`` 排版和连续空白后的字符串。函数不会删除文件扩展名、
        单位、字段或列表项。
    """

    normalized = str(text or "").strip().casefold()
    normalized = normalized.translate(_PUNCTUATION_TRANSLATION)
    normalized = _strip_one_outer_quote_pair(normalized)
    normalized = re.sub(
        r"\b(mev|gev)\s*/\s*c(?:\s*\^\s*2|²|2)(?!\w)",
        lambda match: f"{match.group(1)}/c2",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s*:\s*=\s*", ":", normalized)
    normalized = re.sub(r"\s*([;,:/])\s*", r"\1", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _strip_one_outer_quote_pair(text: str) -> str:
    """只剥离一层成对外引号并保留答案内部引号。

    输入参数：
        text：已完成 Unicode 标点映射的候选答案。
    输出返回值：
        首尾是同类单/双引号时返回内部文本，否则返回去首尾空白的原文。
    """

    candidate = text.strip()
    if (
        len(candidate) >= 2
        and candidate[0] in {"'", '"'}
        and candidate[-1] == candidate[0]
    ):
        return candidate[1:-1].strip()
    return candidate
