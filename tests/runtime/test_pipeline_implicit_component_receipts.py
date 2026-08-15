"""pipeline implicit component receipt 的公开门禁回归测试。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from paraguibench.runtime import pipeline_implicit_component_receipts as receipt_module
from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ALLOWLIST_PATH,
    PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT,
    PIPELINE_IMPLICIT_COMPONENT_TASK_IDS,
    PipelineImplicitComponentIdentity,
    PipelineImplicitComponentReceipt,
    PipelineImplicitComponentReceiptError,
    derive_pipeline_implicit_component_identity,
    derive_pipeline_implicit_component_identity_for_environment,
    derive_pipeline_implicit_environment_identity,
    load_trusted_pipeline_implicit_component_receipts,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RECEIPT_SCHEMA_PATH = (
    REPO_ROOT / "benchmark/schemas/pipeline-implicit-component-receipt-v1.schema.json"
)


def _copy_identity_repository(tmp_path: Path, task_id: str) -> Path:
    """复制派生一个 pipeline component 身份所需的最小闭集。

    输入参数：tmp_path 为 pytest 隔离目录；task_id 为 selected task。
    输出返回值：包含任务、环境、代码/schema 且不含 receipt 的仓库根。
    """

    repo = tmp_path / "repo"
    for relative in ("src/paraguibench", "benchmark/schemas"):
        shutil.copytree(REPO_ROOT / relative, repo / relative)
    reference_relative = (
        f"benchmark/provenance/pipeline-implicit-known-negative/{task_id}.json"
        if task_id == "Operation-FileOperate-CombinationDocs-002"
        else f"benchmark/gold/manifests/{task_id}.json"
    )
    for relative in (
        "pyproject.toml",
        "scripts/benchmark/runtime_support_manifest.py",
        "src/paraguibench/cli/main.py",
        "benchmark/manifests/release-v1.json",
        f"benchmark/tasks/{task_id}.json",
        f"benchmark/assets/manifests/{task_id}.json",
        reference_relative,
        "environments/osworld/image-manifest.json",
    ):
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    # 本 writer 暂不改共享 release；隔离身份夹具只同步 selected entry，
    # 让 task/reference ABA 回归不被串行派生阶段的已知 SHA 漂移遮蔽。
    task_path = repo / f"benchmark/tasks/{task_id}.json"
    release_path = repo / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    selected = next(item for item in release["tasks"] if item["task_id"] == task_id)
    selected["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return repo


def _write_current_component_receipt(repo: Path, task_id: str) -> dict[str, object]:
    """写入与当前三层身份精确绑定的合成 receipt 与外置授权。

    输入参数：repo 为隔离仓库；task_id 为当前可验证 pipeline task。
    输出返回值：已写入 receipt 的严格脱敏 object。
    """

    identity = derive_pipeline_implicit_component_identity(repo, task_id)
    receipt = PipelineImplicitComponentReceipt(
        schema_version=1,
        receipt_kind="paraguibench.pipeline-implicit.component.v1",
        task_id=task_id,
        run_id="pipeline-component-run-001",
        attempt_id="attempt-001",
        execution_outcome="SUCCEEDED",
        evaluation_outcome="PASSED",
        score=1.0,
        candidate_protocol=("paraguibench.pipeline-implicit.component-validation.v1"),
        task_evaluation_protocol=(
            "paraguibench.operation.image-classification.sha256.v1"
        ),
        environment_protocol="osworld.desktop.v1",
        attempt_version_vector_sha256="1" * 64,
        task_identity_sha256=identity.task_identity_sha256,
        environment_identity_sha256=identity.environment_identity_sha256,
        component_identity_sha256=identity.component_identity_sha256,
    ).to_dict()
    payload = (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
    receipt_root = repo / PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT
    receipt_root.mkdir(parents=True)
    (receipt_root / f"{task_id}.json").write_bytes(payload)
    allowlist = {
        "schema_version": 1,
        "receipts": {
            task_id: {
                "receipt_sha256": hashlib.sha256(payload).hexdigest(),
                "task_identity_sha256": identity.task_identity_sha256,
                "environment_identity_sha256": (identity.environment_identity_sha256),
                "component_identity_sha256": identity.component_identity_sha256,
            }
        },
    }
    allowlist_path = repo / PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ALLOWLIST_PATH
    allowlist_path.parent.mkdir(parents=True, exist_ok=True)
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def test_checked_in_ppt003_receipt_is_optional_audit_artifact() -> None:
    """确认 PPT-003 receipt 只是可选审计物，不作为普通评测门禁。

    输入参数：无；读取专用 allowlist、receipt，并派生当前身份。
    输出返回值：历史 1173 字节 receipt 仍在仓库中；当前身份可以
        与其不一致；公开 loader 因此不得把该任务标成 component-ready。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    expected_receipt_sha256 = (
        "cbf1f356c2dda1118490f45434e7f1546344a86a2647f8f40919c631ef458144"
    )
    historical_identity = {
        "task_identity_sha256": (
            "02ec464403701fecea61efa834e0ba4850ac44f5f7267d63041b7780a92f0e81"
        ),
        "environment_identity_sha256": (
            "96516cf34aaf408d769e8ea8b472776fa1086b9868688f36b5323921dbc1f10e"
        ),
        "component_identity_sha256": (
            "11578da71a8de5ba7f0ff344a87296bc941f7ea715a75870826e40acbcf9afbe"
        ),
    }
    allowlist_path = REPO_ROOT / PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ALLOWLIST_PATH
    receipt_root = REPO_ROOT / PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT
    receipt_path = receipt_root / f"{task_id}.json"
    payload = receipt_path.read_bytes()
    receipt = json.loads(payload)
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    current = derive_pipeline_implicit_component_identity(REPO_ROOT, task_id)

    assert len(payload) == 1173
    assert payload.endswith(b"\n") and payload.count(b"\n") == 1
    assert hashlib.sha256(payload).hexdigest() == expected_receipt_sha256
    assert set(receipt_root.iterdir()) == {receipt_path}
    assert allowlist == {
        "schema_version": 1,
        "receipts": {
            task_id: {
                "receipt_sha256": expected_receipt_sha256,
                **historical_identity,
            }
        },
    }
    assert {field: receipt[field] for field in historical_identity} == (
        historical_identity
    )
    assert current != PipelineImplicitComponentIdentity(**historical_identity)
    with pytest.raises(PipelineImplicitComponentReceiptError):
        load_trusted_pipeline_implicit_component_receipts(REPO_ROOT)
    assert len(PIPELINE_IMPLICIT_COMPONENT_TASK_IDS) == 3
    assert "Operation-FileOperate-SearchAndWrite-008" not in (
        PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
    )
    assert "Operation-FileOperate-Settings-001" not in (
        PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
    )


