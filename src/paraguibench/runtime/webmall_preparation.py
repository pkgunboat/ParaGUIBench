"""把 WebMall logical URL 只物化到 Agent 可见任务投影。"""

from __future__ import annotations

from copy import deepcopy

from paraguibench.benchmark import PreparedTask
from paraguibench.integrations.webmall import (
    WebMallEnvironmentManifest,
    WebMallURLRegistry,
    WebMallURLRegistryError,
)


class WebMallPreparationError(RuntimeError):
    """表示 WebMall Agent-only 部署物化违反四店或隐私边界。"""

    code = "WEBMALL_PREPARATION_ERROR"

    def __init__(self) -> None:
        """构造不回显 instruction、origin、profile 或 gold 的固定错误。

        输入参数：
            无。
        输出返回值：
            无；异常字符串固定为公开 code。
        """

        super().__init__(self.code)


def materialize_webmall_prepared_task(
    prepared: PreparedTask,
    *,
    manifest: WebMallEnvironmentManifest,
    registry: WebMallURLRegistry,
) -> PreparedTask:
    """在保持 trusted/audit 隔离时把 Agent 指令中的 logical origin 物化。

    输入参数：
        prepared：已经解析 synthetic fixture 并完成三投影隔离的 release task。
        manifest：固定四店、reader、lease 与 reset 的 WebMall 环境 manifest。
        registry：由 manifest 四个 ``origin_env`` 在部署期构造的 URL registry。
    输出返回值：
        新 ``PreparedTask``；只有 ``agent_task.instruction`` 含 runtime origin，
        trusted task 的 expected URLs 与原始输入保持 logical，audit 只增加绑定名。
    异常：
        WebMallPreparationError：类型、task source、四店 scope、指令或 audit
            状态不合规；错误不包含任何外部值。
    """

    if (
        not isinstance(prepared, PreparedTask)
        or not isinstance(manifest, WebMallEnvironmentManifest)
        or not isinstance(registry, WebMallURLRegistry)
    ):
        raise WebMallPreparationError
    trusted_task = deepcopy(prepared.trusted_task)
    agent_task = deepcopy(prepared.agent_task)
    audit_metadata = deepcopy(prepared.audit_metadata)
    if trusted_task.get("task_source") != "WebMall":
        raise WebMallPreparationError
    instruction = agent_task.get("instruction")
    if not isinstance(instruction, str) or not instruction:
        raise WebMallPreparationError
    manifest_store_ids = tuple(store.logical_store_id for store in manifest.stores)
    if registry.logical_store_ids != manifest_store_ids:
        raise WebMallPreparationError
    if "webmall_environment" in audit_metadata:
        raise WebMallPreparationError
    try:
        agent_task["instruction"] = registry.materialize_text(instruction)
    except (TypeError, ValueError, WebMallURLRegistryError):
        raise WebMallPreparationError from None
    audit_metadata["webmall_environment"] = {
        "manifest_id": manifest.manifest_id,
        "store_universe_id": manifest.store_universe_id,
        "origin_binding_names": [store.origin_env for store in manifest.stores],
    }
    return PreparedTask(
        trusted_task=trusted_task,
        agent_task=agent_task,
        audit_metadata=audit_metadata,
    )
