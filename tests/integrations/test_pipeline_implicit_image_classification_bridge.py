"""PPT-003 production artifact capture 到正式评价器的纵向测试。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any

import pytest

from paraguibench.evaluation.pipeline_implicit import (
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    PINNED_CLASSIFIED_IMAGE_SHA256,
    PINNED_PRESENTATION_SHA256,
    PINNED_UNCLASSIFIED_IMAGE_SHA256,
    evaluate_image_classification,
)
from paraguibench.integrations.pipeline_implicit import (
    PipelineImplicitArtifactEvidenceError,
    PipelineImplicitArtifactEvidenceSource,
    PipelineImplicitArtifactFile,
    PipelineImplicitArtifactObservation,
    build_image_classification_observation,
)


_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_PPT003_FIXTURE_DIR"


class _DirectoryController:
    """把已核验的本地固定 revision 树暴露为 guest controller seam。

    输入参数：
        root：包含 PPT-003 完整 gold 闭集的本地目录。
    输出返回值：
        无；实例通过 production source 需要的两个窄接口返回
        稳定 manifest 和单文件字节。
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        **limits: Any,
    ) -> tuple[tuple[str, int, str], ...]:
        """返回按 UTF-8 字节序排列的完整常规文件树。

        输入参数：
            guest_directory：production source 冻结的 guest shared 根。
            limits：source 下发的资源与超时上限。
        输出返回值：
            ``(relative_path, size, sha256)`` 的有序不可变 tuple。
        """

        assert guest_directory == "/home/oai/share"
        assert limits
        records = []
        for path in self._root.rglob("*"):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            relative_path = path.relative_to(self._root).as_posix()
            records.append(
                (
                    relative_path,
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                )
            )
        return tuple(sorted(records, key=lambda item: item[0].encode("utf-8")))

    def collect_file_bytes(
        self,
        guest_path: str,
        **limits: Any,
    ) -> bytes:
        """按 production source 给出的 guest 路径返回真实字节。

        输入参数：
            guest_path：必须位于固定 ``/home/oai/share`` 根下。
            limits：source 下发的单文件资源与超时上限。
        输出返回值：
            对应固定 revision 成员的原始字节。
        """

        assert limits
        guest_root = PurePosixPath("/home/oai/share")
        relative_path = PurePosixPath(guest_path).relative_to(guest_root)
        return self._root.joinpath(*relative_path.parts).read_bytes()


class _ManifestOnlyController:
    """在路径预检阶段返回可控 manifest 并禁止文件读取。

    输入参数：
        manifest：应在单文件 getter 前被 production source 拒绝的记录。
    输出返回值：
        无；如 source 错误启动 payload 读取，测试立即失败。
    """

    def __init__(
        self,
        manifest: tuple[tuple[str, int, str], ...],
    ) -> None:
        self._manifest = manifest
        self.file_calls = 0

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        **limits: Any,
    ) -> tuple[tuple[str, int, str], ...]:
        """返回预置的有序 manifest。

        输入参数：
            guest_directory：production source 要求的固定 guest 根。
            limits：source 下发的有界资源参数。
        输出返回值：
            构造时传入的 manifest tuple。
        """

        assert guest_directory == "/home/oai/share"
        assert limits
        return self._manifest

    def collect_file_bytes(
        self,
        guest_path: str,
        **limits: Any,
    ) -> bytes:
        """在路径碰撞未被预先拒绝时明确使测试失败。

        输入参数：
            guest_path/limits：不应由 source 产生的文件读取参数。
        输出返回值：
            不返回；直接报告路径闭集预检失效。
        """

        del guest_path, limits
        self.file_calls += 1
        pytest.fail("path collision reached the file getter")


