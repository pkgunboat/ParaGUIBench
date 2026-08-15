"""Excel-008 正式 pipeline-implicit input/gold 资产边界测试。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from paraguibench.integrations.pipeline_implicit import verified_assets
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    EXCEL008_GOLD_MANIFEST_PATH,
    EXCEL008_INPUT_MANIFEST_PATH,
    PipelineImplicitGoldIntegrityError,
    PipelineImplicitGoldManifestError,
    build_excel008_asset_manifest_documents,
    check_excel008_asset_manifest_files,
    load_verified_pipeline_implicit_gold_manifest,
    resolve_verified_pipeline_implicit_gold_bundle,
    serialize_pipeline_implicit_asset_manifest,
)
from paraguibench.runtime.assets import (
    load_asset_manifest,
    verify_asset_directory,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "Operation-FileOperate-BatchOperationExcel-008"
TASK_UID = "1c73128f-a5ef-4a97-97ce-ef427d6d46b4"
PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
INPUT_FILES = (
    (
        "KFC_Monthly_Data.xlsx",
        9_532,
        "3e21f4657d6fe68210e5f68ba5bad2db979dd47f5902b8be09114903fed00ead",
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        9_535,
        "4c901ba683cff4c629eba5ca070b5d76684a827f2012145e3b8a09d477230761",
    ),
    (
        "Mixue_Monthly_Data.xlsx",
        5_866,
        "e7f7bd52d195f878fc94c3845c10acef0f1c0e570afdd9de0a342212cf2e19d2",
    ),
    (
        "PizzaHut_Monthly_Data.xlsx",
        9_535,
        "1fda0cabc98adc934b4314da8afb36bb38cb7681a49f3753a43384dda0f211c8",
    ),
    (
        "Subway_Monthly_Data.xlsx",
        9_527,
        "20ee0a872bd508276c9971122c12eaa510c8a1825bc44a94433261894892ba96",
    ),
)
GOLD_FILES = (
    (
        "KFC_Monthly_Data.xlsx",
        7_313,
        "35d4144ed899fdbb14ccb07a99d18d042190027718a1467f852355e09491e60e",
    ),
    (
        "McDonalds_Monthly_Data.xlsx",
        7_326,
        "db90397c5afcdcbfc280c18afd694d9802c05327b93f079cb34742d4ca398f04",
    ),
    (
        "Mixue_Monthly_Data.xlsx",
        5_866,
        "e7f7bd52d195f878fc94c3845c10acef0f1c0e570afdd9de0a342212cf2e19d2",
    ),
    (
        "PizzaHut_Monthly_Data.xlsx",
        7_326,
        "f6c67a77d417484174eede29b173774dc158d0f25229abc6f8db6fac8d00572b",
    ),
    (
        "Subway_Monthly_Data.xlsx",
        7_354,
        "322c97248f024c5cbac031736caae5b450e1c51c6ddb44e4f0f59398428dfa13",
    ),
)
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "pipeline-implicit-gold-asset-manifest-v1.schema.json"
)
_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_EXCEL008_FIXTURE_ROOT"


def test_excel008_canonical_binds_formal_input_and_gold_without_changing_instruction() -> (
    None
):
    """验证 Excel-008 原题保持不变，仅授权正式 input/gold resolver。

    输入参数：无；读取 canonical task JSON。
    输出返回值：题面逐字保持原始文本，正式五文件 input/gold 清单可由
        production resolver 发现，旧远端 prepare 路径不再作为执行入口。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{TASK_ID}.json").read_text(encoding="utf-8")
    )

    assert task["instruction"] == (
        "Some data in these files is currently missing and temporarily filled with "
        "'N/A'. Please temporarily hide them in the table."
    )
    assert task["asset_manifest"] == EXCEL008_INPUT_MANIFEST_PATH
    assert task["gold_manifest"] == EXCEL008_GOLD_MANIFEST_PATH
    assert "prepare_script_path" not in task


def test_excel008_release_entry_hashes_current_canonical_bytes() -> None:
    """验证串行派生后 release 唯一条目绑定 Excel-008 当前字节。

    输入参数：无；读取 canonical 原始字节和 release-v1。
    输出返回值：唯一 selected entry 的 SHA-256 精确等于
        当前 task 字节，防止隔离 candidate helper 掩盖正式 checkout 漂移。
    """

    task_path = REPO_ROOT / "benchmark/tasks" / f"{TASK_ID}.json"
    release = json.loads(
        (REPO_ROOT / "benchmark/manifests/release-v1.json").read_text(encoding="utf-8")
    )
    entries = [entry for entry in release["tasks"] if entry["task_id"] == TASK_ID]

    assert len(entries) == 1
    assert entries[0]["sha256"] == hashlib.sha256(task_path.read_bytes()).hexdigest()


