"""CombinationDocs-002 正式 input 与 audit known-negative 边界测试。"""

from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

import pytest

from paraguibench.integrations.pipeline_implicit import verified_assets
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    COMBINATION002_INPUT_MANIFEST_PATH,
    COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
    PipelineImplicitGoldIntegrityError,
    PipelineImplicitKnownNegativeIntegrityError,
    PipelineImplicitKnownNegativeManifestError,
    build_combination002_asset_manifest_documents,
    check_combination002_asset_manifest_files,
    load_pipeline_implicit_known_negative_manifest,
    resolve_pipeline_implicit_known_negative_bundle,
    serialize_pipeline_implicit_asset_manifest,
)
from paraguibench.runtime.assets import load_asset_manifest, verify_asset_directory


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "Operation-FileOperate-CombinationDocs-002"
TASK_UID = "6bf5b1c9-a2a2-4901-bbe3-631a33da45e8"
PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "pipeline-implicit-known-negative-manifest-v1.schema.json"
)
INPUT_FILES = (
    (
        "McDonald_finacial_report.docx",
        14_351,
        "df1a15647946cba883e00cb1d0228f075b5e12e6b5deb02acb9c4f79a931515b",
        DOCX_MEDIA_TYPE,
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        9_545,
        "abaf2d2622354d6c8a1cd6115cda4b1e5b82ccdcd01565d739e75aa606e750b9",
        XLSX_MEDIA_TYPE,
    ),
    (
        "McDonalds_powerpoint_report.pptx",
        39_699,
        "a96a98ecba8bf648fae8357c35d31197d1594c063130737dd098a9c3ac1c712d",
        PPTX_MEDIA_TYPE,
    ),
)
KNOWN_NEGATIVE_FILES = (
    (
        "McDonald_finacial_report.docx",
        10_974,
        "8ae14dbe3e701e8671bfdd17b24e1b9e098cd42d0f08c2e9ea584908d21dd9fa",
        DOCX_MEDIA_TYPE,
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        9_545,
        "abaf2d2622354d6c8a1cd6115cda4b1e5b82ccdcd01565d739e75aa606e750b9",
        XLSX_MEDIA_TYPE,
    ),
    (
        "McDonalds_powerpoint_report.pptx",
        49_687,
        "963617e29c37f7b653f40d4616dd636d6f756d64dcb24afe4bb68e3a4447c635",
        PPTX_MEDIA_TYPE,
    ),
)
_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_COMBINATION002_FIXTURE_ROOT"


