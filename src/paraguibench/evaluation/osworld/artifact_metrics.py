"""OSWorld artifact 的固定、无 I/O、标准库 metric registry。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import re
from types import MappingProxyType

from .artifact_metric_values import (
    ArtifactMetricValueError,
    evaluate_docx_content,
    evaluate_first_sheet_table,
    evaluate_named_unseen_movies_table,
    evaluate_restaurant_fuzzy_sheet,
    evaluate_problem_invoice_pdf,
    evaluate_pdf_archive,
    evaluate_sheet1_print,
    evaluate_apa_references,
    evaluate_slide_background_image,
    evaluate_speaker_notes_presentation,
)


_CONTENT_MISMATCH = "CONTENT_MISMATCH"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactMetricEvaluationError(ValueError):
    """表示 contract、observation、gold 或 options 无法可靠评价。

    输入参数：
        code：固定且不含 artifact 内容的错误分类码。
        message：固定且不含 artifact 内容或文件名的安全说明。
    输出返回值：
        可由 evidence adapter 映射为 schema/evaluator ERROR 的异常对象。
    """

    def __init__(self, code: str, message: str) -> None:
        """构造一个不回显输入值的 metric 配置异常。

        输入参数：
            code：公开的固定错误分类码。
            message：开发者可读但不含实际输入值的固定说明。
        输出返回值：
            无；初始化当前异常实例。
        """

        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ArtifactMetricContract:
    """公开一个固定 metric contract 的只读身份。

    输入参数：
        contract_id：版本化 metric contract 身份。
        metric_id：旧最终 OSWorld evaluator 中的 metric 名称。
    输出返回值：
        不包含 gold、options、文件路径或可调用对象的不可变元数据。
    """

    contract_id: str
    metric_id: str


@dataclass(frozen=True, slots=True)
class ArtifactMetricEvaluation:
    """保存不含 artifact 原值的安全 metric 结果。

    输入参数：
        contract_id/metric_id：实际执行的固定 contract 与源 metric 身份。
        score：有限 ``[0, 1]`` 分数。
        matched：是否满足固定 gold contract。
        reason_code：不匹配时唯一公开的固定原因码。
    输出返回值：
        可转换为 ``ArtifactMetricObservation`` 的不可变安全结果。
    """

    contract_id: str
    metric_id: str
    score: float
    matched: bool
    reason_code: str | None


_MOUNTAIN_CONTRACT = ArtifactMetricContract(
    contract_id="mountain-file-hash-name-map.v1",
    metric_id="check_direct_json_object",
)
_PROBLEMATIC_MEMBERSHIP_CONTRACT = ArtifactMetricContract(
    contract_id="problematic-invoice-membership.v1",
    metric_id="check_include_exclude",
)
_BIBTEX_CONTRACT = ArtifactMetricContract(
    contract_id="bibtex.ignore-blanks.v1",
    metric_id="compare_text_file",
)
_FIRST_SHEET_CONTRACT = ArtifactMetricContract(
    contract_id="sheet-data.first-sheet.v1",
    metric_id="compare_table",
)
_NAMED_UNSEEN_MOVIES_CONTRACT = ArtifactMetricContract(
    contract_id="sheet-data.named-unseen-movies.v1",
    metric_id="compare_table",
)
_DOCX_CONTENT_CONTRACT = ArtifactMetricContract(
    contract_id="docx-content.v1",
    metric_id="compare_docx_files",
)
_SPEAKER_NOTES_CONTRACT = ArtifactMetricContract(
    contract_id="speaker-notes.no-shape-no-bullets.v1",
    metric_id="compare_pptx_files",
)
_RESTAURANT_FUZZY_CONTRACT = ArtifactMetricContract(
    contract_id="sheet-fuzzy.restaurant-contacts.v1",
    metric_id="compare_table",
)
_PROBLEM_INVOICE_PDF_CONTRACT = ArtifactMetricContract(
    contract_id="problem-invoice-content.v1",
    metric_id="compare_pdfs",
)
_PDF_ARCHIVE_CONTRACT = ArtifactMetricContract(
    contract_id="pdf-chapter-archive.v1",
    metric_id="compare_archive",
)
_APA_REFERENCES_CONTRACT = ArtifactMetricContract(
    contract_id="apa7-references.content-only.base-0_6.v1",
    metric_id="compare_references",
)
_GRF_SHEET_PRINT_CONTRACT = ArtifactMetricContract(
    contract_id="grf-sheet-print.sheet1.v1",
    metric_id="compare_table",
)
_SUPPORTED_RATE_SHEET_PRINT_CONTRACT = ArtifactMetricContract(
    contract_id="supported-rate-sheet-print.sheet1.v1",
    metric_id="compare_table",
)
_SLIDE_BACKGROUND_IMAGE_CONTRACT = ArtifactMetricContract(
    contract_id="slide-index-1.frame-00-08.v1",
    metric_id="compare_images",
)

OSWORLD_ARTIFACT_METRIC_CONTRACTS: Mapping[str, ArtifactMetricContract] = (
    MappingProxyType(
        {
            _MOUNTAIN_CONTRACT.contract_id: _MOUNTAIN_CONTRACT,
            _PROBLEMATIC_MEMBERSHIP_CONTRACT.contract_id: (
                _PROBLEMATIC_MEMBERSHIP_CONTRACT
            ),
            _BIBTEX_CONTRACT.contract_id: _BIBTEX_CONTRACT,
            _FIRST_SHEET_CONTRACT.contract_id: _FIRST_SHEET_CONTRACT,
            _NAMED_UNSEEN_MOVIES_CONTRACT.contract_id: (_NAMED_UNSEEN_MOVIES_CONTRACT),
            _DOCX_CONTENT_CONTRACT.contract_id: _DOCX_CONTENT_CONTRACT,
            _SPEAKER_NOTES_CONTRACT.contract_id: _SPEAKER_NOTES_CONTRACT,
            _RESTAURANT_FUZZY_CONTRACT.contract_id: _RESTAURANT_FUZZY_CONTRACT,
            _PROBLEM_INVOICE_PDF_CONTRACT.contract_id: (_PROBLEM_INVOICE_PDF_CONTRACT),
            _PDF_ARCHIVE_CONTRACT.contract_id: _PDF_ARCHIVE_CONTRACT,
            _APA_REFERENCES_CONTRACT.contract_id: _APA_REFERENCES_CONTRACT,
            _GRF_SHEET_PRINT_CONTRACT.contract_id: _GRF_SHEET_PRINT_CONTRACT,
            _SUPPORTED_RATE_SHEET_PRINT_CONTRACT.contract_id: (
                _SUPPORTED_RATE_SHEET_PRINT_CONTRACT
            ),
            _SLIDE_BACKGROUND_IMAGE_CONTRACT.contract_id: (
                _SLIDE_BACKGROUND_IMAGE_CONTRACT
            ),
        }
    )
)


def evaluate_artifact_metric(
    contract_id: str,
    *,
    actual: object,
    gold: object,
    options: object = None,
) -> ArtifactMetricEvaluation:
    """通过固定 registry 评价已解析的可信内存值或 bytes。

    输入参数：
        contract_id：必须存在于本模块只读 registry 的版本化身份。
        actual：evidence adapter 已读取并解析的 Agent artifact 值。
        gold：已固定来源并在内存中加载的可信 gold 值。
        options：源 evaluator contract 中固定的 options 映射。
    输出返回值：
        只含固定身份、有限分数与原因码的安全结果。
    异常：
        ArtifactMetricEvaluationError：contract 未注册，或 observation、gold、
            options schema 无效；异常不会包含实际内容或文件名。
    """

    if (
        not isinstance(contract_id, str)
        or contract_id not in OSWORLD_ARTIFACT_METRIC_CONTRACTS
    ):
        raise ArtifactMetricEvaluationError(
            "CONTRACT_NOT_REGISTERED",
            "artifact metric contract 未注册",
        )
    contract = OSWORLD_ARTIFACT_METRIC_CONTRACTS[contract_id]
    try:
        if contract is _MOUNTAIN_CONTRACT:
            score = _evaluate_mountain_hash_name_map(actual, gold, options)
        elif contract is _PROBLEMATIC_MEMBERSHIP_CONTRACT:
            score = _evaluate_problematic_directory_membership(actual, gold, options)
        elif contract is _BIBTEX_CONTRACT:
            score = _evaluate_bibtex_text(actual, gold, options)
        elif contract is _FIRST_SHEET_CONTRACT:
            score = evaluate_first_sheet_table(actual, gold, options)
        elif contract is _NAMED_UNSEEN_MOVIES_CONTRACT:
            score = evaluate_named_unseen_movies_table(actual, gold, options)
        elif contract is _DOCX_CONTENT_CONTRACT:
            score = evaluate_docx_content(actual, gold, options)
        elif contract is _SPEAKER_NOTES_CONTRACT:
            score = evaluate_speaker_notes_presentation(actual, gold, options)
        elif contract is _RESTAURANT_FUZZY_CONTRACT:
            score = evaluate_restaurant_fuzzy_sheet(actual, gold, options)
        elif contract is _PROBLEM_INVOICE_PDF_CONTRACT:
            score = evaluate_problem_invoice_pdf(actual, gold, options)
        elif contract is _PDF_ARCHIVE_CONTRACT:
            score = evaluate_pdf_archive(actual, gold, options)
        elif contract in {
            _GRF_SHEET_PRINT_CONTRACT,
            _SUPPORTED_RATE_SHEET_PRINT_CONTRACT,
        }:
            score = evaluate_sheet1_print(actual, gold, options)
        elif contract is _APA_REFERENCES_CONTRACT:
            score = evaluate_apa_references(actual, gold, options)
        elif contract is _SLIDE_BACKGROUND_IMAGE_CONTRACT:
            score = evaluate_slide_background_image(actual, gold, options)
        else:  # pragma: no cover - registry 与分派在模块内同步维护
            raise ArtifactMetricEvaluationError(
                "CONTRACT_NOT_REGISTERED",
                "artifact metric contract 未接入分派器",
            )
    except ArtifactMetricValueError as exc:
        error_codes = {
            "observation": "OBSERVATION_SCHEMA_ERROR",
            "gold": "GOLD_SCHEMA_ERROR",
            "options": "OPTIONS_SCHEMA_ERROR",
        }
        raise ArtifactMetricEvaluationError(
            error_codes[exc.role],
            "artifact typed metric schema 无效",
        ) from None
    return _build_evaluation(contract, score)


def _evaluate_mountain_hash_name_map(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现旧最终 ``check_direct_json_object`` 的山峰任务语义。

    输入参数：
        actual：已解析的 JSON object；只有必需 SHA 对应允许文件名或旧实现
            接受的列表值时才匹配，其他可解析值属于 Agent 内容不匹配。
        gold：非空 ``SHA-256 -> 允许文件名序列`` 映射。
        options：必须精确绑定 ``expect_in_result/result_not_list=true``。
    输出返回值：
        所有 gold SHA 都命中允许候选时为 ``1.0``，内容不匹配时为
        ``0.0``；actual 中额外 SHA 按旧最终实现不影响分数。
    异常：
        ArtifactMetricEvaluationError：actual、gold 或 options schema 无效。
    """

    expected_options = {
        "expect_in_result": True,
        "result_not_list": True,
    }
    if not _has_exact_bool_options(options, expected_options):
        raise ArtifactMetricEvaluationError(
            "OPTIONS_SCHEMA_ERROR",
            "图片名称 metric options 与固定 contract 不一致",
        )
    if not isinstance(gold, Mapping) or not gold:
        raise ArtifactMetricEvaluationError(
            "GOLD_SCHEMA_ERROR",
            "图片名称 metric gold 必须是非空映射",
        )
    if not all(
        isinstance(digest, str)
        and _SHA256_PATTERN.fullmatch(digest)
        and isinstance(candidates, (list, tuple))
        and bool(candidates)
        and all(isinstance(candidate, str) for candidate in candidates)
        for digest, candidates in gold.items()
    ):
        raise ArtifactMetricEvaluationError(
            "GOLD_SCHEMA_ERROR",
            "图片名称 metric gold schema 无效",
        )
    if not isinstance(actual, Mapping):
        raise ArtifactMetricEvaluationError(
            "OBSERVATION_SCHEMA_ERROR",
            "图片名称 metric observation schema 无效",
        )

    for digest, candidates in gold.items():
        actual_value = actual.get(digest)
        if isinstance(actual_value, list):
            if not any(candidate in actual_value for candidate in candidates):
                return 0.0
        elif not isinstance(actual_value, str) or actual_value not in candidates:
            return 0.0
    return 1.0


