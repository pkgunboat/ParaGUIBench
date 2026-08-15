"""BatchOperationPPT-003 的 pinned 内容哈希分类评价协议。

协议以 HF 固定 commit 的图片 SHA-256 为身份，不依赖 Agent
是否重命名图片。已分类图可从源 ``images`` 目录移走，也可复制
后保留；评价结果只包含数量与固定 reason code。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from types import MappingProxyType


IMAGE_CLASSIFICATION_TASK_ID = "Operation-FileOperate-BatchOperationPPT-003"
IMAGE_CLASSIFICATION_PROTOCOL_ID = (
    "paraguibench.operation.image-classification.sha256.v1"
)

PINNED_CLASSIFIED_IMAGE_SHA256 = MappingProxyType(
    {
        "basketball": (
            "df28be039df8fe12f6edba1654ba9a239cd463bfdba5189d4530d3de74a44d1d",
            "920c257be076389b03fe784a05181d91d45f3f679b554d4717b5786880b8ccba",
            "67cc896c74124417543c367b28a136539a7e218f4c2249f12c86d7b8aa4e89ee",
        ),
        "soccer": (
            "98e6eb3d5cfe30a7918ddefea122353451639a515421d8234e2944424f9f8dd5",
            "87260c5b287cb41013b590b46360ee35ceb3e64a0fb1b3aa5db4c00d7c9d47ae",
            "dfcd32b378a7f9e585036373c5a24d4cb92bb0360d0714f4afb3dfd13ee6ebdf",
            "1b20d8c30349856b961b870391dfb4a50e19af652295fd9b24a00d7ae39a8593",
        ),
        "volleyball": (
            "4ecc1ed717d25250dcb9ff12b92cb8361ad0d88d90f06d0fc3a606e1a0506485",
            "5eabea4b59197f829ed7fb12e8c5a220d87b12427baeb79551126cff69389032",
        ),
        "esport": (
            "8f43a7206becfb714d8e978124fa3416e684a804a2d225d27d3795c15bb1b0f0",
            "0ab17ba065b88996a05bd6176a4626a16a02f1db4048fd6f92da83046f86b058",
            "d5dfa19f5270f6005b33eca0afe95509c9a5b5c317db26cdb4abc40a2d2fbbe4",
        ),
    }
)
PINNED_UNCLASSIFIED_IMAGE_SHA256 = (
    "56691d1a2e16c1f02a69f6451ea0cf36800943b15cf1e3f8cf9ac5ba452fc343",
    "7b0c75780124245d509ab97373c52c90f9d337519babbaafab02cabb4ad6562d",
    "4a3f86e36920a19a5ee7922d24fe45c795b597f8bd4b8f1224c869c0a5abe7f8",
    "66a6116e2912dcaed8864256dc77826e50e5f9f104f41feb7b6460d0ac50981a",
)
PINNED_PRESENTATION_SHA256 = MappingProxyType(
    {
        "ppt-1": "9482822e725c18c7aca918d244888353442d77ce9d0962c7652d6bb9eaf5b7fe",
        "ppt-2": "3ace91b9f45521a96857c94db36fa5a21d0f90f7401636c08b2d12d691166c05",
        "ppt-3": "2ad2b30684b8e8fe690b8b5d94b27878b41266b7b945870d30273effdb9c6cf0",
        "ppt-4": "0d5080eacc501d8c5e67e32ef1a8bc8535fedfb420df1e9d31b3ad961c12cdfc",
    }
)

_EXPECTED_CLASSIFICATIONS = frozenset(
    (category, digest)
    for category, digests in PINNED_CLASSIFIED_IMAGE_SHA256.items()
    for digest in digests
)
_CLASSIFIED_DIGESTS = frozenset(digest for _, digest in _EXPECTED_CLASSIFICATIONS)
_UNCLASSIFIED_DIGESTS = frozenset(PINNED_UNCLASSIFIED_IMAGE_SHA256)
_ALL_IMAGE_DIGESTS = _CLASSIFIED_DIGESTS | _UNCLASSIFIED_DIGESTS
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_LOGICAL_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_MAX_CATEGORIES = 64
_MAX_IMAGES = 256
_MAX_PRESENTATIONS = 32
_MAX_UNEXPECTED_FILES = 4096
_REASON_ORDER = (
    "MISSING_CATEGORY",
    "UNEXPECTED_CATEGORY",
    "MISSING_CLASSIFIED_IMAGE",
    "MISCLASSIFIED_IMAGE",
    "DUPLICATE_CLASSIFIED_IMAGE",
    "UNCLASSIFIED_IMAGE_MISSING",
    "DUPLICATE_SOURCE_IMAGE",
    "UNEXPECTED_IMAGE",
    "PRESENTATION_SET_CHANGED",
    "UNEXPECTED_FILE",
)


class ImageClassificationEvaluationError(RuntimeError):
    """表示分类目录证据不完整、重复或超出边界。"""


@dataclass(frozen=True, slots=True)
class CategorizedImage:
    """保存一份分类目录内图片的短生命周观测。

    输入参数：
        category_id：分类目录的逻辑名，评价时忽略 ASCII 大小写。
        content_sha256：文件内容 SHA-256，不依赖文件名。
    输出返回值：
        不可变内存证据；不得直接持久化。
    """

    category_id: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PresentationArtifact:
    """保存一个 pinned PPT 的逻辑身份与内容哈希。

    输入参数：
        document_id：固定 PPT 逻辑身份。
        content_sha256：当前 artifact 的 SHA-256。
    输出返回值：
        不可变 PPT 证据。
    """

    document_id: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ImageClassificationObservation:
    """保存当前 Attempt 分类目录的闭集观测。

    输入参数：
        complete：是否已完整枚举 artifact 根、源图目录和分类目录。
        category_names：实际存在的分类目录逻辑名。
        categorized_images：所有分类目录内的图片内容观测。
        source_image_sha256：仍保留在源 images 目录的内容哈希。
        presentations：根目录四个 PPT 观测。
        unexpected_regular_file_count：不属于上述集合的常规文件数。
    输出返回值：
        不可变观测批次。
    """

    complete: bool
    category_names: tuple[str, ...]
    categorized_images: tuple[CategorizedImage, ...]
    source_image_sha256: tuple[str, ...]
    presentations: tuple[PresentationArtifact, ...]
    unexpected_regular_file_count: int


@dataclass(frozen=True, slots=True)
class ImageClassificationEvaluation:
    """保存不含目录名、文件名或哈希的脱敏结果。

    输入参数：
        protocol_id/passed/score/reason_codes：协议、结论、十二张分类
            图的固定分母得分和原因码。
        其余字段：分类、图片、PPT 和额外文件的脱敏计数。
    输出返回值：
        可安全写入 RunStore details 的不可变结果。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    expected_category_count: int
    matched_category_count: int
    unexpected_category_count: int
    expected_classification_count: int
    matched_classification_count: int
    missing_classification_count: int
    misclassified_image_count: int
    duplicate_classification_count: int
    unexpected_image_count: int
    missing_unclassified_image_count: int
    duplicate_source_image_count: int
    changed_presentation_count: int
    unexpected_regular_file_count: int


