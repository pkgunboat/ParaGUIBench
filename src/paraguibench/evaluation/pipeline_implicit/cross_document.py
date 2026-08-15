"""CombinationDocs-002 以 pinned XLSX 为唯一事实源的评价协议。

本协议不对 docx/pptx 做序列化全等比较，而评价受控 source
提取的事实。强度排序由 pinned XLSX 月度利润值直接派生，因而
不继承当前 HF docx gold 中 December 排在 July 之前的逻辑错误。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType


CROSS_DOCUMENT_TASK_ID = "Operation-FileOperate-CombinationDocs-002"
CROSS_DOCUMENT_PROTOCOL_ID = "paraguibench.operation.cross-document-facts.v1"


@dataclass(frozen=True, slots=True)
class MonthlyReference:
    """保存 pinned XLSX 一个月份的协议所需事实。

    输入参数：
        profit：工作簿 Profit 列的整数值。
        customers：工作簿 Customers 列的整数值。
    输出返回值：
        不可变月度参考事实。
    """

    profit: int
    customers: int


PINNED_XLSX_MONTHLY_REFERENCE = MappingProxyType(
    {
        "january": MonthlyReference(47_109, 1_895),
        "february": MonthlyReference(-9_019, 3_602),
        "march": MonthlyReference(-5_823, 1_896),
        "april": MonthlyReference(30_242, 1_621),
        "may": MonthlyReference(-12_207, 3_602),
        "june": MonthlyReference(37_149, 3_650),
        "july": MonthlyReference(52_642, 3_069),
        "august": MonthlyReference(37_869, 4_167),
        "september": MonthlyReference(34_951, 2_341),
        "october": MonthlyReference(26_644, 3_896),
        "november": MonthlyReference(28_688, 2_885),
        "december": MonthlyReference(48_095, 2_516),
    }
)
_EXPECTED_PROFIT_ORDER = tuple(
    month
    for month, _ in sorted(
        PINNED_XLSX_MONTHLY_REFERENCE.items(),
        key=lambda item: item[1].profit,
        reverse=True,
    )[:3]
)
_EXPECTED_JANUARY_PROFIT = PINNED_XLSX_MONTHLY_REFERENCE["january"].profit
_EXPECTED_JANUARY_CUSTOMERS = PINNED_XLSX_MONTHLY_REFERENCE["january"].customers
_REQUIRED_FACT_COUNT = 3
_MONTH_ID_PATTERN = re.compile(r"[A-Za-z]{3,16}")
_MAX_FACT_VALUE = 1_000_000_000
_MAX_PROFIT_ORDER_LENGTH = 12
_MAX_UNEXPECTED_DOCUMENTS = 256
_REASON_ORDER = (
    "REFERENCE_SPREADSHEET_CHANGED",
    "MISSING_DOCUMENT",
    "DOCX_JANUARY_PROFIT_INCORRECT",
    "DOCX_PROFIT_ORDER_INCORRECT",
    "PPTX_JANUARY_CUSTOMERS_INCORRECT",
    "DOCX_OTHER_FACT_MISMATCH",
    "PPTX_OTHER_FACT_MISMATCH",
    "UNEXPECTED_DOCUMENT",
)


class CrossDocumentEvaluationError(RuntimeError):
    """表示跨文档证据不完整、类型无效或超出边界。"""


@dataclass(frozen=True, slots=True, repr=False)
class NarrativeFacts:
    """保存受控 docx source 提取的事实摘要。

    输入参数：
        january_profit：文档声明的 January profit。
        strongest_profit_order：文档声明的利润由高到低月份顺序。
        other_facts_match_reference：除两个显式目标外，其余数值与
            基线叙事是否与 pinned XLSX 及原正确内容一致。
    输出返回值：
        不可变 docx 事实观测；不得直接持久化。
    """

    january_profit: int
    strongest_profit_order: tuple[str, ...]
    other_facts_match_reference: bool

    def __repr__(self) -> str:
        """返回不含月份、金额或叙述文本的脱敏表示。

        输入参数：无。
        输出返回值：仅包含固定事实数量和其他事实完整性布尔值。
        """

        return (
            "NarrativeFacts(fact_count=2, "
            f"other_facts_match_reference={self.other_facts_match_reference!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PresentationFacts:
    """保存受控 pptx source 提取的事实摘要。

    输入参数：
        january_customers：January 页声明的 Customers。
        other_facts_match_reference：其余报表事实与 pinned XLSX 及
            原正确内容是否一致。
    输出返回值：
        不可变 pptx 事实观测；不得直接持久化。
    """

    january_customers: int
    other_facts_match_reference: bool

    def __repr__(self) -> str:
        """返回不含客户数或演示文稿文本的脱敏表示。

        输入参数：无。
        输出返回值：仅包含固定事实数量和其他事实完整性布尔值。
        """

        return (
            "PresentationFacts(fact_count=1, "
            f"other_facts_match_reference={self.other_facts_match_reference!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CrossDocumentObservation:
    """保存当前 Attempt 三个 Office 文档的闭集语义观测。

    输入参数：
        complete：受控 source 是否已完整枚举应评文档。
        reference_spreadsheet_unchanged：Agent artifact 中的 reference xlsx 是否
            与 pinned XLSX 归一化语义一致。
        narrative/presentation：可选 docx/pptx 事实；None 表示缺文档。
        unexpected_document_count：三个应评 Office 文档之外的文档数。
    输出返回值：
        不可变跨文档观测。
    """

    complete: bool
    reference_spreadsheet_unchanged: bool
    narrative: NarrativeFacts | None
    presentation: PresentationFacts | None
    unexpected_document_count: int

    def __repr__(self) -> str:
        """返回不含业务事实、路径或文档内容的脱敏表示。

        输入参数：无。
        输出返回值：仅含闭集状态、事实源完整性、文档存在性和额外计数。
        """

        return (
            "CrossDocumentObservation("
            f"complete={self.complete!r}, "
            "reference_spreadsheet_unchanged="
            f"{self.reference_spreadsheet_unchanged!r}, "
            f"narrative_present={self.narrative is not None!r}, "
            f"presentation_present={self.presentation is not None!r}, "
            f"unexpected_document_count={self.unexpected_document_count!r})"
        )


@dataclass(frozen=True, slots=True)
class CrossDocumentEvaluation:
    """保存不含文件内容、月份、数值或路径的脱敏结果。

    输入参数：
        protocol_id/passed/score/reason_codes：协议、结论、三事实分数
            和稳定原因码。
        required/matched/failed_fact_count：必需事实计数。
        missing/unexpected_document_count：文档闭集计数。
        semantic_integrity_failure_count：其他事实或基线叙事损坏数。
        reference_spreadsheet_changed：事实源是否被 Agent 修改。
    输出返回值：
        可安全写入 RunStore details 的不可变结果。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    required_fact_count: int
    matched_fact_count: int
    failed_fact_count: int
    missing_document_count: int
    unexpected_document_count: int
    semantic_integrity_failure_count: int
    reference_spreadsheet_changed: bool


