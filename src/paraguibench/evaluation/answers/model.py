"""QA 答案评价的稳定结果与契约错误类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


class EvaluationContractError(ValueError):
    """表示 task 声明的答案评价契约无效或当前不受支持。"""


@dataclass(frozen=True)
class AnswerEvaluation:
    """保存一次 QA 最终答案评价的脱敏结构化结果。

    字段说明：
        passed：答案是否达到该模式的通过条件。
        score：位于 ``[0, 1]`` 的确定性分数。
        match_type：只描述匹配路径，不含 gold 或模型原文。
        details：可选的计数型诊断信息；禁止存放任务答案或模型原文。
    """

    passed: bool
    score: float
    match_type: str
    details: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