def test_ppt003_receipt_documentation_matches_the_formal_trust_boundary() -> None:
    """确认公开文档把 PPT-003 receipt 写成可选审计物，而不是普通评测门禁。

    输入参数：无；读取 provenance README 与总架构树。
    输出返回值：文档仍记录历史 1173 字节 receipt，但明确普通
        runtime-support / run 不消费它；四项 pipeline-live 均保留。
    """

    provenance = (REPO_ROOT / "benchmark/provenance/README.md").read_text(
        encoding="utf-8"
    )
    architecture_tree = (REPO_ROOT / "docs/architecture/dependency-tree.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "checked-in allowlist contains exactly BatchOperationPPT-003",
        "1173-byte",
        "cbf1f356c2dda1118490f45434e7f1546344a86a2647f8f40919c631ef458144",
        "optional official audit",
        "exactly four pipeline-live blockers remain",
    ):
        assert required in provenance
    assert (
        "BatchOperationPPT-003   [local ready; optional audit receipt "
        "not a production gate; pipeline-live + versioned-live remain]"
    ) in architecture_tree
    for forbidden in (
        "pipeline gate cleared; versioned gate remains",
        "exactly three pipeline-live blockers remain",
    ):
        assert forbidden not in provenance
        assert forbidden not in architecture_tree


def test_allowlist_rejects_symlinked_parent_directory(tmp_path: Path) -> None:
    """确认 allowlist 的每级父目录都必须来自 nofollow held dirfd 链。

    输入参数：tmp_path 提供仓库与仓库外 provenance 目录。
    输出返回值：即使外部 allowlist 内容为空且合法，父目录 symlink
        仍触发固定脱敏错误，不能被路径解析隐式跟随。
    """

    repo = tmp_path / "repo"
    external = tmp_path / "external-provenance"
    (repo / "benchmark").mkdir(parents=True)
    external.mkdir()
    (external / "pipeline-implicit-component-receipt-allowlist-v1.json").write_text(
        '{"receipts": {}, "schema_version": 1}\n',
        encoding="utf-8",
    )
    (repo / "benchmark" / "provenance").symlink_to(external, target_is_directory=True)

    with pytest.raises(PipelineImplicitComponentReceiptError) as caught:
        load_trusted_pipeline_implicit_component_receipts(repo)

    assert str(caught.value) == "PIPELINE_IMPLICIT_COMPONENT_RECEIPT_INVALID"


