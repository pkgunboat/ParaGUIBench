"""资产、部署检查、单任务执行与结果查看的安全 CLI。"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import math
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
from paraguibench.agents.systems.gui_only import QwenGUIOnlyAgentSystem
from paraguibench.agents.systems.paragui import (
    GUIWorkerParaGUIAdapter,
    KimiOpenAIPlanningBackend,
    KimiPlannerConfig,
    ParaGUIAgentSystem,
    StructuredParaGUIPlanner,
)
from paraguibench.agents.workers.qwen import QwenGUIWorker, QwenModelConfig
from paraguibench.benchmark import PreparedTask, prepare_release_task
from paraguibench.cli.model_probe import add_model_probe_commands
from paraguibench.integrations.osworld.controller import OSWorldController
from paraguibench.integrations.osworld.active_tab_probe import (
    capture_google_shopping_active_tab_observation,
)
from paraguibench.integrations.osworld.artifact_finalizer import (
    OSWorldArtifactFinalizer,
)
from paraguibench.runtime.osworld_artifact_evidence import (
    OSWorldArtifactEvidenceSource,
)
from paraguibench.runtime.osworld_artifact_component_candidate import (
    OSWorldArtifactComponentCandidateConfig,
    run_osworld_artifact_component_candidate,
)
from paraguibench.runtime.osworld_artifact_component_receipts import (
    OSWorldArtifactComponentReceipt,
)
from paraguibench.integrations.osworld.docker_session import (
    OSWorldDockerConfig,
    OSWorldDockerSession,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    load_osworld_image_manifest,
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.integrations.osworld.bookmark_evidence import (
    OSWorldChromeBookmarkEvidenceSource,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OSWorldOperationArtifactSource,
)
from paraguibench.integrations.osworld.state_evidence import (
    OSWorldChromeStateEvidenceSource,
)
from paraguibench.integrations.pipeline_implicit import (
    PipelineImplicitArtifactEvidenceSource,
)
from paraguibench.integrations.webmall.browser_cart_source import (
    WebMallBrowserCartSource,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceReceipt,
    validate_webmall_cart_reference_receipt,
)
from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest_with_sha256,
)
from paraguibench.runstore import (
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
    RunVersionVector,
)
from paraguibench.runstore.identifiers import validate_identifier
from paraguibench.runtime.assets import (
    ResolvedTaskAssets,
    TaskAssetMode,
    fetch_asset_manifest,
    resolve_task_assets,
    verify_asset_directory,
)
from paraguibench.runtime.artifact_family_task_prepare import (
    ArtifactFamilyTaskPrepareBinding,
    preflight_artifact_family_task_prepare,
)
from paraguibench.runtime.attempt_runner import AttemptRunner, TaskEvaluator
from paraguibench.runtime.doctor import (
    DoctorReport,
    OSWorldDoctorConfig,
    inspect_osworld_prerequisites,
)
from paraguibench.runtime.evaluators import build_task_evaluator
from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    GoldAssetResolver,
    GoldFetchError,
    fetch_gold_assets,
    load_gold_asset_manifest,
)
from paraguibench.runtime.osworld_environment import OSWorldTaskEnvironment
from paraguibench.runtime.osworld_gold import (
    ResolvedOSWorldTaskGold,
    TaskGoldMode,
    bind_osworld_task_gold,
)
from paraguibench.runtime.pipeline_implicit_binding import (
    PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS,
    PipelineImplicitRuntimeCapability,
    preflight_pipeline_implicit_local_runtime,
    preflight_pipeline_implicit_runtime,
)
from paraguibench.runtime.pipeline_implicit_component_candidate import (
    PipelineImplicitComponentCandidateConfig,
    run_pipeline_implicit_component_candidate,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    PipelineImplicitComponentReceipt,
)
from paraguibench.runtime.run_versioning import build_run_version_vector
from paraguibench.runtime.single_vm_lease import (
    SingleVMEnvironmentLeaseAdapter,
)
from paraguibench.runtime.webmall_binding import (
    WebMallEvidenceMode,
    WebMallRuntimeBinding,
    bind_webmall_privileged_runtime,
    preflight_webmall_cart_reference_candidate_runtime,
    preflight_webmall_identity,
    preflight_webmall_runtime,
)
from paraguibench.runtime.webmall_doctor import (
    inspect_webmall_prerequisites,
)
from paraguibench.runtime.webmall_cart_environment import (
    WebMallCartTaskEnvironment,
)
from paraguibench.runtime.webmall_cart_reference_validation import (
    build_webmall_cart_reference_component_revision,
    run_webmall_cart_reference_validation,
)
from paraguibench.runtime.webmall_cart_qcow2 import (
    WebMallCartAttestedDockerSession,
)
from paraguibench.runtime.webmall_environment import WebMallTaskEnvironment
from paraguibench.runtime.webmall_url_environment import (
    WebMallURLTaskEnvironment,
)

_IMAGE_MANIFEST_RELATIVE = Path("environments/osworld/image-manifest.json")


class _SafeArgumentParser(argparse.ArgumentParser):
    """实现不回显 argv 值的命令行解析器。

    输入参数：构造参数与 ``argparse.ArgumentParser`` 相同；
        本类强制禁用长选项缩写，防止 ``--api-key`` 被误当作
        ``--api-key-env`` 并使其值进入后续运行链。
    输出返回值：与 argparse 兼容的 parser；参数错误仅输出
        无输入值的 usage 和固定错误类型，然后以 2 退出。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """构造禁用长选项缩写的安全 parser。

        输入参数：args/kwargs 为 argparse 原生构造参数。
        输出返回值：无；初始化当前 parser 实例。
        """

        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        """将任意解析错误折叠为不含输入值的固定输出。

        输入参数：message 为 argparse 产生的原始错误；出于
            凭据保密边界而故意不输出。
        输出返回值：无正常返回；输出安全 usage 后触发
            ``SystemExit(2)``。
        """

        del message
        self.print_usage(sys.stderr)
        self.exit(2, "error=ArgumentParseError\n")


def build_parser() -> argparse.ArgumentParser:
    """构造不接受 secret 值的 argparse 命令树。

    输入参数：
        无。
    输出返回值：
        包含 assets、gold、doctor、run 和 inspect 子命令的 parser；模型
        key 和 endpoint 只能通过环境变量名引用。
    """

    parser = _SafeArgumentParser(prog="paraguibench")
    commands = parser.add_subparsers(dest="command", required=True)

    add_model_probe_commands(commands)

    assets = commands.add_parser("assets", help="管理固定任务资产缓存")
    asset_commands = assets.add_subparsers(
        dest="asset_command",
        required=True,
    )
    for action_name in ("fetch", "verify"):
        action = asset_commands.add_parser(action_name)
        _add_task_asset_arguments(action)
        action.set_defaults(handler=_handle_assets)

    gold = commands.add_parser(
        "gold",
        help="显式预置或离线验证 evaluator-only pinned gold",
    )
    gold_commands = gold.add_subparsers(
        dest="gold_command",
        required=True,
    )
    for action_name in ("fetch", "verify"):
        action = gold_commands.add_parser(action_name)
        _add_task_gold_arguments(action)
        action.set_defaults(handler=_handle_gold)
    materialize = gold_commands.add_parser(
        "materialize",
        help="从已验证 input 私有物化 evaluator-only derived gold",
    )
    _add_task_gold_materialize_arguments(materialize)
    materialize.set_defaults(handler=_handle_gold)

    doctor = commands.add_parser(
        "doctor",
        help="一次列出首个 OSWorld live run 的全部门禁",
    )
    _add_live_arguments(doctor, include_run_arguments=False)
    doctor.set_defaults(handler=_handle_doctor)

    run = commands.add_parser(
        "run",
        help=("运行并评价 GUI-only，或实验性单 VM 串行 ParaGUI 任务"),
    )
    _add_live_arguments(run, include_run_arguments=True)
    run.set_defaults(handler=_handle_run)

    webmall_cart = commands.add_parser(
        "webmall-cart",
        help="显式验证 WebMall Cart reference reader，不运行 Agent",
    )
    webmall_cart_commands = webmall_cart.add_subparsers(
        dest="webmall_cart_command",
        required=True,
    )
    reference_validate = webmall_cart_commands.add_parser(
        "reference-validate",
        help="在 owned VM 中生成脱敏 Cart reader component receipt",
    )
    _add_webmall_cart_reference_arguments(reference_validate)
    reference_validate.set_defaults(
        handler=_handle_webmall_cart_reference_validate,
    )

    osworld_artifact = commands.add_parser(
        "osworld-artifact",
        help="不运行 Agent 的 artifact component 实机验证",
    )
    osworld_artifact_commands = osworld_artifact.add_subparsers(
        dest="osworld_artifact_command",
        required=True,
    )
    component_validate = osworld_artifact_commands.add_parser(
        "component-validate",
        help="在 owned VM 中生成 task-scoped G/D/S receipt",
    )
    _add_osworld_artifact_component_arguments(component_validate)
    component_validate.set_defaults(
        handler=_handle_osworld_artifact_component_validate,
    )

    pipeline_implicit = commands.add_parser(
        "pipeline-implicit",
        help="不运行 Agent 的 pipeline component 实机验证",
    )
    pipeline_implicit_commands = pipeline_implicit.add_subparsers(
        dest="pipeline_implicit_command",
        required=True,
    )
    pipeline_component_validate = pipeline_implicit_commands.add_parser(
        "component-validate",
        help="在 owned VM 中生成 task-scoped pipeline receipt",
    )
    _add_pipeline_implicit_component_arguments(pipeline_component_validate)
    pipeline_component_validate.set_defaults(
        handler=_handle_pipeline_implicit_component_validate,
    )

    inspect = commands.add_parser(
        "inspect",
        help="只显示 Attempt 的执行与评价终态",
    )
    inspect.add_argument("--runs-root", required=True)
    inspect.add_argument("--run-id", required=True)
    inspect.add_argument("--task-id", required=True)
    inspect.add_argument("--attempt-id", required=True)
    inspect.add_argument(
        "--diagnostics",
        action="store_true",
        help="追加 allowlist-only 失败阶段与版本向量。",
    )
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


