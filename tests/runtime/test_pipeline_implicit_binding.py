"""pipeline-implicit 正式 production runtime 能力绑定测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

import paraguibench.runtime.pipeline_implicit_binding as binding_module
from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_PROTOCOL_ID,
    HIDE_NA_ROWS_TASK_ID,
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    SEARCHWRITE_XLSX_TASK_ID,
)
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    COMBINATION002_INPUT_MANIFEST_PATH,
    COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
    EXCEL008_GOLD_MANIFEST_PATH,
    EXCEL008_INPUT_MANIFEST_PATH,
    PPT003_GOLD_MANIFEST_PATH,
    PPT003_INPUT_MANIFEST_PATH,
)
from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    derive_pipeline_implicit_environment_identity,
)
from paraguibench.runtime.pipeline_implicit_binding import (
    PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS,
    PipelineImplicitRuntimeCapability,
    preflight_pipeline_implicit_component_candidate_runtime,
    preflight_pipeline_implicit_runtime,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ppt003_preflight_exports_machine_bound_runtime_capability() -> None:
    """验证 PPT-003 完整组件形成不可变、脱敏且可执行的正式能力。

    输入参数：
        无；读取 canonical task 及两份固定 manifest 原始字节。
    输出返回值：
        无；preflight 返回唯一正式任务能力，协议和两份机器摘要绑定当前
        仓库字节，同时调试表示不泄漏路径或摘要；当前专用
        receipt 复验后 ordinary preflight 返回同一 capability，不代替
        runtime-support 中独立的 versioned-live 门禁。
    """

    task = json.loads(
        (
            REPO_ROOT / "benchmark" / "tasks" / f"{IMAGE_CLASSIFICATION_TASK_ID}.json"
        ).read_text(encoding="utf-8")
    )

    image_manifest = load_osworld_image_manifest(
        REPO_ROOT / "environments/osworld/image-manifest.json"
    )
    capability = preflight_pipeline_implicit_component_candidate_runtime(
        repo_root=REPO_ROOT,
        task=task,
        image_manifest=image_manifest,
    )

    assert PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS == frozenset(
        {
            IMAGE_CLASSIFICATION_TASK_ID,
            HIDE_NA_ROWS_TASK_ID,
            CROSS_DOCUMENT_TASK_ID,
            SEARCHWRITE_XLSX_TASK_ID,
        }
    )
    assert isinstance(capability, PipelineImplicitRuntimeCapability)
    assert capability.task_id == IMAGE_CLASSIFICATION_TASK_ID
    assert capability.protocol_id == IMAGE_CLASSIFICATION_PROTOCOL_ID
    input_digest = hashlib.sha256(
        (REPO_ROOT / PPT003_INPUT_MANIFEST_PATH).read_bytes()
    ).hexdigest()
    gold_digest = hashlib.sha256(
        (REPO_ROOT / PPT003_GOLD_MANIFEST_PATH).read_bytes()
    ).hexdigest()
    assert capability.input_manifest_sha256 == input_digest
    assert capability.reference_manifest_sha256 == gold_digest
    assert capability.reference_manifest_role == "gold"
    assert input_digest not in repr(capability)
    assert gold_digest not in repr(capability)
    assert "manifest" not in repr(capability).lower()

    trusted_capability = preflight_pipeline_implicit_runtime(
        repo_root=REPO_ROOT,
        task=task,
        image_manifest=image_manifest,
    )
    assert trusted_capability == capability


def test_candidate_refresh_does_not_consume_old_component_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认普通评测与 candidate refresh 都不消费 component receipt。

    输入参数：monkeypatch 把 receipt loader 替换为计数哨兵。
    输出返回值：两条 preflight 都完成 PPT003 静态能力绑定，且都不触发 loader。
    """

    task = json.loads(
        (
            REPO_ROOT / "benchmark" / "tasks" / f"{IMAGE_CLASSIFICATION_TASK_ID}.json"
        ).read_text(encoding="utf-8")
    )
    calls = 0
    observed_environment_identities: list[str | None] = []

    def receipt_loader(
        _repo_root: Path,
        *,
        expected_environment_identity_sha256: str | None = None,
    ) -> frozenset[str]:
        """记录 ordinary receipt 门禁调用并返回空授权。

        输入参数：_repo_root 为被测 preflight 传入的仓库根。
        输出返回值：空的当前 component receipt 集合。
        """

        nonlocal calls
        calls += 1
        observed_environment_identities.append(expected_environment_identity_sha256)
        return frozenset()

    monkeypatch.setattr(
        binding_module,
        "load_trusted_pipeline_implicit_component_receipts",
        receipt_loader,
    )

    image_manifest = load_osworld_image_manifest(
        REPO_ROOT / "environments/osworld/image-manifest.json"
    )
    expected_environment_identity = derive_pipeline_implicit_environment_identity(
        image_manifest
    )
    capability = preflight_pipeline_implicit_component_candidate_runtime(
        repo_root=REPO_ROOT,
        task=task,
        image_manifest=image_manifest,
    )

    assert isinstance(capability, PipelineImplicitRuntimeCapability)
    assert capability.environment_manifest_sha256 == image_manifest.manifest_sha256
    assert capability.environment_identity_sha256 == expected_environment_identity
    assert capability.container_image == image_manifest.container_image
    assert capability.extracted_qcow2_sha256 == image_manifest.extracted_sha256
    ordinary = preflight_pipeline_implicit_runtime(
        repo_root=REPO_ROOT,
        task=task,
        image_manifest=image_manifest,
    )
    assert ordinary == capability
    assert calls == 0
    assert observed_environment_identities == []


