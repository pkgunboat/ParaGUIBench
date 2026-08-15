"""pipeline implicit component receipt 的任务级物理闭集门禁。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from paraguibench.integrations.pipeline_implicit import (
    PIPELINE_IMPLICIT_TASK_PROTOCOLS,
)
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
    PipelineImplicitKnownNegativeManifestError,
    load_pipeline_implicit_known_negative_manifest,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    OSWorldImageManifestError,
    load_osworld_image_manifest_bytes_with_sha256,
)
from paraguibench.runstore import RunVersionVector
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runtime.pipeline_implicit_component_contracts import (
    PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL,
    PIPELINE_IMPLICIT_COMPONENT_CHECK_NAMES,
    PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL,
    PIPELINE_IMPLICIT_COMPONENT_RECEIPT_KIND,
)


PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT = Path(
    "benchmark/provenance/pipeline-implicit-component-receipts"
)
PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ALLOWLIST_PATH = Path(
    "benchmark/provenance/pipeline-implicit-component-receipt-allowlist-v1.json"
)
PIPELINE_IMPLICIT_COMPONENT_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperationExcel-008",
        "Operation-FileOperate-BatchOperationPPT-003",
        "Operation-FileOperate-CombinationDocs-002",
    }
)
_RELEASE_MANIFEST_PATH = Path("benchmark/manifests/release-v1.json")
_OSWORLD_MANIFEST_PATH = Path("environments/osworld/image-manifest.json")
_RUNTIME_SUPPORT_GUARD_PATH = Path("scripts/benchmark/runtime_support_manifest.py")
_PYPROJECT_PATH = Path("pyproject.toml")
_CLI_PATH = Path("src/paraguibench/cli/main.py")
_TASK_IDENTITY_DOMAIN = b"paraguibench-pipeline-implicit-task-v1\0"
_ENVIRONMENT_IDENTITY_DOMAIN = b"paraguibench-pipeline-implicit-environment-v1\0"
_COMPONENT_IDENTITY_DOMAIN = b"paraguibench-pipeline-implicit-component-v1\0"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAX_IDENTITY_FILE_BYTES = 16 * 1024 * 1024
_MAX_ALLOWLIST_BYTES = 64 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024
_ALLOWLIST_FIELDS = frozenset({"schema_version", "receipts"})
_ALLOWLIST_ENTRY_FIELDS = frozenset(
    {
        "receipt_sha256",
        "task_identity_sha256",
        "environment_identity_sha256",
        "component_identity_sha256",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
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
)


@dataclass(frozen=True, slots=True)
class PipelineImplicitComponentIdentity:
    """保存一个 pipeline task 的三层 receipt-neutral 身份摘要。

    task 身份绑定 selected release entry、canonical task、input/gold
    manifest 与 typed evaluator 协议；environment 身份绑定同一 held
    manifest 原始字节及 qcow/OCI 机器身份；component 身份组合前两层并
    绑定 candidate、issuer、loader、schema、guard、bridge 与 evaluator
    代码闭集。三层均不包含 receipt、allowlist、RunStore、派生 runtime
    support 或网站输出。
    """

    task_identity_sha256: str
    environment_identity_sha256: str
    component_identity_sha256: str


@dataclass(frozen=True, slots=True)
class PipelineImplicitComponentReceipt:
    """保存由 owned candidate 闭环导出的脱敏 task-scoped receipt。"""

    schema_version: int
    receipt_kind: str
    task_id: str
    run_id: str
    attempt_id: str
    execution_outcome: str
    evaluation_outcome: str
    score: float
    candidate_protocol: str
    task_evaluation_protocol: str
    environment_protocol: str
    attempt_version_vector_sha256: str
    task_identity_sha256: str
    environment_identity_sha256: str
    component_identity_sha256: str

    def __post_init__(self) -> None:
        """验证 receipt 只能表达完整 task-scoped candidate 成功事实。

        输入参数：无；读取冻结字段。
        输出返回值：终态、协议、任务与摘要闭集严格有效时返回。
        异常：PipelineImplicitComponentReceiptError：任一类型、任务、
            协议、得分、标识符或 SHA-256 不符合合同。
        """

        try:
            safe_task_id = validate_identifier("task_id", self.task_id)
            safe_run_id = validate_identifier("run_id", self.run_id)
            safe_attempt_id = validate_identifier("attempt_id", self.attempt_id)
        except (TypeError, ValueError):
            raise PipelineImplicitComponentReceiptError from None
        digests = (
            self.attempt_version_vector_sha256,
            self.task_identity_sha256,
            self.environment_identity_sha256,
            self.component_identity_sha256,
        )
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.receipt_kind != PIPELINE_IMPLICIT_COMPONENT_RECEIPT_KIND
            or safe_task_id != self.task_id
            or safe_run_id != self.run_id
            or safe_attempt_id != self.attempt_id
            or self.task_id not in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
            or self.execution_outcome != "SUCCEEDED"
            or self.evaluation_outcome != "PASSED"
            or not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not math.isfinite(float(self.score))
            or float(self.score) != 1.0
            or self.candidate_protocol != PIPELINE_IMPLICIT_COMPONENT_CANDIDATE_PROTOCOL
            or self.task_evaluation_protocol
            != PIPELINE_IMPLICIT_TASK_PROTOCOLS[self.task_id]
            or self.environment_protocol
            != PIPELINE_IMPLICIT_COMPONENT_ENVIRONMENT_PROTOCOL
            or any(
                not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None
                for value in digests
            )
        ):
            raise PipelineImplicitComponentReceiptError

    def to_dict(self) -> dict[str, Any]:
        """投影为严格闭合、无正文/路径/secret 的 JSON object。

        输入参数：无；读取冻结 receipt 字段。
        输出返回值：仅含可信终态、固定协议、版本摘要、三层身份与
            八个 ``passed`` 检查的 JSON-compatible 字典。
        """

        return {
            "schema_version": self.schema_version,
            "receipt_kind": self.receipt_kind,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "execution_outcome": self.execution_outcome,
            "evaluation_outcome": self.evaluation_outcome,
            "score": self.score,
            "candidate_protocol": self.candidate_protocol,
            "task_evaluation_protocol": self.task_evaluation_protocol,
            "environment_protocol": self.environment_protocol,
            "attempt_version_vector_sha256": (self.attempt_version_vector_sha256),
            "task_identity_sha256": self.task_identity_sha256,
            "environment_identity_sha256": self.environment_identity_sha256,
            "component_identity_sha256": self.component_identity_sha256,
            "component_checks": {
                name: "passed"
                for name in sorted(PIPELINE_IMPLICIT_COMPONENT_CHECK_NAMES)
            },
        }


class PipelineImplicitComponentReceiptError(RuntimeError):
    """表示 pipeline component receipt 数据或物理闭集无效。"""

    code = "PIPELINE_IMPLICIT_COMPONENT_RECEIPT_INVALID"

    def __init__(self) -> None:
        """构造不回显路径、正文或外部值的固定错误。

        输入参数：无。
        输出返回值：无；异常消息仅包含稳定错误码。
        """

        super().__init__(self.code)


def _run_version_vector_sha256(vector: RunVersionVector) -> str:
    """把历史 candidate RunVersionVector 投影为单向摘要。

    输入参数：vector 为已由 RunStore inspection 验证的完整六字段向量。
    输出返回值：canonical JSON 的小写 SHA-256；只记录历史 Attempt，
        不参与 current identity 或 allowlist 自引用。
    异常：PipelineImplicitComponentReceiptError：类型或字段无法序列化。
    """

    if not isinstance(vector, RunVersionVector):
        raise PipelineImplicitComponentReceiptError
    payload = {
        "source_revision": vector.source_revision,
        "agent_code_revision": vector.agent_code_revision,
        "evaluator_revision": vector.evaluator_revision,
        "evaluation_protocol": vector.evaluation_protocol,
        "environment_protocol": vector.environment_protocol,
        "environment_revision": vector.environment_revision,
    }
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, AttributeError):
        raise PipelineImplicitComponentReceiptError from None
    return hashlib.sha256(encoded).hexdigest()


def derive_pipeline_implicit_component_identity(
    repo_root: Path,
    task_id: str,
) -> PipelineImplicitComponentIdentity:
    """从当前仓库事实源派生 task-scoped 三层中性身份。

    输入参数：repo_root 为仓库根；task_id 必须是三个已实现
        pipeline implicit component candidate 之一。
    输出返回值：任务、环境、组件三份 64 位小写 SHA-256。
    异常：PipelineImplicitComponentReceiptError：任务不在闭集，或
        release/task/input/gold/environment/code 任一物理或语义事实无效。
    """

    if (
        not isinstance(repo_root, Path)
        or not isinstance(task_id, str)
        or task_id not in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        or not PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        or not PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        <= set(PIPELINE_IMPLICIT_TASK_PROTOCOLS)
    ):
        raise PipelineImplicitComponentReceiptError
    try:
        root = repo_root.resolve(strict=True)
        if not root.is_dir():
            raise OSError
        task_identity = _derive_task_identity(root, task_id)
        environment_identity = _derive_environment_identity(root)
        component_identity = _derive_component_identity(
            root,
            task_identity_sha256=task_identity,
            environment_identity_sha256=environment_identity,
        )
    except PipelineImplicitComponentReceiptError:
        raise
    except Exception:
        raise PipelineImplicitComponentReceiptError from None
    return PipelineImplicitComponentIdentity(
        task_identity_sha256=task_identity,
        environment_identity_sha256=environment_identity,
        component_identity_sha256=component_identity,
    )


def derive_pipeline_implicit_component_identity_for_environment(
    repo_root: Path,
    task_id: str,
    image_manifest: OSWorldImageManifest,
    *,
    expected_task: dict[str, Any],
    expected_task_sha256: str,
    expected_input_manifest_sha256: str,
    expected_reference_manifest_sha256: str,
    expected_reference_manifest_role: str,
) -> PipelineImplicitComponentIdentity:
    """用首次 same-FD image 对象派生 candidate 的完整三层身份。

    输入参数：repo_root/task_id 固定当前 task-scoped 仓库事实；
        image_manifest 必须是生产 loader 已绑定原始 SHA 的精确对象；
        expected 参数来自同次 PreparedTask/candidate capability，用于拒绝
        task/input/reference 路径 A→B→A 换包并区分 gold 与 audit-only。
    输出返回值：task、supplied environment 与 component 三层中性摘要；
        环境层不会再次读取 manifest 路径。
    异常：PipelineImplicitComponentReceiptError：任务、仓库、image
        对象或 receipt-neutral 代码闭集任一无效。
    """

    if (
        not isinstance(repo_root, Path)
        or not isinstance(task_id, str)
        or task_id not in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        or type(image_manifest) is not OSWorldImageManifest
        or type(expected_task) is not dict
        or any(
            not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None
            for value in (
                expected_task_sha256,
                expected_input_manifest_sha256,
                expected_reference_manifest_sha256,
            )
        )
        or expected_reference_manifest_role not in {"gold", "audit_known_negative"}
        or not PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        or not PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
        <= set(PIPELINE_IMPLICIT_TASK_PROTOCOLS)
    ):
        raise PipelineImplicitComponentReceiptError
    try:
        root = repo_root.resolve(strict=True)
        if not root.is_dir():
            raise OSError
        task_identity = _derive_task_identity(
            root,
            task_id,
            expected_task=expected_task,
            expected_task_sha256=expected_task_sha256,
            expected_input_manifest_sha256=expected_input_manifest_sha256,
            expected_reference_manifest_sha256=expected_reference_manifest_sha256,
            expected_reference_manifest_role=expected_reference_manifest_role,
        )
        environment_identity = derive_pipeline_implicit_environment_identity(
            image_manifest
        )
        component_identity = _derive_component_identity(
            root,
            task_identity_sha256=task_identity,
            environment_identity_sha256=environment_identity,
        )
    except PipelineImplicitComponentReceiptError:
        raise
    except Exception:
        raise PipelineImplicitComponentReceiptError from None
    return PipelineImplicitComponentIdentity(
        task_identity_sha256=task_identity,
        environment_identity_sha256=environment_identity,
        component_identity_sha256=component_identity,
    )


def _derive_task_identity(
    repo_root: Path,
    task_id: str,
    *,
    expected_task: dict[str, Any] | None = None,
    expected_task_sha256: str | None = None,
    expected_input_manifest_sha256: str | None = None,
    expected_reference_manifest_sha256: str | None = None,
    expected_reference_manifest_role: str | None = None,
) -> str:
    """摘要 selected release entry 与该任务的正式事实闭集。

    输入参数：repo_root 为已解析仓库根；task_id 为三任务 candidate
        成员；四个 expected 参数要么全部省略（ordinary current
        loader），要么全部由 candidate 的 PreparedTask/capability 提供。
    输出返回值：domain-separated task identity SHA-256。
    异常：PipelineImplicitComponentReceiptError：release 重复、task SHA、
        task UID、input/reference 角色或 typed protocol 不一致。
    """

    supplied_expectations = (
        expected_task,
        expected_task_sha256,
        expected_input_manifest_sha256,
        expected_reference_manifest_sha256,
        expected_reference_manifest_role,
    )
    if any(value is not None for value in supplied_expectations) and (
        type(expected_task) is not dict
        or any(
            not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None
            for value in supplied_expectations[1:4]
        )
        or expected_reference_manifest_role not in {"gold", "audit_known_negative"}
    ):
        raise PipelineImplicitComponentReceiptError
    release = _decode_json_object(
        _read_repository_file(
            repo_root,
            _RELEASE_MANIFEST_PATH,
            maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
        )
    )
    entries = release.get("tasks")
    if release.get("release_id") != "release-v1" or not isinstance(entries, list):
        raise PipelineImplicitComponentReceiptError
    selected: dict[str, Any] | None = None
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise PipelineImplicitComponentReceiptError
        candidate_task_id = entry.get("task_id")
        if not isinstance(candidate_task_id, str) or candidate_task_id in seen:
            raise PipelineImplicitComponentReceiptError
        seen.add(candidate_task_id)
        if candidate_task_id == task_id:
            selected = entry
    if selected is None:
        raise PipelineImplicitComponentReceiptError
    task_path = _safe_relative_path(selected.get("path"))
    release_task_sha256 = selected.get("sha256")
    task_uid = selected.get("task_uid")
    if (
        not isinstance(release_task_sha256, str)
        or _SHA256_PATTERN.fullmatch(release_task_sha256) is None
        or not isinstance(task_uid, str)
        or not task_uid
    ):
        raise PipelineImplicitComponentReceiptError
    task_payload = _read_repository_file(
        repo_root,
        task_path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    task_payload_sha256 = hashlib.sha256(task_payload).hexdigest()
    if task_payload_sha256 != release_task_sha256:
        raise PipelineImplicitComponentReceiptError
    task = _decode_json_object(task_payload)
    if task.get("task_id") != task_id or task.get("task_uid") != task_uid:
        raise PipelineImplicitComponentReceiptError
    if expected_task is not None and (
        task != expected_task or task_payload_sha256 != expected_task_sha256
    ):
        raise PipelineImplicitComponentReceiptError

    input_path = Path("benchmark/assets/manifests") / f"{task_id}.json"
    if task_id == "Operation-FileOperate-CombinationDocs-002":
        reference_path = Path(COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH)
        reference_role = "audit_known_negative"
        valid_reference_declaration = (
            task.get("known_negative_manifest") == reference_path.as_posix()
            and "gold_manifest" not in task
        )
    else:
        reference_path = Path("benchmark/gold/manifests") / f"{task_id}.json"
        reference_role = "gold"
        valid_reference_declaration = (
            task.get("gold_manifest") == reference_path.as_posix()
            and "known_negative_manifest" not in task
        )
    if (
        task.get("asset_manifest") != input_path.as_posix()
        or not valid_reference_declaration
        or (
            expected_reference_manifest_role is not None
            and expected_reference_manifest_role != reference_role
        )
    ):
        raise PipelineImplicitComponentReceiptError
    input_payload = _read_repository_file(
        repo_root,
        input_path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    reference_payload = _read_repository_file(
        repo_root,
        reference_path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    if expected_task is not None and (
        hashlib.sha256(input_payload).hexdigest() != expected_input_manifest_sha256
        or hashlib.sha256(reference_payload).hexdigest()
        != expected_reference_manifest_sha256
    ):
        raise PipelineImplicitComponentReceiptError
    input_manifest = _decode_json_object(input_payload)
    if (
        type(input_manifest.get("schema_version")) is not int
        or input_manifest["schema_version"] != 1
        or input_manifest.get("asset_set_id") != task_id
        or not isinstance(input_manifest.get("files"), list)
        or not input_manifest["files"]
    ):
        raise PipelineImplicitComponentReceiptError
    if reference_role == "audit_known_negative":
        try:
            reference_manifest = load_pipeline_implicit_known_negative_manifest(
                reference_payload
            )
        except PipelineImplicitKnownNegativeManifestError:
            raise PipelineImplicitComponentReceiptError from None
        if (
            reference_manifest.task_id != task_id
            or reference_manifest.task_uid != task_uid
            or not reference_manifest.entries
        ):
            raise PipelineImplicitComponentReceiptError
    else:
        reference_manifest = _decode_json_object(reference_payload)
        if (
            type(reference_manifest.get("schema_version")) is not int
            or reference_manifest["schema_version"] != 1
            or reference_manifest.get("task_id") != task_id
            or reference_manifest.get("task_uid") != task_uid
            or reference_manifest.get("manifest_role") != "gold"
            or not isinstance(reference_manifest.get("entries"), list)
            or not reference_manifest["entries"]
        ):
            raise PipelineImplicitComponentReceiptError

    digest = hashlib.sha256(_TASK_IDENTITY_DOMAIN)
    selected_payload = json.dumps(
        selected,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    protocol_id = PIPELINE_IMPLICIT_TASK_PROTOCOLS[task_id]
    for label, relative, payload in (
        ("release-entry", _RELEASE_MANIFEST_PATH, selected_payload),
        ("task", task_path, task_payload),
        ("input", input_path, input_payload),
        ("reference", reference_path, reference_payload),
    ):
        digest.update(label.encode("ascii"))
        digest.update(b"\0")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    digest.update(b"task-evaluation-protocol\0")
    digest.update(protocol_id.encode("utf-8"))
    digest.update(b"\0reference-role\0")
    digest.update(reference_role.encode("ascii"))
    return digest.hexdigest()


def _derive_environment_identity(repo_root: Path) -> str:
    """从同一 held manifest 字节派生 OSWorld 环境身份。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：绑定 manifest 原始 SHA、desktop 协议、extracted qcow
        SHA 状态与 digest-pinned OCI 引用的环境 SHA-256。
    异常：PipelineImplicitComponentReceiptError：manifest 字段、digest
        或 pending/verified 状态组合无效。
    """

    payload = _read_repository_file(
        repo_root,
        _OSWORLD_MANIFEST_PATH,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    try:
        manifest, manifest_sha256 = load_osworld_image_manifest_bytes_with_sha256(
            payload
        )
    except OSWorldImageManifestError:
        raise PipelineImplicitComponentReceiptError from None
    if manifest.manifest_sha256 != manifest_sha256:
        raise PipelineImplicitComponentReceiptError
    return derive_pipeline_implicit_environment_identity(manifest)


def derive_pipeline_implicit_environment_identity(
    image_manifest: OSWorldImageManifest,
) -> str:
    """从首次 same-FD 解析对象组合 receipt 环境身份。

    输入参数：image_manifest 必须是生产 loader 产生、携带同源原始
        ``manifest_sha256`` 的精确 OSWorldImageManifest。
    输出返回值：绑定原始 manifest、desktop 协议、extracted qcow2
        声明与 digest-pinned OCI 的 domain-separated SHA-256。
    异常：PipelineImplicitComponentReceiptError：对象类型、同源 SHA、
        协议、qcow 状态或 OCI digest 无效。
    """

    if type(image_manifest) is not OSWorldImageManifest:
        raise PipelineImplicitComponentReceiptError
    extracted_sha256 = image_manifest.extracted_sha256
    valid_extracted = (
        image_manifest.schema_version == 1
        and extracted_sha256 is None
        and image_manifest.materialization_status == "must_verify_before_live_run"
        and image_manifest.materialization_spec is None
    ) or (
        image_manifest.schema_version == 2
        and isinstance(extracted_sha256, str)
        and _SHA256_PATTERN.fullmatch(extracted_sha256) is not None
        and image_manifest.materialization_status
        in {
            "must_verify_before_live_run",
            "verified_reproducible_materialization",
        }
        and image_manifest.materialization_recipe_ready
        and (
            image_manifest.materialization_status
            != "verified_reproducible_materialization"
            or image_manifest.live_run_ready
        )
    )
    manifest_sha256 = image_manifest.manifest_sha256
    container_image = image_manifest.container_image
    if (
        not isinstance(manifest_sha256, str)
        or _SHA256_PATTERN.fullmatch(manifest_sha256) is None
        or not isinstance(image_manifest.environment_id, str)
        or not image_manifest.environment_id
        or "osworld.desktop.v1" not in image_manifest.protocol_ids
        or len(image_manifest.protocol_ids) != len(set(image_manifest.protocol_ids))
        or not valid_extracted
        or not isinstance(container_image, str)
        or container_image.count("@sha256:") != 1
        or _SHA256_PATTERN.fullmatch(container_image.rsplit("@sha256:", 1)[1]) is None
    ):
        raise PipelineImplicitComponentReceiptError
    digest = hashlib.sha256(_ENVIRONMENT_IDENTITY_DOMAIN)
    digest.update(bytes.fromhex(manifest_sha256))
    for label, value in (
        ("environment", image_manifest.environment_id),
        ("protocol", "osworld.desktop.v1"),
        ("materialization_status", image_manifest.materialization_status),
        ("qcow2", extracted_sha256 or "pending"),
        ("container", container_image),
    ):
        digest.update(label.encode("ascii"))
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _derive_component_identity(
    repo_root: Path,
    *,
    task_identity_sha256: str,
    environment_identity_sha256: str,
) -> str:
    """摘要 receipt-neutral 生产代码与 schema 的稳定物理闭集。

    输入参数：repo_root 为仓库根；task/environment 摘要来自同次派生。
    输出返回值：包含 task/environment 与路径化逐文件摘要的组件 SHA。
    异常：PipelineImplicitComponentReceiptError：代码树、schema 树、
        固定 guard/CLI/pyproject 缺失、symlink 或读取期闭集漂移。
    """

    if any(
        not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None
        for value in (task_identity_sha256, environment_identity_sha256)
    ):
        raise PipelineImplicitComponentReceiptError
    paths_before = _collect_component_paths(repo_root)
    digest = hashlib.sha256(_COMPONENT_IDENTITY_DOMAIN)
    digest.update(bytes.fromhex(task_identity_sha256))
    digest.update(bytes.fromhex(environment_identity_sha256))
    for relative in paths_before:
        payload = _read_repository_file(
            repo_root,
            relative,
            maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
        )
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    if _collect_component_paths(repo_root) != paths_before:
        raise PipelineImplicitComponentReceiptError
    return digest.hexdigest()


def _collect_component_paths(repo_root: Path) -> tuple[Path, ...]:
    """枚举不含 receipt、allowlist、RunStore 输出和派生数据的闭集。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：runtime、pipeline/OSWorld bridge、evaluator、RunStore
        inspection、schemas、CLI、guard 与 pyproject 的稳定排序路径。
    异常：PipelineImplicitComponentReceiptError：任一树为空或含 symlink、
        特殊节点、硬链接或目录读取期漂移。
    """

    paths = [_PYPROJECT_PATH, _RUNTIME_SUPPORT_GUARD_PATH, _CLI_PATH]
    # Candidate 通过 benchmark preparation、runtime、OSWorld bridge、
    # evaluator、RunStore inspection 与 CLI 共享链执行。覆盖完整公开
    # Python 树能避免遗漏间接调用，同时 receipt/allowlist/RunStore 输出、
    # runtime-support 与网站派生文件均不在该树中，不形成自引用。
    paths.extend(
        _collect_regular_tree_files(
            repo_root,
            Path("src/paraguibench"),
            suffix=".py",
        )
    )
    paths.extend(
        _collect_regular_tree_files(
            repo_root,
            Path("benchmark/schemas"),
            suffix=".json",
        )
    )
    result = tuple(sorted(set(paths), key=lambda value: value.as_posix()))
    if not result:
        raise PipelineImplicitComponentReceiptError
    return result


def _collect_regular_tree_files(
    repo_root: Path,
    relative_root: Path,
    *,
    suffix: str,
) -> list[Path]:
    """使用 held dirfd 递归枚举指定后缀的稳定普通文件树。

    输入参数：repo_root/relative_root 定位树根；suffix 为文件后缀。
    输出返回值：按 POSIX 相对路径排序的单链接普通文件列表。
    异常：PipelineImplicitComponentReceiptError：路径链或树内出现
        symlink、特殊节点、多硬链接、空目标闭集或枚举期漂移。
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0 or directory == 0:
        raise PipelineImplicitComponentReceiptError
    descriptors: list[int] = []
    paths: list[Path] = []

    def walk(descriptor: int, relative: Path) -> None:
        """递归验证一个已持有目录并收集目标文件。

        输入参数：descriptor 为 held 目录 FD；relative 为仓库相对目录。
        输出返回值：无；匹配路径追加到外层 ``paths``。
        """

        before = os.fstat(descriptor)
        names = sorted(os.listdir(descriptor))
        for name in names:
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            child_relative = relative / name
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=descriptor,
                )
                try:
                    walk(child, child_relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise OSError
                if child_relative.suffix == suffix:
                    paths.append(child_relative)
            else:
                raise OSError
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_mtime_ns", "st_ctime_ns")
        if any(
            getattr(before, field) != getattr(after, field) for field in stable_fields
        ):
            raise OSError

    try:
        descriptors.append(
            os.open(repo_root, os.O_RDONLY | directory | nofollow | cloexec)
        )
        for part in relative_root.parts:
            descriptors.append(
                os.open(
                    part,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=descriptors[-1],
                )
            )
        walk(descriptors[-1], relative_root)
    except (OSError, ValueError):
        raise PipelineImplicitComponentReceiptError from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    if not paths:
        raise PipelineImplicitComponentReceiptError
    return sorted(paths, key=lambda value: value.as_posix())


def _safe_relative_path(value: object) -> Path:
    """把 JSON 路径字段收紧为规范安全相对路径。

    输入参数：value 为候选 POSIX 相对路径字符串。
    输出返回值：不含点段、反斜杠或绝对语义的 ``Path``。
    异常：PipelineImplicitComponentReceiptError：路径形式无效。
    """

    if not isinstance(value, str) or not value or "\\" in value:
        raise PipelineImplicitComponentReceiptError
    relative = Path(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value
    ):
        raise PipelineImplicitComponentReceiptError
    return relative


def _decode_json_object(payload: bytes) -> dict[str, Any]:
    """解码字段唯一、有限且顶层为 object 的 UTF-8 JSON。

    输入参数：payload 为 held FD 得到的稳定原始字节。
    输出返回值：不含重复键或非有限常量的字典。
    异常：PipelineImplicitComponentReceiptError：编码、语法、深度、
        重复字段、NaN/Infinity 或顶层类型无效。
    """

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_raise_invalid_json,
        )
    except PipelineImplicitComponentReceiptError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise PipelineImplicitComponentReceiptError from None
    if not isinstance(value, dict):
        raise PipelineImplicitComponentReceiptError
    return value