def _fixed_revision_fixture(role: str) -> Path:
    """返回 Excel-008 一个角色的 download-only 固定资产树。

    输入参数：
        role：``input`` 或 ``gold``，分别对应 ``benchmark_dataset``
            与 ``answer_files``。
    输出返回值：
        包含 5 份已核验 XLSX 的目录；未配置 fixture 时跳过。
    """

    raw_root = os.environ.get(_FIXTURE_ENVIRONMENT_VARIABLE)
    if raw_root is None:
        pytest.skip(
            f"{_FIXTURE_ENVIRONMENT_VARIABLE} is required for download-only fixture"
        )
    role_directory = "benchmark_dataset" if role == "input" else "answer_files"
    candidate = Path(raw_root) / role_directory / TASK_UID
    if not candidate.is_dir():
        pytest.fail("Excel-008 fixed-revision fixture is unavailable")
    return candidate


def test_excel008_builder_separates_five_input_and_five_gold_identities() -> None:
    """验证 Excel-008 builder 固定 5 input + 5 gold 的不同字节闭集。

    输入参数：无；只读取 canonical 任务的固定身份。
    输出返回值：无；input 复用通用 AssetManifest，gold 使用
        pipeline 专属 strict manifest，两者均绑定 Lee 固定 revision。
    """

    documents = build_excel008_asset_manifest_documents(REPO_ROOT)

    assert set(documents) == {
        EXCEL008_INPUT_MANIFEST_PATH,
        EXCEL008_GOLD_MANIFEST_PATH,
    }
    input_document = documents[EXCEL008_INPUT_MANIFEST_PATH]
    assert input_document["asset_set_id"] == TASK_ID
    assert input_document["source"] == {
        "provider": "huggingface_dataset",
        "repository": "leeLegendary/Parallel_benchmark",
        "revision": PINNED_REVISION,
        "base_path": f"benchmark_dataset/{TASK_UID}",
        "license_status": "unverified",
    }
    assert input_document["files"] == [
        {
            "path": path,
            "size": size_bytes,
            "sha256": sha256,
            "media_type": XLSX_MEDIA_TYPE,
        }
        for path, size_bytes, sha256 in INPUT_FILES
    ]
    gold_document = documents[EXCEL008_GOLD_MANIFEST_PATH]
    assert gold_document["task_id"] == TASK_ID
    assert gold_document["task_uid"] == TASK_UID
    assert gold_document["source"]["revision"] == PINNED_REVISION
    assert gold_document["source"]["base_path"] == f"answer_files/{TASK_UID}"
    assert gold_document["entries"] == [
        {
            "path": path,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "media_type": XLSX_MEDIA_TYPE,
        }
        for path, size_bytes, sha256 in GOLD_FILES
    ]


