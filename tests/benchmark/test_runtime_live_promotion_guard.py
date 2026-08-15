"""runtime-support live 晋升门禁的 fail-closed 回归测试。"""

from __future__ import annotations

import importlib.util
import hashlib
import inspect
import json
import math
from collections import Counter
from pathlib import Path
import shutil
import sys
from types import ModuleType
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT_TOOL_PATH = REPO_ROOT / "scripts/benchmark/runtime_support_manifest.py"
GLOBAL_IMAGE_BLOCKER = "osworld_vm_image_materialization_unverified"
VERSIONED_LIVE_BLOCKER = "versioned_live_validation_not_completed"
PIPELINE_COMPONENT_BLOCKER = "pipeline_implicit_live_validation_not_completed"
CART_COMPONENT_BLOCKER = "webmall_cart_reader_reference_live_validation_not_completed"
WORD_TEXT_FIDELITY_BLOCKER = (
    "operation_word009_010_writer_live_validation_not_completed"
)
WORD_ABBREVIATION_BLOCKER = "operation_word012_abbreviation_semantics_not_migrated"
COMBINATIONDOCS003_RENDER_BLOCKER = (
    "combinationdocs003_real_render_validation_not_completed"
)
SUPPORT_SCHEMA_PATH = REPO_ROOT / "benchmark/schemas/runtime-support-v1.schema.json"


def _load_support_tool() -> ModuleType:
    """从独立脚本路径加载 runtime-support 生成器。

    输入参数：
        无；脚本路径由当前仓库根目录确定。
    输出返回值：
        可直接调用内部投影与 receipt 校验函数的模块。
    """

    spec = importlib.util.spec_from_file_location(
        "paraguibench_runtime_live_promotion_tool",
        SUPPORT_TOOL_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 runtime-support 生成器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_canonical_task(task_id: str) -> dict[str, object]:
    """读取一个仓库内 canonical task 供窄门禁测试使用。

    输入参数：
        task_id：待读取的稳定 canonical task ID。
    输出返回值：
        解码后的 task JSON object。
    """

    return json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )


def _valid_receipt(
    task_id: str = "synthetic-task",
) -> dict[str, object]:
    """构造一份不含任务正文或 Agent 文本的最小合法 receipt。

    输入参数：
        task_id：receipt 必须绑定的合成任务身份。
    输出返回值：
        仅包含稳定身份、终态、有限分数、六字段版本向量与
        promotion-safe component revision 的 JSON object。
    """

    revision = "tree-sha256:" + "1" * 64
    return {
        "schema_version": "2.0",
        "task_id": task_id,
        "run_id": "run-live-001",
        "attempt_id": "attempt-001",
        "execution_outcome": "SUCCEEDED",
        "evaluation_outcome": "PASSED",
        "score": 1.0,
        "version_vector": {
            "source_revision": revision,
            "agent_code_revision": revision,
            "evaluator_revision": revision,
            "evaluation_protocol": "paraguibench.answer.exact.v1",
            "environment_protocol": "osworld.desktop.v1",
            "environment_revision": "manifest-sha256:" + "2" * 64,
        },
        "promotion_component_revision": "component-sha256:" + "3" * 64,
    }


def _write_allowlisted_receipt(
    root: Path,
    support_tool: ModuleType,
    receipt: dict[str, object],
) -> None:
    """把一份稳定 JSON receipt 写入合成仓库并固定字节摘要。

    输入参数：
        root：pytest 隔离仓库根。
        support_tool：已加载的 runtime-support 生成器。
        receipt：待严格序列化的合成 receipt。
    输出返回值：
        无；写入目标文件及独立 allowlist 数据，使 task→SHA
        精确匹配。
    """

    task_id = receipt["task_id"]
    assert isinstance(task_id, str)
    receipt_root = root / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
    receipt_root.mkdir(parents=True)
    payload = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    (receipt_root / f"{task_id}.json").write_bytes(payload)
    _write_receipt_allowlist(
        root,
        support_tool,
        {task_id: hashlib.sha256(payload).hexdigest()},
    )