def _add_task_gold_arguments(parser: argparse.ArgumentParser) -> None:
    """给 evaluator-only gold 命令添加任务与私有缓存参数。

    输入参数：
        parser：``gold fetch`` 或 ``gold verify`` 子命令 parser。
    输出返回值：
        无；原地添加仓库根、canonical task ID 与 evaluator 私有缓存根。
        不接受 URL、token、gold 内容或来源路径覆盖。
    """

    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--gold-cache-root", required=True)


def _add_task_gold_materialize_arguments(parser: argparse.ArgumentParser) -> None:
    """给 derived gold 物化命令添加显式本地边界。

    输入参数：parser 为 ``gold materialize`` 子命令 parser。
    输出返回：无；原地添加仓库/任务、私有 input/gold 根、
        ffmpeg/ffprobe 可执行路径与正有限超时。不接收 URL、
        token、输出摘要、媒体内容或任意派生参数。
    """

    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--asset-cache-root", required=True)
    parser.add_argument("--gold-cache-root", required=True)
    parser.add_argument("--ffmpeg-path", required=True)
    parser.add_argument("--ffprobe-path", required=True)
    parser.add_argument(
        "--timeout-seconds",
        required=True,
        type=_positive_finite_float,
    )


def _positive_finite_float(value: str) -> float:
    """将 CLI 超时值收紧为正有限浮点数。

    输入参数：value 为 argparse 交付的原始字符串。
    输出返回：有限且大于零时返回 ``float``。
    异常：argparse.ArgumentTypeError：不可解析、非有限或非正；
        错误文本不回显原始值。
    """

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("超时必须为正有限数") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("超时必须为正有限数")
    return parsed


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
    parser.add_argument(
        "--gold-cache-root",
        default="~/.cache/paraguibench/gold",
        help="evaluator-only pinned gold 私有缓存；无外部 gold 的任务不访问。",
    )
    parser.add_argument("--qcow2-path", required=True)
    parser.add_argument("--server-port", type=int, required=True)
    parser.add_argument("--vnc-port", type=int, required=True)
    parser.add_argument("--chromium-port", type=int, required=True)
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
    parser.add_argument(
        "--agent-system",
        choices=["gui-only", "paragui-single-vm"],
        default="gui-only",
        help=(
            "端到端 Agent System；paragui-single-vm 是固定单 VM、"
            "单 worker 串行的实验路径。"
        ),
    )
    parser.add_argument(
        "--worker",
        choices=["seed18", "qwen"],
        default="seed18",
        help="GUI worker；qwen 当前为 contract-tested experimental 路径。",
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--planner-model", default="kimi-k2.6")
    parser.add_argument(
        "--planner-api-key-env",
        default="PARAGUIBENCH_MODEL_API_KEY",
    )
    parser.add_argument(
        "--planner-base-url-env",
        default="PARAGUIBENCH_MODEL_BASE_URL",
    )
    parser.add_argument(
        "--planner-max-subtasks",
        type=int,
        choices=range(1, 7),
        default=4,
    )
    parser.add_argument(
        "--planner-max-output-tokens",
        type=int,
        default=2048,
    )
    parser.add_argument("--run-id")
    parser.add_argument("--attempt-id", default="attempt-001")
    parser.add_argument("--max-steps", type=int, default=18)
    parser.add_argument("--ready-timeout", type=float, default=360.0)
    parser.add_argument("--post-action-delay", type=float, default=1.0)
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--max-image-pixels", type=int, default=4_194_304)
    parser.add_argument(
        "--max-history-image-pixels",
        type=int,
        default=1_048_576,
        help="仅 Qwen：每张历史截图的低像素预算。",
    )
    parser.add_argument(
        "--qwen-visual-history",
        type=int,
        choices=range(5),
        default=2,
        help="仅 Qwen：按旧到新回放 0–4 张历史截图。",
    )
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="仅 Qwen：启用模型侧 thinking；便宜验证默认关闭。",
    )
    parser.add_argument(
        "--qwen-tool-protocol",
        choices=["native", "osworld_xml"],
        default="native",
        help="仅 Qwen：原生 Function Calling 或 OSWorld 文本 XML 协议。",
    )
    parser.add_argument("--ram-size", default="8G")
    parser.add_argument("--cpu-cores", type=int, default=4)


def _add_webmall_cart_reference_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    """添加显式 Cart reference validation 的非模型参数。

    输入参数：parser 为 ``webmall-cart reference-validate`` 子命令。
    输出返回值：无；只添加仓库、canonical task、缓存、镜像与 owned VM
        资源参数，不接受 API key、模型 endpoint、Cart 或 origin 值。
    """

    _add_task_asset_arguments(parser)
    parser.add_argument("--qcow2-path", required=True)
    parser.add_argument("--server-port", type=int, required=True)
    parser.add_argument("--vnc-port", type=int, required=True)
    parser.add_argument("--chromium-port", type=int, required=True)
    parser.add_argument("--ready-timeout", type=float, default=360.0)
    parser.add_argument("--ram-size", default="8G")
    parser.add_argument("--cpu-cores", type=int, default=4)


