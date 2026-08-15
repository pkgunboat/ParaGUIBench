"""约束公开安装文档的分层、wheel 和凭据安全协议。"""

from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_DOC = REPO_ROOT / "INSTALL.md"
DEPENDENCY_DOC = REPO_ROOT / "docs" / "installation" / "dependency-tree.md"
CHINESE_INSTALL_DOC = REPO_ROOT / "docs" / "installation" / "zh-CN.md"
DEPLOYMENT_DOC = REPO_ROOT / "docs" / "deployment" / "osworld-linux.md"
EVALUATION_DOC = REPO_ROOT / "docs" / "evaluation" / "protocol.md"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture" / "dependency-tree.md"


def test_install_docs_define_clean_core_and_live_osworld_paths() -> None:
    """功能：确认其他用户可从文档得到两层干净 wheel 安装路径。

    输入参数：
        无；读取公开安装入口和安装依赖树。
    输出返回值：
        无；断言 Python 范围、venv/wheel、验证器、secret 边界和无 conda 依赖。
    """

    install_text = INSTALL_DOC.read_text(encoding="utf-8")
    dependency_text = DEPENDENCY_DOC.read_text(encoding="utf-8")
    combined = f"{install_text}\n{dependency_text}"

    for required_text in (
        "Python 3.11–3.13",
        "Core",
        "Live OSWorld",
        "python3 -m venv",
        "python -m pip wheel",
        "verify_install.py --profile core",
        "--profile live-osworld",
        "verify_secret_file.py",
        "0600",
        "secret manager",
        "hatchling",
        "openai",
        "Pillow",
        "requests",
        "Playwright",
        "pytest",
    ):
        assert required_text in combined
    assert "conda" not in combined.lower()
    assert "base environment" not in combined.lower()
    assert (
        re.search(
            r"PARAGUIBENCH_MODEL_API_KEY\s*=\s*\S+",
            combined,
        )
        is None
    )


def test_chinese_install_guide_preserves_the_same_security_boundary() -> None:
    """功能：确认中文安装指南覆盖两层安装、干净 wheel 与凭据边界。

    输入参数：
        无；读取中文公开指南。
    输出返回值：
        无；关键命令和安全协议缺失时失败，且不得引入旧环境依赖。
    """

    guide = CHINESE_INSTALL_DOC.read_text(encoding="utf-8")

    for required_text in (
        "Core",
        "Live OSWorld",
        "Python 3.11–3.13",
        "pip wheel",
        "--profile core",
        "--profile live-osworld",
        "0600",
        "secret manager",
        "不执行真实 GUI E2E",
    ):
        assert required_text in guide
    assert "conda" not in guide.lower()


def test_install_and_deployment_docs_preserve_evaluator_gold_boundary() -> None:
    """功能：防止 input assets 与 evaluator-only gold 再次混写。

    输入参数：
        无；读取英文安装、中文安装、部署、评价与架构文档。
    输出返回值：
        无；断言显式 fetch/verify、私有离线 cache、15/15 native 与 0/233
        live 边界均存在。
    """

    installation_text = "\n".join(
        (
            INSTALL_DOC.read_text(encoding="utf-8"),
            CHINESE_INSTALL_DOC.read_text(encoding="utf-8"),
            DEPENDENCY_DOC.read_text(encoding="utf-8"),
            DEPLOYMENT_DOC.read_text(encoding="utf-8"),
        )
    )
    protocol_text = "\n".join(
        (
            EVALUATION_DOC.read_text(encoding="utf-8"),
            ARCHITECTURE_DOC.read_text(encoding="utf-8"),
        )
    )

    for required_text in (
        "paraguibench gold fetch",
        "paraguibench gold verify",
        "--gold-cache-root",
        "gold_cache",
        "Operation-FileOperate-CombinationDocs-015",
        "evaluator-only",
        "offline",
    ):
        assert required_text in installation_text
    for required_text in (
        "15/15",
        "13 tasks use a durable pre-Docker capability gate",
        "10 non-`none`",
        "Operation-FileOperate-BatchOperation-001",
        "Operation-FileOperate-CombinationDocs-015",
        "paraguibench.osworld.artifact-state.v1",
        "blocked",
        "live_validated=0/233",
    ):
        assert required_text in protocol_text
