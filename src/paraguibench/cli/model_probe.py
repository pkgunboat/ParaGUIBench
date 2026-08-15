"""不启动 GUI 环境的模型协议探针 CLI。"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import BytesIO
import logging
import os
import re
import sys

from paraguibench.agents.workers.qwen import (
    QwenActionRejectedError,
    QwenModelConfig,
    QwenModelError,
    QwenOpenAIModel,
)

_DEFAULT_API_KEY_ENV = "PARAGUIBENCH_MODEL_API_KEY"
_DEFAULT_BASE_URL_ENV = "PARAGUIBENCH_MODEL_BASE_URL"
_DEFAULT_MODEL_ENV = "PARAGUIBENCH_MODEL_ID"
_ENVIRONMENT_REFERENCE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{1,127}")


class ProbeConfigurationError(ValueError):
    """表示探针环境引用缺失或对应非敏感配置无效。"""


def add_model_probe_commands(commands: argparse._SubParsersAction) -> None:
    """向顶层 CLI 注册不触及 VM 的模型协议探针。

    输入参数：
        commands：``paraguibench`` 的顶层 argparse 子命令集合。
    输出返回值：
        无；原地添加 ``model-probe qwen-native`` 命令。
    """

    model_probe = commands.add_parser(
        "model-probe",
        help="不启动 VM 验证模型工具调用协议",
    )
    probe_commands = model_probe.add_subparsers(
        dest="model_probe_command",
        required=True,
    )
    qwen_native = probe_commands.add_parser(
        "qwen-native",
        help="验证 Qwen 原生 computer_use 协议",
    )
    qwen_native.add_argument(
        "--api-key-env",
        default=_DEFAULT_API_KEY_ENV,
    )
    qwen_native.add_argument(
        "--base-url-env",
        default=_DEFAULT_BASE_URL_ENV,
    )
    qwen_native.add_argument(
        "--model-env",
        default=_DEFAULT_MODEL_ENV,
    )
    qwen_native.set_defaults(handler=handle_qwen_native_model_probe)


def handle_qwen_native_model_probe(arguments: argparse.Namespace) -> int:
    """通过 production Qwen adapter 执行一次原生协议探针。

    输入参数：
        arguments：只含 API key、base URL 和 model 环境变量名的
            argparse namespace，不包含它们的值。
    输出返回值：
        0 表示服务返回了可解析的唯一 GUI 动作；返回动作
        本身被丢弃，不会执行。
    """

    try:
        config = _build_qwen_native_probe_config(arguments)
    except ProbeConfigurationError:
        print("FAIL ProbeConfigurationError", file=sys.stderr)
        return 2
    except Exception:
        print("FAIL ProbeInternalError", file=sys.stderr)
        return 2
    try:
        with _silence_model_sdk_output():
            QwenOpenAIModel(config).next_action(
                instruction=(
                    "Protocol probe only. Inspect the blank image and call "
                    "computer_use exactly once with a wait action."
                ),
                screenshot=_build_blank_probe_png(),
                step_index=1,
                action_history=(),
            )
    except QwenActionRejectedError:
        print("FAIL QwenActionRejectedError", file=sys.stderr)
        return 2
    except QwenModelError:
        print("FAIL QwenModelError", file=sys.stderr)
        return 2
    except Exception:
        print("FAIL ProbeInternalError", file=sys.stderr)
        return 2
    print("PASS qwen-native-computer-use")
    return 0


def _build_qwen_native_probe_config(
    arguments: argparse.Namespace,
) -> QwenModelConfig:
    """从三个环境变量名构造有界的 native Qwen 探针配置。

    输入参数：
        arguments：只携带 key、base URL 和 model 环境变量名的
            argparse namespace。
    输出返回值：
        关闭 thinking、固定 native 协议与最小成本边界的
        ``QwenModelConfig``。
    异常：
        ProbeConfigurationError：引用缺失或解析后配置无效。
    """

    try:
        environment_references = (
            arguments.api_key_env,
            arguments.base_url_env,
            arguments.model_env,
        )
        if any(
            not isinstance(reference, str)
            or _ENVIRONMENT_REFERENCE_PATTERN.fullmatch(reference) is None
            for reference in environment_references
        ):
            raise ProbeConfigurationError
        if not os.environ.get(arguments.api_key_env):
            raise ProbeConfigurationError
        return QwenModelConfig(
            base_url=os.environ[arguments.base_url_env],
            model=os.environ[arguments.model_env],
            api_key_env=arguments.api_key_env,
            max_output_tokens=64,
            max_image_pixels=1024,
            max_history_image_pixels=1024,
            enable_thinking=False,
            request_timeout_seconds=30.0,
            history_limit=0,
            tool_protocol="native",
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ProbeConfigurationError from None


@contextmanager
def _silence_model_sdk_output() -> Iterator[None]:
    """在模型边界内丢弃 SDK 的 stdout、stderr 和 logging 输出。

    输入参数：
        无。
    输出返回值：
        上下文管理器；退出时恢复进入前的全局日志禁用级别。
        丢弃的内容直接写入系统 null device，不在内存或磁盘保存。
    """

    previous_logging_disable = logging.root.manager.disable
    logging.disable(sys.maxsize)
    try:
        with open(os.devnull, "w", encoding="utf-8") as null_stream:
            with redirect_stdout(null_stream), redirect_stderr(null_stream):
                yield
    finally:
        logging.disable(previous_logging_disable)


def _build_blank_probe_png() -> bytes:
    """在内存中生成探针专用的 32×32 空白 PNG。

    输入参数：
        无。
    输出返回值：
        白色 RGB PNG 字节；函数不创建任何文件。
    """

    from PIL import Image

    buffer = BytesIO()
    image = Image.new("RGB", (32, 32), color=(255, 255, 255))
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
