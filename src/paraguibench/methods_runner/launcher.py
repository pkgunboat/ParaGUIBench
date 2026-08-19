"""methods_runner 共享装载器：环境检查 + 原样执行原 runner。"""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
from typing import Sequence

RUNNER_FILES = {
    "qa": "run_QA_pipeline_parallel.py",
    "webmall": "run_webmall_pipeline_parallel.py",
    "webnavigate": "run_webnavigate_pipeline_parallel.py",
    "self_operation": "self_operation_pipeline/run_self_operation_pipeline_parallel.py",
    "searchwrite": "self_operation_pipeline/run_searchwrite_pipeline_parallel.py",
}

_CREDENTIAL_GROUPS = (
    ("gui-worker", ("DEERAPI_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")),
    ("planner", ("DEERAPI_API_KEY", "OPENAI_API_KEY")),
)

_MODEL_OVERRIDES = (
    "BENCH_DEFAULT_PLAN_AGENT",
    "BENCH_DEFAULT_QWEN_GUI_AGENT",
    "ABLATION_AGENT_MODE",
    "ABLATION_GUI_AGENT",
)


def runner_script_path(category: str) -> Path:
    """返回类别对应的原 runner 脚本路径。

    输入参数：
        category：RUNNER_FILES 中的类别名。
    输出返回值：
        仓库 src/stages 下的脚本绝对路径。
    异常：
        KeyError：类别未知；FileNotFoundError：脚本缺失。
    """

    if category not in RUNNER_FILES:
        raise KeyError(f"unknown methods category: {category}")
    script = Path(__file__).resolve().parents[2] / "stages" / RUNNER_FILES[category]
    if not script.is_file():
        raise FileNotFoundError(f"runner script missing: {script.name}")
    return script


def environment_report(environ: dict[str, str] | None = None) -> dict[str, object]:
    """汇总凭据与模型环境变量的配置状态（不含任何值）。

    输入参数：
        environ：可选的环境字典，默认读 os.environ。
    输出返回值：
        {role: {var: configured}} 与模型覆盖项列表。
    """

    env = os.environ if environ is None else environ
    credentials = {
        role: {name: bool(env.get(name)) for name in names}
        for role, names in _CREDENTIAL_GROUPS
    }
    models = {name: bool(env.get(name)) for name in _MODEL_OVERRIDES}
    return {"credentials": credentials, "model_overrides": models}


def check_environment(environ: dict[str, str] | None = None) -> None:
    """凭据缺失时 fail-fast；只打印变量名与状态。

    输入参数：
        environ：可选的环境字典，默认读 os.environ。
    输出返回值：
        无。
    异常：
        SystemExit：任一角色的凭据组全部未配置。
    """

    report = environment_report(environ)
    missing_roles = [
        role
        for role, names in report["credentials"].items()
        if not any(names.values())
    ]
    if missing_roles:
        for role in missing_roles:
            names = ", ".join(dict(report["credentials"][role]))
            print(f"methods-runner: {role} 凭据未配置（需要以下之一: {names}）")
        raise SystemExit(2)


def launch(category: str, argv: Sequence[str]) -> None:
    """以原 runner 自己的 argv 原样执行它。

    输入参数：
        category：RUNNER_FILES 中的类别名。
        argv：透传给原 runner 的参数列表。
    输出返回值：
        无；异常与退出码由原 runner 决定。
    """

    check_environment()
    script = runner_script_path(category)
    sys.argv = [str(script)] + list(argv)
    runpy.run_path(str(script), run_name="__main__")
