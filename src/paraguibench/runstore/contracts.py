"""RunStore 对调用方公开的稳定数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """表示已归档并可由摘要校验的单个 Attempt artifact。

    输入参数：
        path：artifact 在当前 RunStore 中的完整文件路径。
        logical_name：调用方赋予 artifact 的稳定逻辑名称。
        relative_path：相对 Attempt ``artifacts`` 目录的 POSIX 路径。
        sha256：脱敏后实际落盘字节的 SHA-256 十六进制摘要。
        byte_count：脱敏后实际落盘内容的字节数。
        media_type：artifact 的 IANA 媒体类型。
    输出返回值：
        该类型本身不执行 I/O；调用方通过只读属性取得已提交
        artifact 的路径及完整性元数据。
    """

    path: Path
    logical_name: str
    relative_path: str
    sha256: str
    byte_count: int
    media_type: str


class ExecutionOutcome(StrEnum):
    """Agent 与运行环境的执行状态或终态。

    输入参数：
        枚举值由 RunStore 内部和调用方通过具名成员选择。
    输出返回值：
        字符串枚举值用于稳定 JSON 持久化；不包含评价器是否运行或得分。
    """

    QUEUED = "QUEUED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    INFRA_ERROR = "INFRA_ERROR"


class EvaluationOutcome(StrEnum):
    """评价协议的独立状态或终态。

    输入参数：
        枚举值由 evaluator 或 runtime 通过具名成员选择。
    输出返回值：
        字符串枚举值用于稳定 JSON 持久化；未运行、不可用和错误状态不得
        通过 ``score=0`` 伪装成任务失败。
    """

    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RunHandle:
    """表示已经安全建立且 manifest 固定的一次 Run。

    输入参数：
        path：该 Run 在 RunStore 中的目录路径。
        run_id：固定代码、配置、Agent System 和环境版本的 Run 标识。
    输出返回值：
        该类型本身不执行 I/O；调用方通过只读属性取得 Run 身份与目录。
    """

    path: Path
    run_id: str


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    """表示已经安全建立的单个任务执行尝试。

    输入参数：
        path：该 Attempt 在 RunStore 中的绝对或根目录相对路径。
        run_id：所属 Run 的稳定标识。
        task_id：所属 Benchmark Task 的原始稳定标识。
        attempt_id：本次执行尝试的唯一标识。
    输出返回值：
        该类型本身不执行 I/O；调用方通过只读属性取得 Attempt 身份与目录。
    """

    path: Path
    run_id: str
    task_id: str
    attempt_id: str
