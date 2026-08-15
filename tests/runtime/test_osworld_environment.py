"""代表任务 OSWorld 环境准备与 owned cleanup 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest
from docx import Document

from paraguibench.evaluation.operation import (
    OPERATION_PROTOCOL_ID,
    WordTextBaseline,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OperationArtifactSnapshot,
)
from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
from paraguibench.runtime.osworld_environment import (
    OSWorldEnvironmentError,
    OSWorldTaskEnvironment,
)
from paraguibench.runtime.pipeline_implicit_binding import (
    PipelineImplicitRuntimeCapability,
    preflight_pipeline_implicit_component_candidate_runtime,
)
import paraguibench.runtime.osworld_environment as osworld_environment_module
import paraguibench.runtime.pipeline_implicit_binding as pipeline_binding_module


class _DockerSession:
    """记录当前环境是否只启动/关闭自身 Docker session。"""

    def __init__(self, calls: list[str]) -> None:
        """保存调用记录。

        输入参数：
            calls：测试共享的阶段列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def start(self) -> str:
        """记录启动并返回合成 owned container ID。

        输入参数：
            无。
        输出返回值：
            合成容器 ID。
        """

        self.calls.append("docker.start")
        return "a" * 64

    def close(self) -> None:
        """记录只清理本 session。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls.append("docker.close")


class _RetryableOperationTemporaryDirectory:
    """为 environment close 测试提供可失败并重试的目录句柄。"""

    def __init__(self, name: Path, *, failures: int) -> None:
        """保存合成目录名和 cleanup 预置失败次数。

        输入参数：
            name：snapshot 用于形成 ``artifact_root`` 的目录。
            failures：前若干次 cleanup 应抛错的数量。
        输出返回值：
            无；构造阶段不执行清理。
        """

        self.name = str(name)
        self.failures = failures
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        """记录一次清理，并按预置失败计数抛出 I/O 错误。

        输入参数：无。
        输出返回值：无；成功调用正常返回。
        """

        self.cleanup_calls += 1
        if self.cleanup_calls <= self.failures:
            raise OSError("PRIVATE SNAPSHOT PATH")


class _RetryableDockerSession(_DockerSession):
    """模拟 owned Docker 首次关闭失败、后续重试成功。"""

    def __init__(self, calls: list[str], *, failures: int) -> None:
        """保存调用记录与预置失败次数。

        输入参数：
            calls：测试共享的生命周期记录。
            failures：前若干次 close 应抛错的数量。
        输出返回值：
            无；构造阶段不启动或关闭容器。
        """

        super().__init__(calls)
        self.failures = failures
        self.close_calls = 0

    def close(self) -> None:
        """记录 Docker close，并在预置次数内模拟失败。

        输入参数：无。
        输出返回值：无；超过预置失败次数后正常返回。
        """

        self.close_calls += 1
        self.calls.append("docker.close")
        if self.close_calls <= self.failures:
            raise OSError("synthetic docker close failure")


class _Controller:
    """模拟 ready guest、文件上传、摘要校验和目录打开。"""

    def __init__(self, calls: list[str]) -> None:
        """初始化 guest 内存文件表。

        输入参数：
            calls：测试共享的阶段列表。
        输出返回值：
            无。
        """

        self.calls = calls
        self.files: dict[str, bytes] = {}
        self.symlinks: dict[str, str] = {}
        self.special_entries: dict[str, str] = {}
        self.desktop_path = "/home/oai/Desktop"
        self.desktop_path_calls = 0

    def _find_entries(self, guest_root: str) -> dict[str, str]:
        """构造 ``find`` 可观察的 guest 成员类型与相对路径。

        输入参数：
            guest_root：生产环境传给 ``find`` 的 guest 绝对目录。
        输出返回值：
            相对路径到 GNU ``find %y`` 类型字符的映射；普通文件为
            ``f``、必要祖先目录为 ``d``、符号链接为 ``l``，且不跟随链接。
        """

        root = PurePosixPath(guest_root)
        entries: dict[str, str] = {}
        typed_paths = [
            *((path, "f") for path in self.files),
            *((path, "l") for path in self.symlinks),
            *self.special_entries.items(),
        ]
        for guest_path, entry_type in typed_paths:
            try:
                relative_path = PurePosixPath(guest_path).relative_to(root)
            except ValueError:
                continue
            if relative_path == PurePosixPath("."):
                continue
            for parent in relative_path.parents:
                if parent != PurePosixPath("."):
                    entries.setdefault(parent.as_posix(), "d")
            entries[relative_path.as_posix()] = entry_type
        return entries

    def wait_until_ready(self, *, timeout: float) -> None:
        """记录 controller readiness。

        输入参数：
            timeout：runtime 提供的启动上限。
        输出返回值：
            无。
        """

        assert timeout > 0
        self.calls.append("controller.ready")

    def get_desktop_path(self) -> str:
        """返回合成 guest 当前用户 Desktop。

        输入参数：
            无。
        输出返回值：
            POSIX 绝对路径。
        """

        self.desktop_path_calls += 1
        return self.desktop_path

    def upload_file(self, local_path: Path, guest_path: str) -> None:
        """把本地 bytes 保存到 guest 内存文件表。

        输入参数：
            local_path：已验证的 host 文件。
            guest_path：runtime 推导的 guest shared 目标。
        输出返回值：
            无。
        """

        self.files[guest_path] = local_path.read_bytes()
        self.calls.append("controller.upload")

    def execute(self, command: list[str]) -> Any:
        """模拟 sha256sum、find 与固定输出文件创建命令。

        输入参数：
            command：runtime 生成的 shell-free argv。
        输出返回值：
            具有 returncode/stdout/stderr 属性的合成对象。
        """

        class Result:
            """保存一次合成 guest 命令结果。"""

            returncode = 0
            stdout = ""
            stderr = ""

        result = Result()
        if command[0] == "sha256sum":
            guest_path = command[-1]
            result.stdout = (
                hashlib.sha256(self.files[guest_path]).hexdigest()
                + "  "
                + guest_path
                + "\n"
            )
        elif command[0] == "find":
            guest_root = command[2] if command[1] == "-P" else command[1]
            entries = self._find_entries(guest_root)
            if "-type" in command:
                expected_type = command[command.index("-type") + 1]
                result.stdout = "\n".join(
                    sorted(
                        path
                        for path, entry_type in entries.items()
                        if entry_type == expected_type
                    )
                )
            else:
                result.stdout = "\n".join(
                    f"{entry_type}\t{path}"
                    for path, entry_type in sorted(entries.items())
                )
            if result.stdout:
                result.stdout += "\n"
        elif command[:2] == ["touch", "--"]:
            self.files[command[2]] = b""
            self.calls.append("controller.execute:" + " ".join(command))
        return result

    def execute_with_timeout(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> Any:
        """模拟 Settings-003 有界 PDF 验证与 Chrome 启动动作。

        输入参数：
            command：task prepare source 发出的固定 shell-free argv。
            timeout_seconds：当次启动上下文的总超时。
        输出返回值：
            具有零退出码且不含私有输出的合成结果。
        """

        class Result:
            """保存一次成功的合成 guest 动作结果。"""

            returncode = 0
            stdout = ""
            stderr = ""

        assert command[:3] == ["python3", "-I", "-c"]
        assert command[-2:] == ["/home/oai/shared", "2206.08853.pdf"]
        self.calls.append(f"controller.execute_with_timeout:{timeout_seconds}")
        return Result()

    def launch(self, command: list[str]) -> None:
        """记录一次固定、shell-free 的图形进程启动。

        输入参数：
            command：task prepare source 生成的固定 argv。
        输出返回值：
            无。
        """

        self.calls.append("controller.launch:" + " ".join(command))

    def wait_for_chrome_cdp(self, *, port: int, timeout: float) -> None:
        """记录对固定 Chrome CDP 端口的就绪门禁。

        输入参数：
            port：待检查的 guest-local CDP 端口。
            timeout：最大等待秒数。
        输出返回值：
            无。
        """

        self.calls.append(f"controller.wait_cdp:{port}:{timeout}")

    def open_path(self, guest_path: str) -> None:
        """记录 runtime 打开的 shared 目录。

        输入参数：
            guest_path：已准备完成的 guest 目录。
        输出返回值：
            无。
        """

        assert guest_path == "/home/oai/shared"
        self.calls.append("controller.open_path")


class _StateEvidenceSource:
    """记录 environment 对受控 Chrome 状态 source 的生命周期调用。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录与合成 observation。

        输入参数：
            calls：测试共享的阶段列表。
        输出返回值：
            无。
        """

        self.calls = calls
        self.prepared_task: dict[str, Any] | None = None

    def prepare(self, task: dict[str, Any], controller: Any) -> None:
        """记录 task-specific setup 在 guest ready 之后执行。

        输入参数：
            task：可信 canonical task。
            controller：当前 environment 的 controller。
        输出返回值：
            无。
        """

        assert isinstance(controller, _Controller)
        self.prepared_task = dict(task)
        self.calls.append("state.prepare")

    def capture(
        self,
        protocol_id: str,
        controller: Any,
    ) -> tuple[object, ...]:
        """记录 evaluate 期间的状态捕获并返回合成 observation。

        输入参数：
            protocol_id：runtime evaluator 请求的协议。
            controller：仍存活的同一 controller。
        输出返回值：
            含一个不透明 observation 的 tuple。
        """

        assert isinstance(controller, _Controller)
        self.calls.append(f"state.capture:{protocol_id}")
        return ("synthetic-observation",)


class _ArtifactEvidenceSource:
    """记录 environment 对单 VM artifact evidence source 的捕获。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录并创建唯一合成 observation。

        输入参数：
            calls：测试共享的阶段列表。
        输出返回值：
            无；构造阶段不读取 guest 或 artifact。
        """

        self.calls = calls
        self.observation = object()
        self.guest_shared_dirs: list[str | None] = []

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> object:
        """记录当前 task 与 controller 并返回单台 VM observation。

        输入参数：
            task_id：environment 已完成 prepare 的 canonical task ID。
            controller：仍存活的同一 OSWorld controller。
            guest_shared_dir：prepare 阶段冻结的 guest shared 目录；无固定
                资产的合成测试任务允许为 ``None``。
        输出返回值：
            唯一合成 observation；environment 应将其包装为单元素 tuple。
        """

        assert isinstance(controller, _Controller)
        self.guest_shared_dirs.append(guest_shared_dir)
        self.calls.append(f"artifact.capture:{task_id}")
        return self.observation


class _OperationArtifactSource:
    """记录 environment 对 Operation 完整快照 source 的捕获与清理。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录与尚未创建的快照。

        输入参数：
            calls：测试共享的生命周期记录。
        输出返回值：
            无；构造阶段不创建临时目录。
        """

        self.calls = calls
        self.guest_shared_dirs: list[str | None] = []
        self.snapshot: OperationArtifactSnapshot | None = None
        self.snapshot_root: Path | None = None

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> OperationArtifactSnapshot:
        """创建一个可观察 close 清理的合成 host 快照。

        输入参数：
            task_id：environment 已准备的 canonical task ID。
            controller：仍存活的同一 OSWorld controller。
            guest_shared_dir：prepare 阶段冻结的 guest shared 路径。
        输出返回值：
            拥有空临时目录的 ``OperationArtifactSnapshot``。
        """

        assert isinstance(controller, _Controller)
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="paraguibench-operation-environment-test-"
        )
        self.snapshot = OperationArtifactSnapshot(
            task_id=task_id,
            protocol_id=OPERATION_PROTOCOL_ID,
            file_count=0,
            temporary_directory=temporary_directory,
        )
        self.snapshot_root = self.snapshot.artifact_root()
        self.guest_shared_dirs.append(guest_shared_dir)
        self.calls.append(f"operation.capture:{task_id}")
        return self.snapshot


class _BookmarkEvidenceSource:
    """记录书签基线重置与评价阶段快照捕获。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录与唯一合成 observation。

        输入参数：
            calls：测试共享的生命周期记录。
        输出返回值：
            无。
        """

        self.calls = calls
        self.observations = (object(),)

    def prepare(self, task: dict[str, Any], controller: Any) -> None:
        """记录 Agent 前的书签空基线准备。

        输入参数：
            task：可信 canonical task。
            controller：当前单 VM controller。
        输出返回值：
            无。
        """

        assert isinstance(controller, _Controller)
        self.calls.append(f"bookmark.prepare:{task['task_id']}")

    def capture(
        self,
        protocol_id: str,
        controller: Any,
    ) -> tuple[object, ...]:
        """记录 Agent 后的同步快照捕获。

        输入参数：
            protocol_id：固定 Chrome Bookmarks 协议。
            controller：仍存活的同一 controller。
        输出返回值：
            构造时冻结的单 VM observation tuple。
        """

        assert isinstance(controller, _Controller)
        self.calls.append(f"bookmark.capture:{protocol_id}")
        return self.observations


class _RecordingTaskPrepareSource:
    """记录 bookmark reset 后的版本化 task setup。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录。

        输入参数：
            calls：测试共享的生命周期记录。
        输出返回值：
            无。
        """

        self.calls = calls

    def prepare(
        self,
        task: dict[str, Any],
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> bool:
        """记录 task setup 并报告已处理，避免通用 Files fallback。

        输入参数：
            task：可信 canonical task。
            controller：当前单 VM controller。
            guest_shared_dir：资产任务冻结的 shared 路径或 ``None``。
        输出返回值：
            始终返回 ``True``。
        """

        assert isinstance(controller, _Controller)
        del guest_shared_dir
        self.calls.append(f"task.prepare:{task['task_id']}")
        return True


class _FailingTaskPrepareSource:
    """模拟在 task-specific setup 边界失败的版本化 source。"""

    def prepare(
        self,
        task: dict[str, Any],
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> bool:
        """抛出包含敏感合成路径的底层异常。

        输入参数：
            task：environment 正在准备的 canonical task。
            controller：当前 VM controller，本 fake 不使用。
            guest_shared_dir：冻结的 guest shared 路径或 ``None``。
        输出返回值：
            不返回；始终抛出异常。
        """

        del task, controller, guest_shared_dir
        raise RuntimeError("failed at synthetic-private-locator")


def test_environment_prepares_asset_free_task_without_shared_directory(
    tmp_path: Path,
) -> None:
    """验证零资产任务不会访问或伪造 guest shared 目录。

    输入参数：
        tmp_path：pytest 提供的合成仓库与未使用缓存根目录。
    输出返回值：
        无；环境仅启动、等待就绪并进入 prepared 状态，随后正常关闭；
        controller 的路径、上传、命令与打开目录接口均不被调用。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    controller = _Controller(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "unused-cache",
        docker_session=_DockerSession(calls),
        controller=controller,
    )

    environment.start()
    environment.prepare(
        {
            "task_id": "asset-free-task",
            "instruction": "Inspect the current browser state.",
            "prepare_script_path": "",
        }
    )
    environment.close()

    assert environment.guest_shared_dir is None
    assert controller.files == {}
    assert calls == [
        "docker.start",
        "controller.ready",
        "docker.close",
    ]


