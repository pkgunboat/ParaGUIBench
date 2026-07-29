"""结构化 QA 答案的保守解析与一对一比较工具。"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from paraguibench.evaluation.answers.numeric import (
    IntervalPolicy,
    interval_prediction_matches,
    parse_numeric_literal,
)

_ASSERTION_WORD_PATTERNS = (
    r"\bno\b",
    r"\bnot\b",
    r"\bwithout\b",
    r"\bneither\b",
    r"\bnor\b",
    r"\beither\b",
    r"\binstead(?:\s+of)?\b",
    r"\bversus\b",
    r"\bvs\.?\b",
    r"\band\b",
    r"\bor\b",
    r"\bplus\b",
    r"\bmaybe\b",
    r"\bperhaps\b",
    r"\bapproximately\b",
    r"\bapprox\.?\b",
    r"\babout\b",
    r"\baround\b",
    r"\bbetween\b",
    r"\bto\b",
    r"\bextra\b",
)
_ASSERTION_SYMBOLS = (";", "/", "&", "?", "±", "~", "<", ">")


def split_semicolon_items(text: str | None) -> tuple[str, ...] | None:
    """把至少两个非空分号分项解析为结构化答案。

    输入参数：
        text：待解析的 canonical 答案；允许一个常见的末尾分号。
    输出返回值：
        至少两个非空项时返回保持顺序的元组，否则返回 ``None``。中间出现空项
        时失败，避免静默吞掉缺失字段。
    """

    candidate = str(text or "").strip()
    if candidate.endswith(";"):
        candidate = candidate[:-1].rstrip()
    if not candidate:
        return None
    raw_parts = candidate.split(";")
    if len(raw_parts) < 2 or any(not part.strip() for part in raw_parts):
        return None
    return tuple(part.strip() for part in raw_parts)


def parse_keyed_numeric_set(
    text: str | None,
) -> dict[str, tuple[int, ...]] | None:
    """解析 ``key:n,n;key:n`` 形式的键控无序整数集合。

    输入参数：
        text：待解析的完整答案文本。
    输出返回值：
        格式合法时返回规范化键到已排序整数元组的映射；空分项、重复键、
        负数、重复值或其他文本均返回 ``None``。键和分项顺序不影响语义。
    """

    candidate = str(text or "").strip()
    if not candidate:
        return None
    parts = candidate.split(";")
    if any(not part.strip() for part in parts):
        return None
    parsed: dict[str, tuple[int, ...]] = {}
    for part in parts:
        match = re.fullmatch(
            r"\s*([^:;]+?)\s*:\s*(\d+(?:\s*,\s*\d+)*)\s*",
            part,
        )
        if match is None:
            return None
        key = match.group(1).strip().casefold()
        if not key or key in parsed:
            return None
        values = [int(item.strip()) for item in match.group(2).split(",")]
        if len(values) != len(set(values)):
            return None
        parsed[key] = tuple(sorted(values))
    return parsed


def maximum_one_to_one_matches(
    reference_items: Sequence[str],
    prediction_items: Sequence[str],
    matcher: Callable[[str, str], bool],
) -> int:
    """计算参考项与预测项之间的一对一最大匹配数。

    输入参数：
        reference_items：每项最多被消费一次的参考列表。
        prediction_items：每项最多产生一次命中的预测列表。
        matcher：判断一对完整分项是否满足任务允许的窄等价。
    输出返回值：
        二分图最大匹配数。重复预测不能重复消费同一个参考项。
    """

    matched_prediction_by_reference: dict[int, int] = {}

    def augment(
        prediction_index: int,
        seen_references: set[int],
    ) -> bool:
        """为一个预测项寻找可用的一对一增广路径。

        输入参数：
            prediction_index：当前待匹配的预测项下标。
            seen_references：本次搜索已访问的参考项下标，防止循环。
        输出返回值：
            能新增或重排出一个匹配时返回 ``True``，否则返回 ``False``。
        """

        for reference_index, reference_item in enumerate(reference_items):
            if reference_index in seen_references:
                continue
            if not matcher(
                reference_item,
                prediction_items[prediction_index],
            ):
                continue
            seen_references.add(reference_index)
            previous_prediction = matched_prediction_by_reference.get(
                reference_index
            )
            if previous_prediction is None or augment(
                previous_prediction,
                seen_references,
            ):
                matched_prediction_by_reference[reference_index] = (
                    prediction_index
                )
                return True
        return False

    return sum(
        augment(prediction_index, set())
        for prediction_index in range(len(prediction_items))
    )


def split_structured_prediction(
    prediction: str,
    reference_items: Sequence[str],
) -> tuple[tuple[str, ...], int]:
    """按参考实体感知规则拆分结构化预测。

    输入参数：
        prediction：canonical 模型答案。
        reference_items：当前候选的完整参考分项。
    输出返回值：
        ``(预测分项, or 对冲冲突数)``。先按分号拆分；完整参考实体中的
        ``and`` 保持整体，其他片段再按 ``and/or/&`` 拆开；每个 ``or`` 额外
        计入 precision 分母，使二选一不能冒充确定列表。
    """

    primary_parts = tuple(
        part.strip()
        for part in prediction.split(";")
        if part.strip()
    )
    normalized_references = {
        item.strip().casefold() for item in reference_items
    }
    result: list[str] = []
    disjunction_conflicts = 0
    for part in primary_parts:
        if part.casefold() in normalized_references:
            result.append(part)
            continue
        disjunction_conflicts += len(
            re.findall(r"\bor\b", part, flags=re.IGNORECASE)
        )
        split_parts = tuple(
            item.strip()
            for item in re.split(
                r"\s+(?:and|or)\s+|\s*&\s*",
                part,
                flags=re.IGNORECASE,
            )
            if item.strip()
        )
        result.extend(split_parts or (part,))
    return tuple(result), disjunction_conflicts


def narrow_item_matches(
    reference: str,
    prediction: str,
    interval_policy: IntervalPolicy | None = None,
) -> bool:
    """判断两个完整结构化分项是否满足保守窄等价。

    输入参数：
        reference：一个 canonical 参考分项。
        prediction：一个 canonical 预测分项。
        interval_policy：任务显式声明的窄误差区间；缺省时完全禁用区间。
    输出返回值：
        完全相同，或仅存在规则英文单复数、等值十进制表示时返回 ``True``。
        新增否定、候选连接、近似、范围、问号、误差棒、额外 token 或 KV 键
        变化均返回 ``False``。
    """

    reference_text = reference.strip().casefold()
    prediction_text = prediction.strip().casefold()
    if reference_text == prediction_text:
        return True
    if (
        interval_policy is not None
        and interval_prediction_matches(
            reference_text,
            prediction_text,
            interval_policy,
        )
    ):
        return True
    supported_parenthetical = _remove_supported_parenthetical_alias(
        reference_text,
        prediction_text,
    )
    if supported_parenthetical == reference_text:
        return True
    if has_unexpected_assertion_markers(
        reference_text,
        prediction_text,
    ):
        return False
    if any(
        marker in reference_text or marker in prediction_text
        for marker in ("(", ")", "[", "]", "（", "）")
    ):
        return False

    if ":" in reference_text or ":" in prediction_text:
        reference_kv = _parse_single_key_value(reference_text)
        prediction_kv = _parse_single_key_value(prediction_text)
        if reference_kv is None or prediction_kv is None:
            return False
        if reference_kv[0] != prediction_kv[0]:
            return False
        return _narrow_value_matches(reference_kv[1], prediction_kv[1])
    return _narrow_value_matches(reference_text, prediction_text)


def narrow_single_value_matches(
    reference: str,
    prediction: str,
) -> bool:
    """比较 legacy 单值答案的有限安全兼容形式。

    输入参数：
        reference：canonical 单值参考答案。
        prediction：canonical 模型最终答案。
    输出返回值：
        完整窄等价，或仅增加 ``The/Final answer is/:`` 固定前缀且内部仍窄等价
        时返回 ``True``。函数不执行任意 contains，也不接受否定、候选或超集。
    """

    if narrow_item_matches(reference, prediction):
        return True
    wrapped = re.fullmatch(
        r"(?:the\s+)?(?:final\s+)?answer\s*(?:is\s+|:\s*)(.+)",
        prediction,
        flags=re.IGNORECASE,
    )
    return bool(
        wrapped
        and narrow_item_matches(reference, wrapped.group(1).strip())
    )


def has_unexpected_assertion_markers(
    reference: str,
    prediction: str,
) -> bool:
    """检测预测相对参考新增的冲突断言标记。

    输入参数：
        reference：canonical 参考分项。
        prediction：canonical 预测分项。
    输出返回值：
        预测新增否定、候选连接、范围、近似或不确定性标记时返回 ``True``。
        参考实体自身已有的 ``and``、斜线等不会被误判。
    """

    for pattern in _ASSERTION_WORD_PATTERNS:
        if len(re.findall(pattern, prediction)) > len(
            re.findall(pattern, reference)
        ):
            return True
    return any(
        prediction.count(symbol) > reference.count(symbol)
        for symbol in _ASSERTION_SYMBOLS
    )


def occurs_as_complete_span(reference: str, prediction: str) -> bool:
    """判断完整参考串是否以 Unicode 词边界出现在预测中。

    输入参数：
        reference：canonical 单值参考答案。
        prediction：canonical 模型答案。
    输出返回值：
        参考非空且左右不紧邻字母、数字或下划线时返回 ``True``。本函数只供
        失败样本的低置信诊断计分使用，不能作为通过条件。
    """

    candidate = reference.strip().casefold()
    if not candidate:
        return False
    return bool(
        re.search(
            rf"(?<!\w){re.escape(candidate)}(?!\w)",
            prediction.casefold(),
            flags=re.UNICODE,
        )
    )


def _parse_single_key_value(text: str) -> tuple[str, str] | None:
    """解析且只解析一个完整 ``key:value`` 记录。

    输入参数：
        text：待解析的单个结构化分项。
    输出返回值：
        键和值都非空且不含第二字段时返回二元组，否则返回 ``None``。
    """

    match = re.fullmatch(r"\s*([^:;]+?)\s*:\s*([^:;]+?)\s*", text)
    if match is None:
        return None
    key = match.group(1).strip()
    value = match.group(2).strip()
    if not key or not value:
        return None
    return key, value


def _remove_supported_parenthetical_alias(
    reference: str,
    prediction: str,
) -> str | None:
    """仅移除能由参考姓名词元支持的括号昵称。

    输入参数：
        reference：不含待删除昵称的 canonical 参考答案。
        prediction：可能含圆括号、方括号或中文圆括号昵称的预测。
    输出返回值：
        每个括号 token 长度至少为 2、且是某个参考词元前缀，并且删除括号后
        与参考完全相同时，返回清理后的预测；否则返回 ``None``。
    """

    contents: list[str] = []
    for pattern in (r"\(([^)]*)\)", r"\[([^\]]*)\]", r"（([^）]*)）"):
        contents.extend(re.findall(pattern, prediction))
    if not contents:
        return None
    cleaned = re.sub(r"\([^)]*\)|\[[^\]]*\]|（[^）]*）", "", prediction)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned != reference:
        return None
    reference_tokens = re.findall(
        r"[^\W_]+",
        reference,
        flags=re.UNICODE,
    )
    if not reference_tokens:
        return None
    for content in contents:
        content_tokens = re.findall(
            r"[^\W_]+",
            content,
            flags=re.UNICODE,
        )
        if not content_tokens:
            return None
        for token in content_tokens:
            if len(token) < 2 or not any(
                reference_token.startswith(token)
                for reference_token in reference_tokens
            ):
                return None
    return cleaned


def _narrow_value_matches(reference: str, prediction: str) -> bool:
    """比较 KV value 或普通分项的 token 级窄等价。

    输入参数：
        reference：参考值或分项文本。
        prediction：预测值或分项文本。
    输出返回值：
        token 数量和顺序一致，且每对 token 完全相同、十进制数值相等或为规则
        英文单复数时返回 ``True``。
    """

    reference_number = parse_numeric_literal(reference)
    prediction_number = parse_numeric_literal(prediction)
    if reference_number is not None or prediction_number is not None:
        return (
            reference_number is not None
            and prediction_number is not None
            and reference_number == prediction_number
        )
    reference_tokens = re.findall(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)|[^\W\d_]+",
        reference,
        flags=re.UNICODE,
    )
    prediction_tokens = re.findall(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)|[^\W\d_]+",
        prediction,
        flags=re.UNICODE,
    )
    if not reference_tokens or len(reference_tokens) != len(prediction_tokens):
        return False
    return all(
        _token_matches(reference_token, prediction_token)
        for reference_token, prediction_token in zip(
            reference_tokens,
            prediction_tokens,
            strict=True,
        )
    )


def _token_matches(reference: str, prediction: str) -> bool:
    """判断一对 token 是否完全相等、数值相等或为规则单复数。

    输入参数：
        reference：参考 token。
        prediction：预测 token。
    输出返回值：
        满足上述任一窄等价时返回 ``True``；共享前缀但不同词返回 ``False``。
    """

    if reference == prediction:
        return True
    reference_number = parse_numeric_literal(reference)
    prediction_number = parse_numeric_literal(prediction)
    if reference_number is not None or prediction_number is not None:
        return (
            reference_number is not None
            and prediction_number is not None
            and reference_number == prediction_number
        )
    if (
        not reference.isascii()
        or not prediction.isascii()
        or not reference.isalpha()
        or not prediction.isalpha()
        or abs(len(reference) - len(prediction)) > 2
    ):
        return False
    return _simple_singular_token(reference) == _simple_singular_token(
        prediction
    )


def _simple_singular_token(token: str) -> str:
    """把常见英文规则复数转换为保守单数候选。

    输入参数：
        token：小写 ASCII 英文字母 token。
    输出返回值：
        识别 ``-s``、``-es`` 或 ``-ies`` 时返回单数候选，否则原样返回。
    """

    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(
        ("ches", "shes", "xes", "zes", "ses")
    ):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token
