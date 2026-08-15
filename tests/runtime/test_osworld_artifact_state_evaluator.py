"""OSWorld artifact-state runtime adapter 与 registry 绑定测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from paraguibench import evaluation as public_evaluation
from paraguibench.benchmark.release import load_release_task
from paraguibench.evaluation.osworld import (
    ARTIFACT_STATE_PROTOCOL_ID,
    OSWORLD_ARTIFACT_STATE_TASK_RULES,
    ArtifactMetricObservation,
    ArtifactSlotObservation,
    ArtifactStateObservation,
    OSWorldArtifactStateEvaluationError,
)
from paraguibench.runtime.evaluators import (
    OSWorldArtifactStateTaskEvaluator,
    UnsupportedTaskEvaluatorError,
    build_task_evaluator,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _ArtifactStateEnvironment:
    """为 runtime adapter 提供不落盘的合成 artifact observation。"""

    def __init__(self, observations: tuple[object, ...]) -> None:
        """保存已完整生成的逐 VM observation。

        输入参数：
            observations：评价阶段应返回的不可变快照元组。
        输出返回值：
            无；同时初始化调用记录。
        """

        self._observations = observations
        self.requests: list[tuple[str, str]] = []

    def osworld_artifact_state_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """按 task 与协议身份返回当前 Attempt 的冻结证据。

        输入参数：
            task_id：runtime adapter 固定的 canonical task ID。
            protocol_id：runtime adapter 固定的 artifact-state 协议 ID。
        输出返回值：
            构造时提供的逐 VM observation 元组。
        """

        self.requests.append((task_id, protocol_id))
        return self._observations


def _task(task_id: str) -> dict[str, object]:
    """从可信规则目录构造最小 canonical artifact task 元数据。

    输入参数：
        task_id：15 条 artifact-state 规则之一。
    输出返回值：
        与发布 task 一致的来源、类型与 evaluator path 映射。
    """

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    return {
        "task_id": task_id,
        "task_source": "OSWorld",
        "task_type": "OSWorld脚本",
        "evaluator_path": (f"eval/osworld_scripts/{rule.source_evaluator_id}.json"),
    }


def _passing_observation(task_id: str) -> ArtifactStateObservation:
    """构造单台 VM 独立满足全部槽位与 metric 的快照。

    输入参数：
        task_id：需要生成证据的 canonical task ID。
    输出返回值：
        与规则身份、contract 摘要和 metric 闭集精确匹配的快照。
    """

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    return ArtifactStateObservation(
        rule_id=rule.rule_id,
        source_contract_sha256=rule.source_contract_sha256,
        evidence_spec_sha256=rule.evidence_spec_sha256,
        artifact_slots=tuple(
            ArtifactSlotObservation(
                slot_id=slot.slot_id,
                status="available",
                metric_scores=tuple(
                    ArtifactMetricObservation(
                        metric_id=metric.metric_id,
                        score=1.0,
                    )
                    for metric in slot.metrics
                ),
            )
            for slot in rule.artifact_slots
        ),
    )


def test_registry_builds_artifact_adapter_and_returns_safe_details() -> None:
    """验证已注册 artifact task 通过纯评价器产生脱敏 runtime 结果。

    输入参数：
        无；使用山峰文件重命任务与单台 VM 满分 observation。
    输出返回值：
        无；断言 registry 返回 artifact adapter，且 details 只含协议、
        规则身份、原因码和计数。
    """

    task_id = "Operation-FileOperate-BatchOperation-001"
    task = _task(task_id)
    environment = _ArtifactStateEnvironment((_passing_observation(task_id),))
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
    )

    result = evaluator.evaluate(
        task,
        "/guest-profile/Desktop/private-output",
        environment,
    )

    assert isinstance(evaluator, OSWorldArtifactStateTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "protocol_id": ARTIFACT_STATE_PROTOCOL_ID,
        "task_rule_id": OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id].rule_id,
        "reason_codes": (),
        "evaluated_vm_count": 1,
        "evaluator_error_vm_count": 0,
        "missing_artifact_count": 0,
        "failed_metric_count": 0,
    }
    assert "/guest-profile" not in repr(result.details)
    assert "private-output" not in repr(result.details)
    assert environment.requests == [(task_id, ARTIFACT_STATE_PROTOCOL_ID)]


def test_top_level_evaluation_exports_reference_the_same_artifact_protocol() -> None:
    """验证顶层 evaluation 入口公开同一个版本化 artifact 协议。

    输入参数：
        无；同时读取顶层和 ``evaluation.osworld`` 的公开符号。
    输出返回值：
        无；断言协议 ID、规则目录和纯评价入口不存在重复实例。
    """

    assert public_evaluation.ARTIFACT_STATE_PROTOCOL_ID == (ARTIFACT_STATE_PROTOCOL_ID)
    assert public_evaluation.OSWORLD_ARTIFACT_STATE_TASK_RULES is (
        OSWORLD_ARTIFACT_STATE_TASK_RULES
    )


@pytest.mark.parametrize(
    "task_id",
    tuple(OSWORLD_ARTIFACT_STATE_TASK_RULES),
)
def test_registry_binds_all_fifteen_rules_by_exact_source_metadata(
    task_id: str,
) -> None:
    """验证规则目录中 15 个任务均可被 registry 精确选中。

    输入参数：
        task_id：参数化遍历的 artifact-state canonical task ID。
    输出返回值：
        无；只验证 adapter 构造，不启动 VM 也不伪造 guest evidence。
    """

    evaluator = build_task_evaluator(
        load_release_task(REPOSITORY_ROOT, task_id),
        evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
    )

    assert isinstance(evaluator, OSWorldArtifactStateTaskEvaluator)


@pytest.mark.parametrize(
    "overrides",
    (
        {"task_source": "self"},
        {"task_type": "QA"},
        {"evaluator_path": "eval/osworld_scripts/wrong.json"},
        {"task_id": "Operation-FileOperate-Unknown-999"},
    ),
)
def test_registry_rejects_artifact_task_metadata_mismatch(
    overrides: dict[str, object],
) -> None:
    """验证 task ID、来源、类型与 evaluator path 必须四向精确匹配。

    输入参数：
        overrides：故意篡改的一项 canonical metadata。
    输出返回值：
        无；registry 在任何环境或 artifact 读取前失败关闭。
    """

    task = _task("Operation-FileOperate-BatchOperation-001")
    task.update(overrides)

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            task,
            evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
        )


def test_registry_does_not_treat_legacy_protocol_as_runtime_ready() -> None:
    """验证纯评价注册不会隐式移除 manifest 中的 legacy blocker。

    输入参数：
        无；对已有纯规则的 canonical task 仍传入 legacy protocol。
    输出返回值：
        无；断言 registry 拒绝 legacy 协议，不声称 guest evidence 已就绪。
    """

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            _task("Operation-FileOperate-BatchOperation-003"),
            evaluation_protocol="legacy.osworld.state.v1",
        )


def test_artifact_adapter_constructor_rejects_non_string_task_identity() -> None:
    """验证直接构造 adapter 时非字符串 task ID 也会安全失败。

    输入参数：
        无；故意传入不可哈希的 list，覆盖低层 mapping 边界。
    输出返回值：
        无；公开 adapter 应抛固定 registry 错误，不泄露或暴露
        底层 ``unhashable type`` 异常。
    """

    with pytest.raises(UnsupportedTaskEvaluatorError, match="task"):
        OSWorldArtifactStateTaskEvaluator(
            task_id=[],  # type: ignore[arg-type]
            evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
        )


def test_explicit_missing_artifact_remains_agent_failure_in_runtime() -> None:
    """验证环境明确报告缺失 artifact 时 runtime 返回 FAIL/0。

    输入参数：
        无；将 BibTeX 任务唯一槽位标记为 ``missing``。
    输出返回值：
        无；断言这是 Agent 可评价失败，详情仅包含计数。
    """

    task_id = "Operation-FileOperate-CombinationDocs-015"
    task = _task(task_id)
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    observation = ArtifactStateObservation(
        rule_id=rule.rule_id,
        source_contract_sha256=rule.source_contract_sha256,
        evidence_spec_sha256=rule.evidence_spec_sha256,
        artifact_slots=(
            ArtifactSlotObservation(
                slot_id=rule.artifact_slots[0].slot_id,
                status="missing",
            ),
        ),
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
    )

    result = evaluator.evaluate(
        task,
        "private-final-output",
        _ArtifactStateEnvironment((observation,)),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.details["reason_codes"] == ("ARTIFACT_MISSING",)
    assert result.details["missing_artifact_count"] == 1
    assert "references.bib" not in repr(result.details)


def test_unreliable_artifact_evidence_propagates_as_evaluator_error() -> None:
    """验证读取错误不会被 runtime adapter 降级为 Agent 零分。

    输入参数：
        无；将 PPTX 任务槽位显式标记为 ``read_error``。
    输出返回值：
        无；纯 evaluator 异常原样传播，供 AttemptRunner 记 ERROR/null。
    """

    task_id = "Operation-FileOperate-CombinationDocs-009"
    task = _task(task_id)
    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
    observation = ArtifactStateObservation(
        rule_id=rule.rule_id,
        source_contract_sha256=rule.source_contract_sha256,
        evidence_spec_sha256=rule.evidence_spec_sha256,
        artifact_slots=(
            ArtifactSlotObservation(
                slot_id=rule.artifact_slots[0].slot_id,
                status="read_error",
            ),
        ),
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
    )

    with pytest.raises(OSWorldArtifactStateEvaluationError):
        evaluator.evaluate(
            task,
            "/guest-profile/Desktop/private.pptx",
            _ArtifactStateEnvironment((observation,)),
        )


def test_adapter_rejects_missing_or_non_tuple_environment_evidence() -> None:
    """验证 adapter 不会从普通 OSWorld 环境猜测或伪造 guest evidence。

    输入参数：
        无；分别使用无接口对象和返回 list 的不合规环境。
    输出返回值：
        无；两种情形均抛出固定 TypeError，不进入纯评价。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-003"
    task = _task(task_id)
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
    )

    with pytest.raises(TypeError, match="observation"):
        evaluator.evaluate(task, "Done", object())

    class _ListEnvironment:
        """返回可变 list 以验证 runtime 输入边界。"""

        def osworld_artifact_state_observations(
            self,
            task_id: str,
            protocol_id: str,
        ) -> list[object]:
            """返回故意不合规的 observation 容器。

            输入参数：
                task_id/protocol_id：adapter 请求的固定身份，此处不使用。
            输出返回值：
                空 list，用于制造输入类型错误。
            """

            del task_id, protocol_id
            return []

    with pytest.raises(TypeError, match="tuple"):
        evaluator.evaluate(task, "Done", _ListEnvironment())


def test_adapter_rechecks_task_identity_before_reading_environment() -> None:
    """验证 registry 构造后 task 被替换时不会读取错误 artifact 证据。

    输入参数：
        无；用任务 A 构造 adapter，评价时改传任务 B。
    输出返回值：
        无；在 environment seam 之前拒绝身份错配，且没有证据请求。
    """

    first_id = "Operation-FileOperate-BatchOperation-001"
    second_id = "Operation-FileOperate-BatchOperation-003"
    evaluator = build_task_evaluator(
        _task(first_id),
        evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
    )
    environment = _ArtifactStateEnvironment((_passing_observation(first_id),))

    with pytest.raises(UnsupportedTaskEvaluatorError, match="contract"):
        evaluator.evaluate(_task(second_id), "Done", environment)

    assert environment.requests == []
