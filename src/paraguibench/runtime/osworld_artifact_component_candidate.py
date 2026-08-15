"""OSWorld artifact component receipt 的专属无 Agent live candidate。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import secrets
import stat

from paraguibench.benchmark import prepare_release_task
from paraguibench.integrations.osworld.artifact_finalizer import (
    OSWorldArtifactFinalizer,
)
from paraguibench.integrations.osworld.controller import OSWorldController
from paraguibench.integrations.osworld.docker_session import OSWorldDockerConfig
from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.runstore import RunStore
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runtime import osworld_artifact_component_receipts as _receipts
from paraguibench.runtime.artifact_family_task_prepare import (
    preflight_artifact_family_task_prepare,
)
from paraguibench.runtime.assets import (
    TaskAssetMode,
    resolve_task_assets,
    verify_asset_directory,
)
from paraguibench.runtime.gold_assets import load_gold_asset_manifest
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
    OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL,
    osworld_artifact_environment_protocol,
)
from paraguibench.runtime.osworld_artifact_component_receipts import (
    OSWORLD_ARTIFACT_COMPONENT_RECEIPT_KIND,
    OSWorldArtifactComponentIdentity,
    OSWorldArtifactComponentReceipt,
    derive_osworld_artifact_component_identity,
)
from paraguibench.runtime.osworld_artifact_component_validation import (
    _OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_CAPABILITY,
    _run_osworld_artifact_component_validation,
    OSWorldArtifactComponentValidationResult,
)
from paraguibench.runtime.osworld_artifact_evidence import (
    OSWorldArtifactEvidenceSource,
)
from paraguibench.runtime.osworld_attested_qcow2 import (
    OSWorldAttestedDockerSession,
)
from paraguibench.runtime.osworld_environment import OSWorldTaskEnvironment
from paraguibench.runtime.osworld_gold import bind_osworld_task_gold
from paraguibench.runtime.run_versioning import build_run_version_vector


_IMAGE_MANIFEST_RELATIVE_PATH = Path("environments/osworld/image-manifest.json")


class OSWorldArtifactComponentCandidateError(RuntimeError):
    """表示专属 live candidate 未形成可发证的完整生命周期。"""

    code = "OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_INVALID"

    def __init__(self) -> None:
        """构造不回显路径、artifact、gold 或环境值的固定错误。

        输入参数：无。
        输出返回值：无；异常文本只含稳定错误码。
        """

        super().__init__(self.code)


@dataclass(frozen=True, slots=True, repr=False)
class OSWorldArtifactComponentCandidateConfig:
    """保存专属 candidate 的非敏感、不可注入配置。"""

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
        """在任何仓库、缓存、Docker 或 RunStore I/O 前验证配置形状。

        输入参数：无；读取冻结字段。
        输出返回值：路径均为绝对 ``Path``、任务/运行身份合法、
            端口互异且资源上限可解释时正常返回。
        异常：OSWorldArtifactComponentCandidateError：任一字段不安全。
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
                container_name="paraguibench-artifact-component-config-check",
                image="example.invalid/osworld@sha256:" + "0" * 64,
                qcow2_path=self.qcow2_path,
                server_port=self.server_port,
                vnc_port=self.vnc_port,
                chromium_port=self.chromium_port,
                ram_size=self.ram_size,
                cpu_cores=self.cpu_cores,
            )
        except Exception:
            raise OSWorldArtifactComponentCandidateError from None
        if (
            any(not isinstance(path, Path) or not path.is_absolute() for path in paths)
            or run_id != self.run_id
            or task_id != self.task_id
            or attempt_id != self.attempt_id
            or self.task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
            or not isinstance(self.ready_timeout, (int, float))
            or isinstance(self.ready_timeout, bool)
            or self.ready_timeout <= 0
        ):
            raise OSWorldArtifactComponentCandidateError


