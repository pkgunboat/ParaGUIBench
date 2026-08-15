"""artifact-family prepare binding 与 OSWorld environment 的边界测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.osworld import (
    ARTIFACT_STATE_PROTOCOL_ID,
    OSWORLD_ARTIFACT_STATE_TASK_RULES,
    evaluate_artifact_state_observations,
)
from paraguibench.integrations.osworld.artifact_contracts import (
    ArtifactSlotObservation,
    ArtifactStateObservation,
)
from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
    ArtifactFamilyPreparedAssets,
)
from paraguibench.integrations.osworld.artifact_finalizer import (
    OSWORLD_ARTIFACT_FINALIZER_ACTIONS,
)
from paraguibench.runtime.artifact_family_task_prepare import (
    ArtifactFamilyTaskPrepareBinding,
)
from paraguibench.runstore import (
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
    RunVersionVector,
)
from paraguibench.runtime.attempt_runner import AttemptRunner, RuntimeEvaluation
from paraguibench.runtime.osworld_environment import (
    OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS,
    OSWorldEnvironmentError,
    OSWorldTaskEnvironment,
)
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
    OSWORLD_ARTIFACT_ENVIRONMENT_PROTOCOL,
)
from paraguibench.runtime.osworld_artifact_component_validation import (
    OSWorldArtifactComponentValidationError,
    _run_osworld_artifact_component_validation,
    _validate_candidate_task_evaluation,
    run_osworld_artifact_component_validation,
)
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


TASK_ID = "Operation-FileOperate-BatchOperation-003"


def test_runtime_finalize_capability_matches_the_non_none_action_catalog() -> None:
    """验证 manifest 只能提升已接入 lifecycle 的十个非空动作。

    输入参数：
        无；比较 runtime capability 与 integration 固定动作目录。
    输出返回值：
        无；断言十项精确相等，三个 ``none`` 不被误算为 finalize 接线。
    """

    expected = frozenset(
        task_id
        for task_id, action_id in OSWORLD_ARTIFACT_FINALIZER_ACTIONS.items()
        if action_id != "none"
    )

    assert OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS == expected
    assert len(OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS) == 10


class _DockerSession:
    """记录合成 Docker session 生命周期。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用序列。

        输入参数：
            calls：测试断言使用的有序阶段列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def start(self) -> None:
        """记录 session 启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("docker.start")

    def close(self) -> None:
        """记录 session 清理。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("docker.close")


