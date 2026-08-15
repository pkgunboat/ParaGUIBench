"""pipeline implicit component receipt 的专属无 Agent live candidate。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import math
from pathlib import Path, PurePosixPath
import secrets
import stat

from paraguibench.benchmark import prepare_release_task
from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_TASK_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    CrossDocumentObservation,
    HideNARowsObservation,
    PINNED_HIDDEN_ROWS_BY_DOCUMENT,
    ImageClassificationObservation,
    PINNED_CLASSIFIED_IMAGE_SHA256,
    PINNED_PRESENTATION_SHA256,
    PINNED_UNCLASSIFIED_IMAGE_SHA256,
)
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
    EXCEL008_GOLD_MANIFEST_PATH,
    PPT003_GOLD_MANIFEST_PATH,
    PipelineImplicitGoldManifest,
    VerifiedPipelineImplicitGoldBundle,
    load_verified_pipeline_implicit_gold_manifest,
    load_pipeline_implicit_known_negative_manifest,
    resolve_verified_pipeline_implicit_gold_bundle,
)
from paraguibench.integrations.pipeline_implicit.artifact_evidence import (
    PIPELINE_IMPLICIT_TASK_PROTOCOLS,
    PipelineImplicitArtifactEvidenceSource,
)
from paraguibench.integrations.osworld.docker_session import OSWorldDockerConfig
from paraguibench.integrations.osworld.controller import (
    CommandResult,
    OSWorldController,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.runstore import (
    AttemptFailureStage,
    AttemptInspection,
    EvaluationOutcome,
    ExecutionOutcome,
    RunProvenanceStatus,
    RunStore,
    RunVersionVector,
    TaskAttempt,
)
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runtime import pipeline_implicit_component_receipts as _receipts
from paraguibench.runtime.assets import (
    AssetManifest,
    TaskAssetMode,
    read_manifest_bytes_nofollow,
    resolve_task_assets,
    verify_asset_directory,
)
from paraguibench.runtime.attempt_runner import RuntimeEvaluation
from paraguibench.runtime.evaluators import (
    PipelineImplicitTaskEvaluator,
    build_task_evaluator,
)
from paraguibench.runtime.osworld_attested_qcow2 import (
    OSWorldAttestedDockerSession,
)
from paraguibench.runtime.osworld_environment import OSWorldTaskEnvironment
from paraguibench.runtime.pipeline_implicit_binding import (
    PipelineImplicitRuntimeCapability,
    preflight_pipeline_implicit_component_candidate_runtime,
)
from paraguibench.runtime.pipeline_implicit_component_contracts import (
    PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL,
    PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL,
    PIPELINE_IMPLICIT_COMPONENT_RECEIPT_KIND,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    PIPELINE_IMPLICIT_COMPONENT_TASK_IDS,
    PipelineImplicitComponentIdentity,
    PipelineImplicitComponentReceipt,
    derive_pipeline_implicit_component_identity_for_environment,
)
from paraguibench.runtime.run_versioning import build_run_version_vector


class PipelineImplicitComponentCandidateError(RuntimeError):
    """表示专属 candidate 未形成可发证的完整生命周期。"""

    code = "PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_INVALID"

    def __init__(self) -> None:
        """构造不回显路径、正文、gold、artifact 或环境值的错误。

        输入参数：无。
        输出返回值：无；异常文本只含稳定错误码。
        """

        super().__init__(self.code)


@dataclass(frozen=True, slots=True, repr=False)
class PipelineImplicitComponentCandidateConfig:
    """保存无 Agent candidate 唯一允许的非敏感运行配置。"""

    repo_root: Path
    runs_root: Path
    asset_cache_root: Path
    gold_cache_root: Path
    qcow2_path: Path
    task_id: str
    run_id: str
    attempt_id: str
    server_port: int
    vnc_port: int
    chromium_port: int
    ram_size: str = "8G"
    cpu_cores: int = 4
    ready_timeout: float = 360.0

    def __post_init__(self) -> None:
        """在任何仓库、缓存、Docker 或 RunStore I/O 前验证形状。

        输入参数：无；读取冻结字段。
        输出返回值：绝对路径、三任务 candidate 闭集、安全标识符、
            互异 loopback 端口与资源限制全部有效时返回。
        异常：PipelineImplicitComponentCandidateError：任一字段不安全。
        """

        paths = (
            self.repo_root,
            self.runs_root,
            self.asset_cache_root,
            self.gold_cache_root,
            self.qcow2_path,
        )
        try:
            run_id = validate_identifier("run_id", self.run_id)
            task_id = validate_identifier("task_id", self.task_id)
            attempt_id = validate_identifier("attempt_id", self.attempt_id)
            OSWorldDockerConfig(
                container_name="paraguibench-pipeline-component-config-check",
                image="example.invalid/osworld@sha256:" + "0" * 64,
                qcow2_path=self.qcow2_path,
                server_port=self.server_port,
                vnc_port=self.vnc_port,
                chromium_port=self.chromium_port,
                ram_size=self.ram_size,
                cpu_cores=self.cpu_cores,
            )
        except Exception:
            raise PipelineImplicitComponentCandidateError from None
        if (
            any(not isinstance(path, Path) or not path.is_absolute() for path in paths)
            or run_id != self.run_id
            or task_id != self.task_id
            or attempt_id != self.attempt_id
            or self.task_id not in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
            or not isinstance(self.ready_timeout, (int, float))
            or isinstance(self.ready_timeout, bool)
            or not math.isfinite(float(self.ready_timeout))
            or self.ready_timeout <= 0
        ):
            raise PipelineImplicitComponentCandidateError


@dataclass(frozen=True, slots=True, repr=False)
class _CandidateMaterializationPlan:
    """保存只由已上传 input 驱动的 task-specific guest 动作闭集。

    输入参数：task_id 固定 typed 协议；input_paths 只能来自实际上传 manifest；
        ppt_copy_plan 仅供 PPT003 复制同一 input bytes。
    输出返回值：不可变内部计划；不包含 gold/audit payload、host 路径或正文。
    """

    task_id: str
    input_paths: tuple[str, ...]
    ppt_copy_plan: tuple[tuple[str, str, str], ...] = ()

    def __repr__(self) -> str:
        """返回不含文件名、类别、gold 或业务值的脱敏表示。

        输入参数：无。
        输出返回值：只含任务和动作计数的稳定字符串。
        """

        return (
            "_CandidateMaterializationPlan("
            f"task_id={self.task_id!r}, input_count={len(self.input_paths)!r}, "
            f"copy_count={len(self.ppt_copy_plan)!r})"
        )


_EXCEL008_INPUT_ONLY_SCRIPT = r"""
import os
import sys
import xml.etree.ElementTree as ET
import zipfile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
T = "{" + NS + "}t"
V = "{" + NS + "}v"

