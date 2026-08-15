"""BatchOperationExcel-008 语义行隐藏协议的回归测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.pipeline_implicit.hide_na_rows import (
    HIDE_NA_ROWS_PROTOCOL_ID,
    HideNARowsEvaluationError,
    HideNARowsObservation,
    WorkbookHiddenRows,
    evaluate_hide_na_rows,
)


def _gold_observation() -> HideNARowsObservation:
    """构造与可信 HF gold 等价的完整语义观测。

    输入参数：无。
    输出返回值：包含 5 个工作簿与 8 个隐藏行的完整观测。
    """

    return HideNARowsObservation(
        complete=True,
        workbooks=(
            WorkbookHiddenRows("KFC_Monthly_Data.xlsx", (8, 10), True),
            WorkbookHiddenRows("McDonalds_Monthly_Data.xlsx", (8, 14), True),
            WorkbookHiddenRows("Mixue_Monthly_Data.xlsx", (), True),
            WorkbookHiddenRows("PizzaHut_Monthly_Data.xlsx", (9,), True),
            WorkbookHiddenRows("Subway_Monthly_Data.xlsx", (4, 5, 8), True),
        ),
    )


def test_exact_semantic_hidden_rows_pass_without_serialization_dependency() -> None:
    """验证只比较隐藏语义，不依赖 row_dimensions 序列化痕迹。"""

    result = evaluate_hide_na_rows(_gold_observation())

    assert result.protocol_id == HIDE_NA_ROWS_PROTOCOL_ID
    assert result.passed is True
    assert result.score == 1.0
    assert result.reason_codes == ()
    assert result.expected_hidden_row_count == 8
    assert result.matched_hidden_row_count == 8


def test_noop_and_unexpected_hidden_rows_fail_closed() -> None:
    """验证未操作和额外隐藏行均不能通过。"""

    noop = HideNARowsObservation(
        complete=True,
        workbooks=tuple(
            WorkbookHiddenRows(item.document_name, (), True)
            for item in _gold_observation().workbooks
        ),
    )
    noop_result = evaluate_hide_na_rows(noop)
    assert noop_result.passed is False
    assert noop_result.score == 0.2
    assert noop_result.reason_codes == ("MISSING_HIDDEN_ROW",)

    gold = _gold_observation()
    unexpected = HideNARowsObservation(
        complete=True,
        workbooks=gold.workbooks[:-1]
        + (WorkbookHiddenRows("Subway_Monthly_Data.xlsx", (4, 5, 8, 9), True),),
    )
    unexpected_result = evaluate_hide_na_rows(unexpected)
    assert unexpected_result.passed is False
    assert "UNEXPECTED_HIDDEN_ROW" in unexpected_result.reason_codes


def test_content_mutation_and_incomplete_collection_do_not_pass() -> None:
    """验证改动表内数据会失败，不完整证据则报评价错误。"""

    gold = _gold_observation()
    mutated = HideNARowsObservation(
        complete=True,
        workbooks=(WorkbookHiddenRows("KFC_Monthly_Data.xlsx", (8, 10), False),)
        + gold.workbooks[1:],
    )
    mutated_result = evaluate_hide_na_rows(mutated)
    assert mutated_result.passed is False
    assert mutated_result.reason_codes == ("BASELINE_CONTENT_CHANGED",)

    with pytest.raises(HideNARowsEvaluationError, match="EVIDENCE_INCOMPLETE"):
        evaluate_hide_na_rows(
            HideNARowsObservation(complete=False, workbooks=gold.workbooks)
        )


def test_duplicate_is_evaluator_error_but_unknown_document_is_side_effect() -> None:
    """验证重复身份是证据错误，未知文档是 Agent 副作用。"""

    gold = _gold_observation()
    with pytest.raises(HideNARowsEvaluationError, match="DOCUMENT_SET_INVALID"):
        evaluate_hide_na_rows(
            HideNARowsObservation(
                complete=True,
                workbooks=gold.workbooks + (gold.workbooks[0],),
            )
        )

    unexpected = evaluate_hide_na_rows(
        HideNARowsObservation(
            complete=True,
            workbooks=gold.workbooks
            + (WorkbookHiddenRows("unexpected.xlsx", (), True),),
        )
    )
    assert unexpected.passed is False
    assert unexpected.score == 1.0
    assert unexpected.reason_codes == ("UNEXPECTED_DOCUMENT",)


def test_public_result_does_not_expose_document_names() -> None:
    """验证可公开结果只包含计数和固定原因码。"""

    result = evaluate_hide_na_rows(_gold_observation())
    rendered = repr(result)
    for workbook in _gold_observation().workbooks:
        assert workbook.document_name not in rendered


def test_full_capture_closure_is_fail_not_error_and_observation_repr_is_redacted() -> (
    None
):
    """验证 generic capture 的 64 文件上限在 typed evaluator 中不被误报。

    输入参数：无；构造 5 个期望工作簿与 59 个额外工作簿。
    输出返回值：
        无；完整闭集必须是 Agent 侧效导致的 FAIL，
        同时 observation/workbook 调试表示不能暴露文件名。
    """

    gold = _gold_observation()
    extras = tuple(
        WorkbookHiddenRows(f"private-extra-{index}.xlsx", (), True)
        for index in range(59)
    )
    observation = HideNARowsObservation(
        complete=True,
        workbooks=gold.workbooks + extras,
    )

    result = evaluate_hide_na_rows(observation)

    assert result.passed is False
    assert result.score == 1.0
    assert result.reason_codes == ("UNEXPECTED_DOCUMENT",)
    assert result.unexpected_document_count == 59
    assert "private-extra" not in repr(observation)
    assert "KFC_Monthly_Data.xlsx" not in repr(gold.workbooks[0])
