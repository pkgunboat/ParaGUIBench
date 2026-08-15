"""共享 GUI worker 到 ParaGUI subtask 的环境租约与结果映射测试。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from paraguibench.agents.systems.paragui import GUIWorkerParaGUIAdapter
from paraguibench.agents.workers import GUIWorkerResult
from paraguibench.framework import SubtaskResult, SubtaskSpec, SubtaskStatus


class _Worker:
    """记录自包含 instruction 并返回固定 worker 终态。"""

    def __init__(self, result: GUIWorkerResult) -> None:
        """保存预设结果并初始化调用记录。

        输入参数：
            result：run 应返回的 GUIWorkerResult。
        输出返回值：
            无。
        """

        self.result = result
        self.calls: list[tuple[str, Any]] = []

    def run(self, instruction: str, environment: Any) -> GUIWorkerResult:
        """记录 worker 输入并返回预设终态。

        输入参数：
            instruction：已附加依赖 evidence 的子任务指令。
            environment：pool 租出的独占桌面。
        输出返回值：
            构造时注入的 worker 结果。
        """

        self.calls.append((instruction, environment))
        return self.result


class _Pool:
    """为每个 subtask 产生不同桌面对象的测试环境池。"""

    def __init__(self) -> None:
        """初始化租入、归还记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.entered: list[str] = []
        self.exited: list[str] = []

    @contextmanager
    def lease(self, subtask_id: str) -> Iterator[Any]:
        """返回与 subtask 身份绑定的独占环境并记录退出。

        输入参数：
            subtask_id：当前节点标识。
        输出返回值：
            contextmanager yield 的独占测试桌面。
        """

        self.entered.append(subtask_id)
        try:
            yield type("Environment", (), {"lease_id": subtask_id})()
        finally:
            self.exited.append(subtask_id)


def test_paragui_adapter_leases_environment_and_injects_dependency_evidence() -> None:
    """验证依赖按稳定顺序进入指令，且 lease 在成功后可靠归还。

    输入参数：
        无；一个依赖结果和一个成功 Qwen worker。
    输出返回值：
        无；SubtaskResult 成功并保留 worker 步数与输出。
    """

    worker = _Worker(GUIWorkerResult("evidence-b", 3, "finished"))
    adapter = GUIWorkerParaGUIAdapter(worker_factory=lambda: worker)
    dependency = SubtaskResult(
        subtask_id="source-a",
        status=SubtaskStatus.SUCCEEDED,
        output="evidence-a",
        step_count=2,
    )
    pool = _Pool()

    result = adapter.run_subtask(
        SubtaskSpec(
            "source-b",
            "Inspect the second source.",
            depends_on=("source-a",),
        ),
        (dependency,),
        pool,
    )

    assert result.status is SubtaskStatus.SUCCEEDED
    assert result.output == "evidence-b"
    assert result.step_count == 3
    assert "[source-a] evidence-a" in worker.calls[0][0]
    assert worker.calls[0][1].lease_id == "source-b"
    assert pool.entered == ["source-b"]
    assert pool.exited == ["source-b"]


def test_paragui_adapter_rejects_direct_shared_environment() -> None:
    """验证没有 lease 的单 VM 不会被 ParaGUI scheduler 并发共享。

    输入参数：
        无；传入普通 object 而不是环境池。
    输出返回值：
        无；worker 不执行，返回稳定 environment_pool_required 失败。
    """

    worker = _Worker(GUIWorkerResult("", 0, "finished"))
    result = GUIWorkerParaGUIAdapter(worker_factory=lambda: worker).run_subtask(
        SubtaskSpec("unsafe", "Inspect."),
        (),
        object(),
    )

    assert result.status is SubtaskStatus.FAILED
    assert result.failure_type == "environment_pool_required"
    assert worker.calls == []


def test_paragui_adapter_maps_qwen_non_success_terminal() -> None:
    """验证 Qwen call_user 不会被误报为成功完成的 subtask。

    输入参数：
        无；worker 请求用户输入。
    输出返回值：
        无；保留文本与步数，但状态为 FAILED。
    """

    worker = _Worker(GUIWorkerResult("Need a non-secret choice.", 4, "call_user"))
    result = GUIWorkerParaGUIAdapter(worker_factory=lambda: worker).run_subtask(
        SubtaskSpec("needs-user", "Inspect."),
        (),
        _Pool(),
    )

    assert result.status is SubtaskStatus.FAILED
    assert result.output == "Need a non-secret choice."
    assert result.step_count == 4
    assert result.failure_type == "worker_call_user"