class _Controller:
    """模拟同一 guest 中的上传、摘要与闭集枚举。"""

    def __init__(self, calls: list[str]) -> None:
        """初始化有序调用和 guest 内存文件表。

        输入参数：
            calls：测试共享调用序列。
        输出返回值：无。
        """

        self.calls = calls
        self.files: dict[str, bytes] = {}

    def wait_until_ready(self, *, timeout: float) -> None:
        """记录 guest ready 等待。

        输入参数：
            timeout：environment 固定的正等待上限。
        输出返回值：无。
        """

        assert timeout > 0
        self.calls.append("controller.ready")

    def get_desktop_path(self) -> str:
        """返回合成 guest Desktop 路径。

        输入参数：无。
        输出返回值：规范 guest 绝对路径。
        """

        self.calls.append("controller.desktop")
        return "/guest-home/Desktop"

    def upload_file(self, local_path: Path, guest_path: str) -> None:
        """把已验证 host bytes 放入内存 guest shared 表。

        输入参数：
            local_path：严格 host cache 文件。
            guest_path：environment 推导的 guest shared 目标。
        输出返回值：无。
        """

        self.files[guest_path] = local_path.read_bytes()
        self.calls.append("controller.upload")

    def execute(self, command: list[str]) -> Any:
        """模拟 sha256sum 与 find 两个 environment 门禁 argv。

        输入参数：
            command：environment 生成的 shell-free argv。
        输出返回值：
            带 returncode/stdout 的合成执行结果。
        """

        class _Result:
            """保存合成命令状态。"""

            returncode = 0
            stdout = ""

        result = _Result()
        if command[0] == "sha256sum":
            guest_path = command[-1]
            result.stdout = hashlib.sha256(self.files[guest_path]).hexdigest()
        elif command[0] == "find":
            result.stdout = "\n".join(
                f"f\t{guest_path.rsplit('/', 1)[-1]}"
                for guest_path in sorted(self.files)
            )
        else:
            raise AssertionError("environment 不应执行其它 guest argv")
        self.calls.append(f"controller.execute:{command[0]}")
        return result

    def execute_with_timeout(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> Any:
        """记录 production finalizer 的单次 shell-free guest 命令。

        输入参数：
            command：从冻结 artifact finalize spec 构造的 argv。
            timeout_seconds：有限且严格为正的动作超时。
        输出返回值：
            具有严格零返回码和字符串输出字段的合成结果。
        """

        class _Result:
            """保存 finalizer 需要的最小结构化成功结果。"""

            returncode = 0
            stdout = ""
            stderr = ""

        assert command[:3] == ["python3", "-I", "-c"]
        assert 0.0 < timeout_seconds <= 300.0
        self.calls.append("artifact-family.finalize")
        return _Result()

    def open_path(self, _guest_path: str) -> None:
        """标记 artifact-family source 命中后不应使用通用 Files fallback。

        输入参数：
            _guest_path：未读取的 guest 路径。
        输出返回值：
            不返回；调用即使测试失败。
        """

        raise AssertionError("artifact-family prepare 后不得打开通用 shared")


class _ArtifactFamilySource:
    """记录 environment 传入生产 source 边界的 verified DTO。"""

    def __init__(self, calls: list[str]) -> None:
        """初始化尚未观察的 DTO。

        输入参数：
            calls：测试共享调用序列。
        输出返回值：无。
        """

        self.calls = calls
        self.prepared_assets: ArtifactFamilyPreparedAssets | None = None

    def prepare(
        self,
        task: dict[str, Any],
        controller: Any,
        *,
        guest_shared_dir: str | None,
        prepared_assets: ArtifactFamilyPreparedAssets,
    ) -> bool:
        """记录 verified DTO 并报告专属 prepare 已处理。

        输入参数：
            task：可信 canonical task。
            controller：同一已验证 guest controller。
            guest_shared_dir：冻结的 shared 路径。
            prepared_assets：environment 在 host/guest 双重校验后构造的 DTO。
        输出返回值：
            恒为 ``True``，避免通用 Files fallback。
        """

        assert task["task_id"] == TASK_ID
        assert isinstance(controller, _Controller)
        assert guest_shared_dir == "/guest-home/shared"
        self.prepared_assets = prepared_assets
        self.calls.append("artifact-family.prepare")
        return True


class _ArtifactEvidenceSource:
    """记录 finalizer 之后的单 VM artifact capture。"""

    def __init__(self, calls: list[str]) -> None:
        """构造唯一合成 observation 并保存共享顺序记录。

        输入参数：
            calls：测试使用的全生命周期顺序列表。
        输出返回值：
            无；实例保存一个不透明 observation。
        """

        self.calls = calls
        self.observation = object()

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> object:
        """验证同一冻结 guest 绑定并返回 observation。

        输入参数：
            task_id：当前已 prepare 的 artifact-family 任务。
            controller：同一 OSWorld guest controller。
            guest_shared_dir：prepare 阶段冻结的 shared 绝对路径。
        输出返回值：
            构造时创建的唯一不透明 observation。
        """

        assert task_id == TASK_ID
        assert isinstance(controller, _Controller)
        assert guest_shared_dir == "/guest-home/shared"
        self.calls.append("artifact-family.capture")
        return self.observation


class _CandidateArtifactEvidenceSource:
    """返回“getter 正常但候选输出缺失”的正式 typed observation。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享生命周期调用记录。

        输入参数：calls 为 candidate 测试观察的有序阶段列表。
        输出返回值：无。
        """

        self.calls = calls

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> ArtifactStateObservation:
        """构造完整槽位闭集且状态为 missing 的安全 observation。

        输入参数：task_id/controller/guest_shared_dir 必须绑定当前真实
            ``OSWorldTaskEnvironment`` 实例；不读取或返回 artifact 正文。
        输出返回值：可由正式纯 evaluator 可靠判为任务未完成、但不存在
            getter/schema/read 错误的 typed observation。
        """

        assert task_id == TASK_ID
        assert isinstance(controller, _Controller)
        assert guest_shared_dir == "/guest-home/shared"
        self.calls.append("artifact-family.capture")
        rule = OSWORLD_ARTIFACT_STATE_TASK_RULES[task_id]
        return ArtifactStateObservation(
            rule_id=rule.rule_id,
            source_contract_sha256=rule.source_contract_sha256,
            evidence_spec_sha256=rule.evidence_spec_sha256,
            artifact_slots=tuple(
                ArtifactSlotObservation(
                    slot_id=slot.slot_id,
                    status="missing",
                )
                for slot in rule.artifact_slots
            ),
        )


class _FailingArtifactEvidenceSource(_ArtifactEvidenceSource):
    """模拟 finalizer 成功后 artifact getter 失败的证据边界。"""

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> object:
        """记录唯一 capture 尝试并抛出含敏感值的合成错误。

        输入参数：
            task_id/controller/guest_shared_dir：与成功 source 相同的评价边界。
        输出返回值：
            无；始终抛出合成 I/O 异常。
        """

        assert task_id == TASK_ID
        assert isinstance(controller, _Controller)
        assert guest_shared_dir == "/guest-home/shared"
        self.calls.append("artifact-family.capture")
        raise OSError("PRIVATE_GUEST_PATH PRIVATE_STDERR")


class _ConfigurableArtifactFinalizer:
    """模拟可成功、返回非法值或抛敏感异常的 finalizer 边界。"""

    def __init__(
        self,
        calls: list[str],
        *,
        result: object = True,
        error: Exception | None = None,
    ) -> None:
        """保存共享顺序记录与固定终态。

        输入参数：
            calls：全生命周期有序调用记录。
            result：未抛错时返回给 environment 的值。
            error：可选的合成 finalizer 异常。
        输出返回值：
            无；构造阶段不发生 guest I/O。
        """

        self.calls = calls
        self.result = result
        self.error = error

    def finalize(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> object:
        """记录单次收尾并返回或抛出构造时固定的终态。

        输入参数：
            task_id：environment 已 prepare 的 canonical 任务。
            controller：同一 OSWorld guest controller。
            guest_shared_dir：prepare 阶段冻结的 shared 绝对路径。
        输出返回值：
            构造时提供的 ``result``，用于覆盖成功与非法返回值。
        异常：
            Exception：构造时提供 ``error`` 时原样抛出，仅供脱敏测试。
        """

        assert task_id == TASK_ID
        assert isinstance(controller, _Controller)
        assert guest_shared_dir == "/guest-home/shared"
        self.calls.append("artifact-finalizer.call")
        if self.error is not None:
            raise self.error
        return self.result


class _GenericArtifactEvidenceSource:
    """为 catalog 外 artifact task 返回一个不透明 observation。"""

    def __init__(self, calls: list[str]) -> None:
        """保存顺序记录与唯一 observation。

        输入参数：
            calls：环境测试共享的有序调用列表。
        输出返回值：
            无；构造不读取 guest。
        """

        self.calls = calls
        self.observation = object()

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> object:
        """记录 capture 且不要求任务属于 13-task family。

        输入参数：
            task_id：当前 prepare 的 canonical artifact task。
            controller：同一 guest controller。
            guest_shared_dir：零资产测试中固定为 ``None``。
        输出返回值：
            构造时创建的唯一不透明 observation。
        """

        assert task_id == "Operation-FileOperate-BatchOperation-001"
        assert isinstance(controller, _Controller)
        assert guest_shared_dir is None
        self.calls.append("generic-artifact.capture")
        return self.observation


class _LifecycleAgent:
    """记录 Agent 完成并返回不得作为 artifact 证据的敏感文本。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享生命周期记录。

        输入参数：
            calls：AttemptRunner 与 environment 共用的有序调用列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def run(
        self,
        task_view: dict[str, Any],
        environment: object,
    ) -> AgentRunResult:
        """记录 Agent 完成并返回合规终态。

        输入参数：
            task_view：仅含 task ID 与 instruction 的安全投影。
            environment：当前仍存活的任务环境，本 fake 不调用。
        输出返回值：
            含敏感 final text 的一步完成结果。
        """

        del environment
        assert task_view["task_id"] == TASK_ID
        self.calls.append("agent.run")
        return AgentRunResult(
            final_output="PRIVATE_AGENT_FINAL_TEXT",
            step_count=1,
            termination="finished",
        )


class _LifecycleArtifactEvaluator:
    """在 Agent 完成后请求真实 environment artifact 生命周期。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享生命周期记录。

        输入参数：
            calls：AttemptRunner 与 environment 共用的有序调用列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def evaluate(
        self,
        task: dict[str, Any],
        final_output: str,
        environment: OSWorldTaskEnvironment,
    ) -> RuntimeEvaluation:
        """忽略 Agent 文本并从环境请求 artifact observation。

        输入参数：
            task：可信 canonical task。
            final_output：Agent terminal text，仅验证后立即丢弃。
            environment：已 prepare 且仍存活的真实 OSWorld environment。
        输出返回值：
            observation 可用时返回满分；本测试由 finalizer 先抛固定错误。
        """

        assert task["task_id"] == TASK_ID
        assert final_output == "PRIVATE_AGENT_FINAL_TEXT"
        self.calls.append("evaluator.evaluate")
        environment.osworld_artifact_state_observations(
            TASK_ID,
            ARTIFACT_STATE_PROTOCOL_ID,
        )
        return RuntimeEvaluation(passed=True, score=1.0, details={})


def _build_runtime_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, dict[str, Any], ArtifactFamilyTaskPrepareBinding]:
    """构造单文件严格 manifest、完整 cache、task 与可信绑定。

    输入参数：
        tmp_path：pytest 提供的隔离根目录。
    输出返回值：
        repo 根、cache 根、合成 canonical task 和运行时绑定。
    """

    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    task_cache = cache_root / TASK_ID
    task_cache.mkdir(parents=True)
    asset_content = b"synthetic verified archive"
    (task_cache / "raw_book.zip").write_bytes(asset_content)
    manifest_path = manifest_root / f"{TASK_ID}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": TASK_ID,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/artifact-family",
                    "revision": "a" * 40,
                    "base_path": "dataset/task",
                    "license_status": "apache-2.0",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "raw_book.zip",
                        "size": len(asset_content),
                        "sha256": hashlib.sha256(asset_content).hexdigest(),
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest_bytes = manifest_path.read_bytes()
    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[TASK_ID]
    binding = ArtifactFamilyTaskPrepareBinding(
        task_id=TASK_ID,
        input_draft_sha256=spec.input_draft_sha256,
        asset_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        relative_paths=("raw_book.zip",),
    )
    task = {
        "task_id": TASK_ID,
        "prepare_script_path": "",
        "asset_manifest": str(manifest_path.relative_to(repo_root)),
    }
    return repo_root, cache_root, task, binding


def test_environment_routes_verified_assets_to_artifact_family_source(
    tmp_path: Path,
) -> None:
    """验证 host/guest 闭集通过后才执行专属 source，且 DTO 身份精确。

    输入参数：
        tmp_path：pytest 提供的严格 manifest 与 cache 隔离根。
    输出返回值：
        无；调用顺序固定为 ready→host/guest 校验→source，不能回退
        Files。
    """

    repo_root, cache_root, task, binding = _build_runtime_fixture(tmp_path)
    calls: list[str] = []
    source = _ArtifactFamilySource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=source,
    )

    environment.start()
    environment.prepare(task)
    environment.close()

    assert source.prepared_assets == ArtifactFamilyPreparedAssets(
        task_id=TASK_ID,
        verification_status="verified",
        input_draft_sha256=binding.input_draft_sha256,
        manifest_sha256=binding.asset_manifest_sha256,
        relative_paths=binding.relative_paths,
    )
    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.desktop",
        "controller.upload",
        "controller.execute:sha256sum",
        "controller.execute:find",
        "artifact-family.prepare",
        "docker.close",
    ]