@pytest.mark.parametrize(
    ("task_id", "protocol_id", "input_path", "reference_path", "reference_role"),
    (
        (
            HIDE_NA_ROWS_TASK_ID,
            HIDE_NA_ROWS_PROTOCOL_ID,
            EXCEL008_INPUT_MANIFEST_PATH,
            EXCEL008_GOLD_MANIFEST_PATH,
            "gold",
        ),
        (
            CROSS_DOCUMENT_TASK_ID,
            CROSS_DOCUMENT_PROTOCOL_ID,
            COMBINATION002_INPUT_MANIFEST_PATH,
            COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
            "audit_known_negative",
        ),
    ),
)
def test_excel_and_combo_export_machine_bound_local_capabilities(
    task_id: str,
    protocol_id: str,
    input_path: str,
    reference_path: str,
    reference_role: str,
) -> None:
    """验证 Excel/Combo 已闭合资产、typed bridge 与评价机器身份。

    输入参数：
        task/protocol/path/role：两项任务的正式机器身份参数。
    输出返回值：
        无；candidate 与普通 preflight 都返回 held input/reference
        原始 SHA capability，不消费 component receipt。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    image_manifest = load_osworld_image_manifest(
        REPO_ROOT / "environments/osworld/image-manifest.json"
    )
    capability = preflight_pipeline_implicit_component_candidate_runtime(
        repo_root=REPO_ROOT,
        task=task,
        image_manifest=image_manifest,
    )

    assert isinstance(capability, PipelineImplicitRuntimeCapability)
    assert capability.task_id == task_id
    assert capability.protocol_id == protocol_id
    assert (
        capability.input_manifest_sha256
        == hashlib.sha256((REPO_ROOT / input_path).read_bytes()).hexdigest()
    )
    assert (
        capability.reference_manifest_sha256
        == hashlib.sha256((REPO_ROOT / reference_path).read_bytes()).hexdigest()
    )
    assert capability.reference_manifest_role == reference_role
    ordinary = preflight_pipeline_implicit_runtime(
        repo_root=REPO_ROOT,
        task=task,
        image_manifest=image_manifest,
    )
    assert ordinary == capability


def test_ordinary_preflight_rejects_stale_supplied_image_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正式 image 字节漂移后，旧 DTO 不得解锁 receipt 门禁。

    输入参数：tmp_path 提供仅用于 image path 的隔离仓库；
        monkeypatch 只让本测试穿过与这一安全性无关的本地 task 能力。
    输出返回值：无；正式边界必须内部重读当前 manifest，
        并在任何 receipt loader/guest/RunStore 副作用前拒绝旧快照。
    """

    image_path = tmp_path / "environments/osworld/image-manifest.json"
    image_path.parent.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "environments/osworld/image-manifest.json",
        image_path,
    )
    stale = load_osworld_image_manifest(image_path)
    task = {"task_id": IMAGE_CLASSIFICATION_TASK_ID}
    local_capability = PipelineImplicitRuntimeCapability(
        task_id=IMAGE_CLASSIFICATION_TASK_ID,
        protocol_id=IMAGE_CLASSIFICATION_PROTOCOL_ID,
        input_manifest_sha256="a" * 64,
        reference_manifest_sha256="b" * 64,
        reference_manifest_role="gold",
    )
    monkeypatch.setattr(
        binding_module,
        "preflight_pipeline_implicit_local_runtime",
        lambda **_kwargs: local_capability,
    )
    receipt_calls = {"count": 0}

    def _unexpected_receipt_loader(*_args: object, **_kwargs: object) -> frozenset[str]:
        """若 stale snapshot 未先失败，记录安全顺序违反。"""

        receipt_calls["count"] += 1
        return frozenset({IMAGE_CLASSIFICATION_TASK_ID})

    monkeypatch.setattr(
        binding_module,
        "load_trusted_pipeline_implicit_component_receipts",
        _unexpected_receipt_loader,
    )
    raw = json.loads(image_path.read_text(encoding="utf-8"))
    raw["environment_id"] = "drifted-environment"
    image_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(binding_module.PipelineImplicitRuntimeManifestError):
        preflight_pipeline_implicit_runtime(
            repo_root=tmp_path,
            task=task,
            image_manifest=stale,
        )

    assert receipt_calls["count"] == 0
