"""SearchWrite-008 正式 pipeline-implicit input/gold 资产边界测试。"""

from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import shutil
import zipfile

import pytest

from paraguibench.integrations.pipeline_implicit import verified_assets
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    SEARCHWRITE008_GOLD_MANIFEST_PATH,
    SEARCHWRITE008_INPUT_MANIFEST_PATH,
    PipelineImplicitGoldIntegrityError,
    PipelineImplicitGoldManifestError,
    build_searchwrite008_asset_manifest_documents,
    check_searchwrite008_asset_manifest_files,
    load_verified_pipeline_implicit_gold_manifest,
    resolve_verified_pipeline_implicit_gold_bundle,
    serialize_pipeline_implicit_asset_manifest,
)
from paraguibench.runtime.assets import (
    load_asset_manifest,
    verify_asset_directory,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "Operation-FileOperate-SearchAndWrite-008"
TASK_UID = "65a4848d-b4b2-4173-8308-a0213fdafbd0"
PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
INPUT_FILES = (
    (
        "UK_Universities_Group1.xlsx",
        8_908,
        "df08dc5e24d04a9587c21154b363511e01bc2ec18e9411d179e29e9231188e27",
    ),
    (
        "UK_Universities_Group2.xlsx",
        8_900,
        "7936c66869e26be9e787e703e801c74b7034afd22f934ca3b166a3d4b021caaa",
    ),
)
GOLD_FILES = (
    (
        "UK_Universities_Group1.xlsx",
        5_877,
        "0170c5dab6a6062c610517b297708ad496a8bfa53699915ad6c3ff3948bf81cd",
    ),
    (
        "UK_Universities_Group2.xlsx",
        5_895,
        "b19a72eb28ad9a55ed956247dd8fb97f59ec5ede751ece25ac963614631ef257",
    ),
)
_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_SEARCHWRITE008_FIXTURE_ROOT"
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "pipeline-implicit-gold-asset-manifest-v1.schema.json"
)


def _minimal_xlsx_payload(*, main_content_type: str) -> bytes:
    """构造只用于 OOXML 容器门禁的最小 XLSX ZIP。

    输入参数：
        main_content_type：``xl/workbook.xml`` 在 content-types 中声明的
            MIME，用于分别构造合法与错误 main type。
    输出返回值：
        使用 ``ZIP_STORED`` 的确定性字节，便于测试精确破坏 workbook CRC。
    """

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/xl/workbook.xml" '
        f'ContentType="{main_content_type}"/>'
        "</Types>"
    ).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", b"<workbook/>")
    return stream.getvalue()


def _fixed_revision_fixture(role: str) -> Path:
    """返回 SearchWrite-008 一个角色的 download-only 固定树。

    输入参数：
        role：``input`` 或 ``gold``，分别对应 Lee revision 的
            ``benchmark_dataset`` 和 ``answer_files``。
    输出返回值：
        包含两个已核验 XLSX 的存在目录；未显式配置时
        跳过需要 download-only 真实字节的测试。
    """

    raw_root = os.environ.get(_FIXTURE_ENVIRONMENT_VARIABLE)
    if raw_root is None:
        pytest.skip(
            f"{_FIXTURE_ENVIRONMENT_VARIABLE} is required for download-only fixture"
        )
    role_directory = "benchmark_dataset" if role == "input" else "answer_files"
    candidate = Path(raw_root) / role_directory / TASK_UID
    if not candidate.is_dir():
        pytest.fail("SearchWrite-008 fixed-revision fixture is unavailable")
    return candidate