def test_component_identity_is_deterministic_and_task_scoped() -> None:
    """确认 task、环境与组件三层身份稳定且按任务隔离。

    输入参数：无；重复派生 PPT003，并与 Excel008 比较。
    输出返回值：同任务结果稳定；两个已实现 candidate 任务共享同一
        环境身份，但 task 与包含 task identity 的 component 身份不同，
        所有摘要均为 SHA-256。SearchAndWrite-008 不再进入 candidate 身份。
    """

    ppt_task_id = "Operation-FileOperate-BatchOperationPPT-003"
    other_task_id = "Operation-FileOperate-BatchOperationExcel-008"
    first = derive_pipeline_implicit_component_identity(REPO_ROOT, ppt_task_id)
    repeated = derive_pipeline_implicit_component_identity(REPO_ROOT, ppt_task_id)
    other = derive_pipeline_implicit_component_identity(REPO_ROOT, other_task_id)

    assert first == repeated
    assert first.task_identity_sha256 != other.task_identity_sha256
    assert first.environment_identity_sha256 == other.environment_identity_sha256
    assert first.component_identity_sha256 != other.component_identity_sha256
    for value in (
        first.task_identity_sha256,
        first.environment_identity_sha256,
        first.component_identity_sha256,
        other.task_identity_sha256,
        other.environment_identity_sha256,
        other.component_identity_sha256,
    ):
        assert len(value) == 64
        assert set(value) <= set("0123456789abcdef")

    with pytest.raises(PipelineImplicitComponentReceiptError):
        derive_pipeline_implicit_component_identity(
            REPO_ROOT,
            "Operation-FileOperate-Settings-001",
        )
    with pytest.raises(PipelineImplicitComponentReceiptError):
        derive_pipeline_implicit_component_identity(
            REPO_ROOT,
            "Operation-FileOperate-SearchAndWrite-008",
        )


def test_environment_identity_can_be_derived_from_same_parsed_manifest_snapshot() -> (
    None
):
    """确认首次 same-FD manifest 对象可直接进入 receipt gate。

    输入参数：无；加载一次 OSWorld image manifest 并独立派生完整身份。
    输出返回值：由对象内同源原始 SHA、protocol、qcow 与 OCI 声明组合的
        环境身份，精确等于仓库 identity 的环境层且无需再次读 manifest 路径。
    """

    manifest = load_osworld_image_manifest(
        REPO_ROOT / "environments/osworld/image-manifest.json"
    )
    from_snapshot = derive_pipeline_implicit_environment_identity(manifest)
    complete = derive_pipeline_implicit_component_identity(
        REPO_ROOT,
        "Operation-FileOperate-BatchOperationPPT-003",
    )

    assert from_snapshot == complete.environment_identity_sha256


def test_complete_identity_uses_supplied_manifest_snapshot_without_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认 candidate 三层身份不会重读 image manifest 路径。

    输入参数：monkeypatch 将路径环境身份派生替换为不可达哨兵。
    输出返回值：显式 supplied manifest 仍形成与普通派生相同的三层身份，
        证明 task/component 与首次 held environment object 同次闭合。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    expected = derive_pipeline_implicit_component_identity(REPO_ROOT, task_id)
    manifest = load_osworld_image_manifest(
        REPO_ROOT / "environments/osworld/image-manifest.json"
    )

    def reject_path_environment_identity(_repo_root: Path) -> str:
        """拒绝 supplied-snapshot 分支再次读取环境路径。

        输入参数：_repo_root 为不应使用的仓库根。
        输出返回值：不返回；调用即使测试失败。
        """

        raise AssertionError("candidate identity must not reread image manifest")

    monkeypatch.setattr(
        "paraguibench.runtime.pipeline_implicit_component_receipts._derive_environment_identity",
        reject_path_environment_identity,
    )

    observed = derive_pipeline_implicit_component_identity_for_environment(
        REPO_ROOT,
        task_id,
        manifest,
        expected_task=json.loads(
            (REPO_ROOT / f"benchmark/tasks/{task_id}.json").read_text(encoding="utf-8")
        ),
        expected_task_sha256=hashlib.sha256(
            (REPO_ROOT / f"benchmark/tasks/{task_id}.json").read_bytes()
        ).hexdigest(),
        expected_input_manifest_sha256=hashlib.sha256(
            (REPO_ROOT / f"benchmark/assets/manifests/{task_id}.json").read_bytes()
        ).hexdigest(),
        expected_reference_manifest_sha256=hashlib.sha256(
            (REPO_ROOT / f"benchmark/gold/manifests/{task_id}.json").read_bytes()
        ).hexdigest(),
        expected_reference_manifest_role="gold",
    )

    assert observed == expected

    with pytest.raises(PipelineImplicitComponentReceiptError):
        derive_pipeline_implicit_component_identity_for_environment(
            REPO_ROOT,
            task_id,
            manifest,
            expected_task=json.loads(
                (REPO_ROOT / f"benchmark/tasks/{task_id}.json").read_text(
                    encoding="utf-8"
                )
            ),
            expected_task_sha256="0" * 64,
            expected_input_manifest_sha256="1" * 64,
            expected_reference_manifest_sha256="2" * 64,
            expected_reference_manifest_role="gold",
        )


