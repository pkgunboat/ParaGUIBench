"""Operation guest artifact 经 AttemptRunner 到 RunStore 的纵向隐私测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.operation import OPERATION_PROTOCOL_ID
from paraguibench.integrations.osworld.operation_artifacts import (
    OSWorldOperationArtifactSource,
    OperationArtifactSnapshot,
)
from paraguibench.runstore import EvaluationOutcome, RunStore
from paraguibench.runtime.attempt_runner import AttemptRunner
from paraguibench.runtime.evaluators import build_task_evaluator
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_ID = "Operation-FileOperate-CombinationDocs-005"


class _GuestArtifactController:
    """模拟受控 guest manifest 与 nofollow 单文件 getter 边界。"""

    def __init__(self, files: dict[str, bytes]) -> None:
        """保存 guest 文件闭集及两类 getter 调用记录。

        输入参数：
            files：相对 guest shared 根的文件名到原始字节映射。
        输出返回值：
            无；所有值仅保存在当前测试进程内存。
        """

        self._files = dict(files)
        self.manifest_calls: list[dict[str, object]] = []
        self.file_calls: list[dict[str, object]] = []

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        *,
        max_files: int,
        max_nodes: int,
        max_depth: int,
        max_name_bytes: int,
        max_file_bytes: int,
        max_total_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> tuple[tuple[str, int, str], ...]:
        """返回已由 guest helper 完整哈希的文件树 manifest。

        输入参数：
            guest_directory：冻结的 guest shared 目录；其余参数为资源上限。
        输出返回值：
            按相对路径排序的 ``(path, size, sha256)`` tuple。
        """

        self.manifest_calls.append(
            {
                "guest_directory": guest_directory,
                "max_files": max_files,
                "max_nodes": max_nodes,
                "max_depth": max_depth,
                "max_name_bytes": max_name_bytes,
                "max_file_bytes": max_file_bytes,
                "max_total_bytes": max_total_bytes,
                "max_response_bytes": max_response_bytes,
                "timeout_seconds": timeout_seconds,
            }
        )
        return tuple(
            (
                name,
                len(content),
                hashlib.sha256(content).hexdigest(),
            )
            for name, content in sorted(self._files.items())
        )

    def collect_file_bytes(
        self,
        guest_path: str,
        *,
        max_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        """返回 manifest 已固定文件的受限原始字节。

        输入参数：
            guest_path：受信 source 从 shared 根与 manifest 相对路径拼出的路径。
            max_bytes/max_response_bytes/timeout_seconds：单文件硬边界。
        输出返回值：
            当前 guest 文件字节；不存在时由测试失败暴露。
        """

        relative = PurePosixPath(guest_path).relative_to("/home/user/shared")
        content = self._files[relative.as_posix()]
        self.file_calls.append(
            {
                "guest_path": guest_path,
                "max_bytes": max_bytes,
                "max_response_bytes": max_response_bytes,
                "timeout_seconds": timeout_seconds,
            }
        )
        return content


class _OperationAttemptEnvironment:
    """用 production capture source 模拟单 VM Operation 生命周期。"""

    def __init__(
        self,
        calls: list[str],
        source: OSWorldOperationArtifactSource,
        controller: _GuestArtifactController,
    ) -> None:
        """绑定顺序记录、真实 source 与 guest controller 边界 fake。

        输入参数：
            calls：共享生命周期顺序记录。
            source/controller：待测生产 source 与外部 guest 边界。
        输出返回值：
            无；构造阶段不捕获文件。
        """

        self._calls = calls
        self._source = source
        self._controller = controller
        self._snapshot: OperationArtifactSnapshot | None = None

    def start(self) -> None:
        """记录环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self._calls.append("environment.start")

    def prepare(self, task: dict[str, Any]) -> None:
        """确认可信任务身份并记录资产准备完成。

        输入参数：
            task：AttemptRunner 的可信 canonical task 投影。
        输出返回值：
            无；本 tracer 不模拟尚未具备 manifest 的原始输入资产。
        """

        assert task["task_id"] == _TASK_ID
        self._calls.append("environment.prepare")

    def operation_artifact_snapshot(
        self,
        task_id: str,
        protocol_id: str,
    ) -> OperationArtifactSnapshot:
        """在 Agent 结束后通过 production source 冻结 guest artifact。

        输入参数：
            task_id/protocol_id：runtime evaluator 固定的任务与协议身份。
        输出返回值：
            首次捕获并缓存的 host 临时 artifact 快照。
        """

        assert task_id == _TASK_ID
        assert protocol_id == OPERATION_PROTOCOL_ID
        self._calls.append("operation.capture")
        if self._snapshot is None:
            self._snapshot = self._source.capture(
                task_id,
                self._controller,
                guest_shared_dir="/home/user/shared",
            )
        return self._snapshot

    def close(self) -> None:
        """删除临时快照并记录 owned 环境清理。

        输入参数：无。
        输出返回值：无；快照未创建时只记录清理。
        """

        if self._snapshot is not None:
            self._snapshot.close()
        self._calls.append("environment.close")


