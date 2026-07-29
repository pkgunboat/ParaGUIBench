"""把确定性 benchmark evaluator 适配为 runtime 统一结果。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from paraguibench.evaluation.answers import evaluate_qa_answer
from paraguibench.evaluation.exact_answer import evaluate_exact_answer
from paraguibench.runtime.attempt_runner import RuntimeEvaluation


class UnsupportedTaskEvaluatorError(ValueError):
    """表示 canonical task 尚无可安全装配的原生 runtime evaluator。"""


class AnswerTaskEvaluator:
    """把完整 QA answer evaluator 接到 AttemptRunner 的统一适配器。"""

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """执行确定性 QA 评价并只返回脱敏诊断。

        输入参数：
            task：可信 canonical QA task，包含版本化 answer contract。
            final_output：Agent terminal action 返回的完整文本。
            environment：仍存活的任务环境；纯答案 evaluator 不读取。
        输出返回值：
            passed、确定性 score，以及 match_type 和计数型安全诊断。
        """

        del environment
        result = evaluate_qa_answer(task, final_output)
        return RuntimeEvaluation(
            passed=result.passed,
            score=result.score,
            details={
                "match_type": result.match_type,
                **dict(result.details),
            },
        )


class ExactTaskEvaluator:
    """把 exact answer evaluator 接到 AttemptRunner 的最小适配器。"""

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: Any,
    ) -> RuntimeEvaluation:
        """执行 deterministic exact 评价并省略全部 gold 文本。

        输入参数：
            task：可信 canonical task，包含 answer contract。
            final_output：Agent terminal action 返回的完整文本。
            environment：仍存活的任务环境；纯文本 evaluator 不读取。
        输出返回值：
            passed、0/1 score 以及仅含 match_type 的 RuntimeEvaluation。
        """

        del environment
        result = evaluate_exact_answer(task, final_output)
        return RuntimeEvaluation(
            passed=result.passed,
            score=result.score,
            details={"match_type": result.match_type},
        )


def build_task_evaluator(
    task: Mapping[str, Any],
) -> AnswerTaskEvaluator:
    """按 canonical task contract 选择已迁移的 runtime evaluator。

    输入参数：
        task：可信 canonical task；registry 只读取类型与来源标识，不读取或
            记录答案值。
    输出返回值：
        当前 78 个非 WebMall QA 共用的 ``AnswerTaskEvaluator``。
    异常：
        TypeError：task 不是 Mapping。
        UnsupportedTaskEvaluatorError：任务仍需尚未迁移的状态评价协议。
    """

    if not isinstance(task, Mapping):
        raise TypeError("task 必须是 Mapping")
    if (
        task.get("task_type") == "QA"
        and task.get("task_source") != "WebMall"
    ):
        return AnswerTaskEvaluator()
    raise UnsupportedTaskEvaluatorError("任务评价协议尚未迁移到 runtime registry")
