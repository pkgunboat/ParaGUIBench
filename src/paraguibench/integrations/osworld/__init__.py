"""OSWorld 派生 agent-server 的最小安全 controller。"""

from paraguibench.integrations.osworld.active_tab_probe import (
    ActivePageObservation,
    OSWorldActiveTabProbeError,
    capture_google_shopping_active_tab_observation,
    select_active_page_observation,
)
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID,
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
    ArtifactEvidenceLimits,
    ArtifactEvidenceSpec,
    ArtifactEvidenceSpecError,
    ArtifactMetricEvidenceSpec,
    ArtifactSlotEvidenceSpec,
    canonical_artifact_evidence_spec_json,
    project_inline_artifact_metric_inputs,
    validate_artifact_evidence_spec,
)
from paraguibench.integrations.osworld.artifact_gold_media import (
    OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT,
    OSWorldArtifactGoldMediaContractError,
    artifact_gold_media_types,
)
from paraguibench.integrations.osworld.artifact_metric_projection import (
    ArtifactMetricValueProjection,
    OSWorldArtifactMetricProjectionError,
    project_verified_artifact_metric_values,
)
from paraguibench.integrations.osworld.controller import (
    CommandResult,
    OSWorldController,
    OSWorldControllerError,
)
from paraguibench.integrations.osworld.bookmark_contracts import (
    CHROME_BOOKMARKS_PROTOCOL_ID,
    OSWORLD_BOOKMARK_TASK_BINDINGS,
    OSWORLD_BOOKMARK_TASK_IDS,
    BookmarkTaskBinding,
    ChromeBookmarkRecord,
    ChromeBookmarksObservation,
)
from paraguibench.integrations.osworld.bookmark_evidence import (
    BOOKMARKS_MAX_FILE_BYTES,
    BOOKMARKS_MAX_RESPONSE_BYTES,
    OSWorldBookmarkEvidenceError,
    OSWorldChromeBookmarkEvidenceSource,
    parse_chrome_bookmarks_json,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OSWorldOperationArtifactSource,
    OperationArtifactCaptureError,
    OperationArtifactSnapshot,
)
from paraguibench.integrations.osworld.docker_session import (
    OSWorldDockerConfig,
    OSWorldDockerSession,
    OSWorldDockerSessionError,
)
from paraguibench.integrations.osworld.state_contracts import (
    ChromeProfileNameObservation,
    GoogleShoppingActiveTabObservation,
)
from paraguibench.integrations.osworld.state_evidence import (
    OSWorldChromeStateEvidenceSource,
    OSWorldStateEvidenceError,
)
from paraguibench.integrations.osworld.task_prepare import (
    BOOKMARK_START_CONTEXT_SPEC_SCHEMA_ID,
    OSWORLD_BOOKMARK_START_CONTEXT_SPECS,
    OSWORLD_TASK_PREPARE_SPECS,
    TASK_PREPARE_SPEC_SCHEMA_ID,
    OSWorldBookmarkStartContextSpec,
    OSWorldTaskPrepareError,
    OSWorldTaskPrepareSource,
    OSWorldTaskPrepareSpec,
    canonical_bookmark_start_context_spec_json,
    canonical_task_prepare_spec_json,
)

__all__ = [
    "ActivePageObservation",
    "ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID",
    "OSWORLD_ARTIFACT_EVIDENCE_SPECS",
    "ArtifactEvidenceLimits",
    "ArtifactEvidenceSpec",
    "ArtifactEvidenceSpecError",
    "ArtifactMetricEvidenceSpec",
    "ArtifactMetricValueProjection",
    "ArtifactSlotEvidenceSpec",
    "OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT",
    "CommandResult",
    "BOOKMARKS_MAX_FILE_BYTES",
    "BOOKMARKS_MAX_RESPONSE_BYTES",
    "BOOKMARK_START_CONTEXT_SPEC_SCHEMA_ID",
    "CHROME_BOOKMARKS_PROTOCOL_ID",
    "OSWORLD_BOOKMARK_TASK_BINDINGS",
    "OSWORLD_BOOKMARK_TASK_IDS",
    "OSWORLD_BOOKMARK_START_CONTEXT_SPECS",
    "BookmarkTaskBinding",
    "ChromeBookmarkRecord",
    "ChromeBookmarksObservation",
    "OSWorldController",
    "OSWorldControllerError",
    "OSWorldArtifactMetricProjectionError",
    "OSWorldArtifactGoldMediaContractError",
    "OSWorldBookmarkEvidenceError",
    "OSWorldBookmarkStartContextSpec",
    "OSWorldChromeBookmarkEvidenceSource",
    "OSWorldOperationArtifactSource",
    "OperationArtifactCaptureError",
    "OperationArtifactSnapshot",
    "OSWorldDockerConfig",
    "OSWorldDockerSession",
    "OSWorldDockerSessionError",
    "ChromeProfileNameObservation",
    "GoogleShoppingActiveTabObservation",
    "OSWorldChromeStateEvidenceSource",
    "OSWorldStateEvidenceError",
    "OSWORLD_TASK_PREPARE_SPECS",
    "TASK_PREPARE_SPEC_SCHEMA_ID",
    "OSWorldTaskPrepareError",
    "OSWorldTaskPrepareSource",
    "OSWorldTaskPrepareSpec",
    "OSWorldActiveTabProbeError",
    "capture_google_shopping_active_tab_observation",
    "artifact_gold_media_types",
    "canonical_artifact_evidence_spec_json",
    "canonical_bookmark_start_context_spec_json",
    "canonical_task_prepare_spec_json",
    "project_inline_artifact_metric_inputs",
    "project_verified_artifact_metric_values",
    "parse_chrome_bookmarks_json",
    "select_active_page_observation",
    "validate_artifact_evidence_spec",
]
