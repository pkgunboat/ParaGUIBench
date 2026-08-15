"""RunStore 单元测试使用的合成 task audit factory。"""

from __future__ import annotations

from typing import Any

from paraguibench.runstore import RunVersionVector


def synthetic_run_version_vector() -> RunVersionVector:
    """构造不依赖 Git、网络或环境资产的严格合成版本向量。

    输入参数：
        无。
    输出返回值：
        六个字段均采用合法固定摘要与协议 ID 的 ``RunVersionVector``；仅供
        单元测试建立 Run 身份，不能用于真实 benchmark 结果。
    """

    return RunVersionVector(
        source_revision="tree-sha256:" + "1" * 64,
        agent_code_revision="tree-sha256:" + "2" * 64,
        evaluator_revision="tree-sha256:" + "3" * 64,
        evaluation_protocol="synthetic.evaluation.v1",
        environment_protocol="synthetic.environment.v1",
        environment_revision="manifest-sha256:" + "4" * 64,
    )


def synthetic_task_audit(task_id: str, **identity: str) -> dict[str, Any]:
    """构造不含 instruction 或 gold 的严格合成 audit metadata。

    输入参数：
        task_id：与 Attempt 路径一致的合成任务标识。
        identity：可选 task_uid/task_type/task_source/task_tag 字段。
    输出返回值：
        满足 RunStore allowlist schema 的公开测试字典。
    """

    return {
        "release_id": "synthetic-release",
        "canonical_task_sha256": "0" * 64,
        "task_id": task_id,
        **identity,
        "materialization": {
            "schema_version": 1,
            "environment_binding_names": [],
            "fixture_refs": [],
        },
    }
