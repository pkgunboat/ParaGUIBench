"""OSWorld artifact component 专属 CLI 安全边界测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import paraguibench.cli.main as main_module
import paraguibench.runtime.osworld_artifact_component_candidate as candidate_module
from paraguibench.runtime.osworld_artifact_component_receipts import (
    OSWorldArtifactComponentReceipt,
)


def _arguments(tmp_path: Path) -> list[str]:
    """构造不含模型或 secret 参数的 candidate CLI argv。

    输入参数：tmp_path 提供绝对缓存、RunStore 和 qcow2 路径。
    输出返回值：可被 parser 完整接收的 argv 列表。
    """

    qcow2 = tmp_path / "Ubuntu.qcow2"
    qcow2.write_bytes(b"test-qcow2")
    return [
        "osworld-artifact",
        "component-validate",
        "--repo-root",
        str(Path(__file__).resolve().parents[2]),
        "--task-id",
        "Operation-FileOperate-BatchOperation-003",
        "--asset-cache-root",
        str(tmp_path / "assets"),
        "--gold-cache-root",
        str(tmp_path / "gold"),
        "--runs-root",
        str(tmp_path / "runs"),
        "--run-id",
        "run-component-cli",
        "--attempt-id",
        "attempt-001",
        "--qcow2-path",
        str(qcow2),
        "--server-port",
        "55031",
        "--vnc-port",
        "58031",
        "--chromium-port",
        "59252",
    ]


@pytest.mark.parametrize("forbidden", ("--model", "--api-key-env", "--base-url-env"))
def test_component_candidate_parser_has_no_model_or_secret_option(
    tmp_path: Path,
    forbidden: str,
) -> None:
    """确认专属 CLI 不存在模型、API key 或 endpoint 接口。

    输入参数：tmp_path 构造基础 argv；forbidden 由 pytest 选择
        三类不应出现的选项。
    输出返回值：argparse 在运行任何 handler 前以 ``SystemExit``
        拒绝该选项。
    """

    with pytest.raises(SystemExit):
        main_module.build_parser().parse_args(
            [*_arguments(tmp_path), forbidden, "private-value"]
        )


def test_component_candidate_cli_outputs_only_canonical_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """确认 CLI 不启动 Agent 且只打印单行脱敏 receipt。

    输入参数：tmp_path 提供配置路径；monkeypatch 仅替换
        top-level live 边界的终态返回；capsys 捕获公开输出。
    输出返回值：退出码 0，且 stdout 只含字段闭合 JSON，
        不含 details/events/path/content/gold/secret/final text。
    """

    receipt = OSWorldArtifactComponentReceipt(
        schema_version=1,
        receipt_kind="paraguibench.osworld.artifact-component.v1",
        task_id="Operation-FileOperate-BatchOperation-003",
        run_id="run-component-cli",
        attempt_id="attempt-001",
        execution_outcome="SUCCEEDED",
        evaluation_outcome="PASSED",
        score=1.0,
        candidate_evaluation_protocol=(
            "paraguibench.osworld.artifact-component-validation.v1"
        ),
        task_evaluation_protocol="paraguibench.osworld.artifact-state.v1",
        environment_protocol="osworld.desktop.v1",
        attempt_version_vector_sha256="1" * 64,
        task_identity_sha256="2" * 64,
        environment_identity_sha256="3" * 64,
        setup_component_sha256="4" * 64,
        getter_component_sha256="5" * 64,
        gold_component_sha256="6" * 64,
    )
    seen: list[Any] = []

    def fake_candidate(config: Any) -> OSWorldArtifactComponentReceipt:
        """记录 CLI 只传入冻结 config 并返回脱敏 receipt。

        输入参数：config 为 CLI 创建的 candidate config。
        输出返回值：固定安全 receipt。
        """

        seen.append(config)
        return receipt

    monkeypatch.setattr(
        main_module,
        "run_osworld_artifact_component_candidate",
        fake_candidate,
    )
    monkeypatch.setattr(
        main_module,
        "_build_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("component candidate 不得启动 Agent")
        ),
    )

    assert main_module.main(_arguments(tmp_path)) == 0
    assert len(seen) == 1
    output = capsys.readouterr().out.strip()
    assert json.loads(output) == receipt.to_dict()
    for forbidden in (
        "final_output",
        "details",
        "events",
        "path",
        "content",
        "gold_value",
        "secret",
        "credential",
        "endpoint",
    ):
        assert forbidden not in output


def test_component_candidate_cli_pending_image_fails_before_external_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """确认正式 pending image 从 CLI 入口在外部副作用前失败。

    输入参数：tmp_path 提供 repo 外占位 qcow2 与应保持不存在的
        RunStore 根；monkeypatch 把 Docker/controller/RunStore/Agent 边界
        全部换成调用即失败的哨兵；capsys 捕获脱敏 CLI 输出。
    输出返回：退出码为 2，仅 stderr 暴露固定异常类型；
        不创建 RunStore，不启动 Docker/controller/Agent，不伪装 114 实机。
    """

    def forbidden_boundary(*_args: Any, **_kwargs: Any) -> Any:
        """拒绝任何本应在 image readiness 之后发生的调用。

        输入参数：忽略的位置与关键字参数。
        输出返回：永不返回；被调用即使回归失败。
        """

        raise AssertionError("pending image 不得进入外部生命周期")

    monkeypatch.setattr(candidate_module, "RunStore", forbidden_boundary)
    monkeypatch.setattr(
        candidate_module,
        "OSWorldAttestedDockerSession",
        forbidden_boundary,
    )
    monkeypatch.setattr(candidate_module, "OSWorldController", forbidden_boundary)
    monkeypatch.setattr(main_module, "_build_agent", forbidden_boundary)

    arguments = _arguments(tmp_path)
    runs_root = tmp_path / "runs"
    assert main_module.main(arguments) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "error=OSWorldArtifactComponentCandidateError"
    assert not runs_root.exists()