def test_paragui_adapter_rejects_dependency_identity_or_status_mismatch() -> None:
    """验证依赖身份、顺序或成功状态不一致时不启动 worker。

    输入参数：
        无；构造与 ``depends_on`` 不匹配的失败结果。
    输出返回值：
        无；返回稳定失败类型，且不创建租约或调用 worker。
    """

    worker = _Worker(GUIWorkerResult("unused", 1, "finished"))
    pool = _Pool()
    failed_dependency = SubtaskResult(
        subtask_id="source-a",
        status=SubtaskStatus.FAILED,
        output="partial",
        step_count=1,
        failure_type="worker_failed",
    )

    result = GUIWorkerParaGUIAdapter(worker_factory=lambda: worker).run_subtask(
        SubtaskSpec(
            "source-b",
            "Inspect.",
            depends_on=("source-a",),
        ),
        (failed_dependency,),
        pool,
    )

    assert result.status is SubtaskStatus.FAILED
    assert result.failure_type == "dependency_result_mismatch"
    assert worker.calls == []
    assert pool.entered == []


def test_paragui_adapter_truncates_dependency_evidence_explicitly() -> None:
    """验证超长依赖输出保留身份并显式标记截断。

    输入参数：
        无；构造超过 20000 字符 worker 指令预算的依赖输出。
    输出返回值：
        无；worker 收到恰不超边界、保含依赖 ID 与截断标记的指令。
    """

    worker = _Worker(GUIWorkerResult("done", 1, "finished"))
    dependency = SubtaskResult(
        subtask_id="source-a",
        status=SubtaskStatus.SUCCEEDED,
        output="x" * 30_000,
        step_count=1,
    )

    result = GUIWorkerParaGUIAdapter(worker_factory=lambda: worker).run_subtask(
        SubtaskSpec(
            "source-b",
            "Inspect the result.",
            depends_on=("source-a",),
        ),
        (dependency,),
        _Pool(),
    )

    instruction = worker.calls[0][0]
    assert result.status is SubtaskStatus.SUCCEEDED
    assert len(instruction) <= 20_000
    assert "[source-a] " in instruction
    assert "<evidence_truncated>" in instruction


def test_paragui_adapter_fails_when_dependency_identity_cannot_fit() -> None:
    """验证当完整子任务指令已耗尽预算时不会静默丢弃依赖。

    输入参数：
        无；使用 20000 字符的合法 subtask instruction 和一个依赖。
    输出返回值：
        无；返回 ``instruction_budget_exceeded``，worker 与 pool 都不被调用。
    """

    worker = _Worker(GUIWorkerResult("unused", 1, "finished"))
    pool = _Pool()
    dependency = SubtaskResult(
        subtask_id="source-a",
        status=SubtaskStatus.SUCCEEDED,
        output="evidence",
        step_count=1,
    )

    result = GUIWorkerParaGUIAdapter(worker_factory=lambda: worker).run_subtask(
        SubtaskSpec(
            "source-b",
            "x" * 20_000,
            depends_on=("source-a",),
        ),
        (dependency,),
        pool,
    )

    assert result.status is SubtaskStatus.FAILED
    assert result.failure_type == "instruction_budget_exceeded"
    assert worker.calls == []
    assert pool.entered == []


def test_paragui_adapter_returns_lease_when_worker_raises() -> None:
    """验证 worker 异常不会跳过环境租约的 finally 归还。

    输入参数：
        无；注入一个在 lease 内抛出稳定异常的 worker。
    输出返回值：
        无；异常向 scheduler 传播，但 pool 的退出记录已完成。
    """

    class RaisingWorker:
        """用于验证 context manager 异常路径的 worker。"""

        def run(self, instruction: str, environment: Any) -> GUIWorkerResult:
            """在独占环境内立即抛出测试异常。

            输入参数：
                instruction：adapter 构造的子任务指令。
                environment：pool 租出的独占环境。
            输出返回值：
                不返回；始终抛出 ``RuntimeError``。
            """

            del instruction, environment
            raise RuntimeError("synthetic-worker-error")

    pool = _Pool()
    adapter = GUIWorkerParaGUIAdapter(worker_factory=RaisingWorker)

    with pytest.raises(RuntimeError, match="synthetic-worker-error"):
        adapter.run_subtask(
            SubtaskSpec("source-a", "Inspect."),
            (),
            pool,
        )

    assert pool.entered == ["source-a"]
    assert pool.exited == ["source-a"]
