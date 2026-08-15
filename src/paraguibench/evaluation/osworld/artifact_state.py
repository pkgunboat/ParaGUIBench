"""15 个 OSWorld artifact-state 任务的纯评价协议与规则目录。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType

from paraguibench.integrations.osworld.artifact_contracts import (
    ArtifactMetricObservation,
    ArtifactSlotObservation,
    ArtifactStateObservation,
)
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)


ARTIFACT_STATE_PROTOCOL_ID = "paraguibench.osworld.artifact-state.v1"

_AVAILABLE = "available"
_MISSING = "missing"
_EVALUATOR_ERROR_STATUSES = frozenset({"read_error", "parse_error", "schema_error"})
_ALLOWED_STATUSES = frozenset({_AVAILABLE, _MISSING, *_EVALUATOR_ERROR_STATUSES})


class OSWorldArtifactStateEvaluationError(RuntimeError):
    """表示 artifact 证据、规则绑定或多 VM 闭包无法可靠评价。"""


@dataclass(frozen=True, slots=True)
class ArtifactMetricRule:
    """定义一个 artifact metric 的固定身份与通过阈值。

    输入参数：
        metric_id：最终源 evaluator 使用的 metric 名称。
        contract_id：固定 gold、options 与适配语义的版本身份。
        score_threshold：metric 判定通过的显式阈值。
    输出返回值：
        不可变 metric 规则。
    """

    metric_id: str
    contract_id: str
    score_threshold: float = 1.0


@dataclass(frozen=True, slots=True)
class ArtifactSlotRule:
    """定义一个需要独立观测的逻辑 artifact 槽位。

    输入参数：
        slot_id：不含路径的稳定槽位身份。
        artifact_kind：供后续 evidence adapter 选择解析器的固定媒体家族。
        metrics：该槽位必须完整产生的 metric 闭集。
    输出返回值：
        不可变 artifact 槽位规则。
    """

    slot_id: str
    artifact_kind: str
    metrics: tuple[ArtifactMetricRule, ...]


@dataclass(frozen=True, slots=True)
class ArtifactStateTaskRule:
    """固定 canonical task 与最终 OSWorld evaluator contract 的对应。

    输入参数：
        task_id：ParaGUIBench canonical task ID。
        rule_id：ParaGUIBench 版本化任务规则身份。
        source_evaluator_id：canonical task ``evaluator_path`` 中的外层 UUID。
        source_task_id：最终 evaluator JSON 内记录的 OSWorld 源 task UUID。
        source_contract_sha256：对完整源 evaluator 对象（含 postconfig、
            func、result、expected 与 options）做 canonical JSON 后得到的
            SHA-256。
        evidence_spec_sha256：对 locator/getter/finalize/limits/metric
            取证规格做 canonical JSON 后得到的 SHA-256。
        artifact_slots：单台 VM 必须独立满足的 artifact 槽位闭集。
    输出返回值：
        可供 evidence 与纯评价层共同绑定的不可变规则。
    """

    task_id: str
    rule_id: str
    source_evaluator_id: str
    source_task_id: str
    source_contract_sha256: str
    evidence_spec_sha256: str
    artifact_slots: tuple[ArtifactSlotRule, ...]


@dataclass(frozen=True, slots=True)
class OSWorldArtifactStateEvaluation:
    """保存不含 artifact 路径、内容或 metric 原始输出的结果。

    输入参数：
        protocol_id/task_rule_id：实际执行的版本化协议与任务规则。
        passed/score：通过状态与最佳单 VM 的合取分数。
        reason_codes：可公开的固定失败原因码。
        evaluated_vm_count/evaluator_error_vm_count：参与聚合的 VM
            总数与证据错误 VM 数。
        missing_artifact_count/failed_metric_count：可评价失败 VM 的安全计数。
    输出返回值：
        可安全写入 runtime details 的不可变结果。
    """

    protocol_id: str
    task_rule_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    evaluated_vm_count: int
    evaluator_error_vm_count: int
    missing_artifact_count: int
    failed_metric_count: int


@dataclass(frozen=True, slots=True)
class _SingleVMArtifactEvaluation:
    """保存单台 VM 的内部结果，不携带 observation 原值。"""

    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    missing_artifact_count: int
    failed_metric_count: int


def _metric(
    metric_id: str,
    contract_id: str,
    *,
    threshold: float = 1.0,
) -> ArtifactMetricRule:
    """构造内部不可变 metric 规则。

    输入参数：
        metric_id/contract_id：源 metric 和版本化适配语义。
        threshold：显式通过阈值。
    输出返回值：
        ``ArtifactMetricRule``。
    """

    return ArtifactMetricRule(
        metric_id=metric_id,
        contract_id=contract_id,
        score_threshold=threshold,
    )


def _slot(
    slot_id: str,
    artifact_kind: str,
    *metrics: ArtifactMetricRule,
) -> ArtifactSlotRule:
    """构造内部 artifact 槽位规则。

    输入参数：
        slot_id/artifact_kind：逻辑槽位和解析家族身份。
        metrics：该槽位的 metric 闭集。
    输出返回值：
        ``ArtifactSlotRule``。
    """

    return ArtifactSlotRule(
        slot_id=slot_id,
        artifact_kind=artifact_kind,
        metrics=tuple(metrics),
    )


def _task_rule(
    task_id: str,
    source_evaluator_id: str,
    source_task_id: str,
    source_contract_sha256: str,
    *artifact_slots: ArtifactSlotRule,
) -> ArtifactStateTaskRule:
    """构造一条带版本身份的 canonical task 规则。

    输入参数：
        task_id：canonical task ID。
        source_evaluator_id/source_task_id：外层 evaluator 与源 OSWorld UUID。
        source_contract_sha256：最终源 evaluator contract 摘要。
        artifact_slots：必须在单台 VM 内合取的槽位。
    输出返回值：
        ``ArtifactStateTaskRule``。
    """

    evidence_spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS.get(task_id)
    if evidence_spec is None or (
        evidence_spec.source_evaluator_id != source_evaluator_id
        or evidence_spec.source_task_id != source_task_id
        or evidence_spec.source_contract_sha256 != source_contract_sha256
    ):
        raise RuntimeError("artifact rule 与 evidence spec 身份不一致")
    return ArtifactStateTaskRule(
        task_id=task_id,
        rule_id=f"paraguibench.osworld.artifact-rule.{task_id}.v1",
        source_evaluator_id=source_evaluator_id,
        source_task_id=source_task_id,
        source_contract_sha256=source_contract_sha256,
        evidence_spec_sha256=evidence_spec.evidence_spec_sha256,
        artifact_slots=tuple(artifact_slots),
    )


_TASK_RULES = (
    _task_rule(
        "Operation-FileOperate-BatchOperation-001",
        "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
        "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
        "28fdb8cb9b84390cfd642e1670d15aa4a5179a6931fa8986495fdd8bece2501c",
        _slot(
            "renamed_picture_set",
            "directory-json-state",
            _metric(
                "check_direct_json_object",
                "mountain-file-hash-name-map.v1",
            ),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-BatchOperation-003",
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "0456405408bdb3d305b10dac904cba7fbc556f041417bef5530387a736cfd517",
        _slot(
            "chapter_pdf_archive",
            "zip-pdf-bundle",
            _metric("compare_archive", "pdf-chapter-archive.v1"),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-CombinationDocs-009",
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "bc73a485042a3878b972e5fa14b9841cef85cfc79ef6c42274c5b68aaef1670b",
        _slot(
            "presentation_with_notes",
            "pptx",
            _metric(
                "compare_pptx_files",
                "speaker-notes.no-shape-no-bullets.v1",
            ),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-CombinationDocs-010",
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "1e04563701fde1335a57c6540c5e9919472fd36b1d2ad1c0d5ae75fb5a1b1387",
        _slot(
            "graded_workbook",
            "xlsx",
            _metric("compare_table", "sheet-data.first-sheet.v1"),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-CombinationDocs-011",
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "846c0629ec2dde2f18a34807e9c0b899260fff5de22fd0cd710d5df3170e94f7",
        _slot(
            "problem_invoice",
            "pdf",
            _metric("compare_pdfs", "problem-invoice-content.v1"),
        ),
        _slot(
            "problematic_directory_membership",
            "directory-listing",
            _metric(
                "check_include_exclude",
                "problematic-invoice-membership.v1",
            ),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-CombinationDocs-012",
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "4780cfb96a299a1e8b30ab369fe767150164ee6140dd747ca7e017ecbe8bc948",
        _slot(
            "apa_references_document",
            "docx",
            _metric(
                "compare_references",
                "apa7-references.content-only.base-0_6.v1",
            ),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-CombinationDocs-013",
        "3d514057-efd2-44b9-98dd-4b092ac2828a",
        "7e287123-70ca-47b9-8521-47db09b69b14",
        "99468cac2c1677f2ddda08f8289b97890f01cf39a24417668c494b77e52c4ed3",
        _slot(
            "grf_workbook_bundle",
            "xlsx-csv-bundle",
            _metric("compare_table", "grf-sheet-print.sheet1.v1"),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-CombinationDocs-014",
        "881deb30-9549-4583-a841-8270c65f2a17",
        "881deb30-9549-4583-a841-8270c65f2a17",
        "f8bb09a70d6733f65bbe1e03e6d8c7a7366671cff70bf688450bba34fbcd809d",
        _slot(
            "ecs_workbook_bundle",
            "xlsx-csv-bundle",
            _metric(
                "compare_table",
                "supported-rate-sheet-print.sheet1.v1",
            ),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-CombinationDocs-015",
        "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "df67aebb-fb3a-44fd-b75b-51b6012df509",
        "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c",
        _slot(
            "bibtex_output",
            "bibtex-text",
            _metric("compare_text_file", "bibtex.ignore-blanks.v1"),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-SearchAndWrite-001",
        "e9e7bcf6-92da-4ff0-aaea-821099370093",
        "c7c1e4c3-9e92-4eba-a4b8-689953975ea4",
        "8ff91da03ef3013c0abe4bac318a6c9ddaa5a6271cf6ed4652ffe7f8b6f73539",
        _slot(
            "professor_contact_workbook",
            "xlsx",
            _metric("compare_table", "sheet-data.first-sheet.v1"),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-SearchAndWrite-003",
        "51d7a7fe-e659-4de0-8345-c2c04da90373",
        "da52d699-e8d2-4dc5-9191-a2199e0b6a9b",
        "8485b90d63965980bae26b44093cafa2c4dbd4b1971354f9b9b14dd12b7ed6a1",
        _slot(
            "book_result_document",
            "docx",
            _metric("compare_docx_files", "docx-content.v1"),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-SearchAndWrite-005",
        "dce61462-cf48-42d9-9466-5a0171aa5d12",
        "67890eb6-6ce5-4c00-9e3d-fb4972699b06",
        "f031f50bac3ab93f3dd1894b9cea737a2246c798ba2daf6a2b777c0239365855",
        _slot(
            "acl_awards_workbook",
            "xlsx",
            _metric("compare_table", "sheet-data.first-sheet.v1"),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-SearchAndWrite-009",
        "14b28a49-e101-4458-835e-2067823ddefb",
        "3e3fc409-bff3-4905-bf16-c968eee3f807",
        "8a440569b160bd2b7295ec4b006a83e002e2b578559c06a4f96d8265902189bf",
        _slot(
            "movies_workbook",
            "xlsx",
            _metric(
                "compare_table",
                "sheet-data.named-unseen-movies.v1",
            ),
        ),
    ),
    _task_rule(
        "Operation-FileOperate-Settings-001",
        "9b5220d5-f1f0-4db9-902d-ad41aae4d775",
        "47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5",
        "5f3ebcf626c74ac25b31c54c186166064c8a62edec23a87efbf1655a854ff66d",
        _slot(
            "slide_background_image",
            "pptx-background-image",
            _metric(
                "compare_images",
                "slide-index-1.frame-00-08.v1",
                threshold=0.90,
            ),
        ),
    ),
    _task_rule(
        "Operation-WebOperate-SearchAndWrite-001",
        "d017201e-a098-46ab-86be-6c99d263ecff",
        "d1acdb87-bb67-4f30-84aa-990e56a09c92",
        "2262ca74a553975a89efff303a8731a9cafee598f9a0e2174562fa2f034e35c4",
        _slot(
            "restaurant_contact_workbook",
            "xlsx",
            _metric(
                "compare_table",
                "sheet-fuzzy.restaurant-contacts.v1",
            ),
        ),
    ),
)

OSWORLD_ARTIFACT_STATE_TASK_RULES: Mapping[str, ArtifactStateTaskRule] = (
    MappingProxyType({rule.task_id: rule for rule in _TASK_RULES})
)


def evaluate_artifact_state_observations(
    task_id: str,
    observations: Sequence[ArtifactStateObservation],
) -> OSWorldArtifactStateEvaluation:
    """按固定任务规则评价一台或多台 VM 的 artifact 快照。

    输入参数：
        task_id：必须存在于可信规则目录的 canonical task ID。
        observations：每台参与 VM 各自完整生成的 artifact 快照。
    输出返回值：
        any-complete 聚合结果；一台 VM 必须独立满足全部
        槽位与 metric，不会跨 VM 拼字段或分数。
    异常：
        OSWorldArtifactStateEvaluationError：任务未注册、没有 VM，或
            在没有完整通过 VM 时存在读取/解析/schema 错误。
    """

    if not isinstance(task_id, str) or task_id not in OSWORLD_ARTIFACT_STATE_TASK_RULES:
        raise OSWorldArtifactStateEvaluationError("artifact-state 任务规则未注册")
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise OSWorldArtifactStateEvaluationError("VM artifact observation 必须是序列")
    if not observations:
        raise OSWorldArtifactStateEvaluationError(
            "没有收到任何 VM artifact observation"
        )

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    results: list[_SingleVMArtifactEvaluation] = []
    evaluator_error_count = 0
    for observation in observations:
        try:
            results.append(_evaluate_single_vm(rule, observation))
        except OSWorldArtifactStateEvaluationError:
            evaluator_error_count += 1

    passing = [result for result in results if result.passed]
    if passing:
        return OSWorldArtifactStateEvaluation(
            protocol_id=ARTIFACT_STATE_PROTOCOL_ID,
            task_rule_id=rule.rule_id,
            passed=True,
            score=max(result.score for result in passing),
            reason_codes=(),
            evaluated_vm_count=len(observations),
            evaluator_error_vm_count=evaluator_error_count,
            missing_artifact_count=0,
            failed_metric_count=0,
        )
    if evaluator_error_count:
        raise OSWorldArtifactStateEvaluationError(
            "没有 VM 完整通过，且至少一台 VM 的 artifact 证据无效"
        )

    reason_codes = tuple(
        dict.fromkeys(reason for result in results for reason in result.reason_codes)
    )
    return OSWorldArtifactStateEvaluation(
        protocol_id=ARTIFACT_STATE_PROTOCOL_ID,
        task_rule_id=rule.rule_id,
        passed=False,
        score=max(result.score for result in results),
        reason_codes=reason_codes,
        evaluated_vm_count=len(observations),
        evaluator_error_vm_count=0,
        missing_artifact_count=sum(result.missing_artifact_count for result in results),
        failed_metric_count=sum(result.failed_metric_count for result in results),
    )


def _evaluate_single_vm(
    rule: ArtifactStateTaskRule,
    observation: ArtifactStateObservation,
) -> _SingleVMArtifactEvaluation:
    """在单台 VM 闭包内验证槽位并对所有 metric 取最小分。

    输入参数：
        rule：已从可信目录选中的 task rule。
        observation：单台 VM 的完整 artifact observation。
    输出返回值：
        单 VM 通过状态、最小分和安全失败计数。
    异常：
        OSWorldArtifactStateEvaluationError：规则绑定错误、observation
            schema 不完整，或显式上报 evaluator 读取/解析错误。
    """

    if not isinstance(observation, ArtifactStateObservation):
        raise OSWorldArtifactStateEvaluationError("artifact observation 类型无效")
    if (
        observation.rule_id != rule.rule_id
        or observation.source_contract_sha256 != rule.source_contract_sha256
        or observation.evidence_spec_sha256 != rule.evidence_spec_sha256
    ):
        raise OSWorldArtifactStateEvaluationError("artifact observation 规则绑定无效")
    if not isinstance(observation.artifact_slots, tuple):
        raise OSWorldArtifactStateEvaluationError("artifact 槽位闭集类型无效")

    observed_slots: dict[str, ArtifactSlotObservation] = {}
    for slot in observation.artifact_slots:
        if not isinstance(slot, ArtifactSlotObservation) or not isinstance(
            slot.slot_id, str
        ):
            raise OSWorldArtifactStateEvaluationError("artifact 槽位 schema 无效")
        if slot.slot_id in observed_slots:
            raise OSWorldArtifactStateEvaluationError("artifact 槽位身份重复")
        observed_slots[slot.slot_id] = slot

    expected_slot_ids = {slot.slot_id for slot in rule.artifact_slots}
    if set(observed_slots) != expected_slot_ids:
        raise OSWorldArtifactStateEvaluationError("artifact 槽位闭集不完整")

    scores: list[float] = []
    reasons: list[str] = []
    missing_artifact_count = 0
    failed_metric_count = 0
    for slot_rule in rule.artifact_slots:
        slot = observed_slots[slot_rule.slot_id]
        if not isinstance(slot.status, str) or slot.status not in _ALLOWED_STATUSES:
            raise OSWorldArtifactStateEvaluationError("artifact 状态 schema 无效")
        if not isinstance(slot.metric_scores, tuple):
            raise OSWorldArtifactStateEvaluationError("artifact metric 闭集类型无效")
        if slot.status in _EVALUATOR_ERROR_STATUSES:
            if slot.metric_scores:
                raise OSWorldArtifactStateEvaluationError("artifact 错误状态携带了分数")
            raise OSWorldArtifactStateEvaluationError("artifact 证据无法可靠评价")
        if slot.status == _MISSING:
            if slot.metric_scores:
                raise OSWorldArtifactStateEvaluationError("artifact 缺失状态携带了分数")
            reasons.append("ARTIFACT_MISSING")
            missing_artifact_count += 1
            scores.append(0.0)
            continue

        observed_metrics: dict[str, ArtifactMetricObservation] = {}
        for metric in slot.metric_scores:
            if not isinstance(metric, ArtifactMetricObservation) or not isinstance(
                metric.metric_id, str
            ):
                raise OSWorldArtifactStateEvaluationError("artifact metric schema 无效")
            if metric.metric_id in observed_metrics:
                raise OSWorldArtifactStateEvaluationError("artifact metric 身份重复")
            observed_metrics[metric.metric_id] = metric

        expected_metrics = {metric.metric_id: metric for metric in slot_rule.metrics}
        if set(observed_metrics) != set(expected_metrics):
            raise OSWorldArtifactStateEvaluationError("artifact metric 闭集不完整")
        for metric_id, metric_rule in expected_metrics.items():
            score = observed_metrics[metric_id].score
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not 0.0 <= float(score) <= 1.0
            ):
                raise OSWorldArtifactStateEvaluationError("artifact metric 分数无效")
            score_value = float(score)
            scores.append(score_value)
            if score_value < metric_rule.score_threshold - 1e-9:
                reasons.append("METRIC_BELOW_THRESHOLD")
                failed_metric_count += 1

    passed = not reasons
    return _SingleVMArtifactEvaluation(
        passed=passed,
        score=min(scores) if scores else 0.0,
        reason_codes=tuple(dict.fromkeys(reasons)),
        missing_artifact_count=missing_artifact_count,
        failed_metric_count=failed_metric_count,
    )
