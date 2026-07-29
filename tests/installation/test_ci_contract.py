"""约束公共 CI 的干净 wheel 安装矩阵和无凭据边界。"""

from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "install-and-validate.yml"


def test_ci_installs_built_wheel_on_every_supported_python_without_live_run() -> None:
    """功能：确认 CI 覆盖 Python 3.11–3.13 且从 wheel 做隔离安装。

    输入参数：
        无；读取公开 workflow 文本，不执行 GitHub-hosted runner。
    输出返回值：
        无；断言版本矩阵、wheel 安装、CLI/测试/validator 门禁和无 live E2E。
    """

    workflow = WORKFLOW.read_text(encoding="utf-8")

    for version in ("3.11", "3.12", "3.13"):
        assert f'"{version}"' in workflow
    for required_command in (
        "python -m pip wheel --no-deps --wheel-dir dist .",
        "python -m venv .venv-core",
        "scripts/installation/verify_install.py --profile core",
        "python -m venv .venv-ci",
        "scripts/installation/verify_install.py --profile live-osworld",
        "python -m pytest",
        "scripts/benchmark/validate_release.py --repo-root .",
        "scripts/benchmark/validate_runtime_support.py --repo-root .",
        "scripts/security/scan_repository.py --root .",
    ):
        assert required_command in workflow
    assert "paraguibench run" not in workflow
    assert "PARAGUIBENCH_MODEL_API_KEY" not in workflow
    assert "secrets." not in workflow


def test_installation_ci_pins_actions_to_immutable_commits() -> None:
    """功能：确认安装 CI 不使用可移动 tag 或 branch 引用第三方 Action。

    输入参数：
        无；读取公开安装 workflow。
    输出返回值：
        无；断言每个 ``uses`` 引用均固定到 40 位 Git commit。
    """

    workflow = WORKFLOW.read_text(encoding="utf-8")
    action_references = re.findall(r"^\s*uses:\s*([^#\s]+)", workflow, re.MULTILINE)
    assert action_references
    for reference in action_references:
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference)
