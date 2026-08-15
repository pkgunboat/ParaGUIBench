"""SearchWrite-008 正式 production runtime 能力绑定测试。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from paraguibench.evaluation.pipeline_implicit import (
    SEARCHWRITE_DOCUMENT_CONTRACTS,
    SEARCHWRITE_XLSX_TASK_ID,
)
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    SEARCHWRITE008_GOLD_MANIFEST_PATH,
    SEARCHWRITE008_INPUT_MANIFEST_PATH,
)
from paraguibench.runtime.assets import (
    ResolvedTaskAssets,
    TaskAssetMode,
    resolve_task_assets,
)
from paraguibench.runtime.pipeline_implicit_binding import (
    PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS,
    PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS,
    PipelineImplicitRuntimeCapability,
    PipelineImplicitRuntimeManifestError,
    preflight_pipeline_implicit_local_runtime,
    preflight_pipeline_implicit_runtime,
    validate_pipeline_implicit_runtime_capability,
)
import paraguibench.runtime.pipeline_implicit_binding as runtime_binding


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_searchwrite008_exports_machine_bound_local_runtime_capability() -> None:
    """验证 SearchWrite-008 已形成正式本地 runtime capability。

    输入参数：
        无；读取 canonical task 与两份固定 manifest 原始字节。
    输出返回值：
        无；receipt-neutral 与普通 preflight 都返回与 canonical、
        input/gold 原始字节和 typed evaluator 身份绑定的脱敏 capability。
    """

    task_path = REPO_ROOT / "benchmark/tasks" / f"{SEARCHWRITE_XLSX_TASK_ID}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == SEARCHWRITE008_INPUT_MANIFEST_PATH
    assert task["gold_manifest"] == SEARCHWRITE008_GOLD_MANIFEST_PATH
    capability = preflight_pipeline_implicit_local_runtime(
        repo_root=REPO_ROOT,
        task=task,
    )

    assert isinstance(capability, PipelineImplicitRuntimeCapability)
    assert capability.task_id == SEARCHWRITE_XLSX_TASK_ID
    assert capability.protocol_id == "paraguibench.operation.searchwrite-xlsx.v1"
    assert (
        capability.input_manifest_sha256
        == hashlib.sha256(
            (REPO_ROOT / SEARCHWRITE008_INPUT_MANIFEST_PATH).read_bytes()
        ).hexdigest()
    )
    assert (
        capability.reference_manifest_sha256
        == hashlib.sha256(
            (REPO_ROOT / SEARCHWRITE008_GOLD_MANIFEST_PATH).read_bytes()
        ).hexdigest()
    )
    assert capability.reference_manifest_role == "gold"
    assert capability.input_manifest_sha256 not in repr(capability)
    assert capability.reference_manifest_sha256 not in repr(capability)
    assert "manifest" not in repr(capability).lower()
    assert ".xlsx" not in repr(capability)
    ordinary = preflight_pipeline_implicit_runtime(repo_root=REPO_ROOT, task=task)
    assert ordinary.task_id == capability.task_id
    assert ordinary.protocol_id == capability.protocol_id
    assert ordinary.input_manifest_sha256 == capability.input_manifest_sha256
    assert ordinary.reference_manifest_sha256 == capability.reference_manifest_sha256
    assert ordinary.environment_identity_sha256 is not None
    assert PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS == frozenset(
        {
            "Operation-FileOperate-BatchOperationExcel-008",
            "Operation-FileOperate-BatchOperationPPT-003",
            "Operation-FileOperate-CombinationDocs-002",
            SEARCHWRITE_XLSX_TASK_ID,
        }
    )
    assert PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS == frozenset(
        {
            "Operation-FileOperate-BatchOperationExcel-008",
            "Operation-FileOperate-BatchOperationPPT-003",
            "Operation-FileOperate-CombinationDocs-002",
            SEARCHWRITE_XLSX_TASK_ID,
        }
    )


def test_searchwrite008_prepare_rejects_held_asset_manifest_drift() -> None:
    """验证 prepare ABA 门禁直接比较已持有的 input manifest。

    输入参数：
        无；从正式 canonical 分别构造 preflight capability 与
        environment 实际将上传的 parsed input manifest。
    输出返回值：
        无；同源 A 能通过，已持有 manifest 的文件顺序或
        capability 摘要漂移都以脱敏 manifest error 失败关闭。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{SEARCHWRITE_XLSX_TASK_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    capability = preflight_pipeline_implicit_local_runtime(
        repo_root=REPO_ROOT,
        task=task,
    )
    task_assets = resolve_task_assets(REPO_ROOT, task)
    assert isinstance(capability, PipelineImplicitRuntimeCapability)
    validate_pipeline_implicit_runtime_capability(
        repo_root=REPO_ROOT,
        task=task,
        task_assets=task_assets,
        capability=capability,
    )
    assert task_assets.manifest is not None
    drifted_assets = ResolvedTaskAssets(
        mode=TaskAssetMode.PINNED_DOWNLOAD_MANIFEST,
        manifest=replace(
            task_assets.manifest,
            files=tuple(reversed(task_assets.manifest.files)),
        ),
    )

    for observed_assets, observed_capability in (
        (drifted_assets, capability),
        (
            task_assets,
            replace(capability, input_manifest_sha256="0" * 64),
        ),
    ):
        with pytest.raises(PipelineImplicitRuntimeManifestError) as captured:
            validate_pipeline_implicit_runtime_capability(
                repo_root=REPO_ROOT,
                task=task,
                task_assets=observed_assets,
                capability=observed_capability,
            )
        assert str(captured.value) == "PIPELINE_IMPLICIT_MANIFEST_INVALID"


def test_searchwrite008_raw_manifest_byte_drift_fails_closed(
    tmp_path: Path,
) -> None:
    """验证正式 input 清单即使只有 JSON 字节漂移也被拒绝。

    输入参数：
        tmp_path：pytest 提供的一次性仓库根，不改动 canonical 资产。
    输出返回值：
        无；语义可解析但原始字节不再等于确定性 builder 时，
        local preflight 在 guest、Agent 和 RunStore 前失败关闭。
    """

    repository = tmp_path / "repo"
    task_relative = Path("benchmark/tasks") / f"{SEARCHWRITE_XLSX_TASK_ID}.json"
    for relative in (
        task_relative,
        Path(SEARCHWRITE008_INPUT_MANIFEST_PATH),
        Path(SEARCHWRITE008_GOLD_MANIFEST_PATH),
    ):
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    input_path = repository / SEARCHWRITE008_INPUT_MANIFEST_PATH
    input_path.write_bytes(input_path.read_bytes() + b"\n")
    task = json.loads((repository / task_relative).read_text(encoding="utf-8"))

    with pytest.raises(PipelineImplicitRuntimeManifestError) as captured:
        preflight_pipeline_implicit_local_runtime(repo_root=repository, task=task)

    assert str(captured.value) == "PIPELINE_IMPLICIT_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "drift_kind",
    (
        "expected-value",
        "cell-order",
        "baseline-digest",
        "baseline-protocol",
        "cell-match-protocol",
    ),
)
def test_searchwrite008_typed_contract_drift_fails_closed(
    drift_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证九格值/顺序、基线与匹配协议漂移时拒绝运行。

    输入参数：
        drift_kind：当前故障注入的唯一合同维度。
        monkeypatch：只替换 runtime 本次重算机器身份所见常量。
    输出返回值：
        无；公开 preflight 必须返回固定脱敏 manifest 错误，
        不把单元格值、坐标、摘要或路径写入异常。
    """

    documents = SEARCHWRITE_DOCUMENT_CONTRACTS
    first = documents[0]
    if drift_kind == "expected-value":
        cells = (
            replace(first.expected_cells[0], expected_value=3),
            *first.expected_cells[1:],
        )
        monkeypatch.setattr(
            runtime_binding,
            "SEARCHWRITE_DOCUMENT_CONTRACTS",
            (replace(first, expected_cells=cells), *documents[1:]),
        )
    elif drift_kind == "cell-order":
        cells = (
            first.expected_cells[1],
            first.expected_cells[0],
            *first.expected_cells[2:],
        )
        monkeypatch.setattr(
            runtime_binding,
            "SEARCHWRITE_DOCUMENT_CONTRACTS",
            (replace(first, expected_cells=cells), *documents[1:]),
        )
    elif drift_kind == "baseline-digest":
        monkeypatch.setattr(
            runtime_binding,
            "SEARCHWRITE_DOCUMENT_CONTRACTS",
            (replace(first, baseline_sha256="0" * 64), *documents[1:]),
        )
    elif drift_kind == "baseline-protocol":
        monkeypatch.setattr(
            runtime_binding,
            "SEARCHWRITE_BASELINE_PROJECTION_PROTOCOL_ID",
            "paraguibench.operation.searchwrite-baseline.drift",
        )
    else:
        monkeypatch.setattr(
            runtime_binding,
            "SEARCHWRITE_CELL_MATCH_PROTOCOL_ID",
            "paraguibench.operation.searchwrite-cell-match.drift",
        )
    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{SEARCHWRITE_XLSX_TASK_ID}.json").read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(PipelineImplicitRuntimeManifestError) as captured:
        preflight_pipeline_implicit_runtime(repo_root=REPO_ROOT, task=task)

    assert str(captured.value) == "PIPELINE_IMPLICIT_MANIFEST_INVALID"