def _fixed_revision_gold_fixture() -> Path:
    """返回由显式环境变量配置的 32 文件测试树。

    输入参数：无。
    输出返回值：
        存在时返回完整 gold 树路径；未配置时跳过需要
        download-only 资产的纵向测试，不在仓库内再分发原始文件。
    """

    raw_path = os.environ.get(_FIXTURE_ENVIRONMENT_VARIABLE)
    if raw_path is None:
        pytest.skip(
            f"{_FIXTURE_ENVIRONMENT_VARIABLE} is required for download-only fixture"
        )
    fixture_path = Path(raw_path)
    if not fixture_path.is_dir():
        pytest.fail("PPT-003 fixed-revision fixture directory is unavailable")
    return fixture_path


def _fixed_revision_input_fixture() -> Path:
    """返回与 32 文件 gold 树同级的 20 文件 input 树。

    输入参数：无。
    输出返回值：
        固定 revision input 目录；配置根未提供时由 gold helper
        执行同一 download-only 跳过策略。
    """

    gold_fixture = _fixed_revision_gold_fixture()
    input_fixture = gold_fixture.parent / "input"
    if not input_fixture.is_dir():
        pytest.fail("PPT-003 fixed-revision input fixture directory is unavailable")
    return input_fixture


def _copy_fixed_revision_gold_fixture(tmp_path: Path) -> Path:
    """把 download-only gold 树复制到当前测试隔离目录。

    输入参数：
        tmp_path：pytest 为当前测试提供的空临时目录。
    输出返回值：
        可安全删除、移动或增加成员的独立 32 文件树。
    """

    target = tmp_path / "gold"
    shutil.copytree(_fixed_revision_gold_fixture(), target)
    return target


def _captured_file(
    relative_path: str,
    content_sha256: str,
) -> PipelineImplicitArtifactFile:
    """构造一个仅用于 bridge 单元测试的 generic 文件身份。

    输入参数：
        relative_path：已经 production path validator 允许的相对路径。
        content_sha256：固定 revision 已核对的内容摘要。
    输出返回值：
        不包含原始资产字节的 generic file observation；真实
        payload 完整性由前述 production capture 纵向测试独立覆盖。
    """

    return PipelineImplicitArtifactFile(
        relative_path=relative_path,
        size_bytes=0,
        sha256=content_sha256,
        _payload=b"",
    )


def test_bridge_maps_pinned_generic_identities_without_typed_fake() -> None:
    """验证默认离线门禁也覆盖 generic→typed→formal evaluator。

    输入参数：
        无；仅使用已核对的路径类别和 SHA-256 身份，不在
        仓库内再分发 download-only 图片/PPT 字节。
    输出返回值：
        无；bridge 必须自行创建 ``ImageClassificationObservation``
        并被正式 evaluator 满分接受，测试不注入 typed fake。
    """

    files = []
    source_digests = []
    for category_id, digests in PINNED_CLASSIFIED_IMAGE_SHA256.items():
        for index, digest in enumerate(digests, start=1):
            files.append(
                _captured_file(
                    f"{category_id}/renamed-{index}.bin",
                    digest,
                )
            )
            source_digests.append(digest)
    source_digests.extend(PINNED_UNCLASSIFIED_IMAGE_SHA256)
    files.extend(
        _captured_file(f"images/source-{index}.bin", digest)
        for index, digest in enumerate(source_digests, start=1)
    )
    files.extend(
        _captured_file(f"ppt{index}.pptx", digest)
        for index, digest in enumerate(
            PINNED_PRESENTATION_SHA256.values(),
            start=1,
        )
    )
    generic_observation = PipelineImplicitArtifactObservation(
        task_id=IMAGE_CLASSIFICATION_TASK_ID,
        protocol_id=IMAGE_CLASSIFICATION_PROTOCOL_ID,
        complete=True,
        _files=tuple(
            sorted(
                files,
                key=lambda item: item.relative_path.encode("utf-8"),
            )
        ),
    )

    typed_observation = build_image_classification_observation(generic_observation)
    evaluation = evaluate_image_classification(typed_observation)

    assert evaluation.passed is True
    assert evaluation.score == 1.0
    assert evaluation.reason_codes == ()
    assert evaluation.expected_classification_count == 12