def _write_receipt_allowlist(
    root: Path,
    support_tool: ModuleType,
    receipts: dict[str, str],
) -> None:
    """写入一份字段闭合的合成 receipt SHA allowlist。

    输入参数：
        root：pytest 隔离仓库根。
        support_tool：提供正式 allowlist 固定相对路径。
        receipts：待固定的 task→receipt 完整 SHA-256 字典。
    输出返回值：
        无；目标父目录与 schema-1 数据文件已写入。
    """

    allowlist_path = root / support_tool.LIVE_VALIDATION_RECEIPT_ALLOWLIST_PATH
    allowlist_path.parent.mkdir(parents=True, exist_ok=True)
    allowlist_path.write_text(
        json.dumps(
            {"schema_version": 1, "receipts": receipts},
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def support_tool() -> ModuleType:
    """为本文件所有用例复用一个已加载生成器模块。

    输入参数：
        无；由 pytest 管理模块级生命周期。
    输出返回值：
        runtime-support 生成器模块。
    """

    return _load_support_tool()


def test_bare_versioned_live_task_allowlist_is_retired(
    support_tool: ModuleType,
) -> None:
    """裸 task-ID allowlist 不得再成为 live 晋升路径。

    输入参数：
        support_tool：已加载的 runtime-support 生成器。
    输出返回值：
        无；旧常量消失且投影源码不再引用它。
    """

    assert not hasattr(support_tool, "VERSIONED_LIVE_VALIDATED_TASK_IDS")
    source = inspect.getsource(support_tool._build_task_entry)
    assert "VERSIONED_LIVE_VALIDATED_TASK_IDS" not in source


def test_receipt_sha_allowlist_is_external_closed_data_without_revision_cycle(
    support_tool: ModuleType,
) -> None:
    """Receipt SHA allowlist 必须是与 guard 代码分离的闭集数据。

    输入参数：
        support_tool：提供 allowlist 解析与组件闭包构造器。
    输出返回值：
        无；代码中不再嵌入 task→SHA 值，初始数据闭集为空，
        且 allowlist 数据本身不进入 receipt 声明的 component
        revision，从而避免 receipt SHA 的自引用循环。
    """

    assert not hasattr(
        support_tool,
        "TRUSTED_LIVE_VALIDATION_RECEIPT_SHA256_BY_TASK_ID",
    )
    release = json.loads(
        (REPO_ROOT / "benchmark/manifests/release-v1.json").read_text(encoding="utf-8")
    )
    canonical_task_ids = frozenset(item["task_id"] for item in release["tasks"])
    assert (
        support_tool._load_trusted_live_validation_receipt_allowlist(
            REPO_ROOT,
            canonical_task_ids=canonical_task_ids,
        )
        == {}
    )

    task = _load_canonical_task("Operation-OnlineShopping-AddToCart-001")
    component_paths = support_tool._collect_promotion_component_paths(
        REPO_ROOT,
        task=task,
        environment_protocol="webmall.browser.v1",
    )
    assert support_tool.LIVE_VALIDATION_RECEIPT_ALLOWLIST_PATH not in component_paths


def test_receipt_allowlist_rejects_noncanonical_task_identity(
    support_tool: ModuleType,
    tmp_path: Path,
) -> None:
    """Build 使用的 receipt allowlist 不得含非 canonical task。

    输入参数：
        support_tool：提供 allowlist 闭集 loader。
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；即使 task ID 形状合法，只要不属于当前 release
        闭集也必须失败关闭。
    """

    _write_receipt_allowlist(
        tmp_path,
        support_tool,
        {"synthetic-task": "1" * 64},
    )

    with pytest.raises(
        support_tool.RuntimeSupportError, match="non-canonical|非 canonical"
    ):
        support_tool._load_trusted_live_validation_receipt_allowlist(
            tmp_path,
            canonical_task_ids=frozenset({"canonical-task"}),
        )


@pytest.mark.parametrize(
    ("image_ready", "receipt_ready", "expected_status", "expected_blockers"),
    [
        (False, False, "blocked", [GLOBAL_IMAGE_BLOCKER, VERSIONED_LIVE_BLOCKER]),
        (False, True, "blocked", [GLOBAL_IMAGE_BLOCKER, VERSIONED_LIVE_BLOCKER]),
        (True, False, "blocked", [VERSIONED_LIVE_BLOCKER]),
        (True, True, "live_validated", []),
    ],
)
def test_clean_task_live_promotion_requires_all_three_factors(
    support_tool: ModuleType,
    image_ready: bool,
    receipt_ready: bool,
    expected_status: str,
    expected_blockers: list[str],
) -> None:
    """无组件 blocker 的任务仍需镜像与可信 receipt 同时成立。

    输入参数：
        support_tool：已加载的生成器。
        image_ready：合成镜像门禁状态。
        receipt_ready：合成可信 receipt 状态。
        expected_status：预期支持状态。
        expected_blockers：预期稳定 blocker 顺序。
    输出返回值：
        无；三因子真值表与投影结果一致。
    """

    task = _load_canonical_task("InformationRetrieval-WebSearch-ConditionalSearch-001")
    with patch.object(
        support_tool,
        "_has_trusted_live_validation_receipt",
        return_value=receipt_ready,
    ):
        entry = support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=image_ready,
        )

    assert entry["support_status"] == expected_status
    assert entry["blocker_codes"] == expected_blockers
    if expected_status == "live_validated":
        assert entry["support_reason_code"] == "live_validation_passed"


@pytest.mark.parametrize(
    (
        "image_ready",
        "component_ready",
        "task_receipt_ready",
        "expected_status",
        "expected_blockers",
    ),
    [
        (
            False,
            False,
            False,
            "blocked",
            [GLOBAL_IMAGE_BLOCKER, CART_COMPONENT_BLOCKER, VERSIONED_LIVE_BLOCKER],
        ),
        (
            False,
            True,
            False,
            "blocked",
            [GLOBAL_IMAGE_BLOCKER, VERSIONED_LIVE_BLOCKER],
        ),
        (
            True,
            False,
            True,
            "blocked",
            [CART_COMPONENT_BLOCKER, VERSIONED_LIVE_BLOCKER],
        ),
        (
            True,
            True,
            False,
            "blocked",
            [VERSIONED_LIVE_BLOCKER],
        ),
        (True, True, True, "live_validated", []),
    ],
)
def test_cart_live_promotion_keeps_component_and_task_receipts_distinct(
    support_tool: ModuleType,
    image_ready: bool,
    component_ready: bool,
    task_receipt_ready: bool,
    expected_status: str,
    expected_blockers: list[str],
) -> None:
    """验证 Cart 镜像、共享 component receipt 与单任务 receipt 三因子互不代替。

    输入参数：support_tool 为生成器；image/component/task_receipt
        为三个独立活性事实；expected_status/blockers 为真值表投影。
    输出返回值：无；component proof 只清 reader blocker，通用
        RunStore-v2 task receipt 只清 versioned blocker，且本地就绪度始终不变。
    """

    task = _load_canonical_task("Operation-OnlineShopping-AddToCart-001")
    with patch.object(
        support_tool,
        "_has_trusted_live_validation_receipt",
        return_value=task_receipt_ready,
    ) as task_receipt_loader:
        entry = support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=image_ready,
            webmall_cart_component_ready=component_ready,
        )

    assert entry["support_status"] == expected_status
    assert entry["blocker_codes"] == expected_blockers
    assert entry["local_readiness_status"] == "local_ready"
    assert task_receipt_loader.called is (image_ready and component_ready)


def test_build_snapshots_component_receipt_once_for_exact_eight_cart_tasks(
    support_tool: ModuleType,
) -> None:
    """验证生成器只读一次共享 component attestation 并投影到精确八任务。

    输入参数：support_tool 为动态加载的 runtime-support 生成器。
    输出返回值：无；单次有效快照只清八个 Cart reader blocker；已验证镜像
        不再产生 blocker，component receipt 不替代单任务 versioned receipt，
        也不改变本地计数。
    """

    with patch.object(
        support_tool,
        "has_current_webmall_cart_component_receipt",
        return_value=True,
    ) as component_loader:
        manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)

    cart_entries = [
        entry
        for entry in manifest["tasks"]
        if entry["evaluation_protocol"] == "paraguibench.webmall.cart.closed-world.v1"
    ]
    assert component_loader.call_count == 1
    assert len(cart_entries) == 8
    assert {entry["task_id"] for entry in cart_entries} == set(
        support_tool.NATIVE_WEBMALL_CART_TASK_IDS
    )
    for entry in cart_entries:
        assert CART_COMPONENT_BLOCKER not in entry["blocker_codes"]
        assert entry["blocker_codes"] == [VERSIONED_LIVE_BLOCKER]
        assert entry["support_status"] == "blocked"
        assert entry["local_readiness_status"] == "local_ready"
    assert manifest["local_readiness_status_counts"] == {
        "local_components_incomplete": 0,
        "local_ready": 233,
    }


