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
    monkeypatch.setattr(launcher, "check_environment", lambda **_: None)
    launcher.launch("qa", ["--agent-mode", "gui_only", "-n", "1"])
    assert captured["run_name"] == "__main__"
    assert Path(str(captured["path"])).name == "run_QA_pipeline_parallel.py"
    assert captured["argv"][1:] == ["--agent-mode", "gui_only", "-n", "1"]


def test_launch_creates_tasks_list_alias(tmp_path, monkeypatch):
    """缺 tasks_list 时装载器建立指向 tasks 的软链别名。"""

    package_root = tmp_path / "parallel_benchmark"
    (package_root / "tasks").mkdir(parents=True)
    monkeypatch.setattr(launcher, "check_environment", lambda **_: None)
    monkeypatch.setattr(launcher, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(
        launcher,
        "runner_script_path",
        lambda category: tmp_path / "stages" / "runner.py",
    )
    (tmp_path / "stages").mkdir()
    (tmp_path / "stages" / "runner.py").write_text("print('ok')\n")
    captured = {}
    monkeypatch.setattr(
        launcher.runpy,
        "run_path",
        lambda path, run_name: captured.update(path=path),
    )
    launcher.launch("qa", [])
    alias = package_root / "tasks_list"
    assert alias.is_symlink()
    assert alias.resolve() == (package_root / "tasks").resolve()


def test_launch_creates_alias_for_nested_category(tmp_path, monkeypatch):
    """嵌套类别（self_operation_pipeline）同样在 src/parallel_benchmark 建别名。"""

    package_root = tmp_path / "parallel_benchmark"
    (package_root / "tasks").mkdir(parents=True)
    monkeypatch.setattr(launcher, "check_environment", lambda **_: None)
    monkeypatch.setattr(launcher, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(
        launcher,
        "runner_script_path",
        lambda category: tmp_path
        / "stages"
        / "self_operation_pipeline"
        / "runner.py",
    )
    nested = tmp_path / "stages" / "self_operation_pipeline"
    nested.mkdir(parents=True)
    (nested / "runner.py").write_text("print('ok')\n")
    monkeypatch.setattr(launcher.runpy, "run_path", lambda path, run_name: None)
    launcher.launch("self_operation", [])
    alias = package_root / "tasks_list"
    assert alias.is_symlink()
    assert alias.resolve() == (package_root / "tasks").resolve()


def test_check_environment_gui_only_skips_planner(monkeypatch):
    """gui_only 模式下仅配置 GUI worker 凭据即可通过。"""

    monkeypatch.delenv("DEERAPI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dummy")
    launcher.check_environment(agent_mode="gui_only")
    try:
        launcher.check_environment(agent_mode="plan")
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("plan 模式缺少 planner 凭据应当 SystemExit")


def test_resolve_agent_mode_env_overrides_cli():
    """ABLATION_AGENT_MODE 非空时优先于 --agent-mode，对齐原 runner 语义。"""

    assert (
        launcher._resolve_agent_mode(
            ["--agent-mode", "gui_only"], {"ABLATION_AGENT_MODE": "plan"}
        )
        == "plan"
    )
    assert (
        launcher._resolve_agent_mode(
            ["--agent-mode", "plan"], {"ABLATION_AGENT_MODE": "gui_only"}
        )
        == "gui_only"
    )
    assert launcher._resolve_agent_mode(["--agent-mode", "gui_only"], {}) == "gui_only"
    assert launcher._resolve_agent_mode([], {}) == "plan"