def test_production_capture_feeds_fixed_revision_gold_to_formal_evaluator() -> None:
    """验证 32 文件闭集经 production capture 可直接满分评价。

    输入参数：
        无；使用 HF 固定 revision 的 20 个 input 成员构成
        32 个 gold 成员，其中保留 16 个 ``images`` 源图副本。
    输出返回值：
        无；正式 source 必须返回正式 evaluator 可接受的观测，
        不注入 typed fake，也不读取 Agent final text。
    """

    fixture_path = _fixed_revision_gold_fixture()
    assert sum(path.is_file() for path in fixture_path.rglob("*")) == 32

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        IMAGE_CLASSIFICATION_TASK_ID,
        _DirectoryController(fixture_path),
        guest_shared_dir="/home/oai/share",
    )
    evaluation = evaluate_image_classification(observation)

    assert evaluation.passed is True
    assert evaluation.score == 1.0
    assert evaluation.reason_codes == ()


def test_production_capture_keeps_fixed_revision_input_as_failing_state() -> None:
    """验证原始 20 文件 input 不会被误判为已分类 gold。

    输入参数：
        无；使用固定 revision 的 16 张 ``images`` 源图与
        4 个未修改 PPT。
    输出返回值：
        无；production capture 必须给正式 evaluator 保留“四个
        分类目录和十二张已分类图均缺失”的失败事实。
    """

    fixture_path = _fixed_revision_input_fixture()
    assert sum(path.is_file() for path in fixture_path.rglob("*")) == 20

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        IMAGE_CLASSIFICATION_TASK_ID,
        _DirectoryController(fixture_path),
        guest_shared_dir="/home/oai/share",
    )
    evaluation = evaluate_image_classification(observation)

    assert evaluation.passed is False
    assert evaluation.score == 0.0
    assert evaluation.reason_codes == (
        "MISSING_CATEGORY",
        "MISSING_CLASSIFIED_IMAGE",
    )
    assert evaluation.matched_category_count == 0
    assert evaluation.missing_classification_count == 12


def test_production_capture_fails_closed_when_classified_image_is_missing(
    tmp_path: Path,
) -> None:
    """验证已分类图缺失不会被源图副本补成成功。

    输入参数：
        tmp_path：pytest 隔离目录；测试从固定 revision gold
            副本删除一个已分类成员。
    输出返回值：
        无；production capture 与正式 evaluator 必须返回脱敏
        ``MISSING_CLASSIFIED_IMAGE`` 失败，且分母仍为 12。
    """

    fixture_path = _copy_fixed_revision_gold_fixture(tmp_path)
    (fixture_path / "basketball" / "Unknown-1.jpeg").unlink()

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        IMAGE_CLASSIFICATION_TASK_ID,
        _DirectoryController(fixture_path),
        guest_shared_dir="/home/oai/share",
    )
    evaluation = evaluate_image_classification(observation)

    assert evaluation.passed is False
    assert evaluation.score == 0.9167
    assert evaluation.reason_codes == ("MISSING_CLASSIFIED_IMAGE",)
    assert evaluation.missing_classification_count == 1
    assert "Unknown-1.jpeg" not in repr(evaluation)


def test_production_capture_fails_closed_when_image_is_in_wrong_category(
    tmp_path: Path,
) -> None:
    """验证文件改名不影响 SHA 身份，但错类严格失败。

    输入参数：
        tmp_path：pytest 隔离目录；将一张 basketball 图改名
            并移入 soccer 目录，不修改其内容。
    输出返回值：
        无；结果必须同时报告缺失和错类的固定脱敏码，
        不暴露目录、文件名或 SHA-256。
    """

    fixture_path = _copy_fixed_revision_gold_fixture(tmp_path)
    source = fixture_path / "basketball" / "Unknown-1.jpeg"
    destination = fixture_path / "soccer" / "renamed.jpeg"
    source.rename(destination)

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        IMAGE_CLASSIFICATION_TASK_ID,
        _DirectoryController(fixture_path),
        guest_shared_dir="/home/oai/share",
    )
    evaluation = evaluate_image_classification(observation)

    assert evaluation.passed is False
    assert evaluation.reason_codes == (
        "MISSING_CLASSIFIED_IMAGE",
        "MISCLASSIFIED_IMAGE",
    )
    assert evaluation.missing_classification_count == 1
    assert evaluation.misclassified_image_count == 1
    rendered = repr(evaluation)
    assert "basketball" not in rendered
    assert "renamed.jpeg" not in rendered
    assert "920c257b" not in rendered


