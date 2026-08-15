"""pipeline implicit component 专属无 Agent candidate 回归测试。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess

import pytest

from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_PROTOCOL_ID,
    HIDE_NA_ROWS_TASK_ID,
    PINNED_HIDDEN_ROWS_BY_DOCUMENT,
    CrossDocumentObservation,
    HideNARowsObservation,
    NarrativeFacts,
    PresentationFacts,
    WorkbookHiddenRows,
    evaluate_cross_document,
    evaluate_hide_na_rows,
)
from paraguibench.integrations.osworld.controller import (
    CommandResult,
    OSWorldController,
)
from paraguibench.evaluation.pipeline_implicit import (
    CategorizedImage,
    ImageClassificationObservation,
    PresentationArtifact,
    PINNED_CLASSIFIED_IMAGE_SHA256,
    PINNED_PRESENTATION_SHA256,
    PINNED_UNCLASSIFIED_IMAGE_SHA256,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    load_osworld_image_manifest,
)
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    VerifiedPipelineImplicitGoldBundle,
    VerifiedPipelineImplicitGoldFile,
    load_verified_pipeline_implicit_gold_manifest,
)
from paraguibench.integrations.pipeline_implicit import (
    verified_assets as verified_assets_module,
)
from paraguibench.integrations.pipeline_implicit.artifact_evidence import (
    PipelineImplicitArtifactFile,
    PipelineImplicitArtifactEvidenceSource,
    PipelineImplicitArtifactObservation,
)
from paraguibench.integrations.pipeline_implicit.cross_document_bridge import (
    build_cross_document_observation,
)
from paraguibench.integrations.pipeline_implicit.hide_na_rows_bridge import (
    build_hide_na_rows_observation,
)
from paraguibench.runtime import (
    pipeline_implicit_binding as pipeline_binding_module,
    pipeline_implicit_component_candidate as candidate_module,
)
from paraguibench.runtime.assets import resolve_task_assets
from paraguibench.runtime.evaluators import PipelineImplicitTaskEvaluator
from paraguibench.runtime.osworld_attested_qcow2 import (
    OSWorldAttestedDockerSession,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    PipelineImplicitComponentReceipt,
)
from paraguibench.runtime.pipeline_implicit_component_candidate import (
    PipelineImplicitComponentCandidateConfig,
    PipelineImplicitComponentCandidateError,
    run_pipeline_implicit_component_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
_EXCEL008_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_EXCEL008_FIXTURE_ROOT"
_EXCEL008_TASK_UID = "1c73128f-a5ef-4a97-97ce-ef427d6d46b4"
_COMBINATION002_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_COMBINATION002_FIXTURE_ROOT"
_COMBINATION002_TASK_UID = "6bf5b1c9-a2a2-4901-bbe3-631a33da45e8"


def _load_isolated_verified_image(tmp_path: Path) -> OSWorldImageManifest:
    """从正式 pending recipe 构造仅供 candidate 单测的 verified 副本。

    输入参数：tmp_path 为 pytest 隔离目录。
    输出返回值：严格 loader 从隔离字节产生的 live-ready
        快照；不改动正式 pending manifest、任务题面或 input。
    """

    raw = json.loads(
        (REPO_ROOT / "environments/osworld/image-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    raw["extracted_image"]["status"] = "verified_reproducible_materialization"
    candidate = tmp_path / "verified-image-manifest.json"
    candidate.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    image = load_osworld_image_manifest(candidate)
    assert image.live_run_ready
    return image


def _config(
    tmp_path: Path,
    *,
    task_id: str = "Operation-FileOperate-BatchOperationPPT-003",
    ready_timeout: float = 360.0,
) -> PipelineImplicitComponentCandidateConfig:
    """构造不含 Agent、凭据或执行依赖注入的冻结配置。

    输入参数：tmp_path 为 repo 外测试根；task_id 为待验证 canonical ID；
        ready_timeout 为 guest 就绪期限候选值。
    输出返回值：字段形状有效时返回 candidate config。
    """

    qcow2_path = tmp_path / "System.qcow2"
    qcow2_path.write_bytes(b"not-a-live-image")
    return PipelineImplicitComponentCandidateConfig(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        asset_cache_root=tmp_path / "assets",
        gold_cache_root=tmp_path / "gold",
        qcow2_path=qcow2_path,
        task_id=task_id,
        run_id="run-pipeline-component",
        attempt_id="attempt-001",
        server_port=55111,
        vnc_port=58111,
        chromium_port=59222,
        ready_timeout=ready_timeout,
    )


def test_candidate_api_has_no_agent_or_dependency_injection_parameters() -> None:
    """确认正式 issuer 只有冻结 config，不能注入证据或执行替身。

    输入参数：无；读取公开 runner 签名与 config 字段。
    输出返回值：无 Agent/final/evaluator/environment/controller/session/
        factory/proof/image override、receipt output 或凭据入口。
    """

    assert tuple(
        inspect.signature(run_pipeline_implicit_component_candidate).parameters
    ) == ("config",)
    fields = set(PipelineImplicitComponentCandidateConfig.__dataclass_fields__)
    assert not fields.intersection(
        {
            "agent",
            "final_text",
            "evaluator",
            "environment",
            "controller",
            "docker_session",
            "session_factory",
            "proof",
            "image_manifest",
            "container_image",
            "api_key",
            "endpoint",
            "receipt_output",
        }
    )


@pytest.mark.parametrize("ready_timeout", (math.nan, math.inf, -math.inf))
def test_candidate_config_rejects_non_finite_ready_timeout(
    tmp_path: Path,
    ready_timeout: float,
) -> None:
    """确认 NaN/Infinity 无法进入 owned guest 等待生命周期。

    输入参数：tmp_path 提供隔离路径；ready_timeout 为非有限浮点值。
    输出返回值：构造阶段抛固定脱敏错误。
    """

    with pytest.raises(PipelineImplicitComponentCandidateError) as captured:
        _config(tmp_path, ready_timeout=ready_timeout)

    assert str(captured.value) == "PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_INVALID"


def test_searchwrite_is_outside_component_candidate_closed_set(
    tmp_path: Path,
) -> None:
    """确认 SearchWrite 在构造阶段就被排除出无 Agent candidate。

    输入参数：tmp_path 提供不会落盘的 RunStore。
    输出返回值：配置构造即失败，不进入 preflight、RunStore 或 VM。
    """

    with pytest.raises(PipelineImplicitComponentCandidateError) as captured:
        _config(tmp_path, task_id="Operation-FileOperate-SearchAndWrite-008")

    assert str(captured.value) == "PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_INVALID"
    assert not (tmp_path / "runs").exists()


def _fixed_revision_fixture(
    *,
    environment_variable: str,
    task_uid: str,
) -> Path:
    """返回显式配置的固定 revision input fixture。

    输入参数：environment_variable 为下载专用根目录变量；
        task_uid 为当前 canonical 任务的固定 UID。
    输出返回值：真实 ``benchmark_dataset/<uid>`` 目录；未显式
        配置时跳过 download-only 回归。
    """

    raw_root = os.environ.get(environment_variable)
    if raw_root is None:
        pytest.skip(f"{environment_variable} is required for download-only fixture")
    fixture = Path(raw_root) / "benchmark_dataset" / task_uid
    if not fixture.is_dir():
        pytest.fail("fixed-revision pipeline input fixture is unavailable")
    return fixture


def _artifact_observation_from_directory(
    *,
    task_id: str,
    protocol_id: str,
    root: Path,
) -> PipelineImplicitArtifactObservation:
    """把真实固定资产目录投影为 production generic observation。

    输入参数：task_id/protocol_id 为 typed 评价身份；root 为当前
        已物化的完整 input-only 文件闭集。
    输出返回值：按 UTF-8 路径序冻结且与 payload SHA 同源的
        ``PipelineImplicitArtifactObservation``。
    """

    files = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.encode("utf-8")):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        files.append(
            PipelineImplicitArtifactFile(
                relative_path=path.name,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                _payload=payload,
            )
        )
    return PipelineImplicitArtifactObservation(
        task_id=task_id,
        protocol_id=protocol_id,
        complete=True,
        _files=tuple(files),
    )


def _copy_candidate_repository(tmp_path: Path, task_id: str) -> Path:
    """复制 candidate 顶层生命周期需要的最小仓库闭集。

    输入参数：tmp_path 为隔离根；task_id 仅允许 Excel-008/Combo-002。
    输出返回值：包含当前 source/schema/task/input/reference/image 的独立
        checkout；仅在副本中同步 selected release 与 runtime-support SHA。
    """

    if task_id not in {HIDE_NA_ROWS_TASK_ID, CROSS_DOCUMENT_TASK_ID}:
        raise AssertionError("candidate fixture task is not registered")
    repo = tmp_path / f"candidate-repo-{task_id}"
    shutil.copytree(REPO_ROOT / "src/paraguibench", repo / "src/paraguibench")
    shutil.copytree(REPO_ROOT / "benchmark/schemas", repo / "benchmark/schemas")
    reference_relative = (
        f"benchmark/gold/manifests/{task_id}.json"
        if task_id == HIDE_NA_ROWS_TASK_ID
        else (f"benchmark/provenance/pipeline-implicit-known-negative/{task_id}.json")
    )
    for relative in (
        "pyproject.toml",
        "scripts/benchmark/runtime_support_manifest.py",
        "benchmark/manifests/release-v1.json",
        "benchmark/manifests/runtime-support-v1.json",
        f"benchmark/tasks/{task_id}.json",
        f"benchmark/assets/manifests/{task_id}.json",
        reference_relative,
        "environments/osworld/image-manifest.json",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    task_path = repo / f"benchmark/tasks/{task_id}.json"
    release_path = repo / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    selected = next(item for item in release["tasks"] if item["task_id"] == task_id)
    selected["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    support_path = repo / "benchmark/manifests/runtime-support-v1.json"
    support = json.loads(support_path.read_text(encoding="utf-8"))
    support["release_manifest_sha256"] = hashlib.sha256(
        release_path.read_bytes()
    ).hexdigest()
    support_path.write_text(
        json.dumps(support, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    image_path = repo / "environments/osworld/image-manifest.json"
    image = json.loads(image_path.read_text(encoding="utf-8"))
    image["extracted_image"]["status"] = "verified_reproducible_materialization"
    image_path.write_text(
        json.dumps(image, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return repo


def _perfect_typed_observation(task_id: str) -> object:
    """构造只由正式评价内部语义表达的满分 typed 观测。

    输入参数：task_id 为 Excel-008 或 Combo-002。
    输出返回值：对应协议的精确 observation 类型；该 helper 只用于
        隔离外部 GUI 捕获，candidate 仍调用真实 typed evaluator。
    """

    if task_id == HIDE_NA_ROWS_TASK_ID:
        return HideNARowsObservation(
            complete=True,
            workbooks=tuple(
                WorkbookHiddenRows(name, rows, True)
                for name, rows in PINNED_HIDDEN_ROWS_BY_DOCUMENT.items()
            ),
        )
    if task_id == CROSS_DOCUMENT_TASK_ID:
        return CrossDocumentObservation(
            complete=True,
            reference_spreadsheet_unchanged=True,
            narrative=NarrativeFacts(
                january_profit=47_109,
                strongest_profit_order=("july", "december", "january"),
                other_facts_match_reference=True,
            ),
            presentation=PresentationFacts(
                january_customers=1_895,
                other_facts_match_reference=True,
            ),
            unexpected_document_count=0,
        )
    raise AssertionError("typed observation task is not registered")


def test_ppt003_reference_plan_is_derived_from_verified_input_only() -> None:
    """确认 guest reference 动作不携带 gold bundle 或任意外部 payload。

    输入参数：无；读取 canonical task 的正式 input/gold manifest。
    输出返回值：私有计划恰含十二个 source→category copy，全部 source
        来自 input manifest；gold 只参与 host 闭集等值验证。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )
    assets = resolve_task_assets(REPO_ROOT, task)
    assert assets.manifest is not None
    gold_manifest = load_verified_pipeline_implicit_gold_manifest(
        (REPO_ROOT / task["gold_manifest"]).read_bytes()
    )

    plan = candidate_module._build_ppt003_reference_copy_plan(
        input_manifest=assets.manifest,
        gold_manifest=gold_manifest,
    )

    input_paths = {item.path for item in assets.manifest.files}
    assert len(plan) == 12
    assert all(source in input_paths for source, _destination, _digest in plan)
    assert {destination.split("/", 1)[0] for _, destination, _ in plan} == {
        "basketball",
        "esport",
        "soccer",
        "volleyball",
    }
    assert all(destination not in input_paths for _, destination, _ in plan)


