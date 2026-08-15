"""Kimi planner、Qwen worker、单 VM 租约与现有评价器的纵向测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paraguibench.agents.systems.paragui import (
    GUIWorkerParaGUIAdapter,
    KimiOpenAIPlanningBackend,
    KimiPlannerConfig,
    ParaGUIAgentSystem,
    StructuredParaGUIPlanner,
)
from paraguibench.agents.workers.gui import GUIAction
from paraguibench.agents.workers.qwen import QwenGUIWorker
from paraguibench.benchmark import PreparedTask, build_agent_task_view
from paraguibench.runstore import (
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
)
from paraguibench.runtime.attempt_runner import AttemptRunner
from paraguibench.runtime.evaluators import AnswerTaskEvaluator
from paraguibench.runtime.single_vm_lease import (
    SingleVMEnvironmentLeaseAdapter,
)
from tests.runstore._audit import synthetic_run_version_vector


class _QueuedCompletions:
    """模拟 Kimi 外部 API 边界，依次返回计划与汇总调用。"""

    def __init__(self, responses: list[Any]) -> None:
        """保存响应队列与请求记录。

        输入参数：
            responses：按调用顺序排列的 OpenAI-compatible 响应。
        输出返回值：
            无。
        """

        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        """记录一次模型请求并返回下一个合成响应。

        输入参数：
            request：Kimi backend 发出的模型、messages 与 tool schema。
        输出返回值：
            当前队首的唯一 function-call 响应。
        """

        self.requests.append(request)
        return self._responses.pop(0)


class _QwenBoundaryModel:
    """模拟 Qwen 外部视觉动作边界，并记录实际 worker 指令。"""

    def __init__(self, instructions: list[str]) -> None:
        """绑定跨 worker 共享的指令记录。

        输入参数：
            instructions：每个独立 Qwen worker 的最终指令列表。
        输出返回值：
            无。
        """

        self._instructions = instructions

    def next_action(self, **request: Any) -> GUIAction:
        """根据当前 subtask 指令返回一步 finished evidence。

        输入参数：
            request：通用 GUI 循环传入的指令、截图和有界历史。
        输出返回值：
            不执行 guest 命令的 terminal ``GUIAction``。
        """

        instruction = request["instruction"]
        self._instructions.append(instruction)
        if instruction.startswith("Inspect the architecture diagram"):
            evidence = "diagram evidence"
        elif instruction.startswith("Compare the candidate papers"):
            evidence = "paper comparison evidence"
        else:
            evidence = "verified paper3"
        return GUIAction("finished", {"content": evidence})


class _Controller:
    """为 GUI 循环提供合成截图的外部桌面边界。"""

    def get_screenshot(self) -> bytes:
        """返回非空合成截图。

        输入参数：
            无。
        输出返回值：
            由测试注入尺寸读取器处理的 bytes。
        """

        return b"synthetic-screenshot"


class _Environment:
    """记录 AttemptRunner 生命周期的单桌面外部边界。"""

    def __init__(self) -> None:
        """初始化 controller 与生命周期记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.controller = _Controller()
        self.calls: list[str] = []

    def start(self) -> None:
        """记录唯一环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("start")

    def prepare(self, task: dict[str, Any]) -> None:
        """验证只有可信环境侧能看到 gold，并记录准备。

        输入参数：
            task：AttemptRunner 传入的完整 canonical task。
        输出返回值：
            无。
        """

        assert task["answer"] == "paper3"
        self.calls.append("prepare")

    def close(self) -> None:
        """记录唯一环境清理。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("close")


def _tool_response(name: str, arguments: str) -> Any:
    """构造含唯一 Kimi function call 的合成响应。

    输入参数：
        name：强制调用的 function 名。
        arguments：该 function 的 JSON object 字符串。
    输出返回值：
        兼容 OpenAI SDK 字段访问的合成对象。
    """

    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    tool_calls=[
                        SimpleNamespace(
                            function=SimpleNamespace(
                                name=name,
                                arguments=arguments,
                            )
                        )
                    ]
                )
            )
        ]
    )