def load_trusted_pipeline_implicit_component_receipts(
    repo_root: Path,
    *,
    expected_environment_identity_sha256: str | None = None,
) -> frozenset[str]:
    """读取 task-scoped allowlist，返回当前可信 component 任务集合。

    输入参数：repo_root 为包含 provenance 的仓库根目录。
    输出返回值：当前通过物理闭集与身份复核的任务 ID 不可变集合；
        初始空 allowlist 返回空集合，且不会创建 receipt 目录。
    异常：PipelineImplicitComponentReceiptError：allowlist 类型、JSON、
        字段闭集或 receipt 根物理形态不合法。
    """

    if not isinstance(repo_root, Path) or (
        expected_environment_identity_sha256 is not None
        and (
            not isinstance(expected_environment_identity_sha256, str)
            or _SHA256_PATTERN.fullmatch(expected_environment_identity_sha256) is None
        )
    ):
        raise PipelineImplicitComponentReceiptError
    try:
        root = repo_root.resolve(strict=True)
        if not root.is_dir():
            raise OSError
        allowlist_payload = _read_repository_file(
            root,
            PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ALLOWLIST_PATH,
            maximum_bytes=_MAX_ALLOWLIST_BYTES,
        )
        allowlist = _decode_json_object(allowlist_payload)
    except PipelineImplicitComponentReceiptError:
        raise
    except Exception:
        raise PipelineImplicitComponentReceiptError from None
    if (
        not isinstance(allowlist, dict)
        or frozenset(allowlist) != _ALLOWLIST_FIELDS
        or type(allowlist.get("schema_version")) is not int
        or allowlist["schema_version"] != 1
        or not isinstance(allowlist.get("receipts"), dict)
    ):
        raise PipelineImplicitComponentReceiptError
    receipts = allowlist["receipts"]
    if not receipts:
        _validate_receipt_directory_closure(
            root,
            expected_names=frozenset(),
        )
        return frozenset()
    if len(receipts) > len(PIPELINE_IMPLICIT_COMPONENT_TASK_IDS):
        raise PipelineImplicitComponentReceiptError

    expected_names: set[str] = set()
    for task_id, entry in receipts.items():
        if (
            not isinstance(task_id, str)
            or task_id not in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
            or not isinstance(entry, dict)
            or frozenset(entry) != _ALLOWLIST_ENTRY_FIELDS
            or any(
                not isinstance(entry[field], str)
                or _SHA256_PATTERN.fullmatch(entry[field]) is None
                for field in _ALLOWLIST_ENTRY_FIELDS
            )
        ):
            raise PipelineImplicitComponentReceiptError
        expected_names.add(f"{task_id}.json")
    directory_identity = _validate_receipt_directory_closure(
        root,
        expected_names=frozenset(expected_names),
    )
    if directory_identity is None:
        raise PipelineImplicitComponentReceiptError

    identities: dict[str, PipelineImplicitComponentIdentity] = {}
    receipt_payloads: dict[str, bytes] = {}
    for task_id in sorted(receipts):
        entry = receipts[task_id]
        identity = _derive_identity_for_receipt_loader(
            root,
            task_id,
            expected_environment_identity_sha256=(expected_environment_identity_sha256),
        )
        if (
            entry["task_identity_sha256"] != identity.task_identity_sha256
            or entry["environment_identity_sha256"]
            != identity.environment_identity_sha256
            or entry["component_identity_sha256"] != identity.component_identity_sha256
        ):
            raise PipelineImplicitComponentReceiptError
        relative = PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT / f"{task_id}.json"
        receipt_payload = _read_repository_file(
            root,
            relative,
            maximum_bytes=_MAX_RECEIPT_BYTES,
        )
        if hashlib.sha256(receipt_payload).hexdigest() != entry["receipt_sha256"]:
            raise PipelineImplicitComponentReceiptError
        receipt = _validate_receipt_payload(receipt_payload)
        if (
            receipt.task_id != task_id
            or receipt.task_identity_sha256 != identity.task_identity_sha256
            or receipt.environment_identity_sha256
            != identity.environment_identity_sha256
            or receipt.component_identity_sha256 != identity.component_identity_sha256
        ):
            raise PipelineImplicitComponentReceiptError
        identities[task_id] = identity
        receipt_payloads[task_id] = receipt_payload

    if (
        _read_repository_file(
            root,
            PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ALLOWLIST_PATH,
            maximum_bytes=_MAX_ALLOWLIST_BYTES,
        )
        != allowlist_payload
        or _validate_receipt_directory_closure(
            root,
            expected_names=frozenset(expected_names),
        )
        != directory_identity
    ):
        raise PipelineImplicitComponentReceiptError
    for task_id in sorted(receipts):
        relative = PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT / f"{task_id}.json"
        if (
            _read_repository_file(
                root,
                relative,
                maximum_bytes=_MAX_RECEIPT_BYTES,
            )
            != receipt_payloads[task_id]
            or _derive_identity_for_receipt_loader(
                root,
                task_id,
                expected_environment_identity_sha256=(
                    expected_environment_identity_sha256
                ),
            )
            != identities[task_id]
        ):
            raise PipelineImplicitComponentReceiptError
    return frozenset(receipts)


