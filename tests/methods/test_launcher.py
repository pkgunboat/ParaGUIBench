"""methods_runner 装载器行为测试：路径解析、凭据 fail-fast、原样透传。"""

from __future__ import annotations

from pathlib import Path

import pytest

from paraguibench.methods_runner import launcher


def test_all_categories_resolve_to_existing_scripts() -> None:
    for category in launcher.RUNNER_FILES:
        script = launcher.runner_script_path(category)
        assert script.is_file(), f"{category}: {script.name} 缺失"


def test_unknown_category_raises() -> None:
    with pytest.raises(KeyError):
        launcher.runner_script_path("not-a-category")


def test_environment_report_hides_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEERAPI_API_KEY", "secret-value")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    report = launcher.environment_report()
    rendered = repr(report)
    assert "secret-value" not in rendered
    assert report["credentials"]["planner"]["DEERAPI_API_KEY"] is True
    assert report["credentials"]["planner"]["OPENAI_API_KEY"] is False


def test_check_environment_fails_fast_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("DEERAPI_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        launcher.check_environment()
    assert excinfo.value.code == 2


def test_launch_passes_argv_verbatim_and_runs_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_path(path: str, run_name: str) -> None:
        captured["path"] = path
        captured["run_name"] = run_name
        captured["argv"] = list(__import__("sys").argv)

    monkeypatch.setattr(launcher.runpy, "run_path", fake_run_path)
    monkeypatch.setattr(launcher, "check_environment", lambda: None)
    launcher.launch("qa", ["--agent-mode", "gui_only", "-n", "1"])
    assert captured["run_name"] == "__main__"
    assert Path(str(captured["path"])).name == "run_QA_pipeline_parallel.py"
    assert captured["argv"][1:] == ["--agent-mode", "gui_only", "-n", "1"]
