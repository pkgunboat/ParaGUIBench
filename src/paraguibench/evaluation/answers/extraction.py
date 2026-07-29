"""QA 评价链共享的最终答案标签提取工具。"""

from __future__ import annotations

import re

_ANSWER_TAG_PATTERN = re.compile(
    r"<answer>(.*?)</answer>",
    flags=re.DOTALL | re.IGNORECASE,
)


def extract_last_complete_answer(text: str | None) -> str | None:
    """提取模型输出中最后一个完整的 ``<answer>`` 标签。

    输入参数：
        text：可能包含零个或多个答案标签的模型输出；``None`` 视为空。
    输出返回值：
        存在完整标签时返回最后一项去除首尾空白后的内容；不存在时返回
        ``None``。最后一个完整空标签返回空字符串，末尾未闭合标签不会覆盖它。
    """

    if not text:
        return None
    matches = _ANSWER_TAG_PATTERN.findall(str(text))
    if not matches:
        return None
    return matches[-1].strip()


def is_abstention_answer(text: str | None) -> bool:
    """判断最终选中答案是否完整等于弃答哨兵。

    输入参数：
        text：最后标签内容或无标签时的完整最终输出。
    输出返回值：
        统一空格与下划线并允许一对方括号后，整串为
        ``INSUFFICIENT_EVIDENCE`` 时返回 ``True``。历史叙述中的子串不会触发。
    """

    normalized = re.sub(
        r"[_\s]+",
        " ",
        str(text or "").strip().casefold(),
    )
    return bool(re.fullmatch(r"\[?insufficient evidence\]?", normalized))
