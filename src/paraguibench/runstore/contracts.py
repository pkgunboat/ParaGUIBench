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
class RunVersionVector:
    """保存一次 Run 不可变且可独立审计的实现版本向量。

    输入参数：
        source_revision：完整发布源码或脏工作树冻结摘要。
        agent_code_revision：本次实际 Agent 实现源码摘要。
        evaluator_revision：本次实际 evaluator 实现源码摘要。
        evaluation_protocol：runtime-support 清单声明的评价协议标识。
        environment_protocol：runtime-support 清单声明的环境协议标识。
        environment_revision：完整环境 manifest 的规范化摘要。
    输出返回值：
        该类型不执行 I/O；不可变字段由 RunStore 校验后独立写入
        ``run.json``，不得藏在自由格式 ``run_record`` 中。
    """

    source_revision: str
    agent_code_revision: str
    evaluator_revision: str
    evaluation_protocol: str
    environment_protocol: str
    environment_revision: str


class AttemptFailureStage(StrEnum):
    """表示可公开诊断的 Attempt 失败阶段。

    输入参数：
        枚举值由 RunStore 对 summary 中的严格 allowlist 投影产生。
    输出返回值：
        稳定字符串只表达生命周期阶段，不包含异常消息、任务正文、模型输出
        或 evaluator details。
    """

    NOT_FAILED = "not_failed"
    ENVIRONMENT_START = "environment.start"
    ENVIRONMENT_PREPARE = "environment.prepare"
    AGENT_RUN = "agent.run"
    EVALUATOR_EVALUATE = "evaluator.evaluate"
    ENVIRONMENT_CLOSE = "environment.close"
    UNKNOWN = "unknown"


class RunProvenanceStatus(StrEnum):
    """表示 Attempt 所属 Run 是否拥有完整版本向量。"""

    VERSIONED = "versioned"
    LEGACY_UNVERSIONED = "legacy_unversioned"


@dataclass(frozen=True, slots=True)
class AttemptInspection:
    """保存 Attempt 的 allowlist-only 安全诊断投影。

    输入参数：
        execution_outcome：Agent 与环境执行终态。
        evaluation_outcome：评价协议终态。
        score：评价器有限得分或 ``None``。
        failure_stage：严格枚举化生命周期失败阶段。
        provenance_status：所属 Run 是否带完整版本向量。
        version_vector：versioned Run 的固定版本向量；legacy Run 为 ``None``。
    输出返回值：
        该类型不包含 summary details、事件、异常消息、prompt、模型响应或
        evaluator 任意扩展字段，可直接用于 CLI 安全投影。
    """

    execution_outcome: ExecutionOutcome
    evaluation_outcome: EvaluationOutcome
    score: float | None
    failure_stage: AttemptFailureStage
    provenance_status: RunProvenanceStatus
    version_vector: RunVersionVector | None


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
