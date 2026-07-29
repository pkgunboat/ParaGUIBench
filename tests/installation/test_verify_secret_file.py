"""验证仓库外 secret 文件的权限门禁和脱敏输出。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = (
    REPO_ROOT / "scripts" / "installation" / "verify_secret_file.py"
)


def test_secure_external_secret_file_passes_without_reading_its_value(
    tmp_path: Path,
) -> None:
    """功能：确认仓库外 0600 普通文件通过检查且内容不进入终端。

    输入参数：
        tmp_path：pytest 提供的仓库外临时目录。
    输出返回值：
        无；断言公开脚本的退出码、稳定输出及 sentinel 不可见性。
    """

    sentinel = "SYNTHETIC_SECRET_FILE_SENTINEL_DO_NOT_PRINT"
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text(
        f"PARAGUIBENCH_MODEL_API_KEY={sentinel}\n",
        encoding="utf-8",
    )
    secret_file.chmod(0o600)

    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--secret-file",
            str(secret_file),
            "--checkout-root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "PASS file-exists",
        "PASS file-regular",
        "PASS file-owner",
        "PASS file-mode-0600",
        "PASS file-outside-checkout",
        "PASS secret-file",
    ]
    assert completed.stderr == ""
    assert sentinel not in completed.stdout
    assert str(secret_file) not in completed.stdout


def test_insecure_mode_fails_without_printing_file_or_value(
    tmp_path: Path,
) -> None:
    """功能：确认权限过宽时以固定检查标识失败且仍不读取内容。

    输入参数：
        tmp_path：pytest 提供的仓库外临时目录。
    输出返回值：
        无；断言只有 mode 与汇总项失败，路径和值均不可见。
    """

    sentinel = "SYNTHETIC_INSECURE_SENTINEL_DO_NOT_PRINT"
    secret_file = tmp_path / "insecure.env"
    secret_file.write_text(sentinel, encoding="utf-8")
    secret_file.chmod(0o644)

    completed = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--secret-file",
            str(secret_file),
            "--checkout-root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stdout.splitlines() == [
        "PASS file-exists",
        "PASS file-regular",
        "PASS file-owner",
        "FAIL file-mode-0600",
        "PASS file-outside-checkout",
        "FAIL secret-file",
    ]
    assert completed.stderr == ""
    assert sentinel not in completed.stdout
    assert str(secret_file) not in completed.stdout
