"""无需二次模型调用的确定性 exact answer 评价工具。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from paraguibench.evaluation.answers import (
    EvaluationContractError,
    evaluate_qa_answer,
    extract_last_complete_answer as _extract_last_complete_answer,
)


@dataclass(frozen=True)
class ExactAnswerEvaluation:
    """保存一次 exact answer 评价的最小结构化结果。"""

    passed: bool
    score: float
    match_type: str


def extract_last_complete_answer(text: str | None) -> str | None:
    """提取模型输出中最后一个完整的 ``<answer>`` 标签。

    输入参数：
        text：可能包含零个或多个 answer 标签的模型完整输出；``None`` 视为空。
    输出返回值：
        存在完整标签时返回最后一项的去空白内容；不存在时返回 ``None``。
        末尾未闭合标签不会覆盖较早的完整答案。
    """

    return _extract_last_complete_answer(text)


def evaluate_exact_answer(
    task: Mapping[str, Any],
    model_output: str | None,
) -> ExactAnswerEvaluation:
    """按 task 声明的主答案和别名执行确定性 exact 评价。

    输入参数：
        task：包含 ``answer``、可选 ``accepted_answers`` 以及 exact
            ``answer_match_mode`` 的 canonical task。
        model_output：Agent 最终输出；若含完整 answer 标签，则仅采用最后一项。
    输出返回值：
        通过时 score 为 1.0，否则为 0.0；``match_type`` 只说明主答案、
        别名或未匹配，不复制 gold 文本。
    异常：
        EvaluationContractError：模式、主答案或别名字段不满足确定性契约。
    """

    mode = str(task.get("answer_match_mode") or "").strip().casefold()
    if mode not in {"exact", "strict_exact"}:
        raise EvaluationContractError(
            "exact 兼容入口只支持 exact answer_match_mode"
        )
    result = evaluate_qa_answer(task, model_output)
    return ExactAnswerEvaluation(
        passed=result.passed,
        score=result.score,
        match_type=result.match_type,
    )
