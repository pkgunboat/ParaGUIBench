"""pipeline implicit component 专属 CLI 安全边界测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import paraguibench.cli.main as main_module
from paraguibench.runtime.pipeline_implicit_component_candidate import (
    PipelineImplicitComponentCandidateConfig,
    PipelineImplicitComponentCandidateError,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    PipelineImplicitComponentReceipt,
)


def _arguments(tmp_path: Path) -> list[str]:
    """构造不含模型、Agent 或 secret 的 candidate CLI argv。

    输入参数：tmp_path 提供 repo 外缓存、RunStore 和 qcow2 路径。
    输出返回值：可由公开 parser 完整接收的参数列表。
    """

    qcow2 = tmp_path / "Ubuntu.qcow2"
    qcow2.write_bytes(b"synthetic-qcow2")
    return [
        "pipeline-implicit",
        "component-validate",
        "--repo-root",
        str(Path(__file__).resolve().parents[2]),
        "--task-id",
        "Operation-FileOperate-BatchOperationPPT-003",
        "--asset-cache-root",
        str(tmp_path / "assets"),
        "--gold-cache-root",
        str(tmp_path / "gold"),
        "--runs-root",
        str(tmp_path / "runs"),
        "--run-id",
        "run-pipeline-component-cli",
        "--attempt-id",
        "attempt-001",
        "--qcow2-path",
        str(qcow2),
        "--server-port",
        "55032",
        "--vnc-port",
        "58032",
        "--chromium-port",
        "59253",
    ]


@pytest.mark.parametrize(
    "forbidden",
    (
        "--model",
        "--api-key",
        "--api-key-env",
        "--base-url-env",
        "--agent-system",
        "--final-output",
        "--proof",
        "--evaluator",
        "--environment",
        "--factory",
        "--image",
        "--receipt-output",
    ),
)
def test_pipeline_component_parser_has_no_injectable_or_secret_option(
    tmp_path: Path,
    forbidden: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """确认专属 CLI 不存在运行依赖注入或凭据参数面。

    输入参数：tmp_path 构造基础 argv；forbidden 为禁止选项；
        capsys 捕获解析器的脱敏输出。
    输出返回值：在 handler 前以 2 拒绝，且不回显伪造私密值。
    """

    sentinel = "SYNTHETIC_PRIVATE_VALUE_NOT_A_SECRET"
    with pytest.raises(SystemExit) as captured_exit:
        main_module.build_parser().parse_args(
            [*_arguments(tmp_path), forbidden, sentinel]
        )

    captured = capsys.readouterr()
    assert captured_exit.value.code == 2
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    assert captured.err.endswith("error=ArgumentParseError\n")


def test_pipeline_component_cli_outputs_only_canonical_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """确认 CLI 只组装冻结 config 并输出一行脱敏 receipt。

    输入参数：tmp_path 提供非敏感路径；monkeypatch 仅替换
        顶层 production candidate 边界；capsys 捕获公开输出。
    输出返回值：退出码 0，候选器仅收到一个正式
        ``PipelineImplicitComponentCandidateConfig``，stdout 仅含字段闭合 JSON。
    """

    receipt = PipelineImplicitComponentReceipt(
        schema_version=1,
        receipt_kind="paraguibench.pipeline-implicit.component.v1",
        task_id="Operation-FileOperate-BatchOperationPPT-003",
        run_id="run-pipeline-component-cli",
        attempt_id="attempt-001",
        execution_outcome="SUCCEEDED",
        evaluation_outcome="PASSED",
        score=1.0,
        candidate_protocol=("paraguibench.pipeline-implicit.component-validation.v1"),
        task_evaluation_protocol=(
            "paraguibench.operation.image-classification.sha256.v1"
        ),
        environment_protocol="osworld.desktop.v1",
        attempt_version_vector_sha256="1" * 64,
        task_identity_sha256="2" * 64,
        environment_identity_sha256="3" * 64,
        component_identity_sha256="4" * 64,
    )
    seen: list[Any] = []

    def fake_candidate(
        config: PipelineImplicitComponentCandidateConfig,
    ) -> PipelineImplicitComponentReceipt:
        """记录 CLI 构造的唯一冻结参数对象。

        输入参数：config 为不含任何可注入运行对象的配置。
        输出返回值：固定字段闭合脱敏 receipt。
        """

        seen.append(config)
        return receipt

    monkeypatch.setattr(
        main_module,
        "run_pipeline_implicit_component_candidate",
        fake_candidate,
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "_build_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pipeline component candidate 不得启动 Agent")
        ),
    )

    assert main_module.main(_arguments(tmp_path)) == 0
    assert len(seen) == 1
    assert type(seen[0]) is PipelineImplicitComponentCandidateConfig
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


def test_pipeline_component_cli_candidate_error_is_value_neutral(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """候选链失败不得回显底层路径、gold 或外部值。

    输入参数：tmp_path 构造完整 argv；monkeypatch 使顶层
        runner 抛固定 candidate error；capsys 捕获公开输出。
    输出返回值：``main`` 返回 2，stdout 为空，stderr 仅含
        异常类型，不产生部分 receipt JSON。
    """

    def fail_candidate(
        _config: PipelineImplicitComponentCandidateConfig,
    ) -> PipelineImplicitComponentReceipt:
        """用固定脱敏异常中止顶层 candidate。

        输入参数：_config 为已校验的冻结配置，不使用。
        输出返回值：无；始终抛出固定 candidate error。
        """

        raise PipelineImplicitComponentCandidateError

    monkeypatch.setattr(
        main_module,
        "run_pipeline_implicit_component_candidate",
        fail_candidate,
    )

    assert main_module.main(_arguments(tmp_path)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=PipelineImplicitComponentCandidateError\n"


def test_pipeline_component_cli_rejects_wrong_receipt_type_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """顶层 runner 返回伪造 DTO 时不得输出部分 JSON。

    输入参数：tmp_path 构造完整 argv；monkeypatch 返回外形
        相似但类型错误的 dict；capsys 捕获公开输出。
    输出返回值：``main`` 返回 2，stdout 为空，stderr 只含
        ``TypeError`` 类型，伪造内容不可见。
    """

    monkeypatch.setattr(
        main_module,
        "run_pipeline_implicit_component_candidate",
        lambda _config: {"secret": "SYNTHETIC_PRIVATE_VALUE_NOT_A_SECRET"},
    )

    assert main_module.main(_arguments(tmp_path)) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=TypeError\n"
    assert "SYNTHETIC_PRIVATE_VALUE_NOT_A_SECRET" not in captured.err
