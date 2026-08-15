"""四个 pipeline-implicit 任务的受控 artifact evidence 边界测试。"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_PROTOCOL_ID,
    HIDE_NA_ROWS_TASK_ID,
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    SEARCHWRITE_XLSX_PROTOCOL_ID,
    SEARCHWRITE_XLSX_TASK_ID,
    CrossDocumentObservation,
    HideNARowsObservation,
    ImageClassificationObservation,
    SearchWriteObservation,
    evaluate_searchwrite_xlsx,
)
from paraguibench.integrations.pipeline_implicit.artifact_evidence import (
    PipelineImplicitArtifactEvidenceError,
    PipelineImplicitArtifactEvidenceSource,
)


class _ManifestController:
    """返回可控完整 manifest 与 no-follow 文件字节。

    输入参数：
        manifest：模拟 guest helper 的有序三元组记录。
        files：guest 绝对路径到原始字节的映射。
    输出返回值：
        无；对象记录 source 实际发起的 getter 调用。
    """

    def __init__(
        self,
        manifest: tuple[tuple[str, int, str], ...],
        files: dict[str, bytes],
    ) -> None:
        self.manifest = manifest
        self.files = files
        self.manifest_calls = 0
        self.file_calls: list[str] = []

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        **limits: Any,
    ) -> tuple[tuple[str, int, str], ...]:
        """返回同一完整闭集并校验 source 传入有界参数。

        输入参数：
            guest_directory：已验证 guest shared 绝对路径。
            limits：文件数、节点、深度、字节与截止时间上限。
        输出返回值：
            构造时注入的完整 manifest。
        """

        assert guest_directory == "/home/oai/share"
        assert limits and all(
            isinstance(value, (int, float)) and value > 0 for value in limits.values()
        )
        self.manifest_calls += 1
        return self.manifest

    def collect_file_bytes(
        self,
        guest_path: str,
        **limits: Any,
    ) -> bytes:
        """记录单文件 no-follow 读取并返回注入字节。

        输入参数：
            guest_path：source 由冻结根和 manifest 相对路径拼接的路径。
            limits：单文件和响应字节上限以及截止时间。
        输出返回值：
            对应的原始文件字节。
        """

        assert limits and all(
            isinstance(value, (int, float)) and value > 0 for value in limits.values()
        )
        self.file_calls.append(guest_path)
        return self.files[guest_path]


class _ChangingManifestController(_ManifestController):
    """在捕获前后返回不同闭集的竞态替身。

    输入参数：
        manifests：第一次冻结和下载后复核的两个 manifest。
        files：首个 manifest 中 guest 路径到字节的映射。
    输出返回值：
        无；对象依次返回两个 manifest。
    """

    def __init__(
        self,
        manifests: tuple[
            tuple[tuple[str, int, str], ...],
            tuple[tuple[str, int, str], ...],
        ],
        files: dict[str, bytes],
    ) -> None:
        super().__init__(manifests[0], files)
        self.manifests = manifests

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        **limits: Any,
    ) -> tuple[tuple[str, int, str], ...]:
        """按调用次序返回捕获前后文件树。

        输入参数：
            guest_directory：冻结的 guest shared 根。
            limits：source 传入的所有正数资源上限。
        输出返回值：
            当前调用序号对应的 manifest。
        """

        assert guest_directory == "/home/oai/share"
        assert limits and all(
            isinstance(value, (int, float)) and value > 0 for value in limits.values()
        )
        manifest = self.manifests[self.manifest_calls]
        self.manifest_calls += 1
        return manifest


def _record(relative_path: str, payload: bytes) -> tuple[str, int, str]:
    """为合成文件构造完整性 manifest 记录。

    输入参数：
        relative_path：相对 guest shared 根的 POSIX 路径。
        payload：文件原始字节。
    输出返回值：
        ``(relative_path, size, sha256)`` 三元组。
    """

    return relative_path, len(payload), hashlib.sha256(payload).hexdigest()


def test_searchwrite_source_captures_atomic_closed_bundle_with_redacted_repr() -> None:
    """验证双工作簿通过两次 manifest 冻结成脱敏 typed observation。

    输入参数：
        无；使用两个合成 xlsx 字节串。
    输出返回值：
        无；文件闭集可仅由 evaluator 访问，``repr`` 不泄漏路径、
        内容或摘要，且绑定固定任务与协议。
    """

    group_1 = b"PRIVATE GROUP ONE"
    group_2 = b"PRIVATE GROUP TWO"
    manifest = (
        _record("UK_Universities_Group1.xlsx", group_1),
        _record("UK_Universities_Group2.xlsx", group_2),
    )
    controller = _ManifestController(
        manifest,
        {
            "/home/oai/share/UK_Universities_Group1.xlsx": group_1,
            "/home/oai/share/UK_Universities_Group2.xlsx": group_2,
        },
    )

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        SEARCHWRITE_XLSX_TASK_ID,
        controller,
        guest_shared_dir="/home/oai/share",
    )

    assert isinstance(observation, SearchWriteObservation)
    assert observation.complete is True
    evaluation = evaluate_searchwrite_xlsx(observation)
    assert evaluation.passed is False
    assert evaluation.score == 0.0
    assert evaluation.expected_cell_count == 9
    assert evaluation.missing_cell_count == 9
    assert evaluation.mutated_document_count == 2
    assert controller.manifest_calls == 2
    assert controller.file_calls == [
        "/home/oai/share/UK_Universities_Group1.xlsx",
        "/home/oai/share/UK_Universities_Group2.xlsx",
    ]
    rendered = repr(observation)
    for private_value in (
        "UK_Universities_Group1.xlsx",
        "PRIVATE GROUP ONE",
        manifest[0][2],
    ):
        assert private_value not in rendered


@pytest.mark.parametrize(
    ("task_id", "protocol_id"),
    (
        (HIDE_NA_ROWS_TASK_ID, HIDE_NA_ROWS_PROTOCOL_ID),
        (IMAGE_CLASSIFICATION_TASK_ID, IMAGE_CLASSIFICATION_PROTOCOL_ID),
        (CROSS_DOCUMENT_TASK_ID, CROSS_DOCUMENT_PROTOCOL_ID),
        (SEARCHWRITE_XLSX_TASK_ID, SEARCHWRITE_XLSX_PROTOCOL_ID),
    ),
)
def test_source_binds_each_canonical_task_to_its_fixed_protocol(
    task_id: str,
    protocol_id: str,
) -> None:
    """验证四个 canonical task 不共享或漂移证据协议身份。

    输入参数：
        task_id/protocol_id：当前参数化的固定任务与期望协议。
    输出返回值：
        无；空闭集观测仍必须精确绑定对应协议。
    """

    controller = _ManifestController((), {})

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        task_id,
        controller,
        guest_shared_dir="/home/oai/share",
    )

    if task_id == IMAGE_CLASSIFICATION_TASK_ID:
        assert isinstance(observation, ImageClassificationObservation)
        assert observation.complete is True
        assert observation.category_names == ()
        assert observation.categorized_images == ()
        assert observation.source_image_sha256 == ()
        assert observation.presentations == ()
        assert observation.unexpected_regular_file_count == 0
    elif task_id == HIDE_NA_ROWS_TASK_ID:
        assert protocol_id == HIDE_NA_ROWS_PROTOCOL_ID
        assert isinstance(observation, HideNARowsObservation)
        assert observation.complete is True
        assert observation.workbooks == ()
    elif task_id == SEARCHWRITE_XLSX_TASK_ID:
        assert protocol_id == SEARCHWRITE_XLSX_PROTOCOL_ID
        assert isinstance(observation, SearchWriteObservation)
        assert observation.complete is True
        assert observation.workbooks == ()
    else:
        assert protocol_id == CROSS_DOCUMENT_PROTOCOL_ID
        assert isinstance(observation, CrossDocumentObservation)
        assert observation.complete is True
        assert observation.reference_spreadsheet_unchanged is False
        assert observation.narrative is None
        assert observation.presentation is None
        assert observation.unexpected_document_count == 0


def test_source_rejects_bundle_change_with_fixed_redacted_code() -> None:
    """验证文件下载期间闭集改变不会产生混合时点 observation。

    输入参数：
        无；首次 manifest 含一个文件，复核时注入额外私有文件。
    输出返回值：
        无；source 以固定 ``BUNDLE_CHANGED`` 失败，错误不回显文件名。
    """

    payload = b"stable"
    first = (_record("expected.xlsx", payload),)
    second = tuple(
        sorted(
            first + (_record("PRIVATE-extra.xlsx", b"changed"),),
            key=lambda item: item[0].encode("utf-8"),
        )
    )
    controller = _ChangingManifestController(
        (first, second),
        {"/home/oai/share/expected.xlsx": payload},
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        PipelineImplicitArtifactEvidenceSource().capture(
            SEARCHWRITE_XLSX_TASK_ID,
            controller,
            guest_shared_dir="/home/oai/share",
        )

    assert captured.value.code == "BUNDLE_CHANGED"
    assert str(captured.value) == "BUNDLE_CHANGED"
    assert "PRIVATE" not in repr(captured.value)


@pytest.mark.parametrize(
    "relative_path",
    (
        "../PRIVATE.xlsx",
        "/PRIVATE.xlsx",
        "nested//PRIVATE.xlsx",
        "PRIVATE\n.xlsx",
    ),
)
def test_source_rejects_unsafe_paths_before_no_follow_read(
    relative_path: str,
) -> None:
    """验证穿越、绝对、空分量和控制字符在文件 getter 前失败。

    输入参数：
        relative_path：当前参数化的恶意 manifest 成员路径。
    输出返回值：
        无；source 仅返固定路径错误码，不启动任何文件读取。
    """

    payload = b"PRIVATE"
    controller = _ManifestController(
        (_record(relative_path, payload),),
        {f"/home/oai/share/{relative_path}": payload},
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        PipelineImplicitArtifactEvidenceSource().capture(
            SEARCHWRITE_XLSX_TASK_ID,
            controller,
            guest_shared_dir="/home/oai/share",
        )

    assert captured.value.code == "ARTIFACT_PATH_INVALID"
    assert str(captured.value) == "ARTIFACT_PATH_INVALID"
    assert controller.file_calls == []
    assert "PRIVATE" not in repr(captured.value)


def test_source_rejects_casefold_collision_before_no_follow_read() -> None:
    """验证 guest 上可区分但 host 可折叠的路径不会合并。

    输入参数：
        无；manifest 同时包含大小写不同的父目录。
    输出返回值：
        无；在任何 payload 下载前以固定路径错误码失败。
    """

    payload = b"same"
    paths = ("Folder/a.xlsx", "folder/b.xlsx")
    controller = _ManifestController(
        tuple(_record(path, payload) for path in paths),
        {f"/home/oai/share/{path}": payload for path in paths},
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        PipelineImplicitArtifactEvidenceSource().capture(
            SEARCHWRITE_XLSX_TASK_ID,
            controller,
            guest_shared_dir="/home/oai/share",
        )

    assert captured.value.code == "ARTIFACT_PATH_INVALID"
    assert controller.file_calls == []


def test_source_rejects_mismatched_payload_with_fixed_integrity_code() -> None:
    """验证单文件 getter 返回与冻结 manifest 不同的字节时失败。

    输入参数：
        无；manifest 绑定原 payload，getter 返回等长变造字节。
    输出返回值：
        无；错误精确为 ``FILE_INTEGRITY_INVALID`` 且不泄漏内容。
    """

    original = b"PRIVATE-A"
    changed = b"PRIVATE-B"
    controller = _ManifestController(
        (_record("expected.xlsx", original),),
        {"/home/oai/share/expected.xlsx": changed},
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        PipelineImplicitArtifactEvidenceSource().capture(
            SEARCHWRITE_XLSX_TASK_ID,
            controller,
            guest_shared_dir="/home/oai/share",
        )

    assert captured.value.code == "FILE_INTEGRITY_INVALID"
    assert str(captured.value) == "FILE_INTEGRITY_INVALID"
    assert "PRIVATE" not in repr(captured.value)


def test_source_rejects_unknown_task_before_any_guest_getter() -> None:
    """验证 source 不会为非四任务的 canonical ID 生成泛化证据。

    输入参数：
        无；提供一个不在专属闭集的任务身份。
    输出返回值：
        无；任务在 guest manifest getter 调用前被固定码拒绝。
    """

    controller = _ManifestController((), {})

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        PipelineImplicitArtifactEvidenceSource().capture(
            "Operation-FileOperate-Unknown-999",
            controller,
            guest_shared_dir="/home/oai/share",
        )

    assert captured.value.code == "TASK_NOT_REGISTERED"
    assert controller.manifest_calls == 0
    assert controller.file_calls == []


def test_source_independently_enforces_total_manifest_node_bound() -> None:
    """验证即使测试 controller 违反 helper contract，source 仍限制树节点数。

    输入参数：
        无；构造 43 个各占三个唯一路径节点的小文件。
    输出返回值：
        无；129 个节点超过 128 上限时，不启动文件下载并返固定码。
    """

    payload = b"x"
    paths = tuple(f"d{index:02d}/s{index:02d}/f.xlsx" for index in range(43))
    controller = _ManifestController(
        tuple(_record(path, payload) for path in paths),
        {f"/home/oai/share/{path}": payload for path in paths},
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        PipelineImplicitArtifactEvidenceSource().capture(
            SEARCHWRITE_XLSX_TASK_ID,
            controller,
            guest_shared_dir="/home/oai/share",
        )

    assert captured.value.code == "ARTIFACT_LIMIT_EXCEEDED"
    assert controller.file_calls == []
