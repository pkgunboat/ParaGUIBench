"""SearchAndWrite-008 的固定单元格语义评价协议。

本模块从旧 ``searchwrite_xlsx_evaluator`` 恢复“只评价模板空白而
gold 有值的单元格”逻辑，并把两个工作簿九个目标固定为
版本协议。观测中的值只用于内存评价，返回结果不包含文档、
单元格或任何原始值。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import TypeAlias

from .searchwrite_contract import (
    SEARCHWRITE_DOCUMENT_CONTRACTS,
    SEARCHWRITE_XLSX_PROTOCOL_ID,
)

CellValue: TypeAlias = str | int | float | None
_EXPECTED_CELLS = MappingProxyType(
    {
        document.document_id: MappingProxyType(
            {cell.coordinate: cell.expected_value for cell in document.expected_cells}
        )
        for document in SEARCHWRITE_DOCUMENT_CONTRACTS
    }
)
_EXPECTED_CELL_COUNT = sum(len(cells) for cells in _EXPECTED_CELLS.values())
# 必须与 production generic artifact capture 的 64 文件上限一致：
# bridge 会为每个额外常规文件保留强类型计数，使其成为 FAIL，
# 不会因 evaluator 的更小上限被误分类为 ERROR。
_MAX_DOCUMENTS = 64
_MAX_CELLS_PER_DOCUMENT = 256
_MAX_TEXT_BYTES = 4096
_DOCUMENT_ID_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,63})")
_CELL_COORDINATE_PATTERN = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}")
_YEAR_PATTERN = re.compile(r"[0-9]{4}")
_NUMBER_PATTERN = re.compile(
    r"[+-]?(?:"
    r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    r"|(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]*)?)"
    r")(?:[eE][+-]?[0-9]+)?"
)
_REASON_ORDER = (
    "MISSING_DOCUMENT",
    "UNEXPECTED_DOCUMENT",
    "MISSING_CELL",
    "UNEXPECTED_CELL",
    "CELL_VALUE_MISMATCH",
    "BASELINE_CONTENT_CHANGED",
)


class SearchWriteEvaluationError(RuntimeError):
    """表示 SearchAndWrite 受控证据不完整或不可解释。"""


@dataclass(frozen=True, slots=True, repr=False)
class SearchWriteCell:
    """保存单个目标单元格的短生命周期观测。

    输入参数：
        coordinate：工作簿内的 A1 坐标，仅用于固定目标匹配。
        value：受控 source 提取的原始单元格值。
    输出返回值：
        不可变内存观测；不可直接持久化。
    """

    coordinate: str
    value: CellValue

    def __repr__(self) -> str:
        """返回不含坐标或单元格值的调试表示。

        输入参数：无。
        输出返回值：
            仅含固定类型分类，不会反射候选对象的类名。
        """

        if self.value is None:
            value_kind = "none"
        elif type(self.value) is str:
            value_kind = "text"
        elif type(self.value) is int:
            value_kind = "integer"
        elif type(self.value) is float:
            value_kind = "float"
        else:
            value_kind = "invalid"
        return f"SearchWriteCell(value_kind={value_kind!r})"


@dataclass(frozen=True, slots=True, repr=False)
class SearchWriteWorkbook:
    """保存单个工作簿的目标单元格与基线完整性。

    输入参数：
        document_id：固定逻辑身份 ``group-1`` 或 ``group-2``。
        cells：已闭集采集的目标单元格。
        baseline_unchanged：预填单元格、公式和 sheet 结构是否
            与 pinned template 的归一化语义一致。
    输出返回值：
        不可变工作簿观测。
    """

    document_id: str
    cells: tuple[SearchWriteCell, ...]
    baseline_unchanged: bool

    def __repr__(self) -> str:
        """返回不含文档身份和单元格内容的表示。

        输入参数：无。
        输出返回值：仅含单元格计数与基线布尔结论。
        """

        cell_count = len(self.cells) if isinstance(self.cells, tuple) else 0
        baseline_state: bool | str = (
            self.baseline_unchanged
            if type(self.baseline_unchanged) is bool
            else "invalid"
        )
        return (
            "SearchWriteWorkbook("
            f"cell_count={cell_count!r}, "
            f"baseline_unchanged={baseline_state!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class SearchWriteObservation:
    """保存当前 Attempt 的完整 SearchAndWrite 工作簿集。

    输入参数：
        complete：受控 source 是否已完整枚举 xlsx 集。
        workbooks：已采集的工作簿观测。
    输出返回值：
        不可变观测批次。
    """

    complete: bool
    workbooks: tuple[SearchWriteWorkbook, ...]

    def __repr__(self) -> str:
        """返回观测批次的纯计数调试表示。

        输入参数：无。
        输出返回值：
            完整性、工作簿数和已携带单元格数；不含
            文档身份、坐标或任何原始值。
        """

        if isinstance(self.workbooks, tuple):
            workbook_count = len(self.workbooks)
            cell_count = sum(
                len(workbook.cells)
                for workbook in self.workbooks
                if isinstance(workbook, SearchWriteWorkbook)
                and isinstance(workbook.cells, tuple)
            )
        else:
            workbook_count = 0
            cell_count = 0
        complete_state: bool | str = (
            self.complete if type(self.complete) is bool else "invalid"
        )
        return (
            "SearchWriteObservation("
            f"complete={complete_state!r}, "
            f"workbook_count={workbook_count!r}, "
            f"cell_count={cell_count!r})"
        )


@dataclass(frozen=True, slots=True)
class SearchWriteEvaluation:
    """保存不含文档身份、坐标和单元格值的评价结果。

    输入参数：
        protocol_id/passed/score/reason_codes：协议、结论、九格固定
            分母分数和稳定原因码。
        expected/evaluated/unexpected_document_count：文档计数。
        expected/matched/missing/mismatched/unexpected_cell_count：单元格计数。
        mutated_document_count：基线语义被改动的文档数。
    输出返回值：
        可安全投影到 RunStore details 的不可变计数。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    expected_document_count: int
    evaluated_document_count: int
    unexpected_document_count: int
    expected_cell_count: int
    matched_cell_count: int
    missing_cell_count: int
    mismatched_cell_count: int
    unexpected_cell_count: int
    mutated_document_count: int


