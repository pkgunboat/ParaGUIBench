"""OSWorld artifact component receipt 的公开物理闭集门禁。"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import shutil

import pytest

import paraguibench.runtime.osworld_artifact_component_receipts as receipt_module
from paraguibench.benchmark import prepare_release_task
from paraguibench.runstore import (
    AttemptFailureStage,
    AttemptInspection,
    EvaluationOutcome,
    ExecutionOutcome,
    RunProvenanceStatus,
    RunStore,
    RunVersionVector,
)
from paraguibench.runtime.gold_assets import load_gold_asset_manifest
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
    OSWORLD_ARTIFACT_ENVIRONMENT_PROTOCOL,
    OSWorldArtifactComponentEnvironmentProof,
    osworld_artifact_environment_protocol,
)
from paraguibench.runtime.osworld_artifact_component_receipts import (
    OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_KIND,
    OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_RELATIVE_PATH,
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
    OSWorldArtifactComponentReceiptError,
    OSWorldArtifactComponentReceipt,
    build_osworld_artifact_component_receipt,
    derive_osworld_artifact_component_identity,
    export_osworld_artifact_component_receipt,
    load_trusted_osworld_artifact_component_receipts,
)
from paraguibench.runtime.osworld_artifact_component_validation import (
    OSWorldArtifactComponentValidationError,
    OSWorldArtifactComponentValidationResult,
)
from paraguibench.runtime.run_versioning import build_run_version_vector


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_empty_canonical_allowlist_keeps_all_component_receipts_pending() -> None:
    """确认初始空 allowlist 不会伪造任何实机组件证据。

    输入参数：无；读取仓库内 canonical 空 allowlist。
    输出返回值：公开 loader 返回空的不可变任务集合。
    """

    assert load_trusted_osworld_artifact_component_receipts(REPO_ROOT) == frozenset()


def test_component_receipt_task_set_stays_narrower_than_identity_only_tasks() -> None:
    """确认 component receipt 只覆盖获准候选与晋级的 12 项。

    输入参数：无；读取生产公开任务闭集。
    输出返回值：闭集精确等于十二个 canonical task，
        且不包含仅进入 13 项身份闭集的 ``Settings-001``。
    """

    assert OSWORLD_ARTIFACT_COMPONENT_TASK_IDS == frozenset(
        {
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
            "Operation-WebOperate-SearchAndWrite-001",
        }
    )
    assert "Operation-FileOperate-Settings-001" not in (
        OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
    )


def test_settings_has_identity_only_derivation_without_receipt_eligibility(
    tmp_path: Path,
) -> None:
    """确认 Settings 进入 13 任务身份闭集但不进入 receipt 闭集。

    输入参数：tmp_path 承载已接入 canonical v2 gold 引用的
        receipt-neutral 隔离仓库。
    输出返回：Settings 能派生五层 current identity，但公开
        candidate/receipt 任务集仍不包含它。
    """

    task_id = "Operation-FileOperate-Settings-001"
    repo = _copy_settings_identity_repository(tmp_path)

    identity = derive_osworld_artifact_component_identity(repo, task_id)

    assert task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
    assert len(identity.task_identity_sha256) == 64
    assert len(identity.gold_component_sha256) == 64


@pytest.mark.parametrize("drift", ("strict-v2", "semantic-input-binding"))
def test_settings_identity_rejects_v2_or_input_binding_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    """确认 identity-only 派生不信任原始 JSON 形状或跨任务引用。

    输入参数：tmp_path 承载 Settings 隔离仓库；drift 分别
        选择严格 v2 固定值漂移或 task/input manifest 语义错绑。
    输出返回：两类攻击都抛固定脱敏 identity/receipt 错误，
        不产生可用于 receipt 或 promotion 的摘要。
    """

    task_id = "Operation-FileOperate-Settings-001"
    repo = _copy_settings_identity_repository(tmp_path)
    if drift == "strict-v2":
        manifest_path = repo / f"benchmark/gold/manifests/{task_id}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["derivation"]["requested_pts"] = "8.000001"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        task_path = repo / f"benchmark/tasks/{task_id}.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        task["asset_manifest"] = (
            "benchmark/assets/manifests/Operation-FileOperate-BatchOperation-003.json"
        )
        task_path.write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        release_path = repo / "benchmark/manifests/release-v1.json"
        release = json.loads(release_path.read_text(encoding="utf-8"))
        matching = [entry for entry in release["tasks"] if entry["task_id"] == task_id]
        assert len(matching) == 1
        matching[0]["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
        release_path.write_text(
            json.dumps(release, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        derive_osworld_artifact_component_identity(repo, task_id)


def test_settings_identity_rejects_gold_cross_open_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认 identity 摘要与 v2 语义校验消费同一份 gold 字节。

    输入参数：tmp_path 承载 Settings 隔离仓库；monkeypatch
        在旧 path loader 系统边界模拟「非法 A→合法 B→非法 A」。
    输出返回：第一次已捕获的非法 license basis 必须被严格
        bytes parser 拒绝；身份派生不得二次打开 gold 路径。
    """

    task_id = "Operation-FileOperate-Settings-001"
    repo = _copy_settings_identity_repository(tmp_path)
    gold_path = repo / f"benchmark/gold/manifests/{task_id}.json"
    canonical_payload = gold_path.read_bytes()
    invalid = json.loads(canonical_payload)
    invalid["license"]["basis"] = "invalid-untrusted-basis"
    invalid_payload = (json.dumps(invalid, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    gold_path.write_bytes(invalid_payload)
    path_loader_calls = 0

    def swap_during_path_load(path: Path) -> object:
        """在旧二次 path-open 期间短暂置换为合法 gold。

        输入参数：path 为 identity 尝试重新打开的 gold 路径。
        输出返回：旧 loader 对合法瞬时字节的解析结果；
            返回前恢复非法落盘字节。
        """

        nonlocal path_loader_calls
        path_loader_calls += 1
        gold_path.write_bytes(canonical_payload)
        try:
            return load_gold_asset_manifest(path)
        finally:
            gold_path.write_bytes(invalid_payload)

    monkeypatch.setattr(
        receipt_module,
        "load_gold_asset_manifest",
        swap_during_path_load,
        raising=False,
    )

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        derive_osworld_artifact_component_identity(repo, task_id)

    assert path_loader_calls == 0
    assert json.loads(gold_path.read_bytes())["license"]["basis"] == (
        "invalid-untrusted-basis"
    )


def test_settings_identity_rejects_captured_input_manifest_tamper(
    tmp_path: Path,
) -> None:
    """确认 v2 source contract 反向绑定同一份已捕获 input manifest。

    输入参数：tmp_path 承载 Settings 隔离仓库；测试只把
        实际 input manifest 中 source MP4 SHA 篡改为全零，不修改
        task、release、v2 gold 或 input draft。
    输出返回：identity-only 派生必须拒绝被篡改的 input
        manifest，不得仅凭 schema/asset-set/files 外形生成摘要。
    """

    task_id = "Operation-FileOperate-Settings-001"
    repo = _copy_settings_identity_repository(tmp_path)
    asset_path = repo / f"benchmark/assets/manifests/{task_id}.json"
    asset_manifest = json.loads(asset_path.read_text(encoding="utf-8"))
    matching = [
        entry for entry in asset_manifest["files"] if entry["path"] == "landscape.mp4"
    ]
    assert len(matching) == 1
    matching[0]["sha256"] = "0" * 64
    asset_path.write_text(
        json.dumps(asset_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        derive_osworld_artifact_component_identity(repo, task_id)


def test_component_identity_is_deterministic_and_task_scoped() -> None:
    """确认当前 task/release/资产/环境/组件形成稳定身份。

    输入参数：无；对两个 canonical task 重复派生身份。
    输出返回值：同任务结果稳定，两任务的 task 身份不同，
        共用环境和 setup/getter/gold 代码闭集身份相同。
    """

    first = derive_osworld_artifact_component_identity(
        REPO_ROOT,
        "Operation-FileOperate-BatchOperation-003",
    )
    repeated = derive_osworld_artifact_component_identity(
        REPO_ROOT,
        "Operation-FileOperate-BatchOperation-003",
    )
    other = derive_osworld_artifact_component_identity(
        REPO_ROOT,
        "Operation-FileOperate-CombinationDocs-009",
    )

    assert first == repeated
    assert first.task_identity_sha256 != other.task_identity_sha256
    assert first.environment_identity_sha256 == other.environment_identity_sha256
    assert first.setup_component_sha256 != other.setup_component_sha256
    assert first.getter_component_sha256 == other.getter_component_sha256
    assert first.gold_component_sha256 == other.gold_component_sha256
    for value in (
        first.task_identity_sha256,
        first.environment_identity_sha256,
        first.setup_component_sha256,
        first.getter_component_sha256,
        first.gold_component_sha256,
    ):
        assert len(value) == 64
        assert set(value) <= set("0123456789abcdef")


def test_allowlist_rejects_float_schema_version(tmp_path: Path) -> None:
    """确认 JSON 浮点 ``1.0`` 不能伪装整数 schema 版本。

    输入参数：tmp_path 为 pytest 提供的隔离仓库根。
    输出返回值：公开 loader 抛出固定脱敏错误。
    """

    provenance = tmp_path / "benchmark" / "provenance"
    provenance.mkdir(parents=True)
    allowlist = provenance / "osworld-artifact-component-receipt-allowlist-v1.json"
    allowlist.write_text(
        json.dumps({"schema_version": 1.0, "receipts": {}}),
        encoding="utf-8",
    )

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        load_trusted_osworld_artifact_component_receipts(tmp_path)


def test_component_identity_changes_when_task_input_draft_changes(
    tmp_path: Path,
) -> None:
    """确认 setup 直接依赖的 task input draft 进入当前身份。

    输入参数：tmp_path 为包含最小 receipt-neutral 仓库闭集的隔离根。
    输出返回值：仅改动 input draft 原始字节时，task 与 setup
        身份都必须变化，旧 receipt 不能继续通过。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    before = derive_osworld_artifact_component_identity(repo, task_id)
    draft = (
        repo / "benchmark/assets/manifests/osworld-state-drafts/"
        "Operation-FileOperate-BatchOperation-003.input.draft.json"
    )
    draft.write_bytes(draft.read_bytes() + b"\n")

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        derive_osworld_artifact_component_identity(repo, task_id)

    # 生产 catalog 已固定 draft SHA；漂移必须失败关闭，而不是
    # 产生一个可被旧 receipt 误认为 current 的新身份。
    assert before.task_identity_sha256
    assert before.setup_component_sha256


def _copy_identity_repository(tmp_path: Path, task_id: str) -> Path:
    """复制身份派生所需的最小仓库文件闭集。

    输入参数：tmp_path 为隔离根；task_id 为待保留的 canonical task。
    输出返回值：可供公开 identity API 读取的最小仓库根。
    """

    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "src/paraguibench", repo / "src/paraguibench")
    shutil.copytree(
        REPO_ROOT / "benchmark/schemas",
        repo / "benchmark/schemas",
    )
    relative_files = (
        "pyproject.toml",
        "scripts/benchmark/runtime_support_manifest.py",
        "benchmark/manifests/release-v1.json",
        f"benchmark/tasks/{task_id}.json",
        f"benchmark/assets/manifests/{task_id}.json",
        f"benchmark/gold/manifests/{task_id}.json",
        (f"benchmark/assets/manifests/osworld-state-drafts/{task_id}.input.draft.json"),
        "environments/osworld/image-manifest.json",
    )
    for relative in relative_files:
        source = REPO_ROOT / relative
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return repo


def _copy_settings_identity_repository(tmp_path: Path) -> Path:
    """复制并闭合 Settings v2 gold 引用的 identity-only 隔离仓库。

    输入参数：tmp_path 为 pytest 隔离目录。
    输出返回值：task 已声明 canonical v2 gold 且 release SHA 已同步的仓库根；
        不改写正式仓库的 task 或 release manifest。
    """

    task_id = "Operation-FileOperate-Settings-001"
    repo = _copy_identity_repository(tmp_path, task_id)
    task_path = repo / f"benchmark/tasks/{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["gold_manifest"] = f"benchmark/gold/manifests/{task_id}.json"
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    release_path = repo / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    matching = [entry for entry in release["tasks"] if entry["task_id"] == task_id]
    assert len(matching) == 1
    matching[0]["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return repo


def _write_current_component_receipt(repo: Path, task_id: str) -> dict[str, object]:
    """为隔离仓库写入与当前五层身份一致的 receipt。

    输入参数：repo 为已复制的 receipt-neutral 仓库；task_id
        为 12-task 闭集成员。
    输出返回值：写入 receipt 与外置摘要 allowlist 后返回
        receipt JSON object，供后续负例定点篡改。
    """

    identity = derive_osworld_artifact_component_identity(repo, task_id)
    receipt = OSWorldArtifactComponentReceipt(
        schema_version=1,
        receipt_kind="paraguibench.osworld.artifact-component.v1",
        task_id=task_id,
        run_id="run-live-component-001",
        attempt_id="attempt-001",
        execution_outcome="SUCCEEDED",
        evaluation_outcome="PASSED",
        score=1.0,
        candidate_evaluation_protocol=(
            "paraguibench.osworld.artifact-component-validation.v1"
        ),
        task_evaluation_protocol="paraguibench.osworld.artifact-state.v1",
        environment_protocol=osworld_artifact_environment_protocol(task_id),
        attempt_version_vector_sha256="7" * 64,
        task_identity_sha256=identity.task_identity_sha256,
        environment_identity_sha256=identity.environment_identity_sha256,
        setup_component_sha256=identity.setup_component_sha256,
        getter_component_sha256=identity.getter_component_sha256,
        gold_component_sha256=identity.gold_component_sha256,
    ).to_dict()
    payload = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    receipt_root = repo / "benchmark/provenance/osworld-artifact-component-receipts"
    receipt_root.mkdir(parents=True)
    (receipt_root / f"{task_id}.json").write_bytes(payload)
    allowlist = {
        "schema_version": 1,
        "receipts": {
            task_id: {
                "receipt_sha256": hashlib.sha256(payload).hexdigest(),
                "task_identity_sha256": identity.task_identity_sha256,
                "environment_identity_sha256": (identity.environment_identity_sha256),
                "setup_component_sha256": identity.setup_component_sha256,
                "getter_component_sha256": identity.getter_component_sha256,
                "gold_component_sha256": identity.gold_component_sha256,
            }
        },
    }
    allowlist_path = (
        repo / "benchmark/provenance/"
        "osworld-artifact-component-receipt-allowlist-v1.json"
    )
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def test_current_allowlisted_component_receipt_loads_one_task(
    tmp_path: Path,
) -> None:
    """确认非空 loader 只返回 receipt 与五层 current 身份均一致的任务。

    输入参数：tmp_path 承载单任务隔离仓库和脱敏 receipt。
    输出返回值：loader 返回只含该 task_id 的不可变集合。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    _write_current_component_receipt(repo, task_id)

    assert load_trusted_osworld_artifact_component_receipts(repo) == frozenset(
        {task_id}
    )


def _replace_receipt_and_allowlist_sha(
    repo: Path,
    task_id: str,
    payload: bytes,
) -> None:
    """在负例中同步替换 receipt 字节与外置 SHA。

    输入参数：repo/task_id 定位单任务测试 receipt；payload
        为攻击者希望 allowlist 重签后仍被拒绝的原始字节。
    输出返回值：无；写回 receipt 并只更新 receipt_sha256。
    """

    receipt_path = (
        repo
        / "benchmark/provenance/osworld-artifact-component-receipts"
        / f"{task_id}.json"
    )
    receipt_path.write_bytes(payload)
    allowlist_path = (
        repo / "benchmark/provenance/"
        "osworld-artifact-component-receipt-allowlist-v1.json"
    )
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowlist["receipts"][task_id]["receipt_sha256"] = hashlib.sha256(
        payload
    ).hexdigest()
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("physical_attack", ("missing", "symlink", "special"))
def test_allowlisted_receipt_rejects_non_regular_physical_member(
    tmp_path: Path,
    physical_attack: str,
) -> None:
    """确认 allowlist 不能授权缺失、symlink 或 FIFO receipt。

    输入参数：tmp_path 为隔离仓库；physical_attack 由 pytest
        分别选择三种非单链接普通文件状态。
    输出返回值：loader 统一抛固定脱敏错误。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    _write_current_component_receipt(repo, task_id)
    receipt_path = (
        repo
        / "benchmark/provenance/osworld-artifact-component-receipts"
        / f"{task_id}.json"
    )
    original = receipt_path.read_bytes()
    receipt_path.unlink()
    if physical_attack == "symlink":
        external = tmp_path / "external.json"
        external.write_bytes(original)
        receipt_path.symlink_to(external)
    elif physical_attack == "special":
        os.mkfifo(receipt_path)

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        load_trusted_osworld_artifact_component_receipts(repo)


def test_allowlisted_receipt_rejects_oversize_and_extra_directory_member(
    tmp_path: Path,
) -> None:
    """确认有界读取与目录名称闭集均不可绕过。

    输入参数：tmp_path 承载两个独立测试仓库。
    输出返回值：即使超限 receipt 的 SHA 已同步重签，或额外
        成员本身是普通文件，loader 均固定失败。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    oversize_repo = _copy_identity_repository(tmp_path / "oversize", task_id)
    _write_current_component_receipt(oversize_repo, task_id)
    _replace_receipt_and_allowlist_sha(
        oversize_repo,
        task_id,
        b"{" + b" " * (64 * 1024) + b"}",
    )
    with pytest.raises(OSWorldArtifactComponentReceiptError):
        load_trusted_osworld_artifact_component_receipts(oversize_repo)

    extra_repo = _copy_identity_repository(tmp_path / "extra", task_id)
    _write_current_component_receipt(extra_repo, task_id)
    extra_root = extra_repo / "benchmark/provenance/osworld-artifact-component-receipts"
    (extra_root / "unlisted.json").write_text("{}", encoding="utf-8")
    with pytest.raises(OSWorldArtifactComponentReceiptError):
        load_trusted_osworld_artifact_component_receipts(extra_repo)


@pytest.mark.parametrize(
    "payload_attack",
    ("duplicate-key", "sensitive-extra", "cross-task", "score-zero", "nan"),
)
def test_receipt_payload_closed_set_rejects_re_signed_attacks(
    tmp_path: Path,
    payload_attack: str,
) -> None:
    """确认重签 SHA 不能绕过 JSON/字段/task/得分闭集。

    输入参数：tmp_path 为隔离仓库；payload_attack 选择重复
        key、敏感额外字段、cross-task、非满分或 NaN。
    输出返回值：loader 只抛固定错误，不回显敏感值。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    receipt = _write_current_component_receipt(repo, task_id)
    if payload_attack == "duplicate-key":
        payload = (
            b'{"schema_version":1,"schema_version":1,'
            + json.dumps(
                {
                    key: value
                    for key, value in receipt.items()
                    if key != "schema_version"
                },
                sort_keys=True,
            )[1:].encode("utf-8")
            + b"\n"
        )
    elif payload_attack == "nan":
        payload = (
            json.dumps(receipt, sort_keys=True)
            .replace(
                '"score": 1.0',
                '"score": NaN',
            )
            .encode("utf-8")
        )
    else:
        if payload_attack == "sensitive-extra":
            receipt["secret"] = "PRIVATE_COMPONENT_SECRET"
        elif payload_attack == "cross-task":
            receipt["task_id"] = "Operation-FileOperate-CombinationDocs-009"
        else:
            receipt["score"] = 0.0
        payload = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    _replace_receipt_and_allowlist_sha(repo, task_id, payload)

    with pytest.raises(OSWorldArtifactComponentReceiptError) as captured:
        load_trusted_osworld_artifact_component_receipts(repo)

    assert "PRIVATE_COMPONENT_SECRET" not in repr(captured.value)


@pytest.mark.parametrize(
    "identity_field",
    (
        "task_identity_sha256",
        "environment_identity_sha256",
        "setup_component_sha256",
        "getter_component_sha256",
        "gold_component_sha256",
    ),
)
def test_allowlist_rejects_each_stale_identity(
    tmp_path: Path,
    identity_field: str,
) -> None:
    """确认 task/environment/setup/getter/gold 任一过期均失败关闭。

    输入参数：tmp_path 为隔离仓库；identity_field 为 pytest
        分别篡改的五个外置摘要之一。
    输出返回值：无；任一摘要不等于 current 事实均抛错。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    _write_current_component_receipt(repo, task_id)
    allowlist_path = (
        repo / "benchmark/provenance/"
        "osworld-artifact-component-receipt-allowlist-v1.json"
    )
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowlist["receipts"][task_id][identity_field] = "0" * 64
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        load_trusted_osworld_artifact_component_receipts(repo)


def test_receipt_loader_detects_read_time_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认 receipt 首次读后的 TOCTOU 篡改不能通过复验。

    输入参数：tmp_path 为隔离仓库；monkeypatch 在 loader
        首次获得 receipt 原始字节后立即改写同一路径。
    输出返回值：后置字节/目录身份复验固定拒绝竞态。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    _write_current_component_receipt(repo, task_id)
    original_reader = receipt_module._read_repository_file
    receipt_relative = (
        receipt_module.OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ROOT / f"{task_id}.json"
    )
    mutated = False

    def read_then_mutate(
        repo_root: Path,
        relative_path: Path,
        *,
        maximum_bytes: int,
    ) -> bytes:
        """首次读 receipt 后修改路径字节。

        输入参数：与生产 nofollow reader 完全相同。
        输出返回值：返回竞态发生前已稳定读取的原始字节。
        """

        nonlocal mutated
        payload = original_reader(
            repo_root,
            relative_path,
            maximum_bytes=maximum_bytes,
        )
        if relative_path == receipt_relative and not mutated:
            mutated = True
            target = repo_root / relative_path
            target.write_bytes(b" " + payload[1:])
        return payload

    monkeypatch.setattr(receipt_module, "_read_repository_file", read_then_mutate)

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        load_trusted_osworld_artifact_component_receipts(repo)


def test_settings_and_generic_receipts_never_clear_component_tasks(
    tmp_path: Path,
) -> None:
    """确认 Settings identity-only 任务与 generic receipt 均不进入晋级闭集。

    输入参数：tmp_path 承载 Settings 伪 allowlist 与独立 generic
        receipt 的两个最小仓库。
    输出返回值：Settings 键固定拒绝；只有 generic 文件且专用
        allowlist 为空时返回空集合。
    """

    settings_repo = tmp_path / "settings"
    provenance = settings_repo / "benchmark/provenance"
    provenance.mkdir(parents=True)
    fake_entry = {field: "0" * 64 for field in receipt_module._ALLOWLIST_ENTRY_FIELDS}
    (provenance / "osworld-artifact-component-receipt-allowlist-v1.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "receipts": {"Operation-FileOperate-Settings-001": fake_entry},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(OSWorldArtifactComponentReceiptError):
        load_trusted_osworld_artifact_component_receipts(settings_repo)

    generic_repo = tmp_path / "generic"
    generic_provenance = generic_repo / "benchmark/provenance"
    generic_provenance.mkdir(parents=True)
    (
        generic_provenance / "osworld-artifact-component-receipt-allowlist-v1.json"
    ).write_text(
        json.dumps({"schema_version": 1, "receipts": {}}),
        encoding="utf-8",
    )
    generic_root = generic_provenance / "live-validation-receipts"
    generic_root.mkdir()
    (generic_root / "Operation-FileOperate-BatchOperation-003.json").write_text(
        "{}",
        encoding="utf-8",
    )
    assert load_trusted_osworld_artifact_component_receipts(generic_repo) == frozenset()


def test_export_rejects_plain_passed_attempt_without_component_attestation(
    tmp_path: Path,
) -> None:
    """确认普通 PASSED Attempt 不能伪造三项组件观测。

    输入参数：tmp_path 承载 repo 外的临时 RunStore。
    输出返回值：即使手工提交 SUCCEEDED/PASSED，缺少专属 candidate
        生命周期 attestation 时导出仍固定失败；纯 evaluator 终态不等于
        setup/getter/gold 三个生产组件已实际运行。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    run_id = "run-artifact-component-001"
    attempt_id = "attempt-001"
    runs_root = tmp_path / "runs"
    vector = build_run_version_vector(
        repo_root=REPO_ROOT,
        task_id=task_id,
        environment_manifest_path=(
            REPO_ROOT / "environments/osworld/image-manifest.json"
        ),
    )
    prepared = prepare_release_task(
        REPO_ROOT,
        task_id,
        environment_bindings={},
    )
    store = RunStore(runs_root)
    store.start_run(run_id=run_id, run_record={}, version_vector=vector)
    attempt = store.start_attempt(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        task_record=prepared.audit_metadata,
    )
    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        evaluation_outcome=EvaluationOutcome.PASSED,
        score=1.0,
        failure_stage=AttemptFailureStage.NOT_FAILED,
        details={},
    )

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        export_osworld_artifact_component_receipt(
            repo_root=REPO_ROOT,
            runs_root=runs_root,
            task_id=task_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )


def test_export_rejects_user_written_attestation_marker(tmp_path: Path) -> None:
    """确认普通 RunStore 调用方写入 marker 仍不能晋升组件支持。

    输入参数：tmp_path 承载 repo 外临时 RunStore。
    输出返回值：即使 Attempt 为 PASSED 且 artifact 自报正确 kind，普通
        导出路径也固定拒绝；用户可写 JSON 不能替代受控内存生命周期。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    run_id = "run-artifact-component-forged"
    attempt_id = "attempt-001"
    runs_root = tmp_path / "runs"
    vector = build_run_version_vector(
        repo_root=REPO_ROOT,
        task_id=task_id,
        environment_manifest_path=(
            REPO_ROOT / "environments/osworld/image-manifest.json"
        ),
    )
    prepared = prepare_release_task(
        REPO_ROOT,
        task_id,
        environment_bindings={},
    )
    store = RunStore(runs_root)
    store.start_run(run_id=run_id, run_record={}, version_vector=vector)
    attempt = store.start_attempt(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        task_record=prepared.audit_metadata,
    )
    store.write_artifact(
        attempt=attempt,
        logical_name="osworld-artifact-component-attempt-v1",
        relative_path=(
            OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_RELATIVE_PATH.as_posix()
        ),
        content={
            "attestation_kind": (OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_KIND)
        },
        media_type="application/json",
    )
    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        evaluation_outcome=EvaluationOutcome.PASSED,
        score=1.0,
        failure_stage=AttemptFailureStage.NOT_FAILED,
        details={},
    )

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        export_osworld_artifact_component_receipt(
            repo_root=REPO_ROOT,
            runs_root=runs_root,
            task_id=task_id,
            run_id=run_id,
            attempt_id=attempt_id,
        )


def test_component_receipt_schema_is_closed_and_task_scoped() -> None:
    """确认独立 JSON Schema 固定安全字段与 12-task 闭集。

    输入参数：无；读取版本化 receipt schema。
    输出返回值：顶层、版本向量与组件检查均禁止额外字段，
        task enum 精确等于生产 12-task 闭集。
    """

    schema = json.loads(
        (
            REPO_ROOT
            / "benchmark/schemas/osworld-artifact-component-receipt-v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    receipt = schema["$defs"]["receipt"]

    assert schema["$id"] == (
        "urn:paraguibench:schema:osworld-artifact-component-receipt:v1"
    )
    assert receipt["additionalProperties"] is False
    assert set(receipt["properties"]["task_id"]["enum"]) == (
        OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
    )
    assert receipt["properties"]["schema_version"] == {
        "type": "integer",
        "const": 1,
    }
    assert receipt["properties"]["score"] == {
        "type": "number",
        "const": 1,
    }
    assert "version_vector" not in receipt["properties"]
    assert receipt["properties"]["candidate_evaluation_protocol"] == {
        "const": "paraguibench.osworld.artifact-component-validation.v1"
    }
    assert receipt["properties"]["task_evaluation_protocol"] == {
        "const": "paraguibench.osworld.artifact-state.v1"
    }
    assert receipt["properties"]["environment_protocol"] == {
        "enum": ["osworld.chrome.v1", "osworld.desktop.v1"]
    }
    assert receipt["properties"]["attempt_version_vector_sha256"] == {
        "$ref": "#/$defs/sha256"
    }
    assert receipt["properties"]["component_checks"]["additionalProperties"] is False
    assert not {
        "final_output",
        "details",
        "events",
        "path",
        "content",
        "gold",
        "secret",
        "credential",
        "endpoint",
    }.intersection(receipt["properties"])


def test_weboperate_receipt_requires_official_chrome_protocol() -> None:
    """确认 WebOperate candidate receipt 不能再用 desktop 协议过关。

    输入参数：无；手工构造字段齐全的 receipt。
    输出返回值：desktop 被拒绝，chrome 通过形状校验。
    """

    task_id = "Operation-WebOperate-SearchAndWrite-001"
    digest = "a" * 64
    common = {
        "schema_version": 1,
        "receipt_kind": "paraguibench.osworld.artifact-component.v1",
        "task_id": task_id,
        "run_id": "run-weboperate-component",
        "attempt_id": "attempt-001",
        "execution_outcome": "SUCCEEDED",
        "evaluation_outcome": "PASSED",
        "score": 1.0,
        "candidate_evaluation_protocol": (
            "paraguibench.osworld.artifact-component-validation.v1"
        ),
        "task_evaluation_protocol": "paraguibench.osworld.artifact-state.v1",
        "attempt_version_vector_sha256": digest,
        "task_identity_sha256": digest,
        "environment_identity_sha256": digest,
        "setup_component_sha256": digest,
        "getter_component_sha256": digest,
        "gold_component_sha256": digest,
    }
    with pytest.raises(OSWorldArtifactComponentReceiptError):
        OSWorldArtifactComponentReceipt(
            **common,
            environment_protocol="osworld.desktop.v1",
        )
    accepted = OSWorldArtifactComponentReceipt(
        **common,
        environment_protocol="osworld.chrome.v1",
    )
    assert accepted.environment_protocol == "osworld.chrome.v1"


def test_hand_built_candidate_result_cannot_mint_receipt() -> None:
    """确认手工 proof 与 PASSED inspection 不能伪造 candidate。

    输入参数：无；调用方手工构造全部 True 的 environment
        proof 与 SUCCEEDED/PASSED RunStore 投影。
    输出返回值：公开 result 构造边界在 receipt builder 之前
        即固定失败，不运行环境就不能获得进程 capability。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    run_id = "run-component-candidate"
    attempt_id = "attempt-001"
    revision = "tree-sha256:" + "7" * 64
    vector = RunVersionVector(
        source_revision=revision,
        agent_code_revision=revision,
        evaluator_revision=revision,
        evaluation_protocol=OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
        environment_protocol=OSWORLD_ARTIFACT_ENVIRONMENT_PROTOCOL,
        environment_revision="manifest-sha256:" + "8" * 64,
    )
    with pytest.raises(OSWorldArtifactComponentValidationError):
        OSWorldArtifactComponentValidationResult(
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            environment_proof=OSWorldArtifactComponentEnvironmentProof(
                task_id=task_id,
                task_setup_completed=True,
                artifact_getter_completed=True,
                evaluator_gold_completed=True,
                owned_environment_closed=True,
            ),
            evaluator_gold_completed=True,
            inspection=AttemptInspection(
                execution_outcome=ExecutionOutcome.SUCCEEDED,
                evaluation_outcome=EvaluationOutcome.PASSED,
                score=1.0,
                failure_stage=AttemptFailureStage.NOT_FAILED,
                provenance_status=RunProvenanceStatus.VERSIONED,
                version_vector=vector,
            ),
        )


def test_public_builder_permanently_rejects_caller_values() -> None:
    """确认公开 builder 不接受任何调用方可构造对象。

    输入参数：无；传入当前仓库与普通 object。
    输出返回值：固定抛出脱敏 receipt 错误；只有专属
        top-level candidate 能在同一内部生命周期构造 receipt。
    """

    with pytest.raises(OSWorldArtifactComponentReceiptError):
        build_osworld_artifact_component_receipt(
            repo_root=REPO_ROOT,
            validation=object(),  # type: ignore[arg-type]
        )