@pytest.mark.parametrize(
    (
        "image_ready",
        "component_ready",
        "task_receipt_ready",
        "expected_status",
        "expected_blockers",
    ),
    [
        (
            False,
            False,
            True,
            "blocked",
            [
                GLOBAL_IMAGE_BLOCKER,
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
                VERSIONED_LIVE_BLOCKER,
            ],
        ),
        (
            False,
            True,
            True,
            "blocked",
            [GLOBAL_IMAGE_BLOCKER, VERSIONED_LIVE_BLOCKER],
        ),
        (
            True,
            False,
            True,
            "blocked",
            [
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
                VERSIONED_LIVE_BLOCKER,
            ],
        ),
        (
            True,
            True,
            False,
            "blocked",
            [VERSIONED_LIVE_BLOCKER],
        ),
        (True, True, True, "live_validated", []),
    ],
)
def test_osworld_artifact_component_and_task_receipts_are_independent(
    support_tool: ModuleType,
    image_ready: bool,
    component_ready: bool,
    task_receipt_ready: bool,
    expected_status: str,
    expected_blockers: list[str],
) -> None:
    """确认 G/D/S receipt 只清三项组件，不代替镜像或通用任务 receipt。

    输入参数：support_tool 为生成器；image/component/task_receipt
        为三个独立事实；expected_status/blockers 为真值表结果。
    输出返回值：component 有效时只移除 getter/gold/setup；镜像与
        versioned task receipt 仍分别决定全局镜像 blocker 和最终晋升。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    task = _load_canonical_task(task_id)
    component_tasks = frozenset({task_id}) if component_ready else frozenset()
    with patch.object(
        support_tool,
        "_has_trusted_live_validation_receipt",
        return_value=task_receipt_ready,
    ) as generic_loader:
        entry = support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=image_ready,
            osworld_artifact_component_ready_task_ids=component_tasks,
        )

    assert entry["support_status"] == expected_status
    assert entry["blocker_codes"] == expected_blockers
    assert entry["local_readiness_status"] == "local_ready"
    assert generic_loader.called is (image_ready and component_ready)


def test_build_snapshots_osworld_component_allowlist_once_and_only_clears_gds(
    support_tool: ModuleType,
) -> None:
    """确认生成器只加载一次专属 allowlist 且只投影到对应任务。

    输入参数：support_tool 为动态加载的正式生成器。
    输出返回值：单任务 receipt 只清该任务 getter/gold/setup；已验证镜像
        不再产生 blocker，其他 artifact 任务与 versioned 门禁均不变，且
        全仓本地就绪计数保持 233/0。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    with patch.object(
        support_tool,
        "load_trusted_osworld_artifact_component_receipts",
        return_value=frozenset({task_id}),
    ) as component_loader:
        manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)

    assert component_loader.call_count == 1
    target = next(item for item in manifest["tasks"] if item["task_id"] == task_id)
    other = next(
        item
        for item in manifest["tasks"]
        if item["task_id"] == "Operation-FileOperate-CombinationDocs-009"
    )
    assert target["blocker_codes"] == [VERSIONED_LIVE_BLOCKER]
    assert other["blocker_codes"] == [
        "osworld_artifact_getter_live_validation_not_completed",
        "osworld_artifact_gold_live_validation_not_completed",
        "osworld_task_setup_live_validation_not_completed",
        VERSIONED_LIVE_BLOCKER,
    ]
    assert manifest["local_readiness_status_counts"] == {
        "local_components_incomplete": 0,
        "local_ready": 233,
    }


def test_checked_in_ppt003_receipt_matches_committed_runtime_bytes(
    support_tool: ModuleType,
) -> None:
    """确认过期 PPT-003 receipt 不阻断普通 runtime-support 生成。

    输入参数：support_tool 为正式生成器。
    输出返回值：builder 把四项 pipeline 任务都保留 pipeline-live，
        序列化字节与正式 runtime-support-v1.json 精确一致。
    """

    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    target = next(
        entry
        for entry in manifest["tasks"]
        if entry["task_id"] == "Operation-FileOperate-BatchOperationPPT-003"
    )

    assert target["support_status"] == "blocked"
    assert target["blocker_codes"] == [
        PIPELINE_COMPONENT_BLOCKER,
        VERSIONED_LIVE_BLOCKER,
    ]
    assert Counter(entry["support_status"] for entry in manifest["tasks"]) == Counter(
        {"blocked": 233, "live_validated": 0}
    )
    assert (
        payload
        == (REPO_ROOT / "benchmark/manifests/runtime-support-v1.json").read_bytes()
    )


def test_build_snapshots_pipeline_component_allowlist_once(
    support_tool: ModuleType,
) -> None:
    """确认生成器仅加载一次 pipeline 专属 allowlist。

    输入参数：support_tool 为动态加载的正式生成器。
    输出返回值：PPT003 receipt 只清该任务 pipeline-live；已验证镜像不再产生
        blocker，Search 仅保留 pipeline-live/versioned-live，全仓本地
        就绪计数为 233/0。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    with patch.object(
        support_tool,
        "load_trusted_pipeline_implicit_component_receipts",
        return_value=frozenset({task_id}),
    ) as component_loader:
        manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)

    assert component_loader.call_count == 1
    target = next(item for item in manifest["tasks"] if item["task_id"] == task_id)
    search = next(
        item
        for item in manifest["tasks"]
        if item["task_id"] == "Operation-FileOperate-SearchAndWrite-008"
    )
    assert target["blocker_codes"] == [VERSIONED_LIVE_BLOCKER]
    assert search["blocker_codes"] == [
        PIPELINE_COMPONENT_BLOCKER,
        VERSIONED_LIVE_BLOCKER,
    ]
    assert manifest["local_readiness_status_counts"] == {
        "local_components_incomplete": 0,
        "local_ready": 233,
    }


@pytest.mark.parametrize(
    "task_id",
    (
        "Operation-FileOperate-BatchOperationPPT-003",
        "Operation-FileOperate-BatchOperationExcel-008",
        "Operation-FileOperate-CombinationDocs-002",
    ),
)
def test_pipeline_receipt_only_removes_matching_pipeline_live_blocker(
    support_tool: ModuleType,
    task_id: str,
) -> None:
    """确认三类已实现 candidate receipt 都不能清除其他本地或镜像门禁。

    输入参数：support_tool 为正式生成器；task_id 为三个
        已实现 pipeline component candidate 之一。
    输出返回值：与当前空 allowlist 投影相比，精确只少
        ``pipeline_implicit_live_validation_not_completed``。
    """

    task = _load_canonical_task(task_id)
    baseline = support_tool._build_task_entry(
        REPO_ROOT,
        task,
        image_live_run_ready=False,
    )
    promoted_component = support_tool._build_task_entry(
        REPO_ROOT,
        task,
        image_live_run_ready=False,
        pipeline_implicit_component_ready_task_ids=frozenset({task_id}),
    )

    expected = list(baseline["blocker_codes"])
    expected.remove(PIPELINE_COMPONENT_BLOCKER)
    assert promoted_component["blocker_codes"] == expected
    assert promoted_component["support_status"] == "blocked"


def test_searchwrite_cannot_enter_pipeline_component_ready_set(
    support_tool: ModuleType,
) -> None:
    """确认 SearchAndWrite-008 不能被标成 component ready。

    输入参数：support_tool 为正式生成器。
    输出返回值：把 008 注入 ready-set 时生成器失败关闭，
        评价闭集仍可独立投影该任务的 pipeline-live blocker。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-008"
    task = _load_canonical_task(task_id)
    with pytest.raises(support_tool.RuntimeSupportError):
        support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=False,
            pipeline_implicit_component_ready_task_ids=frozenset({task_id}),
        )
    baseline = support_tool._build_task_entry(
        REPO_ROOT,
        task,
        image_live_run_ready=False,
    )
    assert PIPELINE_COMPONENT_BLOCKER in baseline["blocker_codes"]


