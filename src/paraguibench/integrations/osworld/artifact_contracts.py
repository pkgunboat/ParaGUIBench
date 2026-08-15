"""OSWorld artifact evidence 与纯评价层共享的不可变 contract。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactMetricObservation:
    """保存单个已完整执行 metric 的安全分数投影。

    输入参数：
        metric_id：任务规则目录中的固定 metric 身份。
        score：metric 返回的有限 ``[0, 1]`` 分数。
    输出返回值：
        不含 artifact 路径或内容的不可变 observation。
    """

    metric_id: str
    score: float


@dataclass(frozen=True, slots=True)
class ArtifactSlotObservation:
    """保存单台 VM 中一个逻辑 artifact 槽位的证据。

    输入参数：
        slot_id：规则目录中不含客户机路径的逻辑槽位身份。
        status：``available`` 表示可靠读取并完成指标；
            ``missing`` 表示 Agent 未产出必需结果；
            ``read_error``、``parse_error`` 或 ``schema_error`` 表示
            evaluator 无法可靠评分。
        metric_scores：该槽位已完整计算的 metric 闭集。
    输出返回值：
        不可变槽位 observation；实际路径与文件内容不进入对象。
    """

    slot_id: str
    status: str
    metric_scores: tuple[ArtifactMetricObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactStateObservation:
    """保存单台 VM 针对一条固定任务规则的完整快照。

    输入参数：
        rule_id：生成证据时使用的版本化任务规则身份。
        source_contract_sha256：生成证据时绑定的源 evaluator
            contract 摘要，防止误接 gold 或 options。
        evidence_spec_sha256：生成证据时使用的 canonical
            ``ArtifactEvidenceSpec`` 摘要，防止 getter、locator、
            finalize 或 metric 投影发生静默漂移。
        artifact_slots：同一台 VM 快照内的全部逻辑 artifact
            槽位；多 VM 时不得拆分或拼接。
    输出返回值：
        可交给纯评价器的不可变、不含原始 artifact 的 observation。
    """

    rule_id: str
    source_contract_sha256: str
    evidence_spec_sha256: str
    artifact_slots: tuple[ArtifactSlotObservation, ...]
