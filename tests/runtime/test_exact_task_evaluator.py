"""runtime exact evaluator adapter 的统一结果契约测试。"""

from __future__ import annotations

import pytest

from paraguibench.runtime.evaluators import (
    AnswerTaskEvaluator,
    ExactTaskEvaluator,
    UnsupportedTaskEvaluatorError,
    build_task_evaluator,
)


def test_exact_evaluator_adapter_returns_runtime_evaluation_without_gold() -> None:
    """验证确定性评价只返回 match type、通过状态和分数。

    输入参数：
        无；使用合成 canonical task 与完整 answer 标签。
    输出返回值：
        无；details 不得复制主答案或别名。
    """

    task = {
        "answer_match_mode": "exact",
        "answer": "private-gold",
        "accepted_answers": ["alias"],
    }

    result = ExactTaskEvaluator().evaluate(
        task,
        "<answer>alias</answer>",
        object(),
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {"match_type": "strict_exact_via_alias"}
    assert "private-gold" not in repr(result)


def test_answer_evaluator_adapter_supports_numeric_without_persisting_gold() -> None:
    """验证通用 QA adapter 支持 numeric 且诊断中不包含答案原文。

    输入参数：
        无；使用数值等价但文本表示不同的合成任务和模型输出。
    输出返回值：
        无；统一 runtime 结果必须通过，且 details 只含安全匹配类型。
    """

    task = {
        "task_type": "QA",
        "task_source": "self",
        "answer_match_mode": "numeric",
        "answer": "2000",
        "accepted_answers": [],
    }

    result = AnswerTaskEvaluator().evaluate(
        task,
        "<answer>2000.0</answer>",
        object(),
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {"match_type": "numeric_value"}
    assert "2000" not in repr(result.details)


def test_task_evaluator_registry_accepts_answer_qa_and_rejects_webmall() -> None:
    """验证 runtime registry 只把已迁移的非 WebMall QA 路由到答案评价器。

    输入参数：
        无；分别提供已迁移 QA 和仍需浏览器状态评价的 WebMall QA。
    输出返回值：
        无；前者返回通用 adapter，后者以类型安全异常拒绝，且异常不回显
        任务答案。
    """

    migrated_task = {
        "task_type": "QA",
        "task_source": "self",
        "answer_match_mode": "exact",
        "answer": "private-answer",
    }
    webmall_task = {
        "task_type": "QA",
        "task_source": "WebMall",
        "answer": "private-webmall-state",
    }

    assert isinstance(build_task_evaluator(migrated_task), AnswerTaskEvaluator)
    with pytest.raises(UnsupportedTaskEvaluatorError) as captured:
        build_task_evaluator(webmall_task)
    assert "private-webmall-state" not in str(captured.value)
