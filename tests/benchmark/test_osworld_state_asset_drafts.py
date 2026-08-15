"""13 个 legacy OSWorld state 任务资产/gold 草案的仓库级合同测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    REPO_ROOT / "benchmark" / "schemas" / "osworld-state-asset-draft-v1.schema.json"
)
DRAFT_ROOT = REPO_ROOT / "benchmark" / "assets" / "manifests" / "osworld-state-drafts"
GOLD_DRAFT_ROOT = (
    REPO_ROOT / "benchmark" / "gold" / "manifests" / "osworld-state-drafts"
)
XLANG_REVISION = "711e0811642364e7aa8f10a8918367d0b626d578"
DRAFT_TOOL_PATH = REPO_ROOT / "scripts" / "benchmark" / "osworld_state_asset_drafts.py"
DEPENDENCY_DOC_PATH = REPO_ROOT / "docs" / "evaluation" / "osworld-state-assets.md"
ARCHITECTURE_DEPENDENCY_PATH = (
    REPO_ROOT / "docs" / "architecture" / "dependency-tree.md"
)
TASK_ENTRY_COUNTS = {
    "Operation-FileOperate-BatchOperation-003": (1, 1),
    "Operation-FileOperate-CombinationDocs-009": (2, 1),
    "Operation-FileOperate-CombinationDocs-010": (1, 1),
    "Operation-FileOperate-CombinationDocs-011": (4, 1),
    "Operation-FileOperate-CombinationDocs-012": (15, 1),
    "Operation-FileOperate-CombinationDocs-013": (19, 2),
    "Operation-FileOperate-CombinationDocs-014": (20, 2),
    "Operation-FileOperate-SearchAndWrite-001": (1, 1),
    "Operation-FileOperate-SearchAndWrite-003": (2, 1),
    "Operation-FileOperate-SearchAndWrite-005": (1, 1),
    "Operation-FileOperate-SearchAndWrite-009": (1, 1),
    "Operation-FileOperate-Settings-001": (2, 1),
    "Operation-WebOperate-SearchAndWrite-001": (2, 1),
}
STRICT_STATE_TASK_IDS = frozenset(TASK_ENTRY_COUNTS)
STRICT_GOLD_TASK_IDS = STRICT_STATE_TASK_IDS - {"Operation-FileOperate-Settings-001"}
SETTINGS_HISTORICAL_DRAFT_GOLD_KEY = (
    "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v1"
)


def _load_json(path: Path) -> dict[str, object]:
    """读取仓库内一份 UTF-8 JSON object。

    输入参数：
        path：待核查的专属 draft manifest 路径。
    输出返回值：
        解析后的顶层 JSON object；非 object 立即使测试失败。
    """

    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_draft_tool() -> ModuleType:
    """从仓库路径加载确定性 state asset draft 生成器。

    输入参数：无；使用固定 ``DRAFT_TOOL_PATH``。
    输出返回值：可调用公开 builder/serializer 的生成器模块。
    """

    spec = importlib.util.spec_from_file_location(
        "osworld_state_asset_drafts",
        DRAFT_TOOL_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_batch_operation_003_drafts_pin_verified_input_and_gold_bytes() -> None:
    """验证首个 state 任务的 input/gold 草案固定权威字节身份。

    输入参数：
        无；读取 BatchOperation-003 的两份逐任务草案。
    输出返回值：
        无；input/gold 必须固定同一 HF commit、精确远端相对路径、用途与
        期望媒体类型、精确 size 与 SHA-256 均不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    manifests = (
        (
            "input",
            DRAFT_ROOT / f"{task_id}.input.draft.json",
            "multi_apps/5df7b33a-9f77-4101-823e-02f863e1c1ae/raw_book.zip",
            "Desktop/book.zip",
            "task_input_bundle",
            "application/zip",
            1_091_801,
            "f4c410119a88653225d8016d2594ae395d5b020e7b40067af0e72f0754b3c22e",
        ),
        (
            "gold",
            GOLD_DRAFT_ROOT / f"{task_id}.gold.draft.json",
            "multi_apps/5df7b33a-9f77-4101-823e-02f863e1c1ae/book.zip",
            None,
            "evaluator_gold",
            "application/zip",
            2_935_633,
            "5d028f5cb57e8f04fd8e5a65370959da91e7c873601bc1fcff9dc8ff5b72005f",
        ),
    )
    for (
        role,
        path,
        remote_path,
        guest_path,
        purpose,
        media_type,
        size_bytes,
        sha256,
    ) in manifests:
        manifest = _load_json(path)
        assert manifest["manifest_role"] == role
        assert manifest["draft_status"] == "integrity_verified"
        assert manifest["task_id"] == task_id
        assert manifest["source"] == {
            "provider": "huggingface_dataset",
            "repository": "xlangai/ubuntu_osworld_file_cache",
            "revision": XLANG_REVISION,
        }
        entries = manifest["entries"]
        assert isinstance(entries, list) and len(entries) == 1
        entry = entries[0]
        assert entry["remote_relative_path"] == remote_path
        assert entry.get("guest_relative_path") == guest_path
        assert entry["purpose"] == purpose
        assert entry["expected_media_type"] == media_type
        assert entry["integrity"] == {
            "status": "verified",
            "size_bytes": size_bytes,
            "sha256": sha256,
            "evidence_ref": (
                "https://huggingface.co/datasets/"
                "xlangai/ubuntu_osworld_file_cache/resolve/"
                f"{XLANG_REVISION}/{remote_path}"
            ),
        }