def test_environment_finalizes_once_after_prepare_and_before_cached_capture(
    tmp_path: Path,
) -> None:
    """验证 artifact-family 收尾只在评价阶段执行一次。

    输入参数：
        tmp_path：pytest 提供的严格 manifest、cache 与 repo 隔离根。
    输出返回值：
        无；断言 prepare 不会提前收尾，Agent 后的首次 observation
        严格按 finalize→capture 执行，重复读取返回同一 tuple。
    """

    repo_root, cache_root, task, binding = _build_runtime_fixture(tmp_path)
    calls: list[str] = []
    prepare_source = _ArtifactFamilySource(calls)
    evidence_source = _ArtifactEvidenceSource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=prepare_source,
        artifact_evidence_source=evidence_source,
    )

    environment.start()
    environment.prepare(task)
    assert "artifact-family.finalize" not in calls
    first = environment.osworld_artifact_state_observations(
        TASK_ID,
        ARTIFACT_STATE_PROTOCOL_ID,
    )
    second = environment.osworld_artifact_state_observations(
        TASK_ID,
        ARTIFACT_STATE_PROTOCOL_ID,
    )
    environment.close()

    assert first is second
    assert first == (evidence_source.observation,)
    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.desktop",
        "controller.upload",
        "controller.execute:sha256sum",
        "controller.execute:find",
        "artifact-family.prepare",
        "artifact-family.finalize",
        "artifact-family.capture",
        "docker.close",
    ]


