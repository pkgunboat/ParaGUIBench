"""Qwen native computer_use 无 VM 模型探针的 CLI 行为测试。"""

from __future__ import annotations

import argparse
import base64
import logging
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from paraguibench.cli.main import build_parser, main


class _RecordingCompletions:
    """记录单次 OpenAI-compatible 请求并返回固定工具调用。"""

    def __init__(self) -> None:
        """初始化空请求记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> dict[str, Any]:
        """保存协议请求并模拟合法的 wait 动作。

        输入参数：
            request：production adapter 构造的 Chat Completions 参数。
        输出返回值：
            只含一个 ``computer_use`` 工具调用的响应。
        """

        self.requests.append(request)
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "computer_use",
                                    "arguments": ('{"action":"wait","time":0}'),
                                }
                            }
                        ],
                        "content": None,
                    }
                }
            ]
        }


def test_qwen_native_model_probe_cli_accepts_only_environment_references() -> None:
    """验证探针参数面不接受 key、base URL 或 model 值。

    输入参数：
        无；检查公开 argparse 命令树。
    输出返回值：
        无；``qwen-native`` 只提供三个环境变量名选项。
    """

    parser = build_parser()
    root_commands = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    model_probe_parser = root_commands.choices["model-probe"]
    probe_commands = next(
        action
        for action in model_probe_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    qwen_native_parser = probe_commands.choices["qwen-native"]
    option_strings = {
        option
        for action in qwen_native_parser._actions
        for option in action.option_strings
    }

    assert option_strings == {
        "-h",
        "--help",
        "--api-key-env",
        "--base-url-env",
        "--model-env",
    }
    assert "--api-key" not in option_strings
    assert "--base-url" not in option_strings
    assert "--model" not in option_strings


def test_qwen_native_model_probe_uses_production_protocol_and_prints_pass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证无 VM 探针经 production Qwen adapter 发送原生工具请求。

    输入参数：
        monkeypatch：注入模型服务边界 fake 与三个环境变量。
        capsys：捕获 CLI 对外公开的固定状态行。
    输出返回值：
        无；命令只输出 PASS，且请求使用 32×32 内存 PNG
        和强制的单一 ``computer_use`` 协议。
    """

    completions = _RecordingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("PROBE_QWEN_KEY", "sentinel-secret-key")
    monkeypatch.setenv(
        "PROBE_QWEN_BASE_URL",
        "https://sentinel.endpoint.example/compatible-mode/v1",
    )
    monkeypatch.setenv("PROBE_QWEN_MODEL", "sentinel-model-id")

    def fake_client_factory(**_: Any) -> Any:
        """返回只记录请求的模型服务边界 fake。

        输入参数：
            _：production adapter 传入的凭据、endpoint 与超时配置。
        输出返回值：
            具备 ``chat.completions.create`` 的测试 client。
        """

        return client

    monkeypatch.setattr(
        "paraguibench.agents.workers.qwen.model.create_openai_compatible_qwen_client",
        fake_client_factory,
    )

    exit_code = main(
        [
            "model-probe",
            "qwen-native",
            "--api-key-env",
            "PROBE_QWEN_KEY",
            "--base-url-env",
            "PROBE_QWEN_BASE_URL",
            "--model-env",
            "PROBE_QWEN_MODEL",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "PASS qwen-native-computer-use\n"
    assert captured.err == ""
    assert len(completions.requests) == 1
    request = completions.requests[0]
    assert request["model"] == "sentinel-model-id"
    assert request["tool_choice"] == {
        "type": "function",
        "function": {"name": "computer_use"},
    }
    assert request["parallel_tool_calls"] is False
    assert request["stream"] is False
    image_parts = [
        part
        for part in request["messages"][1]["content"]
        if part["type"] == "image_url"
    ]
    encoded_png = image_parts[0]["image_url"]["url"].split(",", 1)[1]
    png = base64.b64decode(encoded_png, validate=True)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", png[16:24]) == (32, 32)


def test_qwen_native_model_probe_has_no_runtime_or_filesystem_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """验证探针不启动 VM/controller/worker/RunStore 且不执行动作。

    输入参数：
        monkeypatch：注入成功模型边界，并将所有运行时边界换成禁止调用。
        capsys：捕获探针的固定 PASS 行。
        tmp_path：作为空工作目录，用于观测任何文件副作用。
    输出返回值：
        无；唯一外部行为是被 fake 承接的单次模型协议请求。
    """

    completions = _RecordingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setenv("PARAGUIBENCH_MODEL_API_KEY", "sentinel-secret-key")
    monkeypatch.setenv(
        "PARAGUIBENCH_MODEL_BASE_URL",
        "https://sentinel.endpoint.example/compatible-mode/v1",
    )
    monkeypatch.setenv("PARAGUIBENCH_MODEL_ID", "sentinel-model-id")
    monkeypatch.chdir(tmp_path)

    def fake_client_factory(**_: Any) -> Any:
        """返回唯一允许的外部模型服务 fake。

        输入参数：
            _：production adapter 构造 client 所需的内存参数。
        输出返回值：
            记录单次请求的 fake client。
        """

        return client

    def forbidden_runtime_boundary(*_: Any, **__: Any) -> None:
        """在探针误触任何 GUI 或持久化边界时立即失败。

        输入参数：
            _：误调用边界的位置参数。
            __：误调用边界的关键字参数。
        输出返回值：
            无；始终抛出 AssertionError。
        """

        raise AssertionError("runtime boundary must remain unused")

    monkeypatch.setattr(
        "paraguibench.agents.workers.qwen.model.create_openai_compatible_qwen_client",
        fake_client_factory,
    )
    for boundary in (
        "paraguibench.cli.main.OSWorldController",
        "paraguibench.cli.main.OSWorldDockerSession",
        "paraguibench.cli.main.OSWorldTaskEnvironment",
        "paraguibench.cli.main.QwenGUIWorker",
        "paraguibench.cli.main.AttemptRunner",
        "paraguibench.cli.main.RunStore",
    ):
        monkeypatch.setattr(boundary, forbidden_runtime_boundary)

    exit_code = main(["model-probe", "qwen-native"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "PASS qwen-native-computer-use\n"
    assert captured.err == ""
    assert len(completions.requests) == 1
    assert list(tmp_path.iterdir()) == []


def test_qwen_native_model_probe_silences_sdk_failure_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """验证 SDK 的输出、日志和异常内容都不能越过探针边界。

    输入参数：
        monkeypatch：注入会输出敏感哨兵值的模型服务 fake。
        capsys：捕获 CLI 标准输出与标准错误。
        caplog：捕获 SDK logger 可见的日志记录。
    输出返回值：
        无；失败时仅 stderr 含固定 QwenModelError 状态，
        不含 key、endpoint、响应、日志或 traceback。
    """

    sensitive_marker = "sentinel-secret-key"
    endpoint = "https://sentinel.endpoint.example/compatible-mode/v1"
    raw_response = "sentinel-provider-response"
    monkeypatch.setenv("PROBE_QWEN_KEY", sensitive_marker)
    monkeypatch.setenv("PROBE_QWEN_BASE_URL", endpoint)
    monkeypatch.setenv("PROBE_QWEN_MODEL", "sentinel-model-id")

    class _NoisyFailingCompletions:
        """模拟同时写三种输出通道的失败 SDK 边界。"""

        def create(self, **_: Any) -> None:
            """输出哨兵值后抛出含原始响应的异常。

            输入参数：
                _：production adapter 构造的请求参数。
            输出返回值：
                无；始终抛出 RuntimeError。
            """

            print(sensitive_marker)
            print(endpoint, file=sys.stderr)
            logging.getLogger("openai").log(
                logging.CRITICAL + 10,
                raw_response,
            )
            raise RuntimeError(raw_response)

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_NoisyFailingCompletions())
    )

    def fake_client_factory(**_: Any) -> Any:
        """返回故意失败的外部服务 fake。

        输入参数：
            _：仅在探针内存中传递的 client 构造参数。
        输出返回值：
            具备失败 ``chat.completions.create`` 的 fake client。
        """

        return client

    monkeypatch.setattr(
        "paraguibench.agents.workers.qwen.model.create_openai_compatible_qwen_client",
        fake_client_factory,
    )

    exit_code = main(
        [
            "model-probe",
            "qwen-native",
            "--api-key-env",
            "PROBE_QWEN_KEY",
            "--base-url-env",
            "PROBE_QWEN_BASE_URL",
            "--model-env",
            "PROBE_QWEN_MODEL",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "FAIL QwenModelError\n"
    assert caplog.records == []
    combined = captured.out + captured.err + caplog.text
    assert sensitive_marker not in combined
    assert endpoint not in combined
    assert raw_response not in combined


def test_qwen_native_model_probe_reports_rejected_protocol_without_response(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证非 native tool 响应只暴露固定协议拒绝类型。

    输入参数：
        monkeypatch：注入不含 tool call 的外部服务 fake。
        capsys：捕获探针唯一可见的固定失败行。
    输出返回值：
        无；原始响应哨兵值不出现在 stdout 或 stderr。
    """

    raw_response = "sentinel-unstructured-provider-response"
    monkeypatch.setenv("PROBE_QWEN_KEY", "sentinel-secret-key")
    monkeypatch.setenv(
        "PROBE_QWEN_BASE_URL",
        "https://sentinel.endpoint.example/compatible-mode/v1",
    )
    monkeypatch.setenv("PROBE_QWEN_MODEL", "sentinel-model-id")
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": None,
                    "content": raw_response,
                }
            }
        ]
    }
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))
    )

    def fake_client_factory(**_: Any) -> Any:
        """返回响应结构不符合 native 协议的 fake client。

        输入参数：
            _：production adapter 传入的 client 构造参数。
        输出返回值：
            可返回非结构化响应的 fake client。
        """

        return client

    monkeypatch.setattr(
        "paraguibench.agents.workers.qwen.model.create_openai_compatible_qwen_client",
        fake_client_factory,
    )

    exit_code = main(
        [
            "model-probe",
            "qwen-native",
            "--api-key-env",
            "PROBE_QWEN_KEY",
            "--base-url-env",
            "PROBE_QWEN_BASE_URL",
            "--model-env",
            "PROBE_QWEN_MODEL",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "FAIL QwenActionRejectedError\n"
    assert raw_response not in captured.err


def test_qwen_native_model_probe_reports_missing_environment_as_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证必需环境引用缺失时只返回固定配置错误。

    输入参数：
        monkeypatch：保留 model/key 引用并显式删除 base URL 引用。
        capsys：捕获固定的 CLI 失败状态。
    输出返回值：
        无；命令不启动模型 client，且不输出环境变量名或值。
    """

    monkeypatch.setenv("PARAGUIBENCH_MODEL_API_KEY", "sentinel-secret-key")
    monkeypatch.setenv("PARAGUIBENCH_MODEL_ID", "sentinel-model-id")
    monkeypatch.delenv("PARAGUIBENCH_MODEL_BASE_URL", raising=False)

    exit_code = main(["model-probe", "qwen-native"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "FAIL ProbeConfigurationError\n"
    assert "PARAGUIBENCH_MODEL_BASE_URL" not in captured.err
    assert "sentinel" not in captured.err


def test_qwen_native_model_probe_preflights_missing_key_reference(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证 key 环境引用缺失时在外部 client 创建前固定失败。

    输入参数：
        monkeypatch：提供 base URL/model，并删除默认 key 环境变量。
        capsys：捕获 CLI 的固定配置失败行。
    输出返回值：
        无；输出不区分环境变量名、值或具体缺失项。
    """

    monkeypatch.delenv("PARAGUIBENCH_MODEL_API_KEY", raising=False)
    monkeypatch.setenv(
        "PARAGUIBENCH_MODEL_BASE_URL",
        "https://sentinel.endpoint.example/compatible-mode/v1",
    )
    monkeypatch.setenv("PARAGUIBENCH_MODEL_ID", "sentinel-model-id")

    exit_code = main(["model-probe", "qwen-native"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "FAIL ProbeConfigurationError\n"
    assert "PARAGUIBENCH_MODEL_API_KEY" not in captured.err
    assert "sentinel" not in captured.err


def test_qwen_native_model_probe_redacts_unexpected_local_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证非预期本地依赖异常也只暴露固定内部错误。

    输入参数：
        monkeypatch：注入合法环境，并使内存 PNG 构造抛出敏感异常。
        capsys：捕获 CLI 的唯一固定失败行。
    输出返回值：
        无；异常类型、消息和 traceback 都不对外可见。
    """

    sentinel = "sentinel-local-image-failure"
    monkeypatch.setenv("PARAGUIBENCH_MODEL_API_KEY", "sentinel-secret-key")
    monkeypatch.setenv(
        "PARAGUIBENCH_MODEL_BASE_URL",
        "https://sentinel.endpoint.example/compatible-mode/v1",
    )
    monkeypatch.setenv("PARAGUIBENCH_MODEL_ID", "sentinel-model-id")

    def fail_image_construction(*_: Any, **__: Any) -> None:
        """模拟图片依赖在内存构造阶段失败。

        输入参数：
            _：Pillow 传入的位置参数。
            __：Pillow 传入的关键字参数。
        输出返回值：
            无；始终抛出 RuntimeError。
        """

        raise RuntimeError(sentinel)

    monkeypatch.setattr("PIL.Image.new", fail_image_construction)

    exit_code = main(["model-probe", "qwen-native"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "FAIL ProbeInternalError\n"
    assert sentinel not in captured.err
    assert "RuntimeError" not in captured.err


def test_qwen_native_model_probe_rejects_noncanonical_environment_name(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证 base URL 选项只接受规范环境变量名而不是值。

    输入参数：
        monkeypatch：创建小写非规范引用，并安装不得触发的 client fake。
        capsys：捕获不含输入内容的固定配置失败。
    输出返回值：
        无；非规范引用在读取其值或创建 SDK client 前被拒绝。
    """

    invalid_reference = "sentinel_base_url"
    monkeypatch.setenv("PROBE_QWEN_KEY", "sentinel-secret-key")
    monkeypatch.setenv(invalid_reference, "https://endpoint.example/v1")
    monkeypatch.setenv("PROBE_QWEN_MODEL", "sentinel-model-id")

    def forbidden_client_factory(**_: Any) -> Any:
        """在非规范环境引用越过预检时立即使测试失败。

        输入参数：
            _：不应被构造的 client 参数。
        输出返回值：
            无；始终抛出 AssertionError。
        """

        raise AssertionError("model client must not be created")

    monkeypatch.setattr(
        "paraguibench.agents.workers.qwen.model.create_openai_compatible_qwen_client",
        forbidden_client_factory,
    )

    exit_code = main(
        [
            "model-probe",
            "qwen-native",
            "--api-key-env",
            "PROBE_QWEN_KEY",
            "--base-url-env",
            invalid_reference,
            "--model-env",
            "PROBE_QWEN_MODEL",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "FAIL ProbeConfigurationError\n"
    assert invalid_reference not in captured.err