def _evaluate_problematic_directory_membership(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现旧最终 ``check_include_exclude`` 的原样子串语义。

    输入参数：
        actual：evidence adapter 已安全解析的目录成员 ``tuple[str, ...]``。
        gold：同时包含 ``include`` 与 ``exclude`` 字符串序列的可信规则。
        options：源 evaluator 没有 options，因此必须为 ``None``。
    输出返回值：
        将成员以换行连接后执行旧实现的子串存在/不存在判断，满足时为
        ``1.0``，内容不匹配时为 ``0.0``。
    异常：
        ArtifactMetricEvaluationError：actual、gold 或 options schema 无效。

    注意：
        ``tuple[str, ...]`` 是迁移后 evidence adapter 的输入结构契约；旧
        metric 接收整段命令文本。本适配器只做确定性的换行连接，再保留源
        ``include/exclude`` 子串逻辑，包括空规则序列的 ``all([])`` 语义。
        该 v1 contract 有意保留额外成员不受罚、``name.pdf.bak`` 可满足
        ``name.pdf`` include 的 source-parity 行为；任何 exact/闭集收紧都
        必须另建协议版本并重新批准，不能静默改变本 contract。
    """

    if options is not None:
        raise ArtifactMetricEvaluationError(
            "OPTIONS_SCHEMA_ERROR",
            "目录成员 metric 不接受 options",
        )
    if not isinstance(gold, Mapping) or set(gold) != {"include", "exclude"}:
        raise ArtifactMetricEvaluationError(
            "GOLD_SCHEMA_ERROR",
            "目录成员 metric gold schema 无效",
        )
    include = gold["include"]
    exclude = gold["exclude"]
    if not all(
        isinstance(rules, (list, tuple))
        and all(isinstance(rule, str) for rule in rules)
        for rules in (include, exclude)
    ):
        raise ArtifactMetricEvaluationError(
            "GOLD_SCHEMA_ERROR",
            "目录成员 metric gold 规则无效",
        )
    if not isinstance(actual, tuple) or not all(
        isinstance(member, str) for member in actual
    ):
        raise ArtifactMetricEvaluationError(
            "OBSERVATION_SCHEMA_ERROR",
            "目录成员 metric observation schema 无效",
        )

    listing_text = "\n".join(actual)
    return float(
        all(rule in listing_text for rule in include)
        and all(rule not in listing_text for rule in exclude)
    )


def _evaluate_bibtex_text(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现旧最终 ``compare_text_file(ignore_blanks=true)`` 语义。

    输入参数：
        actual：已从 Agent artifact 有界读取的 UTF-8 bytes。
        gold：已从可信资产有界读取的 UTF-8 bytes。
        options：必须精确为 ``{"ignore_blanks": True}``；本固定 contract
            不启用旧函数可选的 ``ignore_case``。
    输出返回值：
        先把制表符和换行替换为空格，再裁剪并把所有连续空白折叠为单空格；
        归一化文本精确相等时为 ``1.0``，否则为 ``0.0``。
    异常：
        ArtifactMetricEvaluationError：bytes、UTF-8、gold 或 options 无效。
    """

    if not _has_exact_bool_options(options, {"ignore_blanks": True}):
        raise ArtifactMetricEvaluationError(
            "OPTIONS_SCHEMA_ERROR",
            "BibTeX metric options 与固定 contract 不一致",
        )
    if not isinstance(actual, bytes):
        raise ArtifactMetricEvaluationError(
            "OBSERVATION_SCHEMA_ERROR",
            "BibTeX metric observation 必须是 bytes",
        )
    if not isinstance(gold, bytes):
        raise ArtifactMetricEvaluationError(
            "GOLD_SCHEMA_ERROR",
            "BibTeX metric gold 必须是 bytes",
        )
    try:
        actual_text = actual.decode("utf-8")
    except UnicodeDecodeError:
        raise ArtifactMetricEvaluationError(
            "OBSERVATION_SCHEMA_ERROR",
            "BibTeX metric observation 不是有效 UTF-8",
        ) from None
    try:
        gold_text = gold.decode("utf-8")
    except UnicodeDecodeError:
        raise ArtifactMetricEvaluationError(
            "GOLD_SCHEMA_ERROR",
            "BibTeX metric gold 不是有效 UTF-8",
        ) from None

    return float(
        _normalize_ignore_blanks_text(actual_text)
        == _normalize_ignore_blanks_text(gold_text)
    )


def _normalize_ignore_blanks_text(value: str) -> str:
    """按旧最终 ``compare_text_file`` 顺序归一化空白。

    输入参数：
        value：已严格解码的内存文本。
    输出返回值：
        制表符/换行替换、首尾裁剪、连续 Unicode 空白折叠后的文本。
    """

    normalized = re.sub(r"[\t\n]", " ", value).strip()
    return re.sub(r"\s+", " ", normalized)


def _has_exact_bool_options(
    options: object,
    expected: Mapping[str, bool],
) -> bool:
    """验证 options 的键集、bool 类型和值均与固定 contract 精确一致。

    输入参数：
        options：公共接口收到的候选 options。
        expected：模块内部固定的 ``str -> bool`` options 映射。
    输出返回值：
        仅当 options 是相同键集且每个值都是精确 ``bool`` 单例时返回真；
        不接受 Python 中与 bool 相等的整数 ``0/1``。
    """

    return (
        isinstance(options, Mapping)
        and set(options) == set(expected)
        and all(
            type(options[key]) is bool and options[key] is expected[key]
            for key in expected
        )
    )


def _build_evaluation(
    contract: ArtifactMetricContract,
    score: float,
) -> ArtifactMetricEvaluation:
    """把内部 metric 分数收敛为安全、有限的公共结果。

    输入参数：
        contract：当前固定 registry 中选中的只读 contract。
        score：内部 metric 产生的候选分数。
    输出返回值：
        ``ArtifactMetricEvaluation``；不包含 observation 或 gold 原值。
    异常：
        ArtifactMetricEvaluationError：内部实现返回非有限或越界分数。
    """

    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not 0.0 <= float(score) <= 1.0
    ):
        raise ArtifactMetricEvaluationError(
            "METRIC_OUTPUT_ERROR",
            "artifact metric 返回了无效分数",
        )
    score_value = float(score)
    matched = score_value == 1.0
    return ArtifactMetricEvaluation(
        contract_id=contract.contract_id,
        metric_id=contract.metric_id,
        score=score_value,
        matched=matched,
        reason_code=None if matched else _CONTENT_MISMATCH,
    )


__all__ = [
    "OSWORLD_ARTIFACT_METRIC_CONTRACTS",
    "ArtifactMetricContract",
    "ArtifactMetricEvaluation",
    "ArtifactMetricEvaluationError",
    "evaluate_artifact_metric",
]