def test_pipeline_capability_rejects_manifest_swap_before_guest_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 preflight 后替换 input manifest 不能把其他字节上传 guest。

    输入参数：
        tmp_path：pytest 提供的合成仓库、缓存和容器边界。
        monkeypatch：精确模拟 validator 的 preflight 读取窗口短暂看到
            原 manifest A，而 prepare 已解析且将上传的仍是 B。
    输出返回值：
        无；PPT-003 正式 capability 必须穿透到 ``prepare``，
        当同 task ID 的合法 download-only manifest 在 preflight 后
        换成另一组字节身份时，在任何 guest 上传前失败关闭。
    """

    source_root = Path(__file__).resolve().parents[2]
    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    task_relative = Path("benchmark/tasks") / f"{task_id}.json"
    input_relative = Path("benchmark/assets/manifests") / f"{task_id}.json"
    gold_relative = Path("benchmark/gold/manifests") / f"{task_id}.json"
    image_relative = Path("environments/osworld/image-manifest.json")
    repo_root = tmp_path / "repo"
    for relative_path in (
        task_relative,
        input_relative,
        gold_relative,
        image_relative,
    ):
        destination = repo_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source_root / relative_path).read_bytes())
    task = json.loads((repo_root / task_relative).read_text(encoding="utf-8"))
    image_manifest = load_osworld_image_manifest(repo_root / image_relative)
    capability = preflight_pipeline_implicit_component_candidate_runtime(
        repo_root=repo_root,
        task=task,
        image_manifest=image_manifest,
    )
    assert isinstance(capability, PipelineImplicitRuntimeCapability)

    replacement_payloads = {
        item["path"]: f"synthetic-swapped-{index}".encode()
        for index, item in enumerate(
            json.loads((repo_root / input_relative).read_text(encoding="utf-8"))[
                "files"
            ],
            start=1,
        )
    }
    swapped_manifest = json.loads(
        (repo_root / input_relative).read_text(encoding="utf-8")
    )
    for item in swapped_manifest["files"]:
        payload = replacement_payloads[item["path"]]
        item["size"] = len(payload)
        item["sha256"] = hashlib.sha256(payload).hexdigest()
    (repo_root / input_relative).write_text(
        json.dumps(swapped_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    cache_root = tmp_path / "cache"
    cache_directory = cache_root / task_id
    cache_directory.mkdir(parents=True)
    for relative_path, payload in replacement_payloads.items():
        candidate = cache_directory / relative_path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(payload)
    monkeypatch.setattr(
        pipeline_binding_module,
        "preflight_pipeline_implicit_local_runtime",
        lambda **_kwargs: capability,
    )

    calls: list[str] = []
    controller = _Controller(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
        pipeline_implicit_runtime_capability=capability,
    )
    environment.start()

    with pytest.raises(
        OSWorldEnvironmentError,
        match="pipeline-implicit runtime binding 无效",
    ):
        environment.prepare(task)
    environment.close()

    assert controller.files == {}
    assert calls == ["docker.start", "controller.ready", "docker.close"]


def test_environment_runs_state_setup_and_exposes_frozen_observations(
    tmp_path: Path,
) -> None:
    """验证 state setup 在 VM ready 后执行，评价时复用同一 controller。

    输入参数：
        tmp_path：pytest 提供的合成零资产 repo/cache 根目录。
    输出返回值：
        无；environment 完成 prepare 后才能捕获状态，且 close 发生在其后。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    controller = _Controller(calls)
    source = _StateEvidenceSource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "unused-cache",
        docker_session=_DockerSession(calls),
        controller=controller,
        state_evidence_source=source,
    )
    task = {
        "task_id": "Operation-WebOperate-Settings-001",
        "evaluation_mode": "osworld_profile_state",
    }

    environment.start()
    environment.prepare(task)
    observations = environment.osworld_state_observations(
        "paraguibench.osworld.chrome-profile-name.v1"
    )
    environment.close()

    assert source.prepared_task == task
    assert observations == ("synthetic-observation",)
    assert calls == [
        "docker.start",
        "controller.ready",
        "state.prepare",
        ("state.capture:paraguibench.osworld.chrome-profile-name.v1"),
        "docker.close",
    ]