def run_osworld_artifact_component_candidate(
    config: OSWorldArtifactComponentCandidateConfig,
) -> OSWorldArtifactComponentReceipt:
    """在同一 resolved repo 内执行 preflight→VM→G/D/S→close→receipt。

    输入参数：config 只提供路径、身份、端口和资源上限；
        不接受 Agent、evaluator、environment、controller、Docker runner、
        HTTP session、component proof 或 factory 注入。
    输出返回值：仅在 release/task/input/gold/image/loaded-package、
        私有 asset/gold cache、专属 qcow2+OCI 快照、生产 setup/getter/
        gold metric、owned close 和 RunStore-v2 inspection 全部通过后，
        返回无路径、正文、gold、secret 或 Agent final text 的 receipt。
    异常：OSWorldArtifactComponentCandidateError：任一门禁失败；错误不回显
        底层值。``KeyboardInterrupt``/``SystemExit`` 不被吞掉，但环境
        runner 的 ``finally`` 仍会尝试关闭 owned VM。
    """

    if type(config) is not OSWorldArtifactComponentCandidateConfig:
        raise OSWorldArtifactComponentCandidateError
    try:
        root = _resolve_repository_root(config.repo_root)
        identity_before = derive_osworld_artifact_component_identity(
            root,
            config.task_id,
        )
        image_manifest, image_manifest_sha256 = load_osworld_image_manifest_with_sha256(
            root / _IMAGE_MANIFEST_RELATIVE_PATH,
        )
        expected_environment_protocol = osworld_artifact_environment_protocol(
            config.task_id
        )
        if (
            not image_manifest.live_run_ready
            or image_manifest.extracted_sha256 is None
            or expected_environment_protocol not in image_manifest.protocol_ids
        ):
            raise OSWorldArtifactComponentCandidateError
        prepared_task = prepare_release_task(
            root,
            config.task_id,
            environment_bindings={},
        )
        prepare_binding = preflight_artifact_family_task_prepare(
            repo_root=root,
            task=prepared_task.trusted_task,
        )
        if prepare_binding is None or prepare_binding.task_id != config.task_id:
            raise OSWorldArtifactComponentCandidateError
        task_assets = resolve_task_assets(root, prepared_task.trusted_task)
        if (
            task_assets.mode is not TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
            or task_assets.manifest is None
            or not verify_asset_directory(
                task_assets.manifest,
                config.asset_cache_root / task_assets.manifest.asset_set_id,
            ).ok
        ):
            raise OSWorldArtifactComponentCandidateError
        task_gold = _load_candidate_gold(
            root=root,
            task=prepared_task.trusted_task,
        )
        task_gold.verify(config.gold_cache_root)
        gold_resolver = task_gold.build_resolver(config.gold_cache_root)
        if gold_resolver is None:
            raise OSWorldArtifactComponentCandidateError
        formal_vector = build_run_version_vector(
            repo_root=root,
            task_id=config.task_id,
            environment_manifest_path=root / _IMAGE_MANIFEST_RELATIVE_PATH,
        )
        if (
            formal_vector.evaluation_protocol
            != OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL
            or formal_vector.environment_protocol != expected_environment_protocol
        ):
            raise OSWorldArtifactComponentCandidateError
        candidate_vector = replace(
            formal_vector,
            evaluation_protocol=OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
        )

        docker_config = OSWorldDockerConfig(
            container_name=("paraguibench-artifact-component-" + secrets.token_hex(8)),
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
        controller = OSWorldController(
            f"http://127.0.0.1:{config.server_port}",
        )
        environment = OSWorldTaskEnvironment(
            repo_root=root,
            asset_cache_root=config.asset_cache_root,
            docker_session=docker_session,
            controller=controller,
            artifact_family_task_prepare_binding=prepare_binding,
            artifact_finalizer=OSWorldArtifactFinalizer(),
            artifact_evidence_source=OSWorldArtifactEvidenceSource(
                gold_resolver=gold_resolver,
            ),
            ready_timeout=float(config.ready_timeout),
        )
        store = RunStore(config.runs_root)
        store.start_run(
            run_id=config.run_id,
            run_record={
                "candidate_kind": (
                    "paraguibench.osworld.artifact-component-validation.v1"
                )
            },
            version_vector=candidate_vector,
        )
        attempt = store.start_attempt(
            run_id=config.run_id,
            task_id=config.task_id,
            attempt_id=config.attempt_id,
            task_record=prepared_task.audit_metadata,
        )
        validation = _run_osworld_artifact_component_validation(
            store=store,
            attempt=attempt,
            prepared_task=prepared_task,
            environment=environment,
            _candidate_capability=(_OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_CAPABILITY),
        )
        identity_after = derive_osworld_artifact_component_identity(
            root,
            config.task_id,
        )
        _, image_manifest_sha256_after = load_osworld_image_manifest_with_sha256(
            root / _IMAGE_MANIFEST_RELATIVE_PATH,
        )
        if (
            identity_after != identity_before
            or image_manifest_sha256_after != image_manifest_sha256
        ):
            raise OSWorldArtifactComponentCandidateError
        receipt = _build_candidate_receipt(
            validation=validation,
            identity=identity_before,
        )
        if (
            derive_osworld_artifact_component_identity(root, config.task_id)
            != identity_before
        ):
            raise OSWorldArtifactComponentCandidateError
        return receipt
    except OSWorldArtifactComponentCandidateError:
        raise
    except Exception:
        raise OSWorldArtifactComponentCandidateError from None


def _resolve_repository_root(repo_root: Path) -> Path:
    """以 no-symlink 根节点固定 candidate 的单一仓库上下文。

    输入参数：repo_root 为配置中的绝对路径。
    输出返回值：存在、非 symlink 且为目录的 resolved ``Path``。
    异常：OSWorldArtifactComponentCandidateError：根节点类型或身份无效。
    """

    try:
        metadata = repo_root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        return repo_root.resolve(strict=True)
    except OSError:
        raise OSWorldArtifactComponentCandidateError from None


def _load_candidate_gold(*, root: Path, task: dict[str, object]):
    """从同一仓库 task 安全加载并语义绑定 evaluator-only gold。

    输入参数：root 为 candidate 固定仓库；task 为 release 三投影
        验证过的 canonical task 副本。
    输出返回值：与 task UID、正式 evaluator path、spec logical keys
        及 provenance 精确闭合的 ``ResolvedOSWorldTaskGold``。
    异常：OSWorldArtifactComponentCandidateError：引用路径、manifest 或语义
        绑定无效。
    """

    try:
        task_id = validate_identifier("task_id", str(task["task_id"]))
        reference = task.get("gold_manifest")
        if (
            not isinstance(reference, str)
            or not reference
            or "\\" in reference
            or "\x00" in reference
        ):
            raise ValueError
        relative = PurePosixPath(reference)
        if relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise ValueError
        manifest = load_gold_asset_manifest(root / relative)
        task_uid = task.get("task_uid")
        evaluator_path = task.get("evaluator_path")
        return bind_osworld_task_gold(
            task_id,
            manifest,
            task_uid=task_uid if isinstance(task_uid, str) else None,
            evaluator_path=(
                evaluator_path if isinstance(evaluator_path, str) else None
            ),
        )
    except Exception:
        raise OSWorldArtifactComponentCandidateError from None


def _build_candidate_receipt(
    *,
    validation: OSWorldArtifactComponentValidationResult,
    identity: OSWorldArtifactComponentIdentity,
) -> OSWorldArtifactComponentReceipt:
    """在 top-level 同一调用栈中投影 sealed result 与执行前身份。

    输入参数：validation 由专属 runner 在 owned close 后铸造；
        identity 为同一 root 在任何 VM/RunStore I/O 前派生且已在
        runner 后复验的五层身份。
    输出返回值：只含终态、历史 vector 摘要和五层身份的
        字段闭合 receipt。
    异常：OSWorldArtifactComponentCandidateError：类型、版本向量或投影失效。
    """

    if (
        type(validation) is not OSWorldArtifactComponentValidationResult
        or not isinstance(identity, OSWorldArtifactComponentIdentity)
        or validation.inspection.version_vector is None
    ):
        raise OSWorldArtifactComponentCandidateError
    vector = validation.inspection.version_vector
    try:
        return OSWorldArtifactComponentReceipt(
            schema_version=1,
            receipt_kind=OSWORLD_ARTIFACT_COMPONENT_RECEIPT_KIND,
            task_id=validation.task_id,
            run_id=validation.run_id,
            attempt_id=validation.attempt_id,
            execution_outcome=validation.inspection.execution_outcome.value,
            evaluation_outcome=validation.inspection.evaluation_outcome.value,
            score=float(validation.inspection.score),
            candidate_evaluation_protocol=vector.evaluation_protocol,
            task_evaluation_protocol=OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL,
            environment_protocol=vector.environment_protocol,
            attempt_version_vector_sha256=(
                _receipts._run_version_vector_sha256(vector)
            ),
            task_identity_sha256=identity.task_identity_sha256,
            environment_identity_sha256=identity.environment_identity_sha256,
            setup_component_sha256=identity.setup_component_sha256,
            getter_component_sha256=identity.getter_component_sha256,
            gold_component_sha256=identity.gold_component_sha256,
        )
    except Exception:
        raise OSWorldArtifactComponentCandidateError from None


__all__ = [
    "OSWorldArtifactComponentCandidateConfig",
    "OSWorldArtifactComponentCandidateError",
    "run_osworld_artifact_component_candidate",
]