class _SensitiveOperationAgent:
    """返回不得参与 Operation 评价或持久化的敏感最终文本。"""

    def __init__(self, calls: list[str], final_output: str) -> None:
        """保存生命周期记录与合成敏感最终文本。

        输入参数：
            calls/final_output：共享顺序记录和 Agent terminal text。
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
        """确认准备完成后返回一步结束的 Agent 结果。

        输入参数：
            task_view：不含 eval_rules 的 Agent 安全任务投影。
            environment：当前存活的任务环境，本 fake 不读取。
        输出返回值：
            带敏感 final text 的合法 ``AgentRunResult``。
        """

        del environment
        assert task_view["task_id"] == _TASK_ID
        assert "eval_rules" not in task_view
        assert self._calls[-1] == "environment.prepare"
        self._calls.append("agent.run")
        return AgentRunResult(
            final_output=self._final_output,
            step_count=1,
            termination="finished",
        )


def _prepared_combination_docs_task() -> PreparedTask:
    """从仓库 canonical JSON 构造 CombinationDocs-005 三投影任务。

    输入参数：无。
    输出返回值：
        evaluator 可见完整 eval_rules、Agent 仅见 instruction 的 PreparedTask。
    """

    task = json.loads(
        (
            _REPO_ROOT
            / "benchmark/tasks/Operation-FileOperate-CombinationDocs-005.json"
        ).read_text(encoding="utf-8")
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


def test_combination_docs_guest_capture_evaluates_and_persists_only_counts(
    tmp_path: Path,
) -> None:
    """验证首条 Operation guest capture 到 RunStore 的完整安全链。

    输入参数：
        tmp_path：pytest 提供的任务级 RunStore 根目录。
    输出返回值：
        无；五个有效 PDF 得满分，capture 在 Agent 后/close 前发生，
        文件名、文件内容、guest 路径、gold 与 final text 均不落盘。
    """

    prepared = _prepared_combination_docs_task()
    sensitive_content = b"%PDF-1.7\nPRIVATE ARTIFACT CONTENT SENTINEL\n"
    expected_names = prepared.trusted_task["eval_rules"][0]["params"]["filenames"]
    controller = _GuestArtifactController(
        {name: sensitive_content for name in expected_names}
    )
    calls: list[str] = []
    environment = _OperationAttemptEnvironment(
        calls,
        OSWorldOperationArtifactSource(),
        controller,
    )
    final_output = "PRIVATE OPERATION FINAL SENTINEL"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-operation-privacy",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-operation-privacy",
        task_id=_TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared.audit_metadata,
    )
    evaluator = build_task_evaluator(
        prepared.trusted_task,
        evaluation_protocol=OPERATION_PROTOCOL_ID,
    )

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=prepared,
        environment=environment,
        agent=_SensitiveOperationAgent(calls, final_output),
        evaluator=evaluator,
    )

    assert result.evaluation_outcome is EvaluationOutcome.PASSED
    assert result.score == 1.0
    assert calls == [
        "environment.start",
        "environment.prepare",
        "agent.run",
        "operation.capture",
        "environment.close",
    ]
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for sentinel in (
        final_output.encode("utf-8"),
        b"PRIVATE ARTIFACT CONTENT SENTINEL",
        b"/home/user/shared",
        b"Business_Report.pdf",
        b"filenames",
    ):
        assert sentinel not in persisted
    for safe_field in (
        OPERATION_PROTOCOL_ID.encode("utf-8"),
        b"task_rule_id",
        b"evaluated_rule_count",
        b"artifact_count",
    ):
        assert safe_field in persisted