@pytest.mark.parametrize(
    (
        "image_ready",
        "component_ready",
        "generic_receipt_ready",
        "expected_status",
        "expected_blockers",
    ),
    (
        (
            False,
            False,
            False,
            "blocked",
            [GLOBAL_IMAGE_BLOCKER, PIPELINE_COMPONENT_BLOCKER, VERSIONED_LIVE_BLOCKER],
        ),
        (
            False,
            True,
            False,
            "blocked",
            [GLOBAL_IMAGE_BLOCKER, VERSIONED_LIVE_BLOCKER],
        ),
        (
            True,
            False,
            True,
            "blocked",
            [PIPELINE_COMPONENT_BLOCKER, VERSIONED_LIVE_BLOCKER],
        ),
        (True, True, False, "blocked", [VERSIONED_LIVE_BLOCKER]),
        (True, True, True, "live_validated", []),
    ),
)
def test_pipeline_component_and_generic_receipts_are_independent(
    support_tool: ModuleType,
    image_ready: bool,
    component_ready: bool,
    generic_receipt_ready: bool,
    expected_status: str,
    expected_blockers: list[str],
) -> None:
    """确认 pipeline receipt 仅清专属 live blocker。

    输入参数：image/component/generic 三项为独立事实；其余为预期投影。
    输出返回值：component 不清 image/versioned，generic 不清 component；
        仅三者全真时正式 live_validated。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    task = _load_canonical_task(task_id)
    component_tasks = frozenset({task_id}) if component_ready else frozenset()
    with patch.object(
        support_tool,
        "_has_trusted_live_validation_receipt",
        return_value=generic_receipt_ready,
    ) as generic_loader:
        entry = support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=image_ready,
            pipeline_implicit_component_ready_task_ids=component_tasks,
        )

    assert entry["support_status"] == expected_status
    assert entry["blocker_codes"] == expected_blockers
    assert entry["local_readiness_status"] == "local_ready"
    assert generic_loader.called is (image_ready and component_ready)


def test_settings_cannot_be_injected_into_component_ready_set(
    support_tool: ModuleType,
) -> None:
    """确认直接调用也不能用 12-task receipt 提升 Settings。

    输入参数：support_tool 为正式生成器；传入伪造的
        ``Settings-001`` ready 集合。
    输出返回值：在 blocker 投影前即抛 ``RuntimeSupportError``。
    """

    task = _load_canonical_task("Operation-FileOperate-Settings-001")
    with pytest.raises(support_tool.RuntimeSupportError):
        support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=False,
            osworld_artifact_component_ready_task_ids=frozenset(
                {"Operation-FileOperate-Settings-001"}
            ),
        )


def test_settings_cannot_be_injected_into_pipeline_component_ready_set(
    support_tool: ModuleType,
) -> None:
    """Settings 任务不得借用 pipeline receipt 语义。

    输入参数：support_tool 为正式生成器。
    输出返回值：伪造 Settings ready 闭集在投影前失败关闭。
    """

    task_id = "Operation-FileOperate-Settings-001"
    task = _load_canonical_task(task_id)
    with pytest.raises(support_tool.RuntimeSupportError):
        support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=False,
            pipeline_implicit_component_ready_task_ids=frozenset({task_id}),
        )


def test_clean_task_real_receipt_path_promotes_without_has_receipt_mock(
    support_tool: ModuleType,
    tmp_path: Path,
) -> None:
    """真实 allowlist→component/environment→receipt 路径必须端到端晋升。

    输入参数：
        support_tool：提供正式 task projection、revision 与 receipt loader。
        tmp_path：pytest 提供的最小隔离 promotion 仓库。
    输出返回值：
        无；不 mock ``_has_trusted_live_validation_receipt``，仅在镜像就绪、
        组件 blocker 为空，且外置 SHA allowlist 与当前两类 revision
        完全匹配时返回 ``live_validated``。
    """

    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "src" / "paraguibench", root / "src/paraguibench")
    shutil.copytree(
        REPO_ROOT / "benchmark" / "schemas",
        root / "benchmark/schemas",
    )
    task_id = "InformationRetrieval-WebSearch-ConditionalSearch-001"
    task_relative_path = Path("benchmark/tasks") / f"{task_id}.json"
    for relative_path in (
        task_relative_path,
        Path("environments/osworld/image-manifest.json"),
        Path("scripts/benchmark/runtime_support_manifest.py"),
        Path("pyproject.toml"),
    ):
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative_path, target)
    task = json.loads((root / task_relative_path).read_text(encoding="utf-8"))
    evaluation_protocol = support_tool._derive_evaluation_protocol(task)
    environment_protocol = support_tool._derive_environment_protocol(task)
    asset_status = support_tool._derive_asset_status(root, task)
    component_revision = support_tool._derive_promotion_component_revision(
        root,
        task=task,
        evaluation_protocol=evaluation_protocol,
        environment_protocol=environment_protocol,
        asset_status=asset_status,
        canonical_task_relative_path=task_relative_path,
    )
    environment_revision = support_tool._derive_current_environment_revision(
        root,
        environment_protocol=environment_protocol,
    )
    receipt = _valid_receipt(task_id)
    receipt["version_vector"]["evaluation_protocol"] = evaluation_protocol  # type: ignore[index]
    receipt["version_vector"]["environment_protocol"] = environment_protocol  # type: ignore[index]
    receipt["version_vector"]["environment_revision"] = environment_revision  # type: ignore[index]
    receipt["promotion_component_revision"] = component_revision
    _write_allowlisted_receipt(root, support_tool, receipt)

    entry = support_tool._build_task_entry(
        root,
        task,
        image_live_run_ready=True,
        canonical_task_relative_path=task_relative_path,
    )

    assert entry["support_status"] == "live_validated"
    assert entry["support_reason_code"] == "live_validation_passed"
    assert entry["blocker_codes"] == []


@pytest.mark.parametrize(
    ("task_id", "required_blockers"),
    [
        (
            "Operation-FileOperate-CombinationDocs-003",
            {COMBINATIONDOCS003_RENDER_BLOCKER},
        ),
        (
            "Operation-OnlineShopping-AddToCart-001",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-OnlineShopping-AddToCart-002",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-OnlineShopping-AddToCart-003",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-OnlineShopping-AddToCart-004",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-OnlineShopping-AddToCart-005",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-OnlineShopping-AddToCart-006",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-OnlineShopping-AddToCart-007",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-OnlineShopping-CheapestProductSearch-007",
            {"webmall_cart_reader_reference_live_validation_not_completed"},
        ),
        (
            "Operation-FileOperate-Settings-001",
            {
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
            },
        ),
        (
            "Operation-FileOperate-BatchOperationExcel-008",
            {"pipeline_implicit_live_validation_not_completed"},
        ),
        (
            "Operation-FileOperate-BatchOperationPPT-003",
            {"pipeline_implicit_live_validation_not_completed"},
        ),
        (
            "Operation-FileOperate-CombinationDocs-002",
            {"pipeline_implicit_live_validation_not_completed"},
        ),
        (
            "Operation-FileOperate-SearchAndWrite-008",
            {"pipeline_implicit_live_validation_not_completed"},
        ),
        (
            "Operation-FileOperate-BatchOperationWord-009",
            {WORD_TEXT_FIDELITY_BLOCKER},
        ),
        (
            "Operation-FileOperate-BatchOperationWord-010",
            {WORD_TEXT_FIDELITY_BLOCKER},
        ),
    ],
)
def test_receipt_never_clears_task_specific_component_blockers(
    support_tool: ModuleType,
    task_id: str,
    required_blockers: set[str],
) -> None:
    """伪 receipt 不得清除资产、Cart、artifact、pipeline 或任务语义 blocker。

    输入参数：
        support_tool：已加载的生成器。
        task_id：覆盖各组件类别的 canonical task ID。
        required_blockers：任务必须保留的精确 blocker 子集。
    输出返回值：
        无；即使镜像与伪 receipt 为真，任务仍失败关闭。
    """

    task = _load_canonical_task(task_id)
    with patch.object(
        support_tool,
        "_has_trusted_live_validation_receipt",
        return_value=True,
    ):
        entry = support_tool._build_task_entry(
            REPO_ROOT,
            task,
            image_live_run_ready=True,
        )

    assert entry["support_status"] == "blocked"
    assert required_blockers <= set(entry["blocker_codes"])
    assert entry["blocker_codes"][-1] == VERSIONED_LIVE_BLOCKER
    assert GLOBAL_IMAGE_BLOCKER not in entry["blocker_codes"]


def test_exact_allowlisted_sanitized_receipt_is_accepted(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """摘要固定且全部当前身份匹配的最小 receipt 才可接受。

    输入参数：
        support_tool：已加载的生成器。
        tmp_path：pytest 提供的隔离仓库根。
        monkeypatch：用于固定本测试 task→receipt SHA。
    输出返回值：
        无；严格 loader 返回 ``True``。
    """

    receipt = _valid_receipt()
    _write_allowlisted_receipt(tmp_path, support_tool, receipt)

    assert support_tool._load_trusted_live_validation_receipt(
        tmp_path,
        task_id="synthetic-task",
        expected_evaluation_protocol="paraguibench.answer.exact.v1",
        expected_environment_protocol="osworld.desktop.v1",
        expected_environment_revision="manifest-sha256:" + "2" * 64,
        expected_component_revision="component-sha256:" + "3" * 64,
    )


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "final_output",
        "details",
        "events",
        "prompt",
        "response",
        "path",
        "host",
        "credential",
    ],
)
def test_receipt_rejects_every_non_allowlisted_sensitive_surface(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forbidden_field: str,
) -> None:
    """任意自由文本、事件、路径、主机或凭据字段都必须失败关闭。

    输入参数：
        support_tool/tmp_path/monkeypatch：合成 receipt 门禁依赖。
        forbidden_field：逐项注入的非 allowlist 字段名。
    输出返回值：
        无；loader 拒绝该 receipt 且错误不回显字段值。
    """

    receipt = _valid_receipt()
    receipt[forbidden_field] = "PRIVATE_VALUE"
    _write_allowlisted_receipt(tmp_path, support_tool, receipt)

    with pytest.raises(support_tool.RuntimeSupportError, match="receipt") as caught:
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id="synthetic-task",
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )
    assert "PRIVATE_VALUE" not in str(caught.value)


@pytest.mark.parametrize(
    ("mutation", "error_match"),
    [
        (lambda receipt: receipt.update(schema_version="1.0"), "schema"),
        (lambda receipt: receipt.update(execution_outcome="FAILED"), "outcome"),
        (lambda receipt: receipt.update(evaluation_outcome="FAILED"), "outcome"),
        (lambda receipt: receipt.update(score=True), "score"),
        (lambda receipt: receipt.update(score=-0.001), "score"),
        (lambda receipt: receipt.update(score=1.001), "score"),
        (lambda receipt: receipt.update(score=math.inf), "JSON"),
        (lambda receipt: receipt.update(score=-math.inf), "JSON"),
        (lambda receipt: receipt.update(score=10**4000), "score"),
        (
            lambda receipt: receipt["version_vector"].update(  # type: ignore[union-attr]
                evaluation_protocol="paraguibench.answer.numeric.v1"
            ),
            "version vector",
        ),
        (lambda receipt: receipt.update(version_vector={}), "version vector"),
        (
            lambda receipt: receipt["version_vector"].update(  # type: ignore[union-attr]
                environment_revision="manifest-sha256:" + "9" * 64
            ),
            "version vector",
        ),
        (
            lambda receipt: receipt.update(
                promotion_component_revision="component-sha256:" + "4" * 64
            ),
            "component revision",
        ),
    ],
)
def test_receipt_rejects_legacy_outcome_score_and_identity_drift(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: object,
    error_match: str,
) -> None:
    """旧 schema、非成功终态、非有限分数或当前身份漂移均被拒绝。

    输入参数：
        support_tool/tmp_path/monkeypatch：合成 receipt 门禁依赖。
        mutation：只改变一个安全契约的可调用对象。
        error_match：不包含外部值的预期错误区域。
    输出返回值：
        无；严格 loader 失败关闭。
    """

    receipt = _valid_receipt()
    assert callable(mutation)
    mutation(receipt)
    score = receipt.get("score", 0.0)
    if isinstance(score, float) and math.isinf(score):
        task_id = receipt["task_id"]
        assert isinstance(task_id, str)
        receipt_root = tmp_path / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
        receipt_root.mkdir(parents=True)
        payload = json.dumps(receipt, allow_nan=True).encode("utf-8")
        (receipt_root / f"{task_id}.json").write_bytes(payload)
        _write_receipt_allowlist(
            tmp_path,
            support_tool,
            {task_id: hashlib.sha256(payload).hexdigest()},
        )
    else:
        _write_allowlisted_receipt(tmp_path, support_tool, receipt)

    with pytest.raises(support_tool.RuntimeSupportError, match=error_match):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id="synthetic-task",
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )


def test_receipt_rejects_task_identity_drift_inside_allowlisted_file(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """receipt 内 task ID 不得与 allowlist key 和文件名漂移。

    输入参数：
        support_tool/tmp_path/monkeypatch：合成 receipt 门禁依赖。
    输出返回值：
        无；三重 task 身份不一致时失败关闭。
    """

    receipt = _valid_receipt()
    receipt["task_id"] = "another-task"
    receipt_root = tmp_path / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
    receipt_root.mkdir(parents=True)
    payload = (json.dumps(receipt, sort_keys=True, allow_nan=False) + "\n").encode(
        "utf-8"
    )
    (receipt_root / "synthetic-task.json").write_bytes(payload)
    _write_receipt_allowlist(
        tmp_path,
        support_tool,
        {"synthetic-task": hashlib.sha256(payload).hexdigest()},
    )

    with pytest.raises(support_tool.RuntimeSupportError, match="identity"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id="synthetic-task",
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )


def test_receipt_root_is_closed_and_rejects_symlink_or_extra_file(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """receipt 目录必须是不跟随符号链接的 task→SHA 物理闭集。

    输入参数：
        support_tool/tmp_path/monkeypatch：合成 receipt 门禁依赖。
    输出返回值：
        无；额外文件与 receipt 符号链接均失败关闭。
    """

    receipt = _valid_receipt()
    _write_allowlisted_receipt(tmp_path, support_tool, receipt)
    receipt_root = tmp_path / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
    (receipt_root / "extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(support_tool.RuntimeSupportError, match="closed"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id="synthetic-task",
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )

    (receipt_root / "extra.json").unlink()
    receipt_path = receipt_root / "synthetic-task.json"
    target_path = receipt_root / "target.json"
    receipt_path.rename(target_path)
    receipt_path.symlink_to(target_path.name)
    with pytest.raises(support_tool.RuntimeSupportError, match="symlink"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id="synthetic-task",
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )


def test_receipt_root_closed_set_is_revalidated_after_anchored_read(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Receipt 读取前后必须复验同一 dirfd 锚定的物理闭集。

    输入参数：
        support_tool：提供有界 receipt 读取与闭集校验。
        tmp_path：pytest 提供的隔离 receipt 仓库。
        monkeypatch：用于在首次 fd 读取时注入并发额外节点。
    输出返回值：
        无；即使目标 receipt 字节本身稳定，读取期间目录闭集
        发生变化也必须失败关闭。
    """

    receipt = _valid_receipt()
    _write_allowlisted_receipt(tmp_path, support_tool, receipt)
    receipt_root = tmp_path / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
    original_read = support_tool.os.read
    read_count = 0

    def racing_read(descriptor: int, maximum_bytes: int) -> bytes:
        """在第一次 receipt fd 读取时创建非 allowlist 节点。

        输入参数：
            descriptor：待读取的已打开文件描述符。
            maximum_bytes：本次 ``os.read`` 请求的上限。
        输出返回值：
            原始 ``os.read`` 返回的字节。
        """

        nonlocal read_count
        read_count += 1
        if read_count == 3:
            (receipt_root / "raced.json").write_text("{}", encoding="utf-8")
        return original_read(descriptor, maximum_bytes)

    monkeypatch.setattr(support_tool.os, "read", racing_read)

    with pytest.raises(support_tool.RuntimeSupportError, match="closed|闭集|unstable"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id="synthetic-task",
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )


