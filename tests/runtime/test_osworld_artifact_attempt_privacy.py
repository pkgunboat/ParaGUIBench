"""OSWorld artifact-state 从 verified gold/guest 到 RunStore 的纵向测试。"""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.osworld import ARTIFACT_STATE_PROTOCOL_ID
from paraguibench.runstore import EvaluationOutcome, RunStore
from paraguibench.runtime.attempt_runner import AttemptRunner
from paraguibench.runtime.evaluators import build_task_evaluator
from paraguibench.runtime.osworld_artifact_evidence import (
    OSWorldArtifactEvidenceSource,
)
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_ID = "Operation-FileOperate-CombinationDocs-010"


def _xlsx_bytes(value: str) -> bytes:
    """构造不落盘的最小 first-sheet workbook。

    输入参数：
        value：写入活动工作表 A1 的敏感测试值。
    输出返回值：
        openpyxl 序列化得到的 OOXML 字节。
    """

    workbook = Workbook()
    workbook.active["A1"] = value
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


class _VerifiedGoldResolver:
    """模拟只在 evaluator 内存交付 verified gold 的窄 resolver。"""

    def __init__(self, content: bytes, calls: list[str]) -> None:
        """保存临时 gold 与共享生命周期记录。

        输入参数：
            content：已视为通过摘要、大小和媒体门禁的 workbook bytes。
            calls：用于证明 gold 读取先于 guest getter 的顺序记录。
        输出返回值：
            无；不访问磁盘或网络。
        """

        self._content = content
        self._calls = calls

    @contextmanager
    def open_verified(
        self,
        logical_key: str,
        *,
        max_bytes: int,
        expected_media_types: frozenset[str],
    ):
        """交付受限 seekable gold 字节流并记录门禁顺序。

        输入参数：
            logical_key：spec 固定的 evaluator-only gold 身份。
            max_bytes/expected_media_types：runtime source 固定的资源和媒体门禁。
        输出返回值：
            context manager 内的只读 ``BytesIO``。
        """

        assert logical_key.endswith(":expected:0:v1")
        assert max_bytes > len(self._content)
        assert expected_media_types == frozenset(
            {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        )
        self._calls.append("gold.open_verified")
        with BytesIO(self._content) as stream:
            yield stream


class _WorkbookController:
    """模拟 production nofollow 单文件 getter。"""

    def __init__(self, content: bytes, calls: list[str]) -> None:
        """保存 guest workbook 与共享顺序记录。

        输入参数：
            content：getter 返回的实际 workbook bytes。
            calls：生命周期顺序记录。
        输出返回值：
            无；构造阶段不执行 guest I/O。
        """

        self._content = content
        self._calls = calls

    def collect_file_bytes(
        self,
        guest_path: str,
        *,
        max_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        """返回有界实际 workbook，并验证 source 使用固定 locator。

        输入参数：
            guest_path：由冻结 shared binding 与 spec locator 拼出的路径。
            max_bytes/max_response_bytes/timeout_seconds：固定 getter 资源上限。
        输出返回值：
            当前测试 workbook 原始字节。
        """

        assert guest_path == "/home/user/exam/grades.xlsx"
        assert len(self._content) < max_bytes < max_response_bytes
        assert timeout_seconds > 0
        self._calls.append("guest.collect_file_bytes")
        return self._content


class _ArtifactAttemptEnvironment:
    """用 production evidence source 模拟单 VM Attempt 生命周期。"""

    def __init__(
        self,
        calls: list[str],
        source: OSWorldArtifactEvidenceSource,
        controller: _WorkbookController,
    ) -> None:
        """绑定顺序记录、真实 source 与 fake controller。

        输入参数：
            calls/source/controller：共享顺序记录、production source 与外部边界。
        输出返回值：
            无；证据只在 evaluator 请求时捕获。
        """

        self._calls = calls
        self._source = source
        self._controller = controller
        self._observation: object | None = None

    def start(self) -> None:
        """记录环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self._calls.append("environment.start")

    def prepare(self, task: dict[str, Any]) -> None:
        """确认 canonical task 身份并记录准备完成。

        输入参数：
            task：AttemptRunner evaluator 可见的可信任务投影。
        输出返回值：
            无；此 synthetic tracer 不模拟尚未核验的输入资产。
        """

        assert task["task_id"] == _TASK_ID
        self._calls.append("environment.prepare")

    def osworld_artifact_state_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """在 Agent 结束后捕获并缓存单 VM 脱敏 observation。

        输入参数：
            task_id/protocol_id：runtime evaluator 固定的任务与协议身份。
        输出返回值：
            含 production source 首次 observation 的单元素 tuple。
        """

        assert task_id == _TASK_ID
        assert protocol_id == ARTIFACT_STATE_PROTOCOL_ID
        self._calls.append("artifact.capture")
        if self._observation is None:
            self._observation = self._source.capture(
                task_id,
                self._controller,
                guest_shared_dir="/home/user/shared",
            )
        return (self._observation,)

    def close(self) -> None:
        """记录 owned 环境关闭。

        输入参数：无。
        输出返回值：无；本 fake 不拥有外部资源。
        """

        self._calls.append("environment.close")


class _SensitiveAgent:
    """返回不得参与 artifact 评价或持久化的敏感最终文本。"""

    def __init__(self, calls: list[str], final_output: str) -> None:
        """保存顺序记录与敏感 terminal text。

        输入参数：
            calls/final_output：共享生命周期记录与 Agent 最终文本。
        输出返回值：
            无。
        """

        self._calls = calls
        self._final_output = final_output

    def run(
        self,
        task_view: dict[str, Any],
        environment: object,
    ) -> AgentRunResult:
        """验证 Agent 只见安全投影并返回完成结果。

        输入参数：
            task_view：不含 evaluator/gold 的 Agent task view。
            environment：当前存活环境；本 fake 不读取。
        输出返回值：
            带敏感 final text 的合法 ``AgentRunResult``。
        """

        del environment
        assert task_view["task_id"] == _TASK_ID
        assert "evaluator_path" not in task_view
        self._calls.append("agent.run")
        return AgentRunResult(
            final_output=self._final_output,
            step_count=1,
            termination="finished",
        )


def _prepared_task() -> PreparedTask:
    """从 canonical JSON 构造 evaluator/Agent/audit 三投影任务。

    输入参数：无。
    输出返回值：
        evaluator 使用完整 task，Agent 仅见 task_id 与 instruction。
    """

    task = json.loads(
        (_REPO_ROOT / f"benchmark/tasks/{_TASK_ID}.json").read_text(encoding="utf-8")
    )
    return PreparedTask(
        trusted_task=task,
        agent_task={
            "task_id": task["task_id"],
            "instruction": task["instruction"],
        },
        audit_metadata=synthetic_task_audit(
            task["task_id"],
            task_uid=task["task_uid"],
            task_type=task["task_type"],
            task_source=task["task_source"],
            task_tag=task["task_tag"],
        ),
    )


def test_first_sheet_runtime_persists_only_protocol_reason_and_counts(
    tmp_path: Path,
) -> None:
    """验证 first-sheet 完整链路只向 RunStore 交付脱敏结果。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根。
    输出返回值：
        无；gold 先于 guest getter，final output、内容、路径和 gold key 均不落盘。
    """

    private_value = "PRIVATE_OSWORLD_FIRST_SHEET_CONTENT"
    final_output = "PRIVATE_OSWORLD_ARTIFACT_FINAL_OUTPUT"
    workbook = _xlsx_bytes(private_value)
    calls: list[str] = []
    source = OSWorldArtifactEvidenceSource(
        gold_resolver=_VerifiedGoldResolver(workbook, calls)
    )
    environment = _ArtifactAttemptEnvironment(
        calls,
        source,
        _WorkbookController(workbook, calls),
    )
    prepared = _prepared_task()
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-osworld-artifact-privacy",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-osworld-artifact-privacy",
        task_id=_TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared.audit_metadata,
    )
    evaluator = build_task_evaluator(
        prepared.trusted_task,
        evaluation_protocol=ARTIFACT_STATE_PROTOCOL_ID,
    )

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=prepared,
        environment=environment,
        agent=_SensitiveAgent(calls, final_output),
        evaluator=evaluator,
    )

    assert result.evaluation_outcome is EvaluationOutcome.PASSED
    assert result.score == 1.0
    assert calls == [
        "environment.start",
        "environment.prepare",
        "agent.run",
        "artifact.capture",
        "gold.open_verified",
        "guest.collect_file_bytes",
        "environment.close",
    ]
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for sentinel in (
        private_value.encode("utf-8"),
        final_output.encode("utf-8"),
        b"/home/user/exam/grades.xlsx",
        b"grades.xlsx",
        b"expected:0:v1",
    ):
        assert sentinel not in persisted
    for safe_field in (
        ARTIFACT_STATE_PROTOCOL_ID.encode("utf-8"),
        b"task_rule_id",
        b"evaluated_vm_count",
        b"failed_metric_count",
        b"reason_codes",
    ):
        assert safe_field in persisted