def test_component_candidate_proof_rejects_injected_sources_after_owned_close(
    tmp_path: Path,
) -> None:
    """验证完整外层生命周期仍不能给注入 source 颁发 proof。

    输入参数：tmp_path 提供严格 manifest/cache/repo 隔离根。
    输出返回值：prepare、capture 与 owned close 即使完成，非精确生产
        Docker/controller/prepare/evidence/gold 依赖仍固定失败。
    """

    repo_root, cache_root, task, binding = _build_runtime_fixture(tmp_path)
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=_ArtifactFamilySource(calls),
        artifact_evidence_source=_ArtifactEvidenceSource(calls),
    )

    environment.start()
    environment.prepare(task)
    environment.osworld_artifact_state_observations(
        TASK_ID,
        ARTIFACT_STATE_PROTOCOL_ID,
    )
    with pytest.raises(OSWorldEnvironmentError):
        environment.osworld_artifact_component_validation_proof(
            TASK_ID,
            ARTIFACT_STATE_PROTOCOL_ID,
        )
    environment.close()

    with pytest.raises(OSWorldEnvironmentError):
        environment.osworld_artifact_component_validation_proof(
            TASK_ID,
            ARTIFACT_STATE_PROTOCOL_ID,
        )
    assert calls[-4:] == [
        "artifact-family.prepare",
        "artifact-family.finalize",
        "artifact-family.capture",
        "docker.close",
    ]
    assert "agent.run" not in calls


