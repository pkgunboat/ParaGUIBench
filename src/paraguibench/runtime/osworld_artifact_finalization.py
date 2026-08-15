"""声明已经接入正式 environment 生命周期的 OSWorld artifact 收尾能力。"""

from __future__ import annotations

from paraguibench.integrations.osworld.artifact_finalizer import (
    OSWORLD_ARTIFACT_FINALIZER_ACTIONS,
)


OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS = frozenset(
    task_id
    for task_id, action_id in OSWORLD_ARTIFACT_FINALIZER_ACTIONS.items()
    if action_id != "none"
)


__all__ = ["OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS"]
