"""不运行 Agent 的 OSWorld artifact component live candidate。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.osworld import (
    OSWORLD_ARTIFACT_STATE_TASK_RULES,
    OSWorldArtifactStateEvaluation,
    evaluate_artifact_state_observations,
)
from paraguibench.runstore import (
    AttemptFailureStage,
    AttemptInspection,
    EvaluationOutcome,
    ExecutionOutcome,
    RunProvenanceStatus,
    RunStore,
    RunVersionVector,
    TaskAttempt,
)
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
    OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL,
    OSWorldArtifactComponentEnvironmentProof,
    osworld_artifact_environment_protocol,
)
from paraguibench.runtime.osworld_environment import OSWorldTaskEnvironment


class OSWorldArtifactComponentValidationError(RuntimeError):
    """表示专属 component candidate 生命周期无法可信闭合。"""

    code = "OSWORLD_ARTIFACT_COMPONENT_VALIDATION_INVALID"

    def __init__(self) -> None:
        """构造不保留路径、artifact、gold 或底层异常的固定错误。

        输入参数：无。
        输出返回值：无；异常文本固定为稳定错误码。
        """

        super().__init__(self.code)


_VALIDATION_PROCESS_CAPABILITY = object()
_OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_CAPABILITY = object()
_ARTIFACT_FAILURE_REASON_CODES = frozenset(
    {"ARTIFACT_MISSING", "METRIC_BELOW_THRESHOLD"}
)


def _validate_candidate_task_evaluation(
    task_id: str,
    evaluation: OSWorldArtifactStateEvaluation,
) -> float:
    """验证 pure evaluator 返回的完整结构与成败事实一致。

    输入参数：task_id 为当前 candidate 任务；evaluation 必须是
        正式 artifact-state evaluator 的精确冻结返回类型。
    输出返回：结构可信时返回经验证的有限任务得分。
        此得分只证明 formal evaluator 确实完成，不是 component
        candidate 的得分；component 只在后续 production proof 闭合后通过。
    异常：OSWorldArtifactComponentValidationError：协议、规则、类型、
        计数、原因码或得分存在任何不一致。
    """

    rule = OSWORLD_ARTIFACT_STATE_TASK_RULES.get(task_id)
    if rule is None or type(evaluation) is not OSWorldArtifactStateEvaluation:
        raise OSWorldArtifactComponentValidationError
    score = evaluation.score
    counts = (
        evaluation.evaluated_vm_count,
        evaluation.evaluator_error_vm_count,
        evaluation.missing_artifact_count,
        evaluation.failed_metric_count,
    )
    if (
        evaluation.protocol_id != OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL
        or evaluation.task_rule_id != rule.rule_id
        or type(evaluation.passed) is not bool
        or isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not 0.0 <= float(score) <= 1.0
        or not isinstance(evaluation.reason_codes, tuple)
        or any(type(reason) is not str for reason in evaluation.reason_codes)
        or len(set(evaluation.reason_codes)) != len(evaluation.reason_codes)
        or any(type(count) is not int or count < 0 for count in counts)
        or evaluation.evaluated_vm_count != 1
        or evaluation.evaluator_error_vm_count != 0
    ):
        raise OSWorldArtifactComponentValidationError

    normalized_score = float(score)
    if evaluation.passed is True:
        if (
            normalized_score != 1.0
            or evaluation.reason_codes != ()
            or evaluation.missing_artifact_count != 0
            or evaluation.failed_metric_count != 0
        ):
            raise OSWorldArtifactComponentValidationError
        return normalized_score

    reasons = frozenset(evaluation.reason_codes)
    if (
        normalized_score >= 1.0
        or not reasons
        or not reasons.issubset(_ARTIFACT_FAILURE_REASON_CODES)
        or evaluation.missing_artifact_count + evaluation.failed_metric_count <= 0
        or ("ARTIFACT_MISSING" in reasons)
        is not (evaluation.missing_artifact_count > 0)
        or ("METRIC_BELOW_THRESHOLD" in reasons)
        is not (evaluation.failed_metric_count > 0)
    ):
        raise OSWorldArtifactComponentValidationError
    return normalized_score


@dataclass(frozen=True, slots=True, repr=False)
class OSWorldArtifactComponentValidationResult:
    """保存 candidate 同进程内可交给 receipt builder 的脱敏事实。"""

    run_id: str
    task_id: str
    attempt_id: str
    environment_proof: OSWorldArtifactComponentEnvironmentProof
    evaluator_gold_completed: bool
    inspection: AttemptInspection
    _process_capability: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """验证结果只表达 versioned SUCCEEDED/PASSED candidate。

        输入参数：无；读取冻结 proof、gold 完成标记与安全 inspection。
        输出返回值：全部成功事实严格成立时正常返回。
        异常：OSWorldArtifactComponentValidationError：类型、终态、得分或
            candidate/environment 协议不匹配。
        """

        if (
            self._process_capability is not _VALIDATION_PROCESS_CAPABILITY
            or not isinstance(self.inspection, AttemptInspection)
        ):
            raise OSWorldArtifactComponentValidationError
        vector = self.inspection.version_vector
        try:
            safe_run_id = validate_identifier("run_id", self.run_id)
            safe_task_id = validate_identifier("task_id", self.task_id)
            safe_attempt_id = validate_identifier("attempt_id", self.attempt_id)
        except (TypeError, ValueError):
            raise OSWorldArtifactComponentValidationError from None
        if (
            safe_run_id != self.run_id
            or safe_task_id != self.task_id
            or safe_attempt_id != self.attempt_id
            or self.task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
            or not isinstance(
                self.environment_proof,
                OSWorldArtifactComponentEnvironmentProof,
            )
            or self.environment_proof.task_id != self.task_id
            or self.environment_proof.evaluator_gold_completed is not True
            or self.evaluator_gold_completed is not True
            or self.inspection.execution_outcome is not ExecutionOutcome.SUCCEEDED
            or self.inspection.evaluation_outcome is not EvaluationOutcome.PASSED
            or self.inspection.score != 1.0
            or self.inspection.failure_stage is not AttemptFailureStage.NOT_FAILED
            or self.inspection.provenance_status is not RunProvenanceStatus.VERSIONED
            or not isinstance(vector, RunVersionVector)
            or vector.evaluation_protocol
            != OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL
            or vector.environment_protocol
            != osworld_artifact_environment_protocol(self.task_id)
            or not (
                vector.source_revision
                == vector.agent_code_revision
                == vector.evaluator_revision
            )
        ):
            raise OSWorldArtifactComponentValidationError


def run_osworld_artifact_component_validation(
    *,
    store: RunStore,
    attempt: TaskAttempt,
    prepared_task: PreparedTask,
    environment: OSWorldTaskEnvironment,
) -> OSWorldArtifactComponentValidationResult:
    """永久拒绝旧的可注入低层 component validation 入口。

    输入参数：保留 store/attempt/prepared_task/environment 仅为兼容旧
        调用签名；所有值均不被访问，不启动 VM 也不写 RunStore。
    输出返回：永不返回；只有不可注入的 top-level candidate
        可以模块私有 capability 调用内部 runner。
    异常：OSWorldArtifactComponentValidationError：每次调用都固定抛出。
    """

    raise OSWorldArtifactComponentValidationError


def _run_osworld_artifact_component_validation(
    *,
    store: RunStore,
    attempt: TaskAttempt,
    prepared_task: PreparedTask,
    environment: OSWorldTaskEnvironment,
    _candidate_capability: object,
) -> OSWorldArtifactComponentValidationResult:
    """执行 setup→getter→pure gold evaluator→owned close 的专属 Attempt。

    输入参数：store/attempt 为已创建的 candidate RunStore-v2 Attempt；
        prepared_task 必须来自可信 release 三投影；environment 必须是精确
        生产 ``OSWorldTaskEnvironment`` 类型。函数不接受 Agent、final text、
        evaluator 或 component proof 注入。
    输出返回值：环境关闭且 RunStore 安全 inspection 为
        SUCCEEDED/PASSED 后，返回同进程脱敏 validation result。
    异常：OSWorldArtifactComponentValidationError：类型、任务、生命周期、
        getter、纯 evaluator、关闭或 RunStore 终态任一步失败；底层值不回显。
    """

    if (
        _candidate_capability is not _OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_CAPABILITY
        or not isinstance(store, RunStore)
        or not isinstance(attempt, TaskAttempt)
        or not isinstance(prepared_task, PreparedTask)
        or type(environment) is not OSWorldTaskEnvironment
    ):
        raise OSWorldArtifactComponentValidationError
    task = prepared_task.trusted_task
    task_id = task.get("task_id")
    if (
        not isinstance(task_id, str)
        or task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
        or attempt.task_id != task_id
        or prepared_task.agent_task.get("task_id") != task_id
    ):
        raise OSWorldArtifactComponentValidationError

    phase = AttemptFailureStage.ENVIRONMENT_START
    execution_outcome = ExecutionOutcome.INFRA_ERROR
    evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
    evaluation_score: float | None = None
    system_failed = False
    candidate_passed = False
    evaluator_gold_completed = False
    try:
        environment.start()
        phase = AttemptFailureStage.ENVIRONMENT_PREPARE
        environment.prepare(task)
        phase = AttemptFailureStage.EVALUATOR_EVALUATE
        execution_outcome = ExecutionOutcome.SUCCEEDED
        observations = environment.osworld_artifact_state_observations(
            task_id,
            OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL,
        )
        evaluation = evaluate_artifact_state_observations(
            task_id,
            observations,
        )
        evaluation_score = _validate_candidate_task_evaluation(
            task_id,
            evaluation,
        )
        candidate_passed = evaluation.passed is True
        evaluation_outcome = (
            EvaluationOutcome.PASSED if candidate_passed else EvaluationOutcome.FAILED
        )
        evaluator_gold_completed = candidate_passed
    except Exception:
        system_failed = True
        if phase is AttemptFailureStage.EVALUATOR_EVALUATE:
            execution_outcome = ExecutionOutcome.SUCCEEDED
            evaluation_outcome = EvaluationOutcome.ERROR
        else:
            execution_outcome = ExecutionOutcome.INFRA_ERROR
            evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
        evaluation_score = None
    finally:
        try:
            environment.close()
        except Exception:
            system_failed = True
            phase = AttemptFailureStage.ENVIRONMENT_CLOSE
            execution_outcome = ExecutionOutcome.INFRA_ERROR
            evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
            evaluation_score = None

    proof: OSWorldArtifactComponentEnvironmentProof | None = None
    if candidate_passed and not system_failed:
        try:
            proof = environment.osworld_artifact_component_validation_proof(
                task_id,
                OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL,
            )
        except Exception:
            system_failed = True
            phase = AttemptFailureStage.ENVIRONMENT_CLOSE
            execution_outcome = ExecutionOutcome.INFRA_ERROR
            evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
            evaluation_score = None

    try:
        store.finish_attempt(
            attempt=attempt,
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
            score=evaluation_score,
            failure_stage=(phase if system_failed else AttemptFailureStage.NOT_FAILED),
            details={},
        )
    except Exception:
        raise OSWorldArtifactComponentValidationError from None
    if (
        system_failed
        or candidate_passed is not True
        or proof is None
        or evaluator_gold_completed is not True
    ):
        raise OSWorldArtifactComponentValidationError
    try:
        inspection_before = store.inspect_attempt(
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
        )
        inspection_after = store.inspect_attempt(
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
        )
        if inspection_after != inspection_before:
            raise OSWorldArtifactComponentValidationError
        return OSWorldArtifactComponentValidationResult(
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
            environment_proof=proof,
            evaluator_gold_completed=proof.evaluator_gold_completed,
            inspection=inspection_before,
            _process_capability=_VALIDATION_PROCESS_CAPABILITY,
        )
    except OSWorldArtifactComponentValidationError:
        raise
    except Exception:
        raise OSWorldArtifactComponentValidationError from None


__all__ = [
    "OSWorldArtifactComponentValidationError",
    "OSWorldArtifactComponentValidationResult",
]
