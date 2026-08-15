"""CombinationDocs-002 跨文档事实协议的合成 fixture 测试。"""

from __future__ import annotations

from paraguibench.evaluation.pipeline_implicit.cross_document import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CrossDocumentObservation,
    NarrativeFacts,
    PresentationFacts,
    evaluate_cross_document,
)


def _corrected_observation() -> CrossDocumentObservation:
    """构造以 pinned XLSX 为事实源的完全正确合成 fixture。

    输入参数：无。
    输出返回值：修正数值与 July/December 排序后的观测。
    """

    return CrossDocumentObservation(
        complete=True,
        reference_spreadsheet_unchanged=True,
        narrative=NarrativeFacts(
            january_profit=47_109,
            strongest_profit_order=("july", "december", "january"),
            other_facts_match_reference=True,
        ),
        presentation=PresentationFacts(
            january_customers=1_895,
            other_facts_match_reference=True,
        ),
        unexpected_document_count=0,
    )


def test_corrected_cross_document_facts_pass() -> None:
    """验证三个必需事实均从 pinned XLSX 派生并通过。"""

    result = evaluate_cross_document(_corrected_observation())

    assert result.protocol_id == CROSS_DOCUMENT_PROTOCOL_ID
    assert result.passed is True
    assert result.score == 1.0
    assert result.required_fact_count == 3
    assert result.matched_fact_count == 3
    assert result.reason_codes == ()


def test_current_hf_gold_order_is_rejected_and_correct_order_is_derived() -> None:
    """验证当前 gold 的 December-before-July 逻辑错误不会被继承。"""

    corrected = _corrected_observation()
    current_gold = CrossDocumentObservation(
        complete=True,
        reference_spreadsheet_unchanged=True,
        narrative=NarrativeFacts(
            january_profit=47_109,
            strongest_profit_order=("december", "july", "january"),
            other_facts_match_reference=True,
        ),
        presentation=corrected.presentation,
        unexpected_document_count=0,
    )
    result = evaluate_cross_document(current_gold)

    assert result.passed is False
    assert result.score == 0.6667
    assert result.matched_fact_count == 2
    assert result.reason_codes == ("DOCX_PROFIT_ORDER_INCORRECT",)


def test_original_incorrect_values_fail_all_required_facts() -> None:
    """验证原始 docx/pptx 的两个错值和错序同时失败。"""

    original = CrossDocumentObservation(
        complete=True,
        reference_spreadsheet_unchanged=True,
        narrative=NarrativeFacts(
            january_profit=45_324,
            strongest_profit_order=("december", "july", "january"),
            other_facts_match_reference=True,
        ),
        presentation=PresentationFacts(
            january_customers=3_602,
            other_facts_match_reference=True,
        ),
        unexpected_document_count=0,
    )
    result = evaluate_cross_document(original)

    assert result.passed is False
    assert result.score == 0.0
    assert result.failed_fact_count == 3
    assert result.reason_codes == (
        "DOCX_JANUARY_PROFIT_INCORRECT",
        "DOCX_PROFIT_ORDER_INCORRECT",
        "PPTX_JANUARY_CUSTOMERS_INCORRECT",
    )


def test_reference_mutation_other_fact_loss_and_extra_document_fail() -> None:
    """验证改动事实源、破坏其他事实或增加文档均失败。"""

    corrected = _corrected_observation()
    result = evaluate_cross_document(
        CrossDocumentObservation(
            complete=True,
            reference_spreadsheet_unchanged=False,
            narrative=NarrativeFacts(
                january_profit=corrected.narrative.january_profit,
                strongest_profit_order=corrected.narrative.strongest_profit_order,
                other_facts_match_reference=False,
            ),
            presentation=PresentationFacts(
                january_customers=corrected.presentation.january_customers,
                other_facts_match_reference=False,
            ),
            unexpected_document_count=1,
        )
    )

    assert result.passed is False
    assert result.score == 1.0
    assert result.semantic_integrity_failure_count == 2
    assert result.reason_codes == (
        "REFERENCE_SPREADSHEET_CHANGED",
        "DOCX_OTHER_FACT_MISMATCH",
        "PPTX_OTHER_FACT_MISMATCH",
        "UNEXPECTED_DOCUMENT",
    )


def test_missing_document_keeps_required_facts_in_denominator() -> None:
    """验证缺少 docx 时其两个必需事实仍留在分母。"""

    corrected = _corrected_observation()
    result = evaluate_cross_document(
        CrossDocumentObservation(
            complete=True,
            reference_spreadsheet_unchanged=True,
            narrative=None,
            presentation=corrected.presentation,
            unexpected_document_count=0,
        )
    )

    assert result.passed is False
    assert result.score == 0.3333
    assert result.missing_document_count == 1
    assert result.failed_fact_count == 2
    assert result.reason_codes == ("MISSING_DOCUMENT",)


def test_public_result_does_not_expose_months_values_or_paths() -> None:
    """验证可持久化结果只含计数、布尔值和原因码。"""

    rendered = repr(evaluate_cross_document(_corrected_observation())).lower()
    for secret in ("july", "december", "47109", "1895", ".xlsx", ".docx"):
        assert secret not in rendered
