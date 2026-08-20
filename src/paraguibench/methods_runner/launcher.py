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
    script = _package_root() / "stages" / RUNNER_FILES[category]
    if not script.is_file():
        raise FileNotFoundError(f"runner script missing: {script.name}")
    return script


def _package_root() -> Path:
    """返回仓库 src 根（launcher 模块向上两级）。

    输入参数：无。
    输出返回值：``src`` 目录绝对路径；runner 脚本与 parallel_benchmark
        均以该锚点定位，不依赖 runner 自身的层级深浅。
    """

    return Path(__file__).resolve().parents[2]


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


def check_environment(
    environ: dict[str, str] | None = None,
    agent_mode: str = "plan",
) -> None:
    """凭据缺失时 fail-fast；只打印变量名与状态。

    输入参数：
        environ：可选的环境字典，默认读 os.environ。
        agent_mode：``plan`` 需要 planner+worker 两组凭据；
            ``gui_only`` 只需要 GUI worker 凭据。
    输出返回值：
        无。
    异常：
        SystemExit：所需角色的凭据组全部未配置。
    """

    report = environment_report(environ)
    roles = ["gui-worker"]
    if agent_mode == "plan":
        roles.append("planner")
    missing_roles = [
        role
        for role in roles
        if not any(report["credentials"][role].values())
    ]
    if missing_roles:
        for role in missing_roles:
            names = ", ".join(dict(report["credentials"][role]))
            print(f"methods-runner: {role} 凭据未配置（需要以下之一: {names}）")
        raise SystemExit(2)


def _resolve_agent_mode(argv: Sequence[str], environ: dict[str, str]) -> str:
    """解析运行模式；与原 runner 语义一致：ABLATION_AGENT_MODE 非空时优先。

    输入参数：
        argv：透传给原 runner 的参数。
        environ：环境字典。
    输出返回值：
        ``plan`` 或 ``gui_only``。
    """

    env_mode = environ.get("ABLATION_AGENT_MODE", "")
    if env_mode:
        return env_mode if env_mode in {"plan", "gui_only"} else "plan"
    mode = ""
    args = list(argv)
    for index, item in enumerate(args):
        if item == "--agent-mode" and index + 1 < len(args):
            mode = args[index + 1]
        elif item.startswith("--agent-mode="):
            mode = item.split("=", 1)[1]
    return mode if mode in {"plan", "gui_only"} else "plan"


def _ensure_tasks_list_alias(package_root: Path) -> None:
    """原 runner 从未入库的 ``tasks_list`` 目录读任务（见 provenance 记录）。

    迁移基线中任务 JSON 位于 ``tasks/`` 且字段与扫描器过滤条件一致；
    装载时若 ``tasks_list`` 缺失则建立指向 ``tasks`` 的相对软链，
    恢复原执行环境布局。链接不进入版本库（.gitignore 已忽略）。

    输入参数：
        package_root：``src/parallel_benchmark`` 目录。
    输出返回值：
        无。
    """

    tasks = package_root / "tasks"
    alias = package_root / "tasks_list"
    if alias.exists() or alias.is_symlink() or not tasks.is_dir():
        return
    try:
        alias.symlink_to("tasks", target_is_directory=True)
    except FileExistsError:
        pass


_WEBMALL_TASK_PREFIX = "Operation-OnlineShopping-"


def _ensure_webmall_tasks_alias(src_root: Path) -> None:
    """webmall runner 从 ``src/extra_docker_env/tasks`` 读任务（原运行时目录）。

    迁移基线中 91 个 OnlineShopping 任务 JSON 位于
    ``parallel_benchmark/tasks`` 且字段一致；装载时为它们建立逐文件相对
    软链，避免整目录混入非购物任务。目录不进入版本库（.gitignore 已忽略）。

    输入参数：
        src_root：仓库 ``src`` 目录。
    输出返回值：
        无。
    """

    tasks_dir = src_root / "parallel_benchmark" / "tasks"
    webmall_dir = src_root / "extra_docker_env" / "tasks"
    if not tasks_dir.is_dir():
        return
    webmall_dir.mkdir(parents=True, exist_ok=True)
    for task_json in tasks_dir.glob(f"{_WEBMALL_TASK_PREFIX}*.json"):
        link = webmall_dir / task_json.name
        if link.exists() or link.is_symlink():
            continue
        try:
            link.symlink_to(
                f"../../parallel_benchmark/tasks/{task_json.name}",
                target_is_directory=False,
            )
        except FileExistsError:
            continue


def launch(category: str, argv: Sequence[str]) -> None:
    """以原 runner 自己的 argv 原样执行它。

    输入参数：
        category：RUNNER_FILES 中的类别名。
        argv：透传给原 runner 的参数列表。
    输出返回值：
        无；异常与退出码由原 runner 决定。
    """

    script = runner_script_path(category)
    check_environment(agent_mode=_resolve_agent_mode(argv, os.environ))
    # 原 runner 以脚本目录为工作目录执行，使用扁平 import（如
    # `from run_QA_pipeline import ...`）；装载时等价恢复该执行上下文。
    script_dir = str(script.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    package_root = _package_root()
    _ensure_tasks_list_alias(package_root / "parallel_benchmark")
    if category == "webmall":
        _ensure_webmall_tasks_alias(package_root)
    sys.argv = [str(script)] + list(argv)
    runpy.run_path(str(script), run_name="__main__")
