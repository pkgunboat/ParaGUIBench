"""BatchOperationPPT-003 内容哈希分类协议的回归测试。"""

from __future__ import annotations

from paraguibench.evaluation.pipeline_implicit.image_classification import (
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    PINNED_CLASSIFIED_IMAGE_SHA256,
    PINNED_PRESENTATION_SHA256,
    PINNED_UNCLASSIFIED_IMAGE_SHA256,
    CategorizedImage,
    ImageClassificationObservation,
    PresentationArtifact,
    evaluate_image_classification,
)


def _presentations() -> tuple[PresentationArtifact, ...]:
    """构造与 pinned 输入完全一致的四个 PPT 观测。

    输入参数：无。
    输出返回值：四个逻辑 PPT 身份及其 SHA-256。
    """

    return tuple(
        PresentationArtifact(document_id, digest)
        for document_id, digest in PINNED_PRESENTATION_SHA256.items()
    )


def _categorized() -> tuple[CategorizedImage, ...]:
    """构造十二张图片的正确分类哈希多集合。

    输入参数：无。
    输出返回值：按固定类别组织的分类图片观测。
    """

    return tuple(
        CategorizedImage(category.upper(), digest)
        for category, digests in PINNED_CLASSIFIED_IMAGE_SHA256.items()
        for digest in digests
    )


def _all_source_images() -> tuple[str, ...]:
    """构造 copy 行为下保留的全部十六张源图片。

    输入参数：无。
    输出返回值：全部已知内容 SHA-256。
    """

    return (
        tuple(
            digest
            for digests in PINNED_CLASSIFIED_IMAGE_SHA256.values()
            for digest in digests
        )
        + PINNED_UNCLASSIFIED_IMAGE_SHA256
    )


def _valid_observation(*, move: bool) -> ImageClassificationObservation:
    """构造 copy 或 move 两种合法终态。

    输入参数：
        move：为真时源目录仅剩四张未归类图片。
    输出返回值：完整分类观测。
    """

    return ImageClassificationObservation(
        complete=True,
        category_names=("Basketball", "ESPORT", "soccer", "volleyball"),
        categorized_images=_categorized(),
        source_image_sha256=(
            PINNED_UNCLASSIFIED_IMAGE_SHA256 if move else _all_source_images()
        ),
        presentations=_presentations(),
        unexpected_regular_file_count=0,
    )


def test_copy_and_move_are_both_valid_closed_world_states() -> None:
    """验证保留源图与移走已分类图都可通过。"""

    copy_result = evaluate_image_classification(_valid_observation(move=False))
    move_result = evaluate_image_classification(_valid_observation(move=True))

    assert copy_result.protocol_id == IMAGE_CLASSIFICATION_PROTOCOL_ID
    assert copy_result.passed is True
    assert move_result.passed is True
    assert copy_result.score == move_result.score == 1.0
    assert copy_result.matched_classification_count == 12


def test_noop_fails_even_when_presentations_and_source_images_are_intact() -> None:
    """验证只保留原始 PPT/images 的 no-op 不会假通过。"""

    result = evaluate_image_classification(
        ImageClassificationObservation(
            complete=True,
            category_names=(),
            categorized_images=(),
            source_image_sha256=_all_source_images(),
            presentations=_presentations(),
            unexpected_regular_file_count=0,
        )
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.missing_classification_count == 12
    assert result.reason_codes == (
        "MISSING_CATEGORY",
        "MISSING_CLASSIFIED_IMAGE",
    )


def test_wrong_category_duplicate_and_unknown_image_are_rejected() -> None:
    """验证错类、重复分类和非源图片都被闭集拒绝。"""

    source = _valid_observation(move=False)
    first = source.categorized_images[0]
    wrong = CategorizedImage("soccer", first.content_sha256)
    unknown = CategorizedImage("soccer", "f" * 64)
    result = evaluate_image_classification(
        ImageClassificationObservation(
            complete=True,
            category_names=source.category_names,
            categorized_images=(wrong,)
            + source.categorized_images[1:]
            + (wrong, unknown),
            source_image_sha256=source.source_image_sha256,
            presentations=source.presentations,
            unexpected_regular_file_count=0,
        )
    )

    assert result.passed is False
    assert result.matched_classification_count == 11
    assert result.misclassified_image_count == 2
    assert result.duplicate_classification_count == 1
    assert result.unexpected_image_count == 1
    assert "MISCLASSIFIED_IMAGE" in result.reason_codes
    assert "DUPLICATE_CLASSIFIED_IMAGE" in result.reason_codes
    assert "UNEXPECTED_IMAGE" in result.reason_codes


def test_changed_presentation_or_extra_regular_file_fails() -> None:
    """验证修改 PPT 或生成任何额外常规文件均失败。"""

    source = _valid_observation(move=False)
    changed_presentations = (
        PresentationArtifact(source.presentations[0].document_id, "0" * 64),
    ) + source.presentations[1:]
    result = evaluate_image_classification(
        ImageClassificationObservation(
            complete=True,
            category_names=source.category_names,
            categorized_images=source.categorized_images,
            source_image_sha256=source.source_image_sha256,
            presentations=changed_presentations,
            unexpected_regular_file_count=1,
        )
    )

    assert result.passed is False
    assert result.changed_presentation_count == 1
    assert result.unexpected_regular_file_count == 1
    assert result.reason_codes == (
        "PRESENTATION_SET_CHANGED",
        "UNEXPECTED_FILE",
    )


def test_public_result_does_not_expose_hashes_or_categories() -> None:
    """验证可持久化结果仅含计数与固定 reason code。"""

    rendered = repr(evaluate_image_classification(_valid_observation(move=False)))
    for secret in (
        "basketball",
        "soccer",
        next(iter(PINNED_PRESENTATION_SHA256.values())),
        PINNED_UNCLASSIFIED_IMAGE_SHA256[0],
    ):
        assert secret not in rendered.lower()
