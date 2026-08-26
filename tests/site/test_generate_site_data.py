"""验证 GitHub Pages 公共数据集的确定性生成契约。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
)
from paraguibench.runtime.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
    ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
    ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
    inspect_artifact_family_task_prepare_capability,
)
from paraguibench.runtime.osworld_environment import (
    OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts/site/generate_site_data.py"
RUNTIME_SUPPORT_GENERATOR = REPO_ROOT / "scripts/benchmark/runtime_support_manifest.py"
PUBLIC_TASK_FIELDS = {
    "task_id",
    "category",
    "benchmark_group",
    "source",
    "tag",
    "type",
    "environment_protocol",
    "evaluation_protocol",
    "asset_status",
    "local_readiness_status",
    "support_status",
    "blocker_codes",
}
FORBIDDEN_KEYS = {
    "instruction",
    "instruction_template",
    "answer",
    "accepted_answers",
    "fixture",
    "fixture_ref",
    "profile",
    "url",
    "path",
    "api_key",
    "credential",
    "model",
}
GLOBAL_IMAGE_BLOCKER = "osworld_vm_image_materialization_unverified"


def _load_generator_module():
    """从脚本路径加载生成器，避免要求仓库脚本成为安装包。

    输入参数：
        无。
    输出返回值：
        已执行的 ``generate_site_data`` 模块。
    """

    spec = importlib.util.spec_from_file_location(
        "generate_site_data",
        GENERATOR,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载站点数据生成器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_generator_module = _load_generator_module()
build_site_data = _generator_module.build_site_data
VALUE_LABELS = _generator_module.VALUE_LABELS
SiteDataError = _generator_module.SiteDataError


def _load_runtime_support_module():
    """从独立脚本路径加载当前 runtime-support 生成器。

    输入参数：
        无；脚本路径由仓库根目录固定派生。
    输出返回值：
        可构建当前未落盘 runtime-support 投影的已加载模块。
    """

    spec = importlib.util.spec_from_file_location(
        "generate_site_data_runtime_support_tool",
        RUNTIME_SUPPORT_GENERATOR,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 runtime-support 生成器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_site_data_preserves_release_and_support_totals() -> None:
    """生成结果必须覆盖完整 release，并准确反映支持状态汇总。"""

    data = build_site_data(REPO_ROOT)

    assert data["summary"]["task_count"] == 233
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }
    assert data["summary"]["local_readiness_status_counts"] == {
        "local_components_incomplete": 0,
        "local_ready": 233,
    }
    assert data["labels"]["values"]["local_readiness_status"]["local_ready"] == {
        "en": "Local components ready (not live-validated)",
        "zh-CN": "本地组件已闭合（非实机验证）",
    }
    assert len(data["tasks"]) == 233


def test_site_data_exposes_exact_local_readiness_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """公开站点数据必须与正式 live 状态分开展示本地就绪度。

    输入参数：
        tmp_path：pytest 隔离目录，用于放置尚未串行落盘的新投影。
        monkeypatch：只将站点生成器的 runtime 输入指向该临时文件。
    输出返回值：
        无；精确断言 233 项 local-ready，同时 233 项仍为正式
        blocked。
    """

    runtime_module = _load_runtime_support_module()
    runtime_path = tmp_path / "runtime-support-v1.json"
    runtime_path.write_text(
        json.dumps(
            runtime_module.build_runtime_support_manifest(REPO_ROOT),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _generator_module,
        "RUNTIME_SUPPORT_MANIFEST",
        runtime_path,
    )

    data = build_site_data(REPO_ROOT)
    expected_incomplete_task_ids: set[str] = set()

    assert data["summary"]["local_readiness_status_counts"] == {
        "local_components_incomplete": 0,
        "local_ready": 233,
    }
    assert {
        task["task_id"]
        for task in data["tasks"]
        if task["local_readiness_status"] == "local_components_incomplete"
    } == expected_incomplete_task_ids
    assert data["labels"]["values"]["local_readiness_status"]["local_ready"] == {
        "en": "Local components ready (not live-validated)",
        "zh-CN": "本地组件已闭合（非实机验证）",
    }
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }


def test_site_data_rejects_drifted_runtime_local_readiness_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """站点不得接受与每任务投影不一致的手工根计数。

    输入参数：
        tmp_path：pytest 隔离目录，用于写入被篡改的 runtime 清单。
        monkeypatch：将站点生成器指向隔离输入。
    输出返回值：
        无；任一 local-readiness 计数漂移均必须失败关闭。
    """

    runtime_module = _load_runtime_support_module()
    runtime = runtime_module.build_runtime_support_manifest(REPO_ROOT)
    runtime["local_readiness_status_counts"]["local_ready"] += 1
    runtime_path = tmp_path / "runtime-support-v1.json"
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        _generator_module,
        "RUNTIME_SUPPORT_MANIFEST",
        runtime_path,
    )

    with pytest.raises(SiteDataError, match="local-readiness"):
        build_site_data(REPO_ROOT)


def test_benchmark_groups_match_the_six_paper_categories() -> None:
    """论文六类分组必须覆盖全部任务，并优先抽取 SearchAndWrite。"""

    data = build_site_data(REPO_ROOT)
    assert data["summary"]["benchmark_group_counts"] == {
        "FileOperation": 42,
        "FileSearch": 12,
        "OnlineShopping": 91,
        "SearchAndWrite": 10,
        "WebNavigation": 13,
        "WebSearch": 65,
    }
    task_groups = {task["task_id"]: task["benchmark_group"] for task in data["tasks"]}
    assert task_groups["Operation-FileOperate-SearchAndWrite-001"] == "SearchAndWrite"
    assert task_groups["Operation-WebOperate-SearchAndWrite-001"] == "SearchAndWrite"
    assert task_groups["InformationRetrieval-VisualSearch-Video-001"] == "WebSearch"
    assert task_groups["InformationRetrieval-FileSearch-Readonly-001"] == "FileSearch"
    assert task_groups["Operation-OnlineShopping-AddToCart-001"] == "OnlineShopping"
    assert task_groups["Operation-FileOperate-Settings-001"] == "FileOperation"
    assert task_groups["Operation-WebOperate-Settings-001"] == "WebNavigation"


def test_public_dataset_has_a_closed_safe_field_set_and_bilingual_labels() -> None:
    """每条任务只能暴露白名单元数据，所有分类值都必须有双语标签。"""

    data = build_site_data(REPO_ROOT)

    assert all(set(task) == PUBLIC_TASK_FIELDS for task in data["tasks"])
    assert not (_collect_keys(data) & FORBIDDEN_KEYS)
    serialized = json.dumps(data, ensure_ascii=False)
    assert not re.search(r"(?:https?|file)://", serialized, re.IGNORECASE)
    private_address = ".".join(("10", "1", "110", "114"))
    macos_home_root = "".join(("/", "Users", "/"))
    linux_home_root = "".join(("/", "home", "/"))
    assert private_address not in serialized
    assert macos_home_root not in serialized
    assert linux_home_root not in serialized

    labels = data["labels"]
    assert labels["fields"]["task_id"] == {
        "en": "Task ID",
        "zh-CN": "任务 ID",
    }
    dimensions = {
        "category": {task["category"] for task in data["tasks"]},
        "benchmark_group": {task["benchmark_group"] for task in data["tasks"]},
        "source": {task["source"] for task in data["tasks"]},
        "tag": {task["tag"] for task in data["tasks"]},
        "type": {task["type"] for task in data["tasks"]},
        "environment_protocol": {
            task["environment_protocol"] for task in data["tasks"]
        },
        "evaluation_protocol": {task["evaluation_protocol"] for task in data["tasks"]},
        "asset_status": {task["asset_status"] for task in data["tasks"]},
        "support_status": {task["support_status"] for task in data["tasks"]},
        "blocker_codes": {
            code for task in data["tasks"] for code in task["blocker_codes"]
        },
    }
    for dimension, values in dimensions.items():
        assert set(labels["values"][dimension]) == values
        assert all(
            set(label) == {"en", "zh-CN"} and all(label.values())
            for label in labels["values"][dimension].values()
        )


def test_verified_vm_image_is_absent_from_public_blockers() -> None:
    """已完成可重现物化的镜像不得继续显示为公开 blocker。

    输入参数：
        无；从当前 release/runtime-support 生成公开站点数据。
    输出返回值：
        无；233 项均不再带全局镜像 blocker，公开标签投影也不保留
        已失效值；受审阅的静态标签闭集仍保留历史代码释义。
    """

    blocker_code = GLOBAL_IMAGE_BLOCKER
    data = build_site_data(REPO_ROOT)

    assert sum(blocker_code in task["blocker_codes"] for task in data["tasks"]) == 0
    assert blocker_code not in data["summary"]["blocker_code_counts"]
    assert blocker_code not in data["labels"]["values"]["blocker_codes"]
    assert VALUE_LABELS["blocker_codes"][blocker_code] == {
        "en": (
            "Verified VM image recipe is awaiting a reproducible "
            "materialization receipt."
        ),
        "zh-CN": "已验证的虚拟机镜像 recipe 正等待可重现物化回执。",
    }


def test_osworld_chrome_state_protocols_have_reviewed_public_labels() -> None:
    """两个 Chrome 状态协议必须分开展示，且不得误报 live。

    输入参数：
        无；从当前 runtime-support 安全投影生成站点数据。
    输出返回值：
        无；断言 profile 与 active-tab 各自使用受审阅的双语标签，
        两个任务仍是 ``blocked``，全量 live 计数仍为 0。
    """

    profile_protocol = "paraguibench.osworld.chrome-profile-name.v1"
    active_tab_protocol = "paraguibench.osworld.google-shopping-active-tab.v1"
    data = build_site_data(REPO_ROOT)
    tasks = {task["task_id"]: task for task in data["tasks"]}

    assert (
        tasks["Operation-WebOperate-Settings-001"]["evaluation_protocol"]
        == profile_protocol
    )
    assert (
        tasks["Operation-WebOperate-WebNavigate-009"]["evaluation_protocol"]
        == active_tab_protocol
    )
    assert tasks["Operation-WebOperate-Settings-001"]["support_status"] == "blocked"
    assert tasks["Operation-WebOperate-WebNavigate-009"]["support_status"] == "blocked"
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }

    protocol_labels = VALUE_LABELS["evaluation_protocol"]
    for protocol in (profile_protocol, active_tab_protocol):
        assert set(protocol_labels[protocol]) == {"en", "zh-CN"}
        assert all(protocol_labels[protocol].values())

    assert tasks["Operation-WebOperate-Settings-001"]["blocker_codes"] == [
        "versioned_live_validation_not_completed"
    ]
    assert tasks["Operation-WebOperate-WebNavigate-009"]["blocker_codes"] == [
        "versioned_live_validation_not_completed"
    ]
    blocker_labels = VALUE_LABELS["blocker_codes"]
    blocker_code = "versioned_live_validation_not_completed"
    assert set(blocker_labels[blocker_code]) == {"en", "zh-CN"}
    assert all(blocker_labels[blocker_code].values())


def test_osworld_bookmark_protocol_and_start_context_have_public_labels() -> None:
    """原生 Bookmark 协议与 Settings-003 blocker 必须有双语标签。

    输入参数：
        无；读取站点生成器的受审阅标签闭集。
    输出返回值：
        无；协议和 blocker 标签都只含完整英文与中文值。
    """

    protocol = "paraguibench.osworld.chrome-bookmarks.v1"
    blocker = "osworld_bookmark_start_context_not_migrated"

    assert VALUE_LABELS["evaluation_protocol"][protocol] == {
        "en": "OSWorld Chrome Bookmarks",
        "zh-CN": "OSWorld Chrome 书签评价",
    }
    assert VALUE_LABELS["blocker_codes"][blocker] == {
        "en": "OSWorld bookmark start context not migrated",
        "zh-CN": "OSWorld 书签任务启动上下文尚未迁移",
    }


def test_osworld_artifact_site_projection_is_native_but_fail_closed() -> None:
    """站点必须展示 15 项原生 artifact 协议与完整阻塞信息。

    输入参数：
        无；从版本化 release/runtime-support 清单生成公开投影。
    输出返回值：
        无；断言 15 项皆使用原生协议但仍 blocked，新提升 13 项
        保留 getter/gold/setup/live 门禁；13 项 strict input 与 strict
        gold 身份均闭合 legacy asset 门禁；Settings-001 使用 host-only
        v2 derived gold，需要 production finalize 的 10 项已接入。
    """

    protocol = "paraguibench.osworld.artifact-state.v1"
    data = build_site_data(REPO_ROOT)
    tasks = {
        task["task_id"]: task
        for task in data["tasks"]
        if task["evaluation_protocol"] == protocol
    }
    established_task_ids = {
        "Operation-FileOperate-BatchOperation-001",
        "Operation-FileOperate-CombinationDocs-015",
    }
    promoted_task_ids = set(tasks) - established_task_ids
    strict_input_asset_task_ids = {
        "Operation-FileOperate-BatchOperation-003",
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
        "Operation-FileOperate-Settings-001",
        "Operation-WebOperate-SearchAndWrite-001",
    }
    common_blockers = {
        "legacy_asset_manifest_not_migrated",
        "osworld_artifact_getter_live_validation_not_completed",
        "osworld_artifact_gold_live_validation_not_completed",
        "osworld_task_setup_live_validation_not_completed",
        "versioned_live_validation_not_completed",
    }
    finalize_action_task_ids = {
        task_id
        for task_id, prepare_spec in (ARTIFACT_FAMILY_TASK_PREPARE_SPECS.items())
        if prepare_spec.finalize_action_id != "none"
    }
    canonical_tasks = {
        task_id: json.loads(
            (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )
        for task_id in promoted_task_ids
    }
    capabilities = {
        task_id: inspect_artifact_family_task_prepare_capability(
            repo_root=REPO_ROOT,
            task=canonical_tasks[task_id],
        )
        for task_id in promoted_task_ids
    }
    assert all(capabilities.values())
    capability_blocker_projection = (
        (
            ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
            "osworld_source_start_context_ambiguous",
        ),
        (
            ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
            "osworld_artifact_input_path_inferred",
        ),
        (
            ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
            "osworld_artifact_input_license_unverified",
        ),
    )

    assert len(tasks) == 15
    assert len(promoted_task_ids) == 13
    assert len(finalize_action_task_ids) == 10
    assert finalize_action_task_ids == set(OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS)
    assert all(task["support_status"] == "blocked" for task in tasks.values())
    for task_id in promoted_task_ids:
        expected_blockers = set(common_blockers)
        if task_id in strict_input_asset_task_ids:
            expected_blockers.remove("legacy_asset_manifest_not_migrated")
        capability = capabilities[task_id]
        assert capability is not None
        for internal_code, public_code in capability_blocker_projection:
            if internal_code in capability.blocker_ids:
                expected_blockers.add(public_code)
        assert set(tasks[task_id]["blocker_codes"]) == expected_blockers
        assert (
            "osworld_artifact_finalize_not_migrated"
            not in tasks[task_id]["blocker_codes"]
        )

    assert data["summary"]["evaluation_protocol_counts"][protocol] == 15
    assert (
        "legacy.osworld.state.v1" not in data["summary"]["evaluation_protocol_counts"]
    )
    assert (
        "osworld_artifact_settings_gold_conflict_unresolved"
        not in data["summary"]["blocker_code_counts"]
    )
    assert (
        "osworld_artifact_settings_gold_conflict_unresolved"
        not in VALUE_LABELS["blocker_codes"]
    )
    assert (
        "osworld_artifact_finalize_not_migrated"
        not in data["summary"]["blocker_code_counts"]
    )
    assert VALUE_LABELS["blocker_codes"]["osworld_artifact_finalize_not_migrated"] == {
        "en": "OSWorld artifact finalize action not migrated",
        "zh-CN": "OSWorld 产物收尾动作尚未迁移",
    }
    capability_labels = {
        "osworld_source_start_context_ambiguous": {
            "en": "OSWorld source start context is ambiguous",
            "zh-CN": "OSWorld 来源任务启动上下文存在歧义",
        },
        "osworld_artifact_input_path_inferred": {
            "en": "OSWorld artifact input location is inferred",
            "zh-CN": "OSWorld 产物输入位置仅为推断",
        },
        "osworld_artifact_input_license_unverified": {
            "en": "OSWorld artifact input license is unverified",
            "zh-CN": "OSWorld 产物输入许可尚未核验",
        },
    }
    for blocker_code in capability_labels:
        assert data["summary"]["blocker_code_counts"].get(blocker_code, 0) == 0
        assert blocker_code not in data["labels"]["values"]["blocker_codes"]
        assert (
            VALUE_LABELS["blocker_codes"][blocker_code]
            == (capability_labels[blocker_code])
        )


def test_operation_protocol_has_32_asset_blocked_tasks_and_public_label() -> None:
    """Operation 原生协议必须公开精确任务数、阻塞项与双语标签。

    输入参数：
        无；从当前 release/runtime-support 构造站点安全投影。
    输出返回值：
        无；精确 32 项使用原生协议；三十二项均绑定固定下载资产，
            仅保留各自尚未闭合的语义与 live blocker；全部
        保持 blocked，且标签不暴露规则参数。
    """

    protocol = "paraguibench.operation.eval-rules.v1"
    data = build_site_data(REPO_ROOT)
    tasks = [task for task in data["tasks"] if task["evaluation_protocol"] == protocol]
    pinned_asset_task_ids = {
        "Operation-FileOperate-BatchOperationExcel-001",
        "Operation-FileOperate-BatchOperationExcel-002",
        "Operation-FileOperate-BatchOperationExcel-003",
        "Operation-FileOperate-BatchOperationExcel-004",
        "Operation-FileOperate-BatchOperationExcel-005",
        "Operation-FileOperate-BatchOperationExcel-006",
        "Operation-FileOperate-BatchOperationExcel-007",
        "Operation-FileOperate-BatchOperationExcel-009",
        "Operation-FileOperate-BatchOperationPPT-001",
        "Operation-FileOperate-BatchOperationPPT-002",
        "Operation-FileOperate-BatchOperationWord-001",
        "Operation-FileOperate-BatchOperationWord-002",
        "Operation-FileOperate-BatchOperationWord-003",
        "Operation-FileOperate-BatchOperationWord-004",
        "Operation-FileOperate-BatchOperationWord-005",
        "Operation-FileOperate-BatchOperationWord-006",
        "Operation-FileOperate-BatchOperationWord-007",
        "Operation-FileOperate-BatchOperationWord-008",
        "Operation-FileOperate-BatchOperationWord-009",
        "Operation-FileOperate-BatchOperationWord-010",
        "Operation-FileOperate-BatchOperationWord-011",
        "Operation-FileOperate-BatchOperationWord-012",
        "Operation-FileOperate-CombinationDocs-001",
        "Operation-FileOperate-CombinationDocs-003",
        "Operation-FileOperate-CombinationDocs-005",
        "Operation-FileOperate-CombinationDocs-006",
        "Operation-FileOperate-CombinationDocs-007",
        "Operation-FileOperate-CombinationDocs-008",
        "Operation-FileOperate-SearchAndWrite-002",
        "Operation-FileOperate-SearchAndWrite-004",
        "Operation-FileOperate-SearchAndWrite-006",
        "Operation-FileOperate-SearchAndWrite-007",
    }
    pinned_asset_tasks = [
        task for task in tasks if task["task_id"] in pinned_asset_task_ids
    ]
    legacy_asset_tasks = [
        task for task in tasks if task["task_id"] not in pinned_asset_task_ids
    ]

    assert len(tasks) == 32
    assert all(task["support_status"] == "blocked" for task in tasks)
    assert {task["task_id"] for task in pinned_asset_tasks} == pinned_asset_task_ids
    for task in pinned_asset_tasks:
        expected_blockers = ["versioned_live_validation_not_completed"]
        if task["task_id"] in {
            "Operation-FileOperate-BatchOperationWord-009",
            "Operation-FileOperate-BatchOperationWord-010",
        }:
            expected_blockers.append(
                "operation_word009_010_writer_live_validation_not_completed"
            )
        if task["task_id"] == "Operation-FileOperate-CombinationDocs-003":
            expected_blockers.append(
                "combinationdocs003_real_render_validation_not_completed"
            )
        assert task["blocker_codes"] == sorted(expected_blockers)
        assert task["asset_status"] == "pinned_download_manifest"
    assert len(pinned_asset_tasks) == 32
    assert legacy_asset_tasks == []
    assert VALUE_LABELS["evaluation_protocol"][protocol] == {
        "en": "ParaGUIBench Operation Artifact Rules",
        "zh-CN": "ParaGUIBench 操作产物规则评价",
    }
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }
    assert (
        data["summary"]["blocker_code_counts"][
            "operation_word009_010_writer_live_validation_not_completed"
        ]
        == 2
    )
    assert (
        "operation_word012_abbreviation_semantics_not_migrated"
        not in data["summary"]["blocker_code_counts"]
    )
    assert (
        data["summary"]["blocker_code_counts"][
            "combinationdocs003_real_render_validation_not_completed"
        ]
        == 1
    )
    assert VALUE_LABELS["blocker_codes"][
        "operation_word009_010_writer_live_validation_not_completed"
    ] == {
        "en": "Word-009/010 Writer live validation not completed",
        "zh-CN": "Word-009/010 Writer 真实环境验证尚未完成",
    }
    assert VALUE_LABELS["blocker_codes"][
        "combinationdocs003_real_render_validation_not_completed"
    ] == {
        "en": "CombinationDocs-003 real render validation not completed",
        "zh-CN": "CombinationDocs-003 真实渲染验证尚未完成",
    }


def test_webmall_v2_site_projection_exposes_only_versioned_live_gate() -> None:
    """站点数据必须精确投影 WebMall v2 当前版本化 live 阻塞。

    输入参数：
        无；从两个版本化清单构造完整站点公开数据。
    输出返回值：
        无；断言 16 个 Checkout/EndToEnd 任务仍为 blocked、
        全局镜像 blocker 已消失且 live 计数仍为零，四个已完成
        组件 blocker 也不再公开，
        URL-multiset 迁移的独立 67/166 不漂移门禁由下一测试负责。
    """

    data = build_site_data(REPO_ROOT)
    target_protocols = {
        "paraguibench.webmall.checkout.closed-world.v2",
        "paraguibench.webmall.find-and-order.closed-world.v2",
    }
    target_tasks = [
        task
        for task in data["tasks"]
        if task["evaluation_protocol"] in target_protocols
    ]
    obsolete_codes = {
        "webmall_privileged_order_source_not_integrated",
        "webmall_distributed_lease_not_integrated",
        "webmall_environment_manifest_not_integrated",
        "webmall_cli_runtime_binding_not_integrated",
    }

    assert len(target_tasks) == 16
    assert all(task["support_status"] == "blocked" for task in target_tasks)
    assert all(
        task["blocker_codes"] == ["versioned_live_validation_not_completed"]
        for task in target_tasks
    )
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }
    assert not (obsolete_codes & set(data["summary"]["blocker_code_counts"]))
    assert not (obsolete_codes & set(data["labels"]["values"]["blocker_codes"]))
    assert not (obsolete_codes & set(VALUE_LABELS["blocker_codes"]))


def test_webmall_url_multiset_site_projection_changes_exactly_67_tasks() -> None:
    """站点必须公开 67 条 URL 协议且只授权双层状态更新。

    输入参数：
        无；从当前 release/runtime-support 构造站点安全投影。
        输出返回值：
                无；精确 67 条任务使用 URL-multiset v1 并保留 live
            blocker；排除 URL 与另行授权的 8 条 Cart 后，158 条公开
            语义摘要与已包含四项 pipeline-implicit 及 15 项
            artifact-state 原生升级、ReadonlyWord-003 与三十二项
                Operation FileOperate 固定资产接线的当前基线一致；本轮只
                    授权 ReadonlyPPT-002/-003、Excel-008、CombinationDocs-002
                    、SearchWrite-008 local capability/typed blocker 移除与
                    Word-012 的本地闭合投影，以及 Settings-001 严格
                    derived-gold 本地语义闭合与 PPT-003 最终 797
                    component receipt；其余公开语义无漂移。
    """

    protocol = "paraguibench.webmall.url-multiset.v1"
    data = build_site_data(REPO_ROOT)
    target_tasks = [
        task for task in data["tasks"] if task["evaluation_protocol"] == protocol
    ]
    unchanged_tasks = [
        task
        for task in data["tasks"]
        if task["evaluation_protocol"]
        not in {
            protocol,
            "paraguibench.webmall.cart.closed-world.v1",
        }
    ]
    stable_projection = json.dumps(
        unchanged_tasks,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(target_tasks) == 67
    assert all(task["support_status"] == "blocked" for task in target_tasks)
    assert all(
        task["blocker_codes"] == ["versioned_live_validation_not_completed"]
        for task in target_tasks
    )
    assert len(unchanged_tasks) == 158
    assert hashlib.sha256(stable_projection).hexdigest() == (
        "66310d0488f88387c03345be9af543f8a65947b42da3c54ec4001e23281c32f5"
    )
    assert VALUE_LABELS["evaluation_protocol"][protocol] == {
        "en": "WebMall URL Multiset",
        "zh-CN": "WebMall URL 多集合评价",
    }
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }


def test_webmall_cart_site_projection_is_native_but_not_live_ready() -> None:
    """站点必须区分 8 项 Cart evaluator-ready 与 live-ready 状态。

    输入参数：
        无；从固定 release 与 runtime-support 构造公开站点数据。
    输出返回值：
        无；8 项展示原生 Cart 协议但仍全部 blocked，reader reference
        和版本化运行门禁均被保留，live_validated 总数为零。
    """

    protocol = "paraguibench.webmall.cart.closed-world.v1"
    reader_blocker = "webmall_cart_reader_reference_live_validation_not_completed"
    data = build_site_data(REPO_ROOT)
    target_tasks = [
        task for task in data["tasks"] if task["evaluation_protocol"] == protocol
    ]

    assert len(target_tasks) == 8
    assert all(task["support_status"] == "blocked" for task in target_tasks)
    assert all(
        set(task["blocker_codes"])
        == {
            reader_blocker,
            "versioned_live_validation_not_completed",
        }
        for task in target_tasks
    )
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }
    assert data["summary"]["blocker_code_counts"][reader_blocker] == 8
    assert VALUE_LABELS["evaluation_protocol"][protocol] == {
        "en": "WebMall Cart Closed World",
        "zh-CN": "WebMall 购物车闭集评价",
    }
    assert VALUE_LABELS["blocker_codes"][reader_blocker] == {
        "en": "WebMall cart reader reference live validation not completed",
        "zh-CN": "WebMall 购物车读取器尚未完成参考环境真实验证",
    }


def test_pipeline_implicit_site_projection_is_native_but_fail_closed() -> None:
    """站点必须公开四项原生协议与各自真实未闭环门禁。

    输入参数：无；从 release/runtime-support 生成公开投影。
    输出返回值：
        无；四项 pipeline-implicit 任务隐藏已完成的本地组件门禁，
        但仍保持 blocked，并同时保留 pipeline-live 与 versioned-live。
    """

    expected_protocols = {
        "Operation-FileOperate-BatchOperationExcel-008": (
            "paraguibench.operation.xlsx.hide-na-rows.v1"
        ),
        "Operation-FileOperate-BatchOperationPPT-003": (
            "paraguibench.operation.image-classification.sha256.v1"
        ),
        "Operation-FileOperate-CombinationDocs-002": (
            "paraguibench.operation.cross-document-facts.v1"
        ),
        "Operation-FileOperate-SearchAndWrite-008": (
            "paraguibench.operation.searchwrite-xlsx.v1"
        ),
    }
    data = build_site_data(REPO_ROOT)
    tasks = {task["task_id"]: task for task in data["tasks"]}
    for task_id, protocol in expected_protocols.items():
        task = tasks[task_id]
        expected_blockers = {
            "pipeline_implicit_live_validation_not_completed",
            "versioned_live_validation_not_completed",
        }
        assert task["evaluation_protocol"] == protocol
        assert task["support_status"] == "blocked"
        assert set(task["blocker_codes"]) == expected_blockers
        assert protocol in VALUE_LABELS["evaluation_protocol"]
    for blocker in (
        "pipeline_implicit_input_asset_metadata_unverified",
        "pipeline_implicit_gold_asset_metadata_unverified",
        "pipeline_implicit_live_validation_not_completed",
        "pipeline_implicit_combination_gold_conflict_unresolved",
    ):
        assert blocker in VALUE_LABELS["blocker_codes"]
    assert data["summary"]["support_status_counts"] == {
        "blocked": 233,
        "live_validated": 0,
    }


def test_cli_writes_deterministic_data_and_check_detects_output_drift(
    tmp_path: Path,
) -> None:
    """CLI 必须稳定落盘，并由 --check 拒绝被修改的派生文件。"""

    output_path = tmp_path / "site-data.json"
    generate = _run_generator("--output", str(output_path))

    assert generate.returncode == 0, generate.stderr
    first_bytes = output_path.read_bytes()
    data = json.loads(first_bytes)
    assert "generated_at" not in data
    assert data["input_manifests"] == {
        "release": {
            "id": "release-v1",
            "sha256": _sha256(REPO_ROOT / "benchmark/manifests/release-v1.json"),
            "task_count": 233,
        },
        "runtime_support": {
            "id": "runtime-support-v1",
            "sha256": _sha256(
                REPO_ROOT / "benchmark/manifests/runtime-support-v1.json"
            ),
            "task_count": 233,
        },
    }

    regenerate = _run_generator("--output", str(output_path))
    assert regenerate.returncode == 0, regenerate.stderr
    assert output_path.read_bytes() == first_bytes
    assert (
        _run_generator(
            "--output",
            str(output_path),
            "--check",
        ).returncode
        == 0
    )

    output_path.write_text("{}\n", encoding="utf-8")
    stale_bytes = output_path.read_bytes()
    stale_check = _run_generator(
        "--output",
        str(output_path),
        "--check",
    )
    assert stale_check.returncode != 0
    assert output_path.read_bytes() == stale_bytes


def test_check_rejects_canonical_source_drift_before_projection(
    tmp_path: Path,
) -> None:
    """canonical 文件偏离 release 摘要时必须作为来源错误拒绝且不回显内容。"""

    isolated_root = tmp_path / "isolated-repo"
    _copy_site_sources(isolated_root)
    output_path = isolated_root / "website/public/data/site-data.json"
    generate = _run_generator_for_root(
        isolated_root,
        "--output",
        str(output_path),
    )
    assert generate.returncode == 0, generate.stderr
    original_output = output_path.read_bytes()

    task_path = (
        isolated_root
        / "benchmark/tasks/InformationRetrieval-FileSearch-Readonly-001.json"
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    sentinel = "PRIVATE_SOURCE_DRIFT_SENTINEL"
    task["task_source"] = sentinel
    task_path.write_text(
        json.dumps(task, ensure_ascii=False),
        encoding="utf-8",
    )

    check = _run_generator_for_root(
        isolated_root,
        "--output",
        str(output_path),
        "--check",
    )
    assert check.returncode == 2
    assert sentinel not in f"{check.stdout}\n{check.stderr}"
    assert output_path.read_bytes() == original_output


def test_generator_rejects_sensitive_metadata_even_with_updated_hashes(
    tmp_path: Path,
) -> None:
    """同步更新摘要也不能把 URL 伪装成公开 source 写入页面数据。"""

    isolated_root = tmp_path / "isolated-repo"
    _copy_site_sources(isolated_root)
    task_path = (
        isolated_root
        / "benchmark/tasks/InformationRetrieval-FileSearch-Readonly-001.json"
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    sensitive_value = "https://" + "private.example.invalid/resource"
    task["task_source"] = sensitive_value
    task_path.write_text(
        json.dumps(task, ensure_ascii=False),
        encoding="utf-8",
    )

    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["tasks"][0]["sha256"] = _sha256(task_path)
    release_path.write_text(
        json.dumps(release, ensure_ascii=False),
        encoding="utf-8",
    )
    runtime_path = isolated_root / "benchmark/manifests/runtime-support-v1.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["release_manifest_sha256"] = _sha256(release_path)
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False),
        encoding="utf-8",
    )

    output_path = isolated_root / "website/public/data/site-data.json"
    generate = _run_generator_for_root(
        isolated_root,
        "--output",
        str(output_path),
    )
    assert generate.returncode == 2
    assert sensitive_value not in f"{generate.stdout}\n{generate.stderr}"
    assert not output_path.exists()


def _collect_keys(value: object) -> set[str]:
    """递归收集 JSON 结构的全部 object 字段名。

    输入参数：
        value：任意可序列化 JSON 结构。
    输出返回值：
        所有层级 object key 的集合。
    """

    if isinstance(value, dict):
        return set(value) | {
            key for child in value.values() for key in _collect_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _collect_keys(child)}
    return set()


def _run_generator(*arguments: str) -> subprocess.CompletedProcess[str]:
    """在真实 CLI 边界运行站点数据生成器。

    输入参数：
        arguments：传给生成脚本的附加命令行参数。
    输出返回值：
        捕获标准输出与错误输出的子进程结果。
    """

    return _run_generator_for_root(REPO_ROOT, *arguments)


def _run_generator_for_root(
    repo_root: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    """针对指定仓库根目录运行真实生成器 CLI。

    输入参数：
        repo_root：包含最小 benchmark 来源的仓库根目录。
        arguments：传给生成脚本的附加命令行参数。
    输出返回值：
        捕获标准输出与错误输出的子进程结果。
    """

    return subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--repo-root",
            str(repo_root),
            *arguments,
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )


def _copy_site_sources(destination: Path) -> None:
    """复制站点生成器允许读取的三类输入到隔离测试仓库。

    输入参数：
        destination：待创建的隔离仓库根目录。
    输出返回值：
        无；复制 release、runtime-support 与 canonical tasks。
    """

    manifest_root = destination / "benchmark/manifests"
    manifest_root.mkdir(parents=True)
    for name in ("release-v1.json", "runtime-support-v1.json"):
        shutil.copyfile(
            REPO_ROOT / "benchmark/manifests" / name,
            manifest_root / name,
        )
    shutil.copytree(
        REPO_ROOT / "benchmark/tasks",
        destination / "benchmark/tasks",
    )


def _sha256(path: Path) -> str:
    """计算测试输入文件的 SHA-256。

    输入参数：
        path：待读取文件。
    输出返回值：
        小写十六进制 SHA-256。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()
