"""ParaGUIBench QA 最终答案评价公开接口。

依赖关系：
    ``evaluator`` 依赖 ``canonical``、``extraction`` 与 ``model``；
    其余三个模块只依赖 Python 标准库且彼此无环。
"""

from paraguibench.evaluation.answers.evaluator import evaluate_qa_answer
from paraguibench.evaluation.answers.extraction import (
    extract_last_complete_answer,
)
from paraguibench.evaluation.answers.model import (
    AnswerEvaluation,
    EvaluationContractError,
)

__all__ = [
    "AnswerEvaluation",
    "EvaluationContractError",
    "evaluate_qa_answer",
    "extract_last_complete_answer",
]