path = sys.argv[1]
with zipfile.ZipFile(path, "r") as source:
    infos = source.infolist()
    payloads = {info.filename: source.read(info) for info in infos}
shared = []
if "xl/sharedStrings.xml" in payloads:
    root = ET.fromstring(payloads["xl/sharedStrings.xml"])
    for item in root.findall("{" + NS + "}si"):
        shared.append("".join(node.text or "" for node in item.iter(T)))
sheet_name = "xl/worksheets/sheet1.xml"
sheet = ET.fromstring(payloads[sheet_name])
sheet_format = sheet.find("{" + NS + "}sheetFormatPr")
if sheet_format is None or not sheet_format.attrib.get("defaultRowHeight"):
    raise SystemExit(2)
default_row_height = sheet_format.attrib["defaultRowHeight"]
hidden = 0
for row in sheet.iter("{" + NS + "}row"):
    contains_na = False
    for cell in row.findall("{" + NS + "}c"):
        kind = cell.attrib.get("t")
        if kind == "inlineStr":
            value = "".join(node.text or "" for node in cell.iter(T))
        else:
            node = cell.find(V)
            value = "" if node is None else (node.text or "")
            if kind == "s" and value.isdigit() and int(value) < len(shared):
                value = shared[int(value)]
        if value == "N/A":
            contains_na = True
    if contains_na:
        row.set("hidden", "1")
        # 新建 RowDimension 时显式复用当前 sheet 的默认行高，
        # 避免 Office/openpyxl 将“仅 hidden”重解释为可见高度变化。
        if "ht" not in row.attrib:
            row.set("ht", default_row_height)
        hidden += 1
if hidden:
    payloads[sheet_name] = ET.tostring(sheet, encoding="utf-8", xml_declaration=True)
    temporary = path + ".paraguibench-tmp"
    with zipfile.ZipFile(temporary, "w") as target:
        for info in infos:
            target.writestr(info, payloads[info.filename])
    os.replace(temporary, path)
print("OK:" + str(hidden))
""".strip()


_COMBINATION002_INPUT_ONLY_SCRIPT = r"""
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
root = sys.argv[1]
xlsx = os.path.join(root, "McDonalds_Monthly_Data.xlsx")
docx = os.path.join(root, "McDonald_finacial_report.docx")
pptx = os.path.join(root, "McDonalds_powerpoint_report.pptx")

def archive_payloads(path):
    with zipfile.ZipFile(path, "r") as source:
        infos = source.infolist()
        payloads = {info.filename: source.read(info) for info in infos}
    return infos, payloads

def replace_archive(path, infos, payloads):
    temporary = path + ".paraguibench-tmp"
    with zipfile.ZipFile(temporary, "w") as target:
        for info in infos:
            target.writestr(info, payloads[info.filename])
    os.replace(temporary, path)

infos, payloads = archive_payloads(xlsx)
shared = []
if "xl/sharedStrings.xml" in payloads:
    strings = ET.fromstring(payloads["xl/sharedStrings.xml"])
    for item in strings.findall("{" + S + "}si"):
        shared.append("".join(node.text or "" for node in item.iter("{" + S + "}t")))
sheet = ET.fromstring(payloads["xl/worksheets/sheet1.xml"])
cells = {}
for cell in sheet.iter("{" + S + "}c"):
    reference = cell.attrib.get("r")
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        value = "".join(node.text or "" for node in cell.iter("{" + S + "}t"))
    else:
        node = cell.find("{" + S + "}v")
        value = "" if node is None else (node.text or "")
        if kind == "s" and value.isdigit() and int(value) < len(shared):
            value = shared[int(value)]
    if reference:
        cells[reference] = value
months = []
for row in range(4, 16):
    months.append((cells["A" + str(row)], int(cells["D" + str(row)]), int(cells["E" + str(row)])))
by_month = {name.casefold(): (profit, customers) for name, profit, customers in months}
top = sorted(months, key=lambda item: item[1], reverse=True)[:3]
if "january" not in by_month or len(top) != 3:
    raise SystemExit(2)

infos, payloads = archive_payloads(docx)
document = ET.fromstring(payloads["word/document.xml"])
targets = []
for paragraph in document.iter("{" + W + "}p"):
    nodes = [node for node in paragraph.iter("{" + W + "}t")]
    text = "".join(node.text or "" for node in nodes)
    if "Conversely, " in text and "demonstrated the strongest performance" in text:
        targets.append((nodes, text))
if len(targets) != 1 or not targets[0][0]:
    raise SystemExit(2)
nodes, current = targets[0]
prefix = current.split("Conversely, ", 1)[0] + "Conversely, "
labels = [name for name, _profit, _customers in top]
profits = [profit for _name, profit, _customers in top]
replacement = (
    prefix
    + labels[0]
    + " demonstrated the strongest performance with a profit of $"
    + format(profits[0], ",")
    + ", followed by "
    + labels[1]
    + " ($"
    + format(profits[1], ",")
    + ") and "
    + labels[2]
    + " ($"
    + format(profits[2], ",")
    + ")."
)
nodes[0].text = replacement
for node in nodes[1:]:
    node.text = ""