@pytest.mark.parametrize(
    ("task_id", "reference_relative", "reference_role"),
    (
        (
            "Operation-FileOperate-BatchOperationPPT-003",
            "benchmark/gold/manifests/Operation-FileOperate-BatchOperationPPT-003.json",
            "gold",
        ),
        (
            "Operation-FileOperate-BatchOperationExcel-008",
            "benchmark/gold/manifests/Operation-FileOperate-BatchOperationExcel-008.json",
            "gold",
        ),
        (
            "Operation-FileOperate-CombinationDocs-002",
            (
                "benchmark/provenance/pipeline-implicit-known-negative/"
                "Operation-FileOperate-CombinationDocs-002.json"
            ),
            "audit_known_negative",
        ),
    ),
)
def test_candidate_identity_rejects_semantically_equal_task_byte_swap(
    tmp_path: Path,
    task_id: str,
    reference_relative: str,
    reference_role: str,
) -> None:
    """candidate 必须把 PreparedTask 摘要绑定到当前 task 原始字节。

    输入参数：tmp_path 提供可交换 canonical task/release 的隔离仓库。
    输出返回值：prepare 持有 A 字节后，即使 B 只改变 JSON
        序列化且 release 已更新为 B，三层身份仍失败关闭。
    """

    repo = _copy_identity_repository(tmp_path, task_id)
    task_path = repo / f"benchmark/tasks/{task_id}.json"
    release_path = repo / "benchmark/manifests/release-v1.json"
    input_path = repo / f"benchmark/assets/manifests/{task_id}.json"
    reference_path = repo / reference_relative
    task_a_payload = task_path.read_bytes()
    task = json.loads(task_a_payload)
    task_b_payload = (
        json.dumps(
            task,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    assert task_b_payload != task_a_payload
    task_path.write_bytes(task_b_payload)
    release = json.loads(release_path.read_text(encoding="utf-8"))
    selected = next(entry for entry in release["tasks"] if entry["task_id"] == task_id)
    selected["sha256"] = hashlib.sha256(task_b_payload).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = load_osworld_image_manifest(
        repo / "environments/osworld/image-manifest.json"
    )

    with pytest.raises(PipelineImplicitComponentReceiptError):
        derive_pipeline_implicit_component_identity_for_environment(
            repo,
            task_id,
            manifest,
            expected_task=task,
            expected_task_sha256=hashlib.sha256(task_a_payload).hexdigest(),
            expected_input_manifest_sha256=hashlib.sha256(
                input_path.read_bytes()
            ).hexdigest(),
            expected_reference_manifest_sha256=hashlib.sha256(
                reference_path.read_bytes()
            ).hexdigest(),
            expected_reference_manifest_role=reference_role,
        )


def test_receipt_schema_and_projection_close_sensitive_field_surface() -> None:
    """确认 receipt 仅投影脱敏终态、协议、三身份与固定检查。

    输入参数：无；构造 PPT003 的合成成功 receipt 并读取公开 schema。
    输出返回值：class 与 schema 字段闭集一致，所有 component check
        精确为 ``passed``，且禁用正文、路径、Agent、gold 与 secret 字段。
    """

    identity = derive_pipeline_implicit_component_identity(
        REPO_ROOT,
        "Operation-FileOperate-BatchOperationPPT-003",
    )
    receipt = PipelineImplicitComponentReceipt(
        schema_version=1,
        receipt_kind="paraguibench.pipeline-implicit.component.v1",
        task_id="Operation-FileOperate-BatchOperationPPT-003",
        run_id="pipeline-component-run-001",
        attempt_id="attempt-001",
        execution_outcome="SUCCEEDED",
        evaluation_outcome="PASSED",
        score=1.0,
        candidate_protocol=("paraguibench.pipeline-implicit.component-validation.v1"),
        task_evaluation_protocol=(
            "paraguibench.operation.image-classification.sha256.v1"
        ),
        environment_protocol="osworld.desktop.v1",
        attempt_version_vector_sha256="1" * 64,
        task_identity_sha256=identity.task_identity_sha256,
        environment_identity_sha256=identity.environment_identity_sha256,
        component_identity_sha256=identity.component_identity_sha256,
    ).to_dict()
    schema = json.loads(RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    expected_fields = {
        "schema_version",
        "receipt_kind",
        "task_id",
        "run_id",
        "attempt_id",
        "execution_outcome",
        "evaluation_outcome",
        "score",
        "candidate_protocol",
        "task_evaluation_protocol",
        "environment_protocol",
        "attempt_version_vector_sha256",
        "task_identity_sha256",
        "environment_identity_sha256",
        "component_identity_sha256",
        "component_checks",
    }
    expected_checks = {
        "image_manifest_held",
        "qcow2_snapshot_verified",
        "container_image_verified",
        "task_prepare_completed",
        "reference_bundle_materialized",
        "typed_observation_captured",
        "task_evaluator_completed",
        "owned_environment_closed",
    }

    assert set(receipt) == expected_fields
    assert set(receipt["component_checks"]) == expected_checks
    assert set(receipt["component_checks"].values()) == {"passed"}
    receipt_schema = schema["$defs"]["receipt"]
    assert receipt_schema["additionalProperties"] is False
    assert set(receipt_schema["required"]) == expected_fields
    assert set(receipt_schema["properties"]) == expected_fields
    rendered = json.dumps(schema, sort_keys=True).lower()
    for forbidden in (
        "final_output",
        "final_text",
        "instruction",
        "prompt",
        "response",
        "api_key",
        "secret",
        "credential",
        "gold_body",
        "file_path",
        "qcow2_path",
        "container_id",
        "details",
        "events",
    ):
        assert forbidden not in rendered


def test_nonempty_allowlist_requires_current_receipt_and_three_way_identity(
    tmp_path: Path,
) -> None:
    """确认非空 allowlist 逐任务绑定 receipt/current/authorization 三方。

    输入参数：tmp_path 提供只含 PPT003 的隔离仓库闭集。
    输出返回值：当前 receipt 的文件摘要、receipt 内三身份、allowlist
        外置三身份与实时重算三身份全部相同后，仅返回该任务。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    _write_current_component_receipt(repo, task_id)
    manifest = load_osworld_image_manifest(
        repo / "environments/osworld/image-manifest.json"
    )
    expected_environment_identity = derive_pipeline_implicit_environment_identity(
        manifest
    )

    assert load_trusted_pipeline_implicit_component_receipts(
        repo,
        expected_environment_identity_sha256=expected_environment_identity,
    ) == frozenset({task_id})


def _replace_receipt_payload_and_digest(
    repo: Path,
    task_id: str,
    payload: bytes,
) -> None:
    """同步替换隔离仓库的 receipt 字节与外置摘要。

    输入参数：repo/task_id 定位已由测试 helper 创建的证据；
        payload 为待验证的畸形字节。
    输出返回值：无；receipt SHA 同步更新，使 loader 必须在
        JSON/字段合同而非只在外置摘要处拒绝。
    """

    receipt_path = repo / PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT / f"{task_id}.json"
    receipt_path.write_bytes(payload)
    allowlist_path = repo / PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ALLOWLIST_PATH
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowlist["receipts"][task_id]["receipt_sha256"] = hashlib.sha256(
        payload
    ).hexdigest()
    allowlist_path.write_text(
        json.dumps(allowlist, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "mutation",
    ("leaf_symlink", "hardlink", "oversize", "extra_member"),
)
def test_receipt_loader_rejects_unsafe_physical_closure(
    tmp_path: Path,
    mutation: str,
) -> None:
    """receipt 物理闭集拒绝链接、超限与额外成员。

    输入参数：tmp_path 提供隔离仓库；mutation 选择最终
        symlink、多硬链、超过 64 KiB 或未授权目录成员。
    输出返回值：四类物理反例均在信任集返回前抛出
        固定脱敏 receipt error。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    _write_current_component_receipt(repo, task_id)
    receipt_root = repo / PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT
    receipt_path = receipt_root / f"{task_id}.json"
    if mutation in {"leaf_symlink", "hardlink"}:
        external = tmp_path / f"{mutation}.json"
        external.write_bytes(receipt_path.read_bytes())
        receipt_path.unlink()
        if mutation == "leaf_symlink":
            receipt_path.symlink_to(external)
        else:
            os.link(external, receipt_path)
    elif mutation == "oversize":
        receipt_path.write_bytes(b" " * (64 * 1024 + 1))
    else:
        (receipt_root / "unexpected.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(PipelineImplicitComponentReceiptError):
        load_trusted_pipeline_implicit_component_receipts(repo)


@pytest.mark.parametrize("malformed", ("duplicate_key", "nan"))
def test_receipt_loader_rejects_noncanonical_json_values(
    tmp_path: Path,
    malformed: str,
) -> None:
    """receipt JSON 即使 SHA 已外置固定也拒绝重复键与 NaN。

    输入参数：tmp_path 提供隔离仓库；malformed 选择顶层
        重复 ``schema_version`` 或非有限 ``score``。
    输出返回值：外置 receipt SHA 同步后仍失败关闭。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    receipt = _write_current_component_receipt(repo, task_id)
    canonical = json.dumps(receipt, sort_keys=True)
    if malformed == "duplicate_key":
        payload = (canonical[:-1] + ',"schema_version":1}\n').encode("utf-8")
    else:
        payload = (
            canonical.replace('"score": 1.0', '"score": NaN').encode("utf-8") + b"\n"
        )
    _replace_receipt_payload_and_digest(repo, task_id, payload)

    with pytest.raises(PipelineImplicitComponentReceiptError):
        load_trusted_pipeline_implicit_component_receipts(repo)


def test_receipt_loader_rejects_receipt_read_aba(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """loader 必须在返回信任集前复验 receipt 同一字节。

    输入参数：tmp_path 构造当前有效 receipt；monkeypatch 使
        同一物理路径首读返回 A、后验返回语义相同但字节不同的 B。
    输出返回值：后验精确检测 A→B，不返回 task ready。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    _write_current_component_receipt(repo, task_id)
    receipt_relative = PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT / f"{task_id}.json"
    original_reader = receipt_module._read_repository_file
    receipt_reads = 0

    def unstable_reader(
        repo_root: Path,
        relative_path: Path,
        *,
        maximum_bytes: int,
    ) -> bytes:
        """在第二次 receipt 读取时返回多一个空格的 B 字节。

        输入参数：repo_root/relative_path/maximum_bytes 与正式 reader 一致。
        输出返回值：非 receipt 原样返回；receipt 首次 A、后续 B。
        """

        nonlocal receipt_reads
        payload = original_reader(
            repo_root,
            relative_path,
            maximum_bytes=maximum_bytes,
        )
        if relative_path == receipt_relative:
            receipt_reads += 1
            if receipt_reads > 1:
                return payload + b" "
        return payload

    monkeypatch.setattr(receipt_module, "_read_repository_file", unstable_reader)

    with pytest.raises(PipelineImplicitComponentReceiptError):
        load_trusted_pipeline_implicit_component_receipts(repo)
    assert receipt_reads == 2


def test_component_identity_covers_release_preparation_execution_code(
    tmp_path: Path,
) -> None:
    """确认 candidate 实际调用的 benchmark preparation 进入组件闭集。

    输入参数：tmp_path 提供可单独改动执行代码的隔离仓库。
    输出返回值：仅改变 ``prepare_release_task`` 所在源码时 task/env
        身份不变而 component 身份变化，旧 receipt 因而失效。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    repo = _copy_identity_repository(tmp_path, task_id)
    before = derive_pipeline_implicit_component_identity(repo, task_id)
    preparation = repo / "src/paraguibench/benchmark/preparation.py"
    preparation.write_bytes(preparation.read_bytes() + b"\n")
    after = derive_pipeline_implicit_component_identity(repo, task_id)

    assert after.task_identity_sha256 == before.task_identity_sha256
    assert after.environment_identity_sha256 == before.environment_identity_sha256
    assert after.component_identity_sha256 != before.component_identity_sha256
