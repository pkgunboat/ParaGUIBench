"""公开 benchmark 中 QA answer evaluator 的真实任务覆盖测试。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from paraguibench.evaluation.answers import evaluate_qa_answer

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = REPOSITORY_ROOT / "benchmark" / "tasks"


def _load_answer_evaluator_tasks() -> list[dict[str, object]]:
    """加载由 QA answer evaluator 负责的公开任务。

    输入参数：
        无；从仓库 ``benchmark/tasks`` 扫描 canonical JSON。
    输出返回值：
        77 个 InformationRetrieval QA 与唯一 FileOperate QA 组成的稳定列表。
        WebMall 的 91 个 QA 任务不在本评价器职责内。
    """

    tasks: list[dict[str, object]] = []
    for path in sorted(TASK_ROOT.glob("*.json")):
        task = json.loads(path.read_text(encoding="utf-8"))
        task_id = str(task.get("task_id") or "")
        if task.get("task_type") != "QA":
            continue
        if task_id.startswith("InformationRetrieval-") or task_id == (
            "Operation-FileOperate-CombinationDocs-004"
        ):
            tasks.append(task)
    return tasks


def test_all_78_answer_tasks_accept_primary_and_declared_aliases() -> None:
    """验证 78 个 QA 答案任务的主答案与每个显式别名均可评价通过。

    输入参数：
        无；读取公开仓 canonical task。
    输出返回值：
        无；覆盖数量、模式分布和每个已配置候选必须符合迁移契约。
    """

    tasks = _load_answer_evaluator_tasks()
    modes = Counter(
        str(task.get("answer_match_mode") or "<implicit>")
        for task in tasks
    )

    assert len(tasks) == 78
    assert modes == {
        "exact": 39,
        "numeric": 28,
        "keyed_numeric_set": 1,
        "ordered_structured": 2,
        "<implicit>": 8,
    }
    candidate_count = 0
    for task in tasks:
        task_id = str(task["task_id"])
        candidates = [
            str(task["answer"]),
            *(
                str(alias)
                for alias in (task.get("accepted_answers") or [])
            ),
        ]
        for candidate in candidates:
            candidate_count += 1
            result = evaluate_qa_answer(
                task,
                f"<answer>{candidate}</answer>",
            )
            assert result.passed, (task_id, result.match_type)
            assert result.score == 1.0, task_id
    assert candidate_count == 149