def test_environment_resets_bookmarks_before_task_setup_and_freezes_capture(
    tmp_path: Path,
) -> None:
    """验证书签 reset 先于初始页面 setup，且评价快照只捕获一次。

    输入参数：
        tmp_path：pytest 提供的合成 repo/cache 根目录。
    输出返回值：
        无；调用顺序固定为 reset→task setup→capture→close，重复读取
        返回同一冻结 tuple 而不再次访问 source。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    source = _BookmarkEvidenceSource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "unused-cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        bookmark_evidence_source=source,
        task_prepare_source=_RecordingTaskPrepareSource(calls),
    )
    task_id = "Operation-WebOperate-WebNavigate-008"

    environment.start()
    environment.prepare(
        {
            "task_id": task_id,
            "prepare_script_path": "",
        }
    )
    first = environment.osworld_bookmark_observations(
        task_id,
        "paraguibench.osworld.chrome-bookmarks.v1",
    )
    second = environment.osworld_bookmark_observations(
        task_id,
        "paraguibench.osworld.chrome-bookmarks.v1",
    )
    environment.close()

    assert first is second
    assert first == source.observations
    assert calls == [
        "docker.start",
        "controller.ready",
        f"bookmark.prepare:{task_id}",
        f"task.prepare:{task_id}",
        "bookmark.capture:paraguibench.osworld.chrome-bookmarks.v1",
        "docker.close",
    ]


def test_settings_003_resets_after_asset_verification_then_opens_pdf(
    tmp_path: Path,
) -> None:
    """验证 Settings-003 的资产、reset 与 PDF 启动上下文顺序。

    输入参数：
        tmp_path：pytest 提供的合成 repo、cache 与 manifest 根目录。
    输出返回值：
        无；固定 PDF 先完成上传与 guest 摘要闭集校验，随后清空
        书签基线，最后执行有界 Chrome PDF 启动；成功时不得
        再打开通用 shared 目录。
    """

    task_id = "Operation-WebOperate-Settings-003"
    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    task_cache = cache_root / task_id
    task_cache.mkdir(parents=True)
    asset_content = b"%PDF-1.7\nsynthetic pinned paper\n"
    (task_cache / "2206.08853.pdf").write_bytes(asset_content)
    manifest_path = manifest_root / f"{task_id}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": task_id,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "xlangai/ubuntu_osworld_file_cache",
                    "revision": "a" * 40,
                    "base_path": ("multi_apps/a82b78bb-7fde-4cb3-94a4-035baf10bcf0"),
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "2206.08853.pdf",
                        "size": len(asset_content),
                        "sha256": hashlib.sha256(asset_content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        bookmark_evidence_source=_BookmarkEvidenceSource(calls),
    )
    task = {
        "task_id": task_id,
        "task_uid": "bc69ee94-cf90-4cc4-a6ed-4266daa71706",
        "task_source": "OSWorld",
        "task_type": "OSWorld脚本",
        "task_tag": "WebOperate",
        "evaluator_path": "eval/webnavigate_bookmark_evaluator.py",
        "asset_manifest": str(manifest_path.relative_to(repo_root)),
        "agent_start_context": {
            "type": "local_pdf",
            "asset_relative_path": "2206.08853.pdf",
            "open_with": "chrome",
            "target": "all_vms",
        },
    }

    environment.start()
    environment.prepare(task)
    environment.close()

    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.upload",
        f"bookmark.prepare:{task_id}",
        "controller.execute_with_timeout:30.0",
        "docker.close",
    ]


def test_environment_rejects_state_capture_before_prepare_or_without_source(
    tmp_path: Path,
) -> None:
    """验证 state evidence 不会在错误生命周期或缺少 source 时降级。

    输入参数：
        tmp_path：pytest 提供的合成 repo/cache 根目录。
    输出返回值：
        无；两种错误装配均抛 OSWorldEnvironmentError。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    with_source = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "cache-a",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        state_evidence_source=_StateEvidenceSource(calls),
    )
    without_source = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "cache-b",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
    )

    with pytest.raises(OSWorldEnvironmentError, match="prepare"):
        with_source.osworld_state_observations("protocol")
    without_source.start()
    without_source.prepare({"task_id": "asset-free"})
    with pytest.raises(OSWorldEnvironmentError, match="source"):
        without_source.osworld_state_observations("protocol")
    without_source.close()


