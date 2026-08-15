"""BatchOperationExcel-008 的纯语义行隐藏评价协议。

本模块不比较 Office 文件序列化字节或样式元数据，而由
受控 source 对可见内容与样式做归一化基线比对。评价器本身不依赖
``row_dimensions`` 键集，只评价 HF 固定 gold 证明的语义目标：
包含 ``N/A`` 的数据行被隐藏，其余行不隐藏，工作簿内容不改动。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


HIDE_NA_ROWS_TASK_ID = "Operation-FileOperate-BatchOperationExcel-008"
HIDE_NA_ROWS_PROTOCOL_ID = "paraguibench.operation.xlsx.hide-na-rows.v1"

PINNED_HIDDEN_ROWS_BY_DOCUMENT = MappingProxyType(
    {
        "KFC_Monthly_Data.xlsx": (8, 10),
        "McDonalds_Monthly_Data.xlsx": (8, 14),
        "Mixue_Monthly_Data.xlsx": (),
        "PizzaHut_Monthly_Data.xlsx": (9,),
        "Subway_Monthly_Data.xlsx": (4, 5, 8),
    }
)
_MAX_DOCUMENTS = 64
_MAX_DOCUMENT_NAME_BYTES = 512
_MAX_ROW_NUMBER = 1_048_576
_REASON_ORDER = (
    "MISSING_DOCUMENT",
    "UNEXPECTED_DOCUMENT",
    "MISSING_HIDDEN_ROW",
    "UNEXPECTED_HIDDEN_ROW",
    "BASELINE_CONTENT_CHANGED",
)


class HideNARowsEvaluationError(RuntimeError):
    """表示受控工作簿证据不完整或无法可靠解释。"""


@dataclass(frozen=True, slots=True, repr=False)
class WorkbookHiddenRows:
    """保存单个工作簿的受控语义观测。

    输入参数：
        document_name：相对 artifact 根的文件名，仅在评价器内部匹配。
        hidden_rows：按 Excel 1-based 行号表示的语义隐藏行。
        content_matches_baseline：受控 source 忽略 Office 序列化
            元数据后，确认单元格值、公式、sheet 集与
            归一化可见样式与基线一致。
    输出返回值：
        不可变工作簿证据；本对象不可直接写入 RunStore。
    """

    document_name: str
    hidden_rows: tuple[int, ...]
    content_matches_baseline: bool

    def __repr__(self) -> str:
        """返回不暴露工作簿名称和行号的调试表示。

        输入参数：无。
        输出返回值：
            仅含隐藏行计数和基线匹配布尔值的字符串。
        """

        return (
            "WorkbookHiddenRows("
            f"hidden_row_count={len(self.hidden_rows)!r}, "
            f"content_matches_baseline={self.content_matches_baseline!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class HideNARowsObservation:
    """保存当前 Attempt 完整工作簿集的受控观测。

    输入参数：
        complete：source 是否已闭集枚举 artifact 根下所有 xlsx。
        workbooks：已枚举的工作簿观测。
    输出返回值：
        不可变观测批次。
    """

    complete: bool
    workbooks: tuple[WorkbookHiddenRows, ...]

    def __repr__(self) -> str:
        """返回仅含完整性和工作簿计数的调试表示。

        输入参数：无。
        输出返回值：
            不含文件名或隐藏行号的固定格式字符串。
        """

        return (
            "HideNARowsObservation("
            f"complete={self.complete!r}, "
            f"workbook_count={len(self.workbooks)!r})"
        )


@dataclass(frozen=True, slots=True)
class HideNARowsEvaluation:
    """保存可安全写入 RunStore 的脱敏评价结果。

    输入参数：
        protocol_id/passed/score/reason_codes：版本协议、结论、
            按工作簿等权的语义得分与固定原因码。
        expected/evaluated/unexpected_document_count：文档集计数。
        expected/matched/missing/unexpected_hidden_row_count：行语义计数。
        mutated_document_count：基线语义内容被改动的文档数。
    输出返回值：
        不含文件名、单元格值或路径的不可变结果。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    expected_document_count: int
    evaluated_document_count: int
    unexpected_document_count: int
    expected_hidden_row_count: int
    matched_hidden_row_count: int
    missing_hidden_row_count: int
    unexpected_hidden_row_count: int
    mutated_document_count: int


