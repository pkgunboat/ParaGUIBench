"""pipeline implicit component candidate 的不可变公开合同。"""

from __future__ import annotations


PIPELINE_IMPLICIT_COMPONENT_RECEIPT_KIND = "paraguibench.pipeline-implicit.component.v1"
PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL = (
    "paraguibench.pipeline-implicit.component-validation.v1"
)
PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL = "osworld.desktop.v1"
PIPELINE_IMPLICIT_COMPONENT_CHECK_NAMES = frozenset(
    {
        "image_manifest_held",
        "qcow2_snapshot_verified",
        "container_image_verified",
        "task_prepare_completed",
        "reference_bundle_materialized",
        "typed_observation_captured",
        "task_evaluator_completed",
        "owned_environment_closed",
    }
)


__all__ = [
    "PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL",
    "PIPELINE_IMPLICIT_COMPONENT_CHECK_NAMES",
    "PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL",
    "PIPELINE_IMPLICIT_COMPONENT_RECEIPT_KIND",
]