def evaluate_cross_document(
    observation: CrossDocumentObservation,
) -> CrossDocumentEvaluation:
    """以 pinned XLSX 派生事实评价 CombinationDocs-002。

    输入参数：
        observation：受控 source 提取的三文档闭集观测。
    输出返回值：
        January profit、利润顺序、January customers 三事实为
            固定分母，同时严格拒绝事实源改动和额外副作用的结果。
    异常：
        CrossDocumentEvaluationError：采集不完整或字段无效。
    """

    narrative, presentation = _validate_observation(observation)
    matched_facts = 0
    missing_documents = int(narrative is None) + int(presentation is None)
    semantic_integrity_failures = 0
    reason_set: set[str] = set()

    if not observation.reference_spreadsheet_unchanged:
        reason_set.add("REFERENCE_SPREADSHEET_CHANGED")
    if missing_documents:
        reason_set.add("MISSING_DOCUMENT")

    if narrative is not None:
        if narrative.january_profit == _EXPECTED_JANUARY_PROFIT:
            matched_facts += 1
        else:
            reason_set.add("DOCX_JANUARY_PROFIT_INCORRECT")
        if narrative.strongest_profit_order == _EXPECTED_PROFIT_ORDER:
            matched_facts += 1
        else:
            reason_set.add("DOCX_PROFIT_ORDER_INCORRECT")
        if not narrative.other_facts_match_reference:
            semantic_integrity_failures += 1
            reason_set.add("DOCX_OTHER_FACT_MISMATCH")

    if presentation is not None:
        if presentation.january_customers == _EXPECTED_JANUARY_CUSTOMERS:
            matched_facts += 1
        else:
            reason_set.add("PPTX_JANUARY_CUSTOMERS_INCORRECT")
        if not presentation.other_facts_match_reference:
            semantic_integrity_failures += 1
            reason_set.add("PPTX_OTHER_FACT_MISMATCH")

    if observation.unexpected_document_count:
        reason_set.add("UNEXPECTED_DOCUMENT")
    reason_codes = tuple(code for code in _REASON_ORDER if code in reason_set)
    return CrossDocumentEvaluation(
        protocol_id=CROSS_DOCUMENT_PROTOCOL_ID,
        passed=not reason_codes and matched_facts == _REQUIRED_FACT_COUNT,
        score=round(matched_facts / _REQUIRED_FACT_COUNT, 4),
        reason_codes=reason_codes,
        required_fact_count=_REQUIRED_FACT_COUNT,
        matched_fact_count=matched_facts,
        failed_fact_count=_REQUIRED_FACT_COUNT - matched_facts,
        missing_document_count=missing_documents,
        unexpected_document_count=observation.unexpected_document_count,
        semantic_integrity_failure_count=semantic_integrity_failures,
        reference_spreadsheet_changed=(not observation.reference_spreadsheet_unchanged),
    )