def test_reference_materializer_only_copies_already_uploaded_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确认 candidate 不把 gold bundle、manifest 或正文上传 guest。

    输入参数：monkeypatch 记录 production controller 的结构化 argv。
    输出返回值：仅出现固定 mkdir/cp/sha256sum；upload_file 不可达，且
        copy source 全部位于已 prepare 的 shared input 根。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )
    assets = resolve_task_assets(REPO_ROOT, task)
    assert assets.manifest is not None
    gold_manifest = load_verified_pipeline_implicit_gold_manifest(
        (REPO_ROOT / task["gold_manifest"]).read_bytes()
    )
    plan = candidate_module._build_ppt003_reference_copy_plan(
        input_manifest=assets.manifest,
        gold_manifest=gold_manifest,
    )
    expected_digest_by_destination = {
        f"/home/oai/share/{destination}": digest
        for _source, destination, digest in plan
    }
    commands: list[tuple[str, ...]] = []
    controller = OSWorldController("http://127.0.0.1:55111")

    def record_execute(command: object) -> CommandResult:
        """记录结构化 argv，并为目标摘要返回精确预期值。

        输入参数：command 为 materializer 发出的非 shell argv。
        输出返回值：mkdir/cp 成功；sha256sum 返回目标对应摘要。
        """

        assert isinstance(command, list)
        argv = tuple(command)
        commands.append(argv)
        if argv[0] == "sha256sum":
            digest = expected_digest_by_destination[argv[-1]]
            return CommandResult(0, f"{digest}  artifact\n", "")
        return CommandResult(0, "", "")

    def reject_upload(_local_path: Path, _guest_path: str) -> None:
        """拒绝 candidate 在 prepare 后再次上传任何 host/gold 文件。

        输入参数：两个参数均为不可达哨兵。
        输出返回值：不返回；调用即使测试失败。
        """

        raise AssertionError("reference materializer must not upload files")

    monkeypatch.setattr(controller, "execute", record_execute)
    monkeypatch.setattr(controller, "upload_file", reject_upload)

    candidate_module._materialize_ppt003_reference_result(
        controller=controller,
        guest_shared_dir="/home/oai/share",
        copy_plan=plan,
    )

    assert {argv[0] for argv in commands} == {"mkdir", "cp", "sha256sum"}
    assert sum(argv[0] == "cp" for argv in commands) == 12
    assert all("gold" not in " ".join(argv).casefold() for argv in commands)
    assert all(
        argv[2].startswith("/home/oai/share/images/")
        for argv in commands
        if argv[0] == "cp"
    )


