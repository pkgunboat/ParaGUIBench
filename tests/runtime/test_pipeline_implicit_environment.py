"""pipeline-implicit observation 在 OSWorld 环境中的冻结与清理测试。"""

from __future__ import annotations

from typing import Any

from paraguibench.evaluation.pipeline_implicit import (
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
)
from paraguibench.runtime.osworld_environment import OSWorldTaskEnvironment


class _DockerSession:
    """记录 owned Docker session 的启动和关闭。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享生命周期记录。

        输入参数：calls 为测试阶段列表。
        输出返回值：无。
        """

        self.calls = calls

    def start(self) -> None:
        """记录启动当前 owned session。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("docker.start")

    def close(self) -> None:
        """记录关闭当前 owned session。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("docker.close")


class _Controller:
    """只提供 asset-free 环境准备所需的 ready seam。"""

    def __init__(self, calls: list[str]) -> None:
        """保存共享调用记录。

        输入参数：calls 为阶段列表。
        输出返回值：无。
        """

        self.calls = calls

    def wait_until_ready(self, *, timeout: float) -> None:
        """记录 guest ready 门禁。

        输入参数：timeout 为正数等待上限。
        输出返回值：无。
        """

        assert timeout > 0
        self.calls.append("controller.ready")


class _TaskPrepareSource:
    """为 asset-free 合成任务报告专属 setup 已完成。"""

    def prepare(
        self,
        task: dict[str, Any],
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> bool:
        """跳过通用 Files 窗口而不触发 guest I/O。

        输入参数：
            task/controller/guest_shared_dir：环境传入的已准备上下文。
        输出返回值：始终为 ``True``。
        """

        del task, controller
        assert guest_shared_dir is None
        return True


class _PipelineImplicitSource:
    """返回唯一 typed observation 并记录 capture 次数。"""

    def __init__(self, observation: object) -> None:
        """保存合成观测和调用记录。

        输入参数：observation 为 evaluator-only 合成对象。
        输出返回值：无。
        """

        self.observation = observation
        self.calls: list[tuple[str, str | None]] = []

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> object:
        """记录单次 artifact capture 并返回同一观测。

        输入参数：
            task_id/controller/guest_shared_dir：当前已准备环境身份。
        输出返回值：构造时注入的 observation。
        """

        assert isinstance(controller, _Controller)
        self.calls.append((task_id, guest_shared_dir))
        return self.observation


def test_environment_freezes_one_observation_and_clears_bundle_cache_on_close(
    tmp_path,
    monkeypatch,
) -> None:
    """验证 pipeline-implicit observation 不会跨时点重抓且会随环境清理。

    输入参数：
        tmp_path：pytest 提供的空仓库和缓存根。
        monkeypatch：仅隔离本用例不关心的 production capability
            门禁；该门禁由独立 binding 与 ABA 用例覆盖。
    输出返回值：
        无；重复读取返回同一对象、source 只调一次，close 后
        原始 bundle 缓存为空且 owned Docker 已关闭。
    """

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    calls: list[str] = []
    observation = object()
    source = _PipelineImplicitSource(observation)
    monkeypatch.setattr(
        "paraguibench.runtime.osworld_environment.validate_pipeline_implicit_runtime_capability",
        lambda **_kwargs: None,
    )
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
        task_prepare_source=_TaskPrepareSource(),
        pipeline_implicit_evidence_source=source,
    )
    environment.start()
    environment.prepare(
        {
            "task_id": IMAGE_CLASSIFICATION_TASK_ID,
            "instruction": "synthetic",
        }
    )

    first = environment.pipeline_implicit_observation(
        IMAGE_CLASSIFICATION_TASK_ID,
        IMAGE_CLASSIFICATION_PROTOCOL_ID,
    )
    second = environment.pipeline_implicit_observation(
        IMAGE_CLASSIFICATION_TASK_ID,
        IMAGE_CLASSIFICATION_PROTOCOL_ID,
    )

    assert first is observation
    assert second is observation
    assert source.calls == [(IMAGE_CLASSIFICATION_TASK_ID, None)]
    environment.close()
    assert environment._pipeline_implicit_observation_cache == {}
    assert calls == ["docker.start", "controller.ready", "docker.close"]
