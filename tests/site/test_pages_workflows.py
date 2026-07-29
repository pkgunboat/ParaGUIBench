"""约束 GitHub Pages 的只读 PR 检查与可信 main 部署边界。"""

from __future__ import annotations

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_CI = REPO_ROOT / ".github" / "workflows" / "site-ci.yml"
PAGES = REPO_ROOT / ".github" / "workflows" / "pages.yml"


def _action_references(workflow: str) -> tuple[str, ...]:
    """功能：提取 workflow 中所有外部 Action 引用。

    输入参数：
        workflow：完整 YAML 文本。
    输出返回值：
        按声明顺序排列的 ``owner/repository@revision`` 元组。
    """

    return tuple(
        re.findall(r"^\s*uses:\s*([^#\s]+)", workflow, re.MULTILINE)
    )


def test_pull_request_site_ci_is_read_only_and_never_deploys() -> None:
    """功能：确认不可信 PR 只执行构建检查且不能获得 Pages 写权限。

    输入参数：
        无；读取 PR workflow。
    输出返回值：
        无；断言事件、权限、凭据和部署 Action 边界。
    """

    workflow = SITE_CI.read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "pull_request_target" not in workflow
    assert "pages: write" not in workflow
    assert "id-token: write" not in workflow
    assert "actions/deploy-pages" not in workflow
    assert "persist-credentials: false" in workflow
    assert "secrets." not in workflow
    assert "/ParaGUIBench/" in workflow


def test_production_pages_deploy_is_owner_main_gated_and_job_scoped() -> None:
    """功能：确认生产部署仅接受主仓库 main，且构建和部署权限相互分离。

    输入参数：
        无；读取生产 Pages workflow。
    输出返回值：
        无；断言仓库/ref 门禁、Pages 输出路径及最小权限。
    """

    workflow = PAGES.read_text(encoding="utf-8")
    gate = (
        "github.repository == 'pkgunboat/ParaGUIBench' &&\n"
        "      github.ref == 'refs/heads/main'"
    )
    assert workflow.count(gate) == 2
    assert "pull_request_target" not in workflow
    assert "pages: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "steps.pages.outputs.base_path" in workflow
    assert "environment:\n      name: github-pages" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "persist-credentials: false" in workflow
    assert "secrets." not in workflow


def test_pages_workflows_pin_every_action_to_full_commit() -> None:
    """功能：确认 PR 与生产 workflow 的 Action 全部使用不可变提交。

    输入参数：
        无；读取两个站点 workflow。
    输出返回值：
        无；断言不存在浮动 tag、branch 或短 SHA。
    """

    references = _action_references(SITE_CI.read_text(encoding="utf-8"))
    references += _action_references(PAGES.read_text(encoding="utf-8"))
    assert references
    for reference in references:
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference)
