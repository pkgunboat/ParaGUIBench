"""pipeline-implicit 任务的正式运行能力与 fail-closed 门禁。

PPT-003、Excel-008、CombinationDocs-002 与 SearchWrite-008 已绑定正式
input/reference 清单、typed artifact bridge 与纯评价器机器身份。
公开 live-validation marker 只描述实机证据状态。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_PROTOCOL_ID,
    HIDE_NA_ROWS_TASK_ID,
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    PINNED_CLASSIFIED_IMAGE_SHA256,
    PINNED_HIDDEN_ROWS_BY_DOCUMENT,
    PINNED_PRESENTATION_SHA256,
    PINNED_UNCLASSIFIED_IMAGE_SHA256,
    PINNED_XLSX_MONTHLY_REFERENCE,
    SEARCHWRITE_BASELINE_PROJECTION_PROTOCOL_ID,
    SEARCHWRITE_CELL_MATCH_PROTOCOL_ID,
    SEARCHWRITE_DOCUMENT_CONTRACTS,
    SEARCHWRITE_GOLD_MANIFEST_SHA256,
    SEARCHWRITE_INPUT_MANIFEST_SHA256,
    SEARCHWRITE_MACHINE_IDENTITY_SHA256,
    SEARCHWRITE_MACHINE_IDENTITY_VERSION,
    SEARCHWRITE_XLSX_PROTOCOL_ID,
    SEARCHWRITE_XLSX_TASK_ID,
    SEARCHWRITE_XLSX_TASK_UID,
)
from paraguibench.integrations.pipeline_implicit import (
    PIPELINE_IMPLICIT_TASK_PROTOCOLS,
    PINNED_HIDE_NA_ROWS_BASELINE_SHA256,
)
from paraguibench.integrations.pipeline_implicit.verified_assets import (
    COMBINATION002_INPUT_MANIFEST_PATH,
    COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
    COMBINATION002_TASK_UID,
    EXCEL008_GOLD_MANIFEST_PATH,
    EXCEL008_INPUT_MANIFEST_PATH,
    EXCEL008_TASK_UID,
    PPT003_GOLD_MANIFEST_PATH,
    PPT003_INPUT_MANIFEST_PATH,
    PPT003_TASK_UID,
    SEARCHWRITE008_GOLD_MANIFEST_PATH,
    SEARCHWRITE008_INPUT_MANIFEST_PATH,
    PipelineImplicitGoldManifest,
    PipelineImplicitGoldManifestError,
    PipelineImplicitKnownNegativeManifest,
    PipelineImplicitKnownNegativeManifestError,
    build_combination002_asset_manifest_documents,
    build_excel008_asset_manifest_documents,
    build_ppt003_asset_manifest_documents,
    build_searchwrite008_asset_manifest_documents,
    load_verified_pipeline_implicit_gold_manifest,
    load_pipeline_implicit_known_negative_manifest,
    serialize_pipeline_implicit_asset_manifest,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.runtime.assets import (
    AssetManifest,
    AssetManifestError,
    ResolvedTaskAssets,
    TaskAssetMode,
    load_asset_manifest_bytes,
    read_manifest_bytes_nofollow,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    derive_pipeline_implicit_environment_identity,
    load_trusted_pipeline_implicit_component_receipts,
)


_PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
_INPUT_DRAFT_MANIFEST = Path(
    "benchmark/assets/manifests/pipeline-implicit-input-v1.draft.json"
)
_GOLD_DRAFT_MANIFEST = Path(
    "benchmark/gold/manifests/pipeline-implicit-gold-v1.draft.json"
)
_OSWORLD_IMAGE_MANIFEST = Path("environments/osworld/image-manifest.json")
_COMBINATION_TASK_ID = "Operation-FileOperate-CombinationDocs-002"
_BLOCKED_LOCAL_COMPONENT_CODES = (
    "pipeline_implicit_input_asset_metadata_unverified",
    "pipeline_implicit_gold_asset_metadata_unverified",
    "pipeline_implicit_typed_observation_parser_not_migrated",
)
_PIPELINE_LIVE_BLOCKER = "pipeline_implicit_live_validation_not_completed"
_GOLD_REFERENCE_ROLE = "gold"
_KNOWN_NEGATIVE_REFERENCE_ROLE = "audit_known_negative"
_EXCEL008_MACHINE_IDENTITY_VERSION = "paraguibench.pipeline.excel008.runtime.v1"
_COMBINATION002_MACHINE_IDENTITY_VERSION = (
    "paraguibench.pipeline.combination002.runtime.v1"
)
_EXCEL008_MACHINE_IDENTITY_SHA256 = (
    "e6f09bf55d0c71378559c5f290d1f1b81d82f804cde75bdf9667838fe9d9be57"
)
_COMBINATION002_MACHINE_IDENTITY_SHA256 = (
    "15abfb1eafa563723fc331112680f2b6aabb52d374cf42858eac06b2e08c4900"
)
PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS: frozenset[str] = frozenset(
    {
        IMAGE_CLASSIFICATION_TASK_ID,
        HIDE_NA_ROWS_TASK_ID,
        CROSS_DOCUMENT_TASK_ID,
        SEARCHWRITE_XLSX_TASK_ID,
    }
)
PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS: frozenset[str] = frozenset(
    {
        IMAGE_CLASSIFICATION_TASK_ID,
        HIDE_NA_ROWS_TASK_ID,
        CROSS_DOCUMENT_TASK_ID,
        SEARCHWRITE_XLSX_TASK_ID,
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class PipelineImplicitRuntimeCapability:
    """保存一个 production-ready pipeline task 的机器身份。

    输入参数：
        task_id/protocol_id：固定 canonical task 与纯评价协议。
        input_manifest_sha256/reference_manifest_sha256：本次 preflight
            实际读取的 input 与 task-specific reference 原始字节摘要。
        reference_manifest_role：正式 gold 或 audit-only known-negative；
            后者只可绑定机器身份，不能成为 pass oracle。
    输出返回值：
        不可变能力；仅由 ``preflight_pipeline_implicit_runtime`` 在任务、
        清单、typed bridge 与评价器哈希映射全部一致时返回。
    """

    task_id: str
    protocol_id: str
    input_manifest_sha256: str
    reference_manifest_sha256: str
    reference_manifest_role: str
    environment_manifest_sha256: str | None = None
    environment_identity_sha256: str | None = None
    container_image: str | None = None
    extracted_qcow2_sha256: str | None = None

    def __repr__(self) -> str:
        """返回不含路径、摘要或资产身份的脱敏表示。

        输入参数：无。
        输出返回值：只显示 task/protocol 的稳定字符串。
        """

        return (
            "PipelineImplicitRuntimeCapability("
            f"task_id={self.task_id!r}, protocol_id={self.protocol_id!r})"
        )


class PipelineImplicitRuntimeBlockedError(RuntimeError):
    """表示 pipeline task 的正式组件仍未闭合。

    输入参数：
        blocker_codes：不含路径、文件名、摘要或内容的固定阻断码。
    输出返回值：
        可由 CLI 稳定分类、且不泄漏草案证据的异常。
    """

    code = "PIPELINE_IMPLICIT_RUNTIME_BLOCKED"

    def __init__(self, blocker_codes: tuple[str, ...]) -> None:
        self.blocker_codes = blocker_codes
        super().__init__(self.code)


class PipelineImplicitRuntimeManifestError(RuntimeError):
    """表示仓库内 pipeline 正式清单或草案不可信。

    输入参数：无；错误文本恒定。
    输出返回值：不暴露路径或 manifest 内容的 fail-closed 异常。
    """

    code = "PIPELINE_IMPLICIT_MANIFEST_INVALID"

    def __init__(self) -> None:
        super().__init__(self.code)


def validate_pipeline_implicit_runtime_capability(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
    task_assets: ResolvedTaskAssets,
    capability: PipelineImplicitRuntimeCapability | None,
) -> None:
    """把 preflight capability 绑定到 environment 实际上传的资产。

    输入参数：
        repo_root/task：当前 release checkout 与已准备 canonical task。
        task_assets：environment 本次已解析、后续将用于验证缓存
            和上传 guest 的不可变 input manifest。
        capability：CLI 在版本向量和 VM 启动前获得的 pipeline
            runtime capability；非 pipeline task 必须为 ``None``。
    输出返回值：
        task/protocol、input/gold 原始字节摘要及本次实际使用的
        parsed input manifest 全部同源时返回 ``None``。
    异常：
        PipelineImplicitRuntimeManifestError：capability 缺失、多余，
            或 preflight→prepare 期间任何 canonical/manifest 身份漂移。
        PipelineImplicitRuntimeBlockedError：调用方试图绕过未完成任务阻断。

    本函数在 guest I/O 前重新执行完整 preflight，并把调用方
    实际持有、后续将用于上传的 ``task_assets.manifest`` 直接与
    确定性正式 builder 的固定 A 字节解析结果比较。该比较不依赖
    第二次仓库路径读取，因而拒绝 A→B→A/B 时序下的 ABA 换包。
    """

    if not isinstance(task_assets, ResolvedTaskAssets):
        raise PipelineImplicitRuntimeManifestError()
    task_id = task.get("task_id")
    is_pipeline_task = task_id in PIPELINE_IMPLICIT_TASK_PROTOCOLS
    if not is_pipeline_task:
        if capability is not None:
            raise PipelineImplicitRuntimeManifestError()
        return
    if (
        not isinstance(capability, PipelineImplicitRuntimeCapability)
        or capability.task_id != task_id
        or capability.protocol_id != PIPELINE_IMPLICIT_TASK_PROTOCOLS[task_id]
        or task_assets.mode is not TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
        or task_assets.manifest is None
    ):
        raise PipelineImplicitRuntimeManifestError()
    observed_capability = preflight_pipeline_implicit_local_runtime(
        repo_root=repo_root,
        task=task,
    )
    if task_id == IMAGE_CLASSIFICATION_TASK_ID:
        expected_documents = build_ppt003_asset_manifest_documents(repo_root)
        expected_input_path = PPT003_INPUT_MANIFEST_PATH
    elif task_id == HIDE_NA_ROWS_TASK_ID:
        expected_documents = build_excel008_asset_manifest_documents(repo_root)
        expected_input_path = EXCEL008_INPUT_MANIFEST_PATH
    elif task_id == CROSS_DOCUMENT_TASK_ID:
        expected_documents = build_combination002_asset_manifest_documents(repo_root)
        expected_input_path = COMBINATION002_INPUT_MANIFEST_PATH
    elif task_id == SEARCHWRITE_XLSX_TASK_ID:
        expected_documents = build_searchwrite008_asset_manifest_documents(repo_root)
        expected_input_path = SEARCHWRITE008_INPUT_MANIFEST_PATH
    else:
        raise PipelineImplicitRuntimeBlockedError(_BLOCKED_LOCAL_COMPONENT_CODES)
    expected_input_payload = serialize_pipeline_implicit_asset_manifest(
        expected_documents[expected_input_path]
    )
    expected_input_manifest = load_asset_manifest_bytes(expected_input_payload)
    if (
        not _same_task_asset_capability(observed_capability, capability)
        or hashlib.sha256(expected_input_payload).hexdigest()
        != capability.input_manifest_sha256
        or task_assets.manifest != expected_input_manifest
        or task_assets.manifest.asset_set_id != task_id
    ):
        raise PipelineImplicitRuntimeManifestError()


def preflight_pipeline_implicit_runtime(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
    image_manifest: OSWorldImageManifest | None = None,
) -> PipelineImplicitRuntimeCapability | None:
    """在外部副作用前绑定普通评测能力，不消费 component receipt。

    输入参数：
        repo_root：包含 canonical、正式资产清单和历史草案的仓库根。
        task：已校验的 trusted task 视图。
        image_manifest：可选的当前 OSWorld 镜像快照；提供时必须与
            仓库内正式 manifest 一致。
    输出返回值：
        非 pipeline task 返回 ``None``；四个本地闭合任务返回不可变、
        绑定两份 manifest 原始字节摘要的 capability。
    异常：
        PipelineImplicitRuntimeManifestError：canonical、正式清单、草案或
            纯 evaluator 固定映射任一身份漂移。
        PipelineImplicitRuntimeBlockedError：当前任务仍缺正式本地组件。
            component receipt 只供可选官方审计，不阻断普通 ``run``。
    """

    try:
        capability = preflight_pipeline_implicit_local_runtime(
            repo_root=repo_root,
            task=task,
        )
    except PipelineImplicitRuntimeBlockedError as error:
        blockers = error.blocker_codes
        if _PIPELINE_LIVE_BLOCKER not in blockers:
            if blockers[: len(_BLOCKED_LOCAL_COMPONENT_CODES)] == (
                _BLOCKED_LOCAL_COMPONENT_CODES
            ):
                blockers = (
                    blockers[: len(_BLOCKED_LOCAL_COMPONENT_CODES)]
                    + (_PIPELINE_LIVE_BLOCKER,)
                    + blockers[len(_BLOCKED_LOCAL_COMPONENT_CODES) :]
                )
            else:
                blockers += (_PIPELINE_LIVE_BLOCKER,)
        raise PipelineImplicitRuntimeBlockedError(blockers) from None
    if capability is None:
        return None
    try:
        current_manifest, current_sha256 = load_osworld_image_manifest_with_sha256(
            repo_root / _OSWORLD_IMAGE_MANIFEST,
        )
    except Exception:
        raise PipelineImplicitRuntimeManifestError from None
    if image_manifest is not None and (
        type(image_manifest) is not OSWorldImageManifest
        or image_manifest != current_manifest
        or image_manifest.manifest_sha256 != current_sha256
    ):
        raise PipelineImplicitRuntimeManifestError
    return _bind_capability_to_image_manifest(capability, current_manifest)


def preflight_pipeline_implicit_component_candidate_runtime(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
    image_manifest: OSWorldImageManifest,
) -> PipelineImplicitRuntimeCapability | None:
    """为显式无 Agent candidate refresh 绑定 receipt-neutral 本地能力。

    输入参数：repo_root 为当前 checkout；task 为 trusted task 视图。
    输出返回值：四个当前静态组件闭合任务返回 capability；非 pipeline
        task 返回 ``None``。本函数故意不读取旧 receipt/allowlist。
    异常：PipelineImplicitRuntimeManifestError：task/manifest/evaluator 漂移；
        PipelineImplicitRuntimeBlockedError：任一未闭合组件失败关闭。本函数
        不会删除 image/manifest 门禁。
    """

    capability = preflight_pipeline_implicit_local_runtime(
        repo_root=repo_root,
        task=task,
    )
    if capability is None:
        return capability
    try:
        current_manifest, current_sha256 = load_osworld_image_manifest_with_sha256(
            repo_root / _OSWORLD_IMAGE_MANIFEST,
        )
    except Exception:
        raise PipelineImplicitRuntimeManifestError from None
    if (
        type(image_manifest) is not OSWorldImageManifest
        or image_manifest != current_manifest
        or image_manifest.manifest_sha256 != current_sha256
    ):
        raise PipelineImplicitRuntimeManifestError
    return _bind_capability_to_image_manifest(capability, current_manifest)


def _bind_capability_to_image_manifest(
    capability: PipelineImplicitRuntimeCapability,
    image_manifest: OSWorldImageManifest,
) -> PipelineImplicitRuntimeCapability:
    """把静态 task capability 绑定到 CLI 首次 same-FD image 对象。

    输入参数：capability 为本地 input/gold/typed 绑定；image_manifest
        必须是生产 loader 的精确对象并携带同源原始 SHA。
    输出返回值：追加 environment manifest SHA/identity、qcow 与 OCI
        声明的冻结 capability。
    异常：PipelineImplicitRuntimeManifestError：对象或环境身份无效。
    """

    if (
        not isinstance(capability, PipelineImplicitRuntimeCapability)
        or type(image_manifest) is not OSWorldImageManifest
    ):
        raise PipelineImplicitRuntimeManifestError
    try:
        environment_identity = derive_pipeline_implicit_environment_identity(
            image_manifest
        )
    except Exception:
        raise PipelineImplicitRuntimeManifestError from None
    if not isinstance(image_manifest.manifest_sha256, str):
        raise PipelineImplicitRuntimeManifestError
    return replace(
        capability,
        environment_manifest_sha256=image_manifest.manifest_sha256,
        environment_identity_sha256=environment_identity,
        container_image=image_manifest.container_image,
        extracted_qcow2_sha256=image_manifest.extracted_sha256,
    )


def _same_task_asset_capability(
    observed: PipelineImplicitRuntimeCapability,
    expected: PipelineImplicitRuntimeCapability,
) -> bool:
    """比较 prepare ABA 复验所需的 task/input/gold 核心身份。

    输入参数：observed 为 prepare 时重算的本地能力；expected 为 CLI
        可能已追加 environment 绑定的能力。
    输出返回值：task、protocol 与 input/gold 原始 SHA 全等时返回真。
    """

    return (
        isinstance(observed, PipelineImplicitRuntimeCapability)
        and isinstance(expected, PipelineImplicitRuntimeCapability)
        and observed.task_id == expected.task_id
        and observed.protocol_id == expected.protocol_id
        and observed.input_manifest_sha256 == expected.input_manifest_sha256
        and observed.reference_manifest_sha256 == expected.reference_manifest_sha256
        and observed.reference_manifest_role == expected.reference_manifest_role
    )


def preflight_pipeline_implicit_local_runtime(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
) -> PipelineImplicitRuntimeCapability | None:
    """绑定不消费 component receipt 的静态 production 能力。

    输入参数：repo_root 为当前 release checkout；task 为 trusted task。
    输出返回值：非 pipeline task 返回 ``None``；四个本地闭合任务返回与
        input/reference 原始字节绑定的 capability。
    异常：PipelineImplicitRuntimeManifestError：canonical、清单或纯 evaluator
        身份漂移；PipelineImplicitRuntimeBlockedError：尚有真实本地组件阻断。

    该函数供 candidate refresh、gold context、environment.prepare 的
    capability ABA 复验，以及普通 ``run`` 使用。component receipt 只供
    可选官方审计，不进入本函数。
    """

    task_id = task.get("task_id")
    if task_id not in PIPELINE_IMPLICIT_TASK_PROTOCOLS:
        return None
    if task_id == IMAGE_CLASSIFICATION_TASK_ID:
        return _bind_ppt003_runtime(repo_root=repo_root, task=task)
    if task_id == HIDE_NA_ROWS_TASK_ID:
        return _bind_excel008_runtime(repo_root=repo_root, task=task)
    if task_id == CROSS_DOCUMENT_TASK_ID:
        return _bind_combination002_runtime(repo_root=repo_root, task=task)
    if task_id == SEARCHWRITE_XLSX_TASK_ID:
        return _bind_searchwrite008_runtime(repo_root=repo_root, task=task)
    raise PipelineImplicitRuntimeManifestError


def _bind_excel008_runtime(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
) -> PipelineImplicitRuntimeCapability:
    """绑定 Excel-008 canonical、五 input/五 gold 与语义评价身份。

    输入参数：repo_root 为当前 checkout；task 为 trusted canonical object。
    输出返回值：绑定两份 held manifest 原始 SHA、gold 角色及 typed 协议的
        production capability；任务正文不参与消歧或 Agent 视图扩展。
    异常：PipelineImplicitRuntimeManifestError：canonical、manifest、隐藏行
        语义、baseline 或 evaluator 机器身份任一漂移。
    """

    expected_identity = {
        "task_id": HIDE_NA_ROWS_TASK_ID,
        "task_uid": EXCEL008_TASK_UID,
        "task_type": "OSWorld脚本改造",
        "task_source": "OSWorld",
        "task_tag": "FileOperate",
        "evaluator_path": "",
        "asset_manifest": EXCEL008_INPUT_MANIFEST_PATH,
        "gold_manifest": EXCEL008_GOLD_MANIFEST_PATH,
    }
    if (
        any(task.get(field) != value for field, value in expected_identity.items())
        or "prepare_script_path" in task
    ):
        raise PipelineImplicitRuntimeManifestError
    try:
        input_payload = read_manifest_bytes_nofollow(
            repo_root / EXCEL008_INPUT_MANIFEST_PATH
        )
        reference_payload = read_manifest_bytes_nofollow(
            repo_root / EXCEL008_GOLD_MANIFEST_PATH,
            max_bytes=1_048_576,
        )
        documents = build_excel008_asset_manifest_documents(repo_root)
        if input_payload != serialize_pipeline_implicit_asset_manifest(
            documents[EXCEL008_INPUT_MANIFEST_PATH]
        ) or reference_payload != serialize_pipeline_implicit_asset_manifest(
            documents[EXCEL008_GOLD_MANIFEST_PATH]
        ):
            raise PipelineImplicitRuntimeManifestError
        input_manifest = load_asset_manifest_bytes(input_payload)
        reference_manifest = load_verified_pipeline_implicit_gold_manifest(
            reference_payload
        )
        _validate_excel008_evaluator_machine_identity(
            input_manifest=input_manifest,
            reference_manifest=reference_manifest,
            input_manifest_sha256=hashlib.sha256(input_payload).hexdigest(),
            reference_manifest_sha256=hashlib.sha256(reference_payload).hexdigest(),
        )
    except PipelineImplicitRuntimeManifestError:
        raise
    except (
        OSError,
        AssetManifestError,
        PipelineImplicitGoldManifestError,
        TypeError,
        ValueError,
    ):
        raise PipelineImplicitRuntimeManifestError from None
    return PipelineImplicitRuntimeCapability(
        task_id=HIDE_NA_ROWS_TASK_ID,
        protocol_id=HIDE_NA_ROWS_PROTOCOL_ID,
        input_manifest_sha256=hashlib.sha256(input_payload).hexdigest(),
        reference_manifest_sha256=hashlib.sha256(reference_payload).hexdigest(),
        reference_manifest_role=_GOLD_REFERENCE_ROLE,
    )


def _validate_excel008_evaluator_machine_identity(
    *,
    input_manifest: AssetManifest,
    reference_manifest: PipelineImplicitGoldManifest,
    input_manifest_sha256: str,
    reference_manifest_sha256: str,
) -> None:
    """重算 Excel-008 五文档 hidden-row evaluator 机器身份。

    输入参数：两份 strict parsed manifest 与各自同次 nofollow 原始 SHA。
    输出返回值：input/gold 路径闭集、固定隐藏行和忽略 hidden 的 baseline
        摘要共同得到编译期机器身份时返回。
    异常：PipelineImplicitRuntimeManifestError：任一合同漂移。
    """

    expected_paths = tuple(PINNED_HIDDEN_ROWS_BY_DOCUMENT)
    if (
        not isinstance(input_manifest, AssetManifest)
        or not isinstance(reference_manifest, PipelineImplicitGoldManifest)
        or input_manifest.asset_set_id != HIDE_NA_ROWS_TASK_ID
        or reference_manifest.task_id != HIDE_NA_ROWS_TASK_ID
        or reference_manifest.task_uid != EXCEL008_TASK_UID
        or tuple(item.path for item in input_manifest.files) != expected_paths
        or tuple(item.path for item in reference_manifest.entries) != expected_paths
        or set(PINNED_HIDE_NA_ROWS_BASELINE_SHA256) != set(expected_paths)
    ):
        raise PipelineImplicitRuntimeManifestError
    identity = {
        "identity_version": _EXCEL008_MACHINE_IDENTITY_VERSION,
        "task_id": HIDE_NA_ROWS_TASK_ID,
        "task_uid": EXCEL008_TASK_UID,
        "protocol_id": HIDE_NA_ROWS_PROTOCOL_ID,
        "input_manifest": {
            "raw_sha256": input_manifest_sha256,
            "entries": [
                {
                    "path": item.path,
                    "size_bytes": item.size,
                    "sha256": item.sha256,
                    "media_type": item.media_type,
                }
                for item in input_manifest.files
            ],
        },
        "reference_manifest": {
            "role": _GOLD_REFERENCE_ROLE,
            "raw_sha256": reference_manifest_sha256,
            "entries": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "media_type": item.media_type,
                }
                for item in reference_manifest.entries
            ],
        },
        "documents": [
            {
                "relative_path": path,
                "hidden_rows": list(PINNED_HIDDEN_ROWS_BY_DOCUMENT[path]),
                "baseline_sha256": PINNED_HIDE_NA_ROWS_BASELINE_SHA256[path],
            }
            for path in sorted(
                PINNED_HIDDEN_ROWS_BY_DOCUMENT,
                key=lambda value: value.encode("utf-8"),
            )
        ],
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    if hashlib.sha256(encoded).hexdigest() != _EXCEL008_MACHINE_IDENTITY_SHA256:
        raise PipelineImplicitRuntimeManifestError


def _bind_combination002_runtime(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
) -> PipelineImplicitRuntimeCapability:
    """绑定 Combo-002 input 与 XLSX-source/audit-negative 机器身份。

    输入参数：repo_root 为当前 checkout；task 为 trusted canonical object。
    输出返回值：input 与 audit-only manifest 原始 SHA、typed 协议和 reference
        角色绑定的 capability；不存在 formal gold/pass-oracle seam。
    异常：PipelineImplicitRuntimeManifestError：canonical、manifest、XLSX
        事实源、known-negative 失败结论或 evaluator 身份任一漂移。
    """

    expected_identity = {
        "task_id": CROSS_DOCUMENT_TASK_ID,
        "task_uid": COMBINATION002_TASK_UID,
        "task_type": "self",
        "task_source": "",
        "task_tag": "FileOperate",
        "evaluator_path": "",
        "asset_manifest": COMBINATION002_INPUT_MANIFEST_PATH,
        "known_negative_manifest": COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
    }
    if (
        any(task.get(field) != value for field, value in expected_identity.items())
        or "prepare_script_path" in task
        or "gold_manifest" in task
    ):
        raise PipelineImplicitRuntimeManifestError
    try:
        input_payload = read_manifest_bytes_nofollow(
            repo_root / COMBINATION002_INPUT_MANIFEST_PATH
        )
        reference_payload = read_manifest_bytes_nofollow(
            repo_root / COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH,
            max_bytes=1_048_576,
        )
        documents = build_combination002_asset_manifest_documents(repo_root)
        if input_payload != serialize_pipeline_implicit_asset_manifest(
            documents[COMBINATION002_INPUT_MANIFEST_PATH]
        ) or reference_payload != serialize_pipeline_implicit_asset_manifest(
            documents[COMBINATION002_KNOWN_NEGATIVE_MANIFEST_PATH]
        ):
            raise PipelineImplicitRuntimeManifestError
        input_manifest = load_asset_manifest_bytes(input_payload)
        reference_manifest = load_pipeline_implicit_known_negative_manifest(
            reference_payload
        )
        _validate_combination002_evaluator_machine_identity(
            input_manifest=input_manifest,
            reference_manifest=reference_manifest,
            input_manifest_sha256=hashlib.sha256(input_payload).hexdigest(),
            reference_manifest_sha256=hashlib.sha256(reference_payload).hexdigest(),
        )
    except PipelineImplicitRuntimeManifestError:
        raise
    except (
        OSError,
        AssetManifestError,
        PipelineImplicitKnownNegativeManifestError,
        TypeError,
        ValueError,
    ):
        raise PipelineImplicitRuntimeManifestError from None
    return PipelineImplicitRuntimeCapability(
        task_id=CROSS_DOCUMENT_TASK_ID,
        protocol_id=CROSS_DOCUMENT_PROTOCOL_ID,
        input_manifest_sha256=hashlib.sha256(input_payload).hexdigest(),
        reference_manifest_sha256=hashlib.sha256(reference_payload).hexdigest(),
        reference_manifest_role=_KNOWN_NEGATIVE_REFERENCE_ROLE,
    )


def _validate_combination002_evaluator_machine_identity(
    *,
    input_manifest: AssetManifest,
    reference_manifest: PipelineImplicitKnownNegativeManifest,
    input_manifest_sha256: str,
    reference_manifest_sha256: str,
) -> None:
    """重算 Combo-002 XLSX 唯一事实源与 known-negative 机器身份。

    输入参数：strict input/audit manifest 与各自同次原始 SHA。
    输出返回值：三 input、typed XLSX 事实、HF answer 固定 FAIL 2/3 和协议
        共同等于编译期身份时返回；不读取负例 payload。
    异常：PipelineImplicitRuntimeManifestError：任一合同漂移。
    """

    expected_paths = (
        "McDonald_finacial_report.docx",
        "McDonalds_Monthly_Data.xlsx",
        "McDonalds_powerpoint_report.pptx",
    )
    if (
        not isinstance(input_manifest, AssetManifest)
        or not isinstance(
            reference_manifest,
            PipelineImplicitKnownNegativeManifest,
        )
        or input_manifest.asset_set_id != CROSS_DOCUMENT_TASK_ID
        or reference_manifest.task_id != CROSS_DOCUMENT_TASK_ID
        or reference_manifest.task_uid != COMBINATION002_TASK_UID
        or reference_manifest.expected_score != 0.6667
        or reference_manifest.expected_reason_codes != ("DOCX_PROFIT_ORDER_INCORRECT",)
        or tuple(item.path for item in input_manifest.files) != expected_paths
        or tuple(item.path for item in reference_manifest.entries) != expected_paths
    ):
        raise PipelineImplicitRuntimeManifestError
    input_by_path = {item.path: item for item in input_manifest.files}
    spreadsheet = input_by_path.get("McDonalds_Monthly_Data.xlsx")
    if (
        spreadsheet is None
        or spreadsheet.sha256
        != "abaf2d2622354d6c8a1cd6115cda4b1e5b82ccdcd01565d739e75aa606e750b9"
    ):
        raise PipelineImplicitRuntimeManifestError
    identity = {
        "identity_version": _COMBINATION002_MACHINE_IDENTITY_VERSION,
        "task_id": CROSS_DOCUMENT_TASK_ID,
        "task_uid": COMBINATION002_TASK_UID,
        "protocol_id": CROSS_DOCUMENT_PROTOCOL_ID,
        "input_manifest": {
            "raw_sha256": input_manifest_sha256,
            "entries": [
                {
                    "path": item.path,
                    "size_bytes": item.size,
                    "sha256": item.sha256,
                    "media_type": item.media_type,
                }
                for item in input_manifest.files
            ],
        },
        "reference_manifest": {
            "role": _KNOWN_NEGATIVE_REFERENCE_ROLE,
            "raw_sha256": reference_manifest_sha256,
            "entries": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "media_type": item.media_type,
                }
                for item in reference_manifest.entries
            ],
            "expected_evaluation": {
                "protocol_id": CROSS_DOCUMENT_PROTOCOL_ID,
                "passed": False,
                "score": 0.6667,
                "required_fact_count": 3,
                "matched_fact_count": 2,
                "reason_codes": ["DOCX_PROFIT_ORDER_INCORRECT"],
            },
        },
        "source_spreadsheet_sha256": spreadsheet.sha256,
        "monthly_reference": [
            {
                "month": month,
                "profit": facts.profit,
                "customers": facts.customers,
            }
            for month, facts in PINNED_XLSX_MONTHLY_REFERENCE.items()
        ],
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    if hashlib.sha256(encoded).hexdigest() != _COMBINATION002_MACHINE_IDENTITY_SHA256:
        raise PipelineImplicitRuntimeManifestError


def _bind_searchwrite008_runtime(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
) -> PipelineImplicitRuntimeCapability:
    """绑定 SearchWrite-008 canonical、两份正式清单与 typed 身份。

    输入参数：
        repo_root：当前 release checkout 根。
        task：待校验的 trusted canonical task。
    输出返回值：
        task/UID/revision/path/size/SHA/MIME、typed bridge 与 evaluator
        机器身份全部一致时，返回与同次读取原始字节绑定的
        不可变 production capability。
    异常：
        PipelineImplicitRuntimeManifestError：canonical 或任一实际读取
            的 bounded nofollow 稳定字节快照与固定身份不一致。
    """

    expected_identity = {
        "task_id": SEARCHWRITE_XLSX_TASK_ID,
        "task_uid": SEARCHWRITE_XLSX_TASK_UID,
        "task_type": "",
        "task_source": "self",
        "task_tag": "FileOperate",
        "evaluator_path": "",
        "asset_manifest": SEARCHWRITE008_INPUT_MANIFEST_PATH,
        "gold_manifest": SEARCHWRITE008_GOLD_MANIFEST_PATH,
    }
    if (
        any(task.get(field) != value for field, value in expected_identity.items())
        or "prepare_script_path" in task
    ):
        raise PipelineImplicitRuntimeManifestError()
    try:
        input_payload = read_manifest_bytes_nofollow(
            repo_root / SEARCHWRITE008_INPUT_MANIFEST_PATH,
        )
        gold_payload = read_manifest_bytes_nofollow(
            repo_root / SEARCHWRITE008_GOLD_MANIFEST_PATH,
            max_bytes=1_048_576,
        )
        expected_documents = build_searchwrite008_asset_manifest_documents(repo_root)
        expected_input_payload = serialize_pipeline_implicit_asset_manifest(
            expected_documents[SEARCHWRITE008_INPUT_MANIFEST_PATH]
        )
        expected_gold_payload = serialize_pipeline_implicit_asset_manifest(
            expected_documents[SEARCHWRITE008_GOLD_MANIFEST_PATH]
        )
        input_manifest_sha256 = hashlib.sha256(input_payload).hexdigest()
        gold_manifest_sha256 = hashlib.sha256(gold_payload).hexdigest()
        if (
            input_payload != expected_input_payload
            or gold_payload != expected_gold_payload
            or input_manifest_sha256 != SEARCHWRITE_INPUT_MANIFEST_SHA256
            or gold_manifest_sha256 != SEARCHWRITE_GOLD_MANIFEST_SHA256
        ):
            raise PipelineImplicitRuntimeManifestError()
        input_manifest = load_asset_manifest_bytes(input_payload)
        gold_manifest = load_verified_pipeline_implicit_gold_manifest(gold_payload)
        if (
            input_manifest.asset_set_id != SEARCHWRITE_XLSX_TASK_ID
            or len(input_manifest.files) != 2
            or gold_manifest.task_id != SEARCHWRITE_XLSX_TASK_ID
            or gold_manifest.task_uid != SEARCHWRITE_XLSX_TASK_UID
            or len(gold_manifest.entries) != 2
        ):
            raise PipelineImplicitRuntimeManifestError()
        _validate_searchwrite008_evaluator_machine_identity(
            input_manifest=input_manifest,
            gold_manifest=gold_manifest,
            input_manifest_sha256=input_manifest_sha256,
            gold_manifest_sha256=gold_manifest_sha256,
        )
    except PipelineImplicitRuntimeManifestError:
        raise
    except (
        OSError,
        AssetManifestError,
        PipelineImplicitGoldManifestError,
        TypeError,
        ValueError,
    ):
        raise PipelineImplicitRuntimeManifestError() from None

    return PipelineImplicitRuntimeCapability(
        task_id=SEARCHWRITE_XLSX_TASK_ID,
        protocol_id=SEARCHWRITE_XLSX_PROTOCOL_ID,
        input_manifest_sha256=input_manifest_sha256,
        reference_manifest_sha256=gold_manifest_sha256,
        reference_manifest_role=_GOLD_REFERENCE_ROLE,
    )


def _validate_searchwrite008_evaluator_machine_identity(
    *,
    input_manifest: AssetManifest,
    gold_manifest: PipelineImplicitGoldManifest,
    input_manifest_sha256: str,
    gold_manifest_sha256: str,
) -> None:
    """重算 SearchWrite-008 跨清单与 typed evaluator 机器身份。

    输入参数：
        input_manifest/gold_manifest：仅由严格原始 bytes loader
            产生的不可变清单。
        input_manifest_sha256/gold_manifest_sha256：本次 nofollow 读取
            所得的原始 manifest 字节摘要。
    输出返回值：
        全部身份组成的 strict JSON SHA-256 等于固定值时返回
        ``None``；不读取 Agent final text 或任何工件内容。
    异常：
        PipelineImplicitRuntimeManifestError：条目、顺序、协议、坐标、
            显式值类型、期望值或基线摘要任一漂移。
    """

    if (
        not isinstance(input_manifest, AssetManifest)
        or not isinstance(gold_manifest, PipelineImplicitGoldManifest)
        or input_manifest.asset_set_id != SEARCHWRITE_XLSX_TASK_ID
        or gold_manifest.task_id != SEARCHWRITE_XLSX_TASK_ID
        or gold_manifest.task_uid != SEARCHWRITE_XLSX_TASK_UID
        or input_manifest_sha256 != SEARCHWRITE_INPUT_MANIFEST_SHA256
        or gold_manifest_sha256 != SEARCHWRITE_GOLD_MANIFEST_SHA256
    ):
        raise PipelineImplicitRuntimeManifestError()
    expected_paths = tuple(
        document.relative_path for document in SEARCHWRITE_DOCUMENT_CONTRACTS
    )
    if (
        tuple(item.path for item in input_manifest.files) != expected_paths
        or tuple(item.path for item in gold_manifest.entries) != expected_paths
    ):
        raise PipelineImplicitRuntimeManifestError()

    input_entries = {
        item.path: {
            "path": item.path,
            "size_bytes": item.size,
            "sha256": item.sha256,
            "media_type": item.media_type,
        }
        for item in input_manifest.files
    }
    gold_entries = {
        item.path: {
            "path": item.path,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "media_type": item.media_type,
        }
        for item in gold_manifest.entries
    }
    if (
        len(input_entries) != len(expected_paths)
        or len(gold_entries) != len(expected_paths)
        or set(input_entries) != set(expected_paths)
        or set(gold_entries) != set(expected_paths)
    ):
        raise PipelineImplicitRuntimeManifestError()

    identity = {
        "identity_version": SEARCHWRITE_MACHINE_IDENTITY_VERSION,
        "task_id": SEARCHWRITE_XLSX_TASK_ID,
        "task_uid": SEARCHWRITE_XLSX_TASK_UID,
        "protocol_id": SEARCHWRITE_XLSX_PROTOCOL_ID,
        "baseline_projection_protocol_id": (
            SEARCHWRITE_BASELINE_PROJECTION_PROTOCOL_ID
        ),
        "cell_match_protocol_id": SEARCHWRITE_CELL_MATCH_PROTOCOL_ID,
        "input_manifest": {
            "raw_sha256": input_manifest_sha256,
            "source": {
                "provider": input_manifest.source.provider,
                "repository": input_manifest.source.repository,
                "revision": input_manifest.source.revision,
                "base_path": input_manifest.source.base_path,
            },
            "entries": [input_entries[path] for path in expected_paths],
        },
        "gold_manifest": {
            "raw_sha256": gold_manifest_sha256,
            "source": {
                "provider": "huggingface_dataset",
                "repository": "leeLegendary/Parallel_benchmark",
                "revision": gold_manifest.source_revision,
                "base_path": f"answer_files/{SEARCHWRITE_XLSX_TASK_UID}",
            },
            "entries": [gold_entries[path] for path in expected_paths],
        },
        "documents": [
            {
                "relative_path": document.relative_path,
                "document_id": document.document_id,
                "target_coordinates": list(document.target_coordinates),
                "baseline_sha256": document.baseline_sha256,
                "expected_cells": [
                    {
                        "coordinate": cell.coordinate,
                        "value_kind": cell.value_kind,
                        "expected_value": cell.expected_value,
                    }
                    for cell in document.expected_cells
                ],
            }
            for document in SEARCHWRITE_DOCUMENT_CONTRACTS
        ],
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    if hashlib.sha256(encoded).hexdigest() != SEARCHWRITE_MACHINE_IDENTITY_SHA256:
        raise PipelineImplicitRuntimeManifestError()


def _bind_ppt003_runtime(
    *,
    repo_root: Path,
    task: Mapping[str, Any],
) -> PipelineImplicitRuntimeCapability:
    """绑定 PPT-003 canonical、两份清单和纯 evaluator 固定映射。

    输入参数：
        repo_root：当前 release checkout 根。
        task：待绑定的 trusted canonical task。
    输出返回值：
        包含正式 task/protocol 与本机所读清单摘要的不可变 capability。
    异常：
        PipelineImplicitRuntimeManifestError：任一机器身份或闭集不匹配。
    """

    expected_identity = {
        "task_id": IMAGE_CLASSIFICATION_TASK_ID,
        "task_uid": PPT003_TASK_UID,
        "task_type": "self",
        "task_source": "",
        "task_tag": "FileOperate",
        "evaluator_path": "",
        "asset_manifest": PPT003_INPUT_MANIFEST_PATH,
        "gold_manifest": PPT003_GOLD_MANIFEST_PATH,
    }
    if (
        any(task.get(field) != value for field, value in expected_identity.items())
        or "prepare_script_path" in task
    ):
        raise PipelineImplicitRuntimeManifestError()
    try:
        input_payload = read_manifest_bytes_nofollow(
            repo_root / PPT003_INPUT_MANIFEST_PATH,
        )
        gold_payload = read_manifest_bytes_nofollow(
            repo_root / PPT003_GOLD_MANIFEST_PATH,
            max_bytes=1_048_576,
        )
        expected_documents = build_ppt003_asset_manifest_documents(repo_root)
        if input_payload != serialize_pipeline_implicit_asset_manifest(
            expected_documents[PPT003_INPUT_MANIFEST_PATH]
        ):
            raise PipelineImplicitRuntimeManifestError()
        input_manifest = load_asset_manifest_bytes(input_payload)
        if (
            input_manifest.asset_set_id != IMAGE_CLASSIFICATION_TASK_ID
            or len(input_manifest.files) != 20
        ):
            raise PipelineImplicitRuntimeManifestError()
        gold_manifest = load_verified_pipeline_implicit_gold_manifest(gold_payload)
        _validate_ppt003_evaluator_machine_identity(gold_manifest)
    except PipelineImplicitRuntimeManifestError:
        raise
    except (
        OSError,
        AssetManifestError,
        PipelineImplicitGoldManifestError,
        TypeError,
        ValueError,
    ):
        raise PipelineImplicitRuntimeManifestError() from None

    return PipelineImplicitRuntimeCapability(
        task_id=IMAGE_CLASSIFICATION_TASK_ID,
        protocol_id=IMAGE_CLASSIFICATION_PROTOCOL_ID,
        input_manifest_sha256=hashlib.sha256(input_payload).hexdigest(),
        reference_manifest_sha256=hashlib.sha256(gold_payload).hexdigest(),
        reference_manifest_role=_GOLD_REFERENCE_ROLE,
    )


def _validate_ppt003_evaluator_machine_identity(
    manifest: PipelineImplicitGoldManifest,
) -> None:
    """证明 strict gold 的路径→SHA 语义等于纯 evaluator 固定映射。

    输入参数：
        manifest：只能由严格原始 bytes loader 产生的不可变 gold manifest。
    输出返回值：
        映射完全一致时返回 ``None``；比较不读取 Agent final text，也不接受
        调用方提供的可变 dict 作为 gold。
    异常：
        PipelineImplicitRuntimeManifestError：分类、未分类图片或 PPT 映射漂移。
    """

    if (
        not isinstance(manifest, PipelineImplicitGoldManifest)
        or manifest.task_id != IMAGE_CLASSIFICATION_TASK_ID
        or manifest.task_uid != PPT003_TASK_UID
    ):
        raise PipelineImplicitRuntimeManifestError()
    classified: dict[str, set[str]] = {}
    source_images: set[str] = set()
    presentations: dict[str, str] = {}
    for entry in manifest.entries:
        path = PurePosixPath(entry.path)
        if len(path.parts) == 2 and path.parts[0] == "images":
            source_images.add(entry.sha256)
        elif len(path.parts) == 2:
            classified.setdefault(path.parts[0], set()).add(entry.sha256)
        elif len(path.parts) == 1 and path.suffix == ".pptx":
            stem = path.stem
            if not stem.startswith("ppt") or not stem[3:].isdigit():
                raise PipelineImplicitRuntimeManifestError()
            presentations[f"ppt-{int(stem[3:])}"] = entry.sha256
        else:
            raise PipelineImplicitRuntimeManifestError()

    expected_classified = {
        category: set(digests)
        for category, digests in PINNED_CLASSIFIED_IMAGE_SHA256.items()
    }
    classified_digests = set().union(*classified.values()) if classified else set()
    if (
        classified != expected_classified
        or source_images - classified_digests != set(PINNED_UNCLASSIFIED_IMAGE_SHA256)
        or presentations != dict(PINNED_PRESENTATION_SHA256)
    ):
        raise PipelineImplicitRuntimeManifestError()


def _validate_draft_manifest(path: Path, *, role: str) -> None:
    """验证未完成任务仍明确保留未核实元数据。

    输入参数：
        path：仓库固定草案文件。
        role：必须与 manifest 一致的 ``input`` 或 ``gold``。
    输出返回值：
        无；修订、任务闭集、分发政策、license 与每个 entry 的
        size/media_type 均保持明确 unverified 时返回。
    """

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest root invalid")
    if (
        raw.get("schema_version") != 1
        or raw.get("manifest_role") != role
        or raw.get("draft_status") != "metadata_unverified"
        or raw.get("distribution_policy") != "download_only"
    ):
        raise ValueError("manifest identity invalid")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 4:
        raise ValueError("manifest task set invalid")
    observed_task_ids: set[str] = set()
    for item in tasks:
        if not isinstance(item, dict):
            raise ValueError("manifest task invalid")
        task_id = item.get("task_id")
        if not isinstance(task_id, str) or task_id in observed_task_ids:
            raise ValueError("manifest task identity invalid")
        observed_task_ids.add(task_id)
        source = item.get("source")
        license_block = item.get("license")
        entries = item.get("entries")
        if (
            not isinstance(source, dict)
            or source.get("revision") != _PINNED_REVISION
            or not isinstance(license_block, dict)
            or license_block.get("status") != "unverified"
            or license_block.get("spdx_expression") is not None
            or license_block.get("distribution") != "download_only"
            or not isinstance(entries, list)
            or not entries
        ):
            raise ValueError("manifest provenance invalid")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("manifest entry invalid")
            if entry.get("size") != {"status": "unverified", "value": None}:
                raise ValueError("manifest size status invalid")
            if entry.get("media_type") != {
                "status": "unverified",
                "value": None,
            }:
                raise ValueError("manifest media type status invalid")
    if observed_task_ids != set(PIPELINE_IMPLICIT_TASK_PROTOCOLS):
        raise ValueError("manifest task closed set invalid")


__all__ = [
    "PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS",
    "PIPELINE_IMPLICIT_RUNTIME_READY_TASK_IDS",
    "PipelineImplicitRuntimeBlockedError",
    "PipelineImplicitRuntimeCapability",
    "PipelineImplicitRuntimeManifestError",
    "preflight_pipeline_implicit_component_candidate_runtime",
    "preflight_pipeline_implicit_local_runtime",
    "preflight_pipeline_implicit_runtime",
    "validate_pipeline_implicit_runtime_capability",
]