def test_combination002_canonical_uses_xlsx_source_and_audit_only_negative() -> None:
    """验证原题不变，XLSX input 是唯一事实源，HF answer 仅供负例审计。

    输入参数：无；读取 canonical task JSON。
    输出返回值：input 与 known-negative manifest 均显式绑定，但不存在会把
        错误 HF answer 当作 pass oracle 的 ``gold_manifest`` 字段。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{TASK_ID}.json").read_text(encoding="utf-8")
    )

    assert task["instruction"] == (
        "Based on the data in Excel, check the corresponding data in PPT and Word "
        "documents, and correct any errors."
    )
    assert task["asset_manifest"] == COMBINATION002_INPUT_MANIFEST_PATH
    assert (
        task["known_negative_manifest"] == COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH
    )
    assert "gold_manifest" not in task
    assert "prepare_script_path" not in task


def test_combination002_release_entry_hashes_current_canonical_bytes() -> None:
    """验证串行派生后 release 唯一条目绑定 Combo-002 当前字节。

    输入参数：无；读取 canonical 原始字节和 release-v1。
    输出返回值：唯一 selected entry 的 SHA-256 精确等于
        当前 task 字节，确保 audit-only reference 引用已进入正式发布身份。
    """

    task_path = REPO_ROOT / "benchmark/tasks" / f"{TASK_ID}.json"
    release = json.loads(
        (REPO_ROOT / "benchmark/manifests/release-v1.json").read_text(encoding="utf-8")
    )
    entries = [entry for entry in release["tasks"] if entry["task_id"] == TASK_ID]

    assert len(entries) == 1
    assert entries[0]["sha256"] == hashlib.sha256(task_path.read_bytes()).hexdigest()


def _fixed_revision_fixture(role: str) -> Path:
    """返回 CombinationDocs-002 一个角色的固定资产树。

    输入参数：
        role：``input`` 或 ``known_negative``。
    输出返回值：
        显式 download-only fixture 下对应的三文件目录；未配置时跳过。
    """

    raw_root = os.environ.get(_FIXTURE_ENVIRONMENT_VARIABLE)
    if raw_root is None:
        pytest.skip(
            f"{_FIXTURE_ENVIRONMENT_VARIABLE} is required for download-only fixture"
        )
    if role not in {"input", "known_negative"}:
        raise AssertionError("fixture role is not registered")
    role_directory = "benchmark_dataset" if role == "input" else "answer_files"
    candidate = Path(raw_root) / role_directory / TASK_UID
    if not candidate.is_dir():
        pytest.fail("CombinationDocs-002 fixed-revision fixture is unavailable")
    return candidate


def _minimal_docx_payload(*, main_content_type: str) -> bytes:
    """构造只用于 strict media gate 的最小 DOCX 容器。

    输入参数：
        main_content_type：``word/document.xml`` 的 content type。
    输出返回值：
        使用 ZIP_STORED 的确定性字节，便于精确破坏 CRC。
    """

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/word/document.xml" '
        f'ContentType="{main_content_type}"/>'
        "</Types>"
    ).encode()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", b"<document/>")
    return stream.getvalue()


def test_combination002_builder_closes_input_and_audit_only_negative_files() -> None:
    """验证 builder 固定真实 revision 的 3 input + 3 known-negative 身份。

    输入参数：无；只读取 canonical task 的固定 ID/UID。
    输出返回值：无；input 采用通用 AssetManifest，HF answer 采用专属
        audit-only strict manifest，且 DOCX/XLSX/PPTX 各恰好一份。
    """

    documents = build_combination002_asset_manifest_documents(REPO_ROOT)

    assert set(documents) == {
        COMBINATION002_INPUT_MANIFEST_PATH,
        COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
    }
    input_document = documents[COMBINATION002_INPUT_MANIFEST_PATH]
    assert input_document["asset_set_id"] == TASK_ID
    assert input_document["source"]["revision"] == PINNED_REVISION
    assert input_document["source"]["base_path"] == (f"benchmark_dataset/{TASK_UID}")
    assert input_document["files"] == [
        {
            "path": path,
            "size": size_bytes,
            "sha256": sha256,
            "media_type": media_type,
        }
        for path, size_bytes, sha256, media_type in INPUT_FILES
    ]
    negative_document = documents[COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH]
    assert negative_document["task_id"] == TASK_ID
    assert negative_document["task_uid"] == TASK_UID
    assert negative_document["manifest_role"] == "audit_known_negative"
    assert negative_document["use_as_pass_oracle"] is False
    assert negative_document["source"]["revision"] == PINNED_REVISION
    assert negative_document["source"]["base_path"] == f"answer_files/{TASK_UID}"
    assert negative_document["expected_evaluation"] == {
        "protocol_id": "paraguibench.operation.cross-document-facts.v1",
        "passed": False,
        "score": 0.6667,
        "required_fact_count": 3,
        "matched_fact_count": 2,
        "reason_codes": ["DOCX_PROFIT_ORDER_INCORRECT"],
    }
    assert negative_document["entries"] == [
        {
            "path": path,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "media_type": media_type,
        }
        for path, size_bytes, sha256, media_type in KNOWN_NEGATIVE_FILES
    ]


def test_known_negative_schema_is_audit_only_exact_three_media_identity() -> None:
    """验证专属 schema 固定 audit-only 身份、失败结论与三文件闭集。

    输入参数：无；读取 pipeline-implicit known-negative v1 schema。
    输出返回值：无；task/UID/base path 与 3-entry 数量均精确绑定。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["task_id"]["const"] == TASK_ID
    assert schema["properties"]["task_uid"]["const"] == TASK_UID
    assert schema["properties"]["manifest_role"]["const"] == "audit_known_negative"
    assert schema["properties"]["use_as_pass_oracle"]["const"] is False
    assert schema["properties"]["source"]["properties"]["base_path"]["const"] == (
        f"answer_files/{TASK_UID}"
    )
    assert schema["properties"]["entries"]["minItems"] == 3
    assert schema["properties"]["entries"]["maxItems"] == 3
    assert set(
        schema["properties"]["entries"]["items"]["properties"]["media_type"]["enum"]
    ) == {
        DOCX_MEDIA_TYPE,
        XLSX_MEDIA_TYPE,
        PPTX_MEDIA_TYPE,
    }
    assert schema["properties"]["expected_evaluation"]["properties"]["score"] == {
        "const": 0.6667
    }


