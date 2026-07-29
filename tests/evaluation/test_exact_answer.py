"""确定性 exact answer 评价链的回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

from paraguibench.evaluation.exact_answer import (
    evaluate_exact_answer,
    extract_last_complete_answer,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_answer_extraction_uses_last_complete_tag_and_ignores_trailing_draft() -> None:
    """验证多轮输出只采用最后一个完整 answer 标签。

    输入参数：
        无；合成输出包含两个完整标签和一个末尾未闭合草稿。
    输出返回值：
        无；提取结果必须是第二个完整标签的去空白内容。
    """

    model_output = (
        "<answer>first draft</answer>\n"
        "Correction: <ANSWER>\nfinal answer\n</ANSWER>\n"
        "<answer>unfinished"
    )

    assert extract_last_complete_answer(model_output) == "final answer"


def test_representative_file_search_task_accepts_primary_and_declared_aliases() -> None:
    """验证首个 E2E 代表任务的主答案与全部声明别名均确定性通过。

    输入参数：
        无；读取 release-v1 的 FileSearch-Readonly-001 canonical task。
    输出返回值：
        无；每个声明候选经 answer 标签提交后都必须得到 1.0。
    """

    task_path = (
        REPO_ROOT
        / "benchmark"
        / "tasks"
        / "InformationRetrieval-FileSearch-Readonly-001.json"
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    candidates = [task["answer"], *task["accepted_answers"]]

    for candidate in candidates:
        result = evaluate_exact_answer(
            task,
            f"<answer>{candidate}</answer>",
        )
        assert result.passed is True
        assert result.score == 1.0


def test_exact_answer_does_not_collapse_different_file_extensions() -> None:
    """验证 exact 模式不会把不同扩展名折叠为同一文件。

    输入参数：
        无；合成 gold 和预测仅扩展名不同。
    输出返回值：
        无；预测必须失败并得到零分。
    """

    task = {
        "answer_match_mode": "exact",
        "answer": "report.docx",
        "accepted_answers": ["final report.docx"],
    }

    result = evaluate_exact_answer(
        task,
        "<answer>report.pdf</answer>",
    )

    assert result.passed is False
    assert result.score == 0.0


def test_exact_answer_preserves_legacy_punctuation_canonicalization() -> None:
    """验证 exact 模式兼容排版标点、外层引号和冒号空格差异。

    输入参数：
        无；gold 使用 Unicode 长横线/弯引号，预测使用 ASCII 和外层引号。
    输出返回值：
        无；只改变排版而语义文本完全一致时必须通过。
    """

    task = {
        "answer_match_mode": "exact",
        "answer": (
            "Architectural Blueprints—The “4+1” View Model: "
            "Software Architecture"
        ),
        "accepted_answers": [],
    }

    result = evaluate_exact_answer(
        task,
        (
            "<answer>'architectural blueprints-the \"4+1\" view model : "
            "software architecture'</answer>"
        ),
    )

    assert result.passed is True
    assert result.score == 1.0