def test_draft_builder_accepts_thirteen_inputs_but_only_twelve_verified_remote_gold_sets() -> (
    None
):
    """验证 13 组 input 闭合且旧 Settings 远端 gold 仍失败关闭。

    输入参数：
        无；调用公开 builder 读取当前 13 份 canonical task。
    输出返回值：
        无；13 份 input 草案均为 verified，12 份远端 gold 闭合；
        Settings-001 的旧 v1 远端 gold 仍为 unverified 历史负样本。
    """

    generator = _load_draft_tool()
    documents = generator.build_osworld_state_asset_drafts(REPO_ROOT)
    for task_id in STRICT_STATE_TASK_IDS:
        assert (
            documents[
                f"benchmark/assets/manifests/osworld-state-drafts/"
                f"{task_id}.input.draft.json"
            ]["draft_status"]
            == "integrity_verified"
        )
    for task_id in STRICT_GOLD_TASK_IDS:
        assert (
            documents[
                f"benchmark/gold/manifests/osworld-state-drafts/"
                f"{task_id}.gold.draft.json"
            ]["draft_status"]
            == "integrity_verified"
        )
    settings_gold_draft = documents[
        "benchmark/gold/manifests/osworld-state-drafts/"
        "Operation-FileOperate-Settings-001.gold.draft.json"
    ]
    assert settings_gold_draft["schema_version"] == 1
    assert settings_gold_draft["draft_status"] == "integrity_unverified"
    assert settings_gold_draft["distribution_policy"] == "download_only"
    assert settings_gold_draft["entries"][0]["logical_key"] == (
        SETTINGS_HISTORICAL_DRAFT_GOLD_KEY
    )
    assert settings_gold_draft["entries"][0]["remote_relative_path"].endswith(
        "/landscape.png"
    )


def test_settings_canonical_accepts_the_promoted_derived_gold_reference() -> None:
    """验证 draft 工具接受 Settings 唯一正式 v2 gold 引用。

    输入参数：
        无；读取当前 canonical task 并调用公开 builder。
    输出返回值：
        无；精确的正式 derived-gold manifest 引用通过 canonical mode
        门禁，且历史 gold draft 仍会生成。
    """

    generator = _load_draft_tool()
    documents = generator.build_osworld_state_asset_drafts(REPO_ROOT)

    assert (
        "benchmark/gold/manifests/osworld-state-drafts/"
        "Operation-FileOperate-Settings-001.gold.draft.json"
    ) in documents


