"""OSWorld artifact-state 纯评价协议测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from paraguibench.evaluation.osworld.artifact_state import (
    ARTIFACT_STATE_PROTOCOL_ID,
    OSWORLD_ARTIFACT_STATE_TASK_RULES,
    ArtifactStateTaskRule,
    OSWorldArtifactStateEvaluationError,
    evaluate_artifact_state_observations,
)
from paraguibench.integrations.osworld.artifact_contracts import (
    ArtifactMetricObservation,
    ArtifactSlotObservation,
    ArtifactStateObservation,
)
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)


_EXPECTED_RULE_IDENTITIES = {
    "Operation-FileOperate-BatchOperation-001": (
        "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
        "ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
        "28fdb8cb9b84390cfd642e1670d15aa4a5179a6931fa8986495fdd8bece2501c",
    ),
    "Operation-FileOperate-BatchOperation-003": (
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "0456405408bdb3d305b10dac904cba7fbc556f041417bef5530387a736cfd517",
    ),
    "Operation-FileOperate-CombinationDocs-009": (
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "bc73a485042a3878b972e5fa14b9841cef85cfc79ef6c42274c5b68aaef1670b",
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "1e04563701fde1335a57c6540c5e9919472fd36b1d2ad1c0d5ae75fb5a1b1387",
    ),
    "Operation-FileOperate-CombinationDocs-011": (
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "846c0629ec2dde2f18a34807e9c0b899260fff5de22fd0cd710d5df3170e94f7",
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "4780cfb96a299a1e8b30ab369fe767150164ee6140dd747ca7e017ecbe8bc948",
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        "3d514057-efd2-44b9-98dd-4b092ac2828a",
        "7e287123-70ca-47b9-8521-47db09b69b14",
        "99468cac2c1677f2ddda08f8289b97890f01cf39a24417668c494b77e52c4ed3",
    ),
    "Operation-FileOperate-CombinationDocs-014": (
        "881deb30-9549-4583-a841-8270c65f2a17",
        "881deb30-9549-4583-a841-8270c65f2a17",
        "f8bb09a70d6733f65bbe1e03e6d8c7a7366671cff70bf688450bba34fbcd809d",
    ),
    "Operation-FileOperate-CombinationDocs-015": (
        "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "df67aebb-fb3a-44fd-b75b-51b6012df509",
        "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c",
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        "e9e7bcf6-92da-4ff0-aaea-821099370093",
        "c7c1e4c3-9e92-4eba-a4b8-689953975ea4",
        "8ff91da03ef3013c0abe4bac318a6c9ddaa5a6271cf6ed4652ffe7f8b6f73539",
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        "51d7a7fe-e659-4de0-8345-c2c04da90373",
        "da52d699-e8d2-4dc5-9191-a2199e0b6a9b",
        "8485b90d63965980bae26b44093cafa2c4dbd4b1971354f9b9b14dd12b7ed6a1",
    ),
    "Operation-FileOperate-SearchAndWrite-005": (
        "dce61462-cf48-42d9-9466-5a0171aa5d12",
        "67890eb6-6ce5-4c00-9e3d-fb4972699b06",
        "f031f50bac3ab93f3dd1894b9cea737a2246c798ba2daf6a2b777c0239365855",
    ),
    "Operation-FileOperate-SearchAndWrite-009": (
        "14b28a49-e101-4458-835e-2067823ddefb",
        "3e3fc409-bff3-4905-bf16-c968eee3f807",
        "8a440569b160bd2b7295ec4b006a83e002e2b578559c06a4f96d8265902189bf",
    ),
    "Operation-FileOperate-Settings-001": (
        "9b5220d5-f1f0-4db9-902d-ad41aae4d775",
        "47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5",
        "5f3ebcf626c74ac25b31c54c186166064c8a62edec23a87efbf1655a854ff66d",
    ),
    "Operation-WebOperate-SearchAndWrite-001": (
        "d017201e-a098-46ab-86be-6c99d263ecff",
        "d1acdb87-bb67-4f30-84aa-990e56a09c92",
        "2262ca74a553975a89efff303a8731a9cafee598f9a0e2174562fa2f034e35c4",
    ),
}

_EXPECTED_RULE_SEMANTICS = {
    "Operation-FileOperate-BatchOperation-001": (
        (
            "renamed_picture_set",
            "directory-json-state",
            (("check_direct_json_object", "mountain-file-hash-name-map.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-BatchOperation-003": (
        (
            "chapter_pdf_archive",
            "zip-pdf-bundle",
            (("compare_archive", "pdf-chapter-archive.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-009": (
        (
            "presentation_with_notes",
            "pptx",
            (("compare_pptx_files", "speaker-notes.no-shape-no-bullets.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        (
            "graded_workbook",
            "xlsx",
            (("compare_table", "sheet-data.first-sheet.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-011": (
        (
            "problem_invoice",
            "pdf",
            (("compare_pdfs", "problem-invoice-content.v1", 1.0),),
        ),
        (
            "problematic_directory_membership",
            "directory-listing",
            (("check_include_exclude", "problematic-invoice-membership.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        (
            "apa_references_document",
            "docx",
            (("compare_references", "apa7-references.content-only.base-0_6.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        (
            "grf_workbook_bundle",
            "xlsx-csv-bundle",
            (("compare_table", "grf-sheet-print.sheet1.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-014": (
        (
            "ecs_workbook_bundle",
            "xlsx-csv-bundle",
            (("compare_table", "supported-rate-sheet-print.sheet1.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-CombinationDocs-015": (
        (
            "bibtex_output",
            "bibtex-text",
            (("compare_text_file", "bibtex.ignore-blanks.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        (
            "professor_contact_workbook",
            "xlsx",
            (("compare_table", "sheet-data.first-sheet.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        (
            "book_result_document",
            "docx",
            (("compare_docx_files", "docx-content.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-005": (
        (
            "acl_awards_workbook",
            "xlsx",
            (("compare_table", "sheet-data.first-sheet.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-009": (
        (
            "movies_workbook",
            "xlsx",
            (("compare_table", "sheet-data.named-unseen-movies.v1", 1.0),),
        ),
    ),
    "Operation-FileOperate-Settings-001": (
        (
            "slide_background_image",
            "pptx-background-image",
            (("compare_images", "slide-index-1.frame-00-08.v1", 0.90),),
        ),
    ),
    "Operation-WebOperate-SearchAndWrite-001": (
        (
            "restaurant_contact_workbook",
            "xlsx",
            (("compare_table", "sheet-fuzzy.restaurant-contacts.v1", 1.0),),
        ),
    ),
}


def _observation_for_rule(
    rule: ArtifactStateTaskRule,
    *,
    statuses: dict[str, str] | None = None,
    scores: dict[tuple[str, str], float] | None = None,
) -> ArtifactStateObservation:
    """根据可信规则构造一台 VM 的合成 artifact observation。

    输入参数：
        rule：要绑定的 canonical task rule。
        statuses：可选的 ``slot_id -> status`` 覆盖。
        scores：可选的 ``(slot_id, metric_id) -> score`` 覆盖。
    输出返回值：
        默认所有槽位可用且满分的不可变 observation。
    """

    status_overrides = statuses or {}
    score_overrides = scores or {}
    slots: list[ArtifactSlotObservation] = []
    for slot_rule in rule.artifact_slots:
        status = status_overrides.get(slot_rule.slot_id, "available")
        metrics = (
            tuple(
                ArtifactMetricObservation(
                    metric_id=metric.metric_id,
                    score=score_overrides.get(
                        (slot_rule.slot_id, metric.metric_id),
                        1.0,
                    ),
                )
                for metric in slot_rule.metrics
            )
            if status == "available"
            else ()
        )
        slots.append(
            ArtifactSlotObservation(
                slot_id=slot_rule.slot_id,
                status=status,
                metric_scores=metrics,
            )
        )
    return ArtifactStateObservation(
        rule_id=rule.rule_id,
        source_contract_sha256=rule.source_contract_sha256,
        evidence_spec_sha256=rule.evidence_spec_sha256,
        artifact_slots=tuple(slots),
    )


def test_rule_catalog_freezes_all_fifteen_canonical_source_identities() -> None:
    """验证 15 个 canonical task 精确绑定最终 evaluator 身份。

    输入参数：
        无；预期表同时固定外层 evaluator UUID、源 OSWorld task
        UUID 与完整 evaluator 对象的 canonical SHA-256。
    输出返回值：
        无；断言规则目录既不缺任务，也没有额外 legacy 任务。
    """

    actual = {
        task_id: (
            rule.source_evaluator_id,
            rule.source_task_id,
            rule.source_contract_sha256,
        )
        for task_id, rule in OSWORLD_ARTIFACT_STATE_TASK_RULES.items()
    }

    assert actual == _EXPECTED_RULE_IDENTITIES


def test_rule_catalog_freezes_all_artifact_slots_metrics_and_thresholds() -> None:
    """验证每个任务的 artifact 家族、metric contract 与阈值都被固定。

    输入参数：
        无；将规则目录投影为不含路径和 gold 内容的语义表。
    输出返回值：
        无；断言 15 个任务的槽位闭集与最终源 evaluator 一致。
    """

    actual = {
        task_id: tuple(
            (
                slot.slot_id,
                slot.artifact_kind,
                tuple(
                    (
                        metric.metric_id,
                        metric.contract_id,
                        metric.score_threshold,
                    )
                    for metric in slot.metrics
                ),
            )
            for slot in rule.artifact_slots
        )
        for task_id, rule in OSWORLD_ARTIFACT_STATE_TASK_RULES.items()
    }

    assert actual == _EXPECTED_RULE_SEMANTICS


def test_one_complete_artifact_observation_passes_its_pinned_rule() -> None:
    """验证一台 VM 的完整 artifact 快照可按固定规则通过。

    输入参数：
        无；从可信规则目录取山峰图片重命名任务，构造单台 VM
        的完整、满分 observation。
    输出返回值：
        无；断言纯评价结果使用版本化协议并满分通过。
    """

    task_id = "Operation-FileOperate-BatchOperation-001"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    observation = ArtifactStateObservation(
        rule_id=rule.rule_id,
        source_contract_sha256=rule.source_contract_sha256,
        evidence_spec_sha256=rule.evidence_spec_sha256,
        artifact_slots=(
            ArtifactSlotObservation(
                slot_id=rule.artifact_slots[0].slot_id,
                status="available",
                metric_scores=(
                    ArtifactMetricObservation(
                        metric_id=rule.artifact_slots[0].metrics[0].metric_id,
                        score=1.0,
                    ),
                ),
            ),
        ),
    )

    result = evaluate_artifact_state_observations(task_id, (observation,))

    assert result.protocol_id == ARTIFACT_STATE_PROTOCOL_ID
    assert result.passed is True
    assert result.score == 1.0
    assert result.reason_codes == ()
    assert result.evaluated_vm_count == 1


def test_observation_binds_the_canonical_evidence_spec_digest() -> None:
    """验证纯评价器同时绑定规则、源 contract 和取证规格摘要。

    输入参数：
        无；从两个公共 catalog 取同一任务，构造携带
        ``evidence_spec_sha256`` 的完整单 VM observation。
    输出返回值：
        无；断言 rule 中的摘要与 canonical spec 一致，且观测
        可满分通过。
    """

    task_id = "Operation-FileOperate-BatchOperation-001"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    evidence_spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]
    slot = rule.artifact_slots[0]
    observation = ArtifactStateObservation(
        rule_id=rule.rule_id,
        source_contract_sha256=rule.source_contract_sha256,
        evidence_spec_sha256=evidence_spec.evidence_spec_sha256,
        artifact_slots=(
            ArtifactSlotObservation(
                slot_id=slot.slot_id,
                status="available",
                metric_scores=(
                    ArtifactMetricObservation(
                        metric_id=slot.metrics[0].metric_id,
                        score=1.0,
                    ),
                ),
            ),
        ),
    )

    assert rule.evidence_spec_sha256 == evidence_spec.evidence_spec_sha256
    assert (
        evaluate_artifact_state_observations(
            task_id,
            (observation,),
        ).passed
        is True
    )


def test_wrong_evidence_spec_digest_is_evaluator_error_without_echo() -> None:
    """验证过期 collector 生成的 observation 不能被新规则静默接受。

    输入参数：
        无；复制一个完整 observation，仅替换为不匹配的
        合成 spec SHA。
    输出返回值：
        无；断言纯评价器返回 evaluator ERROR，且异常不回显
        observation 中的摘要。
    """

    task_id = "Operation-FileOperate-CombinationDocs-015"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    complete = _observation_for_rule(rule)
    wrong_digest = "0" * 64

    with pytest.raises(OSWorldArtifactStateEvaluationError) as caught:
        evaluate_artifact_state_observations(
            task_id,
            (replace(complete, evidence_spec_sha256=wrong_digest),),
        )

    assert wrong_digest not in str(caught.value)


@pytest.mark.parametrize("task_id", tuple(_EXPECTED_RULE_IDENTITIES))
def test_every_task_accepts_one_whole_complete_vm(task_id: str) -> None:
    """验证 15 条任务规则各自接受单台 VM 的完整满分快照。

    输入参数：
        task_id：参数化遍历的 canonical task ID。
    输出返回值：
        无；断言每个任务都满分通过且不出现 evaluator error。
    """

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    result = evaluate_artifact_state_observations(
        task_id,
        (_observation_for_rule(rule),),
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.task_rule_id == rule.rule_id
    assert result.evaluator_error_vm_count == 0


@pytest.mark.parametrize("task_id", tuple(_EXPECTED_RULE_IDENTITIES))
def test_every_task_treats_wrong_but_parseable_artifact_as_agent_failure(
    task_id: str,
) -> None:
    """验证 15 个任务的可读但语义错误 artifact 均正常记零分。

    输入参数：
        task_id：参数化遍历的 canonical task ID。
    输出返回值：
        无；将首个 metric 分数置于阈值以下，断言返回 Agent
        FAIL 而非 evaluator ERROR。
    """

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    first_slot = rule.artifact_slots[0]
    first_metric = first_slot.metrics[0]
    wrong_score = max(0.0, first_metric.score_threshold - 0.01)
    result = evaluate_artifact_state_observations(
        task_id,
        (
            _observation_for_rule(
                rule,
                scores={(first_slot.slot_id, first_metric.metric_id): wrong_score},
            ),
        ),
    )

    assert result.passed is False
    assert result.score == wrong_score
    assert result.reason_codes == ("METRIC_BELOW_THRESHOLD",)
    assert result.failed_metric_count == 1


@pytest.mark.parametrize("task_id", tuple(_EXPECTED_RULE_IDENTITIES))
def test_every_task_treats_explicitly_missing_artifact_as_agent_failure(
    task_id: str,
) -> None:
    """验证 15 个任务的缺失必需 artifact 均为可评价的 Agent FAIL。

    输入参数：
        task_id：参数化遍历的 canonical task ID。
    输出返回值：
        无；首槽位显式上报 ``missing`` 后返回零分与安全原因码。
    """

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    missing_slot = rule.artifact_slots[0].slot_id
    result = evaluate_artifact_state_observations(
        task_id,
        (
            _observation_for_rule(
                rule,
                statuses={missing_slot: "missing"},
            ),
        ),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.reason_codes == ("ARTIFACT_MISSING",)
    assert result.missing_artifact_count == 1


@pytest.mark.parametrize("task_id", tuple(_EXPECTED_RULE_IDENTITIES))
@pytest.mark.parametrize(
    "status",
    ("read_error", "parse_error", "schema_error"),
)
def test_every_task_fails_closed_on_unreliable_artifact_evidence(
    task_id: str,
    status: str,
) -> None:
    """验证任务各类读取、解析或 schema 错误不会伪装成 Agent 零分。

    输入参数：
        task_id：参数化遍历的 15 个 canonical task ID。
        status：证据层显式上报的三类 evaluator 错误。
    输出返回值：
        无；断言公开入口抛出固定的 evaluator error 类型。
    """

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    broken_slot = rule.artifact_slots[0].slot_id

    with pytest.raises(OSWorldArtifactStateEvaluationError):
        evaluate_artifact_state_observations(
            task_id,
            (
                _observation_for_rule(
                    rule,
                    statuses={broken_slot: status},
                ),
            ),
        )


def test_two_slot_task_cannot_splice_passing_metrics_across_vms() -> None:
    """验证发票文件与目录成员关系必须在同一台 VM 完整通过。

    输入参数：
        无；第一台 VM 仅 PDF 指标通过，第二台 VM 仅目录指标通过。
    输出返回值：
        无；断言 any-complete 不跨 VM 拼接，整体仍为零分失败。
    """

    task_id = "Operation-FileOperate-CombinationDocs-011"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    pdf_slot, listing_slot = rule.artifact_slots
    result = evaluate_artifact_state_observations(
        task_id,
        (
            _observation_for_rule(
                rule,
                scores={
                    (listing_slot.slot_id, listing_slot.metrics[0].metric_id): 0.0,
                },
            ),
            _observation_for_rule(
                rule,
                scores={
                    (pdf_slot.slot_id, pdf_slot.metrics[0].metric_id): 0.0,
                },
            ),
        ),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.failed_metric_count == 2


def test_any_complete_accepts_one_whole_pass_even_if_another_vm_errors() -> None:
    """验证一台 VM 完整通过时，另一台证据错误不否定 any-complete。

    输入参数：
        无；构造一个 ``read_error`` 快照与一个独立满分快照。
    输出返回值：
        无；断言整体通过，但安全结果记录一台 evaluator-error VM。
    """

    task_id = "Operation-FileOperate-CombinationDocs-013"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    slot_id = rule.artifact_slots[0].slot_id
    result = evaluate_artifact_state_observations(
        task_id,
        (
            _observation_for_rule(rule, statuses={slot_id: "read_error"}),
            _observation_for_rule(rule),
        ),
    )

    assert result.passed is True
    assert result.evaluator_error_vm_count == 1
    assert result.evaluated_vm_count == 2


def test_any_complete_errors_when_no_vm_passes_and_one_is_unreliable() -> None:
    """验证没有完整通过 VM 时，任一不可靠快照使结果 fail-closed。

    输入参数：
        无；一台 VM 缺失 artifact，另一台为 ``parse_error``。
    输出返回值：
        无；断言不会因已有 Agent FAIL 快照就忽略 evaluator ERROR。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    slot_id = rule.artifact_slots[0].slot_id

    with pytest.raises(OSWorldArtifactStateEvaluationError):
        evaluate_artifact_state_observations(
            task_id,
            (
                _observation_for_rule(rule, statuses={slot_id: "missing"}),
                _observation_for_rule(rule, statuses={slot_id: "parse_error"}),
            ),
        )