def evaluate_hide_na_rows(
    observation: HideNARowsObservation,
) -> HideNARowsEvaluation:
    """按固定基线行集评价 Excel-008，避免序列化假失败。

    输入参数：
        observation：受控 source 从单个 Attempt artifact 根闭集
            采集的 xlsx 语义观测。
    输出返回值：
        全部 5 个文档的隐藏行精确一致、内容未改动且
            没有额外 xlsx 时通过的脱敏结果。
    异常：
        HideNARowsEvaluationError：证据不完整、重复或字段超界。
    """

    observed = _validate_observation(observation)
    expected_names = set(PINNED_HIDDEN_ROWS_BY_DOCUMENT)
    observed_names = set(observed)
    missing_names = expected_names - observed_names
    unexpected_names = observed_names - expected_names

    matched_rows = 0
    missing_rows = 0
    unexpected_rows = 0
    mutated_documents = 0
    correct_documents = 0
    for name, expected_tuple in PINNED_HIDDEN_ROWS_BY_DOCUMENT.items():
        item = observed.get(name)
        expected_rows = set(expected_tuple)
        if item is None:
            missing_rows += len(expected_rows)
            continue
        actual_rows = set(item.hidden_rows)
        matched_rows += len(expected_rows & actual_rows)
        missing_rows += len(expected_rows - actual_rows)
        unexpected_rows += len(actual_rows - expected_rows)
        if not item.content_matches_baseline:
            mutated_documents += 1
        if actual_rows == expected_rows and item.content_matches_baseline:
            correct_documents += 1

    reason_set: set[str] = set()
    if missing_names:
        reason_set.add("MISSING_DOCUMENT")
    if unexpected_names:
        reason_set.add("UNEXPECTED_DOCUMENT")
    if missing_rows:
        reason_set.add("MISSING_HIDDEN_ROW")
    if unexpected_rows:
        reason_set.add("UNEXPECTED_HIDDEN_ROW")
    if mutated_documents:
        reason_set.add("BASELINE_CONTENT_CHANGED")
    reason_codes = tuple(code for code in _REASON_ORDER if code in reason_set)

    score_denominator = len(PINNED_HIDDEN_ROWS_BY_DOCUMENT)
    score = round(correct_documents / score_denominator, 4)
    return HideNARowsEvaluation(
        protocol_id=HIDE_NA_ROWS_PROTOCOL_ID,
        passed=not reason_codes,
        score=score,
        reason_codes=reason_codes,
        expected_document_count=len(PINNED_HIDDEN_ROWS_BY_DOCUMENT),
        evaluated_document_count=len(observed_names & expected_names),
        unexpected_document_count=len(unexpected_names),
        expected_hidden_row_count=sum(
            len(rows) for rows in PINNED_HIDDEN_ROWS_BY_DOCUMENT.values()
        ),
        matched_hidden_row_count=matched_rows,
        missing_hidden_row_count=missing_rows,
        unexpected_hidden_row_count=unexpected_rows,
        mutated_document_count=mutated_documents,
    )


def _validate_observation(
    observation: HideNARowsObservation,
) -> dict[str, WorkbookHiddenRows]:
    """验证观测完整性与有限资源边界。

    输入参数：
        observation：待验证的证据批次。
    输出返回值：
        文件名到工作簿观测的内部映射。
    异常：
        HideNARowsEvaluationError：类型、完整性、唯一性或行号无效。
    """

    if not isinstance(observation, HideNARowsObservation):
        raise HideNARowsEvaluationError("EVIDENCE_INVALID")
    if observation.complete is not True:
        raise HideNARowsEvaluationError("EVIDENCE_INCOMPLETE")
    if (
        not isinstance(observation.workbooks, tuple)
        or len(observation.workbooks) > _MAX_DOCUMENTS
    ):
        raise HideNARowsEvaluationError("DOCUMENT_SET_INVALID")

    result: dict[str, WorkbookHiddenRows] = {}
    for item in observation.workbooks:
        if not isinstance(item, WorkbookHiddenRows):
            raise HideNARowsEvaluationError("DOCUMENT_SET_INVALID")
        name = item.document_name
        if not isinstance(name, str) or not name:
            raise HideNARowsEvaluationError("DOCUMENT_SET_INVALID")
        try:
            encoded_name = name.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise HideNARowsEvaluationError("DOCUMENT_SET_INVALID") from None
        if (
            len(encoded_name) > _MAX_DOCUMENT_NAME_BYTES
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or name in result
        ):
            raise HideNARowsEvaluationError("DOCUMENT_SET_INVALID")
        if type(item.content_matches_baseline) is not bool:
            raise HideNARowsEvaluationError("DOCUMENT_STATE_INVALID")
        rows = item.hidden_rows
        if not isinstance(rows, tuple) or len(rows) > _MAX_ROW_NUMBER:
            raise HideNARowsEvaluationError("DOCUMENT_STATE_INVALID")
        if any(
            not isinstance(row, int)
            or isinstance(row, bool)
            or not 1 <= row <= _MAX_ROW_NUMBER
            for row in rows
        ):
            raise HideNARowsEvaluationError("DOCUMENT_STATE_INVALID")
        if tuple(sorted(set(rows))) != rows:
            raise HideNARowsEvaluationError("DOCUMENT_STATE_INVALID")
        result[name] = item
    return result