def test_settings_canonical_rejects_a_drifted_gold_manifest_reference(
    tmp_path: Path,
) -> None:
    """验证 Settings 不能用任意 gold 引用绕过正式清单门禁。

    输入参数：
        tmp_path：pytest 提供的隔离 canonical task 目录。
    输出返回值：
        无；将正式引用替换为相似但未登记的路径后，builder 必须在
        任何 draft 生成前失败关闭。
    """

    task_root = tmp_path / "benchmark" / "tasks"
    task_root.mkdir(parents=True)
    for task_id in TASK_ENTRY_COUNTS:
        source = REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json"
        (task_root / source.name).write_bytes(source.read_bytes())
    settings_path = task_root / "Operation-FileOperate-Settings-001.json"
    settings = _load_json(settings_path)
    settings["gold_manifest"] = (
        "benchmark/gold/manifests/Operation-FileOperate-Settings-001-drift.json"
    )
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    generator = _load_draft_tool()

    with pytest.raises(
        generator.OSWorldStateAssetDraftError,
        match="state canonical asset mode 无效",
    ):
        generator.build_osworld_state_asset_drafts(tmp_path)


def test_state_asset_draft_schema_is_closed_and_fail_closed() -> None:
    """验证专属 schema 对角色、完整性与许可证状态严格闭集。

    输入参数：
        无；读取 state asset draft JSON Schema。
    输出返回值：
        无；所有 object 禁止额外字段，input/gold entry 分开建模，且
        unverified integrity/license 的未知值只能是 ``null``。
    """

    schema = _load_json(SCHEMA_PATH)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    for definition in definitions.values():
        if isinstance(definition, dict) and definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])

    integrity = definitions["integrity"]["oneOf"]
    assert integrity[0]["properties"]["status"]["const"] == "verified"
    assert integrity[1]["properties"]["status"]["const"] == "unverified"
    for field in ("size_bytes", "sha256", "evidence_ref"):
        assert integrity[1]["properties"][field]["type"] == "null"

    license_union = definitions["license"]["oneOf"]
    assert license_union[0]["properties"]["status"]["const"] == "verified"
    assert license_union[1]["properties"]["status"]["const"] == "unverified"
    assert license_union[1]["properties"]["spdx_expression"]["type"] == "null"
    assert license_union[1]["properties"]["evidence_ref"]["type"] == "null"
    assert "guest_relative_path" in definitions["inputEntry"]["required"]
    assert "logical_key" not in definitions["inputEntry"]["properties"]
    assert "logical_key" in definitions["goldEntry"]["required"]
    assert "guest_relative_path" not in definitions["goldEntry"]["properties"]


def test_all_thirteen_state_tasks_have_exact_71_input_and_15_gold_entries() -> None:
    """验证完整待下载矩阵与 canonical/evidence 身份形成闭包。

    输入参数：
        无；通过公开 builder 生成 13×2 份草案并读取落盘副本。
    输出返回值：
        无；任务集合、逐任务/总计数量、固定 revision、source identity、
        远端路径顺序和未验证 metadata 必须精确一致；canonical task
        必须引用各自已晋升的正式 input/gold manifest。
    """

    tool = _load_draft_tool()
    documents = tool.build_osworld_state_asset_drafts(REPO_ROOT)
    assert isinstance(documents, dict) and len(documents) == 26
    assert sum(counts[0] for counts in TASK_ENTRY_COUNTS.values()) == 71
    assert sum(counts[1] for counts in TASK_ENTRY_COUNTS.values()) == 15

    observed_totals = {"input": 0, "gold": 0}
    for task_id, (input_count, gold_count) in TASK_ENTRY_COUNTS.items():
        task = _load_json(REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json")
        assert "prepare_script_path" not in task
        assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
        assert task["gold_manifest"] == (f"benchmark/gold/manifests/{task_id}.json")
        evidence_spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]
        expected_gold_keys = (
            (SETTINGS_HISTORICAL_DRAFT_GOLD_KEY,)
            if task_id == "Operation-FileOperate-Settings-001"
            else tuple(
                key
                for slot in evidence_spec.artifact_slots
                for metric in slot.metrics
                for key in metric.gold_keys
            )
        )
        for role, expected_count in (
            ("input", input_count),
            ("gold", gold_count),
        ):
            relative_path = tool.draft_manifest_relative_path(task_id, role)
            manifest = _load_json(REPO_ROOT / relative_path)
            assert manifest == documents[relative_path]
            assert manifest["manifest_role"] == role
            assert manifest["task_uid"] == task["task_uid"]
            assert manifest["source_task_id"] == evidence_spec.source_task_id
            assert manifest["source_evaluator_id"] == evidence_spec.source_evaluator_id
            assert (
                manifest["source_contract_sha256"]
                == evidence_spec.source_contract_sha256
            )
            entries = manifest["entries"]
            assert isinstance(entries, list) and len(entries) == expected_count
            observed_totals[role] += len(entries)
            remote_paths = [entry["remote_relative_path"] for entry in entries]
            assert len(remote_paths) == len(set(remote_paths))
            if role == "input":
                assert remote_paths == sorted(
                    remote_paths,
                    key=lambda value: value.encode("utf-8"),
                )
            else:
                assert [entry["expected_index"] for entry in entries] == list(
                    range(expected_count)
                )
            for entry in entries:
                role_is_strict = role == "input" or task_id in STRICT_GOLD_TASK_IDS
                if role_is_strict:
                    assert entry["integrity"]["status"] == "verified"
                    assert entry["integrity"]["size_bytes"] > 0
                    assert len(entry["integrity"]["sha256"]) == 64
                    assert entry["integrity"]["evidence_ref"].startswith(
                        "https://huggingface.co/datasets/"
                    )
                else:
                    assert entry["integrity"] == {
                        "status": "unverified",
                        "size_bytes": None,
                        "sha256": None,
                        "evidence_ref": None,
                    }

            source = manifest["source"]
            assert source == {
                "provider": "huggingface_dataset",
                "repository": "xlangai/ubuntu_osworld_file_cache",
                "revision": XLANG_REVISION,
            }
            if role == "gold":
                assert tuple(entry["logical_key"] for entry in entries) == (
                    expected_gold_keys
                )

    assert observed_totals == {"input": 71, "gold": 15}