def test_continuous_image_score_preserves_threshold_and_raw_score() -> None:
    """验证背景图连续指标按 0.90 阈值通过并保留原始分。

    输入参数：
        无；构造真实 8.208 秒相邻帧分数的完整 observation。
    输出返回值：
        无；断言 0.9104283157114637 通过且保留原始分，未被改写为 1。
    """

    task_id = "Operation-FileOperate-Settings-001"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    slot = rule.artifact_slots[0]
    metric = slot.metrics[0]
    calibrated_score = 0.9104283157114637
    result = evaluate_artifact_state_observations(
        task_id,
        (
            _observation_for_rule(
                rule,
                scores={(slot.slot_id, metric.metric_id): calibrated_score},
            ),
        ),
    )

    assert result.passed is True
    assert result.score == calibrated_score


def test_old_nine_second_gold_score_fails_the_calibrated_threshold() -> None:
    """验证历史 9.042 秒错误 gold 不再越过 Settings 阈值。

    输入参数：
        无；构造真实旧图相对 8.008 秒正确帧的连续分数。
    输出返回值：
        无；断言 0.7960269769984115 是 Agent FAIL，并保留原始分及
        固定低于阈值原因码。
    """

    task_id = "Operation-FileOperate-Settings-001"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    slot = rule.artifact_slots[0]
    metric = slot.metrics[0]
    calibrated_score = 0.7960269769984115
    result = evaluate_artifact_state_observations(
        task_id,
        (
            _observation_for_rule(
                rule,
                scores={(slot.slot_id, metric.metric_id): calibrated_score},
            ),
        ),
    )

    assert result.passed is False
    assert result.score == calibrated_score
    assert result.reason_codes == ("METRIC_BELOW_THRESHOLD",)