def test_production_capture_fails_closed_on_duplicate_classified_content(
    tmp_path: Path,
) -> None:
    """验证不同路径下的相同内容不会被当作两张图。

    输入参数：
        tmp_path：pytest 隔离目录；在同一分类目录内复制一张
            图并改名，使路径唯一但已校验 SHA-256 重复。
    输出返回值：
        无；固定 12 项分数虽全部命中，整体仍必须因
        ``DUPLICATE_CLASSIFIED_IMAGE`` 失败。
    """

    fixture_path = _copy_fixed_revision_gold_fixture(tmp_path)
    shutil.copyfile(
        fixture_path / "basketball" / "Unknown-1.jpeg",
        fixture_path / "basketball" / "duplicate.jpeg",
    )

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        IMAGE_CLASSIFICATION_TASK_ID,
        _DirectoryController(fixture_path),
        guest_shared_dir="/home/oai/share",
    )
    evaluation = evaluate_image_classification(observation)

    assert evaluation.passed is False
    assert evaluation.score == 1.0
    assert evaluation.reason_codes == ("DUPLICATE_CLASSIFIED_IMAGE",)
    assert evaluation.duplicate_classification_count == 1
    assert "duplicate.jpeg" not in repr(evaluation)


def test_production_capture_fails_closed_on_extra_regular_file(
    tmp_path: Path,
) -> None:
    """验证闭集内任何未属于受控结构的文件都使评价失败。

    输入参数：
        tmp_path：pytest 隔离目录；在 gold 根目录增加一个
            内容含敏感哨兵值的未知常规文件。
    输出返回值：
        无；评价仅返回 ``UNEXPECTED_FILE`` 和计数，不回显
        路径、文件名、哈希或内容。
    """

    fixture_path = _copy_fixed_revision_gold_fixture(tmp_path)
    (fixture_path / "PRIVATE-extra.txt").write_bytes(b"PRIVATE PIPELINE SENTINEL")

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        IMAGE_CLASSIFICATION_TASK_ID,
        _DirectoryController(fixture_path),
        guest_shared_dir="/home/oai/share",
    )
    evaluation = evaluate_image_classification(observation)

    assert evaluation.passed is False
    assert evaluation.reason_codes == ("UNEXPECTED_FILE",)
    assert evaluation.unexpected_regular_file_count == 1
    rendered = repr(evaluation)
    assert "PRIVATE-extra.txt" not in rendered
    assert "PRIVATE PIPELINE SENTINEL" not in rendered


def test_production_capture_rejects_portable_category_path_collision() -> None:
    """验证大小写折叠的分类路径在 payload 读取前失败。

    输入参数：
        无；manifest 同时包含 ``Basketball`` 和 ``basketball``
        两个在部分 host 上会折叠的逻辑目录。
    输出返回值：
        无；source 必须仅抛固定 ``ARTIFACT_PATH_INVALID``，
        不访问文件 getter，也不泄漏碰撞路径或摘要。
    """

    digest = hashlib.sha256(b"PRIVATE").hexdigest()
    controller = _ManifestOnlyController(
        (
            ("Basketball/a.jpeg", 7, digest),
            ("basketball/b.jpeg", 7, digest),
        )
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        PipelineImplicitArtifactEvidenceSource().capture(
            IMAGE_CLASSIFICATION_TASK_ID,
            controller,
            guest_shared_dir="/home/oai/share",
        )

    assert captured.value.code == "ARTIFACT_PATH_INVALID"
    assert str(captured.value) == "ARTIFACT_PATH_INVALID"
    assert controller.file_calls == 0
    rendered = repr(captured.value)
    assert "Basketball" not in rendered
    assert digest not in rendered
