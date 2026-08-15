"""为单任务 disposable OSWorld session 准备固定资产与可见 shared 目录。"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from paraguibench.evaluation.osworld import (
    ARTIFACT_STATE_PROTOCOL_ID,
    CHROME_BOOKMARKS_PROTOCOL_ID,
)
from paraguibench.evaluation.operation import (
    OPERATION_PROTOCOL_ID,
    WordAbbreviationBaseline,
    WordAbbreviationError,
    WordTextBaseline,
    WordTextFidelityError,
    WordTextInputFile,
    capture_word_abbreviation_baseline,
    capture_word_text_baseline,
    operation_word_abbreviation_input_contract,
    operation_word_text_input_contract,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OperationArtifactSnapshot,
)
from paraguibench.integrations.osworld.controller import OSWorldController
from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
from paraguibench.integrations.osworld.artifact_finalizer import (
    OSWORLD_ARTIFACT_FINALIZER_TASK_IDS,
    OSWorldArtifactFinalizer,
)
from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
    ArtifactFamilyPreparedAssets,
    ArtifactFamilyTaskPrepareSource,
)
from paraguibench.integrations.osworld.task_prepare import (
    OSWorldTaskPrepareSource,
)
from paraguibench.integrations.pipeline_implicit import (
    PIPELINE_IMPLICIT_TASK_PROTOCOLS,
    PipelineImplicitArtifactObservation,
)
from paraguibench.runtime.assets import (
    AssetManifest,
    AssetManifestError,
    TaskAssetMode,
    resolve_task_assets,
    load_asset_manifest_bytes,
    read_manifest_bytes_nofollow,
    verify_asset_directory,
)
from paraguibench.runtime.artifact_family_task_prepare import (
    ArtifactFamilyTaskPrepareBinding,
    ArtifactFamilyTaskPrepareCapabilityError,
    validate_artifact_family_task_prepare_runtime_binding,
)
from paraguibench.runtime.osworld_artifact_finalization import (
    OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS as _RUNTIME_FINALIZE_TASK_IDS,
)
from paraguibench.runtime.gold_assets import load_gold_asset_manifest
from paraguibench.runtime.osworld_artifact_evidence import (
    OSWorldArtifactEvidenceSource,
)
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
    OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL,
    OSWorldArtifactComponentEnvironmentProof,
)
from paraguibench.runtime.osworld_attested_qcow2 import (
    OSWorldAttestedDockerSession,
)
from paraguibench.runtime.pipeline_implicit_binding import (
    PipelineImplicitRuntimeBlockedError,
    PipelineImplicitRuntimeCapability,
    PipelineImplicitRuntimeManifestError,
    validate_pipeline_implicit_runtime_capability,
)


OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS = _RUNTIME_FINALIZE_TASK_IDS
_OSWORLD_IMAGE_MANIFEST_RELATIVE_PATH = Path("environments/osworld/image-manifest.json")
_OPERATION_WORD_TEXT_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperationWord-009",
        "Operation-FileOperate-BatchOperationWord-010",
    }
)
_OPERATION_WORD_ABBREVIATION_TASK_ID = "Operation-FileOperate-BatchOperationWord-012"


class OSWorldEnvironmentError(RuntimeError):
    """表示 OSWorld session、资产缓存或 guest 准备未通过门禁。"""


class OSWorldTaskEnvironment:
    """组合 owned Docker session、controller 与 download-only 资产缓存。"""

    def __init__(
        self,
        *,
        repo_root: Path,
        asset_cache_root: Path,
        docker_session: Any,
        controller: Any,
        task_prepare_source: Any | None = None,
        artifact_family_task_prepare_binding: (
            ArtifactFamilyTaskPrepareBinding | None
        ) = None,
        artifact_family_task_prepare_source: Any | None = None,
        bookmark_evidence_source: Any | None = None,
        state_evidence_source: Any | None = None,
        artifact_finalizer: Any | None = None,
        artifact_evidence_source: Any | None = None,
        operation_artifact_source: Any | None = None,
        pipeline_implicit_evidence_source: Any | None = None,
        pipeline_implicit_runtime_capability: (
            PipelineImplicitRuntimeCapability | None
        ) = None,
        ready_timeout: float = 360.0,
    ) -> None:
        """构造尚未启动的单任务环境。

        输入参数：
            repo_root：包含 canonical task 与 asset manifest 的仓库根目录。
            asset_cache_root：repo 外、已由 fetch 命令校验的资产缓存根目录。
            docker_session：只创建并清理自身容器 ID 的 session。
            controller：连接本次 loopback 映射端口的 OSWorld controller。
            task_prepare_source：可选测试替身；省略时默认装配
                生产版本化 task-specific prepare catalog。
            artifact_family_task_prepare_binding：CLI 在 Docker 前由 13-task
                asset draft 与严格 manifest 生成的脱敏运行时绑定；非该
                任务族必须为 ``None``。
            artifact_family_task_prepare_source：13-task 专属不可变动作
                source；省略时装配生产实现，测试可注入窄替身。
            bookmark_evidence_source：可选、受控的 Chrome Bookmarks
                空基线重置与评价快照 source。
            state_evidence_source：可选、受控的 Chrome profile/active-tab
                setup 与 evidence source；普通 QA/桌面任务可省略。
            artifact_finalizer：剩余 13-task 的版本化收尾边界；
                省略时装配生产实现，构造与 prepare 阶段不调用。
            artifact_evidence_source：可选、受控的单 VM artifact finalize、
                getter 与 metric source；构造时不读取结果或 gold。
            operation_artifact_source：可选、受控的 Operation
                完整 guest 文件树捕获 source；只在 Agent 结束后调用。
            pipeline_implicit_evidence_source：四个历史隐式任务的受控
                完整 bundle/typed observation source；只在评价阶段调用。
            pipeline_implicit_runtime_capability：CLI 在 VM 启动前形成的
                task/protocol/input/gold 机器身份；在 prepare 上传前
                重新绑定本次实际 input manifest。
            ready_timeout：QEMU guest agent-server 的最大就绪等待秒数。
        输出返回值：
            无；构造阶段不访问 Docker、网络或 guest。
        """

        if ready_timeout <= 0:
            raise ValueError("ready_timeout 必须大于零")
        self._repo_root = repo_root.resolve()
        self._asset_cache_root = asset_cache_root.resolve()
        self._docker_session = docker_session
        self.controller = controller
        self._task_prepare_source = (
            OSWorldTaskPrepareSource()
            if task_prepare_source is None
            else task_prepare_source
        )
        if artifact_family_task_prepare_binding is not None and not isinstance(
            artifact_family_task_prepare_binding,
            ArtifactFamilyTaskPrepareBinding,
        ):
            raise TypeError("artifact-family task prepare binding 类型无效")
        self._artifact_family_task_prepare_binding = (
            artifact_family_task_prepare_binding
        )
        self._artifact_family_task_prepare_source = (
            ArtifactFamilyTaskPrepareSource()
            if artifact_family_task_prepare_source is None
            else artifact_family_task_prepare_source
        )
        self._bookmark_evidence_source = bookmark_evidence_source
        self._state_evidence_source = state_evidence_source
        self._artifact_finalizer = (
            OSWorldArtifactFinalizer()
            if artifact_finalizer is None
            else artifact_finalizer
        )
        self._artifact_evidence_source = artifact_evidence_source
        self._operation_artifact_source = operation_artifact_source
        self._pipeline_implicit_evidence_source = pipeline_implicit_evidence_source
        if pipeline_implicit_runtime_capability is not None and not isinstance(
            pipeline_implicit_runtime_capability,
            PipelineImplicitRuntimeCapability,
        ):
            raise TypeError("pipeline-implicit runtime capability 类型无效")
        self._pipeline_implicit_runtime_capability = (
            pipeline_implicit_runtime_capability
        )
        self._ready_timeout = ready_timeout
        self._started = False
        self._prepared = False
        self._guest_shared_dir: str | None = None
        self._bookmark_observation_cache: dict[tuple[str, str], tuple[object, ...]] = {}
        self._state_observation_cache: dict[str, tuple[object, ...]] = {}
        self._artifact_observation_cache: dict[tuple[str, str], tuple[object, ...]] = {}
        self._artifact_finalize_attempts: set[tuple[str, str]] = set()
        self._artifact_observation_error_cache: dict[tuple[str, str], str] = {}
        self._operation_artifact_snapshot_cache: dict[
            tuple[str, str], OperationArtifactSnapshot
        ] = {}
        self._operation_word_text_baseline: WordTextBaseline | None = None
        self._operation_word_abbreviation_baseline: WordAbbreviationBaseline | None = (
            None
        )
        self._pipeline_implicit_observation_cache: dict[tuple[str, str], object] = {}
        self._prepared_task_id: str | None = None
        self._artifact_component_setup_task_id: str | None = None
        self._prepared_gold_manifest_reference: str | None = None
        self._owned_environment_closed = False

    @property
    def guest_shared_dir(self) -> str | None:
        """返回当前 guest 动态推导的 shared 目录。

        输入参数：
            无。
        输出返回值：
            ``prepare`` 成功后返回 POSIX 绝对路径，否则返回 ``None``。
        """

        return self._guest_shared_dir

    def start(self) -> None:
        """启动 owned Docker/KVM session 并等待 guest controller 就绪。

        输入参数：
            无。
        输出返回值：
            无；成功后环境可进入 ``prepare``。
        异常：
            OSWorldEnvironmentError：重复启动或 controller 未在期限内就绪。
        """

        if self._started:
            raise OSWorldEnvironmentError("OSWorld task environment 已启动")
        self._owned_environment_closed = False
        self._docker_session.start()
        self._started = True
        self.controller.wait_until_ready(timeout=self._ready_timeout)

    def prepare(self, task: Mapping[str, Any]) -> None:
        """按统一资产契约准备零资产或固定下载资产任务。

        输入参数：
            task：可信 canonical task；可以明确不需要任务文件，或声明仓库
                相对 ``asset_manifest``。
        输出返回值：
            无；零资产任务不访问 shared 目录；固定资产在 host
            与 guest 均通过 SHA-256 后，由 task-specific source 决定
            执行原始窗口准备，仅未命中专属规格时打开通用 shared
            Files 窗口。
        异常：
            OSWorldEnvironmentError：生命周期、路径、缓存、上传或 guest 摘要失败。
        """

        if not self._started:
            raise OSWorldEnvironmentError("环境未启动，不能准备 task")
        if self._prepared:
            raise OSWorldEnvironmentError("当前环境已经准备过 task")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise OSWorldEnvironmentError("OSWorld task_id 无效")
        try:
            task_assets = resolve_task_assets(self._repo_root, task)
        except (AssetManifestError, TypeError) as error:
            raise OSWorldEnvironmentError(str(error)) from error
        try:
            validate_pipeline_implicit_runtime_capability(
                repo_root=self._repo_root,
                task=task,
                task_assets=task_assets,
                capability=self._pipeline_implicit_runtime_capability,
            )
        except (
            AssetManifestError,
            PipelineImplicitRuntimeBlockedError,
            PipelineImplicitRuntimeManifestError,
            TypeError,
            ValueError,
        ):
            raise OSWorldEnvironmentError(
                "pipeline-implicit runtime binding 无效"
            ) from None
        try:
            artifact_family_binding = (
                validate_artifact_family_task_prepare_runtime_binding(
                    repo_root=self._repo_root,
                    task=task,
                    task_assets=task_assets,
                    binding=self._artifact_family_task_prepare_binding,
                )
            )
        except ArtifactFamilyTaskPrepareCapabilityError:
            raise OSWorldEnvironmentError(
                "artifact-family runtime binding 无效"
            ) from None
        if task_assets.mode is TaskAssetMode.NONE:
            self._prepare_bookmark_evidence(task)
            self._prepare_task_specific_setup(
                task,
                artifact_family_prepared_assets=None,
            )
            self._prepare_state_evidence(task)
            self._prepared_task_id = task_id
            self._prepared_gold_manifest_reference = _task_gold_manifest_reference(
                task,
            )
            self._prepared = True
            return
        manifest = task_assets.manifest
        if manifest is None:
            raise OSWorldEnvironmentError("固定资产模式缺少 manifest")
        _validate_cache_component(manifest.asset_set_id)
        cache_directory = self._asset_cache_root / manifest.asset_set_id
        verification = verify_asset_directory(manifest, cache_directory)
        if not verification.ok:
            raise OSWorldEnvironmentError("host 资产缓存未通过闭集大小与 SHA-256 校验")
        self._operation_word_text_baseline = self._capture_operation_word_text_baseline(
            task,
            manifest=manifest,
            cache_directory=cache_directory,
        )
        self._operation_word_abbreviation_baseline = (
            self._capture_operation_word_abbreviation_baseline(
                task,
                manifest=manifest,
                cache_directory=cache_directory,
            )
        )

        desktop_path = PurePosixPath(self.controller.get_desktop_path())
        guest_home = desktop_path.parent
        guest_shared = guest_home / "shared"
        self._guest_shared_dir = str(guest_shared)
        expected_paths: set[str] = set()
        for asset in manifest.files:
            local_path = cache_directory / asset.path
            guest_path = guest_shared / PurePosixPath(asset.path)
            self.controller.upload_file(local_path, str(guest_path))
            digest_result = self.controller.execute(
                ["sha256sum", "--", str(guest_path)]
            )
            observed_digest = str(digest_result.stdout).split(maxsplit=1)[0]
            if digest_result.returncode != 0 or observed_digest != asset.sha256:
                raise OSWorldEnvironmentError("guest 资产未通过上传后 SHA-256 校验")
            expected_paths.add(asset.path)

        _verify_guest_shared_closed_set(
            self.controller,
            guest_shared,
            expected_paths,
        )
        artifact_family_prepared_assets = None
        if artifact_family_binding is not None:
            artifact_family_prepared_assets = ArtifactFamilyPreparedAssets(
                task_id=task_id,
                verification_status="verified",
                input_draft_sha256=(artifact_family_binding.input_draft_sha256),
                manifest_sha256=(artifact_family_binding.asset_manifest_sha256),
                relative_paths=artifact_family_binding.relative_paths,
            )
        self._prepare_bookmark_evidence(task)
        task_specific_prepared = self._prepare_task_specific_setup(
            task,
            artifact_family_prepared_assets=(artifact_family_prepared_assets),
        )
        if not task_specific_prepared:
            self.controller.open_path(str(guest_shared))
        self._prepare_state_evidence(task)
        self._prepared_task_id = task_id
        self._prepared_gold_manifest_reference = _task_gold_manifest_reference(
            task,
        )
        self._prepared = True

    def osworld_bookmark_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """捕获并冻结当前 Attempt 的逐 VM Chrome Bookmarks 证据。

        输入参数：
            task_id：必须与本环境已经 prepare 的 canonical task ID 相同。
            protocol_id：必须精确等于版本化 Chrome Bookmarks 协议。
        输出返回值：
            source 返回的单 VM observation tuple；相同 task/protocol
            重复读取返回首次缓存对象，避免跨时点重新组合书签。
        异常：
            OSWorldEnvironmentError：生命周期、身份、协议、source 接口或
                捕获返回值无效。
        """

        if not self._started or not self._prepared:
            raise OSWorldEnvironmentError(
                "环境尚未完成 prepare，不能捕获 bookmark observation"
            )
        if (
            not isinstance(task_id, str)
            or not task_id
            or task_id != self._prepared_task_id
        ):
            raise OSWorldEnvironmentError("bookmark task_id 与已准备环境不一致")
        if protocol_id != CHROME_BOOKMARKS_PROTOCOL_ID:
            raise OSWorldEnvironmentError("bookmark protocol_id 不受支持")
        cache_key = (task_id, protocol_id)
        if cache_key in self._bookmark_observation_cache:
            return self._bookmark_observation_cache[cache_key]
        source = self._bookmark_evidence_source
        capture = getattr(source, "capture", None)
        if not callable(capture):
            raise OSWorldEnvironmentError("OSWorld bookmark evidence source 尚未装配")
        try:
            observations = capture(protocol_id, self.controller)
        except Exception as error:
            raise OSWorldEnvironmentError(
                "OSWorld bookmark evidence 捕获失败"
            ) from error
        if not isinstance(observations, tuple):
            raise OSWorldEnvironmentError("OSWorld bookmark evidence source 返回值无效")
        self._bookmark_observation_cache[cache_key] = observations
        return observations

    def osworld_state_observations(
        self,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """捕获并冻结当前 Attempt 的逐 VM OSWorld 状态证据。

        输入参数：
            protocol_id：runtime evaluator 已固定的版本化 profile/active-tab
                协议 ID。
        输出返回值：
            本单 VM environment 对应的 observation tuple；同一协议重复读取
            返回首次缓存对象，避免 URL 与 DOM 子指标跨时点漂移。
        异常：
            OSWorldEnvironmentError：环境未准备、source 未装配、协议无效或
                source 返回值不是 tuple。
        """

        if not self._started or not self._prepared:
            raise OSWorldEnvironmentError(
                "环境尚未完成 prepare，不能捕获 state observation"
            )
        if not isinstance(protocol_id, str) or not protocol_id:
            raise OSWorldEnvironmentError("state protocol_id 无效")
        if protocol_id in self._state_observation_cache:
            return self._state_observation_cache[protocol_id]
        source = self._state_evidence_source
        capture = getattr(source, "capture", None)
        if not callable(capture):
            raise OSWorldEnvironmentError("OSWorld state evidence source 尚未装配")
        try:
            observations = capture(protocol_id, self.controller)
        except Exception as error:
            raise OSWorldEnvironmentError("OSWorld state evidence 捕获失败") from error
        if not isinstance(observations, tuple):
            raise OSWorldEnvironmentError("OSWorld state evidence source 返回值无效")
        self._state_observation_cache[protocol_id] = observations
        return observations

    def osworld_artifact_state_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """捕获并冻结当前单 VM 的 artifact-state observation。

        输入参数：
            task_id：必须与本环境已经 prepare 的 canonical task ID 相同。
            protocol_id：runtime evaluator 固定的版本化 artifact-state 协议。
        输出返回值：
            仅含当前 VM 一个 observation 的 tuple；同一 task/protocol 重复
            读取返回首次缓存对象，禁止跨时点重新组合 artifact。
        异常：
            OSWorldEnvironmentError：生命周期、身份、source 接口或捕获失败。
        """

        if not self._started or not self._prepared:
            raise OSWorldEnvironmentError(
                "环境尚未完成 prepare，不能捕获 artifact observation"
            )
        if (
            not isinstance(task_id, str)
            or not task_id
            or task_id != self._prepared_task_id
        ):
            raise OSWorldEnvironmentError("artifact task_id 与已准备环境不一致")
        if protocol_id != ARTIFACT_STATE_PROTOCOL_ID:
            raise OSWorldEnvironmentError("artifact protocol_id 不受支持")
        cache_key = (task_id, protocol_id)
        if cache_key in self._artifact_observation_cache:
            return self._artifact_observation_cache[cache_key]
        cached_error = self._artifact_observation_error_cache.get(cache_key)
        if cached_error is not None:
            raise OSWorldEnvironmentError(cached_error) from None
        source = self._artifact_evidence_source
        capture = getattr(source, "capture", None)
        if not callable(capture):
            message = "OSWorld artifact evidence source 尚未装配"
            self._artifact_observation_error_cache[cache_key] = message
            raise OSWorldEnvironmentError(message) from None
        if (
            task_id in OSWORLD_ARTIFACT_FINALIZER_TASK_IDS
            and cache_key not in self._artifact_finalize_attempts
        ):
            self._artifact_finalize_attempts.add(cache_key)
            finalize = getattr(self._artifact_finalizer, "finalize", None)
            if not callable(finalize):
                message = "OSWorld artifact finalizer 尚未装配"
                self._artifact_observation_error_cache[cache_key] = message
                raise OSWorldEnvironmentError(message) from None
            try:
                finalized = finalize(
                    task_id,
                    self.controller,
                    guest_shared_dir=self._guest_shared_dir,
                )
            except Exception:
                message = "OSWorld artifact finalize 失败"
                self._artifact_observation_error_cache[cache_key] = message
                raise OSWorldEnvironmentError(message) from None
            if finalized is not True:
                message = "OSWorld artifact finalizer 返回值无效"
                self._artifact_observation_error_cache[cache_key] = message
                raise OSWorldEnvironmentError(message) from None
        try:
            observation = capture(
                task_id,
                self.controller,
                guest_shared_dir=self._guest_shared_dir,
            )
        except Exception:
            message = "OSWorld artifact evidence 捕获失败"
            self._artifact_observation_error_cache[cache_key] = message
            raise OSWorldEnvironmentError(message) from None
        observations = (observation,)
        self._artifact_observation_cache[cache_key] = observations
        return observations

    def osworld_artifact_component_validation_proof(
        self,
        task_id: str,
        protocol_id: str,
    ) -> OSWorldArtifactComponentEnvironmentProof:
        """在 owned 环境成功关闭后投影 setup/getter 生命周期事实。

        输入参数：task_id 必须属于可晋升的 12-task 闭集并与本实例已准备
            任务一致；protocol_id 必须为正式 artifact-state 协议。
        输出返回值：只含 task 身份和 setup/getter/close 三个布尔成功事实的
            不可变 proof；不含路径、artifact、gold、Agent 文本或异常细节。
        异常：OSWorldEnvironmentError：环境仍存活、关闭失败、专属 setup
            未成功，或同一 task/protocol 尚未完成一次缓存 capture。
        """

        cache_key = (task_id, protocol_id)
        observations = self._artifact_observation_cache.get(cache_key)
        evidence_source = self._artifact_evidence_source
        if (
            task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
            or task_id != self._prepared_task_id
            or protocol_id != OSWORLD_ARTIFACT_TASK_EVALUATION_PROTOCOL
            or self._artifact_component_setup_task_id != task_id
            or not self._prepared
            or self._started
            or not self._owned_environment_closed
            or not isinstance(observations, tuple)
            or len(observations) != 1
            or cache_key in self._artifact_observation_error_cache
            or type(self._docker_session) is not OSWorldAttestedDockerSession
            or type(self.controller) is not OSWorldController
            or not self.controller.uses_production_transport()
            or type(self._artifact_family_task_prepare_source)
            is not ArtifactFamilyTaskPrepareSource
            or type(self._artifact_finalizer) is not OSWorldArtifactFinalizer
            or type(evidence_source) is not OSWorldArtifactEvidenceSource
        ):
            raise OSWorldEnvironmentError("OSWorld artifact component 生命周期未闭合")
        try:
            image_manifest = load_osworld_image_manifest(
                self._repo_root / _OSWORLD_IMAGE_MANIFEST_RELATIVE_PATH,
            )
            if (
                not image_manifest.live_run_ready
                or image_manifest.extracted_sha256 is None
                or not self._docker_session.attests_closed_manifest(
                    container_image=image_manifest.container_image,
                    extracted_qcow2_sha256=image_manifest.extracted_sha256,
                )
            ):
                raise ValueError
            gold_reference = self._prepared_gold_manifest_reference
            if gold_reference != f"benchmark/gold/manifests/{task_id}.json":
                raise ValueError
            expected_gold_manifest = load_gold_asset_manifest(
                self._repo_root / PurePosixPath(gold_reference),
            )
            gold_proof = evidence_source.osworld_artifact_component_gold_proof(
                task_id,
                expected_manifest=expected_gold_manifest,
            )
            if gold_proof.task_id != task_id:
                raise ValueError
        except Exception:
            raise OSWorldEnvironmentError(
                "OSWorld artifact component gold 生命周期未闭合"
            ) from None
        return OSWorldArtifactComponentEnvironmentProof(
            task_id=task_id,
            task_setup_completed=True,
            artifact_getter_completed=True,
            evaluator_gold_completed=True,
            owned_environment_closed=True,
        )

    def _capture_operation_word_text_baseline(
        self,
        task: Mapping[str, Any],
        *,
        manifest: AssetManifest,
        cache_directory: Path,
    ) -> WordTextBaseline | None:
        """在 guest 可变更前构造 Word-009/010 typed input baseline。

        输入参数：
            task：已通过统一资产解析的 canonical 任务；manifest：
            已严格加载且 host cache 闭集验证成功的清单；
            cache_directory：尚未上传 guest 的固定 host 缓存根。
        输出返回值：
            非目标任务返回 ``None``；009/010 返回仅含脱敏
            typed 摘要的 baseline。manifest 原始字节和每个 input
            均通过 held-fd nofollow 稳定读重新绑定。
        异常：
            OSWorldEnvironmentError：manifest 竞态、身份漂移或 DOCX
            快照失败；错误不回显路径、文本或摘要。
        """

        task_id = task.get("task_id")
        if task_id not in _OPERATION_WORD_TEXT_TASK_IDS:
            return None
        manifest_reference = task.get("asset_manifest")
        if not isinstance(manifest_reference, str) or not manifest_reference:
            raise OSWorldEnvironmentError(
                "Operation Word typed baseline manifest 身份无效"
            )
        try:
            manifest_payload = read_manifest_bytes_nofollow(
                self._repo_root / PurePosixPath(manifest_reference)
            )
            formal_contract = operation_word_text_input_contract(task_id)
            manifest_identity = tuple(
                (asset.path, asset.size, asset.sha256) for asset in manifest.files
            )
            if (
                formal_contract is None
                or hashlib.sha256(manifest_payload).hexdigest()
                != formal_contract.manifest_sha256
                or manifest_reference != formal_contract.manifest_reference
                or manifest_identity
                != tuple(
                    (file.path, file.size, file.sha256)
                    for file in formal_contract.files
                )
                or load_asset_manifest_bytes(manifest_payload) != manifest
            ):
                raise WordTextFidelityError("WORD_TEXT_MANIFEST_IDENTITY_INVALID")
            files = tuple(
                WordTextInputFile(
                    path=asset.path,
                    size=asset.size,
                    sha256=asset.sha256,
                    is_docx=(PurePosixPath(asset.path).suffix.casefold() == ".docx"),
                )
                for asset in manifest.files
            )
            return capture_word_text_baseline(
                task_id=task_id,
                protocol_id=OPERATION_PROTOCOL_ID,
                manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
                source_root=cache_directory,
                files=files,
            )
        except (
            AssetManifestError,
            TypeError,
            ValueError,
            WordTextFidelityError,
        ):
            raise OSWorldEnvironmentError(
                "Operation Word typed baseline 构造失败"
            ) from None

    def operation_word_text_baseline(
        self,
        task_id: str,
        protocol_id: str,
    ) -> WordTextBaseline:
        """返回 prepare 前冻结的 Word-009/010 evaluator-only baseline。

        输入参数：
            task_id/protocol_id：runtime evaluator 固定的任务与 Operation 协议。
        输出返回值：
            当前 environment 在首次 guest 访问前构造的同一不可变 DTO。
        异常：
            OSWorldEnvironmentError：生命周期、task/protocol 身份或
            baseline 缺失；不从 post/guest 回退构造。
        """

        if not self._started or not self._prepared:
            raise OSWorldEnvironmentError(
                "环境尚未完成 prepare，不能读取 Word typed baseline"
            )
        baseline = self._operation_word_text_baseline
        if (
            task_id != self._prepared_task_id
            or task_id not in _OPERATION_WORD_TEXT_TASK_IDS
            or protocol_id != OPERATION_PROTOCOL_ID
            or baseline is None
            or baseline.task_id != task_id
            or baseline.protocol_id != protocol_id
        ):
            raise OSWorldEnvironmentError("Operation Word typed baseline 身份无效")
        return baseline

    def _capture_operation_word_abbreviation_baseline(
        self,
        task: Mapping[str, Any],
        *,
        manifest: AssetManifest,
        cache_directory: Path,
    ) -> WordAbbreviationBaseline | None:
        """在 guest 可变更前构造 Word-012 逐处语境 baseline。

        输入参数：
            task：已通过统一资产解析的 canonical 任务；manifest：
            host cache 闭集验证使用的严格清单；cache_directory：
            尚未上传 guest 的固定 host 根。
        输出返回值：
            非 Word-012 返回 ``None``；Word-012 返回仅含期望
            typed 快照的 evaluator-only DTO。
        异常：
            OSWorldEnvironmentError：manifest 竞态、固定身份或源语境
            漂移；错误不回显路径、正文、释义或摘要。
        """

        task_id = task.get("task_id")
        if task_id != _OPERATION_WORD_ABBREVIATION_TASK_ID:
            return None
        manifest_reference = task.get("asset_manifest")
        if not isinstance(manifest_reference, str) or not manifest_reference:
            raise OSWorldEnvironmentError(
                "Operation Word abbreviation baseline manifest 身份无效"
            )
        try:
            manifest_payload = read_manifest_bytes_nofollow(
                self._repo_root / PurePosixPath(manifest_reference)
            )
            formal_contract = operation_word_abbreviation_input_contract(task_id)
            manifest_identity = tuple(
                (asset.path, asset.size, asset.sha256) for asset in manifest.files
            )
            if (
                formal_contract is None
                or hashlib.sha256(manifest_payload).hexdigest()
                != formal_contract.manifest_sha256
                or manifest_reference != formal_contract.manifest_reference
                or manifest_identity
                != tuple(
                    (file.path, file.size, file.sha256)
                    for file in formal_contract.files
                )
                or load_asset_manifest_bytes(manifest_payload) != manifest
            ):
                raise WordAbbreviationError(
                    "WORD_ABBREVIATION_MANIFEST_IDENTITY_INVALID"
                )
            files = tuple(
                WordTextInputFile(
                    path=asset.path,
                    size=asset.size,
                    sha256=asset.sha256,
                    is_docx=(PurePosixPath(asset.path).suffix.casefold() == ".docx"),
                )
                for asset in manifest.files
            )
            return capture_word_abbreviation_baseline(
                task_id=task_id,
                protocol_id=OPERATION_PROTOCOL_ID,
                manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
                source_root=cache_directory,
                files=files,
            )
        except (
            AssetManifestError,
            TypeError,
            ValueError,
            WordAbbreviationError,
        ):
            raise OSWorldEnvironmentError(
                "Operation Word abbreviation typed baseline 构造失败"
            ) from None

    def operation_word_abbreviation_baseline(
        self,
        task_id: str,
        protocol_id: str,
    ) -> WordAbbreviationBaseline:
        """返回 prepare 前冻结的 Word-012 evaluator-only baseline。

        输入参数：
            task_id/protocol_id：runtime evaluator 固定的 Word-012 与
            Operation 协议身份。
        输出返回值：
            首次 guest 访问前构造的同一不可变 DTO。
        异常：
            OSWorldEnvironmentError：生命周期、task/protocol 身份或
            baseline 缺失；不得从 post/guest 回退构造。
        """

        if not self._started or not self._prepared:
            raise OSWorldEnvironmentError(
                "环境尚未完成 prepare，不能读取 abbreviation baseline"
            )
        baseline = self._operation_word_abbreviation_baseline
        if (
            task_id != self._prepared_task_id
            or task_id != _OPERATION_WORD_ABBREVIATION_TASK_ID
            or protocol_id != OPERATION_PROTOCOL_ID
            or baseline is None
            or baseline.task_id != task_id
            or baseline.protocol_id != protocol_id
        ):
            raise OSWorldEnvironmentError(
                "Operation Word abbreviation typed baseline 身份无效"
            )
        return baseline

    def operation_artifact_snapshot(
        self,
        task_id: str,
        protocol_id: str,
    ) -> OperationArtifactSnapshot:
        """捕获并冻结当前 Attempt 的 Operation 完整 artifact 树。

        输入参数：
            task_id：必须与当前 environment 已 prepare 的任务一致。
            protocol_id：必须精确等于版本化 Operation 协议。
        输出返回值：
            首次调用从 guest 捕获的 owned host 临时快照；同一
            task/protocol 重复读取返回同一对象，禁止跨时点重组。
        异常：
            OSWorldEnvironmentError：生命周期、身份、协议、source 接口、
                捕获或快照身份无效；错误不回显文件信息。
        """

        if not self._started or not self._prepared:
            raise OSWorldEnvironmentError(
                "环境尚未完成 prepare，不能捕获 Operation artifact"
            )
        if (
            not isinstance(task_id, str)
            or not task_id
            or task_id != self._prepared_task_id
        ):
            raise OSWorldEnvironmentError("Operation task_id 与已准备环境不一致")
        if protocol_id != OPERATION_PROTOCOL_ID:
            raise OSWorldEnvironmentError("Operation protocol_id 不受支持")
        cache_key = (task_id, protocol_id)
        if cache_key in self._operation_artifact_snapshot_cache:
            return self._operation_artifact_snapshot_cache[cache_key]
        source = self._operation_artifact_source
        capture = getattr(source, "capture", None)
        if not callable(capture):
            raise OSWorldEnvironmentError("OSWorld Operation artifact source 尚未装配")
        try:
            snapshot = capture(
                task_id,
                self.controller,
                guest_shared_dir=self._guest_shared_dir,
            )
        except Exception:
            raise OSWorldEnvironmentError(
                "OSWorld Operation artifact 捕获失败"
            ) from None
        if not isinstance(snapshot, OperationArtifactSnapshot):
            raise OSWorldEnvironmentError(
                "OSWorld Operation artifact source 返回值无效"
            )
        if snapshot.task_id != task_id or snapshot.protocol_id != protocol_id:
            snapshot.close()
            raise OSWorldEnvironmentError("OSWorld Operation artifact 快照身份无效")
        self._operation_artifact_snapshot_cache[cache_key] = snapshot
        return snapshot

    def pipeline_implicit_observation(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """捕获并冻结一个 pipeline-implicit evaluator-only observation。

        输入参数：
            task_id：必须与当前已 prepare 的四任务 canonical ID 相同。
            protocol_id：必须精确等于该任务唯一版本化评价协议。
        输出返回值：
            source 首次返回的同一 typed observation；相同身份重复读取
            不重新访问 guest，避免跨时点混合 bundle。
        异常：
            OSWorldEnvironmentError：生命周期、任务/协议、source 接口、
                capture 或 observation 身份不可信；错误不回显 artifact。
        """

        if not self._started or not self._prepared:
            raise OSWorldEnvironmentError(
                "环境尚未完成 prepare，不能捕获 pipeline-implicit observation"
            )
        expected_protocol = PIPELINE_IMPLICIT_TASK_PROTOCOLS.get(task_id)
        if (
            task_id != self._prepared_task_id
            or expected_protocol is None
            or protocol_id != expected_protocol
        ):
            raise OSWorldEnvironmentError(
                "pipeline-implicit task/protocol 与已准备环境不一致"
            )
        cache_key = (task_id, protocol_id)
        if cache_key in self._pipeline_implicit_observation_cache:
            return self._pipeline_implicit_observation_cache[cache_key]
        source = self._pipeline_implicit_evidence_source
        capture = getattr(source, "capture", None)
        if not callable(capture):
            raise OSWorldEnvironmentError("pipeline-implicit evidence source 尚未装配")
        try:
            observation = capture(
                task_id,
                self.controller,
                guest_shared_dir=self._guest_shared_dir,
            )
        except Exception:
            raise OSWorldEnvironmentError(
                "pipeline-implicit evidence 捕获失败"
            ) from None
        if observation is None:
            raise OSWorldEnvironmentError(
                "pipeline-implicit evidence source 返回值无效"
            )
        if isinstance(observation, PipelineImplicitArtifactObservation) and (
            observation.task_id != task_id
            or observation.protocol_id != protocol_id
            or observation.complete is not True
        ):
            raise OSWorldEnvironmentError("pipeline-implicit artifact bundle 身份无效")
        self._pipeline_implicit_observation_cache[cache_key] = observation
        return observation

    def close(self) -> None:
        """幂等清理本环境拥有的 Docker session。

        输入参数：
            无。
        输出返回值：
            无；从未启动或已清理时直接返回。
        """

        if (
            not self._started
            and not self._operation_artifact_snapshot_cache
            and not self._pipeline_implicit_observation_cache
            and self._operation_word_text_baseline is None
            and self._operation_word_abbreviation_baseline is None
        ):
            return
        docker_error: Exception | None = None
        if self._started:
            try:
                self._docker_session.close()
            except Exception as error:
                docker_error = error
        failed_snapshots: dict[tuple[str, str], OperationArtifactSnapshot] = {}
        for cache_key, snapshot in tuple(
            self._operation_artifact_snapshot_cache.items()
        ):
            try:
                snapshot.close()
            except Exception:
                failed_snapshots[cache_key] = snapshot
        self._operation_artifact_snapshot_cache.clear()
        self._operation_artifact_snapshot_cache.update(failed_snapshots)
        self._pipeline_implicit_observation_cache.clear()
        self._operation_word_text_baseline = None
        self._operation_word_abbreviation_baseline = None
        self._started = docker_error is not None
        if docker_error is not None:
            raise docker_error
        if failed_snapshots:
            raise OSWorldEnvironmentError(
                "OSWorld Operation artifact 快照清理失败"
            ) from None
        self._owned_environment_closed = True

    def _prepare_task_specific_setup(
        self,
        task: Mapping[str, Any],
        *,
        artifact_family_prepared_assets: (ArtifactFamilyPreparedAssets | None),
    ) -> bool:
        """在 guest 资产门禁后执行默认版本化任务准备。

        输入参数：
            task：已通过基础 task ID 与资产解析的 canonical task。
            artifact_family_prepared_assets：仅 13-task 在 host/guest
                双重闭集校验后构造的 verified DTO；其它任务为 ``None``。
        输出返回值：
            source 命中并完整执行规格时返回 ``True``；
            catalog 外任务返回 ``False``。调用方用此结果
            决定是否还需打开通用 shared Files 窗口。
        异常：
            OSWorldEnvironmentError：source 接口、返回类型或任一
                固定 setup 动作失败。错误不回显 guest 路径或 task
                payload。
        """

        task_id = task.get("task_id")
        is_artifact_family = (
            isinstance(task_id, str) and task_id in ARTIFACT_FAMILY_TASK_PREPARE_SPECS
        )
        if is_artifact_family:
            if artifact_family_prepared_assets is None:
                raise OSWorldEnvironmentError("artifact-family verified assets 缺失")
            source = self._artifact_family_task_prepare_source
        else:
            if artifact_family_prepared_assets is not None:
                raise OSWorldEnvironmentError(
                    "非 artifact-family task 不得携带 verified assets"
                )
            source = self._task_prepare_source
        prepare = getattr(source, "prepare", None)
        if not callable(prepare):
            raise OSWorldEnvironmentError(
                "OSWorld task prepare source 缺少 prepare 接口"
            )
        try:
            if is_artifact_family:
                prepared = prepare(
                    task,
                    self.controller,
                    guest_shared_dir=self._guest_shared_dir,
                    prepared_assets=artifact_family_prepared_assets,
                )
            else:
                prepared = prepare(
                    task,
                    self.controller,
                    guest_shared_dir=self._guest_shared_dir,
                )
        except Exception:
            raise OSWorldEnvironmentError("OSWorld task-specific setup 失败") from None
        if not isinstance(prepared, bool):
            raise OSWorldEnvironmentError("OSWorld task prepare source 返回值无效")
        if (
            is_artifact_family
            and prepared is True
            and task_id in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
        ):
            self._artifact_component_setup_task_id = task_id
        return prepared

    def _prepare_bookmark_evidence(self, task: Mapping[str, Any]) -> None:
        """在初始页面 setup 前执行可选书签空基线重置。

        输入参数：
            task：已通过 task ID 与资产解析的可信 canonical task；source
                自行判断是否属于固定 11 个 bookmark task。
        输出返回值：
            无；未装配 source 时保持非 bookmark 任务现有行为。
        异常：
            OSWorldEnvironmentError：source 接口无效或 reset 失败；错误不
                回显 guest 路径、URL、文件夹或 task payload。
        """

        source = self._bookmark_evidence_source
        if source is None:
            return
        prepare = getattr(source, "prepare", None)
        if not callable(prepare):
            raise OSWorldEnvironmentError(
                "OSWorld bookmark evidence source 缺少 prepare 接口"
            )
        try:
            prepare(task, self.controller)
        except Exception:
            raise OSWorldEnvironmentError(
                "OSWorld bookmark baseline reset 失败"
            ) from None

    def _prepare_state_evidence(self, task: Mapping[str, Any]) -> None:
        """在 guest ready/资产准备后执行可选的版本化状态 setup。

        输入参数：
            task：可信 canonical task；source 自行判断是否属于 state mode。
        输出返回值：
            无；未装配 source 时保持普通任务现有行为。
        异常：
            OSWorldEnvironmentError：source 接口无效或 setup 失败。
        """

        source = self._state_evidence_source
        if source is None:
            return
        prepare = getattr(source, "prepare", None)
        if not callable(prepare):
            raise OSWorldEnvironmentError(
                "OSWorld state evidence source 缺少 prepare 接口"
            )
        try:
            prepare(task, self.controller)
        except Exception as error:
            raise OSWorldEnvironmentError("OSWorld state setup 失败") from error


def _task_gold_manifest_reference(task: Mapping[str, Any]) -> str | None:
    """提取不解析正文的 canonical gold manifest 相对引用。

    输入参数：task 为已由统一资产解析和 runtime prepare 接收的可信任务。
    输出返回值：值为规范 POSIX 相对路径时原样返回；缺失或任何绝对、点段、
        反斜杠形式返回 ``None``，供 candidate proof 后续失败关闭。
    """

    value = task.get("gold_manifest")
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or candidate.as_posix() != value
    ):
        return None
    return value


def _validate_cache_component(asset_set_id: str) -> None:
    """验证 asset_set_id 可安全作为单层缓存目录名。

    输入参数：
        asset_set_id：asset manifest 的稳定集合标识。
    输出返回值：
        无；安全标识正常返回。
    异常：
        OSWorldEnvironmentError：值为空、含路径分隔或特殊目录语义。
    """

    if (
        not asset_set_id
        or "/" in asset_set_id
        or "\\" in asset_set_id
        or asset_set_id in {".", ".."}
    ):
        raise OSWorldEnvironmentError("asset_set_id 不能作为安全缓存目录")


def _verify_guest_shared_closed_set(
    controller: Any,
    guest_shared: PurePosixPath,
    expected_file_paths: set[str],
) -> None:
    """枚举 guest shared 全部节点并验证严格的类型化闭集。

    输入参数：
        controller：当前 owned OSWorld session 的 shell-free controller。
        guest_shared：已由 guest Desktop 动态推导的 shared 绝对路径。
        expected_file_paths：manifest 声明且已上传、逐文件验 SHA 的相对路径。
    输出返回值：
        无；仅当全部声明项为普通文件、除必要普通祖先目录外不存在任何
        文件、目录、符号链接或特殊节点时正常返回。
    异常：
        OSWorldEnvironmentError：guest 枚举失败、输出协议无效，或类型化
        成员集合与 manifest 闭集不一致。错误不回显 guest 路径或文件名。
    """

    listing_result = controller.execute(
        [
            "find",
            "-P",
            str(guest_shared),
            "-xdev",
            "-mindepth",
            "1",
            "-printf",
            "%y\t%P\n",
        ]
    )
    if listing_result.returncode != 0:
        raise OSWorldEnvironmentError("guest 无法枚举 shared 资产")
    observed_entries = _parse_guest_shared_entries(listing_result.stdout)
    expected_entries = _expected_guest_shared_entries(expected_file_paths)
    if observed_entries != expected_entries:
        raise OSWorldEnvironmentError("guest shared 目录不满足资产闭集契约")


def _expected_guest_shared_entries(
    expected_file_paths: set[str],
) -> set[tuple[str, str]]:
    """把 manifest 文件路径扩展为允许的普通文件与祖先目录闭集。

    输入参数：
        expected_file_paths：manifest 中的 POSIX 相对文件路径集合。
    输出返回值：
        ``(find 类型, 相对路径)`` 集合；文件类型固定为 ``f``，仅文件的
        必要祖先目录以 ``d`` 纳入，shared 根自身不纳入。
    """

    expected_entries: set[tuple[str, str]] = set()
    for file_path in expected_file_paths:
        expected_entries.add(("f", file_path))
        for parent in PurePosixPath(file_path).parents:
            if parent != PurePosixPath("."):
                expected_entries.add(("d", parent.as_posix()))
    return expected_entries


def _parse_guest_shared_entries(stdout: Any) -> set[tuple[str, str]]:
    """解析固定 GNU ``find %y\\t%P`` 输出且拒绝歧义记录。

    输入参数：
        stdout：controller 返回的 guest 标准输出；协议要求为字符串。
    输出返回值：
        每个 guest 节点的单字符类型与 POSIX 相对路径集合。符号链接和
        特殊节点保留其原始类型，随后由闭集比较统一拒绝。
    异常：
        OSWorldEnvironmentError：输出不是字符串、记录缺字段、类型字段
        不是单字符、路径为空/绝对/穿越/含反斜杠，或记录重复。
    """

    if not isinstance(stdout, str):
        raise OSWorldEnvironmentError("guest shared 枚举协议无效")
    if not stdout:
        return set()
    entries: list[tuple[str, str]] = []
    for record in stdout.splitlines():
        entry_type, separator, relative_path = record.partition("\t")
        path = PurePosixPath(relative_path)
        if (
            separator != "\t"
            or len(entry_type) != 1
            or not relative_path
            or "\\" in relative_path
            or path.is_absolute()
            or "." in path.parts
            or ".." in path.parts
        ):
            raise OSWorldEnvironmentError("guest shared 枚举协议无效")
        entries.append((entry_type, relative_path))
    unique_entries = set(entries)
    if len(unique_entries) != len(entries):
        raise OSWorldEnvironmentError("guest shared 枚举协议无效")
    return unique_entries