def _normalize_profit_order(values: tuple[str, ...]) -> tuple[str, ...]:
    """验证并归一化 docx 提取的月份顺序。

    输入参数：
        values：受控 source 提取的月份逻辑 ID 序列。
    输出返回值：
        忽略 ASCII 大小写的月份 tuple。
    异常：
        CrossDocumentEvaluationError：序列类型、长度或元素无效。
    """

    if not isinstance(values, tuple) or len(values) > _MAX_PROFIT_ORDER_LENGTH:
        raise CrossDocumentEvaluationError("NARRATIVE_FACTS_INVALID")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise CrossDocumentEvaluationError("NARRATIVE_FACTS_INVALID")
        month = value.strip().lower()
        if not _MONTH_ID_PATTERN.fullmatch(month):
            raise CrossDocumentEvaluationError("NARRATIVE_FACTS_INVALID")
        normalized.append(month)
    return tuple(normalized)


def _validate_fact_integer(value: object, error_code: str) -> int:
    """验证提取事实是有界整数且不是布尔值。

    输入参数：
        value：待验证数值。
        error_code：失败时的固定脱敏错误码。
    输出返回值：
        验证通过的整数。
    异常：
        CrossDocumentEvaluationError：数值类型或范围无效。
    """

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not -_MAX_FACT_VALUE <= value <= _MAX_FACT_VALUE
    ):
        raise CrossDocumentEvaluationError(error_code)
    return value


def _validate_observation(
    observation: CrossDocumentObservation,
) -> tuple[NarrativeFacts | None, PresentationFacts | None]:
    """验证跨文档观测并归一化文档事实。

    输入参数：
        observation：待验证观测。
    输出返回值：
        归一化后的可选 docx 与 pptx 事实。
    异常：
        CrossDocumentEvaluationError：采集不完整或字段无效。
    """

    if not isinstance(observation, CrossDocumentObservation):
        raise CrossDocumentEvaluationError("EVIDENCE_INVALID")
    if observation.complete is not True:
        raise CrossDocumentEvaluationError("EVIDENCE_INCOMPLETE")
    if (
        type(observation.reference_spreadsheet_unchanged) is not bool
        or not isinstance(observation.unexpected_document_count, int)
        or isinstance(observation.unexpected_document_count, bool)
        or not 0 <= observation.unexpected_document_count <= _MAX_UNEXPECTED_DOCUMENTS
    ):
        raise CrossDocumentEvaluationError("EVIDENCE_INVALID")

    narrative = observation.narrative
    if narrative is not None:
        if (
            not isinstance(narrative, NarrativeFacts)
            or type(narrative.other_facts_match_reference) is not bool
        ):
            raise CrossDocumentEvaluationError("NARRATIVE_FACTS_INVALID")
        narrative = NarrativeFacts(
            january_profit=_validate_fact_integer(
                narrative.january_profit,
                "NARRATIVE_FACTS_INVALID",
            ),
            strongest_profit_order=_normalize_profit_order(
                narrative.strongest_profit_order
            ),
            other_facts_match_reference=narrative.other_facts_match_reference,
        )

    presentation = observation.presentation
    if presentation is not None:
        if (
            not isinstance(presentation, PresentationFacts)
            or type(presentation.other_facts_match_reference) is not bool
        ):
            raise CrossDocumentEvaluationError("PRESENTATION_FACTS_INVALID")
        presentation = PresentationFacts(
            january_customers=_validate_fact_integer(
                presentation.january_customers,
                "PRESENTATION_FACTS_INVALID",
            ),
            other_facts_match_reference=presentation.other_facts_match_reference,
        )
    return narrative, presentation
