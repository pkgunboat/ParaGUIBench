"""资产、部署检查、单任务执行与结果查看的安全 CLI。"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any

from paraguibench.agents.systems.gui_only.seed18 import (
    Seed18AgentSystem,
    Seed18ModelConfig,
    Seed18OpenAIModel,
)
from paraguibench.benchmark import PreparedTask, prepare_release_task
from paraguibench.integrations.osworld.controller import OSWorldController
from paraguibench.integrations.osworld.docker_session import (
    OSWorldDockerConfig,
    OSWorldDockerSession,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    load_osworld_image_manifest,
)
from paraguibench.runstore import RunStore
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runtime.assets import (
    AssetManifest,
    fetch_asset_manifest,
    load_asset_manifest,
    verify_asset_directory,
)
from paraguibench.runtime.attempt_runner import AttemptRunner
from paraguibench.runtime.doctor import (
    DoctorReport,
    OSWorldDoctorConfig,
    inspect_osworld_prerequisites,
)
from paraguibench.runtime.evaluators import build_task_evaluator
from paraguibench.runtime.osworld_environment import OSWorldTaskEnvironment

_IMAGE_MANIFEST_RELATIVE = Path("environments/osworld/image-manifest.json")


def build_parser() -> argparse.ArgumentParser:
    """构造不接受 secret 值的 argparse 命令树。

    输入参数：
        无。
    输出返回值：
        包含 assets、doctor、run 和 inspect 子命令的 parser；模型 key 和
        endpoint 只能通过环境变量名引用。
    """

    parser = argparse.ArgumentParser(prog="paraguibench")
    commands = parser.add_subparsers(dest="command", required=True)

    assets = commands.add_parser("assets", help="管理固定任务资产缓存")
    asset_commands = assets.add_subparsers(
        dest="asset_command",
        required=True,
    )
    for action_name in ("fetch", "verify"):
        action = asset_commands.add_parser(action_name)
        _add_task_asset_arguments(action)
        action.set_defaults(handler=_handle_assets)

    doctor = commands.add_parser(
        "doctor",
        help="一次列出首个 OSWorld live run 的全部门禁",
    )
    _add_live_arguments(doctor, include_run_arguments=False)
    doctor.set_defaults(handler=_handle_doctor)

    run = commands.add_parser(
        "run",
        help="运行并评价一个 GUI-only Seed18 任务",
    )
    _add_live_arguments(run, include_run_arguments=True)
    run.set_defaults(handler=_handle_run)

    inspect = commands.add_parser(
        "inspect",
        help="只显示 Attempt 的执行与评价终态",
    )
    inspect.add_argument("--runs-root", required=True)
    inspect.add_argument("--run-id", required=True)
    inspect.add_argument("--task-id", required=True)
    inspect.add_argument("--attempt-id", required=True)
    inspect.set_defaults(handler=_handle_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    """解析命令并以不回显异常消息的方式执行。

    输入参数：
        argv：可选参数列表；``None`` 时读取当前进程命令行。
    输出返回值：
        0 表示命令及目标门禁通过；1 表示任务已运行但评价未通过；
        2 表示配置、环境或执行异常。
    """

    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except KeyboardInterrupt:
        print("error=KeyboardInterrupt", file=sys.stderr)
        return 2
    except BaseException as error:
        print(f"error={type(error).__name__}", file=sys.stderr)
        return 2


def _add_task_asset_arguments(parser: argparse.ArgumentParser) -> None:
    """给资产命令添加仓库、任务和 repo 外缓存参数。

    输入参数：
        parser：assets fetch/verify 子命令 parser。
    输出返回值：
        无；原地添加三个非敏感选项。
    """

    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--asset-cache-root", required=True)


def _add_live_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_run_arguments: bool,
) -> None:
    """添加 doctor/run 共用的 OSWorld 非敏感配置和 secret 引用。

    输入参数：
        parser：doctor 或 run parser。
        include_run_arguments：是否追加 Agent、RunStore 和资源上限选项。
    输出返回值：
        无；不会添加直接接收 API key 或 endpoint 值的选项。
    """

    _add_task_asset_arguments(parser)
    parser.add_argument("--qcow2-path", required=True)
    parser.add_argument("--server-port", type=int, required=True)
    parser.add_argument("--vnc-port", type=int, required=True)
    parser.add_argument(
        "--api-key-env",
        default="PARAGUIBENCH_MODEL_API_KEY",
    )
    parser.add_argument(
        "--base-url-env",
        default="PARAGUIBENCH_MODEL_BASE_URL",
    )
    if not include_run_arguments:
        return
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--attempt-id", default="attempt-001")
    parser.add_argument("--max-steps", type=int, default=18)
    parser.add_argument("--ready-timeout", type=float, default=360.0)
    parser.add_argument("--post-action-delay", type=float, default=1.0)
    parser.add_argument("--ram-size", default="8G")
    parser.add_argument("--cpu-cores", type=int, default=4)


def _handle_assets(arguments: argparse.Namespace) -> int:
    """下载或验证一个 release task 的固定资产闭集。

    输入参数：
        arguments：argparse 解析后的 repo、task、cache 和 action。
    输出返回值：
        资产完整时为 0，不完整 verify 为 2；输出不含来源 URL 或路径。
    """

    repo_root, prepared_task, manifest = _load_task_context(arguments)
    del repo_root, prepared_task
    cache_root = _absolute_path(arguments.asset_cache_root)
    asset_directory = cache_root / manifest.asset_set_id
    if arguments.asset_command == "fetch":
        verification = fetch_asset_manifest(manifest, asset_directory)
    else:
        verification = verify_asset_directory(manifest, asset_directory)
    print(f"asset_set={manifest.asset_set_id}")
    print(f"files={len(manifest.files)}")
    print(f"status={'PASS' if verification.ok else 'FAIL'}")
    return 0 if verification.ok else 2


def _handle_doctor(arguments: argparse.Namespace) -> int:
    """运行并打印全部 OSWorld 部署检查。

    输入参数：
        arguments：argparse 解析后的非敏感配置和环境变量引用。
    输出返回值：
        全部检查通过时为 0，否则为 2。
    """

    config = _build_doctor_config(arguments)
    report = inspect_osworld_prerequisites(config)
    _print_doctor_report(report)
    return 0 if report.ok else 2


def _handle_run(arguments: argparse.Namespace) -> int:
    """装配并执行单 VM、单 worker、Seed18、exact evaluator 纵向切片。

    输入参数：
        arguments：argparse 解析后的运行配置；API key 和 endpoint 值只从
            两个显式环境变量引用读取。
    输出返回值：
        评价通过时为 0，已执行但未通过时为 1，门禁失败或异常为 2。
    """

    repo_root, prepared_task, asset_manifest = _load_task_context(arguments)
    task = prepared_task.trusted_task
    image_manifest = _load_image_context(repo_root)
    doctor_config = _doctor_config_from_context(
        arguments,
        image_manifest=image_manifest,
        asset_manifest=asset_manifest,
    )
    report = inspect_osworld_prerequisites(doctor_config)
    _print_doctor_report(report)
    if not report.ok or not image_manifest.live_run_ready:
        return 2

    base_url = os.environ.get(arguments.base_url_env)
    if not isinstance(base_url, str) or not base_url:
        return 2
    model = Seed18OpenAIModel(
        Seed18ModelConfig(
            model=arguments.model,
            api_key_env=arguments.api_key_env,
            base_url=base_url,
        )
    )
    agent = Seed18AgentSystem(
        model=model,
        max_steps=arguments.max_steps,
        post_action_delay=arguments.post_action_delay,
    )
    run_id = arguments.run_id or _new_run_id()
    attempt_id = validate_identifier("attempt_id", arguments.attempt_id)
    validate_identifier("run_id", run_id)
    task_id = validate_identifier("task_id", str(task["task_id"]))
    container_name = f"paraguibench-{secrets.token_hex(8)}"
    docker_config = OSWorldDockerConfig(
        container_name=container_name,
        image=image_manifest.container_image,
        qcow2_path=doctor_config.qcow2_path,
        server_port=arguments.server_port,
        vnc_port=arguments.vnc_port,
        ram_size=arguments.ram_size,
        cpu_cores=arguments.cpu_cores,
    )
    controller = OSWorldController(
        f"http://127.0.0.1:{arguments.server_port}"
    )
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=doctor_config.asset_cache_root,
        docker_session=OSWorldDockerSession(docker_config),
        controller=controller,
        ready_timeout=arguments.ready_timeout,
    )
    store = RunStore(_absolute_path(arguments.runs_root))
    store.start_run(
        run_id=run_id,
        run_record={
            "release_id": "release-v1",
            "task_id": task_id,
            "agent_system": "gui_only.seed18",
            "model": arguments.model,
            "credential_reference": arguments.api_key_env,
            "credential_status": "PRESENT",
            "endpoint_reference": arguments.base_url_env,
            "environment_id": image_manifest.environment_id,
            "container_image": image_manifest.container_image,
            "qcow2_sha256": image_manifest.extracted_sha256,
        },
    )
    attempt = store.start_attempt(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        task_record=prepared_task.audit_metadata,
    )
    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=prepared_task,
        environment=environment,
        agent=agent,
        evaluator=build_task_evaluator(task),
    )
    print(f"run_id={run_id}")
    print(f"task_id={task_id}")
    print(f"attempt_id={attempt_id}")
    print(f"execution={result.execution_outcome.value}")
    print(f"evaluation={result.evaluation_outcome.value}")
    print(f"score={result.score}")
    return 0 if result.score == 1.0 else 1


def _handle_inspect(arguments: argparse.Namespace) -> int:
    """读取 summary 并只打印 execution/evaluation/score。

    输入参数：
        arguments：RunStore 根和三个已验证稳定 ID。
    输出返回值：
        evaluation=PASSED 时为 0，否则为 1；无效路径或 summary 由 main
        折叠为类型安全的错误并返回 2。
    """

    root = _absolute_path(arguments.runs_root)
    run_id = validate_identifier("run_id", arguments.run_id)
    task_id = validate_identifier("task_id", arguments.task_id)
    attempt_id = validate_identifier("attempt_id", arguments.attempt_id)
    relative = (
        Path(run_id)
        / "tasks"
        / task_id
        / "attempts"
        / attempt_id
        / "summary.json"
    )
    summary_path = _safe_child_file(root, relative)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    execution = summary["execution"]["outcome"]
    evaluation = summary["evaluation"]["outcome"]
    score = summary["evaluation"]["score"]
    if not isinstance(execution, str) or not isinstance(evaluation, str):
        raise ValueError("summary outcome 类型无效")
    if score is not None and (
        isinstance(score, bool) or not isinstance(score, (int, float))
    ):
        raise ValueError("summary score 类型无效")
    print(f"execution={execution}")
    print(f"evaluation={evaluation}")
    print(f"score={score}")
    return 0 if evaluation == "PASSED" else 1


def _load_task_context(
    arguments: argparse.Namespace,
) -> tuple[Path, PreparedTask, AssetManifest]:
    """加载固定 release task 及其仓库内资产 manifest。

    输入参数：
        arguments：包含 repo_root 与 task_id 的 argparse namespace。
    输出返回值：
        已 resolve 仓库根、三投影 PreparedTask 和已校验 AssetManifest。
    异常：
        ValueError：task 缺少 manifest 或路径越界/符号链接。
    """

    repo_root = Path(arguments.repo_root).expanduser().resolve()
    prepared_task = prepare_release_task(
        repo_root,
        arguments.task_id,
        environment_bindings={},
    )
    task = prepared_task.trusted_task
    relative_value = task.get("asset_manifest")
    if not isinstance(relative_value, str) or not relative_value:
        raise ValueError("任务缺少 asset_manifest")
    manifest_path = _safe_child_file(repo_root, Path(relative_value))
    return repo_root, prepared_task, load_asset_manifest(manifest_path)


def _load_image_context(repo_root: Path) -> OSWorldImageManifest:
    """加载仓库固定位置的 OSWorld image manifest。

    输入参数：
        repo_root：已 resolve 的 ParaGUIBench 仓库根目录。
    输出返回值：
        已验证但 extracted digest 可空的 OSWorldImageManifest。
    """

    path = _safe_child_file(repo_root, _IMAGE_MANIFEST_RELATIVE)
    return load_osworld_image_manifest(path)


def _build_doctor_config(arguments: argparse.Namespace) -> OSWorldDoctorConfig:
    """从 CLI arguments 加载 task/image context 并建立 doctor config。

    输入参数：
        arguments：doctor 子命令 namespace。
    输出返回值：
        只含非敏感路径、摘要和环境变量引用的配置。
    """

    repo_root, _, asset_manifest = _load_task_context(arguments)
    return _doctor_config_from_context(
        arguments,
        image_manifest=_load_image_context(repo_root),
        asset_manifest=asset_manifest,
    )


def _doctor_config_from_context(
    arguments: argparse.Namespace,
    *,
    image_manifest: OSWorldImageManifest,
    asset_manifest: AssetManifest,
) -> OSWorldDoctorConfig:
    """由已加载 manifest 构造 doctor config。

    输入参数：
        arguments：doctor/run 共用 CLI 选项。
        image_manifest：固定 OSWorld 镜像身份。
        asset_manifest：当前 task 固定资产集合。
    输出返回值：
        可执行全部本机门禁的 OSWorldDoctorConfig。
    """

    return OSWorldDoctorConfig(
        image_manifest=image_manifest,
        qcow2_path=Path(arguments.qcow2_path).expanduser().absolute(),
        asset_manifest=asset_manifest,
        asset_cache_root=_absolute_path(arguments.asset_cache_root),
        server_port=arguments.server_port,
        vnc_port=arguments.vnc_port,
        api_key_env=arguments.api_key_env,
        base_url_env=arguments.base_url_env,
    )


def _print_doctor_report(report: DoctorReport) -> None:
    """打印不含值、路径或详情的完整 doctor check 列表。

    输入参数：
        report：inspect_osworld_prerequisites 返回的结果。
    输出返回值：
        无；每行仅含 PASS/FAIL 和稳定检查名称。
    """

    for check in report.checks:
        print(f"{'PASS' if check.passed else 'FAIL'} {check.name}")
    print(f"doctor={'PASS' if report.ok else 'FAIL'}")


def _new_run_id() -> str:
    """生成按 UTC 时间和随机后缀区分的安全 run_id。

    输入参数：
        无。
    输出返回值：
        符合 RunStore identifier 规则的唯一标识。
    """

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{timestamp}-{secrets.token_hex(4)}"


def _absolute_path(value: str) -> Path:
    """把 CLI 路径扩展为绝对路径但不要求预先存在。

    输入参数：
        value：命令行提供的非敏感本地路径。
    输出返回值：
        展开 ``~`` 后的绝对 Path。
    """

    return Path(value).expanduser().absolute()


def _safe_child_file(root: Path, relative: Path) -> Path:
    """在 root 内解析不经过符号链接的普通文件。

    输入参数：
        root：已选择的仓库或 RunStore 根目录。
        relative：不得为绝对或含 ``..`` 的相对路径。
    输出返回值：
        位于 root 内的普通文件绝对路径。
    异常：
        ValueError：路径穿越、符号链接、缺失或不是普通文件。
    """

    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("相对文件路径不得越过根目录")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("文件路径不得包含符号链接")
    resolved_root = root.resolve()
    resolved = current.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("文件路径不得越过根目录") from error
    if not resolved.is_file():
        raise ValueError("目标不是普通文件")
    return resolved


if __name__ == "__main__":
    raise SystemExit(main())