def _derive_identity_for_receipt_loader(
    repo_root: Path,
    task_id: str,
    *,
    expected_environment_identity_sha256: str | None,
) -> PipelineImplicitComponentIdentity:
    """以调用方首次 image snapshot 或当前路径派生三层身份。

    输入参数：repo_root/task_id 定位 task 与代码事实；expected environment
        identity 非空时来自 CLI/candidate 首次 same-FD manifest 对象。
    输出返回值：task、选定环境及二者参与的 component identity。
    异常：PipelineImplicitComponentReceiptError：任一事实或摘要无效。
    """

    if expected_environment_identity_sha256 is None:
        return derive_pipeline_implicit_component_identity(repo_root, task_id)
    task_identity = _derive_task_identity(repo_root, task_id)
    component_identity = _derive_component_identity(
        repo_root,
        task_identity_sha256=task_identity,
        environment_identity_sha256=expected_environment_identity_sha256,
    )
    return PipelineImplicitComponentIdentity(
        task_identity_sha256=task_identity,
        environment_identity_sha256=expected_environment_identity_sha256,
        component_identity_sha256=component_identity,
    )


def _validate_receipt_payload(payload: bytes) -> PipelineImplicitComponentReceipt:
    """解析 receipt 并验证严格字段及八项固定成功检查。

    输入参数：payload 为 held FD 读取且已由 allowlist 摘要绑定的字节。
    输出返回值：通过 task→protocol、终态、score 与三身份类型检查的 DTO。
    异常：PipelineImplicitComponentReceiptError：额外/缺失字段、敏感
        扩展字段、检查状态或 DTO 合同无效。
    """

    raw = _decode_json_object(payload)
    checks = raw.get("component_checks")
    if (
        frozenset(raw) != _RECEIPT_FIELDS
        or not isinstance(checks, dict)
        or frozenset(checks) != PIPELINE_IMPLICIT_COMPONENT_CHECK_NAMES
        or any(value != "passed" for value in checks.values())
    ):
        raise PipelineImplicitComponentReceiptError
    try:
        return PipelineImplicitComponentReceipt(
            schema_version=raw["schema_version"],
            receipt_kind=raw["receipt_kind"],
            task_id=raw["task_id"],
            run_id=raw["run_id"],
            attempt_id=raw["attempt_id"],
            execution_outcome=raw["execution_outcome"],
            evaluation_outcome=raw["evaluation_outcome"],
            score=raw["score"],
            candidate_protocol=raw["candidate_protocol"],
            task_evaluation_protocol=raw["task_evaluation_protocol"],
            environment_protocol=raw["environment_protocol"],
            attempt_version_vector_sha256=raw["attempt_version_vector_sha256"],
            task_identity_sha256=raw["task_identity_sha256"],
            environment_identity_sha256=raw["environment_identity_sha256"],
            component_identity_sha256=raw["component_identity_sha256"],
        )
    except (KeyError, TypeError):
        raise PipelineImplicitComponentReceiptError from None