payloads["word/document.xml"] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
replace_archive(docx, infos, payloads)

infos, payloads = archive_payloads(pptx)
changed_members = []
for member in sorted(name for name in payloads if re.fullmatch(r"ppt/slides/slide[0-9]+[.]xml", name)):
    slide = ET.fromstring(payloads[member])
    paragraphs = []
    for paragraph in slide.iter("{" + A + "}p"):
        nodes = [node for node in paragraph.iter("{" + A + "}t")]
        paragraphs.append((nodes, "".join(node.text or "" for node in nodes)))
    if not any(text.strip().casefold() == "jan data" for _nodes, text in paragraphs):
        continue
    targets = [(nodes, text) for nodes, text in paragraphs if text.strip().startswith("Customers:")]
    if len(targets) != 1 or not targets[0][0]:
        raise SystemExit(2)
    nodes, _text = targets[0]
    nodes[0].text = "Customers:" + format(by_month["january"][1], ",")
    for node in nodes[1:]:
        node.text = ""
    payloads[member] = ET.tostring(slide, encoding="utf-8", xml_declaration=True)
    changed_members.append(member)
if len(changed_members) != 1:
    raise SystemExit(2)
replace_archive(pptx, infos, payloads)
print("OK")
""".strip()


def run_pipeline_implicit_component_candidate(
    config: PipelineImplicitComponentCandidateConfig,
) -> PipelineImplicitComponentReceipt:
    """执行 task-scoped no-Agent candidate 的完整 production 闭环。

    输入参数：config 为精确冻结配置，不接受任何执行依赖注入。
    输出返回值：仅 held image/input/host reference、独立 qcow snapshot、
        真实 environment.prepare、input-only guest materialization、typed
        capture/evaluator、owned close/attestation 与 RunStore inspection
        全部成功后返回脱敏 receipt。
    异常：PipelineImplicitComponentCandidateError：任一步骤失败；错误不
        回显任务正文、路径、gold、artifact、Agent 文本或 secret。
    """

    if type(config) is not PipelineImplicitComponentCandidateConfig:
        raise PipelineImplicitComponentCandidateError
    try:
        root = _resolve_repository_root(config.repo_root)
        image_manifest, manifest_sha256 = load_osworld_image_manifest_with_sha256(
            root / "environments/osworld/image-manifest.json"
        )
        if (
            image_manifest.manifest_sha256 != manifest_sha256
            or not image_manifest.live_run_ready
            or image_manifest.extracted_sha256 is None
            or "osworld.desktop.v1" not in image_manifest.protocol_ids
        ):
            raise PipelineImplicitComponentCandidateError
        prepared_task = prepare_release_task(
            root,
            config.task_id,
            environment_bindings={},
        )
        capability = preflight_pipeline_implicit_component_candidate_runtime(
            repo_root=root,
            task=prepared_task.trusted_task,
            image_manifest=image_manifest,
        )
        expected_protocols = {
            task_id: PIPELINE_IMPLICIT_TASK_PROTOCOLS[task_id]
            for task_id in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        }
        if (
            not isinstance(capability, PipelineImplicitRuntimeCapability)
            or capability.task_id != config.task_id
            or config.task_id not in expected_protocols
            or capability.protocol_id != expected_protocols[config.task_id]
            or capability.environment_manifest_sha256 != manifest_sha256
            or capability.container_image != image_manifest.container_image
            or capability.extracted_qcow2_sha256 != image_manifest.extracted_sha256
            or capability.environment_identity_sha256 is None
        ):
            raise PipelineImplicitComponentCandidateError
        canonical_task_sha256 = prepared_task.audit_metadata.get(
            "canonical_task_sha256"
        )
        if not isinstance(canonical_task_sha256, str):
            raise PipelineImplicitComponentCandidateError
        identity_before = derive_pipeline_implicit_component_identity_for_environment(
            root,
            config.task_id,
            image_manifest,
            expected_task=prepared_task.trusted_task,
            expected_task_sha256=canonical_task_sha256,
            expected_input_manifest_sha256=(capability.input_manifest_sha256),
            expected_reference_manifest_sha256=(capability.reference_manifest_sha256),
            expected_reference_manifest_role=capability.reference_manifest_role,
        )
        if (
            capability.environment_identity_sha256
            != identity_before.environment_identity_sha256
        ):
            raise PipelineImplicitComponentCandidateError

        task_assets = resolve_task_assets(root, prepared_task.trusted_task)
        if (
            task_assets.mode is not TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
            or task_assets.manifest is None
            or task_assets.manifest.asset_set_id != config.task_id
            or not verify_asset_directory(
                task_assets.manifest,
                config.asset_cache_root / task_assets.manifest.asset_set_id,
            ).ok
        ):
            raise PipelineImplicitComponentCandidateError
        if config.task_id in {IMAGE_CLASSIFICATION_TASK_ID, HIDE_NA_ROWS_TASK_ID}:
            gold_path = (
                PPT003_GOLD_MANIFEST_PATH
                if config.task_id == IMAGE_CLASSIFICATION_TASK_ID
                else EXCEL008_GOLD_MANIFEST_PATH
            )
            if (
                prepared_task.trusted_task.get("gold_manifest") != gold_path
                or capability.reference_manifest_role != "gold"
            ):
                raise PipelineImplicitComponentCandidateError
            reference_payload = read_manifest_bytes_nofollow(
                root / gold_path,
                max_bytes=1_048_576,
            )
            if (
                hashlib.sha256(reference_payload).hexdigest()
                != capability.reference_manifest_sha256
            ):
                raise PipelineImplicitComponentCandidateError
            gold_manifest = load_verified_pipeline_implicit_gold_manifest(
                reference_payload
            )
            gold_bundle = resolve_verified_pipeline_implicit_gold_bundle(
                reference_payload,
                config.gold_cache_root,
            )
            expected_gold_count = (
                32 if config.task_id == IMAGE_CLASSIFICATION_TASK_ID else 5
            )
            if (
                type(gold_bundle) is not VerifiedPipelineImplicitGoldBundle
                or gold_bundle.task_id != config.task_id
                or gold_bundle.file_count != expected_gold_count
                or gold_bundle.total_bytes <= 0
            ):
                raise PipelineImplicitComponentCandidateError
            if config.task_id == IMAGE_CLASSIFICATION_TASK_ID:
                materialization_plan = _CandidateMaterializationPlan(
                    task_id=config.task_id,
                    input_paths=tuple(item.path for item in task_assets.manifest.files),
                    ppt_copy_plan=_build_ppt003_reference_copy_plan(
                        input_manifest=task_assets.manifest,
                        gold_manifest=gold_manifest,
                    ),
                )
            else:
                materialization_plan = _build_excel008_input_only_plan(
                    input_manifest=task_assets.manifest,
                    gold_manifest=gold_manifest,
                )
            # 正式 gold 只在 host preflight 短期存在；guest 仅接收 input。
            del gold_bundle, gold_manifest, reference_payload
        elif config.task_id == CROSS_DOCUMENT_TASK_ID:
            if (
                "gold_manifest" in prepared_task.trusted_task
                or prepared_task.trusted_task.get("known_negative_manifest")
                != COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH
                or capability.reference_manifest_role != "audit_known_negative"
            ):
                raise PipelineImplicitComponentCandidateError
            audit_payload = read_manifest_bytes_nofollow(
                root / COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
                max_bytes=1_048_576,
            )
            if (
                hashlib.sha256(audit_payload).hexdigest()
                != capability.reference_manifest_sha256
            ):
                raise PipelineImplicitComponentCandidateError
            # 只验证 host-side metadata；不解析/下载历史 answer payload。
            load_pipeline_implicit_known_negative_manifest(audit_payload)
            del audit_payload
            materialization_plan = _build_combination002_input_only_plan(
                task_assets.manifest
            )
        else:
            raise PipelineImplicitComponentCandidateError

        formal_vector = build_run_version_vector(
            repo_root=root,
            task_id=config.task_id,
            environment_manifest_path=(
                root / "environments/osworld/image-manifest.json"
            ),
            environment_manifest_sha256=manifest_sha256,
            environment_protocol_ids=image_manifest.protocol_ids,
        )
        if (
            formal_vector.evaluation_protocol != capability.protocol_id
            or formal_vector.environment_protocol
            != PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL
        ):
            raise PipelineImplicitComponentCandidateError
        candidate_vector = replace(
            formal_vector,
            evaluation_protocol=PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL,
        )
        evaluator = build_task_evaluator(
            prepared_task.trusted_task,
            evaluation_protocol=capability.protocol_id,
        )
        if type(evaluator) is not PipelineImplicitTaskEvaluator:
            raise PipelineImplicitComponentCandidateError

        docker_config = OSWorldDockerConfig(
            container_name=("paraguibench-pipeline-component-" + secrets.token_hex(8)),
            image=image_manifest.container_image,
            qcow2_path=config.qcow2_path,
            server_port=config.server_port,
            vnc_port=config.vnc_port,
            chromium_port=config.chromium_port,
            ram_size=config.ram_size,
            cpu_cores=config.cpu_cores,
        )
        docker_session = OSWorldAttestedDockerSession(
            config=docker_config,
            expected_qcow2_sha256=image_manifest.extracted_sha256,
        )
        controller = OSWorldController(f"http://127.0.0.1:{config.server_port}")
        evidence_source = PipelineImplicitArtifactEvidenceSource()
        environment = OSWorldTaskEnvironment(
            repo_root=root,
            asset_cache_root=config.asset_cache_root,
            docker_session=docker_session,
            controller=controller,
            pipeline_implicit_evidence_source=evidence_source,
            pipeline_implicit_runtime_capability=capability,
            ready_timeout=float(config.ready_timeout),
        )
        store = RunStore(config.runs_root)
        store.start_run(
            run_id=config.run_id,
            run_record={
                "candidate_kind": PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL
            },
            version_vector=candidate_vector,
        )
        attempt = store.start_attempt(
            run_id=config.run_id,
            task_id=config.task_id,
            attempt_id=config.attempt_id,
            task_record=prepared_task.audit_metadata,
        )
        inspection = _run_candidate_attempt(
            store=store,
            attempt=attempt,
            task=prepared_task.trusted_task,
            environment=environment,
            controller=controller,
            docker_session=docker_session,
            evidence_source=evidence_source,
            evaluator=evaluator,
            capability=capability,
            materialization_plan=materialization_plan,
            image_manifest=image_manifest,
            candidate_vector=candidate_vector,
        )

        identity_after = derive_pipeline_implicit_component_identity_for_environment(
            root,
            config.task_id,
            image_manifest,
            expected_task=prepared_task.trusted_task,
            expected_task_sha256=canonical_task_sha256,
            expected_input_manifest_sha256=(capability.input_manifest_sha256),
            expected_reference_manifest_sha256=(capability.reference_manifest_sha256),
            expected_reference_manifest_role=capability.reference_manifest_role,
        )
        current_image, current_manifest_sha256 = (
            load_osworld_image_manifest_with_sha256(
                root / "environments/osworld/image-manifest.json"
            )
        )
        if (
            identity_after != identity_before
            or current_manifest_sha256 != manifest_sha256
            or current_image.manifest_sha256 != manifest_sha256
        ):
            raise PipelineImplicitComponentCandidateError
        receipt = _build_candidate_receipt(
            inspection=inspection,
            identity=identity_before,
            capability=capability,
            run_id=config.run_id,
            task_id=config.task_id,
            attempt_id=config.attempt_id,
        )
        if (
            derive_pipeline_implicit_component_identity_for_environment(
                root,
                config.task_id,
                image_manifest,
                expected_task=prepared_task.trusted_task,
                expected_task_sha256=canonical_task_sha256,
                expected_input_manifest_sha256=(capability.input_manifest_sha256),
                expected_reference_manifest_sha256=(
                    capability.reference_manifest_sha256
                ),
                expected_reference_manifest_role=(capability.reference_manifest_role),
            )
            != identity_before
        ):
            raise PipelineImplicitComponentCandidateError
        return receipt
    except PipelineImplicitComponentCandidateError:
        raise
    except Exception:
        raise PipelineImplicitComponentCandidateError from None


def _resolve_repository_root(repo_root: Path) -> Path:
    """固定不经过 symlink 的单一 candidate 仓库根。

    输入参数：repo_root 为冻结配置中的绝对路径。
    输出返回值：存在、非 symlink 且为目录的规范根。
    异常：PipelineImplicitComponentCandidateError：根节点类型或解析无效。
    """

    try:
        metadata = repo_root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        return repo_root.resolve(strict=True)
    except OSError:
        raise PipelineImplicitComponentCandidateError from None


def _build_excel008_input_only_plan(
    *,
    input_manifest: AssetManifest,
    gold_manifest: PipelineImplicitGoldManifest,
) -> _CandidateMaterializationPlan:
    """从已验证 input 与 host-only gold 身份构造无答案 guest 计划。

    输入参数：input_manifest 是实际上传五 XLSX；gold_manifest 只在 host
        证明同名五文件固定 reference 闭集，不提供行号、payload 或路径给 guest。
    输出返回值：仅含 input 文件名的不可变计划；guest 自行从字面 ``N/A``
        推导应隐藏行。
    异常：PipelineImplicitComponentCandidateError：角色、路径、数量或媒体漂移。
    """

    if (
        type(input_manifest) is not AssetManifest
        or type(gold_manifest) is not PipelineImplicitGoldManifest
        or input_manifest.asset_set_id != HIDE_NA_ROWS_TASK_ID
        or gold_manifest.task_id != HIDE_NA_ROWS_TASK_ID
        or len(input_manifest.files) != 5
        or len(gold_manifest.entries) != 5
    ):
        raise PipelineImplicitComponentCandidateError
    input_paths = tuple(item.path for item in input_manifest.files)
    reference_paths = tuple(item.path for item in gold_manifest.entries)
    if (
        input_paths != tuple(PINNED_HIDDEN_ROWS_BY_DOCUMENT)
        or reference_paths != input_paths
        or any(
            len(PurePosixPath(path).parts) != 1 or not path.endswith(".xlsx")
            for path in input_paths
        )
    ):
        raise PipelineImplicitComponentCandidateError
    return _CandidateMaterializationPlan(
        task_id=HIDE_NA_ROWS_TASK_ID,
        input_paths=input_paths,
    )


def _build_combination002_input_only_plan(
    input_manifest: AssetManifest,
) -> _CandidateMaterializationPlan:
    """从实际上传三 input 构造 XLSX-source-relative guest 计划。

    输入参数：input_manifest 为 canonical resolver 将上传的 DOCX/XLSX/PPTX。
    输出返回值：只含三 input 文件名的不可变计划；不读取或接收历史 HF
        answer manifest/bundle，修正事实由 guest 内同批 XLSX 计算。
    异常：PipelineImplicitComponentCandidateError：任务、闭集或类型漂移。
    """

    expected_paths = (
        "McDonald_finacial_report.docx",
        "McDonalds_Monthly_Data.xlsx",
        "McDonalds_powerpoint_report.pptx",
    )
    if (
        type(input_manifest) is not AssetManifest
        or input_manifest.asset_set_id != CROSS_DOCUMENT_TASK_ID
        or tuple(item.path for item in input_manifest.files) != expected_paths
    ):
        raise PipelineImplicitComponentCandidateError
    return _CandidateMaterializationPlan(
        task_id=CROSS_DOCUMENT_TASK_ID,
        input_paths=expected_paths,
    )


def _materialize_input_only_reference_result(
    *,
    controller: OSWorldController,
    guest_shared_dir: str,
    plan: _CandidateMaterializationPlan,
) -> None:
    """在 owned guest 内仅从已上传 input 形成 task 满分参考终态。

    输入参数：controller 为 production loopback transport；guest_shared_dir
        来自真实 environment.prepare；plan 不含 host/gold/audit payload。
    输出返回值：PPT 执行 input copy；Excel 从字面 N/A 自行隐藏整行；Combo
        从同批 XLSX 计算事实并修正 DOCX/PPTX。所有命令均为 shell-free argv。
    异常：PipelineImplicitComponentCandidateError：类型、路径、进程或固定
        完成回执任一无效；不会回显 guest 输出。
    """

    if (
        type(controller) is not OSWorldController
        or controller.uses_production_transport() is not True
        or not isinstance(guest_shared_dir, str)
        or type(plan) is not _CandidateMaterializationPlan
    ):
        raise PipelineImplicitComponentCandidateError
    try:
        root = PurePosixPath(guest_shared_dir)
    except (TypeError, ValueError):
        raise PipelineImplicitComponentCandidateError from None
    if (
        not root.is_absolute()
        or root.as_posix() != guest_shared_dir
        or ".." in root.parts
    ):
        raise PipelineImplicitComponentCandidateError
    if plan.task_id == IMAGE_CLASSIFICATION_TASK_ID:
        _materialize_ppt003_reference_result(
            controller=controller,
            guest_shared_dir=guest_shared_dir,
            copy_plan=plan.ppt_copy_plan,
        )
        return
    if plan.task_id == HIDE_NA_ROWS_TASK_ID:
        if plan.input_paths != tuple(PINNED_HIDDEN_ROWS_BY_DOCUMENT):
            raise PipelineImplicitComponentCandidateError
        for relative_path in plan.input_paths:
            path = PurePosixPath(relative_path)
            if len(path.parts) != 1 or path.suffix.casefold() != ".xlsx":
                raise PipelineImplicitComponentCandidateError
            result = controller.execute(
                [
                    "python3",
                    "-I",
                    "-c",
                    _EXCEL008_INPUT_ONLY_SCRIPT,
                    root.joinpath(*path.parts).as_posix(),
                ]
            )
            expected_count = len(PINNED_HIDDEN_ROWS_BY_DOCUMENT[relative_path])
            if (
                type(result) is not CommandResult
                or result.returncode != 0
                or result.stderr != ""
                or result.stdout.strip() != f"OK:{expected_count}"
            ):
                raise PipelineImplicitComponentCandidateError
        return
    if plan.task_id == CROSS_DOCUMENT_TASK_ID:
        if plan.input_paths != (
            "McDonald_finacial_report.docx",
            "McDonalds_Monthly_Data.xlsx",
            "McDonalds_powerpoint_report.pptx",
        ):
            raise PipelineImplicitComponentCandidateError
        result = controller.execute(
            [
                "python3",
                "-I",
                "-c",
                _COMBINATION002_INPUT_ONLY_SCRIPT,
                root.as_posix(),
            ]
        )
        if (
            type(result) is not CommandResult
            or result.returncode != 0
            or result.stderr != ""
            or result.stdout.strip() != "OK"
        ):
            raise PipelineImplicitComponentCandidateError
        return
    raise PipelineImplicitComponentCandidateError


def _run_candidate_attempt(
    *,
    store: RunStore,
    attempt: TaskAttempt,
    task: dict[str, object],
    environment: OSWorldTaskEnvironment,
    controller: OSWorldController,
    docker_session: OSWorldAttestedDockerSession,
    evidence_source: PipelineImplicitArtifactEvidenceSource,
    evaluator: PipelineImplicitTaskEvaluator,
    capability: PipelineImplicitRuntimeCapability,
    materialization_plan: _CandidateMaterializationPlan,
    image_manifest: OSWorldImageManifest,
    candidate_vector: RunVersionVector,
) -> AttemptInspection:
    """执行 prepare→input-copy→typed capture/evaluate→close→inspection。

    输入参数：全部对象由 top-level issuer 在同一调用栈中构造；不接受
        Agent、final text、factory、session、proof 或 evaluator 替身入口。
    输出返回值：双次稳定读取且为 VERSIONED/SUCCEEDED/PASSED/1.0 的
        allowlist-only AttemptInspection。
    异常：PipelineImplicitComponentCandidateError：类型、生命周期、评价、
        close、qcow/OCI attestation、持久化或 inspection 任一步不成立。
    """

    if (
        not isinstance(store, RunStore)
        or not isinstance(attempt, TaskAttempt)
        or type(task) is not dict
        or task.get("task_id") not in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        or attempt.task_id != task.get("task_id")
        or type(environment) is not OSWorldTaskEnvironment
        or type(controller) is not OSWorldController
        or controller.uses_production_transport() is not True
        or type(docker_session) is not OSWorldAttestedDockerSession
        or type(evidence_source) is not PipelineImplicitArtifactEvidenceSource
        or type(evaluator) is not PipelineImplicitTaskEvaluator
        or not isinstance(capability, PipelineImplicitRuntimeCapability)
        or capability.task_id != task.get("task_id")
        or capability.protocol_id
        != PIPELINE_IMPLICIT_TASK_PROTOCOLS[capability.task_id]
        or type(materialization_plan) is not _CandidateMaterializationPlan
        or materialization_plan.task_id != capability.task_id
        or type(image_manifest) is not OSWorldImageManifest
        or not isinstance(candidate_vector, RunVersionVector)
        or candidate_vector.evaluation_protocol
        != PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL
        or candidate_vector.environment_protocol
        != PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL
        or getattr(environment, "_pipeline_implicit_evidence_source", None)
        is not evidence_source
        or getattr(environment, "_pipeline_implicit_runtime_capability", None)
        is not capability
    ):
        raise PipelineImplicitComponentCandidateError

    phase = AttemptFailureStage.ENVIRONMENT_START
    execution_outcome = ExecutionOutcome.INFRA_ERROR
    evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
    score: float | None = None
    system_failed = False
    candidate_passed = False
    try:
        environment.start()
        phase = AttemptFailureStage.ENVIRONMENT_PREPARE
        environment.prepare(task)
        guest_shared_dir = environment.guest_shared_dir
        if not isinstance(guest_shared_dir, str):
            raise PipelineImplicitComponentCandidateError
        _materialize_input_only_reference_result(
            controller=controller,
            guest_shared_dir=guest_shared_dir,
            plan=materialization_plan,
        )
        phase = AttemptFailureStage.EVALUATOR_EVALUATE
        execution_outcome = ExecutionOutcome.SUCCEEDED
        evaluation = evaluator.evaluate(task, "", environment)
        if (
            type(evaluation) is not RuntimeEvaluation
            or type(evaluation.passed) is not bool
            or not isinstance(evaluation.score, (int, float))
            or isinstance(evaluation.score, bool)
            or not math.isfinite(float(evaluation.score))
            or not 0.0 <= float(evaluation.score) <= 1.0
            or not isinstance(evaluation.details, Mapping)
        ):
            raise PipelineImplicitComponentCandidateError
        # 第二次读取命中 environment 的同一缓存对象，不重新访问 guest；
        # 精确类型检查证明正式 evaluator 确实消费了 typed capture。
        frozen_observation = environment.pipeline_implicit_observation(
            capability.task_id,
            capability.protocol_id,
        )
        expected_observation_type = {
            IMAGE_CLASSIFICATION_TASK_ID: ImageClassificationObservation,
            HIDE_NA_ROWS_TASK_ID: HideNARowsObservation,
            CROSS_DOCUMENT_TASK_ID: CrossDocumentObservation,
        }[capability.task_id]
        if type(frozen_observation) is not expected_observation_type:
            raise PipelineImplicitComponentCandidateError
        score = float(evaluation.score)
        candidate_passed = evaluation.passed is True and score == 1.0
        evaluation_outcome = (
            EvaluationOutcome.PASSED if candidate_passed else EvaluationOutcome.FAILED
        )
    except Exception:
        system_failed = True
        if phase is AttemptFailureStage.EVALUATOR_EVALUATE:
            execution_outcome = ExecutionOutcome.SUCCEEDED
            evaluation_outcome = EvaluationOutcome.ERROR
        else:
            execution_outcome = ExecutionOutcome.INFRA_ERROR
            evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
        score = None
    finally:
        try:
            environment.close()
        except Exception:
            system_failed = True
            phase = AttemptFailureStage.ENVIRONMENT_CLOSE
            execution_outcome = ExecutionOutcome.INFRA_ERROR
            evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
            score = None

    try:
        attested = docker_session.attests_closed_manifest(
            container_image=image_manifest.container_image,
            extracted_qcow2_sha256=str(image_manifest.extracted_sha256),
        )
    except Exception:
        attested = False
    if attested is not True:
        system_failed = True
        phase = AttemptFailureStage.ENVIRONMENT_CLOSE
        execution_outcome = ExecutionOutcome.INFRA_ERROR
        evaluation_outcome = EvaluationOutcome.NOT_REQUESTED
        score = None
    try:
        store.finish_attempt(
            attempt=attempt,
            execution_outcome=execution_outcome,
            evaluation_outcome=evaluation_outcome,
            score=score,
            failure_stage=(phase if system_failed else AttemptFailureStage.NOT_FAILED),
            details={},
        )
    except Exception:
        raise PipelineImplicitComponentCandidateError from None
    if system_failed or candidate_passed is not True or attested is not True:
        raise PipelineImplicitComponentCandidateError
    try:
        inspection_before = store.inspect_attempt(
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
        )
        inspection_after = store.inspect_attempt(
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            attempt_id=attempt.attempt_id,
        )
    except Exception:
        raise PipelineImplicitComponentCandidateError from None
    if inspection_after != inspection_before:
        raise PipelineImplicitComponentCandidateError
    _validate_candidate_inspection(
        inspection_before,
        candidate_vector=candidate_vector,
    )
    return inspection_before


def _validate_candidate_inspection(
    inspection: AttemptInspection,
    *,
    candidate_vector: RunVersionVector,
) -> None:
    """验证 RunStore 只读投影精确表达本次 candidate 成功。

    输入参数：inspection 为双次相等的安全投影；candidate_vector 为本次
        start_run 写入的不可变版本向量。
    输出返回值：全部终态、协议、provenance 与向量一致时返回。
    异常：PipelineImplicitComponentCandidateError：任一字段不一致。
    """

    if (
        type(inspection) is not AttemptInspection
        or inspection.execution_outcome is not ExecutionOutcome.SUCCEEDED
        or inspection.evaluation_outcome is not EvaluationOutcome.PASSED
        or inspection.score != 1.0
        or inspection.failure_stage is not AttemptFailureStage.NOT_FAILED
        or inspection.provenance_status is not RunProvenanceStatus.VERSIONED
        or inspection.version_vector != candidate_vector
        or not isinstance(inspection.version_vector, RunVersionVector)
        or not (
            inspection.version_vector.source_revision
            == inspection.version_vector.agent_code_revision
            == inspection.version_vector.evaluator_revision
        )
    ):
        raise PipelineImplicitComponentCandidateError


def _build_candidate_receipt(
    *,
    inspection: AttemptInspection,
    identity: PipelineImplicitComponentIdentity,
    capability: PipelineImplicitRuntimeCapability,
    run_id: str,
    task_id: str,
    attempt_id: str,
) -> PipelineImplicitComponentReceipt:
    """仅在 close 后把安全 inspection 与 current identity 投影为 receipt。

    输入参数：inspection 已由专属生命周期验证；identity 为执行前后相等
        的三层中性身份；capability 为 held task/env 绑定；其余为 RunStore ID。
    输出返回值：无路径、gold、正文、类别、secret 或 Agent final text 的
        task-scoped component receipt。
    异常：PipelineImplicitComponentCandidateError：类型或投影不一致。
    """

    if (
        type(inspection) is not AttemptInspection
        or not isinstance(identity, PipelineImplicitComponentIdentity)
        or not isinstance(capability, PipelineImplicitRuntimeCapability)
        or inspection.version_vector is None
        or capability.task_id != task_id
    ):
        raise PipelineImplicitComponentCandidateError
    try:
        return PipelineImplicitComponentReceipt(
            schema_version=1,
            receipt_kind=PIPELINE_IMPLICIT_COMPONENT_RECEIPT_KIND,
            task_id=task_id,
            run_id=run_id,
            attempt_id=attempt_id,
            execution_outcome=inspection.execution_outcome.value,
            evaluation_outcome=inspection.evaluation_outcome.value,
            score=float(inspection.score),
            candidate_protocol=PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL,
            task_evaluation_protocol=capability.protocol_id,
            environment_protocol=PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL,
            attempt_version_vector_sha256=(
                _receipts._run_version_vector_sha256(inspection.version_vector)
            ),
            task_identity_sha256=identity.task_identity_sha256,
            environment_identity_sha256=identity.environment_identity_sha256,
            component_identity_sha256=identity.component_identity_sha256,
        )
    except Exception:
        raise PipelineImplicitComponentCandidateError from None


def _build_ppt003_reference_copy_plan(
    *,
    input_manifest: AssetManifest,
    gold_manifest: PipelineImplicitGoldManifest,
) -> tuple[tuple[str, str, str], ...]:
    """从正式 input 与 typed 规则构造 PPT003 的十二项 guest copy 计划。

    输入参数：input_manifest 为 environment 后续实际上传的二十项资产；
        gold_manifest 只在 host 内验证结果闭集，不提供上传字节或动作来源。
    输出返回值：稳定的 ``(input_path, destination_path, sha256)`` tuple；
        每个 source 都来自 input，destination 只由 typed 类别常量派生。
    异常：PipelineImplicitComponentCandidateError：任务、摘要、路径、
        presentation/source 集或 gold 闭集任一漂移。
    """

    if (
        type(input_manifest) is not AssetManifest
        or type(gold_manifest) is not PipelineImplicitGoldManifest
        or input_manifest.asset_set_id != "Operation-FileOperate-BatchOperationPPT-003"
        or gold_manifest.task_id != input_manifest.asset_set_id
        or len(input_manifest.files) != 20
        or len(gold_manifest.entries) != 32
    ):
        raise PipelineImplicitComponentCandidateError
    by_digest: dict[str, object] = {}
    for asset in input_manifest.files:
        if asset.sha256 in by_digest:
            raise PipelineImplicitComponentCandidateError
        by_digest[asset.sha256] = asset
    classified_digests = {
        digest
        for digests in PINNED_CLASSIFIED_IMAGE_SHA256.values()
        for digest in digests
    }
    unclassified_digests = set(PINNED_UNCLASSIFIED_IMAGE_SHA256)
    presentation_digests = set(PINNED_PRESENTATION_SHA256.values())
    if set(by_digest) != (
        classified_digests | unclassified_digests | presentation_digests
    ):
        raise PipelineImplicitComponentCandidateError

    plan: list[tuple[str, str, str]] = []
    expected_gold: dict[str, tuple[int, str, str]] = {
        asset.path: (asset.size, asset.sha256, asset.media_type)
        for asset in input_manifest.files
    }
    for category in sorted(
        PINNED_CLASSIFIED_IMAGE_SHA256,
        key=lambda value: value.encode("utf-8"),
    ):
        for digest in PINNED_CLASSIFIED_IMAGE_SHA256[category]:
            asset = by_digest.get(digest)
            if asset is None:
                raise PipelineImplicitComponentCandidateError
            source_path = asset.path
            source = Path(source_path)
            if (
                len(source.parts) != 2
                or source.parts[0] != "images"
                or source.name in {"", ".", ".."}
            ):
                raise PipelineImplicitComponentCandidateError
            destination_path = f"{category}/{source.name}"
            if destination_path in expected_gold:
                raise PipelineImplicitComponentCandidateError
            expected_gold[destination_path] = (
                asset.size,
                asset.sha256,
                asset.media_type,
            )
            plan.append((source_path, destination_path, digest))
    observed_gold = {
        entry.path: (entry.size_bytes, entry.sha256, entry.media_type)
        for entry in gold_manifest.entries
    }
    if (
        len(observed_gold) != len(gold_manifest.entries)
        or observed_gold != expected_gold
    ):
        raise PipelineImplicitComponentCandidateError
    return tuple(plan)


def _materialize_ppt003_reference_result(
    *,
    controller: OSWorldController,
    guest_shared_dir: str,
    copy_plan: tuple[tuple[str, str, str], ...],
) -> None:
    """仅在 owned guest 内复制已上传 input，形成 PPT003 满分终态。

    输入参数：controller 必须是内部创建直连 loopback 会话的精确生产类型；
        guest_shared_dir 来自真实 ``environment.prepare``；copy_plan 只由
        input manifest 与 typed 分类规则生成，不含 gold payload。
    输出返回值：十二项 copy 全部执行且逐目标 SHA 复验后返回 ``None``。
    异常：PipelineImplicitComponentCandidateError：transport、路径、计划、
        mkdir/cp 或摘要结果任一无效；不会回显 guest 值。
    """

    try:
        root = PurePosixPath(guest_shared_dir)
    except (TypeError, ValueError):
        raise PipelineImplicitComponentCandidateError from None
    expected_categories = frozenset(PINNED_CLASSIFIED_IMAGE_SHA256)
    if (
        type(controller) is not OSWorldController
        or controller.uses_production_transport() is not True
        or not isinstance(guest_shared_dir, str)
        or not root.is_absolute()
        or root.as_posix() != guest_shared_dir
        or ".." in root.parts
        or type(copy_plan) is not tuple
        or len(copy_plan) != 12
    ):
        raise PipelineImplicitComponentCandidateError
    categories: set[str] = set()
    normalized: list[tuple[str, str, str]] = []
    for item in copy_plan:
        if type(item) is not tuple or len(item) != 3:
            raise PipelineImplicitComponentCandidateError
        source_value, destination_value, digest = item
        if not all(isinstance(value, str) and value for value in item):
            raise PipelineImplicitComponentCandidateError
        source = PurePosixPath(source_value)
        destination = PurePosixPath(destination_value)
        if (
            len(source.parts) != 2
            or source.parts[0] != "images"
            or len(destination.parts) != 2
            or destination.parts[0] not in expected_categories
            or destination.name != source.name
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise PipelineImplicitComponentCandidateError
        categories.add(destination.parts[0])
        normalized.append(
            (
                root.joinpath(*source.parts).as_posix(),
                root.joinpath(*destination.parts).as_posix(),
                digest,
            )
        )
    if categories != expected_categories:
        raise PipelineImplicitComponentCandidateError
    try:
        for category in sorted(categories, key=lambda value: value.encode("utf-8")):
            result = controller.execute(["mkdir", "--", (root / category).as_posix()])
            if type(result) is not CommandResult or result.returncode != 0:
                raise PipelineImplicitComponentCandidateError
        for source, destination, expected_digest in normalized:
            copy_result = controller.execute(["cp", "--", source, destination])
            if type(copy_result) is not CommandResult or copy_result.returncode != 0:
                raise PipelineImplicitComponentCandidateError
            digest_result = controller.execute(["sha256sum", "--", destination])
            if (
                type(digest_result) is not CommandResult
                or digest_result.returncode != 0
                or not isinstance(digest_result.stdout, str)
            ):
                raise PipelineImplicitComponentCandidateError
            digest_fields = digest_result.stdout.split(maxsplit=1)
            if not digest_fields or digest_fields[0] != expected_digest:
                raise PipelineImplicitComponentCandidateError
    except PipelineImplicitComponentCandidateError:
        raise
    except Exception:
        raise PipelineImplicitComponentCandidateError from None


__all__ = [
    "PipelineImplicitComponentCandidateConfig",
    "PipelineImplicitComponentCandidateError",
    "run_pipeline_implicit_component_candidate",
]
