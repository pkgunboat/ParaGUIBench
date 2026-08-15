"""Operation eval-rules 的闭集分派、纯 artifact I/O 与脱敏聚合。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
from types import MappingProxyType
from typing import Any
import zipfile

from .catalog import (
    OPERATION_PROTOCOL_ID,
    OPERATION_TASK_RULES,
    OperationTaskRule,
)
from .word_abbreviation_semantics import (
    WordAbbreviationBaseline,
    WordAbbreviationError,
    compare_word_abbreviation_semantics,
    validate_word_abbreviation_baseline_identity,
)
from .word_text_fidelity import (
    WordTextBaseline,
    WordTextFidelityError,
    compare_word_text_fidelity,
    validate_word_text_baseline_identity,
)


_MAX_ARTIFACT_COUNT = 512
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 2048
_MAX_ARCHIVE_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_TREE_MEMBERS = 8192
_MAX_ARCHIVE_TREE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_COMPRESSION_RATIO = 100.0


@dataclass(frozen=True, slots=True)
class OperationPinnedInputFile:
    """保存由正式 input manifest 固定的单文件身份。

    输入参数：
        path/size/sha256：manifest 中的精确 POSIX 路径、字节数和摘要；
        preserved：Agent 执行后是否仍必须与 input 字节完全一致；
        must_change：Agent 输出是否必须与原 input 字节不同。
    输出返回值：
        evaluator 内部使用的不可变文件身份；不会写入 RunStore。
    """

    path: str
    size: int
    sha256: str
    preserved: bool
    must_change: bool


@dataclass(frozen=True, slots=True)
class OperationPinnedArtifactContract:
    """保存特定 Operation 任务的正式 input 与输出闭集。

    输入参数：
        task_id/manifest_reference/manifest_sha256：canonical 任务、manifest
            相对路径与整文件摘要；
        expected_document_count：不可通过删文件缩小的评价分母；
        files：正式 manifest 顺序的全部 input 文件。
    输出返回值：
        evaluator 内部使用的不可变任务合同。
    """

    task_id: str
    manifest_reference: str
    manifest_sha256: str
    expected_document_count: int
    files: tuple[OperationPinnedInputFile, ...]


def _pinned_input(
    path: str,
    size: int,
    sha256: str,
    *,
    preserved: bool = False,
    must_change: bool = False,
) -> OperationPinnedInputFile:
    """构造一条紧凑的正式 input 文件规格。

    输入参数：
        path/size/sha256：formal manifest 原样字段；
        preserved：评价时是否要求输出树中的字节身份不变；
        must_change：是否要求输出字节身份与原 input 不同。
    输出返回值：
        不可变 ``OperationPinnedInputFile``。
    """

    if preserved and must_change:
        raise RuntimeError("Operation pinned input 不能同时保真与强制改动")
    return OperationPinnedInputFile(path, size, sha256, preserved, must_change)


_WORD009_TASK_ID = "Operation-FileOperate-BatchOperationWord-009"
_WORD010_TASK_ID = "Operation-FileOperate-BatchOperationWord-010"
_WORD012_TASK_ID = "Operation-FileOperate-BatchOperationWord-012"
_COMBINATIONDOCS003_TASK_ID = "Operation-FileOperate-CombinationDocs-003"
_PINNED_ARTIFACT_CONTRACTS: Mapping[str, OperationPinnedArtifactContract] = (
    MappingProxyType(
        {
            _COMBINATIONDOCS003_TASK_ID: OperationPinnedArtifactContract(
                task_id=_COMBINATIONDOCS003_TASK_ID,
                manifest_reference=(
                    "benchmark/assets/manifests/"
                    "Operation-FileOperate-CombinationDocs-003.json"
                ),
                manifest_sha256=(
                    "9f6b932bd2162cc7636df914ff633383728d41b570300d4454f4f03f2a82d963"
                ),
                expected_document_count=1,
                files=(
                    _pinned_input(
                        "McDonalds_Monthly_Data.xlsx",
                        9545,
                        "ce00b8df3c48ebb8711a477af2de10053affe0e4a2327c485e8d93ea6ad86e5d",
                        preserved=True,
                    ),
                    _pinned_input(
                        "McDonalds_powerpoint_report.pptx",
                        41099,
                        "c30c3cfeee0c32dd80ea06d54f36d46237af325f4491c975d6d2464b0d08fcc0",
                        must_change=True,
                    ),
                    _pinned_input(
                        "store1.xlsx",
                        9258,
                        "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
                        preserved=True,
                    ),
                    _pinned_input(
                        "store2.xlsx",
                        9278,
                        "fe5bbc48c80cec38568b71a42508cb9df83a6c5b6388701445f1cf4170e3d1d8",
                        preserved=True,
                    ),
                ),
            ),
            _WORD009_TASK_ID: OperationPinnedArtifactContract(
                task_id=_WORD009_TASK_ID,
                manifest_reference=(
                    "benchmark/assets/manifests/"
                    "Operation-FileOperate-BatchOperationWord-009.json"
                ),
                manifest_sha256=(
                    "81f25a195e5c367987c408a2acacfb9da562b8f225e5e442f1f7895112214919"
                ),
                expected_document_count=4,
                files=(
                    _pinned_input(
                        "Introduction to Artificial Intelligence.docx",
                        16208,
                        "e5b051ab0a028470e5a88bb719a7978be290014c6289320448219fe02b8d4717",
                    ),
                    _pinned_input(
                        "Research on Multi.docx",
                        14102,
                        "b1300366fab543621dd388c752a83deaf3f0f8fee704655766369ae88cabc230",
                    ),
                    _pinned_input(
                        "The Quiet Station.docx",
                        16333,
                        "fa8bc8777c99551244b412088233b38ac8afaba6cb6efc65957add591c8abb9d",
                    ),
                    _pinned_input(
                        "The Silent Library.docx",
                        14323,
                        "f1d3966648e4888176de9515dfcda26a3e504e1e73592d1686e88c78d753064f",
                    ),
                ),
            ),
            _WORD010_TASK_ID: OperationPinnedArtifactContract(
                task_id=_WORD010_TASK_ID,
                manifest_reference=(
                    "benchmark/assets/manifests/"
                    "Operation-FileOperate-BatchOperationWord-010.json"
                ),
                manifest_sha256=(
                    "1743cbe45191cdf675d92153ac2a4b075393b4f41da929a1759b6c38cc533697"
                ),
                expected_document_count=5,
                files=(
                    _pinned_input(
                        "Cats.docx",
                        13874,
                        "8ac5b07a61c07cb8f7774d17497a08556786a5df0e5f9f8a01e57f4fa0935503",
                    ),
                    _pinned_input(
                        "Dogs.docx",
                        13971,
                        "e140ed48d16d4d970419e9ed60f0afd6305575646056bbd1ab7aa2786e40010e",
                    ),
                    _pinned_input(
                        "Foxes.docx",
                        13955,
                        "c0cfdacf3dff8f4804b6767cd8448f3155d58909bd8330cb2e658a7adb746de2",
                    ),
                    _pinned_input(
                        "Hamsters.docx",
                        13898,
                        "711688f693e014a1172af1fa3e27f7128bd5eb6483dca25de9ee5ee3363bcd5d",
                    ),
                    _pinned_input(
                        "Tigers.docx",
                        13938,
                        "13889f2886526779bf391a39258f2bba495a04ff04a5409334636cb475754be1",
                    ),
                    _pinned_input(
                        "images/Cats.jpeg",
                        5841,
                        "516a5dc48b50aaf03bd7aeb3f9fd0f20de44d624c9e8f9de66b46d92a36db5b5",
                        preserved=True,
                    ),
                    _pinned_input(
                        "images/Dogs.jpeg",
                        8111,
                        "13d12502a8c626efbf4dc053f73f2c56a7c3de8955d26d2c7fe2bb9282cbd17a",
                        preserved=True,
                    ),
                    _pinned_input(
                        "images/Foxes.jpeg",
                        7257,
                        "5bbc110037d4e937516295531e53cf8dac0f7d4a72100d8457a8f1064a0b643d",
                        preserved=True,
                    ),
                    _pinned_input(
                        "images/Hamsters.jpeg",
                        5900,
                        "c4bc248ca159adc2278a62ccab8314222d22c7378848186468da507532a34469",
                        preserved=True,
                    ),
                    _pinned_input(
                        "images/Tigers.jpeg",
                        10111,
                        "6efffea249b289eb42e416eb67257b894d5f2e8f1ca33949cdd9c0dd1af2a5d4",
                        preserved=True,
                    ),
                ),
            ),
            _WORD012_TASK_ID: OperationPinnedArtifactContract(
                task_id=_WORD012_TASK_ID,
                manifest_reference=(
                    "benchmark/assets/manifests/"
                    "Operation-FileOperate-BatchOperationWord-012.json"
                ),
                manifest_sha256=(
                    "00b56d5ab84094a98e70156f399881792fe01a649b945284705f79ec050bf1f2"
                ),
                expected_document_count=4,
                files=(
                    _pinned_input(
                        "Clinical Procedure.docx",
                        13971,
                        "ccbec2ce1c0ea1df920f08676d3b9bf42b9397543b0d013b8a0f5416cfc40e08",
                        must_change=True,
                    ),
                    _pinned_input(
                        "Hardware Review.docx",
                        13998,
                        "2fdde89b1789626f2e71826b1a0acf1260a54c620597273dcf30d6fb7f53223a",
                        must_change=True,
                    ),
                    _pinned_input(
                        "Infrastructure Log.docx",
                        14071,
                        "51378bf4bb9058631f40a155226d7403425166cca1740aace5d016656943e1e0",
                        must_change=True,
                    ),
                    _pinned_input(
                        "Security Protocol.docx",
                        13934,
                        "02718839e2eb6681c092ff1b2347eb0ce83047772332890f0bd9a435c94ca1ad",
                        must_change=True,
                    ),
                ),
            ),
        }
    )
)


def operation_word_text_input_contract(
    task_id: str,
) -> OperationPinnedArtifactContract | None:
    """返回 Word-009/010 与 pure evaluator 共用的正式 input 合同。

    输入参数：
        task_id：待查询 canonical 任务 ID。
    输出返回值：
        009/010 返回不可变 manifest 摘要、完整文件闭集与
        固定 DOCX 分母；其它任务返回 ``None``。runtime
        用此合同在首次 guest 访问前拒绝伪 manifest。
    """

    if task_id not in {_WORD009_TASK_ID, _WORD010_TASK_ID}:
        return None
    return _PINNED_ARTIFACT_CONTRACTS[task_id]


def operation_word_abbreviation_input_contract(
    task_id: str,
) -> OperationPinnedArtifactContract | None:
    """返回 Word-012 与 pure evaluator 共用的正式 input 合同。

    输入参数：
        task_id：待查询 canonical 任务 ID。
    输出返回值：
        Word-012 返回不可变 manifest 摘要、四文档闭集与固定
        分母；其它任务返回 ``None``。
    """

    if task_id != _WORD012_TASK_ID:
        return None
    return _PINNED_ARTIFACT_CONTRACTS[task_id]


class OperationEvaluationError(RuntimeError):
    """表示规则身份、artifact 边界或检查依赖无法可靠评价。

    输入参数：
        code：固定且不含路径、文件内容或 gold 的错误码。
    输出返回值：
        可由 runtime 映射为 evaluator ``ERROR/UNAVAILABLE`` 的异常对象。
    """

    def __init__(self, code: str) -> None:
        """构造一个严格脱敏的 Operation evaluator 异常。

        输入参数：
            code：公开固定错误码，不接受动态详情。
        输出返回值：
            无；初始化当前异常实例与 ``code`` 属性。
        """

        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class OperationRuleEvaluation:
    """保存单条规则不含路径、内容和 gold 的安全结果。

    输入参数：
        rule_id/check_id：固定规则序号身份与白名单检查身份。
        passed/score：规则严格通过状态与有限 ``[0, 1]`` 分数。
        evaluated_artifact_count：当前 Attempt 中安全预检通过的 artifact 数量。
    输出返回值：
        可安全写入 RunStore details 的不可变规则结果。
    """

    rule_id: str
    check_id: str
    passed: bool
    score: float
    evaluated_artifact_count: int


@dataclass(frozen=True, slots=True)
class OperationEvaluation:
    """保存 Operation 任务的脱敏加权结果。

    输入参数：
        protocol_id/task_rule_id：版本化协议与 canonical 任务规则身份。
        passed/score/reason_codes：任务严格结论、分数与固定原因码。
        evaluated_rule_count/passed_rule_count/failed_rule_count：规则安全计数。
        artifact_count：安全预检后的常规文件计数。
        rule_results：逐规则脱敏结果。
    输出返回值：
        不含 artifact 名称、路径、内容、gold 或原始检查 reason 的不可变结果。
    """

    protocol_id: str
    task_rule_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    evaluated_rule_count: int
    passed_rule_count: int
    failed_rule_count: int
    artifact_count: int
    rule_results: tuple[OperationRuleEvaluation, ...]


@dataclass(frozen=True, slots=True)
class OperationCheckContract:
    """保存白名单检查的固定分派与依赖元数据。

    输入参数：
        check_id：不可由任务任意扩展的固定检查身份。
        family：只允许映射到受审计模块的固定家族。
        default_pattern：canonical 规则省略 ``file_pattern`` 时的默认 glob。
        directory_level：是否对根目录只调用一次。
        dependencies：基础安装 ``stdlib`` 或检查所需的可选
            Python distribution 有序闭集。
    输出返回值：
        不含可执行字符串或任意 import 路径的内部不可变规格。
    """

    check_id: str
    family: str
    default_pattern: str
    directory_level: bool = False
    dependencies: tuple[str, ...] = ("stdlib",)


def _contract(
    check_id: str,
    family: str,
    default_pattern: str,
    *,
    directory_level: bool = False,
    dependency: str | tuple[str, ...] = "stdlib",
) -> OperationCheckContract:
    """构造一条固定检查 contract，避免 registry 内出现任意 callable。

    输入参数：
        check_id/family/default_pattern：检查身份、受审计模块家族和默认 glob。
        directory_level/dependency：目录级标志与最小 distribution 依赖闭集。
    输出返回值：
        不可变 ``OperationCheckContract``。
    """

    dependencies = (dependency,) if isinstance(dependency, str) else dependency
    if not dependencies or len(set(dependencies)) != len(dependencies):
        raise RuntimeError("Operation 检查依赖闭集无效")
    return OperationCheckContract(
        check_id=check_id,
        family=family,
        default_pattern=default_pattern,
        directory_level=directory_level,
        dependencies=dependencies,
    )


_CONTRACTS = (
    _contract(
        "check_batchexcel001_annual_sum", "xlsx", "*.xlsx", dependency="openpyxl"
    ),
    _contract(
        "check_batchexcel002_header_bold", "xlsx", "*.xlsx", dependency="openpyxl"
    ),
    _contract(
        "check_batchexcel002_range_right_align", "xlsx", "*.xlsx", dependency="openpyxl"
    ),
    _contract("check_sort_order", "xlsx", "*.xlsx", dependency="openpyxl"),
    _contract("check_cell_contains_string", "xlsx", "*.xlsx", dependency="openpyxl"),
    _contract(
        "check_values_scaled_from_source", "xlsx", "*.xlsx", dependency="openpyxl"
    ),
    _contract("check_negative_values_colored", "xlsx", "*.xlsx", dependency="openpyxl"),
    _contract(
        "check_sorted_copies_preserve_rows",
        "xlsx",
        "*",
        directory_level=True,
        dependency="openpyxl",
    ),
    _contract("check_sequential_numbers", "xlsx", "*.xlsx", dependency="openpyxl"),
    _contract("check_multi_cell_values", "xlsx", "*.xlsx", dependency="openpyxl"),
    _contract("check_cells_filled", "xlsx", "*.xlsx", dependency="openpyxl"),
    _contract("check_slide_transition", "pptx", "*.pptx", dependency="python-pptx"),
    _contract(
        "check_batchppt002_bounds_overlap", "pptx", "*.pptx", dependency="python-pptx"
    ),
    _contract(
        "check_combinationdocs003_source_table_insert",
        "combinationdocs003",
        "*",
        directory_level=True,
        dependency=("openpyxl", "python-pptx", "Pillow"),
    ),
    _contract("check_heading_hierarchy", "docx", "*.docx", dependency="python-docx"),
    _contract(
        "check_batchword002_tab_indent", "docx", "*.docx", dependency="python-docx"
    ),
    _contract(
        "check_max_consecutive_blank_lines", "docx", "*.docx", dependency="python-docx"
    ),
    _contract(
        "check_highlighted_words_capitalized",
        "docx",
        "*.docx",
        dependency="python-docx",
    ),
    _contract(
        "check_misspelled_words_highlighted", "docx", "*.docx", dependency="python-docx"
    ),
    _contract("check_has_toc", "docx", "*.docx", dependency="python-docx"),
    _contract(
        "check_table_contains_expected_values",
        "docx",
        "*.docx",
        dependency="python-docx",
    ),
    _contract("check_vowels_colored_red", "docx", "*.docx", dependency="python-docx"),
    _contract("check_font_name", "docx", "*.docx", dependency="python-docx"),
    _contract("check_line_spacing", "docx", "*.docx", dependency="python-docx"),
    _contract(
        "check_image_name_matches_doc", "docx", "*.docx", dependency="python-docx"
    ),
    _contract(
        "check_heading_palette_and_references",
        "docx",
        "*",
        directory_level=True,
        dependency="python-docx",
    ),
    _contract(
        "check_uppercase_words_have_parentheses",
        "docx",
        "*.docx",
        dependency="python-docx",
    ),
    _contract("check_docx_has_hyperlink", "docx", "*.docx", dependency="python-docx"),
    _contract("check_headings_have_body", "docx", "*.docx", dependency="python-docx"),
    _contract("check_docx_word_count", "docx", "*.docx", dependency="python-docx"),
    _contract(
        "check_html_files_for_xlsx",
        "file_legacy",
        "*.xlsx",
        directory_level=True,
        dependency="openpyxl",
    ),
    _contract("check_files_in_same_folder", "file_legacy", "*", directory_level=True),
    _contract("check_named_files_exist", "file", "*", directory_level=True),
)

OPERATION_CHECK_CONTRACTS: Mapping[str, OperationCheckContract] = MappingProxyType(
    {contract.check_id: contract for contract in _CONTRACTS}
)

if len(OPERATION_CHECK_CONTRACTS) != 33:
    raise RuntimeError("Operation 检查目录必须精确包含 33 个唯一 check")


def evaluate_operation_artifacts(
    result_dir: str | os.PathLike[str],
    task_config: Mapping[str, object],
    *,
    input_text_baseline: WordTextBaseline | None = None,
    input_abbreviation_baseline: WordAbbreviationBaseline | None = None,
) -> OperationEvaluation:
    """按固定 32-task 目录评价一个隔离 Agent artifact 根目录。

    输入参数：
        result_dir：单个 Attempt 的只读 artifact 根目录。
        task_config：canonical task JSON；完整 ``eval_rules`` 必须命中固定摘要。
        input_text_baseline：Word-009/010 在 guest 可变更前从已验证
            host cache 构造的 typed baseline；其它任务必须省略。
        input_abbreviation_baseline：Word-012 在 guest 可变更前构造的
            evaluator-only 逐处语境期望快照；其它任务必须省略。
    输出返回值：
        只含版本身份、规则 ID、分数和计数的 ``OperationEvaluation``。
    异常：
        OperationEvaluationError：规则被篡改、路径/资源边界无效、依赖缺失
            或检查原语异常；异常消息不会包含不可信值。
    """

    task_rule, rules = _validate_task_config(task_config)
    _validate_word_text_baseline_binding(
        task_rule,
        input_text_baseline=input_text_baseline,
    )
    _validate_word_abbreviation_baseline_binding(
        task_rule,
        input_abbreviation_baseline=input_abbreviation_baseline,
    )
    root, artifacts = _preflight_result_tree(result_dir)
    artifact_contract_failure = _pinned_artifact_contract_failure(
        task_rule,
        task_config,
        root,
        artifacts,
    )
    if artifact_contract_failure is not None:
        return artifact_contract_failure
    text_fidelity_failure = _word_text_fidelity_failure(
        task_rule,
        root,
        artifacts,
        input_text_baseline=input_text_baseline,
    )
    if text_fidelity_failure is not None:
        return text_fidelity_failure
    abbreviation_evaluation = _word_abbreviation_evaluation(
        task_rule,
        root,
        artifacts,
        input_abbreviation_baseline=input_abbreviation_baseline,
    )
    if abbreviation_evaluation is not None:
        return abbreviation_evaluation
    rule_results: list[OperationRuleEvaluation] = []
    weighted_total = 0.0
    weight_total = 0.0
    for index, rule in enumerate(rules, start=1):
        check_id = rule["check"]
        spec = OPERATION_CHECK_CONTRACTS.get(check_id)
        if spec is None:
            raise OperationEvaluationError("CHECK_IMPLEMENTATION_UNAVAILABLE")
        weight = rule.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            raise OperationEvaluationError("RULE_SCHEMA_INVALID")
        numeric_weight = float(weight)
        if not math.isfinite(numeric_weight) or numeric_weight < 0:
            raise OperationEvaluationError("RULE_SCHEMA_INVALID")
        outcome = _execute_check(root, artifacts, rule, spec)
        if outcome.get("status") == "evaluator_error":
            raise OperationEvaluationError("CHECK_CONFIGURATION_INVALID")
        score = outcome.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise OperationEvaluationError("CHECK_RESULT_INVALID")
        numeric_score = float(score)
        if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 1.0:
            raise OperationEvaluationError("CHECK_RESULT_INVALID")
        passed = numeric_score >= 1.0 - 1e-9
        rule_results.append(
            OperationRuleEvaluation(
                rule_id=f"{task_rule.rule_id}.rule-{index:02d}",
                check_id=check_id,
                passed=passed,
                score=round(numeric_score, 4),
                evaluated_artifact_count=int(
                    outcome.get("_evaluated_artifact_count", len(artifacts))
                ),
            )
        )
        weighted_total += numeric_score * numeric_weight
        weight_total += numeric_weight
    if weight_total <= 0:
        raise OperationEvaluationError("RULE_WEIGHT_INVALID")
    score = weighted_total / weight_total
    passed_count = sum(result.passed for result in rule_results)
    failed_count = len(rule_results) - passed_count
    return OperationEvaluation(
        protocol_id=OPERATION_PROTOCOL_ID,
        task_rule_id=task_rule.rule_id,
        passed=score >= 1.0 - 1e-9,
        score=round(score, 4),
        reason_codes=() if failed_count == 0 else ("RULE_MISMATCH",),
        evaluated_rule_count=len(rule_results),
        passed_rule_count=passed_count,
        failed_rule_count=failed_count,
        artifact_count=len(artifacts),
        rule_results=tuple(rule_results),
    )


def _word_abbreviation_evaluation(
    task_rule: OperationTaskRule,
    root: Path,
    artifacts: tuple[Path, ...],
    *,
    input_abbreviation_baseline: WordAbbreviationBaseline | None,
) -> OperationEvaluation | None:
    """Word-012 逐处语境比较后直接生成标准评价。

    输入参数：
        task_rule：已验证 canonical 任务；root/artifacts：已通过
        输出闭集预检的 post 快照；input_abbreviation_baseline：
        prepare 前的 evaluator-only DTO。
    输出返回值：
        非 Word-012 返回 ``None``；Word-012 返回固定一条规则的
        PASS/1 或 ``ABBREVIATION_SEMANTICS_MISMATCH`` FAIL/0。
    """

    if task_rule.task_id != _WORD012_TASK_ID:
        return None
    contract = _PINNED_ARTIFACT_CONTRACTS.get(task_rule.task_id)
    if contract is None or input_abbreviation_baseline is None:
        raise OperationEvaluationError("WORD_ABBREVIATION_SEMANTICS_INVALID")
    try:
        result = compare_word_abbreviation_semantics(
            input_abbreviation_baseline,
            root,
        )
    except WordAbbreviationError:
        raise OperationEvaluationError("WORD_ABBREVIATION_SEMANTICS_INVALID") from None
    if result.document_count != contract.expected_document_count:
        raise OperationEvaluationError("WORD_ABBREVIATION_SEMANTICS_INVALID")
    return _word_abbreviation_result_evaluation(
        task_rule,
        matched=result.matched,
        artifact_count=len(artifacts),
        expected_document_count=contract.expected_document_count,
    )


def _word_text_fidelity_failure(
    task_rule: OperationTaskRule,
    root: Path,
    artifacts: tuple[Path, ...],
    *,
    input_text_baseline: WordTextBaseline | None,
) -> OperationEvaluation | None:
    """对 Word-009/010 执行 formal pre→post typed 文字保真门禁。

    输入参数：
        task_rule：已验证的 canonical 任务；root/artifacts：已通过
        完整路径闭集预检的 post 快照；input_text_baseline：
        prepare 前 evaluator-only DTO。
    输出返回值：
        非 009/010 或文字全等时 ``None``；可比较但有删改增/
        容器漂移时返回固定零分；baseline 或内部解析无效时 ERROR。
    """

    contract = _PINNED_ARTIFACT_CONTRACTS.get(task_rule.task_id)
    if task_rule.task_id not in {_WORD009_TASK_ID, _WORD010_TASK_ID}:
        return None
    if contract is None or input_text_baseline is None:
        raise OperationEvaluationError("WORD_TEXT_FIDELITY_INVALID")
    try:
        fidelity = compare_word_text_fidelity(input_text_baseline, root)
    except WordTextFidelityError:
        raise OperationEvaluationError("WORD_TEXT_FIDELITY_INVALID") from None
    if fidelity.document_count != contract.expected_document_count:
        raise OperationEvaluationError("WORD_TEXT_FIDELITY_INVALID")
    if fidelity.matched:
        return None
    return _word_text_fidelity_mismatch_evaluation(
        task_rule,
        artifact_count=len(artifacts),
        expected_document_count=contract.expected_document_count,
    )


def _validate_word_text_baseline_binding(
    task_rule: OperationTaskRule,
    *,
    input_text_baseline: WordTextBaseline | None,
) -> None:
    """在任何 post artifact I/O 或 FAIL 分支前验证 pre baseline。

    输入参数：
        task_rule：已验证的 canonical 任务；input_text_baseline：
        runtime 从 prepare 前证据 seam 传入的 DTO。
    输出返回值：
        无；009/010 缺失或 task/protocol/manifest/路径身份错误
        必须 ERROR，不得被后续缺文件的 FAIL 绕过。非目标任务
        意外携带 baseline 也 ERROR。
    """

    if task_rule.task_id not in {_WORD009_TASK_ID, _WORD010_TASK_ID}:
        if input_text_baseline is not None:
            raise OperationEvaluationError("WORD_TEXT_BASELINE_UNEXPECTED")
        return
    contract = _PINNED_ARTIFACT_CONTRACTS.get(task_rule.task_id)
    if contract is None or input_text_baseline is None:
        raise OperationEvaluationError("WORD_TEXT_BASELINE_REQUIRED")
    document_paths = tuple(
        file.path for file in contract.files if file.path.casefold().endswith(".docx")
    )
    try:
        validate_word_text_baseline_identity(
            input_text_baseline,
            task_id=task_rule.task_id,
            protocol_id=OPERATION_PROTOCOL_ID,
            manifest_sha256=contract.manifest_sha256,
            document_paths=document_paths,
        )
    except WordTextFidelityError:
        raise OperationEvaluationError("WORD_TEXT_FIDELITY_INVALID") from None


def _validate_word_abbreviation_baseline_binding(
    task_rule: OperationTaskRule,
    *,
    input_abbreviation_baseline: WordAbbreviationBaseline | None,
) -> None:
    """在任何 post artifact I/O 前绑定 Word-012 语义 baseline。

    输入参数：
        task_rule：已验证 canonical 任务；input_abbreviation_baseline：
        runtime 从 prepare 前证据 seam 传入的 DTO。
    输出返回值：
        无；Word-012 缺失或任务/协议/manifest/路径/分母错误必须
        ERROR；非目标任务意外携带 baseline 也 ERROR。
    """

    if task_rule.task_id != _WORD012_TASK_ID:
        if input_abbreviation_baseline is not None:
            raise OperationEvaluationError("WORD_ABBREVIATION_BASELINE_UNEXPECTED")
        return
    contract = _PINNED_ARTIFACT_CONTRACTS.get(task_rule.task_id)
    if contract is None or input_abbreviation_baseline is None:
        raise OperationEvaluationError("WORD_ABBREVIATION_BASELINE_REQUIRED")
    document_paths = tuple(file.path for file in contract.files)
    try:
        validate_word_abbreviation_baseline_identity(
            input_abbreviation_baseline,
            task_id=task_rule.task_id,
            protocol_id=OPERATION_PROTOCOL_ID,
            manifest_sha256=contract.manifest_sha256,
            document_paths=document_paths,
        )
    except WordAbbreviationError:
        raise OperationEvaluationError("WORD_ABBREVIATION_SEMANTICS_INVALID") from None


def _pinned_artifact_contract_failure(
    task_rule: OperationTaskRule,
    task_config: Mapping[str, object],
    root: Path,
    artifacts: tuple[Path, ...],
) -> OperationEvaluation | None:
    """对已登记 Word 任务固定正式 input、输出闭集与评价分母。

    输入参数：
        task_rule：已通过 canonical eval-rules 摘要验证的任务规则；
        task_config：完整 canonical 任务映射；
        root/artifacts：已通过路径、类型和 OOXML 资源预检的快照根与文件。
    输出返回值：
        未登记任务或闭集、必须保持原字节的 input 均完整时返回
        ``None``；manifest 引用、精确路径集、保留文件 size/SHA
        任一不匹配时返回固定零分、固定原因码和固定文档分母。
    """

    contract = _PINNED_ARTIFACT_CONTRACTS.get(task_rule.task_id)
    if contract is None:
        return None
    relative_paths = tuple(
        artifact.relative_to(root).as_posix() for artifact in artifacts
    )
    expected_paths = tuple(file.path for file in contract.files)
    if (
        task_config.get("asset_manifest") != contract.manifest_reference
        or relative_paths != expected_paths
    ):
        return _artifact_contract_mismatch_evaluation(
            task_rule,
            artifact_count=len(artifacts),
            expected_document_count=contract.expected_document_count,
        )
    artifacts_by_path = dict(zip(relative_paths, artifacts, strict=True))
    for expected_file in contract.files:
        if not expected_file.preserved and not expected_file.must_change:
            continue
        identity = _regular_file_identity(artifacts_by_path[expected_file.path])
        expected_identity = (expected_file.size, expected_file.sha256)
        if (expected_file.preserved and identity != expected_identity) or (
            expected_file.must_change
            and (identity is None or identity == expected_identity)
        ):
            return _artifact_contract_mismatch_evaluation(
                task_rule,
                artifact_count=len(artifacts),
                expected_document_count=contract.expected_document_count,
            )
    return None


def _regular_file_identity(path: Path) -> tuple[int, str] | None:
    """以 nofollow 方式重新绑定并摘要一个必须保持的 input 文件。

    输入参数：
        path：已经 artifact 树预检的本地快照路径。
    输出返回值：
        同一打开文件描述符的 ``(size, sha256)``；打开失败、
        symlink 竞态或非常规文件时返回 ``None``，不回显路径。
    """

    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | nofollow)
    except OSError:
        return None
    try:
        item_stat = os.fstat(descriptor)
        if not stat.S_ISREG(item_stat.st_mode):
            return None
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return item_stat.st_size, digest.hexdigest()
    except OSError:
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _artifact_contract_mismatch_evaluation(
    task_rule: OperationTaskRule,
    *,
    artifact_count: int,
    expected_document_count: int,
) -> OperationEvaluation:
    """构造不回显文件身份的固定 artifact-contract 失败。

    输入参数：
        task_rule：已验证的 canonical 任务规则；
        artifact_count：安全预检完成后的实际常规文件数；
        expected_document_count：任务固定的文档评价分母。
    输出返回值：
        仅含协议、规则、固定原因码与整数计数的零分结果。
    """

    check_id = task_rule.check_ids[0]
    rule_result = OperationRuleEvaluation(
        rule_id=f"{task_rule.rule_id}.rule-01",
        check_id=check_id,
        passed=False,
        score=0.0,
        evaluated_artifact_count=expected_document_count,
    )
    return OperationEvaluation(
        protocol_id=OPERATION_PROTOCOL_ID,
        task_rule_id=task_rule.rule_id,
        passed=False,
        score=0.0,
        reason_codes=("ARTIFACT_CONTRACT_MISMATCH",),
        evaluated_rule_count=1,
        passed_rule_count=0,
        failed_rule_count=1,
        artifact_count=artifact_count,
        rule_results=(rule_result,),
    )


def _word_text_fidelity_mismatch_evaluation(
    task_rule: OperationTaskRule,
    *,
    artifact_count: int,
    expected_document_count: int,
) -> OperationEvaluation:
    """构造不回显文件、文字或摘要的固定保真失败。

    输入参数：
        task_rule：已验证 canonical 任务；artifact_count：预检完成的
        常规文件数；expected_document_count：固定 DOCX 分母。
    输出返回值：
        只含协议、规则、``TEXT_FIDELITY_MISMATCH`` 与整数计数的零分结果。
    """

    check_id = task_rule.check_ids[0]
    rule_result = OperationRuleEvaluation(
        rule_id=f"{task_rule.rule_id}.rule-01",
        check_id=check_id,
        passed=False,
        score=0.0,
        evaluated_artifact_count=expected_document_count,
    )
    return OperationEvaluation(
        protocol_id=OPERATION_PROTOCOL_ID,
        task_rule_id=task_rule.rule_id,
        passed=False,
        score=0.0,
        reason_codes=("TEXT_FIDELITY_MISMATCH",),
        evaluated_rule_count=1,
        passed_rule_count=0,
        failed_rule_count=1,
        artifact_count=artifact_count,
        rule_results=(rule_result,),
    )


def _word_abbreviation_result_evaluation(
    task_rule: OperationTaskRule,
    *,
    matched: bool,
    artifact_count: int,
    expected_document_count: int,
) -> OperationEvaluation:
    """构造不回显缩写、释义、文件或摘要的 Word-012 结果。

    输入参数：
        task_rule：已验证 canonical 任务；matched：四文档 typed
        快照是否全等；artifact_count：post 常规文件数；
        expected_document_count：固定评价分母。
    输出返回值：
        单规则 PASS/1 或固定 ``ABBREVIATION_SEMANTICS_MISMATCH`` FAIL/0。
    """

    check_id = task_rule.check_ids[0]
    rule_result = OperationRuleEvaluation(
        rule_id=f"{task_rule.rule_id}.rule-01",
        check_id=check_id,
        passed=matched,
        score=1.0 if matched else 0.0,
        evaluated_artifact_count=expected_document_count,
    )
    return OperationEvaluation(
        protocol_id=OPERATION_PROTOCOL_ID,
        task_rule_id=task_rule.rule_id,
        passed=matched,
        score=1.0 if matched else 0.0,
        reason_codes=() if matched else ("ABBREVIATION_SEMANTICS_MISMATCH",),
        evaluated_rule_count=1,
        passed_rule_count=1 if matched else 0,
        failed_rule_count=0 if matched else 1,
        artifact_count=artifact_count,
        rule_results=(rule_result,),
    )


def _validate_task_config(
    task_config: Mapping[str, object],
) -> tuple[OperationTaskRule, tuple[dict[str, Any], ...]]:
    """把不可信 task object 绑定到固定 canonical 规则摘要。

    输入参数：
        task_config：调用方提供的 task 映射。
    输出返回值：
        固定 ``OperationTaskRule`` 与经过 JSON 类型校验的规则元组。
    """

    if not isinstance(task_config, Mapping):
        raise OperationEvaluationError("TASK_SCHEMA_INVALID")
    task_id = task_config.get("task_id")
    if not isinstance(task_id, str):
        raise OperationEvaluationError("TASK_SCHEMA_INVALID")
    task_rule = OPERATION_TASK_RULES.get(task_id)
    if task_rule is None:
        raise OperationEvaluationError("TASK_NOT_REGISTERED")
    raw_rules = task_config.get("eval_rules")
    if (
        not isinstance(raw_rules, list)
        or not raw_rules
        or not all(isinstance(rule, dict) for rule in raw_rules)
    ):
        raise OperationEvaluationError("TASK_SCHEMA_INVALID")
    try:
        digest = _canonical_sha256(raw_rules)
    except (TypeError, ValueError):
        raise OperationEvaluationError("TASK_SCHEMA_INVALID") from None
    if digest != task_rule.rule_set_sha256:
        raise OperationEvaluationError("RULE_SET_IDENTITY_MISMATCH")
    checks = tuple(rule.get("check") for rule in raw_rules)
    if checks != task_rule.check_ids:
        raise OperationEvaluationError("RULE_SET_IDENTITY_MISMATCH")
    return task_rule, tuple(raw_rules)


def _canonical_sha256(value: object) -> str:
    """计算固定规则身份使用的 canonical JSON SHA-256。

    输入参数：
        value：JSON 兼容规则对象。
    输出返回值：
        UTF-8 canonical JSON 的小写十六进制摘要。
    """

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _preflight_result_tree(
    result_dir: str | os.PathLike[str],
) -> tuple[Path, tuple[Path, ...]]:
    """拒绝 symlink、特殊文件、超量文件和超大 artifact 树。

    输入参数：
        result_dir：单 Attempt artifact 根路径。
    输出返回值：
        规范化根路径与按相对路径排序的常规文件元组。
    """

    root = Path(result_dir)
    try:
        root_stat = root.lstat()
    except OSError:
        raise OperationEvaluationError("ARTIFACT_ROOT_INVALID") from None
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise OperationEvaluationError("ARTIFACT_ROOT_INVALID")
    try:
        root = root.resolve(strict=True)
    except OSError:
        raise OperationEvaluationError("ARTIFACT_ROOT_INVALID") from None
    artifacts: list[Path] = []
    total_bytes = 0
    for current_root, dirnames, filenames in os.walk(
        root,
        followlinks=False,
        onerror=_raise_walk_error,
    ):
        current = Path(current_root)
        for name in tuple(dirnames) + tuple(filenames):
            path = current / name
            try:
                item_stat = path.lstat()
            except OSError:
                raise OperationEvaluationError("ARTIFACT_TREE_UNSTABLE") from None
            if stat.S_ISLNK(item_stat.st_mode):
                raise OperationEvaluationError("ARTIFACT_SYMLINK_REJECTED")
            if name in filenames:
                if not stat.S_ISREG(item_stat.st_mode):
                    raise OperationEvaluationError("ARTIFACT_SPECIAL_FILE_REJECTED")
                if item_stat.st_size > _MAX_ARTIFACT_BYTES:
                    raise OperationEvaluationError("ARTIFACT_SIZE_LIMIT_EXCEEDED")
                total_bytes += item_stat.st_size
                artifacts.append(path)
    if len(artifacts) > _MAX_ARTIFACT_COUNT or total_bytes > _MAX_TOTAL_BYTES:
        raise OperationEvaluationError("ARTIFACT_TREE_LIMIT_EXCEEDED")
    artifacts.sort(key=lambda path: path.relative_to(root).as_posix())
    archive_member_count = 0
    archive_uncompressed_bytes = 0
    for artifact in artifacts:
        if artifact.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
            member_count, uncompressed_bytes = _preflight_ooxml_archive(
                artifact,
                remaining_member_budget=(
                    _MAX_ARCHIVE_TREE_MEMBERS - archive_member_count
                ),
                remaining_uncompressed_budget=(
                    _MAX_ARCHIVE_TREE_UNCOMPRESSED_BYTES - archive_uncompressed_bytes
                ),
            )
            archive_member_count += member_count
            archive_uncompressed_bytes += uncompressed_bytes
    return root, tuple(artifacts)


def _raise_walk_error(error: OSError) -> None:
    """把 ``os.walk`` 的不可读目录错误转换为固定 evaluator 错误。

    输入参数：
        error：``os.walk`` 捕获的宿主文件系统异常；其正文不会被保留。
    输出返回值：
        无；始终抛出不含路径的 ``ARTIFACT_TREE_UNREADABLE``。
    """

    del error
    raise OperationEvaluationError("ARTIFACT_TREE_UNREADABLE")


def _preflight_ooxml_archive(
    path: Path,
    *,
    remaining_member_budget: int,
    remaining_uncompressed_budget: int,
) -> tuple[int, int]:
    """在 Office 库加载前检查 OOXML 容器的资源与被动解析边界。

    输入参数：
        path：已确认位于 artifact 根目录内且不是 symlink 的常规文件。
        remaining_member_budget：整棵 artifact 树尚可接受的 ZIP
            member 数量。
        remaining_uncompressed_budget：整树尚可接受的解压字节数。
    输出返回值：
        当前安全容器的 ``(member_count, uncompressed_bytes)``；
        路径穿越、ZIP bomb、加密 member、宏或 XML DTD/entity
        以固定 ``OperationEvaluationError`` 拒绝。
    """

    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise OperationEvaluationError("ARCHIVE_MEMBER_LIMIT_EXCEEDED")
            if len(members) > remaining_member_budget:
                raise OperationEvaluationError("ARCHIVE_TREE_MEMBER_LIMIT_EXCEEDED")
            uncompressed_total = 0
            xml_members: list[zipfile.ZipInfo] = []
            seen_members: set[str] = set()
            for member in members:
                _validate_archive_member(member)
                normalized_name = member.filename.casefold()
                if normalized_name in seen_members:
                    raise OperationEvaluationError("ARCHIVE_DUPLICATE_MEMBER_REJECTED")
                seen_members.add(normalized_name)
                uncompressed_total += member.file_size
                if uncompressed_total > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise OperationEvaluationError(
                        "ARCHIVE_UNCOMPRESSED_LIMIT_EXCEEDED"
                    )
                if uncompressed_total > remaining_uncompressed_budget:
                    raise OperationEvaluationError(
                        "ARCHIVE_TREE_UNCOMPRESSED_LIMIT_EXCEEDED"
                    )
                if member.filename.lower().endswith((".xml", ".rels")):
                    xml_members.append(member)
            for member in xml_members:
                payload = archive.read(member)
                lowered = payload.replace(b"\x00", b"").lower()
                if b"<!doctype" in lowered or b"<!entity" in lowered:
                    raise OperationEvaluationError("ARCHIVE_ACTIVE_XML_REJECTED")
            return len(members), uncompressed_total
    except OperationEvaluationError:
        raise
    except Exception:
        raise OperationEvaluationError("ARCHIVE_INVALID") from None


def _validate_archive_member(member: zipfile.ZipInfo) -> None:
    """校验单个 ZIP member 的路径、类型、大小与压缩比例。

    输入参数：
        member：``zipfile`` 中尚未解压的 central-directory 元数据。
    输出返回值：
        无；违反任一固定资源/路径边界时抛出脱敏 evaluator 错误。
    """

    raw_name = member.filename
    normalized = PurePosixPath(raw_name)
    if (
        not raw_name
        or "\\" in raw_name
        or "\x00" in raw_name
        or normalized.is_absolute()
        or any(part in {"", ".", ".."} for part in normalized.parts)
        or (normalized.parts and ":" in normalized.parts[0])
    ):
        raise OperationEvaluationError("ARCHIVE_PATH_REJECTED")
    unix_mode = member.external_attr >> 16
    if stat.S_ISLNK(unix_mode):
        raise OperationEvaluationError("ARCHIVE_SYMLINK_REJECTED")
    if member.flag_bits & 0x1:
        raise OperationEvaluationError("ARCHIVE_ENCRYPTED_MEMBER_REJECTED")
    if member.file_size > _MAX_ARCHIVE_MEMBER_BYTES:
        raise OperationEvaluationError("ARCHIVE_MEMBER_SIZE_EXCEEDED")
    if member.file_size > 0:
        if member.compress_size <= 0:
            raise OperationEvaluationError("ARCHIVE_COMPRESSION_RATIO_EXCEEDED")
        if member.file_size / member.compress_size > _MAX_ARCHIVE_COMPRESSION_RATIO:
            raise OperationEvaluationError("ARCHIVE_COMPRESSION_RATIO_EXCEEDED")
    if raw_name.casefold().endswith("vbaproject.bin"):
        raise OperationEvaluationError("ARCHIVE_MACRO_REJECTED")


def _execute_check(
    root: Path,
    artifacts: tuple[Path, ...],
    rule: dict[str, Any],
    spec: OperationCheckContract,
) -> dict[str, object]:
    """通过固定家族分派器执行一条 canonical 检查。

    输入参数：
        root/artifacts/rule/spec：预检根目录、固定常规文件闭集、规则与
            白名单分派规格。
    输出返回值：
        旧检查原语兼容的内部结果；调用方只保留分数和固定身份。
    """

    params = rule.get("params", {})
    if not isinstance(params, dict):
        raise OperationEvaluationError("RULE_SCHEMA_INVALID")
    check = _resolve_check(rule["check"], spec.family)
    if spec.directory_level:
        try:
            outcome = check(str(root), params)
            outcome.setdefault("_evaluated_artifact_count", len(artifacts))
            return outcome
        except OperationEvaluationError:
            raise
        except Exception:
            raise OperationEvaluationError("CHECK_EXECUTION_ERROR") from None
    pattern = rule.get("file_pattern", spec.default_pattern)
    excludes = rule.get("exclude_patterns", [])
    if (
        not _safe_basename_pattern(pattern)
        or not isinstance(excludes, list)
        or not all(_safe_basename_pattern(item) for item in excludes)
    ):
        raise OperationEvaluationError("RULE_PATTERN_INVALID")
    matched = tuple(
        path
        for path in artifacts
        if fnmatch.fnmatch(path.name, pattern)
        and not any(
            fnmatch.fnmatch(path.name, excluded)
            or fnmatch.fnmatch(path.relative_to(root).as_posix(), excluded)
            for excluded in excludes
        )
    )
    if not matched:
        return {
            "pass": False,
            "score": 0.0,
            "reason": "missing_artifact",
            "_evaluated_artifact_count": 0,
        }
    outcomes: list[dict[str, object]] = []
    for path in matched:
        try:
            outcomes.append(check(str(path), params))
        except OperationEvaluationError:
            raise
        except Exception:
            raise OperationEvaluationError("CHECK_EXECUTION_ERROR") from None
    if any(outcome.get("status") == "evaluator_error" for outcome in outcomes):
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": "configuration_error",
        }
    scores = tuple(outcome.get("score") for outcome in outcomes)
    if not all(
        isinstance(score, (int, float)) and not isinstance(score, bool)
        for score in scores
    ):
        raise OperationEvaluationError("CHECK_RESULT_INVALID")
    average = sum(float(score) for score in scores) / len(scores)
    return {
        "pass": average >= 1.0 - 1e-9,
        "score": average,
        "reason": "aggregated",
        "_evaluated_artifact_count": len(matched),
    }


def _safe_basename_pattern(value: object) -> bool:
    """判断 canonical 文件 glob 是否仅作用于 artifact basename。

    输入参数：
        value：规则中的 ``file_pattern`` 或 ``exclude_patterns`` 元素。
    输出返回值：
        非空字符串且不含分隔符、绝对路径、NUL 或 ``..`` 段时为 ``True``。
    """

    return (
        isinstance(value, str)
        and bool(value)
        and "\x00" not in value
        and "/" not in value
        and "\\" not in value
        and value not in {".", ".."}
    )


def _resolve_check(
    check_id: str,
    family: str,
) -> Callable[[str, dict[str, object]], dict[str, object]]:
    """从固定模块内的显式 registry 解析检查 callable。

    输入参数：
        check_id/family：已通过 task 摘要绑定的检查 ID 与内部固定家族。
    输出返回值：
        受审计模块中显式注册的 callable；不使用 ``eval`` 或任意 ``getattr``。
    """

    try:
        if family == "file":
            from .checks.file import FILE_CHECKS

            check = FILE_CHECKS.get(check_id)
        elif family == "file_legacy":
            from .checks.file_legacy import FILE_LEGACY_CHECKS

            check = FILE_LEGACY_CHECKS.get(check_id)
        elif family == "docx":
            from .checks.docx import DOCX_CHECKS

            check = DOCX_CHECKS.get(check_id)
        elif family == "xlsx":
            from .checks.xlsx import XLSX_CHECKS

            check = XLSX_CHECKS.get(check_id)
        elif family == "pptx":
            from .checks.pptx import PPTX_CHECKS

            check = PPTX_CHECKS.get(check_id)
        elif family == "combinationdocs003":
            from .checks.combinationdocs003 import COMBINATIONDOCS003_CHECKS

            check = COMBINATIONDOCS003_CHECKS.get(check_id)
        else:
            check = None
    except (ImportError, OSError):
        raise OperationEvaluationError("DEPENDENCY_UNAVAILABLE") from None
    if check is not None:
        return check
    raise OperationEvaluationError("CHECK_IMPLEMENTATION_UNAVAILABLE")


__all__ = [
    "OperationEvaluation",
    "OperationEvaluationError",
    "OperationCheckContract",
    "OperationRuleEvaluation",
    "OPERATION_CHECK_CONTRACTS",
    "evaluate_operation_artifacts",
    "operation_word_abbreviation_input_contract",
    "operation_word_text_input_contract",
]
