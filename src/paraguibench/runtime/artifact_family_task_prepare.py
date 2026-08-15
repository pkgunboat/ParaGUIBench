"""在 Docker 前绑定 13 个 OSWorld artifact-family 任务准备能力。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
    ArtifactFamilyTaskPrepareSpec,
)
from paraguibench.runtime.assets import (
    AssetManifest,
    AssetManifestError,
    TaskAssetMode,
    resolve_task_assets,
)


ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS = (
    "artifact_family.source_start_context_ambiguous"
)
ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED = "artifact_family.input_path_inferred"
ARTIFACT_FAMILY_BLOCKER_INPUT_INTEGRITY_UNVERIFIED = (
    "artifact_family.input_integrity_unverified"
)
ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED = (
    "artifact_family.input_license_unverified"
)
ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_MISSING = (
    "artifact_family.strict_asset_manifest_missing"
)
ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_INVALID = (
    "artifact_family.strict_asset_manifest_invalid"
)

_ERROR_NOT_READY = "ARTIFACT_FAMILY_TASK_PREPARE_NOT_READY"
_ERROR_CONTRACT_INVALID = "ARTIFACT_FAMILY_TASK_PREPARE_CONTRACT_INVALID"
_ERROR_RUNTIME_BINDING_INVALID = "ARTIFACT_FAMILY_TASK_PREPARE_RUNTIME_BINDING_INVALID"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BLOCKER_ORDER = (
    ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS,
    ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED,
    ARTIFACT_FAMILY_BLOCKER_INPUT_INTEGRITY_UNVERIFIED,
    ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED,
    ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_MISSING,
    ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_INVALID,
)
_FORBIDDEN_TASK_FIELDS = frozenset(
    {
        "argv",
        "command",
        "commands",
        "input_path",
        "output_path",
        "prepare_action",
        "prepare_actions",
        "prepare_command",
        "prepare_commands",
    }
)


class ArtifactFamilyTaskPrepareCapabilityError(RuntimeError):
    """表示 artifact-family 准备能力在启动 Docker 前失败关闭。"""


@dataclass(frozen=True, slots=True)
class ArtifactFamilyTaskPrepareBinding:
    """保存已由可信 preflight 闭合的最小运行时准备绑定。"""

    task_id: str
    input_draft_sha256: str
    asset_manifest_sha256: str
    relative_paths: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        """验证绑定身份、摘要和相对路径闭集。

        输入参数：
            无；读取冻结实例字段。
        输出返回值：
            无；字段完整且安全时完成构造。
        异常：
            ValueError：任务身份、摘要或路径闭集无效。
        """

        if (
            not isinstance(self.task_id, str)
            or not self.task_id
            or _SHA256_PATTERN.fullmatch(self.input_draft_sha256) is None
            or _SHA256_PATTERN.fullmatch(self.asset_manifest_sha256) is None
            or not self.relative_paths
            or len(self.relative_paths) != len(set(self.relative_paths))
        ):
            raise ValueError("artifact-family runtime binding 无效")
        for relative_path in self.relative_paths:
            _validate_relative_path(relative_path)


@dataclass(frozen=True, slots=True)
class ArtifactFamilyTaskPrepareCapability:
    """公开不含来源、host cache 或 guest 路径的能力检查结果。"""

    task_id: str
    input_count: int
    inferred_path_count: int
    unverified_integrity_count: int
    blocker_ids: tuple[str, ...]
    _binding: ArtifactFamilyTaskPrepareBinding | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """验证计数、阻断码顺序和 ready/binding 配对。

        输入参数：
            无；读取冻结实例字段。
        输出返回值：
            无；能力结果自洽时完成构造。
        异常：
            ValueError：计数、阻断码或内部绑定不一致。
        """

        if (
            not self.task_id
            or self.input_count <= 0
            or not 0 <= self.inferred_path_count <= self.input_count
            or not 0 <= self.unverified_integrity_count <= self.input_count
            or tuple(
                blocker for blocker in _BLOCKER_ORDER if blocker in self.blocker_ids
            )
            != self.blocker_ids
            or len(self.blocker_ids) != len(set(self.blocker_ids))
            or (not self.blocker_ids) != (self._binding is not None)
        ):
            raise ValueError("artifact-family capability 结果无效")

    @property
    def ready(self) -> bool:
        """返回当前任务是否具备进入 Docker 生命周期的准备能力。

        输入参数：
            无。
        输出返回值：
            无 blocker 且存在内部严格绑定时返回 ``True``。
        """

        return not self.blocker_ids


def inspect_artifact_family_task_prepare_capability(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
) -> ArtifactFamilyTaskPrepareCapability | None:
    """只读检查 13-task 的 draft、canonical 与严格 manifest 闭包。

    输入参数：
        repo_root：包含冻结 catalog、canonical 和 asset draft 的仓库根。
        task：已经过 release 身份校验的可信 canonical task。
    输出返回值：
        非 13-task 返回 ``None``；命中时返回只含稳定 ID/计数的能力
        结果。
    异常：
        ArtifactFamilyTaskPrepareCapabilityError：仓库文件、身份或 draft
            合同损坏；异常文本不会包含 host、guest 或远端路径。
    """

    if not isinstance(task, Mapping):
        raise ArtifactFamilyTaskPrepareCapabilityError(_ERROR_CONTRACT_INVALID)
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ArtifactFamilyTaskPrepareCapabilityError(_ERROR_CONTRACT_INVALID)
    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS.get(task_id)
    if spec is None:
        return None
    try:
        _validate_canonical_task(task, spec)
        draft_bytes = _read_repository_file(
            repo_root,
            PurePosixPath(spec.input_draft_relative_path),
        )
        if hashlib.sha256(draft_bytes).hexdigest() != spec.input_draft_sha256:
            raise ValueError("draft digest mismatch")
        raw_draft = json.loads(draft_bytes)
        entries = _validate_input_draft(raw_draft, spec)
        inferred_count = sum(entry["path_status"] == "inferred" for entry in entries)
        unverified_count = sum(
            not _entry_integrity_is_verified(entry) for entry in entries
        )
        blockers: set[str] = set()
        if spec.prepare_status != "actionable_when_assets_verified":
            blockers.add(ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS)
        if inferred_count:
            blockers.add(ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED)
        if unverified_count:
            blockers.add(ARTIFACT_FAMILY_BLOCKER_INPUT_INTEGRITY_UNVERIFIED)
        if not _draft_license_is_verified(raw_draft):
            blockers.add(ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED)

        binding: ArtifactFamilyTaskPrepareBinding | None = None
        manifest_reference = task.get("asset_manifest")
        if manifest_reference is None:
            blockers.add(ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_MISSING)
        else:
            binding = _bind_strict_asset_manifest(
                repo_root=repo_root,
                task=task,
                spec=spec,
                raw_draft=raw_draft,
            )
            if binding is None:
                blockers.add(ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_INVALID)
        ordered_blockers = tuple(
            blocker for blocker in _BLOCKER_ORDER if blocker in blockers
        )
        if ordered_blockers:
            binding = None
        return ArtifactFamilyTaskPrepareCapability(
            task_id=task_id,
            input_count=len(entries),
            inferred_path_count=inferred_count,
            unverified_integrity_count=unverified_count,
            blocker_ids=ordered_blockers,
            _binding=binding,
        )
    except ArtifactFamilyTaskPrepareCapabilityError:
        raise
    except (AssetManifestError, OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ArtifactFamilyTaskPrepareCapabilityError(
            _ERROR_CONTRACT_INVALID
        ) from None


def preflight_artifact_family_task_prepare(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
) -> ArtifactFamilyTaskPrepareBinding | None:
    """在 Docker、guest、Agent、凭据和 RunStore 前强制执行能力门禁。

    输入参数：
        repo_root：发布仓库根。
        task：可信 canonical task。
    输出返回值：
        非 13-task 返回 ``None``；全部合同闭合时返回脱敏运行时
        绑定。
    异常：
        ArtifactFamilyTaskPrepareCapabilityError：任一固定 blocker 存在。
    """

    capability = inspect_artifact_family_task_prepare_capability(
        repo_root=repo_root,
        task=task,
    )
    if capability is None:
        return None
    if not capability.ready or capability._binding is None:
        raise ArtifactFamilyTaskPrepareCapabilityError(_ERROR_NOT_READY)
    return capability._binding


def validate_artifact_family_task_prepare_runtime_binding(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
    task_assets: Any,
    binding: ArtifactFamilyTaskPrepareBinding | None,
) -> ArtifactFamilyTaskPrepareBinding | None:
    """在 guest I/O 前复核 CLI 绑定与实际 canonical manifest 身份。

    输入参数：
        repo_root：environment 使用的同一发布仓库根。
        task：AttemptRunner 传入的可信 canonical task。
        task_assets：environment 刚由统一 resolver 得到的强类型资产
            结果。
        binding：CLI pre-Docker gate 生成并注入的可选脱敏绑定。
    输出返回值：
        非 13-task 且未错误注入 binding 时返回 ``None``；命中且摘要、
        任务与文件闭集一致时原样返回 binding。
    异常：
        ArtifactFamilyTaskPrepareCapabilityError：跨任务注入、绑定缺失、
            manifest 漂移或资产模式不匹配；错误文本不含任何路径。
    """

    if not isinstance(task, Mapping):
        raise ArtifactFamilyTaskPrepareCapabilityError(_ERROR_RUNTIME_BINDING_INVALID)
    task_id = task.get("task_id")
    spec = (
        ARTIFACT_FAMILY_TASK_PREPARE_SPECS.get(task_id)
        if isinstance(task_id, str)
        else None
    )
    if spec is None:
        if binding is not None:
            raise ArtifactFamilyTaskPrepareCapabilityError(
                _ERROR_RUNTIME_BINDING_INVALID
            )
        return None
    try:
        manifest = getattr(task_assets, "manifest", None)
        mode = getattr(task_assets, "mode", None)
        manifest_reference = task.get("asset_manifest")
        expected_paths = tuple(item.asset_relative_path for item in spec.asset_bindings)
        if (
            spec.prepare_status != "actionable_when_assets_verified"
            or not isinstance(binding, ArtifactFamilyTaskPrepareBinding)
            or binding.task_id != spec.task_id
            or binding.input_draft_sha256 != spec.input_draft_sha256
            or binding.relative_paths != expected_paths
            or mode is not TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
            or not isinstance(manifest, AssetManifest)
            or manifest.asset_set_id != spec.task_id
            or len(manifest.files) != len(expected_paths)
            or {item.path for item in manifest.files} != set(expected_paths)
            or not isinstance(manifest_reference, str)
            or not manifest_reference
        ):
            raise ValueError("runtime binding mismatch")
        manifest_bytes = _read_repository_file(
            repo_root,
            PurePosixPath(manifest_reference),
        )
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_sha256 != binding.asset_manifest_sha256:
            raise ValueError("runtime manifest digest mismatch")
        return binding
    except (OSError, TypeError, ValueError):
        raise ArtifactFamilyTaskPrepareCapabilityError(
            _ERROR_RUNTIME_BINDING_INVALID
        ) from None


def _validate_canonical_task(
    task: Mapping[str, Any],
    spec: ArtifactFamilyTaskPrepareSpec,
) -> None:
    """验证 canonical 身份及旧 prepare 引用未发生静默漂移。

    输入参数：
        task：按 task_id 命中的 canonical mapping。
        spec：冻结 task-prepare 规格。
    输出返回值：
        无；字段和引用摘要精确匹配时返回。
    异常：
        ValueError：身份、引用或可执行覆盖字段漂移。
    """

    expected = {
        "task_id": spec.task_id,
        "task_uid": spec.task_uid,
        "task_source": spec.task_source,
        "task_type": spec.task_type,
        "task_tag": spec.task_tag,
        "evaluator_path": spec.evaluator_path,
    }
    has_prepare_reference = "prepare_script_path" in task
    prepare_reference = task.get("prepare_script_path")
    has_manifest_reference = "asset_manifest" in task
    manifest_reference = task.get("asset_manifest")
    if spec.canonical_asset_mode == "legacy_prepare_reference":
        asset_declaration_matches = bool(
            has_prepare_reference
            and not has_manifest_reference
            and isinstance(prepare_reference, str)
            and prepare_reference
            and isinstance(spec.canonical_prepare_reference_sha256, str)
            and hashlib.sha256(prepare_reference.encode("utf-8", "strict")).hexdigest()
            == spec.canonical_prepare_reference_sha256
        )
    elif spec.canonical_asset_mode == "strict_asset_manifest":
        asset_declaration_matches = bool(
            not has_prepare_reference
            and has_manifest_reference
            and isinstance(manifest_reference, str)
            and manifest_reference
            and manifest_reference == spec.canonical_asset_manifest_relative_path
        )
    else:
        asset_declaration_matches = False
    if (
        _FORBIDDEN_TASK_FIELDS.intersection(task)
        or any(task.get(key) != value for key, value in expected.items())
        or not asset_declaration_matches
    ):
        raise ValueError("canonical identity mismatch")


def _read_repository_file(
    repo_root: Path,
    relative_path: PurePosixPath,
) -> bytes:
    """读取仓库内无符号链接的固定普通文件。

    输入参数：
        repo_root：仓库根目录。
        relative_path：catalog 或 canonical 给出的 POSIX 相对路径。
    输出返回值：
        文件原始 bytes，用于摘要绑定后解析。
    异常：
        OSError/ValueError：路径逃逸、符号链接或目标不是普通文件。
    """

    if relative_path.is_absolute() or any(
        part in {"", ".", ".."} for part in relative_path.parts
    ):
        raise ValueError("repository relative path invalid")
    root = repo_root.expanduser().resolve()
    candidate = root
    for part in relative_path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("repository file symlink rejected")
    resolved = candidate.resolve()
    resolved.relative_to(root)
    if not resolved.is_file():
        raise ValueError("repository file missing")
    return resolved.read_bytes()


def _validate_input_draft(
    raw_draft: Any,
    spec: ArtifactFamilyTaskPrepareSpec,
) -> tuple[dict[str, Any], ...]:
    """验证 input draft 身份及与 catalog 资产绑定的逐项闭集。

    输入参数：
        raw_draft：从已绑定 bytes 解析的 JSON 值。
        spec：冻结 task-prepare 规格。
    输出返回值：
        保持 draft 顺序的 entry tuple。
    异常：
        ValueError：角色、来源身份、路径或绑定闭集不一致。
    """

    if not isinstance(raw_draft, dict):
        raise ValueError("input draft invalid")
    if (
        raw_draft.get("schema_version") != 1
        or raw_draft.get("manifest_role") != "input"
        or raw_draft.get("distribution_policy") != "download_only"
        or raw_draft.get("task_id") != spec.task_id
        or raw_draft.get("task_uid") != spec.task_uid
        or raw_draft.get("source_task_id") != spec.source_task_id
        or raw_draft.get("source_evaluator_id") != spec.source_evaluator_id
        or raw_draft.get("source_contract_sha256") != spec.source_contract_sha256
    ):
        raise ValueError("input draft identity mismatch")
    entries_raw = raw_draft.get("entries")
    if not isinstance(entries_raw, list) or len(entries_raw) != len(
        spec.asset_bindings
    ):
        raise ValueError("input draft entries mismatch")
    entries: list[dict[str, Any]] = []
    observed_bindings: set[tuple[str, str, str]] = set()
    for entry in entries_raw:
        if not isinstance(entry, dict):
            raise ValueError("input draft entry invalid")
        remote_path = entry.get("remote_relative_path")
        guest_path = entry.get("guest_relative_path")
        purpose = entry.get("purpose")
        path_status = entry.get("path_status")
        if (
            not isinstance(remote_path, str)
            or not isinstance(guest_path, str)
            or not isinstance(purpose, str)
            or path_status not in {"verified", "inferred"}
        ):
            raise ValueError("input draft entry fields invalid")
        _validate_relative_path(remote_path)
        _validate_relative_path(guest_path)
        observed_bindings.add((PurePosixPath(remote_path).name, guest_path, purpose))
        entries.append(entry)
    expected_bindings = {
        (
            binding.asset_relative_path,
            binding.guest_relative_path,
            binding.purpose,
        )
        for binding in spec.asset_bindings
    }
    if len(observed_bindings) != len(entries) or observed_bindings != (
        expected_bindings
    ):
        raise ValueError("input draft binding mismatch")
    return tuple(entries)


def _entry_integrity_is_verified(entry: Mapping[str, Any]) -> bool:
    """判断单个 draft entry 是否拥有完整可信的大小和 SHA 证据。

    输入参数：
        entry：已通过基础 shape 校验的 input entry。
    输出返回值：
        status、非负 size、SHA-256 和 evidence_ref 均有效时为 ``True``。
    """

    integrity = entry.get("integrity")
    return bool(
        isinstance(integrity, dict)
        and integrity.get("status") == "verified"
        and isinstance(integrity.get("size_bytes"), int)
        and not isinstance(integrity.get("size_bytes"), bool)
        and integrity["size_bytes"] >= 0
        and isinstance(integrity.get("sha256"), str)
        and _SHA256_PATTERN.fullmatch(integrity["sha256"]) is not None
        and isinstance(integrity.get("evidence_ref"), str)
        and bool(integrity["evidence_ref"])
    )


def _draft_license_is_verified(raw_draft: Mapping[str, Any]) -> bool:
    """判断 draft 来源许可是否具有可审计的 verified 证明。

    输入参数：
        raw_draft：已完成身份校验的 input draft。
    输出返回值：
        verified、非空 SPDX/evidence 且 download_only 时为 ``True``。
    """

    license_value = raw_draft.get("license")
    return bool(
        isinstance(license_value, dict)
        and license_value.get("status") == "verified"
        and isinstance(license_value.get("spdx_expression"), str)
        and bool(license_value["spdx_expression"])
        and isinstance(license_value.get("evidence_ref"), str)
        and bool(license_value["evidence_ref"])
        and license_value.get("distribution") == "download_only"
    )


def _bind_strict_asset_manifest(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
    spec: ArtifactFamilyTaskPrepareSpec,
    raw_draft: Mapping[str, Any],
) -> ArtifactFamilyTaskPrepareBinding | None:
    """把未来严格 asset manifest 与 verified draft 精确绑定。

    输入参数：
        repo_root：发布仓库根。
        task：canonical task；必须采用统一 asset_manifest 模式。
        spec：冻结 task-prepare 规格。
        raw_draft：已完成 identity/闭集校验的 input draft。
    输出返回值：
        manifest、来源、逐文件摘要全部一致时返回运行时绑定，否则
        ``None``。
    """

    try:
        task_assets = resolve_task_assets(repo_root, task)
    except (AssetManifestError, TypeError):
        return None
    if (
        task_assets.mode is not TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
        or task_assets.manifest is None
    ):
        return None
    manifest = task_assets.manifest
    if not _manifest_matches_verified_draft(manifest, spec, raw_draft):
        return None
    manifest_reference = task.get("asset_manifest")
    if not isinstance(manifest_reference, str) or not manifest_reference:
        return None
    manifest_bytes = _read_repository_file(
        repo_root,
        PurePosixPath(manifest_reference),
    )
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        spec.canonical_asset_mode == "strict_asset_manifest"
        and manifest_sha256 != spec.canonical_asset_manifest_sha256
    ):
        return None
    return ArtifactFamilyTaskPrepareBinding(
        task_id=spec.task_id,
        input_draft_sha256=spec.input_draft_sha256,
        asset_manifest_sha256=manifest_sha256,
        relative_paths=tuple(
            binding.asset_relative_path for binding in spec.asset_bindings
        ),
    )


def _manifest_matches_verified_draft(
    manifest: AssetManifest,
    spec: ArtifactFamilyTaskPrepareSpec,
    raw_draft: Mapping[str, Any],
) -> bool:
    """比较严格下载 manifest、verified draft 与 prepare catalog。

    输入参数：
        manifest：统一资产解析器返回的强类型 manifest。
        spec：冻结 prepare 规格。
        raw_draft：已验证身份的 input draft。
    输出返回值：
        来源、许可、路径、大小和摘要逐项完全一致时返回
        ``True``。
    """

    entries = raw_draft.get("entries")
    source = raw_draft.get("source")
    if not isinstance(entries, list) or not isinstance(source, dict):
        return False
    if (
        not all(
            isinstance(entry, dict) and _entry_integrity_is_verified(entry)
            for entry in entries
        )
        or not _draft_license_is_verified(raw_draft)
        or manifest.asset_set_id != spec.task_id
        or manifest.source.provider != source.get("provider")
        or manifest.source.repository != source.get("repository")
        or manifest.source.revision != source.get("revision")
        or manifest.source.license_status.lower()
        != str(raw_draft["license"]["spdx_expression"]).lower()
    ):
        return False
    manifest_files = {asset.path: asset for asset in manifest.files}
    expected_paths = tuple(
        binding.asset_relative_path for binding in spec.asset_bindings
    )
    if set(manifest_files) != set(expected_paths):
        return False
    draft_by_basename = {
        PurePosixPath(str(entry["remote_relative_path"])).name: entry
        for entry in entries
    }
    if set(draft_by_basename) != set(expected_paths):
        return False
    for relative_path in expected_paths:
        asset = manifest_files[relative_path]
        entry = draft_by_basename[relative_path]
        integrity = entry["integrity"]
        remote_path = str(entry["remote_relative_path"])
        if (
            str(PurePosixPath(manifest.source.base_path) / relative_path) != remote_path
            or asset.size != integrity["size_bytes"]
            or asset.sha256 != integrity["sha256"]
        ):
            return False
    return True


def _validate_relative_path(value: str) -> None:
    """验证一个不可逃逸、无控制符的规范 POSIX 相对路径。

    输入参数：
        value：待校验路径字符串。
    输出返回值：
        无；安全时返回。
    异常：
        ValueError：绝对路径、点段、反斜杠或控制符存在。
    """

    if not isinstance(value, str):
        raise ValueError("relative path invalid")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or str(path) != value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("relative path invalid")


__all__ = [
    "ARTIFACT_FAMILY_BLOCKER_INPUT_INTEGRITY_UNVERIFIED",
    "ARTIFACT_FAMILY_BLOCKER_INPUT_LICENSE_UNVERIFIED",
    "ARTIFACT_FAMILY_BLOCKER_INPUT_PATH_INFERRED",
    "ARTIFACT_FAMILY_BLOCKER_SOURCE_CONTEXT_AMBIGUOUS",
    "ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_INVALID",
    "ARTIFACT_FAMILY_BLOCKER_STRICT_ASSET_MANIFEST_MISSING",
    "ArtifactFamilyTaskPrepareBinding",
    "ArtifactFamilyTaskPrepareCapability",
    "ArtifactFamilyTaskPrepareCapabilityError",
    "inspect_artifact_family_task_prepare_capability",
    "preflight_artifact_family_task_prepare",
    "validate_artifact_family_task_prepare_runtime_binding",
]
