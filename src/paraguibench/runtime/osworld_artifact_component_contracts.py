"""OSWorld artifact component live candidate 的脱敏内存合同。"""

from __future__ import annotations

from dataclasses import dataclass


OSWORLD_ARTIFACT_COMPONENT_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperation-003",
        "Operation-FileOperate-CombinationDocs-009",
        "Operation-FileOperate-CombinationDocs-010",
        "Operation-FileOperate-CombinationDocs-011",
        "Operation-FileOperate-CombinationDocs-012",
        "Operation-FileOperate-CombinationDocs-013",
        "Operation-FileOperate-CombinationDocs-014",
        "Operation-FileOperate-SearchAndWrite-001",
        "Operation-FileOperate-SearchAndWrite-003",
        "Operation-FileOperate-SearchAndWrite-005",
        "Operation-FileOperate-SearchAndWrite-009",
        "Operation-WebOperate-SearchAndWrite-001",
    }
)
OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL = "paraguibench.osworld.artifact-state.v1"
OSWORLD_ARTIFACT_ENVIRONMENT_PROTOCOL = "osworld.desktop.v1"
OSWORLD_ARTIFACT_CHROME_ENVIRONMENT_PROTOCOL = "osworld.chrome.v1"
OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL = (
    "paraguibench.osworld.artifact-component-validation.v1"
)
_WEBOPERATE_SEARCHWRITE_TASK_ID = "Operation-WebOperate-SearchAndWrite-001"


def osworld_artifact_environment_protocol(task_id: str) -> str:
    """返回 artifact candidate/receipt 对该任务要求的官方环境协议。

    输入参数：
        task_id：必须属于 12-task 闭集的 canonical ID。
    输出返回值：
        ``Operation-WebOperate-SearchAndWrite-001`` 为
        ``osworld.chrome.v1``；其余 11 项为 ``osworld.desktop.v1``。
    异常：
        OSWorldArtifactComponentContractError：任务不在闭集。
    """

    if task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS:
        raise OSWorldArtifactComponentContractError
    if task_id == _WEBOPERATE_SEARCHWRITE_TASK_ID:
        return OSWORLD_ARTIFACT_CHROME_ENVIRONMENT_PROTOCOL
    return OSWORLD_ARTIFACT_ENVIRONMENT_PROTOCOL


class OSWorldArtifactComponentContractError(ValueError):
    """表示 component candidate 的脱敏内存合同无效。"""

    code = "OSWORLD_ARTIFACT_COMPONENT_CONTRACT_INVALID"

    def __init__(self) -> None:
        """构造不回显任务、路径或观测值的固定错误。

        输入参数：无。
        输出返回值：无；异常文本固定为公开错误码。
        """

        super().__init__(self.code)


@dataclass(frozen=True, slots=True, repr=False)
class OSWorldArtifactComponentEnvironmentProof:
    """保存同一 owned OSWorld 环境完成 setup/getter/close 的事实。"""

    task_id: str
    task_setup_completed: bool
    artifact_getter_completed: bool
    evaluator_gold_completed: bool
    owned_environment_closed: bool

    def __post_init__(self) -> None:
        """验证 proof 只能表达 12-task 闭集的完整成功事实。

        输入参数：无；读取冻结实例字段。
        输出返回值：四项身份与状态严格有效时正常返回。
        异常：OSWorldArtifactComponentContractError：任务不受支持，或任一
            状态不是精确 ``True``。
        """

        if (
            self.task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
            or self.task_setup_completed is not True
            or self.artifact_getter_completed is not True
            or self.evaluator_gold_completed is not True
            or self.owned_environment_closed is not True
        ):
            raise OSWorldArtifactComponentContractError


@dataclass(frozen=True, slots=True, repr=False)
class OSWorldArtifactComponentGoldProof:
    """保存 production resolver→projection→metric 的脱敏完成事实。"""

    task_id: str
    resolver_manifest_verified: bool
    metric_projection_completed: bool
    metric_evaluation_completed: bool

    def __post_init__(self) -> None:
        """验证 gold proof 只能表达 12-task 完整成功事实。

        输入参数：无；读取冻结实例字段。
        输出返回值：任务受支持且三阶段均精确为 ``True`` 时正常返回。
        异常：OSWorldArtifactComponentContractError：任一身份或状态无效。
        """

        if (
            self.task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
            or self.resolver_manifest_verified is not True
            or self.metric_projection_completed is not True
            or self.metric_evaluation_completed is not True
        ):
            raise OSWorldArtifactComponentContractError


__all__ = [
    "OSWORLD_ARTIFACT_CHROME_ENVIRONMENT_PROTOCOL",
    "OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL",
    "OSWORLD_ARTIFACT_COMPONENT_TASK_IDS",
    "OSWORLD_ARTIFACT_ENVIRONMENT_PROTOCOL",
    "OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL",
    "OSWorldArtifactComponentContractError",
    "OSWorldArtifactComponentEnvironmentProof",
    "OSWorldArtifactComponentGoldProof",
    "osworld_artifact_environment_protocol",
]
