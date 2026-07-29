"""ParaGUIBench QA 最终答案的确定性评价入口。"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from paraguibench.evaluation.answers.canonical import canonical_exact_answer
from paraguibench.evaluation.answers.extraction import (
    extract_last_complete_answer,
    is_abstention_answer,
)
from paraguibench.evaluation.answers.model import (
    AnswerEvaluation,
    EvaluationContractError,
)
from paraguibench.evaluation.answers.numeric import (
    IntervalPolicy,
    numeric_prediction_matches,
    parse_numeric_literal,
)
from paraguibench.evaluation.answers.structured import (
    has_unexpected_assertion_markers,
    maximum_one_to_one_matches,
    narrow_item_matches,
    narrow_single_value_matches,
    occurs_as_complete_span,
    parse_keyed_numeric_set,
    split_structured_prediction,
    split_semicolon_items,
)


def evaluate_qa_answer(
    task: Mapping[str, Any],
    model_output: str | None,
) -> AnswerEvaluation:
    """按 task 声明评价模型最终 QA 答案。

    输入参数：
        task：包含 ``answer``、可选 ``accepted_answers`` 和
            ``answer_match_mode`` 的 canonical task。
        model_output：Agent 完整最终输出；若存在完整 answer 标签，只使用最后
            一项。
    输出返回值：
        不含 gold 或模型原文的 ``AnswerEvaluation``。
    异常：
        EvaluationContractError：任务契约缺字段、类型错误或声明未知模式。
    """

    primary, aliases = _validated_candidates(task)
    mode = str(task.get("answer_match_mode") or "").strip().casefold()
    if mode not in {
        "",
        "exact",
        "strict_exact",
        "numeric",
        "keyed_numeric_set",
        "ordered_structured",
    }:
        raise EvaluationContractError("不支持的 answer_match_mode")
    interval_policy = _validated_interval_policy(task)

    tagged = extract_last_complete_answer(model_output)
    raw_prediction = tagged if tagged is not None else model_output
    if is_abstention_answer(raw_prediction):
        return AnswerEvaluation(False, 0.0, "agent_abstained")
    prediction = canonical_exact_answer(raw_prediction)
    candidates = [
        canonical_exact_answer(primary),
        *(canonical_exact_answer(alias) for alias in aliases),
    ]
    if mode in {"exact", "strict_exact"}:
        if prediction == candidates[0]:
            return AnswerEvaluation(True, 1.0, "strict_exact")
        if prediction in candidates[1:]:
            return AnswerEvaluation(True, 1.0, "strict_exact_via_alias")
        return AnswerEvaluation(False, 0.0, "strict_exact_no_match")

    if mode == "":
        return _evaluate_implicit_candidates(
            candidates,
            prediction,
            interval_policy,
        )

    if mode == "keyed_numeric_set":
        reference_sets = [
            parse_keyed_numeric_set(candidate) for candidate in candidates
        ]
        if any(candidate is None for candidate in reference_sets):
            raise EvaluationContractError(
                "keyed_numeric_set 的 answer 或别名格式无效"
            )
        prediction_set = parse_keyed_numeric_set(prediction)
        for index, reference_set in enumerate(reference_sets):
            if prediction_set is None or prediction_set != reference_set:
                continue
            return AnswerEvaluation(
                True,
                1.0,
                (
                    "keyed_numeric_set"
                    if index == 0
                    else "keyed_numeric_set_via_alias"
                ),
            )
        return AnswerEvaluation(
            False,
            0.0,
            "keyed_numeric_set_no_match",
        )

    if mode == "ordered_structured":
        references = [split_semicolon_items(item) for item in candidates]
        if any(items is None for items in references):
            raise EvaluationContractError(
                "ordered_structured 的 answer 或别名必须含至少两个分号项"
            )
        primary_items = references[0]
        assert primary_items is not None
        prediction_items, primary_conflicts = split_structured_prediction(
            prediction,
            primary_items,
        )
        primary_matched = sum(
            narrow_item_matches(
                reference_item,
                prediction_item,
                interval_policy,
            )
            for reference_item, prediction_item in zip(
                primary_items,
                prediction_items,
                strict=False,
            )
        )
        for index, reference_items in enumerate(references):
            assert reference_items is not None
            candidate_prediction_items, conflicts = (
                split_structured_prediction(prediction, reference_items)
            )
            if (
                conflicts
                or len(candidate_prediction_items) != len(reference_items)
                or not all(
                    narrow_item_matches(
                        reference_item,
                        prediction_item,
                        interval_policy,
                    )
                    for reference_item, prediction_item in zip(
                        reference_items,
                        candidate_prediction_items,
                        strict=True,
                    )
                )
            ):
                continue
            return AnswerEvaluation(
                True,
                1.0,
                (
                    "ordered_structured"
                    if index == 0
                    else "ordered_structured_via_alias"
                ),
                MappingProxyType(
                    {
                        "matched": len(reference_items),
                        "total": len(reference_items),
                        "prediction_items": len(candidate_prediction_items),
                        "disjunction_conflicts": conflicts,
                    }
                ),
            )
        return AnswerEvaluation(
            False,
            primary_matched / len(primary_items),
            "ordered_structured_no_match",
            MappingProxyType(
                {
                    "matched": primary_matched,
                    "total": len(primary_items),
                    "prediction_items": len(prediction_items),
                    "disjunction_conflicts": primary_conflicts,
                }
            ),
        )

    accepted_units = _validated_units(task)
    if parse_numeric_literal(candidates[0]) is None:
        raise EvaluationContractError(
            "numeric 模式的 answer 必须是完整数字面量"
        )
    for index, candidate in enumerate(candidates):
        numeric_match = numeric_prediction_matches(
            candidate,
            prediction,
            accepted_units,
        )
        text_alias_match = (
            index > 0
            and parse_numeric_literal(candidate) is None
            and prediction == candidate
        )
        if not numeric_match and not text_alias_match:
            continue
        if index == 0:
            match_type = "numeric_value"
        elif numeric_match:
            match_type = "numeric_value_via_alias"
        else:
            match_type = "numeric_text_alias"
        return AnswerEvaluation(True, 1.0, match_type)
    return AnswerEvaluation(False, 0.0, "numeric_value_mismatch")


def _validated_candidates(
    task: Mapping[str, Any],
) -> tuple[str, tuple[str, ...]]:
    """验证并读取主答案和显式别名。

    输入参数：
        task：待检查的任务映射。
    输出返回值：
        ``(主答案, 去重别名元组)``；别名顺序与任务声明一致。
    异常：
        EvaluationContractError：task、answer 或 accepted_answers 类型无效。
        异常文本只描述字段，不包含任务值或模型原文。
    """

    if not isinstance(task, Mapping):
        raise EvaluationContractError("task 必须是映射")
    primary = task.get("answer")
    if not isinstance(primary, str) or not primary.strip():
        raise EvaluationContractError("answer 必须是非空字符串")
    raw_aliases = task.get("accepted_answers")
    if raw_aliases is None:
        raw_aliases = []
    if not isinstance(raw_aliases, (list, tuple)) or not all(
        isinstance(alias, str) and alias.strip() for alias in raw_aliases
    ):
        raise EvaluationContractError(
            "accepted_answers 必须是非空字符串列表"
        )
    aliases: list[str] = []
    for alias in raw_aliases:
        if alias != primary and alias not in aliases:
            aliases.append(alias)
    return primary, tuple(aliases)


def _validated_units(task: Mapping[str, Any]) -> tuple[str, ...]:
    """验证并读取 numeric 模式允许的单位白名单。

    输入参数：
        task：待检查的任务映射。
    输出返回值：
        去除首尾空白且按声明顺序去重的单位元组；未声明时为空元组。
    异常：
        EvaluationContractError：accepted_units 不是非空字符串列表。
    """

    raw_units = task.get("accepted_units")
    if raw_units is None:
        return ()
    if not isinstance(raw_units, (list, tuple)) or not all(
        isinstance(unit, str) and unit.strip() for unit in raw_units
    ):
        raise EvaluationContractError(
            "accepted_units 必须是非空字符串列表"
        )
    return tuple(dict.fromkeys(unit.strip() for unit in raw_units))


def _evaluate_implicit_candidates(
    candidates: list[str],
    prediction: str,
    interval_policy: IntervalPolicy | None,
) -> AnswerEvaluation:
    """评价未声明模式的 legacy QA 候选。

    输入参数：
        candidates：canonical 主答案及显式别名，主答案位于第一项。
        prediction：已提取并 canonical 化的模型最终答案。
        interval_policy：任务显式声明的窄误差区间，缺省时为 ``None``。
    输出返回值：
        每个候选按自身单值或结构化形态评价；任一候选通过即返回，全部失败时
        返回主答案结果。结果只含匹配类型与计数，不包含原答案文本。
    """

    primary_result: AnswerEvaluation | None = None
    for index, candidate in enumerate(candidates):
        result = _evaluate_one_implicit_candidate(
            candidate,
            prediction,
            interval_policy,
        )
        if index == 0:
            primary_result = result
        if not result.passed:
            continue
        if index == 0:
            return result
        return AnswerEvaluation(
            True,
            1.0,
            f"{result.match_type}_via_alias",
            result.details,
        )
    assert primary_result is not None
    return primary_result


def _evaluate_one_implicit_candidate(
    candidate: str,
    prediction: str,
    interval_policy: IntervalPolicy | None,
) -> AnswerEvaluation:
    """按单个 legacy 候选自身形态执行评价。

    输入参数：
        candidate：一个 canonical 主答案或别名。
        prediction：canonical 模型最终答案。
        interval_policy：任务可选的显式窄区间策略。
    输出返回值：
        单值候选返回窄匹配/冲突诊断；分号候选返回无序一对一 F1。
    """

    reference_items = split_semicolon_items(candidate)
    if reference_items is None:
        if narrow_single_value_matches(candidate, prediction):
            return AnswerEvaluation(True, 1.0, "legacy_narrow_match")
        if (
            has_unexpected_assertion_markers(candidate, prediction)
            and occurs_as_complete_span(candidate, prediction)
            and not any(
                marker in prediction
                for marker in ("(", ")", "[", "]", "（", "）")
            )
        ):
            return AnswerEvaluation(
                False,
                0.5,
                "conflicting_assertion",
            )
        return AnswerEvaluation(False, 0.0, "legacy_no_match")

    prediction_items, disjunction_conflicts = (
        split_structured_prediction(prediction, reference_items)
    )
    matched = maximum_one_to_one_matches(
        reference_items,
        prediction_items,
        lambda reference, predicted: narrow_item_matches(
            reference,
            predicted,
            interval_policy,
        ),
    )
    recall = matched / len(reference_items)
    precision_denominator = len(prediction_items) + disjunction_conflicts
    precision = (
        matched / precision_denominator if precision_denominator else 0.0
    )
    score = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return AnswerEvaluation(
        score == 1.0,
        score,
        (
            "structured_f1_match"
            if score == 1.0
            else "structured_f1_partial"
        ),
        MappingProxyType(
            {
                "matched": matched,
                "total": len(reference_items),
                "prediction_items": precision_denominator,
                "disjunction_conflicts": disjunction_conflicts,
                "precision": precision,
                "recall": recall,
            }
        ),
    )


def _validated_interval_policy(
    task: Mapping[str, Any],
) -> IntervalPolicy | None:
    """验证任务可选的误差区间白名单。

    输入参数：
        task：待检查的任务映射。
    输出返回值：
        未显式启用时返回 ``None``；配置完整时返回 ``IntervalPolicy``。
    异常：
        EvaluationContractError：启用区间但缺少 ``(0,1]`` 宽度或非空单位列表。
    """

    if task.get("allow_interval") is not True:
        return None
    try:
        width = Decimal(str(task.get("interval_max_relative_width")))
    except (InvalidOperation, TypeError, ValueError):
        raise EvaluationContractError(
            "interval_max_relative_width 必须位于 (0,1]"
        ) from None
    raw_units = task.get("interval_units")
    if (
        not Decimal("0") < width <= Decimal("1")
        or not isinstance(raw_units, (list, tuple))
        or not raw_units
        or not all(
            isinstance(unit, str) and unit.strip() for unit in raw_units
        )
    ):
        raise EvaluationContractError(
            "allow_interval 需要 (0,1] 宽度和非空单位列表"
        )
    units = tuple(
        dict.fromkeys(unit.strip().casefold() for unit in raw_units)
    )
    return IntervalPolicy(width, units)
