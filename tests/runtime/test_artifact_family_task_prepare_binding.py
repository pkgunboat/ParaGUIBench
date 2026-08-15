"""13 个 OSWorld artifact-family 任务的 pre-Docker 能力门禁测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path

import pytest

from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
)
from paraguibench.runtime.artifact_family_task_prepare import (
    ArtifactFamilyTaskPrepareCapabilityError,
    inspect_artifact_family_task_prepare_capability,
    preflight_artifact_family_task_prepare,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _canonical_task(task_id: str) -> dict[str, object]:
    """读取一份受 release 固定的 canonical task。

    输入参数：
        task_id：artifact-family catalog 内的稳定任务 ID。
    输出返回值：
        解析后的 canonical JSON object。
    """

    value = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return value


def test_first_capability_tracer_readies_verified_batch_without_paths() -> None:
    """验证首个任务形成严格绑定，且公开对象不泄露路径。

    输入参数：
        无；使用当前 BatchOperation-003 canonical 与 input draft。
    输出返回值：
        无；已核验草案和严格 manifest 必须形成 ready 能力，同时公开
        capability 与 binding 均不得泄露资产、远端或 host 路径。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    capability = inspect_artifact_family_task_prepare_capability(
        repo_root=REPO_ROOT,
        task=_canonical_task(task_id),
    )

    assert capability is not None
    assert capability.task_id == task_id
    assert capability.input_count == 1
    assert capability.ready is True
    assert capability.blocker_ids == ()
    binding = preflight_artifact_family_task_prepare(
        repo_root=REPO_ROOT,
        task=_canonical_task(task_id),
    )
    assert binding is not None
    public_text = repr(capability) + repr(binding)
    assert "raw_book.zip" not in public_text
    assert "huggingface" not in public_text.lower()
    assert str(REPO_ROOT) not in public_text

    with pytest.raises(FrozenInstanceError):
        capability.input_count = 0  # type: ignore[misc]


def test_batch_canonical_rejects_legacy_and_strict_asset_modes_together() -> None:
    """验证同一 canonical 任务不得同时声明旧 URL 与严格 manifest。

    输入参数：
        无；在当前 BatchOperation-003 strict canonical 上叠加旧 URL 声明。
    输出返回值：
        无；pre-Docker 能力检查必须把混合状态视为合同损坏，而不是把其中
        一种模式静默降级为 blocker 或可运行绑定。
    """

    task = _canonical_task("Operation-FileOperate-BatchOperation-003")
    task["prepare_script_path"] = (
        "https://huggingface.co/datasets/xlangai/"
        "ubuntu_osworld_file_cache/resolve/main/"
        "multi_apps/5df7b33a-9f77-4101-823e-02f863e1c1ae/raw_book.zip"
    )

    with pytest.raises(
        ArtifactFamilyTaskPrepareCapabilityError,
        match=r"^ARTIFACT_FAMILY_TASK_PREPARE_CONTRACT_INVALID$",
    ):
        inspect_artifact_family_task_prepare_capability(
            repo_root=REPO_ROOT,
            task=task,
        )


def test_batch_canonical_strict_mode_binds_verified_manifest_identity() -> None:
    """验证 BatchOperation-003 只使用严格 input/gold manifest。

    输入参数：
        无；读取发布 canonical、prepare spec、正式 input manifest 与草案。
    输出返回值：
        无；legacy URL 必须消失，spec 绑定 manifest 路径/摘要，且经过
        verified draft 与 strict manifest 的 preflight 形成可执行准备绑定。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    task = _canonical_task(task_id)
    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
    manifest_reference = (
        "benchmark/assets/manifests/Operation-FileOperate-BatchOperation-003.json"
    )
    gold_reference = (
        "benchmark/gold/manifests/Operation-FileOperate-BatchOperation-003.json"
    )
    manifest_bytes = (REPO_ROOT / manifest_reference).read_bytes()

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == manifest_reference
    assert task["gold_manifest"] == gold_reference
    assert spec.canonical_asset_mode == "strict_asset_manifest"
    assert spec.canonical_prepare_reference_sha256 is None
    assert spec.canonical_asset_manifest_relative_path == manifest_reference
    assert (
        spec.canonical_asset_manifest_sha256
        == hashlib.sha256(manifest_bytes).hexdigest()
    )

    capability = inspect_artifact_family_task_prepare_capability(
        repo_root=REPO_ROOT,
        task=task,
    )
    binding = preflight_artifact_family_task_prepare(
        repo_root=REPO_ROOT,
        task=task,
    )

    assert capability is not None and capability.ready
    assert capability.blocker_ids == ()
    assert binding is not None
    assert binding.asset_manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()


def test_current_thirteen_task_capabilities_close_71_inputs_and_blockers() -> None:
    """验证当前 13-task 闭集、71 输入与逐任务可信缺口均机器可检查。

    输入参数：
        无；逐项读取 canonical、冻结 catalog 和 input draft。
    输出返回值：
        无；71 个 input 的 path/integrity/license/strict manifest 与三项
        已查明的 idle-desktop start context 全部闭合，十三项均 ready。
    """

    observed_input_count = 0
    observed_ready: set[str] = set()
    for task_id in ARTIFACT_FAMILY_TASK_PREPARE_SPECS:
        capability = inspect_artifact_family_task_prepare_capability(
            repo_root=REPO_ROOT,
            task=_canonical_task(task_id),
        )
        assert capability is not None
        observed_input_count += capability.input_count
        assert capability.inferred_path_count == 0
        assert capability.unverified_integrity_count == 0
        assert capability.ready
        assert capability.blocker_ids == ()
        assert (
            preflight_artifact_family_task_prepare(
                repo_root=REPO_ROOT,
                task=_canonical_task(task_id),
            )
            is not None
        )
        observed_ready.add(task_id)

    assert observed_input_count == 71
    assert observed_ready == set(ARTIFACT_FAMILY_TASK_PREPARE_SPECS)
