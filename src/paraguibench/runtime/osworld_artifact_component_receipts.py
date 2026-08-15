"""OSWorld artifact component receipt 的独立物理闭集门禁。"""

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

from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifestError,
    load_osworld_image_manifest_bytes_with_sha256,
)
from paraguibench.runstore import RunVersionVector
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runtime.assets import load_asset_manifest_bytes
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
    OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL,
    osworld_artifact_environment_protocol,
)
from paraguibench.runtime.osworld_artifact_component_validation import (
    OSWorldArtifactComponentValidationResult,
)
from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    GoldAssetManifest,
    load_gold_asset_manifest_bytes,
)
from paraguibench.runtime.osworld_gold import (
    TaskGoldMode,
    bind_osworld_task_gold,
)


OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ROOT = Path(
    "benchmark/provenance/osworld-artifact-component-receipts"
)
OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ALLOWLIST_PATH = Path(
    "benchmark/provenance/osworld-artifact-component-receipt-allowlist-v1.json"
)
OSWORLD_ARTIFACT_COMPONENT_RECEIPT_KIND = "paraguibench.osworld.artifact-component.v1"
OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_KIND = (
    "paraguibench.osworld.artifact-component-attempt.v1"
)
OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_RELATIVE_PATH = Path(
    "osworld-artifact-component-attempt-v1.json"
)
_RELEASE_MANIFEST_PATH = Path("benchmark/manifests/release-v1.json")
_OSWORLD_MANIFEST_PATH = Path("environments/osworld/image-manifest.json")
_RUNTIME_SUPPORT_GUARD_PATH = Path("scripts/benchmark/runtime_support_manifest.py")
_PYPROJECT_PATH = Path("pyproject.toml")
_TASK_IDENTITY_DOMAIN = b"paraguibench-osworld-artifact-task-v1\0"
_ENVIRONMENT_IDENTITY_DOMAIN = b"paraguibench-osworld-artifact-environment-v1\0"
_SETUP_COMPONENT_IDENTITY_DOMAIN = b"paraguibench-osworld-artifact-setup-component-v1\0"
_GETTER_COMPONENT_IDENTITY_DOMAIN = (
    b"paraguibench-osworld-artifact-getter-component-v1\0"
)
_GOLD_COMPONENT_IDENTITY_DOMAIN = b"paraguibench-osworld-artifact-gold-component-v1\0"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAX_IDENTITY_FILE_BYTES = 16 * 1024 * 1024
_MAX_ALLOWLIST_BYTES = 64 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024
_MAX_ATTEMPT_ATTESTATION_BYTES = 64 * 1024
_ALLOWLIST_FIELDS = frozenset({"schema_version", "receipts"})
_ALLOWLIST_ENTRY_FIELDS = frozenset(
    {
        "receipt_sha256",
        "task_identity_sha256",
        "environment_identity_sha256",
        "setup_component_sha256",
        "getter_component_sha256",
        "gold_component_sha256",
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
        "candidate_evaluation_protocol",
        "task_evaluation_protocol",
        "environment_protocol",
        "attempt_version_vector_sha256",
        "task_identity_sha256",
        "environment_identity_sha256",
        "setup_component_sha256",
        "getter_component_sha256",
        "gold_component_sha256",
        "component_checks",
    }
)
_COMPONENT_CHECK_FIELDS = frozenset({"task_setup", "artifact_getter", "evaluator_gold"})
_SETTINGS001_TASK_ID = "Operation-FileOperate-Settings-001"
_OSWORLD_ARTIFACT_COMPONENT_IDENTITY_TASK_IDS = OSWORLD_ARTIFACT_COMPONENT_TASK_IDS | {
    _SETTINGS001_TASK_ID
}


@dataclass(frozen=True, slots=True)
class OSWorldArtifactComponentIdentity:
    """保存 task、环境与 setup/getter/gold 三类组件的独立摘要。"""

    task_identity_sha256: str
    environment_identity_sha256: str
    setup_component_sha256: str
    getter_component_sha256: str
    gold_component_sha256: str


