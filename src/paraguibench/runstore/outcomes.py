"""RunStore 终态、得分与失败阶段的共享完整性约束。"""

from __future__ import annotations

import math

from .contracts import (
    AttemptFailureStage,
    EvaluationOutcome,
    ExecutionOutcome,
)

_SCORING_OUTCOMES = {
    EvaluationOutcome.PASSED,
    EvaluationOutcome.FAILED,
}
_TERMINAL_EXECUTION_OUTCOMES = {
    ExecutionOutcome.SUCCEEDED,
    ExecutionOutcome.FAILED,
    ExecutionOutcome.TIMED_OUT,
    ExecutionOutcome.CANCELLED,
    ExecutionOutcome.INFRA_ERROR,
}
_TERMINAL_EVALUATION_OUTCOMES = {
    EvaluationOutcome.NOT_REQUESTED,
    EvaluationOutcome.PASSED,
    EvaluationOutcome.FAILED,
    EvaluationOutcome.ERROR,
    EvaluationOutcome.UNAVAILABLE,
}


def validate_terminal_outcomes(
    *,
    execution_outcome: ExecutionOutcome,
    evaluation_outcome: EvaluationOutcome,
) -> None:
    """验证不可变 Attempt summary 只使用终态枚举。

    输入参数：
        execution_outcome：Agent/环境执行结果。
        evaluation_outcome：任务评价结果。
    输出返回值：
        无；两者均为终态时正常返回。
    异常：
        TypeError：输入不是相应枚举。
        ValueError：输入仍是 queued/preparing/running/pending 过程态。
    """

    if not isinstance(execution_outcome, ExecutionOutcome):
        raise TypeError("execution_outcome must be ExecutionOutcome")
    if not isinstance(evaluation_outcome, EvaluationOutcome):
        raise TypeError("evaluation_outcome must be EvaluationOutcome")
    if execution_outcome not in _TERMINAL_EXECUTION_OUTCOMES:
        raise ValueError("execution_outcome must be terminal")
    if evaluation_outcome not in _TERMINAL_EVALUATION_OUTCOMES:
        raise ValueError("evaluation_outcome must be terminal")


def validate_evaluation_score(
    evaluation_outcome: EvaluationOutcome,
    score: float | None,
) -> float | None:
    """验证评价终态与 score 是双向一致的不可歧义组合。

    输入参数：
        evaluation_outcome：评价协议的状态或终态枚举。
        score：评价器给出的可空数值。
    输出返回值：
        ``None`` 或规范化后的有限 ``float``，供写入端与读取端共同使用。
    异常：
        TypeError：outcome 类型不正确，或 score 是布尔/非数值。
        ValueError：评分终态缺 score、非评分终态携带 score，或数值非有限。
    """

    if not isinstance(evaluation_outcome, EvaluationOutcome):
        raise TypeError("evaluation_outcome must be EvaluationOutcome")
    if score is None:
        if evaluation_outcome in _SCORING_OUTCOMES:
            raise ValueError("PASSED/FAILED evaluation score must be present")
        return None
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("score must be a finite number or None")
    normalized = float(score)
    if not math.isfinite(normalized):
        raise ValueError("score must be finite")
    if evaluation_outcome not in _SCORING_OUTCOMES:
        raise ValueError(
            "score must be None unless evaluation outcome is PASSED or FAILED"
        )
    return normalized


def validate_failure_stage(
    *,
    execution_outcome: ExecutionOutcome,
    evaluation_outcome: EvaluationOutcome,
    failure_stage: AttemptFailureStage,
) -> None:
    """验证保留失败阶段与执行/评价终态不存在语义矛盾。

    输入参数：
        execution_outcome：Agent 与环境的最终执行状态。
        evaluation_outcome：评价协议最终状态。
        failure_stage：由 runtime 控制、不可由 evaluator details 覆盖的阶段。
    输出返回值：
        无；合法组合正常返回。
    异常：
        TypeError：任一输入不是相应枚举。
        ValueError：成功、Agent 失败、评价器错误或基础设施错误与阶段矛盾。
    """

    if not isinstance(execution_outcome, ExecutionOutcome):
        raise TypeError("execution_outcome must be ExecutionOutcome")
    if not isinstance(evaluation_outcome, EvaluationOutcome):
        raise TypeError("evaluation_outcome must be EvaluationOutcome")
    if not isinstance(failure_stage, AttemptFailureStage):
        raise TypeError("failure_stage must be AttemptFailureStage")

    environment_stages = {
        AttemptFailureStage.ENVIRONMENT_START,
        AttemptFailureStage.ENVIRONMENT_PREPARE,
        AttemptFailureStage.ENVIRONMENT_CLOSE,
    }
    agent_failure_outcomes = {
        ExecutionOutcome.FAILED,
        ExecutionOutcome.TIMED_OUT,
        ExecutionOutcome.CANCELLED,
    }
    has_system_failure = (
        execution_outcome in agent_failure_outcomes
        or execution_outcome is ExecutionOutcome.INFRA_ERROR
        or evaluation_outcome is EvaluationOutcome.ERROR
    )

    if failure_stage is AttemptFailureStage.NOT_FAILED:
        if has_system_failure:
            raise ValueError("failure_stage contradicts failed outcome")
        return
    if failure_stage is AttemptFailureStage.UNKNOWN:
        if not has_system_failure:
            raise ValueError("unknown failure_stage requires failed outcome")
        return
    if failure_stage in environment_stages:
        if execution_outcome is not ExecutionOutcome.INFRA_ERROR:
            raise ValueError("environment failure_stage requires INFRA_ERROR")
        return
    if failure_stage is AttemptFailureStage.AGENT_RUN:
        if execution_outcome not in agent_failure_outcomes:
            raise ValueError("agent failure_stage contradicts execution outcome")
        return
    if failure_stage is AttemptFailureStage.EVALUATOR_EVALUATE:
        if (
            execution_outcome is not ExecutionOutcome.SUCCEEDED
            or evaluation_outcome is not EvaluationOutcome.ERROR
        ):
            raise ValueError("evaluator failure_stage contradicts outcomes")
        return
    raise ValueError("failure_stage 无效")


def default_failure_stage(
    *,
    execution_outcome: ExecutionOutcome,
    evaluation_outcome: EvaluationOutcome,
) -> AttemptFailureStage:
    """为未显式提供阶段的调用方生成保守且不伪造细节的阶段。

    输入参数：
        execution_outcome：Agent 与环境最终执行状态。
        evaluation_outcome：评价协议最终状态。
    输出返回值：
        没有系统失败时返回 ``NOT_FAILED``；存在失败但来源未知时返回
        ``UNKNOWN``。精确阶段必须由掌握生命周期的 runtime 显式传入。
    """

    has_system_failure = (
        execution_outcome
        in {
            ExecutionOutcome.FAILED,
            ExecutionOutcome.TIMED_OUT,
            ExecutionOutcome.CANCELLED,
            ExecutionOutcome.INFRA_ERROR,
        }
        or evaluation_outcome is EvaluationOutcome.ERROR
    )
    return (
        AttemptFailureStage.UNKNOWN
        if has_system_failure
        else AttemptFailureStage.NOT_FAILED
    )