def _read_repository_file(
    repo_root: Path,
    relative_path: Path,
    *,
    maximum_bytes: int,
) -> bytes:
    """通过逐级 nofollow dirfd 链读取一个单链接普通文件。

    输入参数：repo_root/relative_path 定位仓库文件；maximum_bytes
        为允许的最大字节数。
    输出返回值：从已验证 FD 读取的完整原始字节。
    异常：PipelineImplicitComponentReceiptError：symlink、特殊节点、
        多硬链接、超限、短读或读取期身份漂移。
    """

    if (
        not isinstance(repo_root, Path)
        or not isinstance(relative_path, Path)
        or relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
    ):
        raise PipelineImplicitComponentReceiptError
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0 or directory == 0:
        raise PipelineImplicitComponentReceiptError
    directory_descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        directory_descriptors.append(
            os.open(repo_root, os.O_RDONLY | directory | nofollow | cloexec)
        )
        for part in relative_path.parts[:-1]:
            directory_descriptors.append(
                os.open(
                    part,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=directory_descriptors[-1],
                )
            )
        file_descriptor = os.open(
            relative_path.name,
            os.O_RDONLY | nofollow | cloexec,
            dir_fd=directory_descriptors[-1],
        )
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            raise OSError
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise OSError
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            raise OSError
        after = os.fstat(file_descriptor)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field) for field in stable_fields
        ):
            raise OSError
        return b"".join(chunks)
    except (OSError, ValueError):
        raise PipelineImplicitComponentReceiptError from None
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        for descriptor in reversed(directory_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _validate_receipt_directory_closure(
    repo_root: Path,
    *,
    expected_names: frozenset[str],
) -> tuple[int, int, int, int, int] | None:
    """通过 held nofollow dirfd 验证 receipt 物理目录闭集。

    输入参数：repo_root 为仓库根；expected_names 为 allowlist 机械派生
        的全部 receipt 文件名。
    输出返回值：目录缺失且预期为空时返回 ``None``；否则返回稳定
        ``(dev, ino, mode, mtime_ns, ctime_ns)`` 身份。
    异常：PipelineImplicitComponentReceiptError：symlink、特殊节点或
        任意未授权物理条目、读取期目录漂移存在。
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0 or directory == 0:
        raise PipelineImplicitComponentReceiptError
    descriptors: list[int] = []
    try:
        descriptors.append(
            os.open(repo_root, os.O_RDONLY | directory | nofollow | cloexec)
        )
        parts = PIPELINE_IMPLICIT_COMPONENT_RECEIPT_ROOT.parts
        for part in parts:
            try:
                descriptors.append(
                    os.open(
                        part,
                        os.O_RDONLY | directory | nofollow | cloexec,
                        dir_fd=descriptors[-1],
                    )
                )
            except FileNotFoundError:
                if not expected_names and part == parts[-1]:
                    return None
                raise
        before = os.fstat(descriptors[-1])
        names = os.listdir(descriptors[-1])
        after = os.fstat(descriptors[-1])
        stable_fields = ("st_dev", "st_ino", "st_mode", "st_mtime_ns", "st_ctime_ns")
        if (
            not stat.S_ISDIR(before.st_mode)
            or any(
                getattr(before, field) != getattr(after, field)
                for field in stable_fields
            )
            or set(names) != expected_names
        ):
            raise OSError
        for name in names:
            metadata = os.stat(name, dir_fd=descriptors[-1], follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OSError
        return (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
    except (OSError, ValueError):
        raise PipelineImplicitComponentReceiptError from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _raise_invalid_json(_value: str) -> None:
    """拒绝 JSON 的 NaN、Infinity 与负 Infinity 常量。

    输入参数：_value 为 JSON decoder 识别到的非常量文本。
    输出返回值：无；始终抛出固定脱敏错误。
    """

    raise PipelineImplicitComponentReceiptError


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """把 JSON object 转为字典并拒绝重复键。

    输入参数：pairs 为 decoder 保序提供的键值对。
    输出返回值：无重复键的字典。
    异常：PipelineImplicitComponentReceiptError：发现重复键。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PipelineImplicitComponentReceiptError
        result[key] = value
    return result
