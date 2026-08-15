"""任务级 RunStore 的公开实现。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import write_json_artifact
from .contracts import (
    ArtifactRecord,
    AttemptFailureStage,
    AttemptInspection,
    EvaluationOutcome,
    ExecutionOutcome,
    RunHandle,
    RunVersionVector,
    TaskAttempt,
)
from .events import EventStream
from .identifiers import validate_identifier
from .inspection import (
    project_attempt_inspection,
    validate_attempt_identity_record,
    validate_versioned_run_manifest,
)
from .persistence import (
    ensure_private_subdirectory,
    read_private_json_if_exists,
    write_private_json_exclusive,
    write_private_json_once_or_verify,
)
from .outcomes import (
    default_failure_stage,
    validate_evaluation_score,
    validate_failure_stage,
    validate_terminal_outcomes,
)
from .privacy import sanitize_record
from .task_audit import validate_task_audit_record
from .versioning import validate_run_version_vector

_RUN_SCHEMA_VERSION = "2.0"
_ATTEMPT_SCHEMA_VERSION = "1.0"


class RunStore:
    """按 Run、Benchmark Task 与 Attempt 持久化脱敏运行记录。

    输入参数：
        root：RunStore 数据根目录。生产环境应位于 Git 工作树之外。
    输出返回值：
        构造函数返回 RunStore 实例；仅写入方法会按 ``0700`` 建立目录，
        只读 inspect 不会创建或收紧用户提供的路径。
    """

    def __init__(self, root: str | Path) -> None:
        """初始化 RunStore 路径句柄且不执行文件系统写入。

        输入参数：
            root：RunStore 数据根目录的字符串或 ``Path``。
        输出返回值：
            无；初始化后的实例可通过公开写入方法建立私有目录，或通过
            inspect 严格读取既有目录。
        """

        self._root = Path(root)

    def start_run(
        self,
        *,
        run_id: str,
        run_record: Mapping[str, Any],
        version_vector: RunVersionVector,
    ) -> RunHandle:
        """建立 Run 并持久化不可变、默认脱敏的复现 manifest。

        输入参数：
            run_id：固定代码、配置、Agent System 和环境版本的一次 Run 标识。
            run_record：Git revision、benchmark manifest digest、Agent System、
                环境镜像与配置摘要等复现元数据。
            version_vector：源码、Agent、评价器、评价协议和环境身份组成的
                强类型不可变版本向量。
        输出返回值：
            ``RunHandle``，包含 Run 的安全目录和稳定标识。同一 run_id 仅接受
            完全相同的 manifest，冲突内容会被拒绝。
        """

        safe_run_id = validate_identifier("run_id", run_id)
        validate_run_version_vector(version_vector)
        run_path = ensure_private_subdirectory(self._root, safe_run_id)
        write_private_json_once_or_verify(
            run_path / "run.json",
            sanitize_record(
                {
                    "schema_version": _RUN_SCHEMA_VERSION,
                    "run_id": run_id,
                    "version_vector": asdict(version_vector),
                    "run": dict(run_record),
                }
            ),
        )
        return RunHandle(path=run_path, run_id=run_id)

    def start_attempt(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        task_record: Mapping[str, Any],
    ) -> TaskAttempt:
        """建立任务 Attempt，并原子写入默认脱敏的身份快照。

        输入参数：
            run_id：固定代码、配置和环境的一次 Run 标识。
            task_id：Benchmark Task 原始稳定标识。
            attempt_id：该任务本次执行尝试的唯一标识。
            task_record：需要随任务保存的公开定义或配置摘要；所有层级在
                序列化前经过统一脱敏。
        输出返回值：
            ``TaskAttempt``，包含安全建立后的 Attempt 路径和三个稳定标识。
        """

        safe_run_id = validate_identifier("run_id", run_id)
        safe_task_id = validate_identifier("task_id", task_id)
        safe_attempt_id = validate_identifier("attempt_id", attempt_id)
        validated_task_record = validate_task_audit_record(
            task_record,
            expected_task_id=safe_task_id,
        )

        run_path = ensure_private_subdirectory(self._root, safe_run_id)
        run_manifest = read_private_json_if_exists(run_path / "run.json")
        if run_manifest is None:
            raise ValueError(f"run manifest is missing for run_id={safe_run_id!r}")
        validate_versioned_run_manifest(
            run_manifest,
            expected_run_id=safe_run_id,
        )
        task_path = ensure_private_subdirectory(
            run_path,
            "tasks",
            safe_task_id,
        )
        attempt_path = ensure_private_subdirectory(
            task_path,
            "attempts",
            safe_attempt_id,
        )

        task_payload = sanitize_record(
            {
                "schema_version": _ATTEMPT_SCHEMA_VERSION,
                "run_id": run_id,
                "task_id": task_id,
                "task": validated_task_record,
            }
        )
        write_private_json_once_or_verify(
            task_path / "task.json",
            task_payload,
        )
        write_private_json_exclusive(
            attempt_path / "attempt.json",
            {
                "schema_version": _ATTEMPT_SCHEMA_VERSION,
                "run_id": run_id,
                "task_id": task_id,
                "attempt_id": attempt_id,
            },
        )

        return TaskAttempt(
            path=attempt_path,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
        )

    def open_event_stream(
        self,
        *,
        attempt: TaskAttempt,
        producer_kind: str,
        producer_id: str,
    ) -> EventStream:
        """为 Attempt 中的单个 producer 建立独占事件流。

        输入参数：
            attempt：由当前 RunStore 建立的任务 Attempt。
            producer_kind：planner、worker、environment、evaluator 或 runtime。
            producer_id：本 Attempt 内稳定的 producer 标识。
        输出返回值：
            ``EventStream``；不同 producer 写入各自的 JSONL 文件。
        """

        self._validate_attempt(attempt)

        return EventStream(
            attempt=attempt,
            producer_kind=producer_kind,
            producer_id=producer_id,
        )

    def write_artifact(
        self,
        *,
        attempt: TaskAttempt,
        logical_name: str,
        relative_path: str,
        content: Any,
        media_type: str,
    ) -> ArtifactRecord:
        """归档脱敏后的结构化 Attempt artifact 并写入摘要 manifest。

        输入参数：
            attempt：由当前 RunStore 建立的任务 Attempt。
            logical_name：当前 Attempt 内稳定且唯一的 artifact
                逻辑名称。
            relative_path：相对 Attempt ``artifacts`` 目录的 POSIX 文件路径。
            content：由 JSON 基本类型、Mapping 或 Sequence 组成的
                结构化内容；落盘前统一经过 ``sanitize_record``。
            media_type：必须为与实际序列化格式一致的
                ``application/json``。
        输出返回值：
            ``ArtifactRecord``，包含 artifact 路径、脱敏后摘要、字节数
            和媒体类型。manifest 以原子替换方式累计记录，既有正文不会
            被覆盖。
        """

        self._validate_attempt(attempt)
        return write_json_artifact(
            attempt=attempt,
            logical_name=logical_name,
            relative_path=relative_path,
            content=content,
            media_type=media_type,
        )

    def finish_attempt(
        self,
        *,
        attempt: TaskAttempt,
        execution_outcome: ExecutionOutcome,
        evaluation_outcome: EvaluationOutcome,
        score: float | None,
        failure_stage: AttemptFailureStage | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """一次性提交 Attempt 的执行与评价终态。

        输入参数：
            attempt：由当前 RunStore 建立的任务 Attempt。
            execution_outcome：Agent 和运行环境的独立执行终态。
            evaluation_outcome：评价协议的独立状态或终态。
            score：评价器产生的有限数值；评价未请求、不可用或报错时必须为
                ``None``，不能用零分替代。
            failure_stage：由掌握生命周期的 runtime 提供的保留失败阶段；
                省略时仅在确有系统失败的终态上保守记录 ``UNKNOWN``。
            details：可选的结构化终态说明，写入前统一递归脱敏。
        输出返回值：
            无；首次调用原子创建不可变 ``summary.json``，重复终结同一
            Attempt 时抛出 ``RunStoreConflictError``。
        """

        self._validate_attempt(attempt)
        if not isinstance(execution_outcome, ExecutionOutcome):
            raise TypeError("execution_outcome must be ExecutionOutcome")
        if not isinstance(evaluation_outcome, EvaluationOutcome):
            raise TypeError("evaluation_outcome must be EvaluationOutcome")
        validate_terminal_outcomes(
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
        )
        normalized_score = validate_evaluation_score(
            evaluation_outcome,
            score,
        )
        normalized_stage = failure_stage or default_failure_stage(
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
        )
        validate_failure_stage(
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
            failure_stage=normalized_stage,
        )

        write_private_json_exclusive(
            attempt.path / "summary.json",
            sanitize_record(
                {
                    "schema_version": _ATTEMPT_SCHEMA_VERSION,
                    "run_id": attempt.run_id,
                    "task_id": attempt.task_id,
                    "attempt_id": attempt.attempt_id,
                    "completed_at_utc": datetime.now(UTC).isoformat(),
                    "execution": {
                        "outcome": execution_outcome.value,
                    },
                    "evaluation": {
                        "outcome": evaluation_outcome.value,
                        "score": normalized_score,
                    },
                    "failure_stage": normalized_stage.value,
                    "details": dict(details or {}),
                }
            ),
        )

    def inspect_attempt(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
    ) -> AttemptInspection:
        """读取一个 Attempt 的 allowlist-only 安全诊断投影。

        输入参数：
            run_id：所属 Run 的稳定标识。
            task_id：Benchmark Task 稳定标识。
            attempt_id：本次执行尝试的稳定标识。
        输出返回值：
            ``AttemptInspection``；不包含 summary details、事件、异常消息、
            prompt、模型输出或 evaluator 任意扩展字段。
        异常：
            ValueError/TypeError：标识、路径、summary 或 versioned manifest
                无效；读取过程不会创建缺失 Attempt 目录。
        """

        safe_run_id = validate_identifier("run_id", run_id)
        safe_task_id = validate_identifier("task_id", task_id)
        safe_attempt_id = validate_identifier("attempt_id", attempt_id)
        attempt_path = self._existing_attempt_path(
            safe_run_id,
            safe_task_id,
            safe_attempt_id,
        )
        summary = read_private_json_if_exists(attempt_path / "summary.json")
        if summary is None:
            raise ValueError("attempt summary is missing")
        attempt_record = read_private_json_if_exists(attempt_path / "attempt.json")
        if attempt_record is None:
            raise ValueError("attempt identity record is missing")
        validate_attempt_identity_record(
            attempt_record,
            expected_run_id=safe_run_id,
            expected_task_id=safe_task_id,
            expected_attempt_id=safe_attempt_id,
        )
        run_manifest = read_private_json_if_exists(
            self._root / safe_run_id / "run.json"
        )
        if run_manifest is None:
            raise ValueError("run manifest is missing")
        return project_attempt_inspection(
            summary=summary,
            run_manifest=run_manifest,
            expected_run_id=safe_run_id,
            expected_task_id=safe_task_id,
            expected_attempt_id=safe_attempt_id,
        )

    def _validate_attempt(self, attempt: TaskAttempt) -> None:
        """验证 Attempt 属于当前 RunStore 且身份记录存在。

        输入参数：
            attempt：待用于事件或终态写入的 Attempt handle。
        输出返回值：
            无；路径越界或身份记录缺失时抛出 ``ValueError``。
        """

        if not isinstance(attempt, TaskAttempt):
            raise TypeError("attempt must be TaskAttempt")
        safe_run_id = validate_identifier("run_id", attempt.run_id)
        safe_task_id = validate_identifier("task_id", attempt.task_id)
        safe_attempt_id = validate_identifier(
            "attempt_id",
            attempt.attempt_id,
        )
        canonical_path = self._existing_attempt_path(
            safe_run_id,
            safe_task_id,
            safe_attempt_id,
        )
        supplied_path = Path(attempt.path).absolute()
        if supplied_path != canonical_path.absolute():
            raise ValueError("attempt handle path does not match its identity")
        record = read_private_json_if_exists(canonical_path / "attempt.json")
        if record is None:
            raise ValueError("attempt identity record is missing")
        validate_attempt_identity_record(
            record,
            expected_run_id=safe_run_id,
            expected_task_id=safe_task_id,
            expected_attempt_id=safe_attempt_id,
        )

    def _existing_attempt_path(
        self,
        run_id: str,
        task_id: str,
        attempt_id: str,
    ) -> Path:
        """只读解析既有 Attempt 目录并拒绝中间符号链接。

        输入参数：
            run_id：已验证 Run 标识。
            task_id：已验证 task 标识。
            attempt_id：已验证 Attempt 标识。
        输出返回值：
            位于当前 RunStore 根内的既有 Attempt 目录。
        异常：
            ValueError：任一级缺失、不是目录、为符号链接或解析到根外。
        """

        root = self._root.resolve(strict=True)
        current = self._root
        for part in (
            run_id,
            "tasks",
            task_id,
            "attempts",
            attempt_id,
        ):
            candidate = current / part
            if candidate.is_symlink() or not candidate.is_dir():
                raise ValueError("attempt path is missing or unsafe")
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError("attempt path resolves outside RunStore")
            current = candidate
        return current