def evaluate_searchwrite_xlsx(
    observation: SearchWriteObservation,
) -> SearchWriteEvaluation:
    """按固定两文件九单元格协议评价 SearchAndWrite-008。

    输入参数：
        observation：受控 source 完整采集的短生命周期观测。
    输出返回值：
        以 9 为固定分母的脱敏评价结果；缺文件、缺单元格
            或损坏对象均不会从分母消失。
    异常：
        SearchWriteEvaluationError：采集不完整、重复身份或字段越界。
    """

    observed = _validate_observation(observation)
    expected_ids = set(_EXPECTED_CELLS)
    observed_ids = set(observed)
    missing_documents = expected_ids - observed_ids
    unexpected_documents = observed_ids - expected_ids

    matched = 0
    missing = 0
    mismatched = 0
    unexpected_cells = 0
    mutated = 0
    for document_id, expected_cells in _EXPECTED_CELLS.items():
        workbook = observed.get(document_id)
        if workbook is None:
            missing += len(expected_cells)
            continue
        actual_cells = {cell.coordinate: cell.value for cell in workbook.cells}
        expected_coordinates = set(expected_cells)
        actual_coordinates = set(actual_cells)
        missing += len(expected_coordinates - actual_coordinates)
        unexpected_cells += len(actual_coordinates - expected_coordinates)
        for coordinate in expected_coordinates & actual_coordinates:
            if _match_cell(expected_cells[coordinate], actual_cells[coordinate]):
                matched += 1
            else:
                mismatched += 1
        if not workbook.baseline_unchanged:
            mutated += 1

    for document_id in unexpected_documents:
        unexpected_cells += len(observed[document_id].cells)

    reason_set: set[str] = set()
    if missing_documents:
        reason_set.add("MISSING_DOCUMENT")
    if unexpected_documents:
        reason_set.add("UNEXPECTED_DOCUMENT")
    if missing:
        reason_set.add("MISSING_CELL")
    if unexpected_cells:
        reason_set.add("UNEXPECTED_CELL")
    if mismatched:
        reason_set.add("CELL_VALUE_MISMATCH")
    if mutated:
        reason_set.add("BASELINE_CONTENT_CHANGED")
    reason_codes = tuple(code for code in _REASON_ORDER if code in reason_set)
    return SearchWriteEvaluation(
        protocol_id=SEARCHWRITE_XLSX_PROTOCOL_ID,
        passed=not reason_codes and matched == _EXPECTED_CELL_COUNT,
        score=round(matched / _EXPECTED_CELL_COUNT, 4),
        reason_codes=reason_codes,
        expected_document_count=len(_EXPECTED_CELLS),
        evaluated_document_count=len(expected_ids & observed_ids),
        unexpected_document_count=len(unexpected_documents),
        expected_cell_count=_EXPECTED_CELL_COUNT,
        matched_cell_count=matched,
        missing_cell_count=missing,
        mismatched_cell_count=mismatched,
        unexpected_cell_count=unexpected_cells,
        mutated_document_count=mutated,
    )


