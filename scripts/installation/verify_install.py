#!/usr/bin/env python3
"""以稳定、脱敏的 PASS/FAIL 行验证 ParaGUIBench 安装结果。"""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import importlib
import os
import subprocess
import sys
from typing import Callable, Sequence


@dataclass(frozen=True, slots=True)
class CheckResult:
    """保存一项不会携带底层异常或环境值的安装检查结果。"""

    check_id: str
    passed: bool


def _import_without_output(module_name: str) -> bool:
    """功能：导入模块并丢弃第三方初始化阶段产生的全部终端输出。

    输入参数：
        module_name：由验证器内部固定表提供的 Python 顶层模块名。
    输出返回值：
        模块成功导入返回 ``True``，任意导入异常返回 ``False``；输出、警告和
        异常内容直接写入空设备，不在内存中保留或转发。
    """

    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with redirect_stdout(sink), redirect_stderr(sink):
                importlib.import_module(module_name)
    except BaseException:
        return False
    return True


def _check_python_version() -> CheckResult:
    """功能：检查当前解释器是否位于项目声明的 Python 版本区间。

    输入参数：
        无；读取当前解释器的主、次版本号。
    输出返回值：
        ``python-version`` 检查结果；不会包含解释器路径或完整版本字符串。
    """

    supported = (3, 11) <= sys.version_info[:2] < (3, 14)
    return CheckResult("python-version", supported)


def _check_package_import() -> CheckResult:
    """功能：检查当前解释器能否导入 ParaGUIBench 顶层 package。

    输入参数：
        无；使用当前解释器的正常 import 解析规则。
    输出返回值：
        ``package-import`` 检查结果；导入异常类型和内容均不会写入输出。
    """

    return CheckResult(
        "package-import",
        _import_without_output("paraguibench"),
    )


def _check_cli_help() -> CheckResult:
    """功能：检查安装后的模块 CLI 能否安全展示帮助并正常退出。

    输入参数：
        无；使用当前 Python 解释器执行 ``python -m paraguibench --help``。
    输出返回值：
        ``cli-help`` 检查结果；子进程的 stdout、stderr 和异常均被丢弃。
    """

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "paraguibench", "--help"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return CheckResult("cli-help", False)
    return CheckResult("cli-help", completed.returncode == 0)


def _check_dependency(module_name: str, check_id: str) -> CheckResult:
    """功能：检查 live-osworld 声明的单个可选依赖能否导入。

    输入参数：
        module_name：Python 顶层模块名，仅由脚本内固定表提供。
        check_id：用于稳定输出的非敏感检查标识。
    输出返回值：
        指定依赖的检查结果；不会输出版本、安装位置或导入异常。
    """

    return CheckResult(check_id, _import_without_output(module_name))


def verify_profile(profile: str) -> tuple[CheckResult, ...]:
    """功能：按固定顺序运行一个公开安装 profile 的全部检查。

    输入参数：
        profile：支持 ``core`` 和 ``live-osworld``。
    输出返回值：
        不可变的脱敏检查结果序列，最后一项为 profile 汇总状态。
    """

    if profile not in {"core", "live-osworld"}:
        return (CheckResult(f"profile-{profile}", False),)

    core_checks: tuple[Callable[[], CheckResult], ...] = (
        _check_python_version,
        _check_package_import,
        _check_cli_help,
    )
    results = tuple(check() for check in core_checks)
    if profile == "live-osworld":
        dependency_checks = (
            _check_dependency("openai", "dependency-openai"),
            _check_dependency("PIL", "dependency-pillow"),
            _check_dependency("requests", "dependency-requests"),
        )
        results += dependency_checks
    return results + (
        CheckResult(f"profile-{profile}", all(result.passed for result in results)),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """功能：构造只接受预定义 profile 名称的参数解析器。

    输入参数：
        无。
    输出返回值：
        支持 ``--profile`` 的 ``ArgumentParser``；不接受 secret 或路径值。
    """

    parser = argparse.ArgumentParser(
        description="Verify a ParaGUIBench installation without printing context."
    )
    parser.add_argument(
        "--profile",
        choices=("core", "live-osworld"),
        required=True,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """功能：执行安装检查并只打印稳定 PASS/FAIL 行。

    输入参数：
        argv：可选命令行参数；``None`` 时读取当前进程参数。
    输出返回值：
        全部检查通过返回 0，否则返回 1；从不输出路径、环境变量或异常文本。
    """

    arguments = build_argument_parser().parse_args(argv)
    results = verify_profile(arguments.profile)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.check_id}")
    return 0 if results[-1].passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