def test_receipt_root_closes_new_descriptor_when_old_close_fails(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dirfd 链更新时旧 fd close 失败不得泄漏已打开的新 fd。

    输入参数：
        support_tool：提供 receipt root nofollow dirfd 链打开器。
        tmp_path：pytest 提供的隔离目录链。
        monkeypatch：用于记录 open/close 并在首次 close 注入异常。
    输出返回值：
        无；异常路径上所有已打开 descriptor 都必须出现在 close
        调用集中，特别是尚未交接给 ``current_descriptor`` 的新 fd。
    """

    receipt_root = tmp_path / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
    receipt_root.mkdir(parents=True)
    real_open = support_tool.os.open
    real_close = support_tool.os.close
    opened: list[int] = []
    close_calls: list[int] = []

    def tracking_open(*args: object, **kwargs: object) -> int:
        """记录真实 ``os.open`` 返回的 descriptor。

        输入参数：
            args/kwargs：原样透传给 ``os.open`` 的位置与关键字参数。
        输出返回值：
            真实打开并记录的 descriptor。
        """

        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def failing_first_close(descriptor: int) -> None:
        """首次真实关闭后注入 ``OSError``，其余关闭正常执行。

        输入参数：
            descriptor：待关闭的 descriptor。
        输出返回值：
            无；首次调用固定抛出 ``OSError``。
        """

        close_calls.append(descriptor)
        if len(close_calls) == 1:
            real_close(descriptor)
            raise OSError("synthetic close failure")
        real_close(descriptor)

    monkeypatch.setattr(support_tool.os, "open", tracking_open)
    monkeypatch.setattr(support_tool.os, "close", failing_first_close)
    try:
        with pytest.raises((support_tool.RuntimeSupportError, OSError)):
            support_tool._open_live_receipt_root_directory(
                tmp_path,
                allowlisted_task_ids=frozenset(),
            )
        assert set(opened).issubset(close_calls)
    finally:
        for descriptor in opened:
            try:
                real_close(descriptor)
            except OSError:
                pass


def test_receipt_reader_rejects_oversized_and_non_standard_nan_payloads(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """receipt 读取必须有界，且 JSON 非标准 NaN 不得伪装成分数。

    输入参数：
        support_tool/tmp_path/monkeypatch：合成 receipt 门禁依赖。
    输出返回值：
        无；超限与 NaN 字节均被拒绝。
    """

    task_id = "synthetic-task"
    receipt_root = tmp_path / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
    receipt_root.mkdir(parents=True)
    receipt_path = receipt_root / f"{task_id}.json"
    oversized = b"{" + b" " * (support_tool.MAX_LIVE_RECEIPT_BYTES + 1) + b"}"
    receipt_path.write_bytes(oversized)
    _write_receipt_allowlist(
        tmp_path,
        support_tool,
        {task_id: hashlib.sha256(oversized).hexdigest()},
    )
    with pytest.raises(support_tool.RuntimeSupportError, match="size"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id=task_id,
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )

    receipt = _valid_receipt()
    payload = json.dumps(receipt, allow_nan=False).replace("1.0", "NaN", 1).encode()
    receipt_path.write_bytes(payload)
    _write_receipt_allowlist(
        tmp_path,
        support_tool,
        {task_id: hashlib.sha256(payload).hexdigest()},
    )
    with pytest.raises(support_tool.RuntimeSupportError, match="JSON"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id=task_id,
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )


def test_receipt_rejects_duplicate_json_keys_and_sha_drift(
    support_tool: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """receipt 必须拒绝重复 JSON key 与任何 allowlist 字节摘要漂移。

    输入参数：
        support_tool/tmp_path/monkeypatch：合成 receipt 门禁依赖。
    输出返回值：
        无；重复 key 不会被 JSON 解码器静默覆盖，且摘要漂移在
        JSON 解码前失败关闭。
    """

    task_id = "synthetic-task"
    receipt_root = tmp_path / support_tool.LIVE_VALIDATION_RECEIPT_ROOT
    receipt_root.mkdir(parents=True)
    receipt_path = receipt_root / f"{task_id}.json"
    payload = json.dumps(_valid_receipt(), sort_keys=True).encode("utf-8")
    duplicated = payload.replace(
        b'"task_id": "synthetic-task"',
        b'"task_id": "synthetic-task", "task_id": "synthetic-task"',
        1,
    )
    receipt_path.write_bytes(duplicated)
    _write_receipt_allowlist(
        tmp_path,
        support_tool,
        {task_id: hashlib.sha256(duplicated).hexdigest()},
    )
    with pytest.raises(support_tool.RuntimeSupportError, match="JSON"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id=task_id,
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )

    receipt_path.write_bytes(payload)
    _write_receipt_allowlist(
        tmp_path,
        support_tool,
        {task_id: "f" * 64},
    )
    with pytest.raises(support_tool.RuntimeSupportError, match="SHA-256"):
        support_tool._load_trusted_live_validation_receipt(
            tmp_path,
            task_id=task_id,
            expected_evaluation_protocol="paraguibench.answer.exact.v1",
            expected_environment_protocol="osworld.desktop.v1",
            expected_environment_revision="manifest-sha256:" + "2" * 64,
            expected_component_revision="component-sha256:" + "3" * 64,
        )


def test_runtime_schema_rejects_pending_blocker_from_unrelated_protocol(
    support_tool: ModuleType,
) -> None:
    """JSON Schema 不得为无关协议接受组件 pending blocker。

    输入参数：
        support_tool：提供确定性清单与项目级 schema 实例校验器。
    输出返回值：
        无；``answer.exact`` 任务伪造 pipeline blocker 时必须被拒绝，
        不能利用宽泛的“非 Cart”分支通过形式校验。
    """

    schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)
    entry = next(
        item
        for item in manifest["tasks"]
        if item["task_id"] == "InformationRetrieval-WebSearch-ConditionalSearch-001"
    )
    entry["support_status"] = "blocked"
    entry["support_reason_code"] = "live_validation_pending"
    entry["blocker_codes"] = [
        "pipeline_implicit_live_validation_not_completed",
        VERSIONED_LIVE_BLOCKER,
    ]

    errors = support_tool._validate_runtime_support_schema_instance(
        schema,
        manifest,
    )

    assert errors


@pytest.mark.parametrize(
    ("task_id", "blocker_codes"),
    [
        (
            "Operation-FileOperate-BatchOperation-001",
            [
                "osworld_artifact_getter_live_validation_not_completed",
                "osworld_artifact_gold_live_validation_not_completed",
                "osworld_task_setup_live_validation_not_completed",
                VERSIONED_LIVE_BLOCKER,
            ],
        ),
        (
            "Operation-FileOperate-BatchOperationPPT-003",
            [
                "pipeline_implicit_live_validation_not_completed",
                VERSIONED_LIVE_BLOCKER,
            ],
        ),
        (
            "Operation-FileOperate-BatchOperationPPT-003",
            [VERSIONED_LIVE_BLOCKER],
        ),
    ],
)
def test_runtime_schema_accepts_protocol_specific_pending_blocker_sets(
    support_tool: ModuleType,
    task_id: str,
    blocker_codes: list[str],
) -> None:
    """JSON Schema 必须仅为所属协议接受其 pending blocker 闭集。

    输入参数：
        support_tool：提供正式清单与项目级 schema 实例校验器。
        task_id：分别代表 artifact-state 与 pipeline-implicit 的任务。
        blocker_codes：该协议在 image-ready 阶段的合法 pending 闭集。
    输出返回值：
        无；协议与 blocker 闭集相符时实例校验通过。
    """

    schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)
    entry = next(item for item in manifest["tasks"] if item["task_id"] == task_id)
    entry["support_status"] = "blocked"
    entry["support_reason_code"] = "live_validation_pending"
    entry["blocker_codes"] = blocker_codes

    assert (
        support_tool._validate_runtime_support_schema_instance(schema, manifest) == []
    )


@pytest.mark.parametrize(
    "blocker_codes",
    (
        [VERSIONED_LIVE_BLOCKER, PIPELINE_COMPONENT_BLOCKER],
        [PIPELINE_COMPONENT_BLOCKER],
        [
            "pipeline_implicit_typed_observation_parser_not_migrated",
            VERSIONED_LIVE_BLOCKER,
        ],
    ),
)
def test_runtime_schema_rejects_invalid_pipeline_pending_blocker_sets(
    support_tool: ModuleType,
    blocker_codes: list[str],
) -> None:
    """pipeline pending schema 拒绝反序、缺 V 或夹带 local blocker。

    输入参数：support_tool 提供项目级 schema 校验器；
        blocker_codes 为三类非法 pipeline pending 序列。
    输出返回值：每个伪造闭集都产生至少一个 schema 错误。
    """

    schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)
    entry = next(
        item
        for item in manifest["tasks"]
        if item["task_id"] == "Operation-FileOperate-BatchOperationPPT-003"
    )
    entry["support_status"] = "blocked"
    entry["support_reason_code"] = "live_validation_pending"
    entry["blocker_codes"] = blocker_codes

    assert support_tool._validate_runtime_support_schema_instance(schema, manifest)


def test_webmall_promotion_identity_binds_current_osworld_browser_manifest(
    support_tool: ModuleType,
    tmp_path: Path,
) -> None:
    """WebMall promotion 必须传递绑定当前 OSWorld 浏览器镜像。

    输入参数：
        support_tool：提供 promotion 环境与组件身份派生函数。
        tmp_path：pytest 提供的隔离环境 manifest 根目录。
    输出返回值：
        无；WebMall 组件闭包同时包含 WebMall 与 OSWorld manifest，
        且嵌套 SHA 与当前 OSWorld 字节不一致时失败关闭。
    """

    webmall_relative = Path("environments/webmall/environment-manifest.json")
    osworld_relative = Path("environments/osworld/image-manifest.json")
    task = _load_canonical_task("Operation-OnlineShopping-AddToCart-001")
    component_paths = support_tool._collect_promotion_component_paths(
        REPO_ROOT,
        task=task,
        environment_protocol="webmall.browser.v1",
    )
    assert webmall_relative in component_paths
    assert osworld_relative in component_paths

    for relative_path in (webmall_relative, osworld_relative):
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative_path).read_bytes())
    support_tool._derive_current_environment_revision(
        tmp_path,
        environment_protocol="webmall.browser.v1",
    )
    (tmp_path / osworld_relative).write_bytes(b"{}\n")

    with pytest.raises(support_tool.RuntimeSupportError, match="WebMall|browser image"):
        support_tool._derive_current_environment_revision(
            tmp_path,
            environment_protocol="webmall.browser.v1",
        )


def test_promotion_component_revision_binds_guard_implementation_bytes(
    support_tool: ModuleType,
    tmp_path: Path,
) -> None:
    """Promotion component revision 必须绑定实际晋升 guard 实现。

    输入参数：
        support_tool：提供 promotion-safe 组件 revision 构造器。
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；只改动 runtime-support guard 脚本字节就必须改变
        component revision，使旧 receipt 失效。
    """

    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "src" / "paraguibench", root / "src/paraguibench")
    shutil.copytree(
        REPO_ROOT / "benchmark" / "schemas",
        root / "benchmark/schemas",
    )
    task_id = "Operation-OnlineShopping-AddToCart-001"
    task_path = root / "benchmark/tasks" / f"{task_id}.json"
    task_path.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "benchmark/tasks" / f"{task_id}.json", task_path)
    for relative_path in (
        Path("environments/webmall/environment-manifest.json"),
        Path("environments/osworld/image-manifest.json"),
        Path("scripts/benchmark/runtime_support_manifest.py"),
        Path("pyproject.toml"),
    ):
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative_path, target)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    arguments = {
        "repo_root": root,
        "task": task,
        "evaluation_protocol": "paraguibench.webmall.cart.closed-world.v1",
        "environment_protocol": "webmall.browser.v1",
        "asset_status": "zero_asset",
    }
    before = support_tool._derive_promotion_component_revision(**arguments)

    guard_path = root / "scripts/benchmark/runtime_support_manifest.py"
    guard_path.write_bytes(
        guard_path.read_bytes() + b"\n# promotion guard revision drift\n"
    )
    after = support_tool._derive_promotion_component_revision(**arguments)

    assert before != after


def test_promotion_component_paths_use_release_validated_canonical_path(
    support_tool: ModuleType,
) -> None:
    """Component revision 必须使用 release 已验证路径而非猜测文件名。

    输入参数：
        support_tool：提供 promotion component 路径闭包构造器。
    输出返回值：
        无；调用方传入的 release-validated 安全相对路径必须
        进入闭包，基于 task ID 猜测的默认路径不得同时进入。
    """

    task = _load_canonical_task("Operation-OnlineShopping-AddToCart-001")
    release_path = Path("benchmark/tasks-reviewed/add-to-cart-001.json")
    guessed_path = Path("benchmark/tasks/Operation-OnlineShopping-AddToCart-001.json")

    paths = support_tool._collect_promotion_component_paths(
        REPO_ROOT,
        task=task,
        environment_protocol="webmall.browser.v1",
        canonical_task_relative_path=release_path,
    )

    assert release_path in paths
    assert guessed_path not in paths


def test_component_reader_rejects_parent_traversal_before_filesystem_access(
    support_tool: ModuleType,
    tmp_path: Path,
) -> None:
    """Component 读取器必须在访问文件系统前拒绝 ``..`` 穿越。

    输入参数：
        support_tool：提供 promotion component 安全读取器。
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；包含父目录段的路径以固定路径错误失败关闭，
        即使仓库外节点真实存在也不得打开。
    """

    repository_root = tmp_path / "repo"
    repository_root.mkdir()
    (tmp_path / "outside.json").write_text("outside", encoding="utf-8")
    with pytest.raises(support_tool.RuntimeSupportError, match="path|路径"):
        support_tool._read_repository_component_file(
            repository_root,
            Path("../outside.json"),
            label="test component",
        )


def test_manifest_build_fails_before_projection_when_loaded_package_drifts(
    support_tool: ModuleType,
) -> None:
    """Generator 入口必须先绑定实际 import package 与 repo src。

    输入参数：
        support_tool：提供 runtime-support 正式 build 入口。
    输出返回值：
        无；已加载 package 与待摘要 repo 不一致时，build 在任何
        task 状态投影前失败关闭。
    """

    with patch.object(
        support_tool,
        "_validate_loaded_runtime_package_matches_repository",
        side_effect=support_tool.RuntimeSupportError(
            "loaded package 与 repository package 源码不一致"
        ),
    ) as package_gate:
        with pytest.raises(support_tool.RuntimeSupportError, match="loaded package"):
            support_tool.build_runtime_support_manifest(REPO_ROOT)

    package_gate.assert_called_once_with(REPO_ROOT.resolve())


def test_formal_validator_executes_schema_instance_validation(
    support_tool: ModuleType,
    tmp_path: Path,
) -> None:
    """独立 validator 必须执行 schema 实例语义，而不只核对 ``$id``。

    输入参数：
        support_tool：提供正式 runtime-support validator。
        tmp_path：pytest 提供的隔离清单路径。
    输出返回值：
        无；只违反 schema 字段闭集的合成条目会产生明确实例错误。
    """

    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)
    manifest["tasks"][0]["unexpected_runtime_field"] = "must-be-rejected"
    path = tmp_path / "runtime-support-v1.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = support_tool.validate_runtime_support_manifest(REPO_ROOT, path)

    assert any("JSON Schema instance" in error for error in result.errors)
    assert all("must-be-rejected" not in error for error in result.errors)


def test_project_schema_validator_fails_closed_on_unknown_assertion_keyword(
    support_tool: ModuleType,
) -> None:
    """项目级 schema evaluator 不得静默忽略未支持的 assertion。

    输入参数：
        support_tool：提供当前 runtime-support schema 实例校验器。
    输出返回值：
        无；在可达 task schema 节点注入未知 assertion 后，校验
        必须返回不含实例值的 unsupported-keyword 错误。
    """

    schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["$defs"]["taskSupport"]["futureAssertion"] = True
    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)

    errors = support_tool._validate_runtime_support_schema_instance(schema, manifest)

    assert any("unsupported-keyword" in error for error in errors)


def test_project_schema_validator_rejects_supported_ref_sibling_assertion(
    support_tool: ModuleType,
) -> None:
    """项目级 evaluator 不得因 ``$ref`` 早返回而忽略同级 assertion。

    输入参数：
        support_tool：提供 runtime-support schema 关键字闭集校验器。
    输出返回值：
        无；当 ``$ref`` 节点出现 evaluator 虽已支持但尚未实现
        sibling 合取语义的 ``const`` 时，必须以固定 ref-sibling
        错误失败关闭。
    """

    schema = json.loads(SUPPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema["properties"]["tasks"]["items"]["const"] = None
    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)

    errors = support_tool._validate_runtime_support_schema_instance(schema, manifest)

    assert any("ref-sibling" in error for error in errors)


def test_word_live_blocker_count_excludes_closed_word012_semantics(
    support_tool: ModuleType,
) -> None:
    """Word Writer 实机门禁保留，而 Word-012 本地语义 blocker 清零。

    输入参数：
        support_tool：提供当前 233-task 确定性 runtime-support 投影。
    输出返回值：
        无；Word-009/010 共享 Writer live-validation blocker 恰好出现 2 次，
        已闭合 production evaluator/runtime 的 Word-012 不再携带本地语义
        blocker；已验证镜像 blocker 归零，四项 pipeline 任务
        都保留 pipeline-live，其他真实环境门禁不因此减少。
    """

    manifest = support_tool.build_runtime_support_manifest(REPO_ROOT)
    counts = Counter(
        blocker for entry in manifest["tasks"] for blocker in entry["blocker_codes"]
    )

    assert counts[WORD_TEXT_FIDELITY_BLOCKER] == 2
    assert counts[WORD_ABBREVIATION_BLOCKER] == 0
    assert counts[GLOBAL_IMAGE_BLOCKER] == 0
    assert counts[VERSIONED_LIVE_BLOCKER] == 233
    assert counts["legacy_asset_manifest_not_migrated"] == 0
    assert counts["webmall_cart_reader_reference_live_validation_not_completed"] == 8
    assert counts["pipeline_implicit_live_validation_not_completed"] == 4
    assert counts["osworld_artifact_getter_live_validation_not_completed"] == 15
