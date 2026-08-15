"""Kimi 结构化 planner backend 的公开行为测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from paraguibench.agents.systems.paragui import (
    KimiOpenAIPlanningBackend,
    KimiPlannerConfig,
    KimiPlanningError,
    StructuredParaGUIPlanner,
)
from paraguibench.framework import SubtaskResult, SubtaskStatus


class _Completions:
    """记录请求并返回预设 Kimi 响应的外部 API 替身。"""

    def __init__(self, response: Any) -> None:
        """保存模型响应并初始化请求记录。

        输入参数：
            response：OpenAI-compatible assistant tool call 响应。
        输出返回值：
            无。
        """

        self.response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        """记录模型请求并返回单个 assistant choice。

        输入参数：
            request：模型、messages 和成本边界。
        输出返回值：
            具有 ``choices[0].message.content`` 的合成响应。
        """

        self.requests.append(request)
        return self.response


def _tool_response(name: str, arguments: str) -> Any:
    """构造只含一个 Kimi 原生 function call 的合成响应。

    输入参数：
        name：期望的 planner function 名。
        arguments：该 function 的 JSON object 参数。
    输出返回值：
        具有唯一 ``tool_calls`` 的 SDK 形状对象。
    """

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=name,
                                arguments=arguments,
                            )
                        )
                    ],
                )
            )
        ]
    )


def test_kimi_backend_builds_a_bounded_sequential_gui_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Kimi 节点列表被收紧为单 VM 可执行的顺序 GUI 计划。

    输入参数：
        monkeypatch：提供不进入仓库的合成 planner key。
    输出返回值：
        无；公开 planner 返回两个 GUI 节点，第二个显式依赖第一个。
    """

    completions = _Completions(
        _tool_response(
            "emit_sequential_plan",
            """{"subtasks":[
        {"id":"inspect-diagram","instruction":"Inspect architecture.jpg."},
        {"id":"compare-pdfs","instruction":"Compare the PDF candidates."}
        ]}""",
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_KIMI_API_KEY", "synthetic-secret")
    backend = KimiOpenAIPlanningBackend(
        KimiPlannerConfig(
            base_url="https://gateway.example/v1",
            model="kimi-k2.6",
            api_key_env="TEST_KIMI_API_KEY",
            max_subtasks=4,
        ),
        client_factory=lambda **_: client,
    )

    plan = StructuredParaGUIPlanner(backend=backend).plan(
        {
            "task_id": "synthetic-file-search",
            "instruction": "Identify the source paper.",
        }
    )

    assert tuple(item.subtask_id for item in plan.subtasks) == (
        "inspect-diagram",
        "compare-pdfs",
    )
    assert plan.subtasks[0].depends_on == ()
    assert plan.subtasks[1].depends_on == ("inspect-diagram",)
    assert all(item.worker_role == "gui" for item in plan.subtasks)
    plan_tool = completions.requests[0]["tools"][0]
    assert (
        plan_tool["function"]["parameters"]["properties"]["subtasks"]["maxItems"] == 4
    )
    assert (
        plan_tool["function"]["parameters"]["properties"]["subtasks"]["items"][
            "properties"
        ]["id"]["pattern"]
        == "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        '{"subtasks":[{"id":"inspect diagram","instruction":"Inspect."}]}',
        (
            '{"subtasks":['
            '{"id":"same","instruction":"Inspect."},'
            '{"id":"same","instruction":"Compare."}]}'
        ),
    ],
)
def test_kimi_backend_rejects_invalid_or_duplicate_ids_before_framework(
    monkeypatch: pytest.MonkeyPatch,
    arguments: str,
) -> None:
    """验证 provider 边界用稳定异常拒绝非法或重复节点标识。

    输入参数：
        monkeypatch：提供不落盘的合成 planner key。
        arguments：包空格标识或重复标识的 function arguments。
    输出返回值：
        无；backend 在进入通用 framework 前抛出不回显原值的
        ``KimiPlanningError``。
    """

    completions = _Completions(_tool_response("emit_sequential_plan", arguments))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_KIMI_API_KEY", "synthetic-secret")
    backend = KimiOpenAIPlanningBackend(
        KimiPlannerConfig(
            base_url="https://gateway.example/v1",
            api_key_env="TEST_KIMI_API_KEY",
        ),
        client_factory=lambda **_: client,
    )

    with pytest.raises(KimiPlanningError, match="subtask id"):
        backend.create_plan(
            {
                "task_id": "synthetic-file-search",
                "instruction": "Identify the source paper.",
            }
        )


def test_kimi_backend_synthesizes_worker_evidence_into_final_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Kimi 将顺序 worker 结果聚合为 evaluator 可消费的文本。

    输入参数：
        monkeypatch：提供不落盘的合成 planner key。
    输出返回值：
        无；公开 planner ``synthesize`` 返回标准 answer tags。
    """

    completions = _Completions(
        _tool_response(
            "emit_final_answer",
            '{"answer":"<answer>paper3</answer>"}',
        )
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_KIMI_API_KEY", "synthetic-secret")
    planner = StructuredParaGUIPlanner(
        backend=KimiOpenAIPlanningBackend(
            KimiPlannerConfig(
                base_url="https://gateway.example/v1",
                api_key_env="TEST_KIMI_API_KEY",
            ),
            client_factory=lambda **_: client,
        )
    )

    final_output = planner.synthesize(
        {
            "task_id": "synthetic-file-search",
            "instruction": "Answer only with <answer>VALUE</answer>.",
        },
        (
            SubtaskResult(
                "inspect-diagram",
                SubtaskStatus.SUCCEEDED,
                "The diagram has four architectural views.",
                3,
            ),
            SubtaskResult(
                "compare-pdfs",
                SubtaskStatus.SUCCEEDED,
                "paper3 is the strongest visual match.",
                5,
            ),
        ),
    )

    assert final_output == "<answer>paper3</answer>"