def test_component_validation_rejects_injected_setup_getter_and_gold_sources(
    tmp_path: Path,
) -> None:
    """验证 typed missing observation 不能伪造三个生产组件通过。

    输入参数：tmp_path 提供真实 environment 合成依赖与 RunStore 隔离根。
    输出返回值：即使外层类型是精确 OSWorldTaskEnvironment，注入的
        prepare/evidence source 没有执行真实 setup/controller getter/gold
        resolver/projection，candidate 仍固定失败且不产生 receipt。
    """

    fixture_root = tmp_path / "fixture"
    repo_root, cache_root, task, binding = _build_runtime_fixture(fixture_root)
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=_ArtifactFamilySource(calls),
        artifact_evidence_source=_CandidateArtifactEvidenceSource(calls),
    )
    prepared_task = PreparedTask(
        trusted_task=task,
        agent_task={"task_id": TASK_ID, "instruction": "not used"},
        audit_metadata=synthetic_task_audit(TASK_ID),
    )
    revision = "tree-sha256:" + "7" * 64
    vector = RunVersionVector(
        source_revision=revision,
        agent_code_revision=revision,
        evaluator_revision=revision,
        evaluation_protocol=OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_PROTOCOL,
        environment_protocol=OSWORLD_ARTIFACT_ENVIRONMENT_PROTOCOL,
        environment_revision="manifest-sha256:" + "8" * 64,
    )
    store = RunStore(tmp_path / "runs")
    store.start_run(
        run_id="run-component-candidate",
        run_record={"candidate_kind": "osworld-artifact-component"},
        version_vector=vector,
    )
    attempt = store.start_attempt(
        run_id="run-component-candidate",
        task_id=TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared_task.audit_metadata,
    )

    with pytest.raises(OSWorldArtifactComponentValidationError):
        run_osworld_artifact_component_validation(
            store=store,
            attempt=attempt,
            prepared_task=prepared_task,
            environment=environment,
        )
    with pytest.raises(OSWorldArtifactComponentValidationError):
        _run_osworld_artifact_component_validation(
            store=store,
            attempt=attempt,
            prepared_task=prepared_task,
            environment=environment,
            _candidate_capability=object(),
        )

    assert calls == []
    persisted = b"\n".join(
        path.read_bytes() for path in (tmp_path / "runs").rglob("*") if path.is_file()
    )
    assert b"final_output" not in persisted
    assert not any(
        part in {"workers", "planners"}
        for path in (tmp_path / "runs").rglob("*")
        for part in path.parts
    )


