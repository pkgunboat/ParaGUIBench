"""从公开命令入口验证安装检查器的稳定、脱敏输出契约。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import venv


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "installation" / "verify_install.py"


def test_core_profile_reports_only_stable_pass_lines() -> None:
    """功能：确认 core 安装成功时只输出固定检查标识，不泄露运行上下文。

    输入参数：
        无；子进程通过仓库 ``src`` 暴露待验证 package，并注入不可输出的
        synthetic sentinel 环境变量。
    输出返回值：
        无；通过退出码和完整 stdout/stderr 断言公开 CLI 行为。
    """

    sentinel = "SYNTHETIC_INSTALL_SENTINEL_DO_NOT_PRINT"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    environment["PARAGUIBENCH_MODEL_API_KEY"] = sentinel

    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--profile",
            "core",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "PASS python-version",
        "PASS package-import",
        "PASS cli-help",
        "PASS profile-core",
    ]
    assert completed.stderr == ""
    assert sentinel not in completed.stdout
    assert str(REPO_ROOT) not in completed.stdout


def test_live_osworld_profile_verifies_declared_optional_dependencies() -> None:
    """功能：确认 live-osworld profile 验证三个已声明的可选运行依赖。

    输入参数：
        无；当前项目测试环境已安装 ``pyproject.toml`` 的 live 依赖。
    输出返回值：
        无；完整输出必须是固定、无版本号与无本地路径的 PASS 行。
    """

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--profile",
            "live-osworld",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "PASS python-version",
        "PASS package-import",
        "PASS cli-help",
        "PASS dependency-openai",
        "PASS dependency-pillow",
        "PASS dependency-requests",
        "PASS profile-live-osworld",
    ]
    assert completed.stderr == ""


def test_failed_core_check_does_not_print_import_error_or_context(
    tmp_path: Path,
) -> None:
    """功能：确认 package 不可用时仍只输出稳定 FAIL 行。

    输入参数：
        tmp_path：用于创建不含 ParaGUIBench 的空白标准 venv。
    输出返回值：
        无；从空白解释器运行验证器并断言失败状态不带路径或底层异常。
    """

    empty_venv = tmp_path / "empty-venv"
    venv.EnvBuilder(with_pip=False).create(empty_venv)
    empty_python = empty_venv / "bin" / "python"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [
            str(empty_python),
            str(VERIFY_SCRIPT),
            "--profile",
            "core",
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stdout.splitlines() == [
        "PASS python-version",
        "FAIL package-import",
        "FAIL cli-help",
        "FAIL profile-core",
    ]
    assert completed.stderr == ""
    assert str(REPO_ROOT) not in completed.stdout
    assert "ModuleNotFoundError" not in completed.stdout


def test_dependency_import_output_is_suppressed(
    tmp_path: Path,
) -> None:
    """功能：确认第三方模块导入时产生的输出不会破坏脱敏协议。

    输入参数：
        tmp_path：用于放置会向 stdout/stderr 写入 sentinel 的 fake openai 模块。
    输出返回值：
        无；live-osworld profile 仍只能输出固定 PASS 行。
    """

    sentinel = "SYNTHETIC_IMPORT_OUTPUT_DO_NOT_PRINT"
    (tmp_path / "openai.py").write_text(
        "import sys\n"
        f"print({sentinel!r})\n"
        f"sys.stderr.write({sentinel!r})\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(tmp_path), str(REPO_ROOT / "src"))
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--profile",
            "live-osworld",
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "PASS python-version",
        "PASS package-import",
        "PASS cli-help",
        "PASS dependency-openai",
        "PASS dependency-pillow",
        "PASS dependency-requests",
        "PASS profile-live-osworld",
    ]
    assert completed.stderr == ""
    assert sentinel not in completed.stdout
