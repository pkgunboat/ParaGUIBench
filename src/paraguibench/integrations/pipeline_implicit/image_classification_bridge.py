"""PPT-003 generic artifact 闭集到强类型分类观测的转换边界。"""

from __future__ import annotations

from pathlib import PurePosixPath
import re

from paraguibench.evaluation.pipeline_implicit import (
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    CategorizedImage,
    ImageClassificationObservation,
    PresentationArtifact,
)

from .artifact_evidence import (
    PipelineImplicitArtifactEvidenceError,
    PipelineImplicitArtifactObservation,
)


_CATEGORY_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_PRESENTATION_DOCUMENT_IDS = {
    "ppt1.pptx": "ppt-1",
    "ppt2.pptx": "ppt-2",
    "ppt3.pptx": "ppt-3",
    "ppt4.pptx": "ppt-4",
}


def build_image_classification_observation(
    artifact_observation: PipelineImplicitArtifactObservation,
) -> ImageClassificationObservation:
    """把已冻结的 PPT-003 文件闭集投影为正式 typed observation。

    输入参数：
        artifact_observation：经 manifest—nofollow—manifest 和逐文件
            size/SHA-256 双重校验的 production generic observation。
    输出返回值：
        只包含类别逻辑身份、已校验 SHA-256 和脱敏额外文件
        计数的 ``ImageClassificationObservation``。
    异常：
        PipelineImplicitArtifactEvidenceError：generic observation 的任务、
            协议或完整性身份不匹配；异常仅含固定脱敏码。

    转换不读取 Agent final text，也不重新信任文件名来判定
    图片身份；图片身份始终由 production capture 已验证的
    内容 SHA-256 决定。
    """

    if (
        not isinstance(
            artifact_observation,
            PipelineImplicitArtifactObservation,
        )
        or artifact_observation.task_id != IMAGE_CLASSIFICATION_TASK_ID
        or artifact_observation.protocol_id != IMAGE_CLASSIFICATION_PROTOCOL_ID
        or artifact_observation.complete is not True
    ):
        raise PipelineImplicitArtifactEvidenceError("TYPED_OBSERVATION_INVALID")

    category_names: set[str] = set()
    categorized_images: list[CategorizedImage] = []
    source_image_sha256: list[str] = []
    presentations: list[PresentationArtifact] = []
    unexpected_regular_file_count = 0

    for artifact_file in artifact_observation.iter_files_for_evaluator():
        path = PurePosixPath(artifact_file.relative_path)
        presentation_id = (
            _PRESENTATION_DOCUMENT_IDS.get(path.name) if len(path.parts) == 1 else None
        )
        if presentation_id is not None:
            presentations.append(
                PresentationArtifact(
                    document_id=presentation_id,
                    content_sha256=artifact_file.sha256,
                )
            )
            continue

        if len(path.parts) == 2 and path.parts[0] == "images":
            source_image_sha256.append(artifact_file.sha256)
            continue

        if (
            len(path.parts) == 2
            and _CATEGORY_ID_PATTERN.fullmatch(path.parts[0]) is not None
        ):
            category_id = path.parts[0]
            category_names.add(category_id)
            categorized_images.append(
                CategorizedImage(
                    category_id=category_id,
                    content_sha256=artifact_file.sha256,
                )
            )
            continue

        unexpected_regular_file_count += 1

    return ImageClassificationObservation(
        complete=True,
        category_names=tuple(
            sorted(category_names, key=lambda value: value.encode("utf-8"))
        ),
        categorized_images=tuple(categorized_images),
        source_image_sha256=tuple(source_image_sha256),
        presentations=tuple(presentations),
        unexpected_regular_file_count=unexpected_regular_file_count,
    )


__all__ = ["build_image_classification_observation"]
