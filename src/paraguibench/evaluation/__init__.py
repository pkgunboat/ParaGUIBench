"""ParaGUIBench 评价协议、适配器与差分 parity 契约。"""

from .osworld import (
    ARTIFACT_STATE_PROTOCOL_ID,
    OSWORLD_ARTIFACT_STATE_TASK_RULES,
    ArtifactMetricObservation,
    ArtifactMetricRule,
    ArtifactSlotObservation,
    ArtifactSlotRule,
    ArtifactStateObservation,
    ArtifactStateTaskRule,
    OSWorldArtifactStateEvaluation,
    OSWorldArtifactStateEvaluationError,
    evaluate_artifact_state_observations,
)
from .parity import (
    EvaluatorObservation,
    EvaluatorOutcome,
    EvaluatorParityDifference,
    EvaluatorParityError,
    EvaluatorParityReport,
    compare_evaluator_observation_files,
)

__all__ = [
    "ARTIFACT_STATE_PROTOCOL_ID",
    "OSWORLD_ARTIFACT_STATE_TASK_RULES",
    "ArtifactMetricObservation",
    "ArtifactMetricRule",
    "ArtifactSlotObservation",
    "ArtifactSlotRule",
    "ArtifactStateObservation",
    "ArtifactStateTaskRule",
    "EvaluatorObservation",
    "EvaluatorOutcome",
    "EvaluatorParityDifference",
    "EvaluatorParityError",
    "EvaluatorParityReport",
    "OSWorldArtifactStateEvaluation",
    "OSWorldArtifactStateEvaluationError",
    "compare_evaluator_observation_files",
    "evaluate_artifact_state_observations",
]