def test_task_specific_setup_failure_keeps_environment_unprepared(
    tmp_path: Path,
) -> None:
    """验证 task setup 失败不会产生半准备的可评价环境。

    输入参数：
        tmp_path：pytest 提供的合成 repo/cache 根目录。
    输出返回值：
        无；断言 environment 对 source 异常脱敏，并拒绝后续
        artifact capture。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "unused-cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        task_prepare_source=_FailingTaskPrepareSource(),
        artifact_evidence_source=_ArtifactEvidenceSource(calls),
    )
    environment.start()

    with pytest.raises(OSWorldEnvironmentError, match="task-specific") as captured:
        environment.prepare({"task_id": "synthetic-task"})
    with pytest.raises(OSWorldEnvironmentError, match="prepare"):
        environment.osworld_artifact_state_observations(
            "synthetic-task",
            "paraguibench.osworld.artifact-state.v1",
        )

    environment.close()
    assert "synthetic-private-locator" not in str(captured.value)
    assert calls == [
        "docker.start",
        "controller.ready",
        "docker.close",
    ]


def test_environment_exposes_one_frozen_artifact_observation_per_attempt(
    tmp_path: Path,
) -> None:
    """验证 artifact evidence 只在同一已准备 VM 中捕获一次并冻结。

    输入参数：
        tmp_path：pytest 提供的合成零资产 repo/cache 根目录。
    输出返回值：
        无；重复评价读取同一 tuple，不跨时点重新执行 finalize/getter/metric。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    source = _ArtifactEvidenceSource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "unused-cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_evidence_source=source,
    )
    task_id = "Operation-FileOperate-BatchOperation-001"
    protocol_id = "paraguibench.osworld.artifact-state.v1"

    environment.start()
    environment.prepare({"task_id": task_id})
    first = environment.osworld_artifact_state_observations(
        task_id,
        protocol_id,
    )
    second = environment.osworld_artifact_state_observations(
        task_id,
        protocol_id,
    )
    environment.close()

    assert first is second
    assert first == (source.observation,)
    assert source.guest_shared_dirs == [None]
    assert calls == [
        "docker.start",
        "controller.ready",
        f"artifact.capture:{task_id}",
        "docker.close",
    ]