def evaluate_image_classification(
    observation: ImageClassificationObservation,
) -> ImageClassificationEvaluation:
    """按 pinned 内容多集合评价 PPT-003 分类终态。

    输入参数：
        observation：受控 source 对当前 Attempt artifact 根的完整观测。
    输出返回值：
        兼容 move/copy，但对漏项、错类、重复、未知内容、
            PPT 改动和额外文件严格失败的脱敏结果。
    异常：
        ImageClassificationEvaluationError：证据不完整或字段无效。
    """

    normalized = _validate_observation(observation)
    category_counter, categorized_counter, source_counter, presentations = normalized

    expected_categories = set(PINNED_CLASSIFIED_IMAGE_SHA256)
    matched_categories = sum(
        1 for category in expected_categories if category_counter[category] == 1
    )
    missing_categories = sum(
        1 for category in expected_categories if category_counter[category] == 0
    )
    unexpected_categories = sum(
        count
        for category, count in category_counter.items()
        if category not in expected_categories
    ) + sum(max(0, category_counter[category] - 1) for category in expected_categories)

    matched_classifications = sum(
        1 for pair in _EXPECTED_CLASSIFICATIONS if categorized_counter[pair] >= 1
    )
    missing_classifications = len(_EXPECTED_CLASSIFICATIONS) - matched_classifications
    misclassified = sum(
        count
        for (category, digest), count in categorized_counter.items()
        if digest in _ALL_IMAGE_DIGESTS
        and (category, digest) not in _EXPECTED_CLASSIFICATIONS
    )
    categorized_digest_counter = Counter(
        {
            digest: sum(
                count
                for (_, candidate_digest), count in categorized_counter.items()
                if candidate_digest == digest
            )
            for _, digest in categorized_counter
        }
    )
    duplicate_classifications = sum(
        max(0, count - 1) for count in categorized_digest_counter.values()
    )
    unexpected_images = sum(
        count
        for (_, digest), count in categorized_counter.items()
        if digest not in _ALL_IMAGE_DIGESTS
    ) + sum(
        count
        for digest, count in source_counter.items()
        if digest not in _ALL_IMAGE_DIGESTS
    )
    missing_unclassified = sum(
        1 for digest in _UNCLASSIFIED_DIGESTS if source_counter[digest] == 0
    )
    duplicate_source = sum(max(0, count - 1) for count in source_counter.values())

    expected_presentations = set(PINNED_PRESENTATION_SHA256)
    actual_presentation_ids = set(presentations)
    changed_presentations = len(expected_presentations - actual_presentation_ids)
    changed_presentations += len(actual_presentation_ids - expected_presentations)
    changed_presentations += sum(
        1
        for document_id in expected_presentations & actual_presentation_ids
        if presentations[document_id] != PINNED_PRESENTATION_SHA256[document_id]
    )

    reason_set: set[str] = set()
    if missing_categories:
        reason_set.add("MISSING_CATEGORY")
    if unexpected_categories:
        reason_set.add("UNEXPECTED_CATEGORY")
    if missing_classifications:
        reason_set.add("MISSING_CLASSIFIED_IMAGE")
    if misclassified:
        reason_set.add("MISCLASSIFIED_IMAGE")
    if duplicate_classifications:
        reason_set.add("DUPLICATE_CLASSIFIED_IMAGE")
    if missing_unclassified:
        reason_set.add("UNCLASSIFIED_IMAGE_MISSING")
    if duplicate_source:
        reason_set.add("DUPLICATE_SOURCE_IMAGE")
    if unexpected_images:
        reason_set.add("UNEXPECTED_IMAGE")
    if changed_presentations:
        reason_set.add("PRESENTATION_SET_CHANGED")
    if observation.unexpected_regular_file_count:
        reason_set.add("UNEXPECTED_FILE")
    reason_codes = tuple(code for code in _REASON_ORDER if code in reason_set)
    return ImageClassificationEvaluation(
        protocol_id=IMAGE_CLASSIFICATION_PROTOCOL_ID,
        passed=not reason_codes,
        score=round(matched_classifications / len(_EXPECTED_CLASSIFICATIONS), 4),
        reason_codes=reason_codes,
        expected_category_count=len(expected_categories),
        matched_category_count=matched_categories,
        unexpected_category_count=unexpected_categories,
        expected_classification_count=len(_EXPECTED_CLASSIFICATIONS),
        matched_classification_count=matched_classifications,
        missing_classification_count=missing_classifications,
        misclassified_image_count=misclassified,
        duplicate_classification_count=duplicate_classifications,
        unexpected_image_count=unexpected_images,
        missing_unclassified_image_count=missing_unclassified,
        duplicate_source_image_count=duplicate_source,
        changed_presentation_count=changed_presentations,
        unexpected_regular_file_count=observation.unexpected_regular_file_count,
    )


