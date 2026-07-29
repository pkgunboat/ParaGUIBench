"""RunStore 单元测试使用的合成 task audit factory。"""

from __future__ import annotations

from typing import Any


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