def test_environment_caches_operation_snapshot_and_closes_owned_host_tree(
    tmp_path: Path,
) -> None:
    """验证生产环境冻结 Operation 快照并在 close 删除临时树。

    输入参数：
        tmp_path：pytest 提供的合成 repo、cache 与 manifest 根目录。
    输出返回值：
        无；重复读取必须返回同一快照，capture 只执行一次，
        source 只看到 prepare 阶段冻结的 shared 路径，close 后 host
        临时目录必须不存在。
    """

    task_id = "Operation-FileOperate-CombinationDocs-005"
    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    task_cache = cache_root / task_id
    task_cache.mkdir(parents=True)
    content = b"synthetic operation input"
    (task_cache / "input.txt").write_bytes(content)
    manifest_path = manifest_root / f"{task_id}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": task_id,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/operation-assets",
                    "revision": "d" * 40,
                    "base_path": "dataset/task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "input.txt",
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    source = _OperationArtifactSource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        operation_artifact_source=source,
    )
    task = {
        "task_id": task_id,
        "task_tag": "FileOperate",
        "asset_manifest": str(manifest_path.relative_to(repo_root)),
    }

    environment.start()
    environment.prepare(task)
    first = environment.operation_artifact_snapshot(
        task_id,
        OPERATION_PROTOCOL_ID,
    )
    second = environment.operation_artifact_snapshot(
        task_id,
        OPERATION_PROTOCOL_ID,
    )
    assert first is second
    assert source.snapshot_root is not None and source.snapshot_root.exists()
    environment.close()

    assert source.guest_shared_dirs == ["/home/oai/shared"]
    assert not source.snapshot_root.exists()
    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.upload",
        "controller.open_path",
        f"operation.capture:{task_id}",
        "docker.close",
    ]


def test_environment_close_cleans_all_snapshots_and_retries_failures(
    tmp_path: Path,
) -> None:
    """验证单个 snapshot 清理失败不会阻断其它清理与重试。

    输入参数：
        tmp_path：pytest 提供的 environment 与合成快照目录根。
    输出返回值：
        无；首次 close 必须关闭 Docker 和另一快照、仅保留
        失败快照，第二次 close 只重试该快照。
    """

    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=tmp_path,
        asset_cache_root=tmp_path / "cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
    )
    environment.start()
    retryable_directory = _RetryableOperationTemporaryDirectory(
        tmp_path / "retryable",
        failures=1,
    )
    successful_directory = _RetryableOperationTemporaryDirectory(
        tmp_path / "successful",
        failures=0,
    )
    retryable_snapshot = OperationArtifactSnapshot(
        task_id="Operation-FileOperate-CombinationDocs-005",
        protocol_id=OPERATION_PROTOCOL_ID,
        file_count=0,
        temporary_directory=retryable_directory,  # type: ignore[arg-type]
    )
    successful_snapshot = OperationArtifactSnapshot(
        task_id="Operation-FileOperate-CombinationDocs-006",
        protocol_id=OPERATION_PROTOCOL_ID,
        file_count=0,
        temporary_directory=successful_directory,  # type: ignore[arg-type]
    )
    environment._operation_artifact_snapshot_cache.update(  # noqa: SLF001
        {
            (retryable_snapshot.task_id, OPERATION_PROTOCOL_ID): (retryable_snapshot),
            (successful_snapshot.task_id, OPERATION_PROTOCOL_ID): (successful_snapshot),
        }
    )

    with pytest.raises(OSWorldEnvironmentError, match="快照清理") as captured:
        environment.close()

    assert "PRIVATE" not in str(captured.value)
    assert calls.count("docker.close") == 1
    assert retryable_directory.cleanup_calls == 1
    assert successful_directory.cleanup_calls == 1
    environment.close()
    assert calls.count("docker.close") == 1
    assert retryable_directory.cleanup_calls == 2
    assert successful_directory.cleanup_calls == 1


