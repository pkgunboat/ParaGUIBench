"""Chrome Bookmarks 从 AttemptRunner 到 RunStore 的纵向顺序与隐私测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.osworld import (
    CHROME_BOOKMARKS_PROTOCOL_ID,
    ChromeBookmarkRecord,
    ChromeBookmarksObservation,
)
from paraguibench.runstore import (
    AttemptFailureStage,
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
)
from paraguibench.runtime.attempt_runner import (
    AgentRunResult,
    AttemptRunner,
)
from paraguibench.runtime.evaluators import build_task_evaluator
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


class _BookmarkAttemptEnvironment:
    """模拟已由 OSWorld environment 保证 reset/setup 顺序的单 VM。"""

    def __init__(
        self,
        calls: list[str],
        observation: ChromeBookmarksObservation,
    ) -> None:
        """保存生命周期记录和仅供 evaluator 使用的书签快照。

        输入参数：
            calls：测试共享的阶段记录。
            observation：Agent 后 capture 返回的完整单 VM 快照。
        输出返回值：
            无。
        """

        self.calls = calls
        self._observation = observation

    def start(self) -> None:
        """记录 VM 启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("environment.start")

    def prepare(self, task: dict[str, Any]) -> None:
        """模拟生产 environment 的 reset→task setup 契约。

        输入参数：
            task：AttemptRunner 传入的可信 canonical task。
        输出返回值：
            无。
        """

        assert task["task_id"] == "Operation-WebOperate-WebNavigate-008"
        self.calls.extend(("bookmark.reset", "task.setup"))

    def osworld_bookmark_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """在 Agent 完成后捕获仍存活 VM 的书签快照。

        输入参数：
            task_id：固定 canonical task ID。
            protocol_id：固定 Chrome Bookmarks 协议。
        输出返回值：
            单元素完整 observation tuple。
        """

        assert task_id == "Operation-WebOperate-WebNavigate-008"
        assert protocol_id == CHROME_BOOKMARKS_PROTOCOL_ID
        self.calls.append("bookmark.capture")
        return (self._observation,)

    def close(self) -> None:
        """记录 capture 完成后的 owned 环境清理。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("environment.close")


class _SensitiveBookmarkAgent:
    """返回只允许留在 evaluator 内存中的敏感最终文本。"""

    def __init__(self, calls: list[str], final_output: str) -> None:
        """保存调用记录与合成最终文本。

        输入参数：
            calls：测试共享的阶段记录。
            final_output：不得写入 RunStore 的 Agent 文本。
        输出返回值：
            无。
        """

        self.calls = calls
        self._final_output = final_output

    def run(
        self,
        task_view: dict[str, Any],
        environment: object,
    ) -> AgentRunResult:
        """确认 task setup 已结束后返回合法运行结果。

        输入参数：
            task_view：不含 evaluator/gold 的 Agent 投影。
            environment：当前已准备且未关闭的环境。
        输出返回值：
            一步完成的 ``AgentRunResult``。
        """

        del environment
        assert task_view["task_id"] == "Operation-WebOperate-WebNavigate-008"
        assert self.calls[-1] == "task.setup"
        self.calls.append("agent.run")
        return AgentRunResult(
            final_output=self._final_output,
            step_count=1,
            termination="finished",
        )


class _FailingCaptureEnvironment(_BookmarkAttemptEnvironment):
    """模拟 Agent 成功后 Bookmark capture 发生敏感底层错误。"""

    def osworld_bookmark_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """记录 capture 后抛出包含 sentinel 的合成错误。

        输入参数：
            task_id：固定 canonical task ID。
            protocol_id：固定 Chrome Bookmarks 协议。
        输出返回值：
            不返回；始终抛出 ``RuntimeError``。
        """

        assert task_id == "Operation-WebOperate-WebNavigate-008"
        assert protocol_id == CHROME_BOOKMARKS_PROTOCOL_ID
        self.calls.append("bookmark.capture")
        raise RuntimeError("PRIVATE CAPTURE ERROR SENTINEL")


def _prepared_steam_task() -> PreparedTask:
    """构造 WebNavigate-008 的可信三投影测试任务。

    输入参数：
        无。
    输出返回值：
        可由 AttemptRunner 和 Bookmark registry 消费的 ``PreparedTask``。
    """

    task_id = "Operation-WebOperate-WebNavigate-008"
    trusted = {
        "task_id": task_id,
        "task_uid": "eb1ad6e6-b3cc-49e6-a633-a012ae38f56e",
        "task_source": "",
        "task_type": "self",
        "task_tag": "WebOperate",
        "evaluator_path": "eval/webnavigate_bookmark_evaluator.py",
        "instruction": "Bookmark the requested Steam page.",
    }
    return PreparedTask(
        trusted_task=trusted,
        agent_task={
            "task_id": task_id,
            "instruction": trusted["instruction"],
        },
        audit_metadata=synthetic_task_audit(
            task_id,
            task_uid=trusted["task_uid"],
            task_type=trusted["task_type"],
            task_source=trusted["task_source"],
            task_tag=trusted["task_tag"],
        ),
    )


def test_attempt_orders_reset_setup_agent_capture_and_persists_no_state(
    tmp_path: Path,
) -> None:
    """验证严格阶段顺序及 URL、文件夹、Agent 文本全量不落盘。

    输入参数：
        tmp_path：pytest 提供的任务级 RunStore 根目录。
    输出返回值：
        无；Attempt 满分通过，capture 发生在 Agent 后/close 前，所有文件
        只含协议、规则身份、原因码与计数。
    """

    task = _prepared_steam_task()
    sensitive_url = "https://store.steampowered.com/app/1238810/_5/"
    sensitive_folder = "PRIVATE FOLDER SENTINEL"
    sensitive_final = "PRIVATE FINAL OUTPUT SENTINEL"
    calls: list[str] = []
    environment = _BookmarkAttemptEnvironment(
        calls,
        ChromeBookmarksObservation(
            records=(
                ChromeBookmarkRecord(
                    url=sensitive_url,
                    folder_path=("bookmark_bar", sensitive_folder),
                ),
            )
        ),
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-bookmark-privacy",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-bookmark-privacy",
        task_id=task.trusted_task["task_id"],
        attempt_id="attempt-001",
        task_record=task.audit_metadata,
    )
    evaluator = build_task_evaluator(
        task.trusted_task,
        evaluation_protocol=CHROME_BOOKMARKS_PROTOCOL_ID,
    )

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=task,
        environment=environment,
        agent=_SensitiveBookmarkAgent(calls, sensitive_final),
        evaluator=evaluator,
    )

    assert result.evaluation_outcome is EvaluationOutcome.PASSED
    assert result.score == 1.0
    assert calls == [
        "environment.start",
        "bookmark.reset",
        "task.setup",
        "agent.run",
        "bookmark.capture",
        "environment.close",
    ]
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for sentinel in (sensitive_url, sensitive_folder, sensitive_final):
        assert sentinel.encode("utf-8") not in persisted
    for safe_field in (
        b"paraguibench.osworld.chrome-bookmarks.v1",
        b"task_rule_id",
        b"evaluated_vm_count",
        b"expected_target_count",
        b"matched_target_count",
    ):
        assert safe_field in persisted


def test_capture_failure_is_evaluation_error_without_persisting_message(
    tmp_path: Path,
) -> None:
    """验证 capture 异常不会误记 Agent 失败或持久化底层消息。

    输入参数：
        tmp_path：pytest 提供的任务级 RunStore 根目录。
    输出返回值：
        无；AttemptRunner 重新抛错供 CLI 处理，但已提交的安全终态为
        execution SUCCEEDED、evaluation ERROR、score None。
    """

    task = _prepared_steam_task()
    calls: list[str] = []
    environment = _FailingCaptureEnvironment(
        calls,
        ChromeBookmarksObservation(records=()),
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-bookmark-capture-error",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-bookmark-capture-error",
        task_id=task.trusted_task["task_id"],
        attempt_id="attempt-001",
        task_record=task.audit_metadata,
    )
    evaluator = build_task_evaluator(
        task.trusted_task,
        evaluation_protocol=CHROME_BOOKMARKS_PROTOCOL_ID,
    )

    with pytest.raises(RuntimeError, match="PRIVATE CAPTURE ERROR"):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=task,
            environment=environment,
            agent=_SensitiveBookmarkAgent(
                calls,
                "PRIVATE FINAL OUTPUT SENTINEL",
            ),
            evaluator=evaluator,
        )

    inspection = store.inspect_attempt(
        run_id="run-bookmark-capture-error",
        task_id=task.trusted_task["task_id"],
        attempt_id="attempt-001",
    )
    assert inspection.execution_outcome is ExecutionOutcome.SUCCEEDED
    assert inspection.evaluation_outcome is EvaluationOutcome.ERROR
    assert inspection.score is None
    assert inspection.failure_stage is AttemptFailureStage.EVALUATOR_EVALUATE
    assert calls == [
        "environment.start",
        "bookmark.reset",
        "task.setup",
        "agent.run",
        "bookmark.capture",
        "environment.close",
    ]
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    assert b"PRIVATE CAPTURE ERROR SENTINEL" not in persisted
    assert b"PRIVATE FINAL OUTPUT SENTINEL" not in persisted