def _add_osworld_artifact_component_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    """添加 OSWorld artifact component candidate 的非模型参数。

    输入参数：parser 为 ``osworld-artifact component-validate``
        子命令解析器。
    输出返回值：无；只添加仓库、task、私有缓存、RunStore、
        镜像、loopback 端口与 VM 资源参数；不接受 API key、
        endpoint、model、Agent final text、environment 或 proof。
    """

    _add_task_asset_arguments(parser)
    parser.add_argument(
        "--gold-cache-root",
        default="~/.cache/paraguibench/gold",
    )
    parser.add_argument("--runs-root", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--attempt-id", default="attempt-001")
    parser.add_argument("--qcow2-path", required=True)
    parser.add_argument("--server-port", type=int, required=True)
    parser.add_argument("--vnc-port", type=int, required=True)
    parser.add_argument("--chromium-port", type=int, required=True)
    parser.add_argument("--ready-timeout", type=float, default=360.0)
    parser.add_argument("--ram-size", default="8G")
    parser.add_argument("--cpu-cores", type=int, default=4)


def _add_pipeline_implicit_component_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    """添加 pipeline implicit component candidate 的非模型参数。

    输入参数：parser 为 ``pipeline-implicit component-validate``
        子命令解析器。
    输出返回值：无；复用已审计的 repo/task/cache/RunStore/
        qcow2/loopback 端口与资源参数闭集，不接受模型、凭据、
        Agent、final text、evaluator、environment、factory 或 receipt 路径。
    """

    _add_osworld_artifact_component_arguments(parser)


def _handle_pipeline_implicit_component_validate(
    arguments: argparse.Namespace,
) -> int:
    """执行专属无 Agent candidate 并只输出脱敏 receipt。

    输入参数：arguments 为不含凭据或可注入运行对象的
        argparse namespace。
    输出返回值：owned VM 关闭、qcow/OCI attestation、typed
        evaluator 和 RunStore inspection 全部通过后，输出一行字段闭合
        canonical JSON 并返回 0；异常由 ``main`` 折叠为脱敏类型。
    """

    run_id = arguments.run_id or _new_run_id()
    receipt = run_pipeline_implicit_component_candidate(
        PipelineImplicitComponentCandidateConfig(
            repo_root=_absolute_path(arguments.repo_root),
            runs_root=_absolute_path(arguments.runs_root),
            asset_cache_root=_absolute_path(arguments.asset_cache_root),
            gold_cache_root=_absolute_path(arguments.gold_cache_root),
            qcow2_path=_absolute_path(arguments.qcow2_path),
            task_id=arguments.task_id,
            run_id=run_id,
            attempt_id=arguments.attempt_id,
            server_port=arguments.server_port,
            vnc_port=arguments.vnc_port,
            chromium_port=arguments.chromium_port,
            ram_size=arguments.ram_size,
            cpu_cores=arguments.cpu_cores,
            ready_timeout=arguments.ready_timeout,
        )
    )
    if type(receipt) is not PipelineImplicitComponentReceipt:
        raise TypeError("pipeline implicit component receipt 类型无效")
    print(
        json.dumps(
            receipt.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return 0


def _handle_osworld_artifact_component_validate(
    arguments: argparse.Namespace,
) -> int:
    """执行专属 component candidate 并只输出 canonical 脱敏 receipt。

    输入参数：arguments 为不含任何凭据、模型或可注入运行组件
        的 argparse namespace。
    输出返回值：owned VM 成功关闭且 RunStore-v2 inspection
        通过时打印一行字段闭合 JSON 并返回 0；异常由 ``main``
        折叠为不回显底层值的退出码 2。
    """

    run_id = arguments.run_id or _new_run_id()
    receipt = run_osworld_artifact_component_candidate(
        OSWorldArtifactComponentCandidateConfig(
            repo_root=_absolute_path(arguments.repo_root),
            runs_root=_absolute_path(arguments.runs_root),
            asset_cache_root=_absolute_path(arguments.asset_cache_root),
            gold_cache_root=_absolute_path(arguments.gold_cache_root),
            qcow2_path=_absolute_path(arguments.qcow2_path),
            task_id=arguments.task_id,
            run_id=run_id,
            attempt_id=arguments.attempt_id,
            server_port=arguments.server_port,
            vnc_port=arguments.vnc_port,
            chromium_port=arguments.chromium_port,
            ram_size=arguments.ram_size,
            cpu_cores=arguments.cpu_cores,
            ready_timeout=arguments.ready_timeout,
        )
    )
    if not isinstance(receipt, OSWorldArtifactComponentReceipt):
        raise TypeError("OSWorld artifact component receipt 类型无效")
    print(
        json.dumps(
            receipt.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return 0


def _handle_webmall_cart_reference_validate(
    arguments: argparse.Namespace,
) -> int:
    """执行显式 candidate 验证并只输出 canonical 脱敏 JSON receipt。

    输入参数：arguments 为不含模型凭据或 origin 值的 argparse namespace。
    输出返回值：receipt 完整且安全序列化时为 0；异常由 ``main`` 折叠为 2。
    """

    receipt = _execute_webmall_cart_reference_validation(arguments)
    if not isinstance(receipt, WebMallCartReferenceReceipt):
        raise TypeError("WebMall Cart reference receipt 类型无效")
    print(
        json.dumps(
            receipt.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return 0


def _execute_webmall_cart_reference_validation(
    arguments: argparse.Namespace,
) -> WebMallCartReferenceReceipt:
    """候选 CLI 的 production live 纵向边界。

    输入参数：arguments 为 reference validation 的非敏感参数；部署 origin
        只从 manifest 指定的环境变量读取，不从命令行接收。
    输出返回值：owned 环境关闭后的脱敏 component receipt。
    异常：ValueError：task 不是 Cart、除显式 pending 项外的 doctor 门禁
        失败，或浏览器镜像尚未固定；其他固定错误由下层安全边界产生。
    """

    (
        repo_root,
        prepared_task,
        task_assets,
        artifact_prepare_binding,
    ) = _load_task_context(arguments)
    if artifact_prepare_binding is not None:
        raise ValueError("WebMall Cart reference task 不得装配 artifact binding")
    task_gold = _load_task_gold_context(
        repo_root=repo_root,
        prepared_task=prepared_task,
    )
    runtime = preflight_webmall_cart_reference_candidate_runtime(
        repo_root=repo_root,
        prepared_task=prepared_task,
        environment=os.environ,
    )
    if (
        runtime.evidence_mode is not WebMallEvidenceMode.BROWSER_CART
        or not runtime.browser_image.live_run_ready
    ):
        raise ValueError("WebMall Cart reference runtime 未就绪")
    webmall_manifest_sha256 = runtime.webmall_manifest_sha256
    component_revision = build_webmall_cart_reference_component_revision(
        runtime.version_vector,
        repo_root=repo_root,
    )
    report = _inspect_webmall_cart_reference_prerequisites(
        arguments=arguments,
        runtime=runtime,
        task_assets=task_assets,
        task_gold=task_gold,
    )
    if not report.ok:
        raise ValueError("WebMall Cart reference prerequisites 未通过")
    environment = _build_webmall_cart_reference_environment(
        arguments=arguments,
        repo_root=repo_root,
        runtime=runtime,
        task_assets=task_assets,
        artifact_prepare_binding=artifact_prepare_binding,
    )
    receipt = run_webmall_cart_reference_validation(
        environment=environment,
        task=runtime.prepared_task.trusted_task,
        manifest=runtime.manifest,
        browser_image=runtime.browser_image,
        webmall_manifest_sha256=webmall_manifest_sha256,
        component_revision=component_revision,
    )
    _verify_webmall_cart_reference_repository_identity(
        repo_root=repo_root,
        prepared_task=prepared_task,
        runtime=runtime,
    )
    return validate_webmall_cart_reference_receipt(
        receipt.to_dict(),
        manifest=runtime.manifest,
        browser_image=runtime.browser_image,
        expected_webmall_manifest_sha256=webmall_manifest_sha256,
        expected_component_revision=component_revision,
    )


def _inspect_webmall_cart_reference_prerequisites(
    *,
    arguments: argparse.Namespace,
    runtime: Any,
    task_assets: ResolvedTaskAssets,
    task_gold: ResolvedOSWorldTaskGold,
) -> DoctorReport:
    """检查 reference component 所需全部门禁并精确排除三项非依赖。

    输入参数：
        arguments：candidate 的镜像、缓存与端口参数。
        runtime：已完成正式 WebMall Cart preflight 的 runtime binding。
        task_assets/task_gold：canonical task 的统一资产与 gold 绑定。
    输出返回值：保留原顺序的报告；只移除模型 key、模型 endpoint 和本命令
        正在验证的 Cart reference pending 检查。
    异常：ValueError：底层报告没有暴露预期三项，避免名称漂移后意外放宽。
    """

    doctor_arguments = argparse.Namespace(**vars(arguments))
    doctor_arguments.api_key_env = "PARAGUIBENCH_REFERENCE_UNUSED_API_KEY"
    doctor_arguments.base_url_env = "PARAGUIBENCH_REFERENCE_UNUSED_BASE_URL"
    doctor_arguments.gold_cache_root = "~/.cache/paraguibench/gold"
    config = _doctor_config_from_context(
        doctor_arguments,
        image_manifest=runtime.browser_image,
        task_assets=task_assets,
        task_gold=task_gold,
    )
    osworld_report = inspect_osworld_prerequisites(
        config,
        environment={},
    )
    webmall_report = inspect_webmall_prerequisites(
        runtime.manifest,
        requires_privileged_order_evidence=False,
        requires_cart_evidence=True,
        cart_reference_validation_verified=(runtime.cart_reference_validation_verified),
        environment=os.environ,
    )
    ignored = {
        "api_key",
        "model_base_url",
        "webmall_cart_reader_reference_live_validation",
    }
    osworld_names = tuple(check.name for check in osworld_report.checks)
    webmall_names = tuple(check.name for check in webmall_report.checks)
    if (
        osworld_names.count("api_key") != 1
        or osworld_names.count("model_base_url") != 1
        or osworld_names.count("webmall_cart_reader_reference_live_validation") != 0
        or webmall_names.count("api_key") != 0
        or webmall_names.count("model_base_url") != 0
        or webmall_names.count("webmall_cart_reader_reference_live_validation") != 1
    ):
        raise ValueError("WebMall Cart reference doctor 闭集漂移")
    combined = osworld_report.checks + webmall_report.checks
    return DoctorReport(
        checks=tuple(check for check in combined if check.name not in ignored)
    )


def _verify_webmall_cart_reference_repository_identity(
    *,
    repo_root: Path,
    prepared_task: PreparedTask,
    runtime: WebMallRuntimeBinding,
) -> None:
    """在 live capture 后复验同源 manifest/browser 快照与全源码向量。

    输入参数：
        repo_root/prepared_task：本命令的正式仓库与 canonical task。
        runtime：preflight 从同次原始字节读取构造的初始身份。
    输出返回值：无；两份 manifest 对象/SHA 与正式版本向量
        在 capture 前后完全一致时返回。
    异常：ValueError：类型、task 身份或任一字节/对象版本漂移。
    """

    if (
        not isinstance(repo_root, Path)
        or not isinstance(prepared_task, PreparedTask)
        or not isinstance(runtime, WebMallRuntimeBinding)
    ):
        raise ValueError("WebMall Cart reference version identity 无效")
    manifest_path = _safe_child_file(
        repo_root,
        Path("environments/webmall/environment-manifest.json"),
    )
    manifest, manifest_sha256 = load_webmall_environment_manifest_with_sha256(
        manifest_path
    )
    browser_path = _safe_child_file(
        repo_root,
        _IMAGE_MANIFEST_RELATIVE,
    )
    browser_image, browser_sha256 = load_osworld_image_manifest_with_sha256(
        browser_path
    )
    task_id = prepared_task.trusted_task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("WebMall Cart reference version identity 无效")
    version_vector = build_run_version_vector(
        repo_root=repo_root,
        task_id=task_id,
        environment_manifest_path=manifest_path,
    )
    if (
        manifest != runtime.manifest
        or manifest_sha256 != runtime.webmall_manifest_sha256
        or browser_image != runtime.browser_image
        or browser_sha256 != runtime.browser_image_manifest_sha256
        or browser_sha256 != manifest.browser_runtime.image_manifest_sha256
        or version_vector != runtime.version_vector
    ):
        raise ValueError("WebMall Cart reference version identity 漂移")


def _build_webmall_cart_reference_environment(
    *,
    arguments: argparse.Namespace,
    repo_root: Path,
    runtime: Any,
    task_assets: ResolvedTaskAssets,
    artifact_prepare_binding: ArtifactFamilyTaskPrepareBinding | None,
) -> WebMallCartTaskEnvironment:
    """构造尚未启动的 owned VM 与同一 worker Cart reference 环境。

    输入参数：
        arguments：固定 qcow2、端口、资源上限与 repo 外资产缓存。
        repo_root/runtime：当前仓库与正式 WebMall Cart runtime binding。
        task_assets：已在 preflight/doctor 验证的 canonical 资产闭包。
        artifact_prepare_binding：Cart task 必须为 ``None``。
    输出返回值：未启动、未连接 CDP、未读取商店的 Cart 专属环境。
    异常：ValueError：误将 artifact-family task 装入 candidate。
    """

    del task_assets
    if artifact_prepare_binding is not None:
        raise ValueError("WebMall Cart reference 不接受 artifact binding")
    docker_config = OSWorldDockerConfig(
        container_name=f"paraguibench-cart-reference-{secrets.token_hex(8)}",
        image=runtime.browser_image.container_image,
        qcow2_path=Path(arguments.qcow2_path).expanduser().absolute(),
        server_port=arguments.server_port,
        vnc_port=arguments.vnc_port,
        chromium_port=arguments.chromium_port,
        ram_size=arguments.ram_size,
        cpu_cores=arguments.cpu_cores,
    )
    controller = OSWorldController(f"http://127.0.0.1:{arguments.server_port}")
    docker_session = WebMallCartAttestedDockerSession(
        config=docker_config,
        expected_qcow2_sha256=runtime.browser_image.extracted_sha256,
    )
    raw_environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=_absolute_path(arguments.asset_cache_root),
        docker_session=docker_session,
        controller=controller,
        artifact_family_task_prepare_binding=None,
        ready_timeout=arguments.ready_timeout,
    )
    worker_id = "worker-1"
    source = WebMallBrowserCartSource(
        registry=runtime.registry,
        cart_reader=runtime.manifest.cart_reader,
        worker_id=worker_id,
        host="127.0.0.1",
        chromium_port=arguments.chromium_port,
    )
    return WebMallCartTaskEnvironment(
        environment=raw_environment,
        evidence_source=source,
        worker_id=worker_id,
    )


def _handle_assets(arguments: argparse.Namespace) -> int:
    """下载或验证一个 release task 的固定资产闭集。

    输入参数：
        arguments：argparse 解析后的 repo、task、cache 和 action。
    输出返回值：
        资产完整时为 0，不完整 verify 为 2；输出不含来源 URL 或路径。
    """

    (
        repo_root,
        prepared_task,
        task_assets,
        artifact_prepare_binding,
    ) = _load_task_context(arguments)
    del repo_root, prepared_task, artifact_prepare_binding
    if task_assets.mode is TaskAssetMode.NONE:
        print("asset_set=NONE")
        print("files=0")
        print("status=PASS")
        return 0
    manifest = task_assets.manifest
    if manifest is None:
        raise ValueError("固定资产模式缺少 manifest")
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


def _handle_gold(arguments: argparse.Namespace) -> int:
    """显式预置或纯离线验证一个 task 的 evaluator gold 闭集。

    输入参数：
        arguments：argparse 解析后的 repo、task、私有 cache 与 action。
    输出返回值：
        全部必需 gold 可用时为 0；异常由 ``main`` 折叠为 2。输出仅含
        manifest ID、条目计数与 PASS，不含 URL、路径、摘要、key 或正文。
    """

    repo_root, prepared_task = _load_prepared_task_context(arguments)
    task_gold = _load_task_gold_context(
        repo_root=repo_root,
        prepared_task=prepared_task,
    )
    cache_root = _absolute_path(arguments.gold_cache_root)
    if task_gold.mode is TaskGoldMode.NONE:
        if arguments.gold_command == "materialize":
            raise ValueError("零 gold 任务不可物化")
        availability = task_gold.verify(cache_root)
        manifest_id = "NONE"
    else:
        manifest = task_gold.manifest
        if manifest is None:
            raise ValueError("外部 gold 模式缺少 manifest")
        if arguments.gold_command == "fetch":
            if task_gold.mode is not TaskGoldMode.PINNED_DOWNLOAD_MANIFEST:
                raise GoldFetchError
            availability = fetch_gold_assets(manifest, cache_root)
        elif arguments.gold_command == "materialize":
            if (
                task_gold.mode is not TaskGoldMode.PRIVATE_DERIVED_MANIFEST
                or type(manifest) is not DerivedGoldAssetManifest
            ):
                raise ValueError("只允许物化私有 derived gold")
            from paraguibench.runtime.derived_gold import materialize_derived_gold

            availability = materialize_derived_gold(
                manifest=manifest,
                repo_root=repo_root,
                asset_cache_root=_absolute_path(arguments.asset_cache_root),
                gold_cache_root=cache_root,
                ffmpeg_path=_absolute_path(arguments.ffmpeg_path),
                ffprobe_path=_absolute_path(arguments.ffprobe_path),
                timeout_seconds=arguments.timeout_seconds,
            )
        else:
            availability = task_gold.verify(cache_root)
        manifest_id = manifest.manifest_id
    print(f"gold_manifest={manifest_id}")
    print(f"entries={availability.requested_count}")
    print("status=PASS")
    return 0


def _handle_doctor(arguments: argparse.Namespace) -> int:
    """运行并打印当前 task 环境协议的全部部署检查。

    输入参数：
        arguments：argparse 解析后的非敏感配置和环境变量引用。
    输出返回值：
        全部检查通过时为 0，否则为 2。
    """

    (
        repo_root,
        prepared_task,
        task_assets,
        artifact_prepare_binding,
    ) = _load_task_context(arguments)
    del artifact_prepare_binding
    task_gold = _load_task_gold_context(
        repo_root=repo_root,
        prepared_task=prepared_task,
    )
    is_webmall = prepared_task.trusted_task.get("task_source") == "WebMall"
    webmall_manifest = None
    webmall_evidence_mode: WebMallEvidenceMode | None = None
    if is_webmall:
        webmall_identity = preflight_webmall_identity(
            repo_root=repo_root,
            prepared_task=prepared_task,
        )
        image_manifest = webmall_identity.browser_image
        webmall_manifest = webmall_identity.manifest
        webmall_evidence_mode = webmall_identity.evidence_mode
    else:
        image_manifest = _load_image_context(repo_root)
        _preflight_osworld_runtime(
            repo_root=repo_root,
            prepared_task=prepared_task,
            image_manifest=image_manifest,
        )
    config = _doctor_config_from_context(
        arguments,
        image_manifest=image_manifest,
        task_assets=task_assets,
        task_gold=task_gold,
    )
    osworld_report = inspect_osworld_prerequisites(config)
    report = osworld_report
    if webmall_manifest is not None:
        webmall_report = inspect_webmall_prerequisites(
            webmall_manifest,
            requires_privileged_order_evidence=(
                webmall_evidence_mode is WebMallEvidenceMode.PRIVILEGED_ORDER
            ),
            requires_cart_evidence=(
                webmall_evidence_mode is WebMallEvidenceMode.BROWSER_CART
            ),
            cart_reference_validation_verified=(
                webmall_identity.cart_reference_validation_verified
            ),
            environment=os.environ,
        )
        report = DoctorReport(
            checks=osworld_report.checks + webmall_report.checks,
        )
    _print_doctor_report(report)
    return 0 if report.ok else 2


def _build_gui_only_agent(
    arguments: argparse.Namespace,
    *,
    base_url: str,
) -> tuple[Any, str, dict[str, Any]]:
    """按 CLI worker 选择装配 GUI-only Agent System。

    输入参数：
        arguments：包含 worker、model、预算和 key 环境变量引用的 namespace。
        base_url：只从 ``--base-url-env`` 指向的环境变量读取的模型 endpoint。
    输出返回值：
        ``(agent, agent_system_id, public_config)``；后二者可安全写入
        RunStore，且不含凭据或 endpoint 值。
    异常：
        ValueError：请求了尚未暴露的 Agent System 或未知 worker。
    """

    if arguments.agent_system != "gui-only":
        raise ValueError("当前 CLI 只支持 gui-only Agent System")
    if arguments.worker == "seed18":
        max_output_tokens = (
            512 if arguments.max_output_tokens is None else arguments.max_output_tokens
        )
        model = Seed18OpenAIModel(
            Seed18ModelConfig(
                model=arguments.model,
                api_key_env=arguments.api_key_env,
                base_url=base_url,
                max_output_tokens=max_output_tokens,
            )
        )
        return (
            Seed18AgentSystem(
                model=model,
                max_steps=arguments.max_steps,
                post_action_delay=arguments.post_action_delay,
            ),
            "gui_only.seed18",
            {
                "worker": "seed18",
                "max_steps": arguments.max_steps,
                "post_action_delay": arguments.post_action_delay,
                "max_output_tokens": max_output_tokens,
            },
        )
    if arguments.worker == "qwen":
        max_output_tokens = (
            1024 if arguments.max_output_tokens is None else arguments.max_output_tokens
        )
        worker = QwenGUIWorker(
            config=QwenModelConfig(
                base_url=base_url,
                model=arguments.model,
                api_key_env=arguments.api_key_env,
                max_output_tokens=max_output_tokens,
                max_image_pixels=arguments.max_image_pixels,
                max_history_image_pixels=(arguments.max_history_image_pixels),
                enable_thinking=arguments.enable_thinking,
                tool_protocol=arguments.qwen_tool_protocol,
            ),
            max_steps=arguments.max_steps,
            post_action_delay=arguments.post_action_delay,
            screenshot_history_limit=arguments.qwen_visual_history,
        )
        return (
            QwenGUIOnlyAgentSystem(worker=worker),
            "gui_only.qwen",
            {
                "worker": "qwen",
                "max_steps": arguments.max_steps,
                "post_action_delay": arguments.post_action_delay,
                "max_output_tokens": max_output_tokens,
                "max_image_pixels": arguments.max_image_pixels,
                "max_history_image_pixels": (arguments.max_history_image_pixels),
                "screenshot_history_limit": arguments.qwen_visual_history,
                "enable_thinking": arguments.enable_thinking,
                "tool_protocol": arguments.qwen_tool_protocol,
            },
        )
    raise ValueError("未知 GUI worker")


def _build_agent(
    arguments: argparse.Namespace,
    *,
    worker_base_url: str,
    planner_base_url: str | None,
) -> tuple[Any, str, dict[str, Any]]:
    """按公开 Agent System 选项装配 GUI-only 或单 VM ParaGUI。

    输入参数：
        arguments：包含 Agent System、worker、planner 和成本边界的
            argparse namespace。
        worker_base_url：Qwen/Seed18 从环境变量引用解析的模型
            endpoint；不进入公开配置。
        planner_base_url：Kimi 从 planner 环境变量引用解析的
            模型 endpoint；GUI-only 时可为 ``None``。
    输出返回值：
        ``(agent, agent_system_id, public_config)``；公开配置只含
        可复现的模型名、资源边界和执行语义。
    异常：
        ValueError：ParaGUI 选择了非 Qwen worker，或缺少 planner endpoint。
    """

    if arguments.agent_system == "gui-only":
        return _build_gui_only_agent(
            arguments,
            base_url=worker_base_url,
        )
    if arguments.agent_system != "paragui-single-vm":
        raise ValueError("未知 Agent System")
    if arguments.worker != "qwen":
        raise ValueError("paragui-single-vm 当前只支持 qwen worker")
    if not isinstance(planner_base_url, str) or not planner_base_url:
        raise ValueError("paragui-single-vm 缺少 planner endpoint")

    worker_max_output_tokens = (
        1024 if arguments.max_output_tokens is None else arguments.max_output_tokens
    )
    worker_config = QwenModelConfig(
        base_url=worker_base_url,
        model=arguments.model,
        api_key_env=arguments.api_key_env,
        max_output_tokens=worker_max_output_tokens,
        max_image_pixels=arguments.max_image_pixels,
        max_history_image_pixels=arguments.max_history_image_pixels,
        enable_thinking=arguments.enable_thinking,
        tool_protocol=arguments.qwen_tool_protocol,
    )
    planner = StructuredParaGUIPlanner(
        backend=KimiOpenAIPlanningBackend(
            KimiPlannerConfig(
                base_url=planner_base_url,
                model=arguments.planner_model,
                api_key_env=arguments.planner_api_key_env,
                max_output_tokens=arguments.planner_max_output_tokens,
                max_subtasks=arguments.planner_max_subtasks,
            )
        )
    )

    def build_qwen_worker() -> QwenGUIWorker:
        """为每个顺序 subtask 构造独立 Qwen GUI worker。

        输入参数：
            无；闭包只读取上述非敏感配置和 CLI 资源边界。
        输出返回值：
            不共享可变模型会话状态的 ``QwenGUIWorker``。
        """

        return QwenGUIWorker(
            config=worker_config,
            max_steps=arguments.max_steps,
            post_action_delay=arguments.post_action_delay,
            screenshot_history_limit=arguments.qwen_visual_history,
        )

    agent = ParaGUIAgentSystem(
        planner=planner,
        worker=GUIWorkerParaGUIAdapter(worker_factory=build_qwen_worker),
        max_workers=1,
    )
    return (
        agent,
        "paragui.kimi_qwen.sequential_single_vm",
        {
            "execution_mode": "sequential_single_vm",
            "parallel_execution": False,
            "environment_count": 1,
            "scheduler_max_workers": 1,
            "planner": {
                "model": arguments.planner_model,
                "max_subtasks": arguments.planner_max_subtasks,
                "max_output_tokens": arguments.planner_max_output_tokens,
                "tool_protocol": "native_function_call",
            },
            "worker": {
                "model": arguments.model,
                "type": "qwen",
                "max_steps_per_subtask": arguments.max_steps,
                "post_action_delay": arguments.post_action_delay,
                "max_output_tokens": worker_max_output_tokens,
                "max_image_pixels": arguments.max_image_pixels,
                "max_history_image_pixels": (arguments.max_history_image_pixels),
                "screenshot_history_limit": arguments.qwen_visual_history,
                "enable_thinking": arguments.enable_thinking,
                "tool_protocol": arguments.qwen_tool_protocol,
            },
        },
    )


def _build_osworld_state_evidence_source(
    arguments: argparse.Namespace,
) -> OSWorldChromeStateEvidenceSource:
    """将当前 Docker session 的成对 API/CDP 端口绑定到状态证据源。

    输入参数：
        arguments：已通过 CLI parser 的 run 参数；必须含
            ``server_port`` 与 ``chromium_port``。
    输出返回值：
        profile 证据读取与 Google Shopping AT→CDP→AT 探针
        共用的 ``OSWorldChromeStateEvidenceSource``。
    安全边界：
        闭包只保存 loopback 与两个非敏感端口，不保存凭据、
        URL 或筛选标签；probe 异常由 evidence source 折叠为类型安全错误。
    """

    def load_active_tab(_controller: object):
        """从与当前 controller 同容器的宿主映射捕获活动页。

        输入参数：
            _controller：evidence source 统一 loader 接口的当前
                controller；生产 probe 通过成对宿主端口读取。
        输出返回值：
            单时点 Google Shopping 活动标签页 observation。
        """

        return capture_google_shopping_active_tab_observation(
            host="127.0.0.1",
            chromium_port=arguments.chromium_port,
            server_port=arguments.server_port,
        )

    return OSWorldChromeStateEvidenceSource(
        active_tab_loader=load_active_tab,
    )


def _build_osworld_bookmark_evidence_source() -> OSWorldChromeBookmarkEvidenceSource:
    """构造固定协议且不保存凭据的 Chrome Bookmarks source。

    输入参数：
        无。
    输出返回值：
        生产 ``OSWorldChromeBookmarkEvidenceSource``；构造阶段不访问
        VM、Chrome profile、文件、网络或任何 secret。
    """

    return OSWorldChromeBookmarkEvidenceSource()


def _build_osworld_artifact_evidence_source(
    *,
    gold_resolver: GoldAssetResolver | None,
) -> OSWorldArtifactEvidenceSource:
    """构造绑定仓库版本化 spec catalog 的 artifact 证据源。

    输入参数：
        gold_resolver：当前 task 已绑定、且由 doctor 离线验证的私有
            resolver；无 external gold 的任务必须显式传 ``None``。
    输出返回值：
        production ``OSWorldArtifactEvidenceSource``；构造阶段只验证
        内存 spec 与 canonical SHA，不读取 guest、artifact、gold、网络
        或任何凭据。resolver 只保存 manifest/cache 绑定；尚未实现的
        finalize/getter 会在捕获时显式失败。
    """

    return OSWorldArtifactEvidenceSource(gold_resolver=gold_resolver)


def _build_osworld_artifact_finalizer() -> OSWorldArtifactFinalizer:
    """构造固定 action catalog 的生产 artifact finalizer。

    输入参数：
        无。
    输出返回值：
        新的 ``OSWorldArtifactFinalizer``；构造阶段不访问 VM、文件、
        网络、窗口、controller 或任何凭据。
    """

    return OSWorldArtifactFinalizer()


def _build_osworld_operation_artifact_source() -> OSWorldOperationArtifactSource:
    """构造绑定 32-task 规则闭集的 Operation artifact source。

    输入参数：
        无。
    输出返回值：
        production ``OSWorldOperationArtifactSource``；构造阶段不读取
        guest、artifact、gold、网络或任何 secret，capture 只在
        Agent 结束后由 evaluator 触发。
    """

    return OSWorldOperationArtifactSource()


def _build_pipeline_implicit_evidence_source() -> (
    PipelineImplicitArtifactEvidenceSource
):
    """构造四任务受控完整 artifact bundle source。

    输入参数：无。
    输出返回值：
        绑定固定 canonical task/protocol 闭集的 source；构造阶段不读取
        guest、artifact、manifest、网络、凭据或 Agent 文本。
    """

    return PipelineImplicitArtifactEvidenceSource()


def _handle_run(arguments: argparse.Namespace) -> int:
    """装配并执行 GUI-only 或单 VM 串行 ParaGUI 纵向切片。

    输入参数：
        arguments：argparse 解析后的运行配置；API key 和 endpoint 值只从
            两个显式环境变量引用读取。
    输出返回值：
        评价通过时为 0，已执行但未通过时为 1，门禁失败或异常为 2。
    """

    (
        repo_root,
        prepared_task,
        task_assets,
        artifact_prepare_binding,
    ) = _load_task_context(arguments)
    task_gold = _load_task_gold_context(
        repo_root=repo_root,
        prepared_task=prepared_task,
    )
    is_webmall = prepared_task.trusted_task.get("task_source") == "WebMall"
    webmall_runtime = None
    pipeline_runtime_capability: PipelineImplicitRuntimeCapability | None = None
    if is_webmall:
        webmall_runtime = preflight_webmall_runtime(
            repo_root=repo_root,
            prepared_task=prepared_task,
            environment=os.environ,
        )
        prepared_task = webmall_runtime.prepared_task
        image_manifest = webmall_runtime.browser_image
        version_vector = webmall_runtime.version_vector
        evaluator = webmall_runtime.evaluator
    else:
        image_manifest = _load_image_context(repo_root)
        (
            version_vector,
            evaluator,
            pipeline_runtime_capability,
        ) = _preflight_osworld_runtime(
            repo_root=repo_root,
            prepared_task=prepared_task,
            image_manifest=image_manifest,
        )
    task = prepared_task.trusted_task
    doctor_config = _doctor_config_from_context(
        arguments,
        image_manifest=image_manifest,
        task_assets=task_assets,
        task_gold=task_gold,
    )
    osworld_report = inspect_osworld_prerequisites(doctor_config)
    report = osworld_report
    if webmall_runtime is not None:
        webmall_report = inspect_webmall_prerequisites(
            webmall_runtime.manifest,
            requires_privileged_order_evidence=(
                webmall_runtime.requires_privileged_order_evidence
            ),
            requires_cart_evidence=webmall_runtime.requires_cart_evidence,
            cart_reference_validation_verified=(
                webmall_runtime.cart_reference_validation_verified
            ),
            environment=os.environ,
        )
        report = DoctorReport(
            checks=osworld_report.checks + webmall_report.checks,
        )
    _print_doctor_report(report)
    if not report.ok or not image_manifest.live_run_ready:
        return 2

    run_id = arguments.run_id or _new_run_id()
    attempt_id = validate_identifier("attempt_id", arguments.attempt_id)
    validate_identifier("run_id", run_id)
    task_id = validate_identifier("task_id", str(task["task_id"]))
    privileged_webmall = None
    if (
        webmall_runtime is not None
        and webmall_runtime.requires_privileged_order_evidence
    ):
        lease_attempt_id = (
            "attempt-"
            + hashlib.sha256(
                f"{run_id}\0{task_id}\0{attempt_id}".encode("utf-8")
            ).hexdigest()
        )
        privileged_webmall = bind_webmall_privileged_runtime(
            repo_root=repo_root,
            runtime=webmall_runtime,
            environment=os.environ,
            attempt_id=lease_attempt_id,
            owner_id=f"worker-{secrets.token_hex(16)}",
        )

    worker_base_url = os.environ.get(arguments.base_url_env)
    if not isinstance(worker_base_url, str) or not worker_base_url:
        return 2
    planner_base_url: str | None = None
    if arguments.agent_system == "paragui-single-vm":
        planner_base_url = os.environ.get(arguments.planner_base_url_env)
        if (
            not isinstance(planner_base_url, str)
            or not planner_base_url
            or not os.environ.get(arguments.planner_api_key_env)
        ):
            return 2
    agent, agent_system_id, agent_configuration = _build_agent(
        arguments,
        worker_base_url=worker_base_url,
        planner_base_url=planner_base_url,
    )
    container_name = f"paraguibench-{secrets.token_hex(8)}"
    if pipeline_runtime_capability is not None and (
        pipeline_runtime_capability.environment_manifest_sha256
        != image_manifest.manifest_sha256
        or pipeline_runtime_capability.container_image != image_manifest.container_image
        or pipeline_runtime_capability.extracted_qcow2_sha256
        != image_manifest.extracted_sha256
        or pipeline_runtime_capability.environment_identity_sha256 is None
    ):
        raise ValueError("pipeline-implicit image capability 不一致")
    docker_config = OSWorldDockerConfig(
        container_name=container_name,
        image=image_manifest.container_image,
        qcow2_path=doctor_config.qcow2_path,
        server_port=arguments.server_port,
        vnc_port=arguments.vnc_port,
        chromium_port=arguments.chromium_port,
        ram_size=arguments.ram_size,
        cpu_cores=arguments.cpu_cores,
    )
    controller = OSWorldController(f"http://127.0.0.1:{arguments.server_port}")
    gold_resolver = task_gold.build_resolver(doctor_config.gold_cache_root)
    raw_environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=doctor_config.asset_cache_root,
        docker_session=OSWorldDockerSession(docker_config),
        controller=controller,
        artifact_family_task_prepare_binding=(artifact_prepare_binding),
        bookmark_evidence_source=_build_osworld_bookmark_evidence_source(),
        state_evidence_source=_build_osworld_state_evidence_source(arguments),
        artifact_finalizer=_build_osworld_artifact_finalizer(),
        artifact_evidence_source=_build_osworld_artifact_evidence_source(
            gold_resolver=gold_resolver,
        ),
        operation_artifact_source=(_build_osworld_operation_artifact_source()),
        pipeline_implicit_evidence_source=(_build_pipeline_implicit_evidence_source()),
        pipeline_implicit_runtime_capability=pipeline_runtime_capability,
        ready_timeout=arguments.ready_timeout,
    )
    environment: Any = raw_environment
    if webmall_runtime is not None:
        if webmall_runtime.evidence_mode is WebMallEvidenceMode.PRIVILEGED_ORDER:
            if privileged_webmall is None:
                raise ValueError("WebMall privileged runtime 未装配")
            environment = WebMallTaskEnvironment(
                environment=raw_environment,
                evidence_session=privileged_webmall.session,
                registry=webmall_runtime.registry,
            )
        elif webmall_runtime.evidence_mode is WebMallEvidenceMode.BROWSER_CART:
            if privileged_webmall is not None:
                raise ValueError("WebMall Cart runtime 不得装配特权证据")
            cart_worker_id = "worker-1"
            cart_source = WebMallBrowserCartSource(
                registry=webmall_runtime.registry,
                cart_reader=webmall_runtime.manifest.cart_reader,
                worker_id=cart_worker_id,
                host="127.0.0.1",
                chromium_port=arguments.chromium_port,
            )
            environment = WebMallCartTaskEnvironment(
                environment=raw_environment,
                evidence_source=cart_source,
                worker_id=cart_worker_id,
            )
        elif webmall_runtime.evidence_mode is WebMallEvidenceMode.REPORTED_URL:
            if privileged_webmall is not None:
                raise ValueError("WebMall URL runtime 不得装配特权证据")
            environment = WebMallURLTaskEnvironment(
                environment=raw_environment,
                registry=webmall_runtime.registry,
            )
        else:
            raise ValueError("WebMall evidence mode 不受支持")
    if arguments.agent_system == "paragui-single-vm":
        environment = SingleVMEnvironmentLeaseAdapter(environment)
    store = RunStore(_absolute_path(arguments.runs_root))
    run_record: dict[str, Any] = {
        "release_id": "release-v1",
        "task_id": task_id,
        "agent_system": agent_system_id,
        "model": arguments.model,
        "agent_configuration": agent_configuration,
        "credential_reference": arguments.api_key_env,
        "credential_status": "PRESENT",
        "endpoint_reference": arguments.base_url_env,
        "environment_id": (
            webmall_runtime.manifest.environment_id
            if webmall_runtime is not None
            else image_manifest.environment_id
        ),
        "container_image": image_manifest.container_image,
        "qcow2_sha256": image_manifest.extracted_sha256,
    }
    if webmall_runtime is not None:
        run_record.update(
            {
                "browser_environment_id": image_manifest.environment_id,
                "webmall_manifest_id": webmall_runtime.manifest.manifest_id,
                "webmall_store_universe_id": (
                    webmall_runtime.manifest.store_universe_id
                ),
            }
        )
        if webmall_runtime.evidence_mode is WebMallEvidenceMode.PRIVILEGED_ORDER:
            run_record.update(
                {
                    "webmall_order_reader_protocol": (
                        webmall_runtime.manifest.order_reader.protocol_id
                    ),
                    "webmall_lease_protocol": (
                        webmall_runtime.manifest.lease.protocol_id
                    ),
                    "webmall_lease_coordinator_reference": (
                        webmall_runtime.manifest.lease.coordinator_url_env
                    ),
                    "webmall_lease_credential_reference": (
                        webmall_runtime.manifest.lease.credential_env
                    ),
                }
            )
        elif webmall_runtime.evidence_mode is WebMallEvidenceMode.BROWSER_CART:
            run_record.update(
                {
                    "webmall_cart_reader_protocol": (
                        webmall_runtime.manifest.cart_reader.protocol_id
                    ),
                    "webmall_cart_evidence_protocol": (
                        webmall_runtime.manifest.cart_reader.evidence_protocol_id
                    ),
                    "webmall_cart_reference_live_validation_status": (
                        "live_validated"
                        if webmall_runtime.cart_reference_validation_verified
                        else "pending"
                    ),
                }
            )
    if arguments.agent_system == "paragui-single-vm":
        run_record.update(
            {
                "planner_model": arguments.planner_model,
                "planner_credential_reference": (arguments.planner_api_key_env),
                "planner_credential_status": "PRESENT",
                "planner_endpoint_reference": (arguments.planner_base_url_env),
            }
        )
    store.start_run(
        run_id=run_id,
        run_record=run_record,
        version_vector=version_vector,
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
        evaluator=evaluator,
    )
    print(f"run_id={run_id}")
    print(f"task_id={task_id}")
    print(f"attempt_id={attempt_id}")
    print(f"execution={result.execution_outcome.value}")
    print(f"evaluation={result.evaluation_outcome.value}")
    print(f"score={result.score}")
    return (
        0
        if (
            result.execution_outcome is ExecutionOutcome.SUCCEEDED
            and result.evaluation_outcome is EvaluationOutcome.PASSED
        )
        else 1
    )


def _handle_inspect(arguments: argparse.Namespace) -> int:
    """通过 RunStore 安全投影打印 Attempt 终态与可选诊断。

    输入参数：
        arguments：RunStore 根和三个已验证稳定 ID。
    输出返回值：
        evaluation=PASSED 时为 0，否则为 1；默认只输出三项终态，显式
        ``--diagnostics`` 才追加枚举阶段和固定版本身份。无效路径或记录由
        main 折叠为类型安全的错误并返回 2。
    """

    root = _absolute_path(arguments.runs_root)
    run_id = validate_identifier("run_id", arguments.run_id)
    task_id = validate_identifier("task_id", arguments.task_id)
    attempt_id = validate_identifier("attempt_id", arguments.attempt_id)
    inspection = RunStore(root).inspect_attempt(
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
    )
    execution = inspection.execution_outcome.value
    evaluation = inspection.evaluation_outcome.value
    score = inspection.score
    print(f"execution={execution}")
    print(f"evaluation={evaluation}")
    print(f"score={score}")
    if arguments.diagnostics:
        print(f"failure_stage={inspection.failure_stage.value}")
        print(f"provenance={inspection.provenance_status.value}")
        vector = inspection.version_vector
        if vector is not None:
            print(f"source_revision={vector.source_revision}")
            print(f"agent_code_revision={vector.agent_code_revision}")
            print(f"evaluator_revision={vector.evaluator_revision}")
            print(f"evaluation_protocol={vector.evaluation_protocol}")
            print(f"environment_protocol={vector.environment_protocol}")
            print(f"environment_revision={vector.environment_revision}")
    return 0 if evaluation == "PASSED" else 1


def _load_task_context(
    arguments: argparse.Namespace,
) -> tuple[
    Path,
    PreparedTask,
    ResolvedTaskAssets,
    ArtifactFamilyTaskPrepareBinding | None,
]:
    """加载固定 release task 并统一解析任务文件资产契约。

    输入参数：
        arguments：包含 repo_root 与 task_id 的 argparse namespace。
    输出返回值：
        已 resolve 仓库根、三投影 PreparedTask、不可变资产解析结果，以及
        可选 artifact-family prepare 运行时绑定；零资产任务不会伪造空
        manifest。
    异常：
        ValueError：task 的资产声明未迁移、路径越界或 manifest 无效。
        ArtifactFamilyTaskPrepareCapabilityError：13-task 的来源上下文、
            input integrity、许可或严格 manifest 尚未闭合。该门禁先于统一
            asset resolver、Docker、guest、凭据、Agent 与 RunStore。
    """

    repo_root, prepared_task = _load_prepared_task_context(arguments)
    artifact_prepare_binding = preflight_artifact_family_task_prepare(
        repo_root=repo_root,
        task=prepared_task.trusted_task,
    )
    task_assets = resolve_task_assets(
        repo_root,
        prepared_task.trusted_task,
    )
    return (
        repo_root,
        prepared_task,
        task_assets,
        artifact_prepare_binding,
    )


def _load_prepared_task_context(
    arguments: argparse.Namespace,
) -> tuple[Path, PreparedTask]:
    """加载 release task，但不解析 input 或 evaluator gold 资产。

    输入参数：
        arguments：包含 repo_root 与 canonical task_id 的 namespace。
    输出返回值：
        已 resolve 仓库根，以及通过 release SHA/task identity/三投影校验的
        ``PreparedTask``。调用方再按命令职责独立解析 input assets 或 gold。
    """

    repo_root = Path(arguments.repo_root).expanduser().resolve()
    prepared_task = prepare_release_task(
        repo_root,
        arguments.task_id,
        environment_bindings={},
    )
    return repo_root, prepared_task


def _load_task_gold_context(
    *,
    repo_root: Path,
    prepared_task: PreparedTask,
) -> ResolvedOSWorldTaskGold:
    """安全加载并语义绑定 canonical task 的 evaluator-only gold。

    输入参数：
        repo_root：已 resolve 的 release 仓库根。
        prepared_task：已通过 release 摘要与三投影校验的任务。
    输出返回值：
        无 external gold 时为 ``NONE``；有声明时返回已与 artifact spec
        logical key 及 provenance 闭合的 pinned manifest 绑定。Pipeline 专属
        ``gold_manifest`` 先经 production local capability 复验；四个已闭合
        pipeline 任务的 reference 都只是纯 evaluator 机器身份合同，
        因此返回 ``NONE`` 且不误入通用 gold cache。
    异常：
        ValueError/GoldManifestError/OSWorldGoldBindingError：task 引用不是
            安全相对路径、目标不是 repo 内普通文件、manifest 无效或跨文件
            身份不一致。任何错误均发生在缓存、VM、Agent 和 RunStore 前。
    """

    task = prepared_task.trusted_task
    task_id = validate_identifier("task_id", str(task["task_id"]))
    if task_id in PIPELINE_IMPLICIT_FORMAL_ASSET_READY_TASK_IDS:
        capability = preflight_pipeline_implicit_local_runtime(
            repo_root=repo_root,
            task=task,
        )
        if capability is None or capability.task_id != task_id:
            raise ValueError("pipeline-implicit gold 绑定无效")
        return bind_osworld_task_gold(
            task_id,
            None,
            task_uid=None,
            evaluator_path=None,
        )
    raw_reference = task.get("gold_manifest")
    manifest = None
    if raw_reference is not None:
        if (
            not isinstance(raw_reference, str)
            or not raw_reference
            or "\\" in raw_reference
            or "\x00" in raw_reference
        ):
            raise ValueError("gold manifest 引用无效")
        manifest_path = _safe_child_file(repo_root, Path(raw_reference))
        manifest = load_gold_asset_manifest(manifest_path)
    task_uid = task.get("task_uid")
    evaluator_path = task.get("evaluator_path")
    return bind_osworld_task_gold(
        task_id,
        manifest,
        task_uid=task_uid if isinstance(task_uid, str) else None,
        evaluator_path=(evaluator_path if isinstance(evaluator_path, str) else None),
        asset_manifest_reference=(
            task.get("asset_manifest")
            if isinstance(manifest, DerivedGoldAssetManifest)
            and isinstance(task.get("asset_manifest"), str)
            else None
        ),
    )


def _load_image_context(repo_root: Path) -> OSWorldImageManifest:
    """加载仓库固定位置的 OSWorld image manifest。

    输入参数：
        repo_root：已 resolve 的 ParaGUIBench 仓库根目录。
    输出返回值：
        已验证但 extracted digest 可空的 OSWorldImageManifest。
    """

    path = _safe_child_file(repo_root, _IMAGE_MANIFEST_RELATIVE)
    return load_osworld_image_manifest(path)


def _preflight_osworld_runtime(
    *,
    repo_root: Path,
    prepared_task: PreparedTask,
    image_manifest: OSWorldImageManifest | None = None,
) -> tuple[
    RunVersionVector,
    TaskEvaluator,
    PipelineImplicitRuntimeCapability | None,
]:
    """在任何 OSWorld probe 或 RunStore 写入前闭合环境与评价协议绑定。

    输入参数：
        repo_root：release、runtime-support 与 OSWorld manifest 所在仓库根。
        prepared_task：已完成 release 摘要与三投影校验的 canonical task。
    输出返回值：
        与实际 OSWorld manifest 一致的版本向量、按该向量中固定
        evaluation protocol 选出的 evaluator，以及必须穿透到
        environment.prepare 的可空 pipeline 机器身份。
    异常：
        RunVersioningError/UnsupportedTaskEvaluatorError：task 环境不是当前
            OSWorld binding、manifest 协议错配或 evaluator 尚未迁移；调用方
            必须在 probe、凭据读取和持久化前失败关闭。
    """

    task_id = validate_identifier(
        "task_id",
        str(prepared_task.trusted_task["task_id"]),
    )
    current_image_manifest, current_manifest_sha256 = (
        load_osworld_image_manifest_with_sha256(
            _safe_child_file(repo_root, _IMAGE_MANIFEST_RELATIVE)
        )
    )
    if image_manifest is not None and (
        type(image_manifest) is not OSWorldImageManifest
        or image_manifest != current_image_manifest
        or image_manifest.manifest_sha256 != current_manifest_sha256
    ):
        raise ValueError("OSWorld image manifest 快照已漂移")
    selected_image_manifest = current_image_manifest
    if type(selected_image_manifest) is not OSWorldImageManifest:
        raise ValueError("OSWorld image manifest 类型无效")
    pipeline_runtime_capability = preflight_pipeline_implicit_runtime(
        repo_root=repo_root,
        task=prepared_task.trusted_task,
        image_manifest=selected_image_manifest,
    )
    version_vector = build_run_version_vector(
        repo_root=repo_root,
        task_id=task_id,
        environment_manifest_path=(repo_root / _IMAGE_MANIFEST_RELATIVE),
        environment_manifest_sha256=(selected_image_manifest.manifest_sha256),
        environment_protocol_ids=selected_image_manifest.protocol_ids,
    )
    evaluator = build_task_evaluator(
        prepared_task.trusted_task,
        evaluation_protocol=version_vector.evaluation_protocol,
    )
    return version_vector, evaluator, pipeline_runtime_capability


def _doctor_config_from_context(
    arguments: argparse.Namespace,
    *,
    image_manifest: OSWorldImageManifest,
    task_assets: ResolvedTaskAssets,
    task_gold: ResolvedOSWorldTaskGold,
) -> OSWorldDoctorConfig:
    """由镜像与统一任务资产契约构造 doctor config。

    输入参数：
        arguments：doctor/run 共用 CLI 选项。
        image_manifest：固定 OSWorld 镜像身份。
        task_assets：当前 task 的零资产或固定下载资产契约。
        task_gold：已与 task/spec/provenance 绑定的 evaluator gold 闭集。
    输出返回值：
        可执行全部本机门禁的 OSWorldDoctorConfig。
    """

    return OSWorldDoctorConfig(
        image_manifest=image_manifest,
        qcow2_path=Path(arguments.qcow2_path).expanduser().absolute(),
        task_assets=task_assets,
        asset_cache_root=_absolute_path(arguments.asset_cache_root),
        server_port=arguments.server_port,
        vnc_port=arguments.vnc_port,
        chromium_port=arguments.chromium_port,
        api_key_env=arguments.api_key_env,
        base_url_env=arguments.base_url_env,
        task_gold=task_gold,
        gold_cache_root=_absolute_path(arguments.gold_cache_root),
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
