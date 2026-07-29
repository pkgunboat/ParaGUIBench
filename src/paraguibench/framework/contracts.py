"""ParaGUI 与其他 planner–worker 系统共享的 DAG 数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

_SUBTASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_SUBTASKS = 64


class SubtaskStatus(StrEnum):
    """描述单个 subtask 的稳定执行终态。

    输入参数：
        枚举成员由 framework scheduler 或 worker adapter 选择。
    输出返回值：
        字符串枚举值可安全写入结构化日志；不包含异常消息或模型原文。
    """

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class SubtaskSpec:
    """描述 planner 产生的一个有界 DAG 节点。

    输入参数：
        subtask_id：plan 内唯一且可用于日志身份的稳定标识。
        instruction：worker 可见的非空子任务指令。
        depends_on：必须先成功完成的 subtask 标识，顺序同时定义依赖结果顺序。
        worker_role：worker registry 使用的抽象角色，不指向具体模型类。
    输出返回值：
        不可变的 subtask specification；构造时完成字段边界校验。
    """

    subtask_id: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    worker_role: str = "gui"

    def __post_init__(self) -> None:
        """校验 subtask 字段并拒绝自依赖或重复依赖。

        输入参数：
            无；读取当前 dataclass 字段。
        输出返回值：
            无；合法时正常返回，非法时抛出 ``ValueError`` 或 ``TypeError``。
        """

        _validate_subtask_id(self.subtask_id)
        if (
            not isinstance(self.instruction, str)
            or not self.instruction
            or len(self.instruction) > 20_000
        ):
            raise ValueError("subtask instruction 必须是长度 1–20000 的字符串")
        if not isinstance(self.depends_on, tuple):
            raise TypeError("depends_on 必须是 tuple")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("subtask contains duplicate dependency")
        for dependency_id in self.depends_on:
            _validate_subtask_id(dependency_id)
        if self.subtask_id in self.depends_on:
            raise ValueError("subtask cannot depend on itself")
        _validate_subtask_id(self.worker_role)


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """保存 planner 输出的已验证有向无环执行计划。

    输入参数：
        subtasks：按稳定展示顺序排列的 1–64 个 ``SubtaskSpec``。
    输出返回值：
        不可变且依赖闭合、无循环的 execution plan。
    """

    subtasks: tuple[SubtaskSpec, ...]

    def __post_init__(self) -> None:
        """校验节点身份、依赖闭包和 DAG 无环性。

        输入参数：
            无；读取当前 ``subtasks``。
        输出返回值：
            无；非法 planner 输出在任何 worker 启动前被拒绝。
        """

        if not isinstance(self.subtasks, tuple):
            raise TypeError("execution plan subtasks 必须是 tuple")
        if not 1 <= len(self.subtasks) <= _MAX_SUBTASKS:
            raise ValueError("execution plan 必须包含 1–64 个 subtask")
        if not all(isinstance(item, SubtaskSpec) for item in self.subtasks):
            raise TypeError("execution plan 只能包含 SubtaskSpec")

        identifiers = [item.subtask_id for item in self.subtasks]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("execution plan contains duplicate subtask id")
        known_identifiers = set(identifiers)
        for subtask in self.subtasks:
            unknown = [
                item
                for item in subtask.depends_on
                if item not in known_identifiers
            ]
            if unknown:
                raise ValueError(
                    f"execution plan contains unknown dependency: {unknown[0]}"
                )
        _reject_dependency_cycle(self.subtasks)


@dataclass(frozen=True, slots=True)
class SubtaskResult:
    """保存一个 subtask 的脱敏终态与可供依赖节点使用的输出。

    输入参数：
        subtask_id：对应 ``SubtaskSpec`` 的稳定标识。
        status：SUCCEEDED、FAILED 或 BLOCKED。
        output：成功或部分执行时供 planner 使用的文本，不由 framework 持久化。
        step_count：worker 为此 subtask 消耗的非负动作步数。
        failure_type：失败或阻塞的稳定类型，不得放入异常消息。
    输出返回值：
        不可变的 worker 结果。
    """

    subtask_id: str
    status: SubtaskStatus
    output: str
    step_count: int
    failure_type: str | None = None

    def __post_init__(self) -> None:
        """校验结果类型与失败元数据边界。

        输入参数：
            无；读取当前结果字段。
        输出返回值：
            无；不合规 worker 返回值在 scheduler 内转为类型安全失败。
        """

        _validate_subtask_id(self.subtask_id)
        if not isinstance(self.status, SubtaskStatus):
            raise TypeError("subtask status 必须是 SubtaskStatus")
        if not isinstance(self.output, str):
            raise TypeError("subtask output 必须是字符串")
        if (
            not isinstance(self.step_count, int)
            or isinstance(self.step_count, bool)
            or self.step_count < 0
        ):
            raise ValueError("subtask step_count 必须是非负整数")
        if self.failure_type is not None:
            _validate_subtask_id(self.failure_type)
        if self.status is SubtaskStatus.SUCCEEDED and self.failure_type is not None:
            raise ValueError("成功 subtask 不能携带 failure_type")
        if self.status is not SubtaskStatus.SUCCEEDED and self.failure_type is None:
            raise ValueError("失败或阻塞 subtask 必须携带 failure_type")


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    """保存一次 DAG 调度的稳定顺序结果。

    输入参数：
        results：严格按原 plan 顺序排列的全部 subtask 终态。
    输出返回值：
        ``succeeded`` 属性表示所有节点是否成功。
    """

    results: tuple[SubtaskResult, ...]

    @property
    def succeeded(self) -> bool:
        """判断 plan 中每个 subtask 是否均成功。

        输入参数：
            无。
        输出返回值：
            全部结果为 ``SUCCEEDED`` 时返回 ``True``，否则返回 ``False``。
        """

        return bool(self.results) and all(
            item.status is SubtaskStatus.SUCCEEDED for item in self.results
        )


def _validate_subtask_id(value: str) -> None:
    """验证 framework 标识符可安全进入日志路径与结构化记录。

    输入参数：
        value：待验证的 subtask、依赖、worker role 或 failure type 标识。
    输出返回值：
        无；合法时正常返回，非法时抛出 ``ValueError``。
    """

    if not isinstance(value, str) or not _SUBTASK_ID_PATTERN.fullmatch(value):
        raise ValueError("framework identifier contains unsupported characters")


def _reject_dependency_cycle(subtasks: tuple[SubtaskSpec, ...]) -> None:
    """通过 Kahn 算法拒绝循环依赖。

    输入参数：
        subtasks：依赖身份已完成闭包校验的 subtask 元组。
    输出返回值：
        无；无环时正常返回，存在循环时抛出 ``ValueError``。
    """

    incoming = {
        item.subtask_id: len(item.depends_on)
        for item in subtasks
    }
    dependants: dict[str, list[str]] = {
        item.subtask_id: [] for item in subtasks
    }
    for item in subtasks:
        for dependency_id in item.depends_on:
            dependants[dependency_id].append(item.subtask_id)

    ready = [
        item.subtask_id
        for item in subtasks
        if incoming[item.subtask_id] == 0
    ]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for dependant_id in dependants[current]:
            incoming[dependant_id] -= 1
            if incoming[dependant_id] == 0:
                ready.append(dependant_id)
    if visited != len(subtasks):
        raise ValueError("execution plan contains dependency cycle")
