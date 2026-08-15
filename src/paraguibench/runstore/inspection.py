"""把持久化 Run/Attempt JSON 投影为 allowlist-only 安全诊断。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import (
    AttemptFailureStage,
    AttemptInspection,
    EvaluationOutcome,
    ExecutionOutcome,
    RunProvenanceStatus,
    RunVersionVector,
)
from .versioning import validate_run_version_vector
from .outcomes import (
    validate_evaluation_score,
    validate_failure_stage,
    validate_terminal_outcomes,
)

_VERSION_VECTOR_FIELDS = {
    "source_revision",
    "agent_code_revision",
    "evaluator_revision",
    "evaluation_protocol",
    "environment_protocol",
    "environment_revision",
}


def validate_versioned_run_manifest(
    run_manifest: Any,
    *,
    expected_run_id: str,
) -> RunVersionVector:
    """验证可继续追加 Attempt 的 schema 2.0 Run manifest。

    输入参数：
        run_manifest：从 ``run.json`` 安全解码得到的不可信对象。
        expected_run_id：由 RunStore 路径和调用参数共同确定的稳定 Run ID。
    输出返回值：
        已完成类型、字段闭集、身份和格式校验的 ``RunVersionVector``。
    异常：
        TypeError/ValueError：manifest 不是对象、不是 schema 2.0、身份错配，
            或六字段版本向量不完整；错误不会回显持久化值。
    """

    if not isinstance(run_manifest, Mapping):
        raise TypeError("run manifest 必须是 Mapping")
    if run_manifest.get("schema_version") != "2.0":
        raise ValueError("run manifest schema 无效")
    if run_manifest.get("run_id") != expected_run_id:
        raise ValueError("run manifest identity 无效")
    raw_vector = run_manifest.get("version_vector")
    if not isinstance(raw_vector, Mapping) or set(raw_vector) != (
        _VERSION_VECTOR_FIELDS
    ):
        raise ValueError("run manifest version vector fields 无效")
    try:
        vector = RunVersionVector(**dict(raw_vector))
    except TypeError as error:
        raise ValueError("run manifest version vector 无效") from error
    try:
        validate_run_version_vector(vector)
    except (TypeError, ValueError) as error:
        raise ValueError("run manifest version vector 无效") from error
    return vector


def project_attempt_inspection(
    *,
    summary: Any,
    run_manifest: Any,
    expected_run_id: str,
    expected_task_id: str,
    expected_attempt_id: str,
) -> AttemptInspection:
    """从不可信 JSON object 生成严格字段白名单诊断。

    输入参数：
        summary：Attempt ``summary.json`` 解码结果。
        run_manifest：所属 ``run.json`` 解码结果；legacy Run 也必须有显式
            schema 1.0 身份记录。
        expected_run_id：由安全路径确定的 Run ID。
        expected_task_id：由安全路径确定的 Benchmark Task ID。
        expected_attempt_id：由安全路径确定的 Attempt ID。
    输出返回值：
        仅含固定枚举、有限 score 和经校验版本向量的 ``AttemptInspection``。
    异常：
        ValueError/TypeError：终态、score 或 versioned manifest 结构无效；
            错误不回显 JSON 值。
    """

    if not isinstance(summary, Mapping):
        raise TypeError("attempt summary 必须是 Mapping")
    _validate_attempt_identity(
        summary,
        expected_run_id=expected_run_id,
        expected_task_id=expected_task_id,
        expected_attempt_id=expected_attempt_id,
        label="attempt summary",
    )
    execution_raw = summary.get("execution")
    evaluation_raw = summary.get("evaluation")
    if not isinstance(execution_raw, Mapping) or not isinstance(
        evaluation_raw,
        Mapping,
    ):
        raise ValueError("attempt summary outcome 结构无效")
    try:
        execution_outcome = ExecutionOutcome(execution_raw.get("outcome"))
        evaluation_outcome = EvaluationOutcome(evaluation_raw.get("outcome"))
    except (TypeError, ValueError) as error:
        raise ValueError("attempt summary outcome 无效") from error
    validate_terminal_outcomes(
        execution_outcome=execution_outcome,
        evaluation_outcome=evaluation_outcome,
    )
    provenance_status, version_vector = _project_version_vector(
        run_manifest,
        expected_run_id=expected_run_id,
    )
    score = validate_evaluation_score(
        evaluation_outcome,
        evaluation_raw.get("score"),
    )
    failure_stage = _project_failure_stage(
        summary,
        provenance_status=provenance_status,
    )
    validate_failure_stage(
        execution_outcome=execution_outcome,
        evaluation_outcome=evaluation_outcome,
        failure_stage=failure_stage,
    )
    return AttemptInspection(
        execution_outcome=execution_outcome,
        evaluation_outcome=evaluation_outcome,
        score=score,
        failure_stage=failure_stage,
        provenance_status=provenance_status,
        version_vector=version_vector,
    )


def _project_failure_stage(
    summary: Mapping[str, Any],
    *,
    provenance_status: RunProvenanceStatus,
) -> AttemptFailureStage:
    """读取 runtime 保留阶段；仅对显式 legacy Run 兼容旧 details 字段。

    输入参数：
        summary：已验证身份的 Attempt summary。
        provenance_status：所属 Run 是 schema 2.0 或显式 schema 1.0。
    输出返回值：
        严格枚举阶段；绝不返回或回显原始自由文本。
    异常：
        ValueError：versioned summary 缺少保留字段，或字段不是已知枚举。
    """

    if provenance_status is RunProvenanceStatus.VERSIONED:
        if "failure_stage" not in summary:
            raise ValueError("versioned attempt summary failure_stage 缺失")
        value = summary.get("failure_stage")
    else:
        details = summary.get("details")
        if not isinstance(details, Mapping) or "failure_stage" not in details:
            return AttemptFailureStage.NOT_FAILED
        value = details.get("failure_stage")
    try:
        stage = AttemptFailureStage(value)
    except (TypeError, ValueError):
        raise ValueError("attempt summary failure_stage 无效") from None
    return stage


def validate_attempt_identity_record(
    record: Any,
    *,
    expected_run_id: str,
    expected_task_id: str,
    expected_attempt_id: str,
) -> None:
    """验证 ``attempt.json`` 与其安全路径表示同一个 schema 1.0 Attempt。

    输入参数：
        record：从 ``attempt.json`` 安全读取的不可信对象。
        expected_run_id：路径所属 Run ID。
        expected_task_id：路径所属 Benchmark Task ID。
        expected_attempt_id：路径所属 Attempt ID。
    输出返回值：
        无；全部 schema 与身份字段一致时正常返回。
    异常：
        TypeError/ValueError：记录缺失对象结构、schema 或任一身份错配。
    """

    _validate_attempt_identity(
        record,
        expected_run_id=expected_run_id,
        expected_task_id=expected_task_id,
        expected_attempt_id=expected_attempt_id,
        label="attempt identity",
    )


def _validate_attempt_identity(
    record: Any,
    *,
    expected_run_id: str,
    expected_task_id: str,
    expected_attempt_id: str,
    label: str,
) -> None:
    """交叉验证 Attempt 记录的 schema 与三层稳定身份。

    输入参数：
        record：``attempt.json`` 或 ``summary.json`` 解码对象。
        expected_run_id：安全路径中的 Run ID。
        expected_task_id：安全路径中的 task ID。
        expected_attempt_id：安全路径中的 Attempt ID。
        label：不含外部值的错误区域名称。
    输出返回值：
        无；schema 1.0 且三层身份完全一致时正常返回。
    异常：
        TypeError/ValueError：对象、schema 或身份无效。
    """

    if not isinstance(record, Mapping):
        raise TypeError(f"{label} 必须是 Mapping")
    if record.get("schema_version") != "1.0":
        raise ValueError(f"{label} schema 无效")
    if (
        record.get("run_id") != expected_run_id
        or record.get("task_id") != expected_task_id
        or record.get("attempt_id") != expected_attempt_id
    ):
        raise ValueError(f"{label} identity 无效")


def _project_version_vector(
    run_manifest: Any,
    *,
    expected_run_id: str,
) -> tuple[RunProvenanceStatus, RunVersionVector | None]:
    """读取新 Run 的严格版本向量，并显式标记旧 Run。

    输入参数：
        run_manifest：``run.json`` 解码结果；旧 Run 必须是身份一致的
            schema 1.0 manifest，缺失文件不能自动伪装成 legacy。
        expected_run_id：由安全路径确定的 Run ID。
    输出返回值：
        ``(provenance_status, version_vector)``。schema 2.0 必须完整合法；
        缺失或旧 schema 只标记 ``LEGACY_UNVERSIONED``，不伪造历史版本。
    异常：
        ValueError/TypeError：声称 schema 2.0 但向量缺失、字段越界或格式无效。
    """

    if not isinstance(run_manifest, Mapping):
        raise TypeError("run manifest 必须是 Mapping")
    schema_version = run_manifest.get("schema_version")
    if schema_version == "1.0":
        if run_manifest.get("run_id") != expected_run_id:
            raise ValueError("run manifest identity 无效")
        return RunProvenanceStatus.LEGACY_UNVERSIONED, None
    if schema_version != "2.0":
        raise ValueError("run manifest schema 无效")
    vector = validate_versioned_run_manifest(
        run_manifest,
        expected_run_id=expected_run_id,
    )
    return RunProvenanceStatus.VERSIONED, vector
