"""SearchAndWrite-008 固定 XLSX 协议的回归测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.pipeline_implicit.searchwrite_xlsx import (
    SEARCHWRITE_XLSX_PROTOCOL_ID,
    SearchWriteCell,
    SearchWriteEvaluationError,
    SearchWriteObservation,
    SearchWriteWorkbook,
    evaluate_searchwrite_xlsx,
)


def _complete_observation() -> SearchWriteObservation:
    """构造两个工作簿九个 gold 单元格的完整观测。

    输入参数：无。
    输出返回值：可以通过固定协议的合成观测。
    """

    return SearchWriteObservation(
        complete=True,
        workbooks=(
            SearchWriteWorkbook(
                document_id="group-1",
                cells=(
                    SearchWriteCell("C6", 2),
                    SearchWriteCell("D6", " London "),
                    SearchWriteCell("B7", 1826),
                    SearchWriteCell("D8", "EDINBURGH"),
                ),
                baseline_unchanged=True,
            ),
            SearchWriteWorkbook(
                document_id="group-2",
                cells=(
                    SearchWriteCell("D4", "Manchester"),
                    SearchWriteCell("B5", 1829.0),
                    SearchWriteCell("C6", "45"),
                    SearchWriteCell("B8", 1965),
                    SearchWriteCell("D8", "Coventry"),
                ),
                baseline_unchanged=True,
            ),
        ),
    )


def test_all_nine_cells_match_with_legacy_normalization() -> None:
    """验证文本大小写/首尾空白与整数数值匹配语义。"""

    result = evaluate_searchwrite_xlsx(_complete_observation())

    assert result.protocol_id == SEARCHWRITE_XLSX_PROTOCOL_ID
    assert result.passed is True
    assert result.score == 1.0
    assert result.matched_cell_count == 9
    assert result.reason_codes == ()


def test_missing_or_wrong_cell_is_counted_in_fixed_denominator() -> None:
    """验证缺格和错值不会从九格分母中消失。"""

    source = _complete_observation()
    group_2 = source.workbooks[1]
    broken = SearchWriteWorkbook(
        document_id="group-2",
        cells=tuple(
            SearchWriteCell(cell.coordinate, 50) if cell.coordinate == "C6" else cell
            for cell in group_2.cells
            if cell.coordinate != "D8"
        ),
        baseline_unchanged=True,
    )
    result = evaluate_searchwrite_xlsx(
        SearchWriteObservation(
            complete=True,
            workbooks=(source.workbooks[0], broken),
        )
    )

    assert result.passed is False
    assert result.score == pytest.approx(7 / 9, abs=1e-4)
    assert result.matched_cell_count == 7
    assert result.missing_cell_count == 1
    assert result.mismatched_cell_count == 1
    assert result.reason_codes == ("MISSING_CELL", "CELL_VALUE_MISMATCH")


def test_year_string_with_grouping_separator_preserves_legacy_strictness() -> None:
    """验证年份字符串仍按旧协议精确匹配。"""

    source = _complete_observation()
    group_1 = source.workbooks[0]
    changed = SearchWriteWorkbook(
        document_id="group-1",
        cells=tuple(
            SearchWriteCell(cell.coordinate, "1,826")
            if cell.coordinate == "B7"
            else cell
            for cell in group_1.cells
        ),
        baseline_unchanged=True,
    )
    result = evaluate_searchwrite_xlsx(
        SearchWriteObservation(
            complete=True,
            workbooks=(changed, source.workbooks[1]),
        )
    )

    assert result.passed is False
    assert result.mismatched_cell_count == 1


@pytest.mark.parametrize("malformed_value", ("4,5", "4,,5"))
def test_malformed_grouping_separator_does_not_match_numeric_gold(
    malformed_value: str,
) -> None:
    """验证非标准千分位逗号不能通过数值匹配。"""

    source = _complete_observation()
    group_2 = source.workbooks[1]
    changed = SearchWriteWorkbook(
        document_id="group-2",
        cells=tuple(
            SearchWriteCell(cell.coordinate, malformed_value)
            if cell.coordinate == "C6"
            else cell
            for cell in group_2.cells
        ),
        baseline_unchanged=True,
    )

    result = evaluate_searchwrite_xlsx(
        SearchWriteObservation(
            complete=True,
            workbooks=(source.workbooks[0], changed),
        )
    )

    assert result.passed is False
    assert result.mismatched_cell_count == 1
    assert result.reason_codes == ("CELL_VALUE_MISMATCH",)


def test_document_side_effects_and_incomplete_evidence_fail_closed() -> None:
    """验证基线改动是 Agent 失败，采集不完整是 evaluator error。"""

    source = _complete_observation()
    changed = SearchWriteWorkbook(
        document_id="group-1",
        cells=source.workbooks[0].cells,
        baseline_unchanged=False,
    )
    result = evaluate_searchwrite_xlsx(
        SearchWriteObservation(
            complete=True,
            workbooks=(changed, source.workbooks[1]),
        )
    )
    assert result.passed is False
    assert result.reason_codes == ("BASELINE_CONTENT_CHANGED",)

    with pytest.raises(SearchWriteEvaluationError, match="EVIDENCE_INCOMPLETE"):
        evaluate_searchwrite_xlsx(
            SearchWriteObservation(complete=False, workbooks=source.workbooks)
        )


def test_capture_limit_of_extra_documents_is_a_task_failure() -> None:
    """验证 capture 上限内的额外文件始终记为任务失败。"""

    source = _complete_observation()
    extras = tuple(
        SearchWriteWorkbook(
            document_id=f"unexpected-{index}",
            cells=(),
            baseline_unchanged=False,
        )
        for index in range(1, 63)
    )

    result = evaluate_searchwrite_xlsx(
        SearchWriteObservation(
            complete=True,
            workbooks=source.workbooks + extras,
        )
    )

    assert result.passed is False
    assert result.score == 1.0
    assert result.unexpected_document_count == 62
    assert result.reason_codes == ("UNEXPECTED_DOCUMENT",)


def test_public_result_contains_only_counts_and_fixed_codes() -> None:
    """验证评价结果不携带单元格值或文档身份。"""

    rendered = repr(evaluate_searchwrite_xlsx(_complete_observation()))
    for secret in ("group-1", "London", "Edinburgh", "Coventry", "D8"):
        assert secret not in rendered


def test_short_lived_observation_repr_redacts_identity_and_values() -> None:
    """验证内存观测的调试表示也不泄露文档、坐标或值。"""

    private_value = "PRIVATE-CELL-VALUE"
    cell = SearchWriteCell("ZZ99", private_value)
    workbook = SearchWriteWorkbook(
        document_id="private-document",
        cells=(cell,),
        baseline_unchanged=False,
    )
    observation = SearchWriteObservation(
        complete=True,
        workbooks=(workbook,),
    )

    rendered = " ".join((repr(cell), repr(workbook), repr(observation)))

    for secret in (private_value, "ZZ99", "private-document"):
        assert secret not in rendered
    assert "workbook_count=1" in rendered
    assert "cell_count=1" in rendered

    invalid_workbook = SearchWriteWorkbook(
        document_id="group-1",
        cells=(),
        baseline_unchanged="PRIVATE-INVALID-BASELINE",  # type: ignore[arg-type]
    )
    invalid_observation = SearchWriteObservation(
        complete="PRIVATE-INVALID-COMPLETE",  # type: ignore[arg-type]
        workbooks=(),
    )
    invalid_rendered = repr(invalid_workbook) + repr(invalid_observation)
    assert "PRIVATE-INVALID" not in invalid_rendered
