"""旧 ``exact_answer`` 入口与完整 QA evaluator 的兼容测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.answers import evaluate_qa_answer
from paraguibench.evaluation.exact_answer import evaluate_exact_answer


@pytest.mark.parametrize(
    ("task", "output"),
    [
        (
            {
                "answer": "report.docx",
                "accepted_answers": ["final report.docx"],
                "answer_match_mode": "exact",
            },
            "<answer>report.pdf</answer>",
        ),
        (
            {
                "answer": "Architectural Blueprints—The “4+1” View Model",
                "accepted_answers": [],
                "answer_match_mode": "exact",
            },
            '<answer>"architectural blueprints-the \\"4+1\\" view model"</answer>',
        ),
        (
            {
                "answer": "primary",
                "accepted_answers": ["alias"],
                "answer_match_mode": "exact",
            },
            "<answer>draft</answer><answer>alias</answer>",
        ),
    ],
)
def test_exact_entrypoint_matches_full_evaluator(
    task: dict[str, object],
    output: str,
) -> None:
    """验证旧 exact 入口和新完整入口产生相同公开结果。

    输入参数：
        task：pytest 注入的 exact 任务。
        output：pytest 注入的模型输出。
    输出返回值：
        无；passed、score、match_type 三个兼容字段必须完全相同。
    """

    old_entrypoint = evaluate_exact_answer(task, output)
    full_evaluator = evaluate_qa_answer(task, output)

    assert (
        old_entrypoint.passed,
        old_entrypoint.score,
        old_entrypoint.match_type,
    ) == (
        full_evaluator.passed,
        full_evaluator.score,
        full_evaluator.match_type,
    )