def test_searchwrite_builder_separates_pinned_input_and_gold_identities() -> None:
    """验证 builder 以两份 input 和两份 gold 形成不同权威闭集。

    输入参数：
        无；builder 只读取仓库 canonical task 的固定身份。
    输出返回值：
        无；input 使用通用 AssetManifest 形状并指向
        ``benchmark_dataset``，gold 使用严格专属形状并指向
        ``answer_files``；两者的 size/SHA 不得混用。
    """

    documents = build_searchwrite008_asset_manifest_documents(REPO_ROOT)

    assert set(documents) == {
        SEARCHWRITE008_INPUT_MANIFEST_PATH,
        SEARCHWRITE008_GOLD_MANIFEST_PATH,
    }
    input_document = documents[SEARCHWRITE008_INPUT_MANIFEST_PATH]
    assert input_document == {
        "schema_version": 1,
        "asset_set_id": TASK_ID,
        "source": {
            "provider": "huggingface_dataset",
            "repository": "leeLegendary/Parallel_benchmark",
            "revision": PINNED_REVISION,
            "base_path": f"benchmark_dataset/{TASK_UID}",
            "license_status": "unverified",
        },
        "distribution_policy": "download_only",
        "files": [
            {
                "path": path,
                "size": size_bytes,
                "sha256": sha256,
                "media_type": XLSX_MEDIA_TYPE,
            }
            for path, size_bytes, sha256 in INPUT_FILES
        ],
    }
    gold_document = documents[SEARCHWRITE008_GOLD_MANIFEST_PATH]
    assert gold_document["task_id"] == TASK_ID
    assert gold_document["task_uid"] == TASK_UID
    assert gold_document["manifest_role"] == "gold"
    assert gold_document["source"]["revision"] == PINNED_REVISION
    assert gold_document["source"]["base_path"] == f"answer_files/{TASK_UID}"
    assert gold_document["license"] == {
        "status": "unverified",
        "spdx_expression": None,
        "evidence_ref": (
            "https://huggingface.co/datasets/leeLegendary/Parallel_benchmark"
        ),
        "distribution": "download_only",
    }
    assert gold_document["entries"] == [
        {
            "path": path,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "media_type": XLSX_MEDIA_TYPE,
        }
        for path, size_bytes, sha256 in GOLD_FILES
    ]