def _prepared_task() -> PreparedTask:
    """构造带现有 exact QA 评价协议的合成任务。

    输入参数：
        无。
    输出返回值：
        trusted、gold-free Agent view 和脱敏 audit 分离的 ``PreparedTask``。
    """

    task = {
        "task_id": "synthetic-kimi-qwen-task",
        "task_uid": "synthetic-kimi-qwen-uid",
        "task_type": "QA",
        "task_source": "self",
        "task_tag": "FileSearch",
        "instruction": "Identify the source paper and answer with tags.",
        "answer": "paper3",
        "accepted_answers": [],
        "answer_match_mode": "exact",
    }
    return PreparedTask(
        trusted_task=task,
        agent_task=build_agent_task_view(task),
        audit_metadata={
            "release_id": "synthetic-release",
            "canonical_task_sha256": "0" * 64,
            "task_id": task["task_id"],
            "materialization": {
                "schema_version": 1,
                "environment_binding_names": [],
                "fixture_refs": [],
            },
        },
    )


def test_kimi_qwen_single_vm_attempt_passes_existing_evaluator_in_plan_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证真实内部组件以单 VM 串行完成并通过现有评价器。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根目录。
        monkeypatch：注入只在当前进程存在的合成 planner key。
    输出返回值：
        无；执行/评价为 SUCCEEDED/PASSED，后续 worker 依次收到
        全部前驱 evidence，且环境只启动和关闭一次。
    """

    completions = _QueuedCompletions(
        [
            _tool_response(
                "emit_sequential_plan",
                json.dumps(
                    {
                        "subtasks": [
                            {
                                "id": "inspect-diagram",
                                "instruction": ("Inspect the architecture diagram."),
                            },
                            {
                                "id": "compare-papers",
                                "instruction": ("Compare the candidate papers."),
                            },
                            {
                                "id": "verify-answer",
                                "instruction": "Verify the final candidate.",
                            },
                        ]
                    }
                ),
            ),
            _tool_response(
                "emit_final_answer",
                '{"answer":"<answer>paper3</answer>"}',
            ),
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("TEST_KIMI_API_KEY", "synthetic-secret")
    planner = StructuredParaGUIPlanner(
        backend=KimiOpenAIPlanningBackend(
            KimiPlannerConfig(
                base_url="https://planner.example/v1",
                api_key_env="TEST_KIMI_API_KEY",
                max_subtasks=3,
            ),
            client_factory=lambda **_: client,
        )
    )
    worker_instructions: list[str] = []

    def build_worker() -> QwenGUIWorker:
        """构造只替换 Qwen 外部模型边界的真实 GUI worker。

        输入参数：
            无；共享上述指令记录。
        输出返回值：
            使用真实 ``GUIActionLoop`` 且一步终止的 Qwen worker。
        """

        return QwenGUIWorker(
            model=_QwenBoundaryModel(worker_instructions),
            max_steps=1,
            post_action_delay=0,
            screenshot_history_limit=0,
            image_size_reader=lambda _: (1920, 1080),
        )

    agent = ParaGUIAgentSystem(
        planner=planner,
        worker=GUIWorkerParaGUIAdapter(worker_factory=build_worker),
        max_workers=1,
    )
    raw_environment = _Environment()
    environment = SingleVMEnvironmentLeaseAdapter(raw_environment)
    prepared_task = _prepared_task()
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-kimi-qwen-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-kimi-qwen-001",
        task_id=prepared_task.trusted_task["task_id"],
        attempt_id="attempt-001",
        task_record=prepared_task.audit_metadata,
    )

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=prepared_task,
        environment=environment,
        agent=agent,
        evaluator=AnswerTaskEvaluator(),
    )

    assert result.execution_outcome is ExecutionOutcome.SUCCEEDED
    assert result.evaluation_outcome is EvaluationOutcome.PASSED
    assert result.score == 1.0
    assert raw_environment.calls == ["start", "prepare", "close"]
    assert len(worker_instructions) == 3
    assert "[inspect-diagram] diagram evidence" in worker_instructions[1]
    assert "[inspect-diagram] diagram evidence" in worker_instructions[2]
    assert "[compare-papers] paper comparison evidence" in worker_instructions[2]
    assert [
        request["tools"][0]["function"]["name"] for request in completions.requests
    ] == ["emit_sequential_plan", "emit_final_answer"]
    summary = json.loads((attempt.path / "summary.json").read_text(encoding="utf-8"))
    assert summary["execution"]["outcome"] == "SUCCEEDED"
    assert summary["evaluation"]["outcome"] == "PASSED"
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert "synthetic-secret" not in persisted
    assert "planner.example" not in persisted
    assert "paper3" not in persisted