def _match_cell(expected: CellValue, actual: CellValue) -> bool:
    """忠实复现旧专用 evaluator 与本任务相关的值匹配。

    输入参数：
        expected：固定 gold 单元格值。
        actual：受控 source 提取的 Agent 单元格值。
    输出返回值：
        年份字符串精确、其余整数数值精确、普通文本
            忽略大小写与首尾空白时为 ``True``。
    """

    expected_text = _cell_to_text(expected)
    actual_text = _cell_to_text(actual)
    if actual_text == "":
        return False
    if _YEAR_PATTERN.fullmatch(expected_text) and 1800 <= int(expected_text) <= 2100:
        return expected_text == actual_text
    if _is_number(expected_text) and _is_number(actual_text):
        expected_number = _parse_number(expected_text)
        actual_number = _parse_number(actual_text)
        if expected_number.is_integer():
            return actual_number == expected_number
        if expected_number == 0:
            return actual_number == 0
        return abs(expected_number - actual_number) / abs(expected_number) <= 0.01
    return expected_text.strip().casefold() == actual_text.strip().casefold()


def _cell_to_text(value: CellValue) -> str:
    """将受限单元格值规范化为旧 evaluator 使用的字符串。

    输入参数：
        value：``str/int/float/None`` 之一。
    输出返回值：
        有限长度字符串；整数浮点数不带 ``.0``。
    异常：
        SearchWriteEvaluationError：类型、有限性或长度无效。
    """

    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise SearchWriteEvaluationError("CELL_VALUE_INVALID")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SearchWriteEvaluationError("CELL_VALUE_INVALID")
        text = str(int(value)) if value.is_integer() else str(value)
    else:
        text = str(value).strip()
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise SearchWriteEvaluationError("CELL_VALUE_INVALID") from None
    if len(encoded) > _MAX_TEXT_BYTES:
        raise SearchWriteEvaluationError("CELL_VALUE_INVALID")
    return text


def _is_number(value: str) -> bool:
    """判断字符串是否可按旧协议解析为有限数值。

    输入参数：
        value：待解析字符串。
    输出返回值：
        符合十进制语法，且逗号仅出现在标准三位分组中，
        并能形成有限 ``float`` 时为 ``True``。
    """

    try:
        return math.isfinite(_parse_number(value))
    except (OverflowError, ValueError):
        return False


def _parse_number(value: str) -> float:
    """按旧协议解析可选千分位逗号数值。

    输入参数：
        value：数值字符串；可含合法三位千分位。
    输出返回值：
        Python ``float`` 数值。
    异常：
        ValueError：字符串不是闭合数值语法，或逗号分组非法。
    """

    normalized = value.strip()
    if _NUMBER_PATTERN.fullmatch(normalized) is None:
        raise ValueError("数值或千分位语法无效")
    return float(normalized.replace(",", ""))


def _validate_observation(
    observation: SearchWriteObservation,
) -> dict[str, SearchWriteWorkbook]:
    """验证观测批次的完整性、唯一性和资源边界。

    输入参数：
        observation：待验证 SearchAndWrite 观测。
    输出返回值：
        逻辑文档 ID 到工作簿观测的内部映射。
    异常：
        SearchWriteEvaluationError：采集不完整、字段无效或身份重复。
    """

    if not isinstance(observation, SearchWriteObservation):
        raise SearchWriteEvaluationError("EVIDENCE_INVALID")
    if observation.complete is not True:
        raise SearchWriteEvaluationError("EVIDENCE_INCOMPLETE")
    if (
        not isinstance(observation.workbooks, tuple)
        or len(observation.workbooks) > _MAX_DOCUMENTS
    ):
        raise SearchWriteEvaluationError("DOCUMENT_SET_INVALID")
    result: dict[str, SearchWriteWorkbook] = {}
    for workbook in observation.workbooks:
        if not isinstance(workbook, SearchWriteWorkbook):
            raise SearchWriteEvaluationError("DOCUMENT_SET_INVALID")
        document_id = workbook.document_id
        if (
            not isinstance(document_id, str)
            or not _DOCUMENT_ID_PATTERN.fullmatch(document_id)
            or document_id in result
            or type(workbook.baseline_unchanged) is not bool
            or not isinstance(workbook.cells, tuple)
            or len(workbook.cells) > _MAX_CELLS_PER_DOCUMENT
        ):
            raise SearchWriteEvaluationError("DOCUMENT_SET_INVALID")
        coordinates: set[str] = set()
        for cell in workbook.cells:
            if (
                not isinstance(cell, SearchWriteCell)
                or not isinstance(cell.coordinate, str)
                or not _CELL_COORDINATE_PATTERN.fullmatch(cell.coordinate)
                or cell.coordinate in coordinates
            ):
                raise SearchWriteEvaluationError("CELL_SET_INVALID")
            _cell_to_text(cell.value)
            coordinates.add(cell.coordinate)
        result[document_id] = workbook
    return result