def test_gold_schema_excludes_combo_audit_only_known_negative() -> None:
    """验证共享 gold schema 仅允许三项正式 pass-reference 任务。

    输入参数：
        无；读取 pipeline-implicit gold v1 schema。
    输出返回值：
        无；公共字段保持闭集，三条 oneOf 分支分别绑定身份；
        CombinationDocs-002 的错误 HF answer 不可进入正式 gold 类型。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    properties = schema["properties"]
    assert set(properties["task_id"]["enum"]) == {
        "Operation-FileOperate-BatchOperationExcel-008",
        "Operation-FileOperate-BatchOperationPPT-003",
        TASK_ID,
    }
    assert (
        XLSX_MEDIA_TYPE in schema["$defs"]["entry"]["properties"]["media_type"]["enum"]
    )
    branches = {
        branch["properties"]["task_id"]["const"]: branch for branch in schema["oneOf"]
    }
    assert set(branches) == {
        "Operation-FileOperate-BatchOperationExcel-008",
        "Operation-FileOperate-BatchOperationPPT-003",
        TASK_ID,
    }
    search_branch = branches[TASK_ID]["properties"]
    assert search_branch["task_uid"]["const"] == TASK_UID
    assert search_branch["manifest_id"]["const"] == f"{TASK_ID}-gold-v1"
    assert search_branch["source"]["properties"]["base_path"]["const"] == (
        f"answer_files/{TASK_UID}"
    )
    assert search_branch["entries"]["minItems"] == 2
    assert search_branch["entries"]["maxItems"] == 2
    assert search_branch["entries"]["items"]["properties"]["media_type"] == {
        "const": XLSX_MEDIA_TYPE
    }


def test_searchwrite_formal_manifests_are_deterministic_builder_output() -> None:
    """验证 SearchWrite 两份正式清单与 builder 输出逐字节一致。

    输入参数：
        无；读取仓库内 SearchWrite task-specific input/gold manifest。
    输出返回值：
        无；input 与 gold 必须分别保持模板和九格答案字节身份，且均由
        同一确定性 builder 产生，不能依赖临时下载器或 Agent final text。
    """

    documents = build_searchwrite008_asset_manifest_documents(REPO_ROOT)

    assert check_searchwrite008_asset_manifest_files(REPO_ROOT) is True
    for relative_path, document in documents.items():
        assert (REPO_ROOT / relative_path).read_bytes() == (
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_searchwrite_input_reuses_generic_asset_verification_chain() -> None:
    """验证两份 input 模板复用通用 AssetManifest 安全链。

    输入参数：
        无；通用 loader 读取 task-specific input manifest，并核验 Lee
        固定 revision ``benchmark_dataset`` 下的两份真实 XLSX。
    输出返回值：
        无；闭集、size、SHA、MIME 全绿，且 input 字节身份与 gold 九格
        答案仍相互独立，不新增 SearchWrite 专属下载器。
    """

    manifest = load_asset_manifest(REPO_ROOT / SEARCHWRITE008_INPUT_MANIFEST_PATH)
    verification = verify_asset_directory(
        manifest,
        _fixed_revision_fixture("input"),
    )

    assert manifest.asset_set_id == TASK_ID
    assert manifest.source.revision == PINNED_REVISION
    assert len(manifest.files) == 2
    assert verification.ok is True


def test_strict_gold_loader_accepts_searchwrite_generated_identity() -> None:
    """验证共享 strict loader 仅按 task ID 选择 SearchWrite 固定闭集。

    输入参数：
        无；传入 SearchWrite builder 的确定性原始 JSON bytes。
    输出返回值：
        无；loader 必须返回两文件不可变合同，不得回落
        到 PPT-003 闭集或把两份 gold 当成 input 模板。
    """

    document = build_searchwrite008_asset_manifest_documents(REPO_ROOT)[
        SEARCHWRITE008_GOLD_MANIFEST_PATH
    ]

    manifest = load_verified_pipeline_implicit_gold_manifest(
        serialize_pipeline_implicit_asset_manifest(document)
    )

    assert manifest.task_id == TASK_ID
    assert manifest.task_uid == TASK_UID
    assert manifest.source_revision == PINNED_REVISION
    assert manifest.distribution_policy == "download_only"
    assert len(manifest.entries) == 2
    assert {entry.media_type for entry in manifest.entries} == {XLSX_MEDIA_TYPE}


@pytest.mark.parametrize(
    "mutation",
    ("missing", "extra", "casefold_collision", "unicode_collision"),
)
def test_strict_gold_loader_rejects_non_exact_searchwrite_path_sets(
    mutation: str,
) -> None:
    """验证 SearchWrite gold manifest 的两文件路径闭集不可漂移。

    输入参数：
        mutation：删除、增加、大小写折叠碰撞或 Unicode NFC 碰撞变体。
    输出返回值：
        无；任何非精确 path→size/SHA/MIME 映射均以固定错误失败关闭。
    """

    document = copy.deepcopy(
        build_searchwrite008_asset_manifest_documents(REPO_ROOT)[
            SEARCHWRITE008_GOLD_MANIFEST_PATH
        ]
    )
    if mutation == "missing":
        document["entries"].pop()
    else:
        extra = copy.deepcopy(document["entries"][0])
        collision_paths = {
            "extra": "extra.xlsx",
            "casefold_collision": "uk_universities_group1.xlsx",
            "unicode_collision": "U\u0301K_Universities_Group1.xlsx",
        }
        extra["path"] = collision_paths[mutation]
        if mutation == "unicode_collision":
            document["entries"][0]["path"] = "\u00daK_Universities_Group1.xlsx"
        document["entries"].append(extra)

    with pytest.raises(PipelineImplicitGoldManifestError) as captured:
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )

    assert str(captured.value) == "PIPELINE_IMPLICIT_GOLD_MANIFEST_INVALID"


@pytest.mark.parametrize("location", ("manifest", "source", "license", "entry"))
def test_strict_gold_loader_rejects_unknown_fields_at_every_level(
    location: str,
) -> None:
    """验证 SearchWrite manifest 所有 object 都是未知字段闭集。

    输入参数：
        location：注入未知字段的顶层、source、license 或 entry。
    输出返回值：
        无；strict loader 拒绝整个 manifest，异常不回显敏感哨兵。
    """

    document = copy.deepcopy(
        build_searchwrite008_asset_manifest_documents(REPO_ROOT)[
            SEARCHWRITE008_GOLD_MANIFEST_PATH
        ]
    )
    targets = {
        "manifest": document,
        "source": document["source"],
        "license": document["license"],
        "entry": document["entries"][0],
    }
    targets[location]["unknown_field"] = "PRIVATE SENTINEL"

    with pytest.raises(PipelineImplicitGoldManifestError) as captured:
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )

    assert "PRIVATE" not in str(captured.value)


def test_strict_gold_loader_rejects_duplicate_json_fields_and_nan() -> None:
    """验证原始 JSON 重复键与非标准 NaN 都不能穿过可信边界。

    输入参数：
        无；分别在合法 payload 注入重复顶层键和 Python encoder 可生成的
        ``NaN`` token。
    输出返回值：
        无；两种有歧义或非标准 JSON 均抛固定 manifest 错误。
    """

    document = build_searchwrite008_asset_manifest_documents(REPO_ROOT)[
        SEARCHWRITE008_GOLD_MANIFEST_PATH
    ]
    duplicate_payload = serialize_pipeline_implicit_asset_manifest(document).replace(
        b'  "schema_version": 1,',
        b'  "schema_version": 1,\n  "schema_version": 1,',
        1,
    )
    nan_document = copy.deepcopy(document)
    nan_document["entries"][0]["size_bytes"] = float("nan")

    for payload in (
        duplicate_payload,
        serialize_pipeline_implicit_asset_manifest(nan_document),
    ):
        with pytest.raises(PipelineImplicitGoldManifestError):
            load_verified_pipeline_implicit_gold_manifest(payload)


def test_strict_gold_loader_rejects_unregistered_task_identity() -> None:
    """验证共享 loader 只允许已正式登记的四个任务。

    输入参数：
        无；把 SearchWrite task ID、UID 和 manifest ID 改成未登记任务。
    输出返回值：
        无；loader 在解析文件身份之前以固定错误拒绝未知任务。
    """

    document = copy.deepcopy(
        build_searchwrite008_asset_manifest_documents(REPO_ROOT)[
            SEARCHWRITE008_GOLD_MANIFEST_PATH
        ]
    )
    document["task_id"] = "Operation-FileOperate-SearchAndWrite-999"
    document["manifest_id"] = f"{document['task_id']}-gold-v1"

    with pytest.raises(PipelineImplicitGoldManifestError):
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_gold_resolver_accepts_only_two_verified_xlsx_payloads() -> None:
    """验证 resolver 只在两份 gold XLSX 的所有身份门通过后返回。

    输入参数：
        无；使用 builder 的原始 manifest bytes 和固定 revision
        ``answer_files`` 两文件目录。
    输出返回值：
        无；返回 bundle 精确包含两个通过 size/SHA/MIME、
        ZIP CRC 和 spreadsheet main type 验证的 evaluator-only payload。
    """

    document = build_searchwrite008_asset_manifest_documents(REPO_ROOT)[
        SEARCHWRITE008_GOLD_MANIFEST_PATH
    ]
    bundle = resolve_verified_pipeline_implicit_gold_bundle(
        serialize_pipeline_implicit_asset_manifest(document),
        _fixed_revision_fixture("gold"),
    )

    assert bundle.task_id == TASK_ID
    assert bundle.file_count == 2
    assert bundle.total_bytes == 5_877 + 5_895
    files = bundle.iter_files_for_pipeline()
    assert len(files) == 2
    assert all(len(item.read_for_pipeline()) == item.size_bytes for item in files)
    assert "UK_Universities" not in repr(bundle)


def test_xlsx_media_gate_requires_spreadsheet_main_type_and_valid_crc() -> None:
    """验证 XLSX 媒体门同时执行 OOXML main type 与 ZIP CRC 校验。

    输入参数：
        无；构造一个合法最小 XLSX、一个错误 main type 变体及一个
        workbook payload 被翻转但 central-directory CRC 未更新的变体。
    输出返回值：
        无；合法容器通过，错误类型和 CRC 损坏均以固定完整性错误拒绝。
    """

    valid_payload = _minimal_xlsx_payload(
        main_content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
        )
    )
    verified_assets._verify_gold_media_type(valid_payload, XLSX_MEDIA_TYPE)

    wrong_type_payload = _minimal_xlsx_payload(
        main_content_type=("application/vnd.ms-excel.sheet.macroEnabled.main+xml")
    )
    corrupted_payload = bytearray(valid_payload)
    workbook_marker = b"<workbook/>"
    marker_offset = corrupted_payload.index(workbook_marker)
    corrupted_payload[marker_offset] ^= 1

    for payload in (wrong_type_payload, bytes(corrupted_payload)):
        with pytest.raises(PipelineImplicitGoldIntegrityError) as captured:
            verified_assets._verify_gold_media_type(payload, XLSX_MEDIA_TYPE)
        assert str(captured.value) == "PIPELINE_IMPLICIT_GOLD_INTEGRITY_INVALID"


@pytest.mark.parametrize("mutation", ("missing", "extra", "casefold_collision"))
def test_gold_resolver_rejects_non_exact_cache_path_sets(
    tmp_path: Path,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证真实 gold cache 的缺失、多余和便携路径碰撞均失败关闭。

    输入参数：
        tmp_path：pytest 隔离目录。
        mutation：删除固定成员、增加普通成员或增加大小写碰撞成员。
        monkeypatch：在大小写不敏感的 macOS 文件系统上模拟目录枚举同时
            暴露两种大小写拼写，不绕过 production 枚举逻辑。
    输出返回值：
        无；resolver 在暴露任何 verified payload 前抛脱敏固定错误。
    """

    cache_root = tmp_path / "gold"
    shutil.copytree(_fixed_revision_fixture("gold"), cache_root)
    if mutation == "missing":
        (cache_root / GOLD_FILES[-1][0]).unlink()
    elif mutation == "extra":
        (cache_root / "PRIVATE-extra.xlsx").write_bytes(b"PRIVATE SENTINEL")
    else:
        original_listdir = os.listdir

        def listdir_with_collision(path: int) -> list[str]:
            """返回真实目录项，并为 gold 根注入一个大小写折叠碰撞名。

            输入参数：
                path：production 枚举器传入的目录描述符或路径。
            输出返回值：
                原始目录项；目标 gold 根首次枚举额外出现小写拼写。
            """

            names = original_listdir(path)
            if GOLD_FILES[0][0] in names:
                return [*names, GOLD_FILES[0][0].lower()]
            return names

        monkeypatch.setattr(os, "listdir", listdir_with_collision)

    with pytest.raises(PipelineImplicitGoldIntegrityError) as captured:
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / SEARCHWRITE008_GOLD_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert str(captured.value) == "PIPELINE_IMPLICIT_GOLD_INTEGRITY_INVALID"
    assert "PRIVATE" not in repr(captured.value)


