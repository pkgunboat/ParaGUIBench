"""任务级 RunStore 的公开实现。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import write_json_artifact
from .contracts import (
    ArtifactRecord,
    EvaluationOutcome,
    ExecutionOutcome,
    RunHandle,
    TaskAttempt,
)
from .events import EventStream
from .identifiers import validate_identifier
from .persistence import (
    ensure_private_directory,
    ensure_private_subdirectory,
    write_private_json_exclusive,
    write_private_json_once_or_verify,
)
from .privacy import sanitize_record
from .task_audit import validate_task_audit_record

_SCHEMA_VERSION = "1.0"


class RunStore:
    """按 Run、Benchmark Task 与 Attempt 持久化脱敏运行记录。

    输入参数：
        root：RunStore 数据根目录。生产环境应位于 Git 工作树之外。
    输出返回值：
        构造函数返回 RunStore 实例；目录在实例化时按 ``0700`` 创建。
    """

    def __init__(self, root: str | Path) -> None:
        """初始化 RunStore 并建立私有根目录。

        输入参数：
            root：RunStore 数据根目录的字符串或 ``Path``。
        输出返回值：
            无；初始化后的实例可通过公开方法建立任务 Attempt。
        """

        self._root = Path(root)
        ensure_private_directory(self._root)

    def start_run(
        self,
        *,
        run_id: str,
        run_record: Mapping[str, Any],
    ) -> RunHandle:
        """建立 Run 并持久化不可变、默认脱敏的复现 manifest。

        输入参数：
            run_id：固定代码、配置、Agent System 和环境版本的一次 Run 标识。
            run_record：Git revision、benchmark manifest digest、Agent System、
                环境镜像与配置摘要等复现元数据。
        输出返回值：
            ``RunHandle``，包含 Run 的安全目录和稳定标识。同一 run_id 仅接受
            完全相同的 manifest，冲突内容会被拒绝。
        """

        safe_run_id = validate_identifier("run_id", run_id)
        run_path = ensure_private_subdirectory(self._root, safe_run_id)
        write_private_json_once_or_verify(
            run_path / "run.json",
            sanitize_record(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "run_id": run_id,
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
        run_manifest_path = run_path / "run.json"
        if run_manifest_path.is_symlink() or not run_manifest_path.is_file():
            raise ValueError(
                f"run manifest is missing for run_id={safe_run_id!r}"
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
                "schema_version": _SCHEMA_VERSION,
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
                "schema_version": _SCHEMA_VERSION,
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
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """一次性提交 Attempt 的执行与评价终态。

        输入参数：
            attempt：由当前 RunStore 建立的任务 Attempt。
            execution_outcome：Agent 和运行环境的独立执行终态。
            evaluation_outcome：评价协议的独立状态或终态。
            score：评价器产生的有限数值；评价未请求、不可用或报错时必须为
                ``None``，不能用零分替代。
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
        _validate_score(evaluation_outcome, score)

        write_private_json_exclusive(
            attempt.path / "summary.json",
            sanitize_record(
                {
                    "schema_version": _SCHEMA_VERSION,
                    "run_id": attempt.run_id,
                    "task_id": attempt.task_id,
                    "attempt_id": attempt.attempt_id,
                    "completed_at_utc": datetime.now(UTC).isoformat(),
                    "execution": {
                        "outcome": execution_outcome.value,
                    },
                    "evaluation": {
                        "outcome": evaluation_outcome.value,
                        "score": score,
                    },
                    "details": dict(details or {}),
                }
            ),
        )

    def _validate_attempt(self, attempt: TaskAttempt) -> None:
        """验证 Attempt 属于当前 RunStore 且身份记录存在。

        输入参数：
            attempt：待用于事件或终态写入的 Attempt handle。
        输出返回值：
            无；路径越界或身份记录缺失时抛出 ``ValueError``。
        """

        attempt_path = attempt.path.resolve()
        root_path = self._root.resolve()
        if not attempt_path.is_relative_to(root_path):
            raise ValueError("attempt does not belong to this RunStore")
        if not (attempt_path / "attempt.json").is_file():
            raise ValueError("attempt identity record is missing")


def _validate_score(
    evaluation_outcome: EvaluationOutcome,
    score: float | None,
) -> None:
    """验证评价状态与 score 的组合不会混淆未评价和零分。

    输入参数：
        evaluation_outcome：评价协议的状态或终态。
        score：可空评价数值。
    输出返回值：
        无；非法类型、非有限数值或非评分状态携带 score 时抛出
        ``TypeError`` 或 ``ValueError``。
    """

    scoring_outcomes = {
        EvaluationOutcome.PASSED,
        EvaluationOutcome.FAILED,
    }
    if score is None:
        return
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise TypeError("score must be a finite number or None")
    if not math.isfinite(float(score)):
        raise ValueError("score must be finite")
    if evaluation_outcome not in scoring_outcomes:
        raise ValueError(
            "score must be None unless evaluation outcome is PASSED or FAILED"
        )