def _normalize_logical_id(value: object) -> str:
    """验证并归一化分类或文档逻辑身份。

    输入参数：
        value：待归一化逻辑身份。
    输出返回值：
        去首尾空白并转小写的 ASCII 身份。
    异常：
        ImageClassificationEvaluationError：身份类型或形式无效。
    """

    if not isinstance(value, str):
        raise ImageClassificationEvaluationError("LOGICAL_ID_INVALID")
    normalized = value.strip().lower()
    if not _LOGICAL_ID_PATTERN.fullmatch(normalized):
        raise ImageClassificationEvaluationError("LOGICAL_ID_INVALID")
    return normalized


def _validate_sha256(value: object) -> str:
    """验证内容哈希是否为小写 SHA-256。

    输入参数：
        value：待验证哈希。
    输出返回值：
        验证通过的原字符串。
    异常：
        ImageClassificationEvaluationError：哈希不是 64 位小写十六进制。
    """

    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ImageClassificationEvaluationError("CONTENT_ID_INVALID")
    return value


def _validate_observation(
    observation: ImageClassificationObservation,
) -> tuple[
    Counter[str],
    Counter[tuple[str, str]],
    Counter[str],
    dict[str, str],
]:
    """验证完整分类观测的类型、资源和唯一性边界。

    输入参数：
        observation：待验证观测批次。
    输出返回值：
        分类目录 Counter、分类图多集合、源图多集合与 PPT 映射。
    异常：
        ImageClassificationEvaluationError：采集不完整或字段无效。
    """

    if not isinstance(observation, ImageClassificationObservation):
        raise ImageClassificationEvaluationError("EVIDENCE_INVALID")
    if observation.complete is not True:
        raise ImageClassificationEvaluationError("EVIDENCE_INCOMPLETE")
    if (
        not isinstance(observation.category_names, tuple)
        or len(observation.category_names) > _MAX_CATEGORIES
        or not isinstance(observation.categorized_images, tuple)
        or len(observation.categorized_images) > _MAX_IMAGES
        or not isinstance(observation.source_image_sha256, tuple)
        or len(observation.source_image_sha256) > _MAX_IMAGES
        or not isinstance(observation.presentations, tuple)
        or len(observation.presentations) > _MAX_PRESENTATIONS
        or not isinstance(observation.unexpected_regular_file_count, int)
        or isinstance(observation.unexpected_regular_file_count, bool)
        or not 0 <= observation.unexpected_regular_file_count <= _MAX_UNEXPECTED_FILES
    ):
        raise ImageClassificationEvaluationError("EVIDENCE_INVALID")

    category_counter = Counter(
        _normalize_logical_id(category) for category in observation.category_names
    )
    categorized_counter: Counter[tuple[str, str]] = Counter()
    for item in observation.categorized_images:
        if not isinstance(item, CategorizedImage):
            raise ImageClassificationEvaluationError("IMAGE_SET_INVALID")
        categorized_counter[
            (
                _normalize_logical_id(item.category_id),
                _validate_sha256(item.content_sha256),
            )
        ] += 1
    source_counter = Counter(
        _validate_sha256(digest) for digest in observation.source_image_sha256
    )
    presentations: dict[str, str] = {}
    for item in observation.presentations:
        if not isinstance(item, PresentationArtifact):
            raise ImageClassificationEvaluationError("PRESENTATION_SET_INVALID")
        document_id = _normalize_logical_id(item.document_id)
        if document_id in presentations:
            raise ImageClassificationEvaluationError("PRESENTATION_SET_INVALID")
        presentations[document_id] = _validate_sha256(item.content_sha256)
    return category_counter, categorized_counter, source_counter, presentations
