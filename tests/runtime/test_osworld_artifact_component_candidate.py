"""OSWorld artifact component 专属 top-level candidate 测试。"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from paraguibench.runtime.osworld_artifact_component_candidate import (
    OSWorldArtifactComponentCandidateConfig,
    OSWorldArtifactComponentCandidateError,
    run_osworld_artifact_component_candidate,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path, *, task_id: str) -> OSWorldArtifactComponentCandidateConfig:
    """构造不含凭据、Agent 或可注入依赖的 candidate 配置。

    输入参数：tmp_path 提供 repo 外 RunStore/缓存/qcow2 路径；
        task_id 为待验证任务。
    输出返回值：字段形状合法的冻结 candidate config。
    """

    qcow2 = tmp_path / "Ubuntu.qcow2"
    qcow2.write_bytes(b"local-placeholder-not-a-live-image")
    return OSWorldArtifactComponentCandidateConfig(
        repo_root=REPO_ROOT,
        runs_root=tmp_path / "runs",
        asset_cache_root=tmp_path / "assets",
        gold_cache_root=tmp_path / "gold",
        qcow2_path=qcow2,
        task_id=task_id,
        run_id="run-component-candidate",
        attempt_id="attempt-001",
        server_port=55021,
        vnc_port=58021,
        chromium_port=59242,
    )


def test_candidate_api_has_no_agent_or_dependency_injection_parameters() -> None:
    """确认 production 发证入口只接受冻结 config。

    输入参数：无；读取公开函数签名与 config 字段。
    输出返回值：入口无 Agent/final text/evaluator/environment/controller/
        Docker factory/HTTP session/proof 注入面。
    """

    assert tuple(
        inspect.signature(run_osworld_artifact_component_candidate).parameters
    ) == ("config",)
    config_fields = set(OSWorldArtifactComponentCandidateConfig.__dataclass_fields__)
    assert not config_fields.intersection(
        {
            "agent",
            "final_text",
            "evaluator",
            "environment",
            "controller",
            "docker_session",
            "session_factory",
            "http_session",
            "proof",
        }
    )


def test_candidate_current_pending_image_fails_before_runstore_write(
    tmp_path: Path,
) -> None:
    """确认未固定 extracted SHA 的 current manifest 在 RunStore/VM 前失败。

    输入参数：tmp_path 提供不会被当成已验镜像的占位文件。
    输出返回值：发证入口抛固定错误，且不创建 runs 目录；
        本地不会伪装 114 实机成功。
    """

    config = _config(
        tmp_path,
        task_id="Operation-FileOperate-BatchOperation-003",
    )

    with pytest.raises(OSWorldArtifactComponentCandidateError) as captured:
        run_osworld_artifact_component_candidate(config)

    assert str(captured.value) == "OSWORLD_ARTIFACT_COMPONENT_CANDIDATE_INVALID"
    assert not config.runs_root.exists()


def test_candidate_config_rejects_identity_only_settings(
    tmp_path: Path,
) -> None:
    """确认 Settings-001 仅进入本地身份闭集，不进入可执行 candidate。

    输入参数：tmp_path 只用于构造非敏感路径。
    输出返回值：构造阶段即抛固定 candidate 错误，不误清 live 门禁。
    """

    with pytest.raises(OSWorldArtifactComponentCandidateError):
        _config(
            tmp_path,
            task_id="Operation-FileOperate-Settings-001",
        )