def test_environment_close_retries_owned_docker_after_transient_failure(
    tmp_path: Path,
) -> None:
    """验证 Docker 关闭失败时保留独立重试入口。

    输入参数：
        tmp_path：pytest 提供的 environment 与快照目录根。
    输出返回值：
        无；首次 close 仍应清理快照但保留 Docker pending，
        第二次只重试 Docker 且成功。
    """

    calls: list[str] = []
    docker_session = _RetryableDockerSession(calls, failures=1)
    environment = OSWorldTaskEnvironment(
        repo_root=tmp_path,
        asset_cache_root=tmp_path / "cache",
        docker_session=docker_session,
        controller=_Controller(calls),
    )
    environment.start()
    temporary_directory = _RetryableOperationTemporaryDirectory(
        tmp_path / "snapshot",
        failures=0,
    )
    snapshot = OperationArtifactSnapshot(
        task_id="Operation-FileOperate-CombinationDocs-005",
        protocol_id=OPERATION_PROTOCOL_ID,
        file_count=0,
        temporary_directory=temporary_directory,  # type: ignore[arg-type]
    )
    environment._operation_artifact_snapshot_cache[  # noqa: SLF001
        (snapshot.task_id, OPERATION_PROTOCOL_ID)
    ] = snapshot

    with pytest.raises(OSError, match="docker close"):
        environment.close()

    assert docker_session.close_calls == 1
    assert temporary_directory.cleanup_calls == 1
    environment.close()
    assert docker_session.close_calls == 2
    assert temporary_directory.cleanup_calls == 1


def test_environment_rejects_unknown_artifact_protocol_before_capture(
    tmp_path: Path,
) -> None:
    """验证环境不会把未知协议路由到 artifact evidence source。

    输入参数：
        tmp_path：pytest 提供的合成零资产 repo/cache 根目录。
    输出返回值：
        无；未知协议在任何 finalize/getter/metric 前失败关闭。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    task_id = "Operation-FileOperate-BatchOperation-001"
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "unused-cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        artifact_evidence_source=_ArtifactEvidenceSource(calls),
    )
    environment.start()
    environment.prepare({"task_id": task_id})

    with pytest.raises(OSWorldEnvironmentError, match="protocol"):
        environment.osworld_artifact_state_observations(
            task_id,
            "paraguibench.osworld.unknown.v1",
        )

    environment.close()
    assert not any(call.startswith("artifact.capture:") for call in calls)


def test_environment_uploads_exact_verified_asset_set_and_cleans_owned_session(
    tmp_path: Path,
) -> None:
    """验证 host 固定资产经摘要校验上传并打开，结束时只关自身 session。

    输入参数：
        tmp_path：pytest 提供的合成 repo、cache 和 manifest 根目录。
    输出返回值：
        无；两个文件必须上传至动态 guest home 下的 shared 目录。
    """

    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    task_cache = cache_root / "synthetic-assets"
    task_cache.mkdir(parents=True)
    files = {"images/diagram.jpg": b"image", "paper.pdf": b"pdf"}
    manifest_files: list[dict[str, Any]] = []
    for name, content in files.items():
        cache_file = task_cache / name
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(content)
        manifest_files.append(
            {
                "path": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest_path = manifest_root / "synthetic.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "synthetic-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/assets",
                    "revision": "c" * 40,
                    "base_path": "dataset/task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": manifest_files,
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    controller = _Controller(calls)
    source = _ArtifactEvidenceSource(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
        artifact_evidence_source=source,
        ready_timeout=300,
    )
    task = {
        "task_id": "Operation-FileOperate-BatchOperation-001",
        "instruction": "Inspect the shared folder.",
        "asset_manifest": str(manifest_path.relative_to(repo_root)),
    }

    environment.start()
    environment.prepare(task)
    controller.desktop_path = "/srv/paraguibench-test/drifted/Desktop"
    environment.osworld_artifact_state_observations(
        task["task_id"],
        "paraguibench.osworld.artifact-state.v1",
    )
    environment.close()

    assert set(controller.files) == {
        "/home/oai/shared/images/diagram.jpg",
        "/home/oai/shared/paper.pdf",
    }
    assert environment.guest_shared_dir == "/home/oai/shared"
    assert source.guest_shared_dirs == ["/home/oai/shared"]
    assert controller.desktop_path_calls == 1
    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.upload",
        "controller.upload",
        "controller.open_path",
        "artifact.capture:Operation-FileOperate-BatchOperation-001",
        "docker.close",
    ]


@pytest.mark.parametrize(
    ("injected_kind", "injected_name", "link_target"),
    (
        ("regular_file", "ConferenceCity Gold.xlsx", None),
        (
            "file_symlink",
            "ConferenceCity Gold.xlsx",
            "/opt/paraguibench-gold/ConferenceCity Gold.xlsx",
        ),
        ("directory_symlink", "answer_files", "/opt/paraguibench-gold"),
        ("fifo", "gold.pipe", None),
    ),
    ids=(
        "regular-gold-file",
        "gold-file-symlink",
        "gold-directory-symlink",
        "gold-special-node",
    ),
)
def test_environment_rejects_gold_injected_into_search_write_007_guest(
    tmp_path: Path,
    injected_kind: str,
    injected_name: str,
    link_target: str | None,
) -> None:
    """验证 SearchAndWrite-007 guest shared 拒绝各类未声明 gold 成员。

    输入参数：
        tmp_path：pytest 提供的合成 repo、cache 与 manifest 根。
        injected_kind：普通文件、文件链接、目录链接或 FIFO 注入身份。
        injected_name：注入到 guest shared 的未声明成员名。
        link_target：符号链接目标；普通文件注入时为 ``None``。
    输出返回值：
        无；host cache 完整通过后，在 guest shared 预置
        ``ConferenceCity Gold.xlsx``、``answer_files`` gold 链接或特殊节点
        必须使 production environment 失败关闭，不打开目录也不进入 Agent。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-007"
    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    task_cache = cache_root / task_id
    task_cache.mkdir(parents=True)
    asset_content = b"synthetic-conference-input"
    (task_cache / "Conference.xlsx").write_bytes(asset_content)
    manifest_path = manifest_root / f"{task_id}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": task_id,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "xlangai/ubuntu_osworld_file_cache",
                    "revision": "711e0811642364e7aa8f10a8918367d0b626d578",
                    "base_path": ("multi_apps/6f4073b8-d8ea-4ade-8a18-c5d1d5d5aa9a"),
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "Conference.xlsx",
                        "size": len(asset_content),
                        "sha256": hashlib.sha256(asset_content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    controller = _Controller(calls)
    injected_path = f"/home/oai/shared/{injected_name}"
    if injected_kind == "regular_file":
        assert link_target is None
        controller.files[injected_path] = b"gold"
    elif injected_kind == "fifo":
        assert link_target is None
        controller.special_entries[injected_path] = "p"
    else:
        assert link_target is not None
        controller.symlinks[injected_path] = link_target
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
    )
    task = {
        "task_id": task_id,
        "instruction": "Fill the conference locations.",
        "asset_manifest": str(manifest_path.relative_to(repo_root)),
    }

    environment.start()
    with pytest.raises(OSWorldEnvironmentError, match="guest shared"):
        environment.prepare(task)
    environment.close()

    expected_files = {"/home/oai/shared/Conference.xlsx"}
    if injected_kind == "regular_file":
        expected_files.add(injected_path)
    assert set(controller.files) == expected_files
    assert controller.symlinks == (
        {} if link_target is None else {injected_path: link_target}
    )
    assert controller.special_entries == (
        {injected_path: "p"} if injected_kind == "fifo" else {}
    )
    assert "controller.open_path" not in calls
    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.upload",
        "docker.close",
    ]