def test_combination002_formal_manifests_are_deterministic_builder_output() -> None:
    """验证两份正式清单与 builder 的唯一字节序列完全一致。

    输入参数：无；读取 task-specific input/known-negative manifest。
    输出返回值：无；两份文件不可继续依赖 metadata-unverified 草案。
    """

    documents = build_combination002_asset_manifest_documents(REPO_ROOT)

    assert check_combination002_asset_manifest_files(REPO_ROOT) is True
    for relative_path, document in documents.items():
        assert (REPO_ROOT / relative_path).read_bytes() == (
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_real_input_and_known_negative_pass_separate_verification_chains() -> None:
    """验证固定 revision 的 3+3 真实 OOXML 字节通过职责分离链。

    输入参数：无；读取显式 fixture 中的 DOCX/XLSX/PPTX。
    输出返回值：无；input 走通用 verifier，HF answer 走 audit-only
        strict bytes loader 与 held-dirfd resolver，且两边都恰好三项。
    """

    input_manifest = load_asset_manifest(REPO_ROOT / COMBINATION002_INPUT_MANIFEST_PATH)
    input_verification = verify_asset_directory(
        input_manifest,
        _fixed_revision_fixture("input"),
    )
    negative_payload = (
        REPO_ROOT / COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH
    ).read_bytes()
    negative_manifest = load_pipeline_implicit_known_negative_manifest(negative_payload)
    negative_bundle = resolve_pipeline_implicit_known_negative_bundle(
        negative_payload,
        _fixed_revision_fixture("known_negative"),
    )

    assert input_manifest.asset_set_id == TASK_ID
    assert len(input_manifest.files) == 3
    assert input_verification.ok is True
    assert negative_manifest.task_id == TASK_ID
    assert negative_manifest.expected_score == 0.6667
    assert len(negative_manifest.entries) == 3
    assert negative_bundle.task_id == TASK_ID
    assert negative_bundle.file_count == 3
    assert not hasattr(negative_bundle, "iter_files_for_pipeline")


@pytest.mark.parametrize("mutation", ("missing", "extra", "casefold_collision"))
def test_strict_known_negative_loader_rejects_non_exact_three_path_sets(
    mutation: str,
) -> None:
    """验证 known-negative 三路径闭集不可删减、扩展或便携碰撞。

    输入参数：
        mutation：当前删除、增加或大小写碰撞变体。
    输出返回值：无；strict bytes loader 只返回固定脱敏错误。
    """

    document = copy.deepcopy(
        build_combination002_asset_manifest_documents(REPO_ROOT)[
            COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH
        ]
    )
    if mutation == "missing":
        document["entries"].pop()
    else:
        extra = copy.deepcopy(document["entries"][0])
        extra["path"] = (
            "PRIVATE-extra.docx"
            if mutation == "extra"
            else "mcdonald_finacial_report.docx"
        )
        document["entries"].append(extra)

    with pytest.raises(PipelineImplicitKnownNegativeManifestError) as captured:
        load_pipeline_implicit_known_negative_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )

    assert str(captured.value) == ("PIPELINE_IMPLICIT_KNOWN_NEGATIVE_MANIFEST_INVALID")
    assert "PRIVATE" not in repr(captured.value)


def test_docx_media_gate_requires_word_main_type_and_valid_crc() -> None:
    """验证 DOCX 媒体门同时检查主 content type 与 ZIP CRC。

    输入参数：无；构造合法、宏类型与 payload CRC 损坏的最小 DOCX。
    输出返回值：无；仅标准 WordprocessingML 主文档通过。
    """

    valid = _minimal_docx_payload(
        main_content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml"
        )
    )
    verified_assets._verify_gold_media_type(valid, DOCX_MEDIA_TYPE)
    wrong_type = _minimal_docx_payload(
        main_content_type=("application/vnd.ms-word.document.macroEnabled.main+xml")
    )
    corrupted = bytearray(valid)
    marker = b"<document/>"
    corrupted[corrupted.index(marker)] ^= 1

    for payload in (wrong_type, bytes(corrupted)):
        with pytest.raises(PipelineImplicitGoldIntegrityError):
            verified_assets._verify_gold_media_type(payload, DOCX_MEDIA_TYPE)


def test_docx_media_gate_rejects_content_types_root_namespace_spoof() -> None:
    """验证 strict gold media gate 不会按 local-name 接受伪造 Types 根。

    输入参数：无；从合法最小 DOCX 出发，仅修改
        ``[Content_Types].xml`` 根 namespace，正确 Override 和主类型不变。
    输出返回值：无；必须拒绝该媒体字节。
    """

    valid = _minimal_docx_payload(
        main_content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml"
        )
    )
    with zipfile.ZipFile(BytesIO(valid)) as archive:
        content_types = ET.fromstring(archive.read("[Content_Types].xml"))
    content_types.tag = "{urn:paraguibench:spoof}Types"
    destination = BytesIO()
    with zipfile.ZipFile(BytesIO(valid)) as source:
        with zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_STORED
        ) as target:
            for info in source.infolist():
                target.writestr(
                    info,
                    ET.tostring(
                        content_types,
                        encoding="utf-8",
                        xml_declaration=True,
                    )
                    if info.filename == "[Content_Types].xml"
                    else source.read(info),
                )

    with pytest.raises(PipelineImplicitGoldIntegrityError):
        verified_assets._verify_gold_media_type(destination.getvalue(), DOCX_MEDIA_TYPE)


