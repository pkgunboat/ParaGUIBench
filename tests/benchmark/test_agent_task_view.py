"""Agent 可见 task 投影的 gold 隔离测试。"""

from __future__ import annotations

from paraguibench.benchmark import build_agent_task_view


def test_agent_task_view_excludes_all_evaluation_and_gold_fields() -> None:
    """验证 runtime 不会把 answer、expected 或 evaluator 配置交给 Agent。

    输入参数：
        无；合成 canonical task 同时包含运行输入和多类评价字段。
    输出返回值：
        无；Agent view 仅保留显式允许的任务身份、instruction 和上下文。
    """

    canonical_task = {
        "task_id": "synthetic-agent-view",
        "task_uid": "synthetic-uid",
        "task_type": "QA",
        "task_source": "self",
        "task_tag": "FileSearch",
        "instruction": "Inspect the shared folder.",
        "agent_start_context": {"active_app": "Files"},
        "answer": "private-gold",
        "accepted_answers": ["alias"],
        "expected_urls": ["webmall://store-1/product/gold"],
        "gold_manifest": "benchmark/gold/manifests/private.json",
        "evaluator_path": "evaluation/hidden.py",
        "answer_match_mode": "exact",
    }

    view = build_agent_task_view(canonical_task)

    assert view == {
        "task_id": "synthetic-agent-view",
        "task_uid": "synthetic-uid",
        "task_type": "QA",
        "task_source": "self",
        "task_tag": "FileSearch",
        "instruction": "Inspect the shared folder.",
        "agent_start_context": {"active_app": "Files"},
    }