def test_state_asset_dependency_doc_has_exact_download_matrix_and_gates() -> None:
    """验证依赖说明公开 13-task 矩阵与诚实晋升门禁。

    输入参数：
        无；读取专属依赖与待下载说明。
    输出返回值：
        无；每个任务均出现且逐项计数可核对，文档明确历史 remote
        草案的 85 条 integrity-verified 与 1 条未验证 gold，同时声明
        Settings 正式 schema-v2 私有派生身份和 12-task candidate 边界。
    """

    content = DEPENDENCY_DOC_PATH.read_text(encoding="utf-8")
    normalized_content = " ".join(content.split())
    for task_id, (input_count, gold_count) in TASK_ENTRY_COUNTS.items():
        assert f"| `{task_id}` | {input_count} | {gold_count} |" in content
    for required_text in (
        "71 个 input",
        "15 个 remote gold 引用",
        "71 个 input 与 14 个 gold 已完成 size/SHA-256 核验",
        "1 个历史 remote gold 仍为 `integrity_unverified`",
        "共 85 条 `integrity_verified`",
        "raw_book.zip",
        "1,091,801 bytes",
        "gold `book.zip` 为 2,935,633 bytes",
        "3 个 source start-context 歧义也已",
        "711e0811642364e7aa8f10a8918367d0b626d578",
        "xlang 数据卡声明 `Apache-2.0`",
        "Settings-001",
        "0.7960269769984115",
        "private derived v2 gold",
        "13 个任务同时闭合正式 gold 身份",
        "13 个任务已完成 input 资产切换",
        "osworld_state_asset_drafts.py check",
    ):
        assert required_text in normalized_content


def test_architecture_tree_separates_input_closure_from_semantic_gates() -> None:
    """验证总依赖树分离已闭合 input 与仍保留的语义门禁。

    输入参数：
        无；读取规范架构依赖树。
    输出返回值：
        无；树中必须显示 canonical/spec/audit 到 26 份 draft 的生成链，
        并精确记录 71/71 input、13 份 strict input、12 份 v1 download
        gold 与 Settings v2 derived gold，且无未解决 context 歧义。
    """

    content = " ".join(ARCHITECTURE_DEPENDENCY_PATH.read_text(encoding="utf-8").split())
    for required_text in (
        "scripts/benchmark/osworld_state_asset_drafts",
        "13 input drafts + 13 evaluator-only gold drafts",
        "71 input / 15 gold",
        "13 strict input manifests",
        "12 strict v1 download gold manifests",
        "Settings strict v2 derived manifest",
        "71/71 input verified",
        "0 inferred paths",
        "0 source start-context ambiguous",
        "13 formal gold identities",
    ):
        assert required_text in content