def test_gold_resolver_rejects_symlinked_cache_ancestor(tmp_path: Path) -> None:
    """验证从文件系统锚点到 cache root 的每一级均为 nofollow。

    输入参数：
        tmp_path：pytest 隔离目录；真实目录置于 ``real/gold``，调用路径
        经目录 symlink ``alias`` 进入。
    输出返回值：
        无；即使最终两个文件本身正常，祖先 symlink 仍使解析失败。
    """

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    shutil.copytree(_fixed_revision_fixture("gold"), real_parent / "gold")
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PipelineImplicitGoldIntegrityError):
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / SEARCHWRITE008_GOLD_MANIFEST_PATH).read_bytes(),
            alias_parent / "gold",
        )


def test_gold_resolver_rechecks_path_closure_after_payload_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证首轮闭集通过后发生的成员新增会被末轮闭集捕获。

    输入参数：
        tmp_path：pytest 隔离目录。
        monkeypatch：在 production 枚举器首轮返回后注入确定性竞态。
    输出返回值：
        无；第二轮枚举观察到新增成员并拒绝整个 bundle，不返回部分结果。
    """

    cache_root = tmp_path / "gold"
    shutil.copytree(_fixed_revision_fixture("gold"), cache_root)
    original_enumerator = verified_assets._enumerate_regular_gold_paths
    call_count = 0

    def enumerate_then_mutate(root_descriptor: int) -> set[str]:
        """调用真实枚举器，并在首轮观察后增加一个晚到成员。

        输入参数：
            root_descriptor：production resolver 持有的 gold 根描述符。
        输出返回值：
            本轮真实路径集合；首次调用返回前在同一目录增加哨兵文件。
        """

        nonlocal call_count
        observed = original_enumerator(root_descriptor)
        call_count += 1
        if call_count == 1:
            (cache_root / "late-extra.bin").write_bytes(b"late")
        return observed

    monkeypatch.setattr(
        verified_assets,
        "_enumerate_regular_gold_paths",
        enumerate_then_mutate,
    )

    with pytest.raises(PipelineImplicitGoldIntegrityError):
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / SEARCHWRITE008_GOLD_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert call_count == 2