@dataclass(frozen=True, slots=True)
class OSWorldArtifactComponentReceipt:
    """保存由 allowlist-only Attempt inspection 导出的脱敏组件证明。"""

    schema_version: int
    receipt_kind: str
    task_id: str
    run_id: str
    attempt_id: str
    execution_outcome: str
    evaluation_outcome: str
    score: float
    candidate_evaluation_protocol: str
    task_evaluation_protocol: str
    environment_protocol: str
    attempt_version_vector_sha256: str
    task_identity_sha256: str
    environment_identity_sha256: str
    setup_component_sha256: str
    getter_component_sha256: str
    gold_component_sha256: str

    def __post_init__(self) -> None:
        """验证 receipt 只能表达完整的专属 candidate 成功事实。

        输入参数：无；读取冻结字段。
        输出返回值：字段闭集、协议、终态与五层摘要全部有效时
            正常返回。
        异常：OSWorldArtifactComponentReceiptError：任一类型、身份、
            协议、得分或 SHA-256 不符合严格合同。
        """

        try:
            safe_run_id = validate_identifier("run_id", self.run_id)
            safe_task_id = validate_identifier("task_id", self.task_id)
            safe_attempt_id = validate_identifier("attempt_id", self.attempt_id)
        except (TypeError, ValueError):
            raise OSWorldArtifactComponentReceiptError from None
        digests = (
            self.attempt_version_vector_sha256,
            self.task_identity_sha256,
            self.environment_identity_sha256,
            self.setup_component_sha256,
            self.getter_component_sha256,
            self.gold_component_sha256,
        )
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.receipt_kind != OSWORLD_ARTIFACT_COMPONENT_RECEIPT_KIND
            or safe_run_id != self.run_id
            or safe_task_id != self.task_id
            or safe_attempt_id != self.attempt_id
            or self.task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
            or self.execution_outcome != "SUCCEEDED"
            or self.evaluation_outcome != "PASSED"
            or not isinstance(self.score, float)
            or not math.isfinite(self.score)
            or self.score != 1.0
            or self.candidate_evaluation_protocol
            != OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL
            or self.task_evaluation_protocol
            != OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL
            or self.environment_protocol
            != osworld_artifact_environment_protocol(self.task_id)
            or any(
                not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None
                for value in digests
            )
        ):
            raise OSWorldArtifactComponentReceiptError

    def to_dict(self) -> dict[str, Any]:
        """投影为严格字段闭合且可安全序列化的 receipt object。

        输入参数：无；读取冻结实例字段。
        输出返回值：只含终态、版本身份、五层组件身份和三个固定
            ``passed`` 检查的 JSON-compatible 字典；不含 Attempt details、
            events、Agent final text、路径、正文、gold 或 secret。
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
            "candidate_evaluation_protocol": self.candidate_evaluation_protocol,
            "task_evaluation_protocol": self.task_evaluation_protocol,
            "environment_protocol": self.environment_protocol,
            "attempt_version_vector_sha256": (self.attempt_version_vector_sha256),
            "task_identity_sha256": self.task_identity_sha256,
            "environment_identity_sha256": self.environment_identity_sha256,
            "setup_component_sha256": self.setup_component_sha256,
            "getter_component_sha256": self.getter_component_sha256,
            "gold_component_sha256": self.gold_component_sha256,
            "component_checks": {
                "task_setup": "passed",
                "artifact_getter": "passed",
                "evaluator_gold": "passed",
            },
        }


class OSWorldArtifactComponentReceiptError(RuntimeError):
    """表示 artifact component receipt 数据或物理闭集无效。"""

    code = "OSWORLD_ARTIFACT_COMPONENT_RECEIPT_INVALID"

    def __init__(self) -> None:
        """构造不回显 receipt、路径、身份或外部值的固定错误。

        输入参数：无。
        输出返回值：无；异常文本仅包含稳定错误码。
        """

        super().__init__(self.code)


def export_osworld_artifact_component_receipt(
    *,
    repo_root: Path,
    runs_root: Path,
    task_id: str,
    run_id: str,
    attempt_id: str,
) -> OSWorldArtifactComponentReceipt:
    """永久拒绝从既有普通 Attempt 回推 component receipt。

    输入参数：保留 repo_root/runs_root/task_id/run_id/attempt_id 仅用于兼容
        早期候选接口；函数不读取这些值或任何 RunStore 文件。
    输出返回值：不返回；普通 Attempt 的终态与用户可写 artifact 均不能
        证明 setup/getter/gold 在同一 owned 环境实际运行。
    异常：始终抛出固定 ``OSWorldArtifactComponentReceiptError``。正式
        receipt 只能由专属同进程 live candidate 在成功关闭环境后构造。
    """

    del repo_root, runs_root, task_id, run_id, attempt_id
    raise OSWorldArtifactComponentReceiptError


def build_osworld_artifact_component_receipt(
    *,
    repo_root: Path,
    validation: OSWorldArtifactComponentValidationResult,
) -> OSWorldArtifactComponentReceipt:
    """永久拒绝调用方把可构造 result 投影为 receipt。

    输入参数：保留 repo_root/validation 仅用于兼容早期接口；
        函数不读取仓库、result、RunStore 或 artifact。
    输出返回值：不返回；正式 receipt 只能由 top-level candidate
        在同一 resolved repo、owned 环境与 close 生命周期内部构造。
    异常：始终抛出固定 ``OSWorldArtifactComponentReceiptError``。
    """

    del repo_root, validation
    raise OSWorldArtifactComponentReceiptError


def _run_version_vector_sha256(vector: RunVersionVector) -> str:
    """将历史 RunVersionVector 投影为单向 provenance 摘要。

    输入参数：vector 为已由 validation result 验证的完整向量。
    输出返回值：六字段 canonical JSON 的 64 位 SHA-256。该摘要
        仅用于记录历史 Attempt，不参与 current receipt 晋升比对。
    异常：OSWorldArtifactComponentReceiptError：输入非精确版本向量。
    """

    if not isinstance(vector, RunVersionVector):
        raise OSWorldArtifactComponentReceiptError
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
        raise OSWorldArtifactComponentReceiptError from None
    return hashlib.sha256(encoded).hexdigest()


def derive_osworld_artifact_component_identity(
    repo_root: Path,
    task_id: str,
) -> OSWorldArtifactComponentIdentity:
    """从 receipt-neutral 仓库事实源派生 task-scoped 组件身份。

    输入参数：repo_root 为 canonical release、task、input/gold manifest、
        OSWorld 环境、公开 Python、schema 与 promotion guard 所在仓库；
        task_id 必须属于 13 项 identity-only 闭集；其中
        Settings-001 仅可派生 current identity，不因此获得
        candidate、receipt、schema、allowlist 或 promotable 资格。
    输出返回值：task、环境、setup、getter 与 gold 五份 64 位 SHA-256；
        活性输出、runtime-support JSON、receipt、allowlist 和网站均不进入
        摘要，避免 receipt 晋升造成身份自引用。
    异常：OSWorldArtifactComponentReceiptError：任务不受支持，或任一
        路径、JSON、摘要、资产绑定、环境协议、文件闭集不可信。
    """

    if (
        not isinstance(repo_root, Path)
        or not isinstance(task_id, str)
        or task_id not in _OSWORLD_ARTIFACT_COMPONENT_IDENTITY_TASK_IDS
    ):
        raise OSWorldArtifactComponentReceiptError
    try:
        root_status = repo_root.lstat()
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise OSError
        root = repo_root.resolve(strict=True)
        task_identity = _derive_task_identity(root, task_id)
        environment_identity = _derive_environment_identity(root)
        component_paths = _collect_component_paths(root)
        setup_identity = _derive_component_code_identity(
            root,
            component_paths=component_paths,
            domain=_SETUP_COMPONENT_IDENTITY_DOMAIN,
            bound_identity=task_identity,
        )
        getter_identity = _derive_component_code_identity(
            root,
            component_paths=component_paths,
            domain=_GETTER_COMPONENT_IDENTITY_DOMAIN,
        )
        gold_identity = _derive_component_code_identity(
            root,
            component_paths=component_paths,
            domain=_GOLD_COMPONENT_IDENTITY_DOMAIN,
        )
        if _collect_component_paths(root) != component_paths:
            raise OSWorldArtifactComponentReceiptError
    except OSWorldArtifactComponentReceiptError:
        raise
    except Exception:
        raise OSWorldArtifactComponentReceiptError from None
    return OSWorldArtifactComponentIdentity(
        task_identity_sha256=task_identity,
        environment_identity_sha256=environment_identity,
        setup_component_sha256=setup_identity,
        getter_component_sha256=getter_identity,
        gold_component_sha256=gold_identity,
    )


def _derive_task_identity(repo_root: Path, task_id: str) -> str:
    """绑定完整 release、目标 task 与 input/gold manifest 原始字节。

    输入参数：repo_root 为已解析仓库根；task_id 为 13-task identity-only 闭集成员。
    输出返回值：路径、顺序与逐文件摘要共同形成的 domain-separated SHA-256。
    异常：OSWorldArtifactComponentReceiptError：release、task、摘要、路径、
        strict input/gold 声明或 manifest 身份无效。
    """

    release_payload = _read_repository_file(
        repo_root,
        _RELEASE_MANIFEST_PATH,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    release = _decode_json_object(release_payload)
    entries = release.get("tasks")
    if (
        type(release.get("schema_version")) is not int
        or release.get("schema_version") != 1
        or release.get("release_id") != "release-v1"
        or not isinstance(entries, list)
        or not entries
    ):
        raise OSWorldArtifactComponentReceiptError
    matches: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise OSWorldArtifactComponentReceiptError
        entry_task_id = entry.get("task_id")
        if (
            not isinstance(entry_task_id, str)
            or not entry_task_id
            or entry_task_id in seen_task_ids
        ):
            raise OSWorldArtifactComponentReceiptError
        seen_task_ids.add(entry_task_id)
        if entry_task_id == task_id:
            matches.append(entry)
    if len(matches) != 1:
        raise OSWorldArtifactComponentReceiptError
    entry = matches[0]
    task_path = _safe_relative_path(entry.get("path"))
    expected_task_sha256 = entry.get("sha256")
    if (
        not isinstance(expected_task_sha256, str)
        or _SHA256_PATTERN.fullmatch(expected_task_sha256) is None
    ):
        raise OSWorldArtifactComponentReceiptError
    task_payload = _read_repository_file(
        repo_root,
        task_path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    if hashlib.sha256(task_payload).hexdigest() != expected_task_sha256:
        raise OSWorldArtifactComponentReceiptError
    task = _decode_json_object(task_payload)
    if task.get("task_id") != task_id or task.get("prepare_script_path") not in (
        None,
        "",
    ):
        raise OSWorldArtifactComponentReceiptError
    asset_path = _safe_relative_path(task.get("asset_manifest"))
    gold_path = _safe_relative_path(task.get("gold_manifest"))
    asset_payload = _read_repository_file(
        repo_root,
        asset_path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    gold_payload = _read_repository_file(
        repo_root,
        gold_path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    gold_manifest = _decode_json_object(gold_payload)
    try:
        loaded_asset_manifest = load_asset_manifest_bytes(asset_payload)
        loaded_gold_manifest = load_gold_asset_manifest_bytes(gold_payload)
        bound_gold = bind_osworld_task_gold(
            task_id,
            loaded_gold_manifest,
            task_uid=(
                task.get("task_uid") if isinstance(task.get("task_uid"), str) else None
            ),
            evaluator_path=(
                task.get("evaluator_path")
                if isinstance(task.get("evaluator_path"), str)
                else None
            ),
            asset_manifest_reference=(
                task.get("asset_manifest")
                if task_id == _SETTINGS001_TASK_ID
                and isinstance(task.get("asset_manifest"), str)
                else None
            ),
        )
    except Exception:
        raise OSWorldArtifactComponentReceiptError from None
    expected_gold_type_and_mode = (
        task_id == _SETTINGS001_TASK_ID
        and type(loaded_gold_manifest) is DerivedGoldAssetManifest
        and bound_gold.mode is TaskGoldMode.PRIVATE_DERIVED_MANIFEST
    ) or (
        task_id != _SETTINGS001_TASK_ID
        and type(loaded_gold_manifest) is GoldAssetManifest
        and bound_gold.mode is TaskGoldMode.PINNED_DOWNLOAD_MANIFEST
    )
    if loaded_asset_manifest.asset_set_id != task_id or not loaded_asset_manifest.files:
        raise OSWorldArtifactComponentReceiptError
    if task_id == _SETTINGS001_TASK_ID:
        if type(loaded_gold_manifest) is not DerivedGoldAssetManifest:
            raise OSWorldArtifactComponentReceiptError
        matching_source_inputs = tuple(
            entry
            for entry in loaded_asset_manifest.files
            if entry.path == loaded_gold_manifest.source_input.path
        )
        if (
            hashlib.sha256(asset_payload).hexdigest()
            != loaded_gold_manifest.asset_manifest_sha256
            or loaded_gold_manifest.asset_set_id != loaded_asset_manifest.asset_set_id
            or len(matching_source_inputs) != 1
            or matching_source_inputs[0].size != loaded_gold_manifest.source_input.size
            or matching_source_inputs[0].sha256
            != loaded_gold_manifest.source_input.sha256
            or matching_source_inputs[0].media_type
            != loaded_gold_manifest.source_input.media_type
        ):
            raise OSWorldArtifactComponentReceiptError
    prepare_spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS.get(task_id)
    if prepare_spec is None:
        raise OSWorldArtifactComponentReceiptError
    input_draft_path = _safe_relative_path(prepare_spec.input_draft_relative_path)
    input_draft_payload = _read_repository_file(
        repo_root,
        input_draft_path,
        maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
    )
    if (
        hashlib.sha256(input_draft_payload).hexdigest()
        != prepare_spec.input_draft_sha256
    ):
        raise OSWorldArtifactComponentReceiptError
    input_draft = _decode_json_object(input_draft_payload)
    if (
        not expected_gold_type_and_mode
        or type(gold_manifest.get("schema_version")) is not int
        or gold_manifest.get("schema_version") not in {1, 2}
        or not isinstance(gold_manifest.get("manifest_id"), str)
        or not gold_manifest["manifest_id"].startswith(f"{task_id}-gold-")
        or not isinstance(gold_manifest.get("entries"), list)
        or not gold_manifest["entries"]
        or type(input_draft.get("schema_version")) is not int
        or input_draft.get("schema_version") != 1
        or input_draft.get("task_id") != task_id
        or not isinstance(input_draft.get("entries"), list)
        or not input_draft["entries"]
    ):
        raise OSWorldArtifactComponentReceiptError
    digest = hashlib.sha256(_TASK_IDENTITY_DOMAIN)
    for relative_path, payload in (
        (_RELEASE_MANIFEST_PATH, release_payload),
        (task_path, task_payload),
        (asset_path, asset_payload),
        (gold_path, gold_payload),
        (input_draft_path, input_draft_payload),
    ):
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _derive_environment_identity(repo_root: Path) -> str:
    """绑定当前 OSWorld manifest 原始字节与 desktop 协议身份。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：包含固定相对路径与 manifest 摘要的 SHA-256。
    异常：OSWorldArtifactComponentReceiptError：schema、环境 ID 或
        ``osworld.desktop.v1`` 协议声明无效。
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
        raise OSWorldArtifactComponentReceiptError from None
    if (
        manifest.environment_id != "osworld-ubuntu-x86_64"
        or "osworld.desktop.v1" not in manifest.protocol_ids
        or manifest.manifest_sha256 != manifest_sha256
    ):
        raise OSWorldArtifactComponentReceiptError
    digest = hashlib.sha256(_ENVIRONMENT_IDENTITY_DOMAIN)
    digest.update(_OSWORLD_MANIFEST_PATH.as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(bytes.fromhex(manifest_sha256))
    return digest.hexdigest()


def _derive_component_code_identity(
    repo_root: Path,
    *,
    component_paths: tuple[Path, ...],
    domain: bytes,
    bound_identity: str | None = None,
) -> str:
    """按给定 domain 摘要 receipt-neutral 生产组件闭集。

    输入参数：repo_root 为已解析仓库根；component_paths 为稳定排序的
        Python/schema/guard 闭集；domain 区分 setup/getter/gold 版本；
        bound_identity 可把 task-specific setup 直接依赖绑定到版本。
    输出返回值：相对路径和逐文件 SHA 组成的 64 位摘要。
    异常：OSWorldArtifactComponentReceiptError：任一文件在读取时失效。
    """

    if bound_identity is not None and (
        not isinstance(bound_identity, str)
        or _SHA256_PATTERN.fullmatch(bound_identity) is None
    ):
        raise OSWorldArtifactComponentReceiptError
    digest = hashlib.sha256(domain)
    if bound_identity is not None:
        digest.update(bytes.fromhex(bound_identity))
    for relative_path in component_paths:
        payload = _read_repository_file(
            repo_root,
            relative_path,
            maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
        )
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _collect_component_paths(repo_root: Path) -> tuple[Path, ...]:
    """枚举不含活性输出与 receipt 的生产代码/schema 闭集。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：公开 Python、benchmark schema、promotion guard 与
        ``pyproject.toml`` 的稳定排序去重元组。
    异常：OSWorldArtifactComponentReceiptError：目录缺失、symlink、
        特殊节点、空闭集或固定文件缺失。
    """

    paths = [
        _PYPROJECT_PATH,
        _RUNTIME_SUPPORT_GUARD_PATH,
    ]
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
    result = tuple(sorted(set(paths), key=lambda item: item.as_posix()))
    if not result:
        raise OSWorldArtifactComponentReceiptError
    for relative_path in result:
        _read_repository_file(
            repo_root,
            relative_path,
            maximum_bytes=_MAX_IDENTITY_FILE_BYTES,
        )
    return result


def _collect_regular_tree_files(
    repo_root: Path,
    relative_root: Path,
    *,
    suffix: str,
) -> list[Path]:
    """nofollow 枚举目录树内指定后缀的普通文件。

    输入参数：repo_root/relative_root 确定树根；suffix 为目标后缀。
    输出返回值：按 POSIX 路径排序的仓库相对文件列表。
    异常：OSWorldArtifactComponentReceiptError：树或任一节点不是预期
        普通目录/文件，或出现 symlink。
    """

    tree_root = repo_root / relative_root
    try:
        root_status = tree_root.lstat()
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise OSError
        paths: list[Path] = []
        for current_raw, directory_names, file_names in os.walk(
            tree_root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_raw)
            for name in directory_names:
                status = (current / name).lstat()
                if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
                    raise OSError
            for name in file_names:
                candidate = current / name
                status = candidate.lstat()
                if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
                    raise OSError
                if candidate.suffix == suffix:
                    paths.append(candidate.relative_to(repo_root))
    except OSError:
        raise OSWorldArtifactComponentReceiptError from None
    if not paths:
        raise OSWorldArtifactComponentReceiptError
    return sorted(paths, key=lambda item: item.as_posix())


def _safe_relative_path(value: object) -> Path:
    """把仓库 JSON 路径字段收紧为规范安全相对路径。

    输入参数：value 为待验证的 POSIX 路径字符串。
    输出返回值：不含空段、反斜杠、绝对语义或点段的 ``Path``。
    异常：OSWorldArtifactComponentReceiptError：路径类型或形式无效。
    """

    if not isinstance(value, str) or not value or "\\" in value:
        raise OSWorldArtifactComponentReceiptError
    relative = Path(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value
    ):
        raise OSWorldArtifactComponentReceiptError
    return relative


def load_trusted_osworld_artifact_component_receipts(
    repo_root: Path,
) -> frozenset[str]:
    """加载并验证 task-scoped artifact component receipt 闭集。

    输入参数：repo_root 为包含 canonical allowlist 与可选 receipt 目录的
        仓库根；路径必须是非符号链接目录。
    输出返回值：空 allowlist 返回空 ``frozenset``；后续非空分支只返回
        已通过 receipt SHA、身份与物理闭集复核的 canonical task ID。
    异常：OSWorldArtifactComponentReceiptError：路径、JSON、字段闭集、
        receipt 目录或读取稳定性无效。
    """

    if not isinstance(repo_root, Path):
        raise OSWorldArtifactComponentReceiptError
    try:
        root_status = repo_root.lstat()
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise OSError
        root = repo_root.resolve(strict=True)
        allowlist_payload = _read_repository_file(
            root,
            OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ALLOWLIST_PATH,
            maximum_bytes=_MAX_ALLOWLIST_BYTES,
        )
        allowlist = _decode_json_object(allowlist_payload)
        schema_version = allowlist.get("schema_version")
        if (
            set(allowlist) != _ALLOWLIST_FIELDS
            or type(schema_version) is not int
            or schema_version != 1
            or not isinstance(allowlist.get("receipts"), dict)
        ):
            raise OSWorldArtifactComponentReceiptError
        entries = allowlist["receipts"]
        task_ids = frozenset(entries)
        if not task_ids <= OSWORLD_ARTIFACT_COMPONENT_TASK_IDS:
            raise OSWorldArtifactComponentReceiptError
        expected_names = frozenset(f"{task_id}.json" for task_id in task_ids)
        directory_identity = _validate_receipt_directory_closure(
            root,
            expected_names=expected_names,
        )
        if not entries:
            if (
                _read_repository_file(
                    root,
                    OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ALLOWLIST_PATH,
                    maximum_bytes=_MAX_ALLOWLIST_BYTES,
                )
                != allowlist_payload
                or _validate_receipt_directory_closure(
                    root,
                    expected_names=frozenset(),
                )
                != directory_identity
            ):
                raise OSWorldArtifactComponentReceiptError
            return frozenset()

        trusted: set[str] = set()
        for task_id in sorted(task_ids):
            entry = entries.get(task_id)
            if (
                not isinstance(entry, dict)
                or set(entry) != _ALLOWLIST_ENTRY_FIELDS
                or any(
                    not isinstance(entry.get(field), str)
                    or _SHA256_PATTERN.fullmatch(entry[field]) is None
                    for field in _ALLOWLIST_ENTRY_FIELDS
                )
            ):
                raise OSWorldArtifactComponentReceiptError
            receipt_relative = (
                OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ROOT / f"{task_id}.json"
            )
            receipt_payload = _read_repository_file(
                root,
                receipt_relative,
                maximum_bytes=_MAX_RECEIPT_BYTES,
            )
            if hashlib.sha256(receipt_payload).hexdigest() != entry["receipt_sha256"]:
                raise OSWorldArtifactComponentReceiptError
            identity_before = derive_osworld_artifact_component_identity(
                root,
                task_id,
            )
            _validate_allowlist_identity(entry, identity_before)
            _validate_receipt_payload(
                _decode_json_object(receipt_payload),
                expected_task_id=task_id,
                expected_identity=identity_before,
            )
            identity_after = derive_osworld_artifact_component_identity(
                root,
                task_id,
            )
            if (
                identity_after != identity_before
                or _read_repository_file(
                    root,
                    receipt_relative,
                    maximum_bytes=_MAX_RECEIPT_BYTES,
                )
                != receipt_payload
            ):
                raise OSWorldArtifactComponentReceiptError
            trusted.add(task_id)
        if (
            _read_repository_file(
                root,
                OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ALLOWLIST_PATH,
                maximum_bytes=_MAX_ALLOWLIST_BYTES,
            )
            != allowlist_payload
            or _validate_receipt_directory_closure(
                root,
                expected_names=expected_names,
            )
            != directory_identity
        ):
            raise OSWorldArtifactComponentReceiptError
        return frozenset(trusted)
    except OSWorldArtifactComponentReceiptError:
        raise
    except Exception:
        raise OSWorldArtifactComponentReceiptError from None


def _validate_allowlist_identity(
    entry: dict[str, Any],
    identity: OSWorldArtifactComponentIdentity,
) -> None:
    """验证 allowlist 外置五层摘要精确等于当前仓库事实。

    输入参数：entry 为已通过字段与 SHA 格式闭集的条目；
        identity 为从同一 resolved repo 刚派生的五层身份。
    输出返回值：全部等值时正常返回。
    异常：OSWorldArtifactComponentReceiptError：任一摘要过期或错绑。
    """

    if (
        entry["task_identity_sha256"] != identity.task_identity_sha256
        or entry["environment_identity_sha256"] != identity.environment_identity_sha256
        or entry["setup_component_sha256"] != identity.setup_component_sha256
        or entry["getter_component_sha256"] != identity.getter_component_sha256
        or entry["gold_component_sha256"] != identity.gold_component_sha256
    ):
        raise OSWorldArtifactComponentReceiptError


def _validate_receipt_payload(
    payload: dict[str, Any],
    *,
    expected_task_id: str,
    expected_identity: OSWorldArtifactComponentIdentity,
) -> OSWorldArtifactComponentReceipt:
    """将不可信 JSON 收紧为当前 task-scoped receipt。

    输入参数：payload 为已拒绝重复 key/非有限常量的对象；
        expected_task_id 由 allowlist key 与文件名共同确定；
        expected_identity 为当前仓库五层身份。
    输出返回值：严格字段、终态、协议、得分、组件检查和
        五层身份均成立的冻结 receipt。
    异常：OSWorldArtifactComponentReceiptError：额外/缺失/敏感字段、
        cross-task、非有限/非满分或 current 身份不等。
    """

    checks = payload.get("component_checks")
    score = payload.get("score")
    if (
        set(payload) != _RECEIPT_FIELDS
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("task_id") != expected_task_id
        or not isinstance(checks, dict)
        or set(checks) != _COMPONENT_CHECK_FIELDS
        or any(checks.get(field) != "passed" for field in _COMPONENT_CHECK_FIELDS)
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(score)
        or score != 1
    ):
        raise OSWorldArtifactComponentReceiptError
    try:
        receipt = OSWorldArtifactComponentReceipt(
            schema_version=payload["schema_version"],
            receipt_kind=payload["receipt_kind"],
            task_id=payload["task_id"],
            run_id=payload["run_id"],
            attempt_id=payload["attempt_id"],
            execution_outcome=payload["execution_outcome"],
            evaluation_outcome=payload["evaluation_outcome"],
            score=float(score),
            candidate_evaluation_protocol=payload["candidate_evaluation_protocol"],
            task_evaluation_protocol=payload["task_evaluation_protocol"],
            environment_protocol=payload["environment_protocol"],
            attempt_version_vector_sha256=payload["attempt_version_vector_sha256"],
            task_identity_sha256=payload["task_identity_sha256"],
            environment_identity_sha256=payload["environment_identity_sha256"],
            setup_component_sha256=payload["setup_component_sha256"],
            getter_component_sha256=payload["getter_component_sha256"],
            gold_component_sha256=payload["gold_component_sha256"],
        )
    except (KeyError, TypeError, ValueError):
        raise OSWorldArtifactComponentReceiptError from None
    if (
        receipt.task_identity_sha256 != expected_identity.task_identity_sha256
        or receipt.environment_identity_sha256
        != expected_identity.environment_identity_sha256
        or receipt.setup_component_sha256 != expected_identity.setup_component_sha256
        or receipt.getter_component_sha256 != expected_identity.getter_component_sha256
        or receipt.gold_component_sha256 != expected_identity.gold_component_sha256
    ):
        raise OSWorldArtifactComponentReceiptError
    return receipt


def _read_repository_file(
    repo_root: Path,
    relative_path: Path,
    *,
    maximum_bytes: int,
) -> bytes:
    """通过 nofollow dirfd 链稳定读取仓库内有界普通文件。

    输入参数：repo_root/relative_path 定位仓库内文件；maximum_bytes 为
        严格正整数读取上限。
    输出返回值：同一 file descriptor 的完整原始字节。
    异常：OSWorldArtifactComponentReceiptError：路径、节点类型、硬链接、
        尺寸、短读或读前后物理身份无效。
    """

    if (
        not isinstance(repo_root, Path)
        or not isinstance(relative_path, Path)
        or relative_path.is_absolute()
        or not relative_path.parts
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or not isinstance(maximum_bytes, int)
        or isinstance(maximum_bytes, bool)
        or maximum_bytes <= 0
    ):
        raise OSWorldArtifactComponentReceiptError
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0 or directory == 0:
        raise OSWorldArtifactComponentReceiptError
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
        raise OSWorldArtifactComponentReceiptError from None
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


def _decode_json_object(payload: bytes) -> dict[str, Any]:
    """解码字段唯一且不含非有限常量的 UTF-8 JSON object。

    输入参数：payload 为稳定读取的 JSON 原始字节。
    输出返回值：顶层为 object 且所有层级字段唯一的普通字典。
    异常：OSWorldArtifactComponentReceiptError：编码、JSON、重复字段、
        NaN/Infinity 或顶层类型无效。
    """

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            parse_constant=lambda _value: (_raise_invalid_json()),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise OSWorldArtifactComponentReceiptError from None
    if not isinstance(value, dict):
        raise OSWorldArtifactComponentReceiptError
    return value


def _raise_invalid_json() -> None:
    """拒绝 JSON decoder 的 NaN/Infinity 非标准常量。

    输入参数：无；候选值故意不进入错误或日志。
    输出返回值：不返回；始终抛出 ``ValueError``。
    """

    raise ValueError("invalid JSON constant")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """从 decoder 字段序列构造拒绝重复 key 的字典。

    输入参数：pairs 为 JSON decoder 提供的有序 key/value 序列。
    输出返回值：字段唯一的新字典。
    异常：ValueError：发现重复 key；异常不回显 key 或值。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_receipt_directory_closure(
    repo_root: Path,
    *,
    expected_names: frozenset[str],
) -> tuple[int, int, int, int, int] | None:
    """通过 nofollow dirfd 验证 receipt 目录成员与物理身份闭集。

    输入参数：repo_root 为已解析仓库根；expected_names 为 allowlist
        机械派生的全部 receipt 文件名。
    输出返回值：目录缺失且预期为空时返回 ``None``；否则返回稳定的
        ``(device, inode, mode, mtime_ns, ctime_ns)`` 目录身份。
    异常：OSWorldArtifactComponentReceiptError：路径链、目录类型、名称闭集、
        成员类型/硬链接或枚举稳定性无效。
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0 or directory == 0:
        raise OSWorldArtifactComponentReceiptError
    descriptors: list[int] = []
    try:
        descriptors.append(
            os.open(repo_root, os.O_RDONLY | directory | nofollow | cloexec)
        )
        parts = OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ROOT.parts
        for part in parts:
            try:
                descriptor = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=descriptors[-1],
                )
            except FileNotFoundError:
                if not expected_names and part == parts[-1]:
                    return None
                raise
            descriptors.append(descriptor)
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
        raise OSWorldArtifactComponentReceiptError from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


__all__ = [
    "OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_KIND",
    "OSWORLD_ARTIFACT_COMPONENT_ATTEMPT_ATTESTATION_RELATIVE_PATH",
    "OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ALLOWLIST_PATH",
    "OSWORLD_ARTIFACT_COMPONENT_RECEIPT_KIND",
    "OSWORLD_ARTIFACT_COMPONENT_RECEIPT_ROOT",
    "OSWORLD_ARTIFACT_COMPONENT_TASK_IDS",
    "OSWorldArtifactComponentIdentity",
    "OSWorldArtifactComponentReceipt",
    "OSWorldArtifactComponentReceiptError",
    "build_osworld_artifact_component_receipt",
    "derive_osworld_artifact_component_identity",
    "export_osworld_artifact_component_receipt",
    "load_trusted_osworld_artifact_component_receipts",
]
