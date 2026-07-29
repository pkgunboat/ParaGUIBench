"""QA numeric 模式的完整语法解析与精确数值比较。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from collections.abc import Sequence

_NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"


@dataclass(frozen=True)
class IntervalPolicy:
    """描述任务显式允许的窄误差区间。

    字段说明：
        max_relative_width：预测总误差相对中心值的最大比例。
        units：参考与预测必须显式使用的单位白名单。
    """

    max_relative_width: Decimal
    units: tuple[str, ...]


def parse_numeric_literal(text: str | None) -> Decimal | None:
    """把完整十进制数字面量解析为 ``Decimal``。

    输入参数：
        text：待解析文本；可以带正负号，但不能包含单位、范围或其他叙述。
    输出返回值：
        合法完整数字面量返回无精度损失的 ``Decimal``，否则返回 ``None``。
    """

    candidate = str(text or "").strip()
    if not re.fullmatch(_NUMBER_PATTERN, candidate):
        return None
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def numeric_prediction_matches(
    reference: str,
    prediction: str,
    accepted_units: Sequence[str],
) -> bool:
    """按白名单完整语法比较 numeric 参考值与预测值。

    输入参数：
        reference：必须是完整十进制数字面量的参考答案。
        prediction：一个完整数字，或“数字 + 任务显式声明单位”。
        accepted_units：任务允许的单位；空列表表示禁止单位。
    输出返回值：
        预测语法合法且数值与参考值 ``Decimal`` 相等时返回 ``True``。
        比较符、近似词、范围、数量级与额外解释均因无法完整匹配而失败。
    """

    reference_number = parse_numeric_literal(reference)
    if reference_number is None:
        return False
    units = tuple(
        dict.fromkeys(
            str(unit).strip().casefold()
            for unit in accepted_units
            if str(unit).strip()
        )
    )
    if units:
        unit_pattern = "|".join(
            re.escape(unit)
            for unit in sorted(units, key=len, reverse=True)
        )
        match = re.fullmatch(
            rf"\s*({_NUMBER_PATTERN})(?:\s+({unit_pattern}))?\s*",
            prediction,
            flags=re.IGNORECASE,
        )
    else:
        match = re.fullmatch(rf"\s*({_NUMBER_PATTERN})\s*", prediction)
    if match is None:
        return False
    prediction_number = parse_numeric_literal(match.group(1))
    return (
        prediction_number is not None
        and prediction_number == reference_number
    )


def interval_prediction_matches(
    reference_part: str,
    prediction_part: str,
    policy: IntervalPolicy,
) -> bool:
    """按任务白名单比较一个显式单位的窄误差区间。

    输入参数：
        reference_part：``key:number unit`` 形式的完整参考分项。
        prediction_part：``key:center±error[±error] unit`` 形式的完整预测分项。
        policy：任务声明的最大相对误差和允许单位。
    输出返回值：
        键、单位、区间宽度均合法，且参考值落入预测区间时返回 ``True``。
        只有 GeV/MeV 之间允许显式千倍换算；无单位或其他猜测换算均失败。
    """

    normalized_units = tuple(
        dict.fromkeys(unit.strip().casefold() for unit in policy.units)
    )
    if not normalized_units:
        return False
    unit_pattern = "|".join(
        re.escape(unit)
        for unit in sorted(normalized_units, key=len, reverse=True)
    )
    reference_match = re.fullmatch(
        rf"\s*([^:;]+?)\s*:\s*({_NUMBER_PATTERN})\s*"
        rf"({unit_pattern})\s*",
        reference_part,
        flags=re.IGNORECASE,
    )
    if reference_match is None:
        return False
    reference_key = reference_match.group(1).strip().casefold()
    prediction_match = re.fullmatch(
        rf"\s*{re.escape(reference_key)}\s*:\s*({_NUMBER_PATTERN})\s*"
        rf"±\s*(\d+(?:\.\d*)?|\.\d+)"
        rf"(?:\s*±\s*(\d+(?:\.\d*)?|\.\d+))?\s*"
        rf"({unit_pattern})\s*",
        prediction_part,
        flags=re.IGNORECASE,
    )
    if prediction_match is None:
        return False

    reference_value = parse_numeric_literal(reference_match.group(2))
    central_value = parse_numeric_literal(prediction_match.group(1))
    first_error = parse_numeric_literal(prediction_match.group(2))
    second_error = parse_numeric_literal(prediction_match.group(3) or "0")
    if None in (
        reference_value,
        central_value,
        first_error,
        second_error,
    ):
        return False
    assert reference_value is not None
    assert central_value is not None
    assert first_error is not None
    assert second_error is not None
    total_error = first_error + second_error
    if central_value == 0:
        if total_error != 0:
            return False
    elif total_error / abs(central_value) > policy.max_relative_width:
        return False

    reference_unit = reference_match.group(3).casefold()
    prediction_unit = prediction_match.group(4).casefold()
    comparable_reference = reference_value
    if reference_unit != prediction_unit:
        if (reference_unit, prediction_unit) == ("gev", "mev"):
            comparable_reference *= Decimal("1000")
        elif (reference_unit, prediction_unit) == ("mev", "gev"):
            comparable_reference /= Decimal("1000")
        else:
            return False
    return (
        central_value - total_error
        <= comparable_reference
        <= central_value + total_error
    )