def test_environment_runs_combination_docs_prepare_after_asset_verification(
    tmp_path: Path,
) -> None:
    """验证 015 资产上传后执行默认版本化 task prepare。

    输入参数：
        tmp_path：pytest 提供的合成 repo、cache 与 manifest 根目录。
    输出返回值：
        无；断言输入仍位于 ``shared/references.docx``，输出
        则保持 ``Desktop/references.bib`` 的源 evaluator identity。
    """

    task_id = "Operation-FileOperate-CombinationDocs-015"
    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    task_cache = cache_root / task_id
    task_cache.mkdir(parents=True)
    asset_content = b"synthetic-docx"
    (task_cache / "references.docx").write_bytes(asset_content)
    manifest_path = manifest_root / f"{task_id}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": task_id,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "xlangai/ubuntu_osworld_file_cache",
                    "revision": "a" * 40,
                    "base_path": ("multi_apps/df67aebb-fb3a-44fd-b75b-51b6012df509"),
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "references.docx",
                        "size": len(asset_content),
                        "sha256": hashlib.sha256(asset_content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    controller = _Controller(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
    )
    task = {
        "task_id": task_id,
        "task_uid": "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "task_source": "OSWorld",
        "asset_manifest": (
            "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-015.json"
        ),
        "gold_manifest": (
            "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-015.json"
        ),
        "evaluator_path": (
            "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
        ),
    }

    environment.start()
    environment.prepare(task)
    environment.close()

    assert controller.files["/home/oai/shared/references.docx"] == (asset_content)
    assert controller.files["/home/oai/Desktop/references.bib"] == b""
    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.upload",
        "controller.launch:google-chrome --remote-debugging-port=1337",
        "controller.wait_cdp:1337:15.0",
        ("controller.launch:socat tcp-listen:9222,fork tcp:localhost:1337"),
        "controller.launch:google-chrome --new-tab https://dblp.org/",
        ("controller.execute:touch -- /home/oai/Desktop/references.bib"),
        "controller.launch:code /home/oai/Desktop/references.bib",
        ("controller.launch:libreoffice --writer /home/oai/shared/references.docx"),
        "docker.close",
    ]


def test_environment_rejects_symlinked_asset_manifest_before_cache_access(
    tmp_path: Path,
) -> None:
    """验证仓库内 manifest 末端符号链接不会因 resolve 而绕过门禁。

    输入参数：
        tmp_path：pytest 提供的合成 repo 与外部 manifest 目录。
    输出返回值：
        无；prepare 在读取资产缓存前以普通文件契约失败。
    """

    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    target = manifest_root / "real.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "synthetic-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/assets",
                    "revision": "d" * 40,
                    "base_path": "task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "paper.pdf",
                        "size": 1,
                        "sha256": hashlib.sha256(b"x").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    link = manifest_root / "linked.json"
    link.symlink_to(target)
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
    )
    environment.start()

    with pytest.raises(OSWorldEnvironmentError, match="普通仓库文件"):
        environment.prepare(
            {
                "task_id": "synthetic-task",
                "instruction": "Inspect.",
                "asset_manifest": "benchmark/assets/manifests/linked.json",
            }
        )

    environment.close()