@pytest.mark.parametrize("score", (float("nan"), float("inf"), -0.01, 1.01, True))
def test_invalid_metric_score_is_evaluator_schema_error(score: object) -> None:
    """验证 NaN、无穷值、越界值和布尔值不会污染任务汇总。

    输入参数：
        score：参数化传入的非法 metric 分数。
    输出返回值：
        无；断言分数 schema 错误统一进入 evaluator ERROR。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-005"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    slot = rule.artifact_slots[0]
    metric = slot.metrics[0]
    observation = _observation_for_rule(
        rule,
        scores={(slot.slot_id, metric.metric_id): score},  # type: ignore[dict-item]
    )

    with pytest.raises(OSWorldArtifactStateEvaluationError):
        evaluate_artifact_state_observations(task_id, (observation,))


def test_omitted_slot_is_collector_error_not_agent_missing_artifact() -> None:
    """验证 collector 省略槽位与显式 ``missing`` 之间的责任边界。

    输入参数：
        无；对双槽位发票任务直接丢弃第二槽位，而不是上报
        ``ArtifactSlotObservation(status="missing")``。
    输出返回值：
        无；断言这是 observation schema 不完整导致的 evaluator ERROR。
    """

    task_id = "Operation-FileOperate-CombinationDocs-011"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    complete = _observation_for_rule(rule)
    incomplete = ArtifactStateObservation(
        rule_id=complete.rule_id,
        source_contract_sha256=complete.source_contract_sha256,
        evidence_spec_sha256=complete.evidence_spec_sha256,
        artifact_slots=complete.artifact_slots[:1],
    )

    with pytest.raises(OSWorldArtifactStateEvaluationError):
        evaluate_artifact_state_observations(task_id, (incomplete,))


def test_public_error_never_echoes_artifact_path_or_content() -> None:
    """验证规则绑定失败时不会把不可信原值写入异常。

    输入参数：
        无；把模拟私密客户机路径与文件内容放入错误 rule ID。
    输出返回值：
        无；断言公开异常仅含固定语义，不回显两段私密原值。
    """

    task_id = "Operation-FileOperate-CombinationDocs-015"
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    secret_path = "/guest-profile/Desktop/private/references.bib"
    secret_content = "private-token-and-document-content"
    observation = ArtifactStateObservation(
        rule_id=f"{secret_path}:{secret_content}",
        source_contract_sha256=rule.source_contract_sha256,
        evidence_spec_sha256=rule.evidence_spec_sha256,
        artifact_slots=(),
    )

    with pytest.raises(OSWorldArtifactStateEvaluationError) as caught:
        evaluate_artifact_state_observations(task_id, (observation,))

    rendered = str(caught.value)
    assert secret_path not in rendered
    assert secret_content not in rendered