def test_component_task_evaluation_accepts_consistent_zero_score_as_component_fact() -> (
    None
):
    """验证任务零分不与 component 协议的完成事实混淆。

    输入参数：无；用正式 pure evaluator 评价闭集且全部
        missing 的 typed observation。
    输出返回：结构与原因计数一致的 0.0 任务得分可被
        结构验证；它本身不会产生 component proof 或 receipt。
    """

    calls: list[str] = []
    observation = _CandidateArtifactEvidenceSource(calls).capture(
        TASK_ID,
        _Controller(calls),
        guest_shared_dir="/guest-home/shared",
    )
    evaluation = evaluate_artifact_state_observations(TASK_ID, (observation,))

    assert evaluation.passed is False
    assert _validate_candidate_task_evaluation(TASK_ID, evaluation) == 0.0


def test_capture_failure_is_cached_without_repeating_finalize_or_guest_io(
    tmp_path: Path,
) -> None:
    """验证 finalizer 后的 capture 失败也会冻结为脱敏终态。

    输入参数：
        tmp_path：pytest 提供的严格 artifact-family runtime fixture。
    输出返回值：
        无；连续两次读取均得到固定脱敏错误，而 finalizer
        和 getter 各至多调用一次。
    """

    repo_root, cache_root, task, binding = _build_runtime_fixture(tmp_path)
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=_ArtifactFamilySource(calls),
        artifact_evidence_source=_FailingArtifactEvidenceSource(calls),
    )
    environment.start()
    environment.prepare(task)

    observed_errors: list[str] = []
    for _ in range(2):
        with pytest.raises(OSWorldEnvironmentError) as caught:
            environment.osworld_artifact_state_observations(
                TASK_ID,
                ARTIFACT_STATE_PROTOCOL_ID,
            )
        observed_errors.append(str(caught.value))
    environment.close()

    assert observed_errors == [
        "OSWorld artifact evidence 捕获失败",
        "OSWorld artifact evidence 捕获失败",
    ]
    assert calls.count("artifact-family.finalize") == 1
    assert calls.count("artifact-family.capture") == 1
    assert "PRIVATE" not in "".join(observed_errors)


def test_missing_capture_source_is_rejected_before_finalize_side_effect(
    tmp_path: Path,
) -> None:
    """验证缺少 evidence source 时不先执行不可逆 finalizer。

    输入参数：
        tmp_path：pytest 提供的严格 artifact-family runtime fixture。
    输出返回值：
        无；断言 source 能力在任何 finalize guest I/O 之前失败关闭。
    """

    repo_root, cache_root, task, binding = _build_runtime_fixture(tmp_path)
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=_ArtifactFamilySource(calls),
    )
    environment.start()
    environment.prepare(task)

    with pytest.raises(
        OSWorldEnvironmentError,
        match="^OSWorld artifact evidence source 尚未装配$",
    ):
        environment.osworld_artifact_state_observations(
            TASK_ID,
            ARTIFACT_STATE_PROTOCOL_ID,
        )
    environment.close()

    assert "artifact-family.finalize" not in calls