def _write_word_text_baseline_fixture(
    tmp_path: Path,
    *,
    valid_docx: bool,
) -> tuple[Path, Path, dict[str, str]]:
    """写入 Word-009 prepare 前 baseline 测试的 repo/cache/manifest。

    输入参数：
        tmp_path：pytest 私有根；valid_docx：是否生成可解析 DOCX。
    输出返回值：
        ``(repo_root, cache_root, canonical_task)``；文件大小与 SHA
        在 manifest 与 cache 中严格一致。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-009"
    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    cache_directory = cache_root / task_id
    cache_directory.mkdir(parents=True)
    document_path = cache_directory / "Document.docx"
    if valid_docx:
        document = Document()
        document.add_paragraph("PREPARE-ONLY VISIBLE BODY")
        document.save(document_path)
    else:
        document_path.write_bytes(b"not-a-docx")
    payload = document_path.read_bytes()
    manifest_reference = (
        "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-009.json"
    )
    (repo_root / manifest_reference).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": task_id,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/word-assets",
                    "revision": "d" * 40,
                    "base_path": "dataset/task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": document_path.name,
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "media_type": (
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return (
        repo_root,
        cache_root,
        {
            "task_id": task_id,
            "task_tag": "FileOperate",
            "asset_manifest": manifest_reference,
        },
    )


def _patch_synthetic_word_text_contract(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo_root: Path,
    task: dict[str, str],
) -> None:
    """为单元测试注入与合成 manifest 一致的不可变合同。

    输入参数：
        monkeypatch：pytest 替换器；repo_root/task：合成 manifest 位置与任务。
    输出返回值：
        无；只替换 environment 已导入的合同 getter，使测试
        可以独立于 download-only 正式大文件；生产 getter 本身
        仍由 evaluator 固定 81f…/1743… 身份。
    """

    manifest_reference = task["asset_manifest"]
    payload = (repo_root / manifest_reference).read_bytes()
    manifest = json.loads(payload)
    contract = SimpleNamespace(
        manifest_reference=manifest_reference,
        manifest_sha256=hashlib.sha256(payload).hexdigest(),
        files=tuple(
            SimpleNamespace(
                path=entry["path"],
                size=entry["size"],
                sha256=entry["sha256"],
            )
            for entry in manifest["files"]
        ),
    )
    monkeypatch.setattr(
        osworld_environment_module,
        "operation_word_text_input_contract",
        lambda task_id: contract,
    )


def test_word_text_baseline_is_captured_before_first_guest_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 typed pre 快照在 desktop 查询和上传前完成。

    输入参数：
        tmp_path：合成 repo/cache；monkeypatch：只记录生产 capture 边界。
    输出返回值：
        无；capture 调用时 controller 尚未读 desktop，也没有
        guest 文件；prepare 后 getter 返回同一 typed DTO。
    """

    repo_root, cache_root, task = _write_word_text_baseline_fixture(
        tmp_path,
        valid_docx=True,
    )
    calls: list[str] = []
    controller = _Controller(calls)
    _patch_synthetic_word_text_contract(
        monkeypatch,
        repo_root=repo_root,
        task=task,
    )
    original_capture = osworld_environment_module.capture_word_text_baseline

    def _record_capture(**kwargs: Any) -> WordTextBaseline:
        """记录 capture 时序并调用真实构造器。"""

        assert controller.desktop_path_calls == 0
        assert controller.files == {}
        calls.append("word-text.capture")
        return original_capture(**kwargs)

    monkeypatch.setattr(
        osworld_environment_module,
        "capture_word_text_baseline",
        _record_capture,
    )
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
    )

    environment.start()
    environment.prepare(task)
    baseline = environment.operation_word_text_baseline(
        task["task_id"],
        OPERATION_PROTOCOL_ID,
    )

    assert isinstance(baseline, WordTextBaseline)
    assert calls.index("word-text.capture") < calls.index("controller.upload")
    environment.close()


def test_word_text_baseline_parse_error_blocks_all_guest_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 host cache 中 DOCX 不可解析时在 guest 前失败。

    输入参数：
        tmp_path：合成 repo/cache 根。
    输出返回值：
        无；即使 size/SHA 正确，无效 DOCX 也只返固定环境
        错误，且 desktop/上传均未发生。
    """

    repo_root, cache_root, task = _write_word_text_baseline_fixture(
        tmp_path,
        valid_docx=False,
    )
    calls: list[str] = []
    controller = _Controller(calls)
    _patch_synthetic_word_text_contract(
        monkeypatch,
        repo_root=repo_root,
        task=task,
    )
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
    )
    environment.start()

    with pytest.raises(
        OSWorldEnvironmentError,
        match="Operation Word typed baseline 构造失败",
    ) as captured:
        environment.prepare(task)

    assert "Document" not in str(captured.value)
    assert controller.desktop_path_calls == 0
    assert controller.files == {}
    environment.close()


def test_word_text_nonformal_manifest_is_rejected_before_guest_access(
    tmp_path: Path,
) -> None:
    """验证 schema-valid 伪 009 manifest 不能进入 guest。

    输入参数：
        tmp_path：合成 repo/cache 根。
    输出返回值：
        无；即使 host cache 与伪 manifest 的 size/SHA 完全一致，
        整 manifest SHA、路径数或文件闭集不等于正式合同时仍
        必须在 desktop/上传前 ERROR。
    """

    repo_root, cache_root, task = _write_word_text_baseline_fixture(
        tmp_path,
        valid_docx=True,
    )
    calls: list[str] = []
    controller = _Controller(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
    )
    environment.start()

    with pytest.raises(
        OSWorldEnvironmentError,
        match="Operation Word typed baseline 构造失败",
    ):
        environment.prepare(task)

    assert controller.desktop_path_calls == 0
    assert controller.files == {}
    environment.close()
