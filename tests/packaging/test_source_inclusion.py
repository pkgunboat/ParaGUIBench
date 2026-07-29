"""验证 Git ignore 规则不会把生产 Python package 排除出 wheel。"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "src" / "paraguibench"


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git check-ignore")
def test_no_production_package_file_is_gitignored() -> None:
    """确认生产 package 文件不会因运行产物规则而从 wheel 中消失。

    输入参数：
        无；枚举 ``src/paraguibench`` 下的全部 Python 源文件。
    输出返回值：
        无；一次性报告被 Git ignore 规则命中的相对路径。
    """

    source_paths = sorted(PACKAGE_ROOT.rglob("*.py"))
    completed = subprocess.run(
        [
            "git",
            "check-ignore",
            "--stdin",
        ],
        cwd=REPO_ROOT,
        input="\n".join(
            path.relative_to(REPO_ROOT).as_posix()
            for path in source_paths
        )
        + "\n",
        check=False,
        capture_output=True,
        text=True,
    )
    ignored_paths = [
        line for line in completed.stdout.splitlines() if line.strip()
    ]

    assert not ignored_paths, (
        "生产 package 文件被 .gitignore 排除：\n"
        + "\n".join(f"- {path}" for path in ignored_paths)
    )