@pytest.mark.parametrize(
    ("finalizer_result", "finalizer_error", "expected_message"),
    (
        (
            True,
            RuntimeError("PRIVATE_ARGV PRIVATE_WINDOW PRIVATE_STDERR"),
            "OSWorld artifact finalize 失败",
        ),
        (False, None, "OSWorld artifact finalizer 返回值无效"),
    ),
)
def test_finalizer_failure_is_cached_and_redacted_before_capture(
    tmp_path: Path,
    finalizer_result: object,
    finalizer_error: Exception | None,
    expected_message: str,
) -> None:
    """验证 finalizer 失败冻结为一次性、固定且脱敏的终态。

    输入参数：
        tmp_path：pytest 提供的严格 artifact-family runtime fixture。
        finalizer_result：成功路径上的合成返回值。
        finalizer_error：可选、含敏感内容的合成底层异常。
        expected_message：environment 对应的固定公开错误。
    输出返回值：
        无；断言重试不再 finalizer/capture，且错误不含底层细节。
    """

    repo_root, cache_root, task, binding = _build_runtime_fixture(tmp_path)
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=_ArtifactFamilySource(calls),
        artifact_finalizer=_ConfigurableArtifactFinalizer(
            calls,
            result=finalizer_result,
            error=finalizer_error,
        ),
        artifact_evidence_source=_ArtifactEvidenceSource(calls),
    )
    environment.start()
    environment.prepare(task)

    observed_errors: list[str] = []
    for _ in range(2):
        with pytest.raises(OSWorldEnvironmentError) as caught:
            environment.osworld_artifact_state_observations(
                TASK_ID,
                ARTIFACT_STATE_PROTOCOL_ID,
            )
        observed_errors.append(str(caught.value))
    environment.close()

    assert observed_errors == [expected_message, expected_message]
    assert calls.count("artifact-finalizer.call") == 1
    assert "artifact-family.capture" not in calls
    assert "PRIVATE" not in "".join(observed_errors)


def test_attempt_runner_maps_finalizer_failure_to_error_null_after_agent(
    tmp_path: Path,
) -> None:
    """验证真实生命周期在 Agent 后收尾并仅持久化 ERROR/null。

    输入参数：
        tmp_path：pytest 提供的 runtime fixture 与 RunStore 隔离根。
    输出返回值：
        无；断言 finalizer 位于 Agent/evaluator 之后、capture 之前，且
        Agent 文本与底层错误均不进入 RunStore。
    """

    fixture_root = tmp_path / "fixture"
    repo_root, cache_root, task, binding = _build_runtime_fixture(fixture_root)
    task = {
        **task,
        "instruction": "Complete the artifact task.",
    }
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=_ArtifactFamilySource(calls),
        artifact_finalizer=_ConfigurableArtifactFinalizer(
            calls,
            error=RuntimeError("PRIVATE_ARGV PRIVATE_STDERR"),
        ),
        artifact_evidence_source=_ArtifactEvidenceSource(calls),
    )
    prepared_task = PreparedTask(
        trusted_task=task,
        agent_task={
            "task_id": TASK_ID,
            "instruction": task["instruction"],
        },
        audit_metadata=synthetic_task_audit(TASK_ID),
    )
    store_root = tmp_path / "runstore"
    store = RunStore(store_root)
    store.start_run(
        run_id="run-artifact-finalizer-error",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-finalizer-error",
        task_id=TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared_task.audit_metadata,
    )

    with pytest.raises(
        OSWorldEnvironmentError,
        match="^OSWorld artifact finalize 失败$",
    ):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=prepared_task,
            environment=environment,
            agent=_LifecycleAgent(calls),
            evaluator=_LifecycleArtifactEvaluator(calls),
        )

    summary_text = (attempt.path / "summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert calls.index("agent.run") < calls.index("evaluator.evaluate")
    assert calls.index("evaluator.evaluate") < calls.index("artifact-finalizer.call")
    assert "artifact-family.capture" not in calls
    assert summary["execution"]["outcome"] == ExecutionOutcome.SUCCEEDED.value
    assert summary["evaluation"]["outcome"] == EvaluationOutcome.ERROR.value
    assert summary["evaluation"]["score"] is None
    persisted = b"\n".join(
        path.read_bytes() for path in store_root.rglob("*") if path.is_file()
    )
    assert b"PRIVATE_AGENT_FINAL_TEXT" not in persisted
    assert b"PRIVATE_ARGV" not in persisted
    assert b"PRIVATE_STDERR" not in persisted


def test_catalog_outside_task_captures_without_invoking_finalizer(
    tmp_path: Path,
) -> None:
    """验证 13-task 闭集外的 artifact task 不进入 finalizer。

    输入参数：
        tmp_path：pytest 提供的零资产 repo/cache 隔离根。
    输出返回值：
        无；断言仍可 capture，但注入的禁用 finalizer 保持零调用。
    """

    task_id = "Operation-FileOperate-BatchOperation-001"
    calls: list[str] = []
    evidence_source = _GenericArtifactEvidenceSource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=tmp_path / "repo",
        asset_cache_root=tmp_path / "cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_finalizer=_ConfigurableArtifactFinalizer(
            calls,
            error=AssertionError("catalog 外不得调用 finalizer"),
        ),
        artifact_evidence_source=evidence_source,
    )
    environment.start()
    environment.prepare(
        {
            "task_id": task_id,
            "prepare_script_path": "",
        }
    )

    observations = environment.osworld_artifact_state_observations(
        task_id,
        ARTIFACT_STATE_PROTOCOL_ID,
    )
    environment.close()

    assert observations == (evidence_source.observation,)
    assert "artifact-finalizer.call" not in calls
    assert calls.count("generic-artifact.capture") == 1