def test_gold_schema_registers_excel008_as_an_exact_five_xlsx_identity() -> None:
    """验证共享 strict gold schema 为 Excel-008 建立五文件闭集。

    输入参数：无；读取 pipeline-implicit gold v1 schema。
    输出返回值：无；task/UID/manifest/base path 均精确绑定，
        且该分支只允许 5 份 XLSX。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    branches = {
        branch["properties"]["task_id"]["const"]: branch for branch in schema["oneOf"]
    }

    assert TASK_ID in schema["properties"]["task_id"]["enum"]
    assert TASK_UID in schema["properties"]["task_uid"]["enum"]
    branch = branches[TASK_ID]["properties"]
    assert branch["manifest_id"]["const"] == f"{TASK_ID}-gold-v1"
    assert branch["task_uid"]["const"] == TASK_UID
    assert branch["source"]["properties"]["base_path"]["const"] == (
        f"answer_files/{TASK_UID}"
    )
    assert branch["entries"]["minItems"] == 5
    assert branch["entries"]["maxItems"] == 5
    assert branch["entries"]["items"]["properties"]["media_type"] == {
        "const": XLSX_MEDIA_TYPE
    }


def test_excel008_formal_manifests_are_deterministic_builder_output() -> None:
    """验证 Excel-008 两份正式清单与 builder 输出逐字节一致。

    输入参数：无；读取 task-specific input/gold manifest。
    输出返回值：无；两份清单均由唯一 builder 产生，不依赖
        draft 占位或临时下载器。
    """

    documents = build_excel008_asset_manifest_documents(REPO_ROOT)

    assert check_excel008_asset_manifest_files(REPO_ROOT) is True
    for relative_path, document in documents.items():
        assert (REPO_ROOT / relative_path).read_bytes() == (
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_excel008_real_input_and_gold_pass_both_verification_chains() -> None:
    """验证固定 revision 的 5 input + 5 gold 通过两条安全链。

    输入参数：无；从显式 download-only fixture 读取真实字节。
    输出返回值：无；input 通过通用 AssetManifest，gold
        通过 strict bytes loader 与 held-dirfd resolver，闭集均为 5。
    """

    input_manifest = load_asset_manifest(REPO_ROOT / EXCEL008_INPUT_MANIFEST_PATH)
    input_verification = verify_asset_directory(
        input_manifest,
        _fixed_revision_fixture("input"),
    )
    gold_payload = (REPO_ROOT / EXCEL008_GOLD_MANIFEST_PATH).read_bytes()
    gold_manifest = load_verified_pipeline_implicit_gold_manifest(gold_payload)
    gold_bundle = resolve_verified_pipeline_implicit_gold_bundle(
        gold_payload,
        _fixed_revision_fixture("gold"),
    )

    assert input_manifest.asset_set_id == TASK_ID
    assert input_verification.ok is True
    assert gold_manifest.task_id == TASK_ID
    assert len(gold_manifest.entries) == 5
    assert gold_bundle.task_id == TASK_ID
    assert gold_bundle.file_count == 5
    observed_digests = {
        hashlib.sha256(item.read_for_pipeline()).hexdigest()
        for item in gold_bundle.iter_files_for_pipeline()
    }
    assert observed_digests == {sha256 for _, _, sha256 in GOLD_FILES}


@pytest.mark.parametrize("mutation", ("missing", "extra", "casefold_collision"))
def test_excel008_strict_gold_loader_rejects_non_exact_path_sets(
    mutation: str,
) -> None:
    """验证 gold manifest 的五路径闭集不可删减、增加或碰撞。

    输入参数：
        mutation：删除、增加或大小写折叠碰撞变体。
    输出返回值：
        无；任一非精确 path→size/SHA/MIME 映射均抛固定脱敏错误。
    """

    document = copy.deepcopy(
        build_excel008_asset_manifest_documents(REPO_ROOT)[EXCEL008_GOLD_MANIFEST_PATH]
    )
    if mutation == "missing":
        document["entries"].pop()
    else:
        extra = copy.deepcopy(document["entries"][0])
        extra["path"] = "extra.xlsx" if mutation == "extra" else "kfc_monthly_data.xlsx"
        document["entries"].append(extra)

    with pytest.raises(PipelineImplicitGoldManifestError) as captured:
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )

    assert str(captured.value) == "PIPELINE_IMPLICIT_GOLD_MANIFEST_INVALID"


@pytest.mark.parametrize("mutation", ("missing", "extra", "casefold_collision"))
def test_excel008_gold_resolver_rejects_non_exact_cache_path_sets(
    tmp_path: Path,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 held-dirfd resolver 在任何 payload 暴露前拒绝缓存漂移。

    输入参数：
        tmp_path：隔离的 gold 缓存副本。
        mutation：缺失、多余或大小写便携碰撞。
        monkeypatch：在大小写不敏感 macOS 上注入碰撞目录观测。
    输出返回值：无；解析仅产生固定完整性错误。
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
            """为真实 gold 根的目录观测注入大小写碰撞名。

            输入参数：
                path：production 枚举器传入的 held directory descriptor。
            输出返回值：
                原始目录项，目标根下再附加折叠碰撞拼写。
            """

            names = original_listdir(path)
            if GOLD_FILES[0][0] in names:
                return [*names, GOLD_FILES[0][0].lower()]
            return names

        monkeypatch.setattr(os, "listdir", listdir_with_collision)

    with pytest.raises(PipelineImplicitGoldIntegrityError) as captured:
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / EXCEL008_GOLD_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert str(captured.value) == "PIPELINE_IMPLICIT_GOLD_INTEGRITY_INVALID"
    assert "PRIVATE" not in repr(captured.value)


def test_excel008_gold_resolver_rejects_symlinked_ancestor(
    tmp_path: Path,
) -> None:
    """验证从文件系统锚点到 gold 根的每级祖先均不可是 symlink。

    输入参数：
        tmp_path：隔离目录，真实 gold 置于 ``real/gold``，经 ``alias``
            目录符号链接访问。
    输出返回值：无；即使五个最终文件正常也必须失败。
    """

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    shutil.copytree(_fixed_revision_fixture("gold"), real_parent / "gold")
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PipelineImplicitGoldIntegrityError):
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / EXCEL008_GOLD_MANIFEST_PATH).read_bytes(),
            alias_parent / "gold",
        )


def test_excel008_gold_resolver_rechecks_closure_after_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证首轮闭集后的竞态新增成员会被末轮枚举捕获。

    输入参数：
        tmp_path：隔离的 gold 副本。
        monkeypatch：在 production 首轮枚举完成后注入晚到文件。
    输出返回值：无；resolver 不得返回部分或过时 bundle。
    """

    cache_root = tmp_path / "gold"
    shutil.copytree(_fixed_revision_fixture("gold"), cache_root)
    original_enumerator = verified_assets._enumerate_regular_gold_paths
    call_count = 0

    def enumerate_then_mutate(root_descriptor: int) -> set[str]:
        """返回当前真实闭集，并在首次返回前注入晚到文件。

        输入参数：
            root_descriptor：production resolver 持有的 gold 根 descriptor。
        输出返回值：本轮注入前观测的路径集合。
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
            (REPO_ROOT / EXCEL008_GOLD_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert call_count == 2