@pytest.mark.parametrize("mutation", ("missing", "extra", "casefold_collision"))
def test_known_negative_resolver_rejects_non_exact_cache_path_sets(
    tmp_path: Path,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 held-dirfd resolver 拒绝缺失、多余和便携路径碰撞。

    输入参数：
        tmp_path：隔离的真实 gold 副本。
        mutation：当前路径闭集破坏方式。
        monkeypatch：在大小写不敏感文件系统上注入碰撞目录项。
    输出返回值：无；任何漂移都在暴露 verified payload 前失败。
    """

    cache_root = tmp_path / "known-negative"
    shutil.copytree(_fixed_revision_fixture("known_negative"), cache_root)
    if mutation == "missing":
        (cache_root / KNOWN_NEGATIVE_FILES[-1][0]).unlink()
    elif mutation == "extra":
        (cache_root / "PRIVATE-extra.docx").write_bytes(b"PRIVATE SENTINEL")
    else:
        original_listdir = os.listdir

        def listdir_with_collision(path: int) -> list[str]:
            """向真实 gold 根目录观测注入大小写折叠碰撞。

            输入参数：
                path：production 枚举器持有的目录 descriptor。
            输出返回值：
                原目录项；目标根额外包含首个 DOCX 的小写拼写。
            """

            names = original_listdir(path)
            if KNOWN_NEGATIVE_FILES[0][0] in names:
                return [*names, KNOWN_NEGATIVE_FILES[0][0].lower()]
            return names

        monkeypatch.setattr(os, "listdir", listdir_with_collision)

    with pytest.raises(PipelineImplicitKnownNegativeIntegrityError) as captured:
        resolve_pipeline_implicit_known_negative_bundle(
            (REPO_ROOT / COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert str(captured.value) == ("PIPELINE_IMPLICIT_KNOWN_NEGATIVE_INTEGRITY_INVALID")
    assert "PRIVATE" not in repr(captured.value)


def test_known_negative_resolver_rejects_symlinked_cache_ancestor(
    tmp_path: Path,
) -> None:
    """验证从文件系统锚点到 audit 根的任一级都不得为 symlink。

    输入参数：
        tmp_path：真实 known-negative 置于
            ``real/known-negative``，调用经 ``alias`` 进入。
    输出返回值：无；即使三个最终文件正确也必须失败关闭。
    """

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    shutil.copytree(
        _fixed_revision_fixture("known_negative"),
        real_parent / "known-negative",
    )
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PipelineImplicitKnownNegativeIntegrityError):
        resolve_pipeline_implicit_known_negative_bundle(
            (REPO_ROOT / COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH).read_bytes(),
            alias_parent / "known-negative",
        )


def test_known_negative_resolver_rechecks_closure_after_payload_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证读取期间新增成员会被第二轮 held-dirfd 枚举捕获。

    输入参数：
        tmp_path：隔离的真实 gold 副本。
        monkeypatch：在首轮闭集返回前注入晚到文件。
    输出返回值：无；resolver 不返回混合时点的部分 bundle。
    """

    cache_root = tmp_path / "known-negative"
    shutil.copytree(_fixed_revision_fixture("known_negative"), cache_root)
    original_enumerator = verified_assets._enumerate_regular_gold_paths
    call_count = 0

    def enumerate_then_mutate(root_descriptor: int) -> set[str]:
        """执行真实枚举并在首轮后制造确定性竞态。

        输入参数：
            root_descriptor：production 持有的 gold 根 descriptor。
        输出返回值：本轮新增前的真实路径集合。
        """

        nonlocal call_count
        observed = original_enumerator(root_descriptor)
        call_count += 1
        if call_count == 1:
            (cache_root / "PRIVATE-late.bin").write_bytes(b"late")
        return observed

    monkeypatch.setattr(
        verified_assets,
        "_enumerate_regular_gold_paths",
        enumerate_then_mutate,
    )

    with pytest.raises(PipelineImplicitKnownNegativeIntegrityError):
        resolve_pipeline_implicit_known_negative_bundle(
            (REPO_ROOT / COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert call_count == 2