@pytest.mark.parametrize("binding_mode", ["missing", "digest-drift"])
def test_environment_rejects_missing_or_drifted_binding_before_guest_io(
    tmp_path: Path,
    binding_mode: str,
) -> None:
    """验证缺失或漂移 binding 在 desktop/upload/source 前失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离 repo/cache 根。
        binding_mode：分别覆盖无绑定与 manifest 摘要漂移。
    输出返回值：
        无；direct environment 即使已启动，也不能开始任何 guest I/O。
    """

    repo_root, cache_root, task, valid_binding = _build_runtime_fixture(tmp_path)
    binding = None
    if binding_mode == "digest-drift":
        binding = ArtifactFamilyTaskPrepareBinding(
            task_id=valid_binding.task_id,
            input_draft_sha256=valid_binding.input_draft_sha256,
            asset_manifest_sha256="b" * 64,
            relative_paths=valid_binding.relative_paths,
        )
    calls: list[str] = []
    source = _ArtifactFamilySource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=source,
    )

    environment.start()
    with pytest.raises(OSWorldEnvironmentError, match="runtime binding"):
        environment.prepare(task)
    environment.close()

    assert source.prepared_assets is None
    assert calls == [
        "docker.start",
        "controller.ready",
        "docker.close",
    ]


def test_environment_accepts_resolved_context_then_checks_cache_before_guest_io(
    tmp_path: Path,
) -> None:
    """验证已查明的 idle-desktop spec 越过语义门禁后仍先检查 host cache。

    输入参数：
        tmp_path：pytest 提供的隔离 repo；故意不创建 host cache。
    输出返回值：
        无；CombinationDocs-011 的严格 binding 应通过 runtime 身份复核，
        随后在 desktop、upload 与专属 source 前由 host cache 失败关闭。
    """

    task_id = "Operation-FileOperate-CombinationDocs-011"
    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    manifest_path = manifest_root / f"{task_id}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": task_id,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/artifact-family",
                    "revision": "c" * 40,
                    "base_path": "dataset/task",
                    "license_status": "apache-2.0",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": item.asset_relative_path,
                        "size": 1,
                        "sha256": "d" * 64,
                    }
                    for item in spec.asset_bindings
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    binding = ArtifactFamilyTaskPrepareBinding(
        task_id=task_id,
        input_draft_sha256=spec.input_draft_sha256,
        asset_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        relative_paths=tuple(item.asset_relative_path for item in spec.asset_bindings),
    )
    calls: list[str] = []
    source = _ArtifactFamilySource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "cache-must-not-be-read",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_family_task_prepare_binding=binding,
        artifact_family_task_prepare_source=source,
    )

    environment.start()
    with pytest.raises(OSWorldEnvironmentError, match="host 资产缓存"):
        environment.prepare(
            {
                "task_id": task_id,
                "prepare_script_path": "",
                "asset_manifest": str(manifest_path.relative_to(repo_root)),
            }
        )
    environment.close()

    assert source.prepared_assets is None
    assert calls == [
        "docker.start",
        "controller.ready",
        "docker.close",
    ]