def test_excel008_plan_contains_only_actual_input_names() -> None:
    """确认 Excel candidate 计划不携带 gold payload 或隐藏行答案。

    输入参数：无；读取 canonical 已授权的五 input/五 gold manifest。
    输出返回值：内部计划仅包含五个实际 input 文件名，不包含
        gold 路径、payload、固定行号或可持久答案字段。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{HIDE_NA_ROWS_TASK_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assets = resolve_task_assets(REPO_ROOT, task)
    assert assets.manifest is not None
    gold_manifest = load_verified_pipeline_implicit_gold_manifest(
        (REPO_ROOT / task["gold_manifest"]).read_bytes()
    )

    plan = candidate_module._build_excel008_input_only_plan(
        input_manifest=assets.manifest,
        gold_manifest=gold_manifest,
    )

    assert plan.task_id == HIDE_NA_ROWS_TASK_ID
    assert plan.input_paths == tuple(item.path for item in assets.manifest.files)
    assert plan.input_paths == tuple(PINNED_HIDDEN_ROWS_BY_DOCUMENT)
    assert plan.ppt_copy_plan == ()
    rendered = repr(plan).casefold()
    for forbidden in ("gold", "manifest", "row", "n/a", ".xlsx"):
        assert forbidden not in rendered


def test_combination002_plan_uses_only_three_actual_inputs() -> None:
    """确认 Combo candidate 计划以 XLSX 同批事实而非 HF answer 为源。

    输入参数：无；读取 canonical 授权的三个 input manifest 成员。
    输出返回值：计划只有 DOCX/XLSX/PPTX input 名，不含历史
        known-negative manifest、错误值或 answer payload。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{CROSS_DOCUMENT_TASK_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assets = resolve_task_assets(REPO_ROOT, task)
    assert assets.manifest is not None

    plan = candidate_module._build_combination002_input_only_plan(assets.manifest)

    assert plan.task_id == CROSS_DOCUMENT_TASK_ID
    assert plan.input_paths == tuple(item.path for item in assets.manifest.files)
    assert plan.ppt_copy_plan == ()
    rendered = repr(plan).casefold()
    for forbidden in (
        "gold",
        "known_negative",
        "answer",
        "45324",
        "3602",
        ".docx",
        ".xlsx",
        ".pptx",
    ):
        assert forbidden not in rendered


def test_excel008_real_input_only_materializer_passes_typed_evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证五份真实 input 在无 gold guest 中形成满分语义结果。

    输入参数：tmp_path 容纳可变副本；monkeypatch 仅把 production
        controller 的 loopback argv 定向本地子进程，不注入评价结果。
    输出返回值：五 input 经通用字面 ``N/A`` 逻辑处理后，真实
        typed bridge/evaluator 得分 1.0；无 N/A 的文件字节不变。
    """

    fixture = _fixed_revision_fixture(
        environment_variable=_EXCEL008_FIXTURE_ENVIRONMENT_VARIABLE,
        task_uid=_EXCEL008_TASK_UID,
    )
    guest_root = tmp_path / "excel-input"
    shutil.copytree(fixture, guest_root)
    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{HIDE_NA_ROWS_TASK_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assets = resolve_task_assets(REPO_ROOT, task)
    assert assets.manifest is not None
    gold_manifest = load_verified_pipeline_implicit_gold_manifest(
        (REPO_ROOT / task["gold_manifest"]).read_bytes()
    )
    plan = candidate_module._build_excel008_input_only_plan(
        input_manifest=assets.manifest,
        gold_manifest=gold_manifest,
    )
    unchanged_path = guest_root / "Mixue_Monthly_Data.xlsx"
    unchanged_before = hashlib.sha256(unchanged_path.read_bytes()).hexdigest()
    commands: list[tuple[str, ...]] = []
    controller = OSWorldController("http://127.0.0.1:55111")

    def execute_locally(command: object) -> CommandResult:
        """执行 candidate 的 shell-free Python argv 并保留记录。

        输入参数：command 为 production materializer 构造的列表参数。
        输出返回值：子进程的脱敏 returncode/stdout/stderr 投影。
        """

        assert isinstance(command, list)
        argv = tuple(command)
        commands.append(argv)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def reject_upload(*_args: object, **_kwargs: object) -> None:
        """拒绝 materializer 额外上传 host/gold 文件。

        输入参数：任意不可达 upload 参数。
        输出返回值：不返回；被调用即失败。
        """

        raise AssertionError("input-only materializer must not upload files")

    monkeypatch.setattr(controller, "execute", execute_locally)
    monkeypatch.setattr(controller, "upload_file", reject_upload)

    candidate_module._materialize_input_only_reference_result(
        controller=controller,
        guest_shared_dir=guest_root.as_posix(),
        plan=plan,
    )

    observation = build_hide_na_rows_observation(
        _artifact_observation_from_directory(
            task_id=HIDE_NA_ROWS_TASK_ID,
            protocol_id=HIDE_NA_ROWS_PROTOCOL_ID,
            root=guest_root,
        )
    )
    result = evaluate_hide_na_rows(observation)
    assert result.passed is True
    assert result.score == 1.0
    assert hashlib.sha256(unchanged_path.read_bytes()).hexdigest() == unchanged_before
    assert len(commands) == 5
    assert all(argv[:3] == ("python3", "-I", "-c") for argv in commands)
    rendered_commands = "\n".join(" ".join(argv) for argv in commands).casefold()
    for forbidden in ("gold", "answer_files", "known_negative", "45324", "3602"):
        assert forbidden not in rendered_commands


def test_combination002_real_input_only_materializer_passes_typed_evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证三份真实 input 仅从 XLSX 事实生成满分跨文档结果。

    输入参数：tmp_path 容纳 input 副本；monkeypatch 仅将结构化 argv
        定向本地进程，不注入 gold、known-negative 或 typed 观测。
    输出返回值：XLSX 字节不变，DOCX/PPTX 经同批 XLSX 派生修正，
        真实 typed bridge/evaluator 得分 1.0。
    """

    fixture = _fixed_revision_fixture(
        environment_variable=_COMBINATION002_FIXTURE_ENVIRONMENT_VARIABLE,
        task_uid=_COMBINATION002_TASK_UID,
    )
    guest_root = tmp_path / "combination-input"
    shutil.copytree(fixture, guest_root)
    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{CROSS_DOCUMENT_TASK_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assets = resolve_task_assets(REPO_ROOT, task)
    assert assets.manifest is not None
    plan = candidate_module._build_combination002_input_only_plan(assets.manifest)
    xlsx_path = guest_root / "McDonalds_Monthly_Data.xlsx"
    xlsx_before = hashlib.sha256(xlsx_path.read_bytes()).hexdigest()
    commands: list[tuple[str, ...]] = []
    controller = OSWorldController("http://127.0.0.1:55111")

    def execute_locally(command: object) -> CommandResult:
        """执行单条 input-only Combo argv 并记录完整参数。

        输入参数：command 是 production materializer 的 shell-free argv。
        输出返回值：真实子进程脱敏结果。
        """

        assert isinstance(command, list)
        argv = tuple(command)
        commands.append(argv)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    def reject_upload(*_args: object, **_kwargs: object) -> None:
        """拒绝任何历史 answer 或 host payload 上传。

        输入参数：任意不可达 upload 参数。
        输出返回值：不返回；被调用即失败。
        """

        raise AssertionError("input-only materializer must not upload files")

    monkeypatch.setattr(controller, "execute", execute_locally)
    monkeypatch.setattr(controller, "upload_file", reject_upload)

    candidate_module._materialize_input_only_reference_result(
        controller=controller,
        guest_shared_dir=guest_root.as_posix(),
        plan=plan,
    )

    observation = build_cross_document_observation(
        _artifact_observation_from_directory(
            task_id=CROSS_DOCUMENT_TASK_ID,
            protocol_id=CROSS_DOCUMENT_PROTOCOL_ID,
            root=guest_root,
        )
    )
    result = evaluate_cross_document(observation)
    assert result.passed is True
    assert result.score == 1.0
    assert hashlib.sha256(xlsx_path.read_bytes()).hexdigest() == xlsx_before
    assert len(commands) == 1
    assert commands[0][:3] == ("python3", "-I", "-c")
    rendered_command = " ".join(commands[0]).casefold()
    for forbidden in (
        "gold",
        "known_negative",
        "answer_files",
        "45324",
        "3602",
        "december-before-july",
    ):
        assert forbidden not in rendered_command


@pytest.mark.parametrize(
    ("task_id", "fixture_environment_variable", "task_uid"),
    (
        (
            HIDE_NA_ROWS_TASK_ID,
            _EXCEL008_FIXTURE_ENVIRONMENT_VARIABLE,
            _EXCEL008_TASK_UID,
        ),
        (
            CROSS_DOCUMENT_TASK_ID,
            _COMBINATION002_FIXTURE_ENVIRONMENT_VARIABLE,
            _COMBINATION002_TASK_UID,
        ),
    ),
)
def test_excel_and_combo_top_level_candidate_keeps_reference_host_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    task_id: str,
    fixture_environment_variable: str,
    task_uid: str,
) -> None:
    """验证 Excel/Combo 顶层 candidate 不向 guest/RunStore/receipt 泄漏 reference。

    输入参数：tmp_path 提供临时 checkout/cache/RunStore；monkeypatch
        仅替换 Docker/HTTP/GUI 外部 I/O；其余三项定位真实固定 input。
    输出返回值：真实 prepare、严格 capability、typed evaluator、owned close、
        RunStore 双 inspection 与 receipt 全链通过；上传闭集只有 input，final
        精确为空，任何 gold/known-negative 路径、摘要或错值都未持久化；
        Attempt 后价交换 canonical+release 的 ABA 不能返回 receipt。
    """

    input_fixture = _fixed_revision_fixture(
        environment_variable=fixture_environment_variable,
        task_uid=task_uid,
    )
    fixture_root = input_fixture.parents[1]
    repo = _copy_candidate_repository(tmp_path, task_id)
    asset_cache_root = tmp_path / f"asset-cache-{task_id}"
    shutil.copytree(input_fixture, asset_cache_root / task_id)
    if task_id == HIDE_NA_ROWS_TASK_ID:
        gold_cache_root = fixture_root / "answer_files" / task_uid
    else:
        gold_cache_root = tmp_path / "unused-combo-gold-cache"
        gold_cache_root.mkdir()
    qcow2_path = tmp_path / f"{task_id}.qcow2"
    qcow2_path.write_bytes(b"external-io-is-stubbed")
    config = PipelineImplicitComponentCandidateConfig(
        repo_root=repo,
        runs_root=tmp_path / f"runs-{task_id}",
        asset_cache_root=asset_cache_root,
        gold_cache_root=gold_cache_root,
        qcow2_path=qcow2_path,
        task_id=task_id,
        run_id="run-pipeline-component",
        attempt_id="attempt-001",
        server_port=55111,
        vnc_port=58111,
        chromium_port=59222,
    )
    live_image = load_osworld_image_manifest(
        repo / "environments/osworld/image-manifest.json"
    )
    task = json.loads(
        (repo / "benchmark/tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )
    assets = resolve_task_assets(repo, task)
    assert assets.manifest is not None
    input_sha_by_name = {item.path: item.sha256 for item in assets.manifest.files}
    uploaded_sources: list[Path] = []
    commands: list[tuple[str, ...]] = []
    final_texts: list[str] = []
    lifecycle: list[str] = []
    original_evaluate = PipelineImplicitTaskEvaluator.evaluate

    def fake_start(self: object) -> str:
        """模拟唯一 owned Docker 外部启动。

        输入参数：self 为生产 attested wrapper。
        输出返回值：合法容器 ID。
        """

        setattr(self, "_started", True)
        lifecycle.append("start")
        return "d" * 12

    def fake_close(self: object) -> None:
        """模拟 owned close 并固定后验时序。

        输入参数：self 为同一 attested wrapper。
        输出返回值：无；记录 close 先于 attestation。
        """

        setattr(self, "_started", False)
        setattr(self, "_closed", True)
        lifecycle.append("close")

    def fake_attestation(
        self: object,
        *,
        container_image: str,
        extracted_qcow2_sha256: str,
    ) -> bool:
        """验证 close 后仍使用 held image 的 OCI/qcow 声明。

        输入参数：self 为同一 wrapper；其余为候选核验身份。
        输出返回值：严格 close 后且两字段非空时返回真。
        """

        lifecycle.append("attest")
        return (
            lifecycle[-2:] == ["close", "attest"]
            and isinstance(container_image, str)
            and "@sha256:" in container_image
            and isinstance(extracted_qcow2_sha256, str)
            and len(extracted_qcow2_sha256) == 64
        )

    def record_upload(
        _self: OSWorldController,
        local_path: Path,
        guest_path: str,
    ) -> None:
        """记录 environment.prepare 上传的 host 来源。

        输入参数：精确 controller、host Path 与 guest 绝对路径。
        输出返回值：无；仅记录，不上传外部字节。
        """

        assert guest_path.startswith("/home/oai/shared/")
        uploaded_sources.append(local_path)

    def fake_execute(_self: OSWorldController, command: object) -> CommandResult:
        """模拟 prepare/materializer 的 shell-free 结构化 argv。

        输入参数：_self 为精确 controller；command 为记录的 argv。
        输出返回值：input SHA、文件闭集或 materializer 固定回执。
        """

        assert isinstance(command, list)
        argv = tuple(command)
        commands.append(argv)
        if argv[0] == "sha256sum":
            name = PurePosixPath(argv[-1]).name
            return CommandResult(0, f"{input_sha_by_name[name]}  artifact\n", "")
        if argv[0] == "find":
            return CommandResult(
                0,
                "".join(f"f\t{name}\n" for name in sorted(input_sha_by_name)),
                "",
            )
        if argv[:3] == ("python3", "-I", "-c"):
            if task_id == HIDE_NA_ROWS_TASK_ID:
                name = PurePosixPath(argv[-1]).name
                return CommandResult(
                    0,
                    f"OK:{len(PINNED_HIDDEN_ROWS_BY_DOCUMENT[name])}\n",
                    "",
                )
            return CommandResult(0, "OK\n", "")
        return CommandResult(0, "", "")

    def fake_capture(
        _self: PipelineImplicitArtifactEvidenceSource,
        observed_task_id: str,
        observed_controller: object,
        *,
        guest_shared_dir: str | None,
    ) -> object:
        """仅隔离外部 GUI capture，保留真实 evaluator 调用。

        输入参数：任务、production controller 与 prepare 冻结 guest 根。
        输出返回值：与任务协议精确类型匹配的 typed observation。
        """

        assert observed_task_id == task_id
        assert type(observed_controller) is OSWorldController
        assert guest_shared_dir == "/home/oai/shared"
        return _perfect_typed_observation(task_id)

    def observe_evaluate(
        self: PipelineImplicitTaskEvaluator,
        observed_task: dict[str, object],
        final_text: str,
        environment: object,
    ) -> object:
        """确认顶层 issuer 不用 Agent final text 当证据。

        输入参数：与正式 evaluator.evaluate 完全同形。
        输出返回值：转发真实 evaluator 的 RuntimeEvaluation。
        """

        final_texts.append(final_text)
        return original_evaluate(self, observed_task, final_text, environment)

    monkeypatch.setattr(OSWorldAttestedDockerSession, "start", fake_start)
    monkeypatch.setattr(OSWorldAttestedDockerSession, "close", fake_close)
    monkeypatch.setattr(
        OSWorldAttestedDockerSession,
        "attests_closed_manifest",
        fake_attestation,
    )
    monkeypatch.setattr(OSWorldController, "wait_until_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        OSWorldController,
        "get_desktop_path",
        lambda _self: "/home/oai/Desktop",
    )
    monkeypatch.setattr(OSWorldController, "upload_file", record_upload)
    monkeypatch.setattr(OSWorldController, "execute", fake_execute)
    monkeypatch.setattr(OSWorldController, "open_path", lambda *_a, **_k: None)
    monkeypatch.setattr(PipelineImplicitArtifactEvidenceSource, "capture", fake_capture)
    monkeypatch.setattr(PipelineImplicitTaskEvaluator, "evaluate", observe_evaluate)
    if task_id == CROSS_DOCUMENT_TASK_ID:
        monkeypatch.setattr(
            verified_assets_module,
            "resolve_pipeline_implicit_known_negative_bundle",
            lambda *_args, **_kwargs: pytest.fail(
                "audit known-negative payload must not enter candidate"
            ),
        )
    monkeypatch.setattr(
        candidate_module,
        "load_osworld_image_manifest_with_sha256",
        lambda _path: (live_image, str(live_image.manifest_sha256)),
    )

    receipt = run_pipeline_implicit_component_candidate(config)

    assert type(receipt) is PipelineImplicitComponentReceipt
    assert receipt.task_id == task_id
    assert receipt.score == 1.0
    assert final_texts == [""]
    assert lifecycle[-2:] == ["close", "attest"]
    assert {path.name for path in uploaded_sources} == set(input_sha_by_name)
    assert all(path.parent == asset_cache_root / task_id for path in uploaded_sources)
    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in config.runs_root.rglob("*.json")
    )
    rendered_receipt = json.dumps(receipt.to_dict(), sort_keys=True)
    rendered_commands = "\n".join("\x00".join(command) for command in commands)
    combined = persisted + rendered_receipt + rendered_commands
    forbidden = {
        "gold_manifest",
        "known_negative_manifest",
        "answer_files",
        "PRIVATE-FINAL-TEXT-SENTINEL",
        "45324",
        "3602",
    }
    if task_id == HIDE_NA_ROWS_TASK_ID:
        forbidden.update(
            entry.sha256
            for entry in load_verified_pipeline_implicit_gold_manifest(
                (repo / task["gold_manifest"]).read_bytes()
            ).entries
        )
    else:
        negative = json.loads(
            (
                repo
                / "benchmark/provenance/pipeline-implicit-known-negative"
                / f"{task_id}.json"
            ).read_text(encoding="utf-8")
        )
        forbidden.update(entry["sha256"] for entry in negative["entries"])
    assert all(value not in combined for value in forbidden)

    original_run_attempt = candidate_module._run_candidate_attempt

    def mutate_task_after_attempt(**kwargs: object) -> object:
        """在完成真实 typed Attempt 后交换 task/release 字节。

        输入参数：kwargs 为顶层 issuer 传入的完整内部生产对象。
        输出返回值：先返回原始 AttemptInspection，同时将语义相同但
            原始字节不同的 canonical B 及其 selected release SHA 落盘。
        """

        inspection = original_run_attempt(**kwargs)
        task_path = repo / "benchmark/tasks" / f"{task_id}.json"
        release_path = repo / "benchmark/manifests/release-v1.json"
        task_payload_a = task_path.read_bytes()
        task_payload_b = (
            json.dumps(
                json.loads(task_payload_a),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        assert task_payload_b != task_payload_a
        task_path.write_bytes(task_payload_b)
        release = json.loads(release_path.read_text(encoding="utf-8"))
        selected = next(
            entry for entry in release["tasks"] if entry["task_id"] == task_id
        )
        selected["sha256"] = hashlib.sha256(task_payload_b).hexdigest()
        release_path.write_text(
            json.dumps(release, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return inspection

    monkeypatch.setattr(
        candidate_module,
        "_run_candidate_attempt",
        mutate_task_after_attempt,
    )
    aba_config = replace(
        config,
        runs_root=tmp_path / f"runs-aba-{task_id}",
        run_id="run-pipeline-component-aba",
    )
    with pytest.raises(PipelineImplicitComponentCandidateError) as captured:
        run_pipeline_implicit_component_candidate(aba_config)
    assert str(captured.value) == "PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_INVALID"


def test_candidate_runs_real_typed_chain_and_issues_only_after_owned_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 top-level issuer 的真实类型链和 close 后发证顺序。

    输入参数：tmp_path 提供隔离 RunStore/cache/qcow；monkeypatch 只替换
        Docker/HTTP 外部 I/O，不注入公开 candidate 依赖。
    输出返回值：真实 environment、evidence source 与正式 evaluator 形成
        1.0，attested close 后返回 receipt；此 unit receipt 不进 allowlist。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    task = json.loads(
        (REPO_ROOT / "benchmark/tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )
    assets = resolve_task_assets(REPO_ROOT, task)
    assert assets.manifest is not None
    gold_manifest = load_verified_pipeline_implicit_gold_manifest(
        (REPO_ROOT / task["gold_manifest"]).read_bytes()
    )
    gold_files = tuple(
        VerifiedPipelineImplicitGoldFile(
            size_bytes=entry.size_bytes,
            media_type=entry.media_type,
            _path=entry.path,
            _sha256=entry.sha256,
            _payload=b"x" * entry.size_bytes,
        )
        for entry in gold_manifest.entries
    )
    gold_bundle = VerifiedPipelineImplicitGoldBundle(
        task_id=task_id,
        _files=gold_files,
    )
    live_image = _load_isolated_verified_image(tmp_path)
    perfect_observation = ImageClassificationObservation(
        complete=True,
        category_names=tuple(PINNED_CLASSIFIED_IMAGE_SHA256),
        categorized_images=tuple(
            CategorizedImage(category_id=category, content_sha256=digest)
            for category, digests in PINNED_CLASSIFIED_IMAGE_SHA256.items()
            for digest in digests
        ),
        source_image_sha256=tuple(
            digest
            for digests in PINNED_CLASSIFIED_IMAGE_SHA256.values()
            for digest in digests
        )
        + tuple(PINNED_UNCLASSIFIED_IMAGE_SHA256),
        presentations=tuple(
            PresentationArtifact(document_id=document_id, content_sha256=digest)
            for document_id, digest in PINNED_PRESENTATION_SHA256.items()
        ),
        unexpected_regular_file_count=0,
    )
    input_digest_by_path = {item.path: item.sha256 for item in assets.manifest.files}
    copy_plan = candidate_module._build_ppt003_reference_copy_plan(
        input_manifest=assets.manifest,
        gold_manifest=gold_manifest,
    )
    result_digest_by_path = {
        destination: digest for _source, destination, digest in copy_plan
    }
    lifecycle: list[str] = []

    class _Verification:
        """提供 environment 既有 verifier 所需的最小只读成功投影。"""

        ok = True

    def fake_verify(_manifest: object, _root: Path) -> _Verification:
        """替代 download-only fixture I/O，但不替代 manifest/parser。

        输入参数：_manifest/_root 为已经由生产代码绑定的输入。
        输出返回值：仅 ``ok=True`` 的验证投影。
        """

        return _Verification()

    def fake_execute(_self: OSWorldController, command: object) -> CommandResult:
        """模拟 owned guest 的 shell-free argv 响应。

        输入参数：_self 为精确 production controller；command 为 argv。
        输出返回值：input/copy SHA 与 initial find 闭集的结构化结果。
        """

        assert isinstance(command, list)
        argv = tuple(command)
        if argv[0] == "find":
            directories = sorted(
                {
                    Path(path).parent.as_posix()
                    for path in input_digest_by_path
                    if Path(path).parent.as_posix() != "."
                }
            )
            records = [f"d\t{path}" for path in directories]
            records.extend(f"f\t{path}" for path in sorted(input_digest_by_path))
            return CommandResult(0, "\n".join(records) + "\n", "")
        if argv[0] == "sha256sum":
            relative = argv[-1].removeprefix("/home/oai/shared/")
            digest = (
                input_digest_by_path.get(relative) or result_digest_by_path[relative]
            )
            return CommandResult(0, f"{digest}  artifact\n", "")
        return CommandResult(0, "", "")

    def fake_capture(
        _self: PipelineImplicitArtifactEvidenceSource,
        observed_task_id: str,
        observed_controller: object,
        *,
        guest_shared_dir: str | None,
    ) -> ImageClassificationObservation:
        """替代 guest bytes，但保留真实 source→environment→evaluator 链。

        输入参数：任务、controller 与 prepare 冻结的 guest 根。
        输出返回值：完整 typed PPT003 observation。
        """

        assert observed_task_id == task_id
        assert type(observed_controller) is OSWorldController
        assert guest_shared_dir == "/home/oai/shared"
        lifecycle.append("typed-capture")
        return perfect_observation

    def fake_docker_start(self: object) -> str:
        """模拟 attested wrapper 的外部 Docker 启动。

        输入参数：self 为真实 wrapper 实例。
        输出返回值：合法容器 ID。
        """

        setattr(self, "_started", True)
        lifecycle.append("docker-start")
        return "d" * 12

    def fake_docker_close(self: object) -> None:
        """模拟 owned close 并记录发证前时序。

        输入参数：self 为真实 wrapper 实例。
        输出返回值：无。
        """

        setattr(self, "_started", False)
        setattr(self, "_closed", True)
        lifecycle.append("owned-close")

    def fake_attestation(
        self: object,
        *,
        container_image: str,
        extracted_qcow2_sha256: str,
    ) -> bool:
        """确认 candidate 以 held image 的 OCI/qcow 身份做 close 后核验。

        输入参数：两项值必须来自首次 live image。
        输出返回值：仅 close 已发生且两个身份匹配时为真。
        """

        lifecycle.append("closed-attestation")
        return (
            lifecycle[-2] == "owned-close"
            and container_image == live_image.container_image
            and extracted_qcow2_sha256 == live_image.extracted_sha256
        )

    monkeypatch.setattr(
        candidate_module,
        "load_osworld_image_manifest_with_sha256",
        lambda _path: (live_image, str(live_image.manifest_sha256)),
    )
    monkeypatch.setattr(
        pipeline_binding_module,
        "load_osworld_image_manifest_with_sha256",
        lambda _path: (live_image, str(live_image.manifest_sha256)),
    )
    monkeypatch.setattr(candidate_module, "verify_asset_directory", fake_verify)
    monkeypatch.setattr(
        "paraguibench.runtime.osworld_environment.verify_asset_directory",
        fake_verify,
    )
    monkeypatch.setattr(
        candidate_module,
        "resolve_verified_pipeline_implicit_gold_bundle",
        lambda _payload, _cache: gold_bundle,
    )
    monkeypatch.setattr(OSWorldAttestedDockerSession, "start", fake_docker_start)
    monkeypatch.setattr(OSWorldAttestedDockerSession, "close", fake_docker_close)
    monkeypatch.setattr(
        OSWorldAttestedDockerSession,
        "attests_closed_manifest",
        fake_attestation,
    )
    monkeypatch.setattr(
        OSWorldController, "wait_until_ready", lambda _self, **_kw: None
    )
    monkeypatch.setattr(
        OSWorldController, "get_desktop_path", lambda _self: "/home/oai/Desktop"
    )
    monkeypatch.setattr(
        OSWorldController, "upload_file", lambda _self, _src, _dst: None
    )
    monkeypatch.setattr(OSWorldController, "execute", fake_execute)
    monkeypatch.setattr(OSWorldController, "open_path", lambda _self, _path: None)
    monkeypatch.setattr(PipelineImplicitArtifactEvidenceSource, "capture", fake_capture)
    config = _config(tmp_path)

    receipt = run_pipeline_implicit_component_candidate(config)

    assert type(receipt) is PipelineImplicitComponentReceipt
    assert receipt.task_id == task_id
    assert receipt.score == 1.0
    assert lifecycle[-2:] == ["owned-close", "closed-attestation"]
    persisted = "\n".join(
        path.read_text(encoding="utf-8") for path in config.runs_root.rglob("*.json")
    )
    for forbidden in (
        "final_output",
        "gold_manifest",
        "Unknown-1.jpeg",
        "basketball",
    ):
        assert forbidden not in persisted
