"""OSWorld artifact 任务固定输入资产的仓库级回归测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paraguibench.runtime.assets import load_asset_manifest
from paraguibench.runtime.artifact_family_task_prepare import (
    inspect_artifact_family_task_prepare_capability,
)
from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    load_gold_asset_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "Operation-FileOperate-CombinationDocs-015"
EXPECTED_REVISION = "711e0811642364e7aa8f10a8918367d0b626d578"
EXPECTED_SHA256 = "b72891462fe583aebd9c48bdaf431fa0ce20047b2de47b2249ac925fa221e7ef"
BATCH_TASK_ID = "Operation-FileOperate-BatchOperation-003"
BATCH_SOURCE_ID = "5df7b33a-9f77-4101-823e-02f863e1c1ae"
BATCH_INPUT_SHA256 = "f4c410119a88653225d8016d2594ae395d5b020e7b40067af0e72f0754b3c22e"
STRICT_TRACER_TASK_ID = "Operation-FileOperate-CombinationDocs-010"
STRICT_TRACER_SOURCE_ID = "aceb0368-56b8-4073-b70e-3dc9aee184e0"
STRICT_INPUT_AND_GOLD_STATE_TASK_IDS = (
    "Operation-FileOperate-CombinationDocs-009",
    "Operation-FileOperate-CombinationDocs-010",
    "Operation-FileOperate-CombinationDocs-011",
    "Operation-FileOperate-CombinationDocs-012",
    "Operation-FileOperate-CombinationDocs-013",
    "Operation-FileOperate-CombinationDocs-014",
    "Operation-FileOperate-SearchAndWrite-001",
    "Operation-FileOperate-SearchAndWrite-003",
    "Operation-FileOperate-SearchAndWrite-005",
    "Operation-FileOperate-SearchAndWrite-009",
    "Operation-WebOperate-SearchAndWrite-001",
)


def test_combination_docs_011_closes_assets_and_idle_desktop_context() -> None:
    """验证首个 state 任务同时闭合固定资产与已查明的空闲桌面语义。

    输入参数：
        无；读取 CombinationDocs-011 canonical、input/gold draft、正式
        manifest 和 pre-Docker 能力。
    输出返回值：
        无；固定 xlang 字节、正式清单、canonical 与无预开窗口的 source
        config 形成可执行闭包。
    """

    task_id = "Operation-FileOperate-CombinationDocs-011"
    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert task["gold_manifest"] == f"benchmark/gold/manifests/{task_id}.json"

    input_manifest = load_asset_manifest(REPO_ROOT / task["asset_manifest"])
    gold_manifest = load_gold_asset_manifest(REPO_ROOT / task["gold_manifest"])
    assert input_manifest.source.repository == ("xlangai/ubuntu_osworld_file_cache")
    assert input_manifest.source.revision == EXPECTED_REVISION
    assert len(input_manifest.files) == 4
    assert len(gold_manifest.entries) == 1
    assert gold_manifest.entries[0].source_locator.revision == EXPECTED_REVISION

    capability = inspect_artifact_family_task_prepare_capability(
        repo_root=REPO_ROOT,
        task=task,
    )
    assert capability is not None
    assert capability.ready is True
    assert capability.inferred_path_count == 0
    assert capability.unverified_integrity_count == 0
    assert capability.blocker_ids == ()


def test_combination_docs_012_replaces_lee_reference_and_is_ready() -> None:
    """验证第二个任务从固定 xlang source task 读取资产并可准备。

    输入参数：
        无；读取 CombinationDocs-012 canonical、两份正式 manifest
        和 pre-Docker 能力。
    输出返回值：
        无；旧 Lee/tree 引用消失，15 个 input 和 1 个 gold 固定到 xlang
        commit，且空闲桌面 start context 不再产生 blocker。
    """

    task_id = "Operation-FileOperate-CombinationDocs-012"
    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert task["gold_manifest"] == f"benchmark/gold/manifests/{task_id}.json"
    assert "leeLegendary" not in json.dumps(task)

    input_manifest = load_asset_manifest(REPO_ROOT / task["asset_manifest"])
    gold_manifest = load_gold_asset_manifest(REPO_ROOT / task["gold_manifest"])
    assert input_manifest.source.repository == ("xlangai/ubuntu_osworld_file_cache")
    assert input_manifest.source.revision == EXPECTED_REVISION
    assert len(input_manifest.files) == 15
    assert len(gold_manifest.entries) == 1
    assert gold_manifest.entries[0].source_locator.repository == (
        "xlangai/ubuntu_osworld_file_cache"
    )

    capability = inspect_artifact_family_task_prepare_capability(
        repo_root=REPO_ROOT,
        task=task,
    )
    assert capability is not None
    assert capability.inferred_path_count == 0
    assert capability.unverified_integrity_count == 0
    assert capability.ready is True
    assert capability.blocker_ids == ()


def test_combination_docs_013_closes_gold_entries_and_idle_desktop_context() -> None:
    """验证资助数据任务闭合 input/gold 与已查明的空闲桌面语义。

    输入参数：
        无；读取 CombinationDocs-013 canonical、19 项 input、两项
        evaluator-only gold 与 pre-Docker 能力。
    输出返回值：
        无；所有 locator 固定到 source task 的 xlang commit，且来源 config
        不预开 Calc/Files 的语义形成 ready 能力。
    """

    task_id = "Operation-FileOperate-CombinationDocs-013"
    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert task["gold_manifest"] == f"benchmark/gold/manifests/{task_id}.json"

    input_manifest = load_asset_manifest(REPO_ROOT / task["asset_manifest"])
    gold_manifest = load_gold_asset_manifest(REPO_ROOT / task["gold_manifest"])
    assert input_manifest.source.repository == ("xlangai/ubuntu_osworld_file_cache")
    assert input_manifest.source.revision == EXPECTED_REVISION
    assert input_manifest.source.base_path == (
        "multi_apps/7e287123-70ca-47b9-8521-47db09b69b14"
    )
    assert len(input_manifest.files) == 19
    assert len(gold_manifest.entries) == 2
    assert {entry.provenance.expected_index for entry in gold_manifest.entries} == {
        0,
        1,
    }

    capability = inspect_artifact_family_task_prepare_capability(
        repo_root=REPO_ROOT,
        task=task,
    )
    assert capability is not None
    assert capability.inferred_path_count == 0
    assert capability.unverified_integrity_count == 0
    assert capability.ready is True
    assert capability.blocker_ids == ()


def test_settings_001_promotes_private_derived_gold_and_keeps_old_draft_unverified() -> (
    None
):
    """验证 Settings-001 晋升私有派生 gold 并保留旧远端负样本。

    输入参数：
        无；读取 Settings-001 canonical、正式 input/derived-gold manifest、
        历史远端 gold draft 和 pre-Docker 资产准备能力。
    输出返回值：
        无；instruction 与 input 语义不变，canonical 仅增加 v2 私有派生
        gold 引用，旧 v1 landscape.png 仍为 unverified 时通过。
    """

    task_id = "Operation-FileOperate-Settings-001"
    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json"
    task_bytes = task_path.read_bytes()
    task = json.loads(task_bytes)

    assert "prepare_script_path" not in task
    assert task["instruction"] == (
        "The scenery at 00:08 in this video is very beautiful. Please extract "
        "this frame and set it as the background of the second page of the "
        "opened slide. "
    )
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert task["gold_manifest"] == (f"benchmark/gold/manifests/{task_id}.json")

    input_manifest = load_asset_manifest(REPO_ROOT / task["asset_manifest"])
    assert input_manifest.source.repository == ("xlangai/ubuntu_osworld_file_cache")
    assert input_manifest.source.revision == EXPECTED_REVISION
    assert {entry.path for entry in input_manifest.files} == {
        "Robotic_Workshop_Infographics.pptx",
        "landscape.mp4",
    }
    input_draft = json.loads(
        (
            REPO_ROOT
            / "benchmark/assets/manifests/osworld-state-drafts"
            / f"{task_id}.input.draft.json"
        ).read_text(encoding="utf-8")
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in input_manifest.files
    ] == [
        (
            Path(entry["remote_relative_path"]).name,
            entry["integrity"]["size_bytes"],
            entry["integrity"]["sha256"],
            entry["expected_media_type"],
        )
        for entry in input_draft["entries"]
    ]

    gold_manifest = load_gold_asset_manifest(REPO_ROOT / task["gold_manifest"])
    assert isinstance(gold_manifest, DerivedGoldAssetManifest)
    assert gold_manifest.schema_version == 2
    assert gold_manifest.distribution_policy == "private_materialization_only"
    assert gold_manifest.entries[0].logical_key == (
        "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v2"
    )

    gold_draft = json.loads(
        (
            REPO_ROOT
            / "benchmark/gold/manifests/osworld-state-drafts"
            / f"{task_id}.gold.draft.json"
        ).read_text(encoding="utf-8")
    )
    assert gold_draft["draft_status"] == "integrity_unverified"
    assert gold_draft["schema_version"] == 1
    assert gold_draft["entries"][0]["logical_key"] == (
        "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v1"
    )
    assert gold_draft["entries"][0]["remote_relative_path"].endswith("/landscape.png")
    assert gold_draft["entries"][0]["integrity"] == {
        "status": "unverified",
        "size_bytes": None,
        "sha256": None,
        "evidence_ref": None,
    }

    capability = inspect_artifact_family_task_prepare_capability(
        repo_root=REPO_ROOT,
        task=task,
    )
    assert capability is not None
    assert capability.ready is True
    assert capability.blocker_ids == ()

    release_manifest = json.loads(
        (REPO_ROOT / "benchmark/manifests/release-v1.json").read_text(encoding="utf-8")
    )
    release_task = next(
        entry for entry in release_manifest["tasks"] if entry["task_id"] == task_id
    )
    assert release_task["sha256"] == hashlib.sha256(task_bytes).hexdigest()


def test_combination_docs_015_uses_pinned_input_asset_manifest() -> None:
    """验证 CombinationDocs-015 的输入文件不再依赖可变下载地址。

    输入参数：
        无；从仓库读取 canonical task 和它声明的输入资产清单。
    输出返回值：
        无；固定仓库、commit、基础路径、许可、文件大小和摘要全部匹配时
        测试通过。
    """

    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{TASK_ID}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (
        "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-015.json"
    )
    assert task["gold_manifest"] == (
        "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-015.json"
    )

    manifest_path = REPO_ROOT / task["asset_manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest == {
        "schema_version": 1,
        "asset_set_id": TASK_ID,
        "source": {
            "provider": "huggingface_dataset",
            "repository": "xlangai/ubuntu_osworld_file_cache",
            "revision": EXPECTED_REVISION,
            "base_path": ("multi_apps/df67aebb-fb3a-44fd-b75b-51b6012df509"),
            "license_status": "apache-2.0",
        },
        "distribution_policy": "download_only",
        "files": [
            {
                "path": "references.docx",
                "size": 14_104,
                "sha256": EXPECTED_SHA256,
            }
        ],
    }


def test_batch_operation_003_has_strict_pinned_input_zip_manifest() -> None:
    """验证 BatchOperation-003 输入 ZIP 形成单文件严格下载闭集。

    输入参数：
        无；读取仓库内正式 input manifest 并通过 production loader。
    输出返回值：
        无；来源固定到 xlang commit，文件名、size、SHA 与 ZIP MIME 精确
        匹配，且 manifest 没有第二个隐式输入。
    """

    manifest_path = (
        REPO_ROOT / "benchmark" / "assets" / "manifests" / f"{BATCH_TASK_ID}.json"
    )
    manifest = load_asset_manifest(manifest_path)

    assert manifest.asset_set_id == BATCH_TASK_ID
    assert manifest.source.repository == "xlangai/ubuntu_osworld_file_cache"
    assert manifest.source.revision == EXPECTED_REVISION
    assert manifest.source.base_path == f"multi_apps/{BATCH_SOURCE_ID}"
    assert manifest.source.license_status == "apache-2.0"
    assert manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in manifest.files
    ] == [
        (
            "raw_book.zip",
            1_091_801,
            BATCH_INPUT_SHA256,
            "application/zip",
        )
    ]


def test_combination_docs_010_closes_strict_input_gold_and_canonical() -> None:
    """验证首个多任务 tracer 的 canonical、input 与 gold 三者闭合。

    输入参数：
        无；通过 production input/gold loader 读取仓库声明。
    输出返回值：
        无；仅当 legacy URL 已移除，两份 manifest 的固定来源、
        字节摘要与 evaluator-only provenance 全部一致时通过。
    """

    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{STRICT_TRACER_TASK_ID}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (
        f"benchmark/assets/manifests/{STRICT_TRACER_TASK_ID}.json"
    )
    assert task["gold_manifest"] == (
        f"benchmark/gold/manifests/{STRICT_TRACER_TASK_ID}.json"
    )

    input_manifest = load_asset_manifest(REPO_ROOT / task["asset_manifest"])
    assert input_manifest.asset_set_id == STRICT_TRACER_TASK_ID
    assert input_manifest.source.repository == ("xlangai/ubuntu_osworld_file_cache")
    assert input_manifest.source.revision == EXPECTED_REVISION
    assert input_manifest.source.base_path == (f"multi_apps/{STRICT_TRACER_SOURCE_ID}")
    assert input_manifest.source.license_status == "apache-2.0"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in input_manifest.files
    ] == [
        (
            "exam.zip",
            387_112,
            "10d6ef9c161b2bbb6eb6515f0e0c1717c39f675d7b569fcac55477f176b1c7c1",
            "application/zip",
        )
    ]

    gold_manifest = load_gold_asset_manifest(REPO_ROOT / task["gold_manifest"])
    assert len(gold_manifest.entries) == 1
    gold = gold_manifest.entries[0]
    assert gold.logical_key == (f"osworld-gold:{STRICT_TRACER_SOURCE_ID}:expected:0:v1")
    assert gold.source_locator.repository == ("xlangai/ubuntu_osworld_file_cache")
    assert gold.source_locator.revision == EXPECTED_REVISION
    assert gold.source_locator.path == (
        f"multi_apps/{STRICT_TRACER_SOURCE_ID}/grades.xlsx"
    )
    assert gold.size == 9_614
    assert gold.sha256 == (
        "7e6b3a6dae808cef87b2847933db04eb2138d82cf1d7b354ff1bbc88bb86f842"
    )
    assert gold.provenance.source_task_id == STRICT_TRACER_SOURCE_ID
    assert gold.provenance.source_evaluator_id == STRICT_TRACER_SOURCE_ID


@pytest.mark.parametrize("task_id", STRICT_INPUT_AND_GOLD_STATE_TASK_IDS)
def test_state_task_closes_verified_drafts_into_runtime_manifests(
    task_id: str,
) -> None:
    """验证无语义裁定的 state 任务完成 strict-only 资产闭合。

    输入参数：
        task_id：11 个已同时闭合正式 input/gold 的 canonical
            state 任务之一；资产闭合不代表 start context 已裁定。
    输出返回值：
        无；canonical 只声明两份正式 manifest，production loader
        接受 input/gold，且 size/SHA/MIME 与已验证 draft 及
        source task/evaluator 双重身份逐项一致时通过。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert task["gold_manifest"] == (f"benchmark/gold/manifests/{task_id}.json")

    input_draft = json.loads(
        (
            REPO_ROOT
            / "benchmark/assets/manifests/osworld-state-drafts"
            / f"{task_id}.input.draft.json"
        ).read_text(encoding="utf-8")
    )
    gold_draft = json.loads(
        (
            REPO_ROOT
            / "benchmark/gold/manifests/osworld-state-drafts"
            / f"{task_id}.gold.draft.json"
        ).read_text(encoding="utf-8")
    )
    assert input_draft["draft_status"] == "integrity_verified"
    assert gold_draft["draft_status"] == "integrity_verified"
    assert (
        input_draft["source"]
        == gold_draft["source"]
        == {
            "provider": "huggingface_dataset",
            "repository": "xlangai/ubuntu_osworld_file_cache",
            "revision": EXPECTED_REVISION,
        }
    )

    input_manifest = load_asset_manifest(REPO_ROOT / task["asset_manifest"])
    assert input_manifest.asset_set_id == task_id
    assert input_manifest.source.repository == input_draft["source"]["repository"]
    assert input_manifest.source.revision == EXPECTED_REVISION
    assert input_manifest.source.license_status == "apache-2.0"
    expected_input = [
        (
            Path(entry["remote_relative_path"]).name,
            entry["integrity"]["size_bytes"],
            entry["integrity"]["sha256"],
            entry["expected_media_type"],
        )
        for entry in input_draft["entries"]
    ]
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in input_manifest.files
    ] == expected_input

    gold_manifest = load_gold_asset_manifest(REPO_ROOT / task["gold_manifest"])
    assert len(gold_manifest.entries) == len(gold_draft["entries"])
    for actual, expected in zip(
        gold_manifest.entries,
        gold_draft["entries"],
        strict=True,
    ):
        assert actual.logical_key == expected["logical_key"]
        assert actual.source_locator.path == expected["remote_relative_path"]
        assert actual.size == expected["integrity"]["size_bytes"]
        assert actual.sha256 == expected["integrity"]["sha256"]
        assert actual.media_type == expected["expected_media_type"]
        assert actual.provenance.source_task_id == gold_draft["source_task_id"]
        assert (
            actual.provenance.source_evaluator_id == gold_draft["source_evaluator_id"]
        )
        assert (
            actual.provenance.source_contract_sha256
            == gold_draft["source_contract_sha256"]
        )
