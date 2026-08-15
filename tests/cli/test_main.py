"""公开 CLI 的 secret-reference 与只读 inspect 输出契约测试。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paraguibench.agents.systems.gui_only import QwenGUIOnlyAgentSystem
from paraguibench.agents.systems.gui_only.seed18 import Seed18AgentSystem
from paraguibench.agents.systems.paragui import ParaGUIAgentSystem
from paraguibench.cli.main import (
    _build_agent,
    _build_gui_only_agent,
    _build_osworld_artifact_evidence_source,
    _build_osworld_artifact_finalizer,
    _build_osworld_bookmark_evidence_source,
    _build_osworld_operation_artifact_source,
    _build_pipeline_implicit_evidence_source,
    _build_osworld_state_evidence_source,
    _load_task_gold_context,
    _preflight_osworld_runtime,
    build_parser,
    main,
)
from paraguibench.benchmark import PreparedTask
from paraguibench.runtime.osworld_artifact_evidence import (
    OSWorldArtifactEvidenceSource,
)
from paraguibench.integrations.osworld.artifact_finalizer import (
    OSWorldArtifactFinalizer,
)
from paraguibench.integrations.osworld.bookmark_evidence import (
    OSWorldChromeBookmarkEvidenceSource,
)
from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OSWorldOperationArtifactSource,
)
from paraguibench.integrations.pipeline_implicit import (
    PipelineImplicitArtifactEvidenceSource,
)
from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest,
)
from paraguibench.runtime.gold_assets import (
    GoldAssetResolver,
    GoldAvailability,
    GoldAvailabilityStatus,
    load_gold_asset_manifest,
)
from paraguibench.runtime.assets import TaskAssetMode
from paraguibench.runtime.doctor import DoctorCheck, DoctorReport
from paraguibench.runtime.osworld_gold import TaskGoldMode
from paraguibench.runtime.attempt_runner import RuntimeAttemptResult
from paraguibench.runtime.artifact_family_task_prepare import (
    ArtifactFamilyTaskPrepareBinding,
)
from paraguibench.runtime.evaluators import (
    OSWorldBookmarkTaskEvaluator,
    PipelineImplicitTaskEvaluator,
)
from paraguibench.runtime.osworld_environment import OSWorldTaskEnvironment
from paraguibench.runtime.pipeline_implicit_binding import (
    PipelineImplicitRuntimeBlockedError,
    PipelineImplicitRuntimeCapability,
)
from paraguibench.runtime.webmall_environment import WebMallTaskEnvironment
from paraguibench.runtime.webmall_cart_environment import (
    WebMallCartTaskEnvironment,
)
from paraguibench.runtime.webmall_url_environment import (
    WebMallURLTaskEnvironment,
)
from paraguibench.evaluation.osworld import (
    GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
    GoogleShoppingActiveTabObservation,
)
from paraguibench.runstore import (
    AttemptFailureStage,
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
)
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


BIBTEX_GOLD_KEY = "osworld-gold:df67aebb-fb3a-44fd-b75b-51b6012df509:expected:0:v1"
_PARSE_ERROR_SENTINEL = "SYNTHETIC_SENTINEL_NOT_A_SECRET"


def _synthetic_combinationdocs_prepared_task(
    gold_manifest: str,
) -> PreparedTask:
    """构造仅供 gold 路径门禁测试的可信投影容器。

    输入参数：
        gold_manifest：待注入 trusted evaluator 视图的仓库相对引用。
    输出返回值：
        包含 015 固定 task/evaluator 身份的 ``PreparedTask``；Agent/audit
        投影为空，测试不会把 gold 引用下发或持久化。
    """

    return PreparedTask(
        trusted_task={
            "task_id": "Operation-FileOperate-CombinationDocs-015",
            "task_uid": "9f55fdb6-a749-4170-91a2-bebddd3492d7",
            "evaluator_path": (
                "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
            ),
            "gold_manifest": gold_manifest,
        },
        agent_task={},
        audit_metadata={},
    )


def _install_synthetic_live_ready_image(
    *,
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """为只验证 CLI 后续装配的 tracer 注入合成可运行镜像。

    输入参数：
        repo_root：提供正式 manifest 字段与 WebMall reader 脚本的仓库根。
        tmp_path：pytest 提供的隔离目录，用于物化测试专用 manifest。
        monkeypatch：只替换 CLI、Pipeline 与 WebMall 已导入的
            image loader，以及 WebMall manifest loader。
    输出返回值：
        无；正式仓库仍保持 ``live_run_ready=False``，仅当前
        synthetic tracer 获得内容完整且 digest 闭合的临时镜像。
    """

    fixture_root = tmp_path / "synthetic-live-image-manifests"
    webmall_root = fixture_root / "webmall"
    osworld_root = fixture_root / "osworld"
    webmall_root.mkdir(parents=True)
    osworld_root.mkdir(parents=True)

    image_source = repo_root / "environments/osworld/image-manifest.json"
    image_payload = json.loads(image_source.read_text(encoding="utf-8"))
    synthetic_qcow2 = b"synthetic-browser-image"
    synthetic_sha256 = hashlib.sha256(synthetic_qcow2).hexdigest()
    image_payload["extracted_image"]["sha256"] = synthetic_sha256
    image_payload["extracted_image"]["status"] = "verified_reproducible_materialization"
    image_payload["materialization"]["output_sha256"] = synthetic_sha256
    image_bytes = (
        json.dumps(image_payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    image_path = osworld_root / "image-manifest.json"
    image_path.write_bytes(image_bytes)

    webmall_source = repo_root / "environments/webmall/environment-manifest.json"
    webmall_payload = json.loads(webmall_source.read_text(encoding="utf-8"))
    webmall_payload["browser_runtime"]["image_manifest_sha256"] = hashlib.sha256(
        image_bytes
    ).hexdigest()
    webmall_path = webmall_root / "environment-manifest.json"
    webmall_path.write_text(
        json.dumps(webmall_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    reader_source = repo_root / "environments/webmall/wp-order-evidence.php"
    (webmall_root / "wp-order-evidence.php").write_bytes(reader_source.read_bytes())

    image_manifest = load_osworld_image_manifest(image_path)
    webmall_manifest = load_webmall_environment_manifest(webmall_path)
    image_manifest_sha256 = hashlib.sha256(image_bytes).hexdigest()
    webmall_manifest_sha256 = hashlib.sha256(webmall_path.read_bytes()).hexdigest()
    assert image_manifest.live_run_ready is True
    monkeypatch.setattr(
        "paraguibench.cli.main._load_image_context",
        lambda _repo_root: image_manifest,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.load_osworld_image_manifest_with_sha256",
        lambda _path: (image_manifest, image_manifest_sha256),
    )
    monkeypatch.setattr(
        "paraguibench.runtime.webmall_binding.load_osworld_image_manifest_with_sha256",
        lambda _path: (image_manifest, image_manifest_sha256),
    )
    monkeypatch.setattr(
        "paraguibench.runtime.pipeline_implicit_binding.load_osworld_image_manifest_with_sha256",
        lambda _path: (image_manifest, image_manifest_sha256),
    )
    monkeypatch.setattr(
        "paraguibench.runtime.webmall_binding.load_webmall_environment_manifest_with_sha256",
        lambda _path: (webmall_manifest, webmall_manifest_sha256),
    )


def test_live_commands_accept_secret_references_not_secret_values() -> None:
    """验证 doctor/run 参数面只允许环境变量名，不接受 key 或 URL 值。

    输入参数：
        无；遍历 argparse action tree。
    输出返回值：
        无；不存在 ``--api-key`` 或 ``--base-url``，引用选项存在。
    """

    parser = build_parser()
    option_strings: set[str] = set()
    pending = [parser]
    while pending:
        current = pending.pop()
        for action in current._actions:
            option_strings.update(action.option_strings)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                pending.extend(choices.values())

    assert "--api-key" not in option_strings
    assert "--base-url" not in option_strings
    assert "--planner-api-key" not in option_strings
    assert "--planner-base-url" not in option_strings
    assert "--api-key-env" in option_strings
    assert "--base-url-env" in option_strings
    assert "--planner-api-key-env" in option_strings
    assert "--planner-base-url-env" in option_strings
    assert "--gold-cache-root" in option_strings


@pytest.mark.parametrize(
    "extra_arguments",
    (
        ("--api-key", _PARSE_ERROR_SENTINEL),
        (f"--api-key={_PARSE_ERROR_SENTINEL}",),
        ("--server-port", _PARSE_ERROR_SENTINEL),
    ),
)
def test_cli_parse_error_never_echoes_unknown_secret_value(
    capsys: pytest.CaptureFixture[str],
    extra_arguments: tuple[str, ...],
) -> None:
    """确认 argparse 未知参数不会回显疑似 API key。

    输入参数：capsys 捕获公开 CLI 的标准输出与标准错误；
        extra_arguments 覆盖未知选项的分隔/等号形式和非法 typed 值。
    输出返回值：完整 doctor 参数以 2 退出，但任何输出
        都不含伪造的疑似凭据值。
    """

    with pytest.raises(SystemExit) as captured_exit:
        main(
            [
                "doctor",
                "--repo-root",
                ".",
                "--task-id",
                "task-001",
                "--asset-cache-root",
                "/tmp/assets",
                "--qcow2-path",
                "/tmp/osworld.qcow2",
                "--server-port",
                "5000",
                "--vnc-port",
                "5900",
                "--chromium-port",
                "9222",
                *extra_arguments,
            ]
        )

    captured = capsys.readouterr()
    assert captured_exit.value.code == 2
    assert _PARSE_ERROR_SENTINEL not in captured.out
    assert _PARSE_ERROR_SENTINEL not in captured.err
    assert captured.err.endswith("error=ArgumentParseError\n")


def test_gold_verify_for_inline_task_is_read_only_and_reports_no_manifest(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证无外部 gold 的任务不会创建缓存或伪造空 manifest。

    输入参数：
        tmp_path：pytest 提供的不存在 evaluator cache 路径。
        capsys：标准输出与错误输出捕获 fixture。
    输出返回值：
        无；命令输出稳定 NONE/0/PASS，缓存路径保持不存在。
    """

    repo_root = Path(__file__).resolve().parents[2]
    cache_root = tmp_path / "must-not-exist"

    exit_code = main(
        [
            "gold",
            "verify",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-FileOperate-BatchOperation-001",
            "--gold-cache-root",
            str(cache_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.splitlines() == [
        "gold_manifest=NONE",
        "entries=0",
        "status=PASS",
    ]
    assert captured.err == ""
    assert not cache_root.exists()


def test_ppt003_embedded_gold_identity_does_not_require_external_cache() -> None:
    """验证 PPT-003 strict gold 只绑定纯 evaluator，不伪造私有缓存依赖。

    输入参数：
        无；把真实 canonical trusted task 交给 CLI gold context loader。
    输出返回值：
        无；loader 必须先验证 task-specific strict manifest 与固定 SHA 映射，
        再返回零 external-gold 模式；Agent final text 不参与该路径。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task = json.loads(
        (
            repo_root
            / "benchmark/tasks/Operation-FileOperate-BatchOperationPPT-003.json"
        ).read_text(encoding="utf-8")
    )
    prepared_task = PreparedTask(
        trusted_task=task,
        agent_task={"task_id": task["task_id"]},
        audit_metadata={"task_id": task["task_id"]},
    )

    resolved = _load_task_gold_context(
        repo_root=repo_root,
        prepared_task=prepared_task,
    )

    assert resolved.mode is TaskGoldMode.NONE
    assert resolved.manifest is None
    assert resolved.logical_keys == ()


def test_gold_verify_fails_safely_when_required_cache_is_missing(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证 015 缺失预置 gold 时只公开稳定异常类型。

    输入参数：
        tmp_path：pytest 提供的未创建 cache 根。
        capsys：标准输出与错误输出捕获 fixture。
    输出返回值：
        无；命令失败且不回显 manifest path、HF locator、摘要或正文。
    """

    repo_root = Path(__file__).resolve().parents[2]
    cache_root = tmp_path / "private-gold-cache"

    exit_code = main(
        [
            "gold",
            "verify",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-FileOperate-CombinationDocs-015",
            "--gold-cache-root",
            str(cache_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "error=GoldUnavailableError\n"
    assert "huggingface" not in captured.err.lower()
    assert "references.bib" not in captured.err
    assert str(cache_root) not in captured.err
    assert not cache_root.exists()


def test_gold_fetch_loads_task_bound_manifest_and_prints_only_public_status(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 CLI 只把严格绑定的 manifest 交给显式 fetcher。

    输入参数：
        tmp_path：pytest 提供的 evaluator cache 根。
        capsys：标准输出与错误输出捕获 fixture。
        monkeypatch：用无网络 fake 替换 production fetcher。
    输出返回值：
        无；fake 收到固定 015 manifest 与绝对 cache，终端不含 URL、路径、
        SHA 或 gold logical key。
    """

    calls: list[tuple[object, Path]] = []

    def fake_fetch(manifest: object, cache_root: Path) -> GoldAvailability:
        """记录 CLI 已验证的 manifest/cache 并模拟预置完成。

        输入参数：
            manifest：应为 015 的严格 gold manifest dataclass。
            cache_root：CLI 解析后的 evaluator-only 绝对 cache。
        输出返回值：
            AVAILABLE 与单条目计数的脱敏结果。
        """

        calls.append((manifest, cache_root))
        return GoldAvailability(
            status=GoldAvailabilityStatus.AVAILABLE,
            requested_count=1,
        )

    monkeypatch.setattr(
        "paraguibench.cli.main.fetch_gold_assets",
        fake_fetch,
    )
    repo_root = Path(__file__).resolve().parents[2]
    cache_root = tmp_path / "gold-cache"

    exit_code = main(
        [
            "gold",
            "fetch",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-FileOperate-CombinationDocs-015",
            "--gold-cache-root",
            str(cache_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(calls) == 1
    manifest, received_root = calls[0]
    assert manifest.manifest_id == ("Operation-FileOperate-CombinationDocs-015-gold-v1")
    assert received_root == cache_root.absolute()
    assert captured.out.splitlines() == [
        "gold_manifest=Operation-FileOperate-CombinationDocs-015-gold-v1",
        "entries=1",
        "status=PASS",
    ]
    assert captured.err == ""
    serialized = captured.out + captured.err
    assert "huggingface" not in serialized.lower()
    assert "references.bib" not in serialized
    assert "056bde" not in serialized
    assert BIBTEX_GOLD_KEY not in serialized


@pytest.mark.parametrize(
    "unsafe_reference",
    [
        "../gold.json",
        "/private/tmp/gold.json",
        "benchmark\\gold\\manifest.json",
        "benchmark/gold/manifest.json\x00suffix",
    ],
)
def test_task_gold_loader_rejects_unsafe_manifest_references(
    tmp_path: Path,
    unsafe_reference: str,
) -> None:
    """验证 task 提供的 gold 引用不能越界、改用绝对路径或反斜杠。

    输入参数：
        tmp_path：pytest 提供的空仓库根。
        unsafe_reference：参数化的不安全 canonical task 字段。
    输出返回值：
        无；在 manifest/cache 读取前抛安全路径错误。
    """

    with pytest.raises(ValueError):
        _load_task_gold_context(
            repo_root=tmp_path,
            prepared_task=_synthetic_combinationdocs_prepared_task(unsafe_reference),
        )


def test_task_gold_loader_rejects_symlinked_manifest(tmp_path: Path) -> None:
    """验证 repo 内 gold manifest 最终分量也不能是符号链接。

    输入参数：
        tmp_path：pytest 提供的合成 repo 根。
    输出返回值：
        无；即使链接目标是真实合法 manifest，loader 仍在解析前失败。
    """

    repo_root = Path(__file__).resolve().parents[2]
    target = (
        repo_root
        / "benchmark"
        / "gold"
        / "manifests"
        / "Operation-FileOperate-CombinationDocs-015.json"
    )
    link = tmp_path / "linked-gold.json"
    link.symlink_to(target)

    with pytest.raises(ValueError):
        _load_task_gold_context(
            repo_root=tmp_path,
            prepared_task=_synthetic_combinationdocs_prepared_task("linked-gold.json"),
        )


def test_run_exposes_qwen_only_as_experimental_gui_only_worker() -> None:
    """验证 CLI 可选择 Qwen，但尚不宣称 ParaGUI live runtime。

    输入参数：
        无；仅解析参数，不访问资产、凭据、Docker 或模型服务。
    输出返回值：
        无；agent-system 固定 gui-only，worker 可选 qwen。
    """

    arguments = build_parser().parse_args(
        [
            "run",
            "--repo-root",
            ".",
            "--task-id",
            "task-001",
            "--asset-cache-root",
            "/tmp/assets",
            "--qcow2-path",
            "/tmp/osworld.qcow2",
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            "/tmp/runs",
            "--agent-system",
            "gui-only",
            "--worker",
            "qwen",
            "--model",
            "qwen3.7-flash-2026-07-15",
        ]
    )

    assert arguments.agent_system == "gui-only"
    assert arguments.worker == "qwen"
    assert arguments.enable_thinking is False
    assert arguments.qwen_visual_history == 2
    assert arguments.max_history_image_pixels == 1_048_576


def test_run_exposes_experimental_kimi_qwen_single_vm_mode() -> None:
    """验证 CLI 显式区分单 VM 串行 ParaGUI 与 GUI-only。

    输入参数：
        无；只解析 Kimi planner 与 Qwen worker 的非敏感引用。
    输出返回值：
        无；新模式默认固定 kimi-k2.6、4 个子任务与单环境串行。
    """

    arguments = build_parser().parse_args(
        [
            "run",
            "--repo-root",
            ".",
            "--task-id",
            "task-001",
            "--asset-cache-root",
            "/tmp/assets",
            "--qcow2-path",
            "/tmp/osworld.qcow2",
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            "/tmp/runs",
            "--agent-system",
            "paragui-single-vm",
            "--worker",
            "qwen",
            "--model",
            "qwen3.7-flash",
        ]
    )

    assert arguments.agent_system == "paragui-single-vm"
    assert arguments.planner_model == "kimi-k2.6"
    assert arguments.planner_api_key_env == "PARAGUIBENCH_MODEL_API_KEY"
    assert arguments.planner_base_url_env == "PARAGUIBENCH_MODEL_BASE_URL"
    assert arguments.planner_max_subtasks == 4
    assert arguments.planner_max_output_tokens == 2048


def _parsed_run_arguments(
    worker: str,
    *extra_arguments: str,
) -> argparse.Namespace:
    """构造不访问磁盘、网络或凭据的 run 参数。

    输入参数：
        worker：待测试的 ``seed18`` 或 ``qwen`` worker 名称。
        extra_arguments：追加在默认参数后的 CLI 覆盖项。
    输出返回值：
        可直接交给 CLI Agent 装配函数的 argparse namespace。
    """

    return build_parser().parse_args(
        [
            "run",
            "--repo-root",
            ".",
            "--task-id",
            "task-001",
            "--asset-cache-root",
            "/tmp/assets",
            "--qcow2-path",
            "/tmp/osworld.qcow2",
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            "/tmp/runs",
            "--agent-system",
            "gui-only",
            "--worker",
            worker,
            "--model",
            "test-model",
            *extra_arguments,
        ]
    )


def test_cli_builder_preserves_seed18_default_and_records_public_config() -> None:
    """验证新增 Qwen 不会改变 Seed18 已验证的 512 token 默认值。

    输入参数：
        无；使用完整 CLI parser 产生 Seed18 默认参数。
    输出返回值：
        无；装配结果的类型、标识与可公开配置均保持稳定。
    """

    agent, agent_id, public_config = _build_gui_only_agent(
        _parsed_run_arguments("seed18"),
        base_url="https://gateway.example/v1",
    )

    assert isinstance(agent, Seed18AgentSystem)
    assert agent_id == "gui_only.seed18"
    assert public_config == {
        "worker": "seed18",
        "max_steps": 18,
        "post_action_delay": 1.0,
        "max_output_tokens": 512,
    }


def test_cli_builder_assembles_qwen_and_records_reproducible_public_config() -> None:
    """验证 Qwen GUI-only 装配及 RunStore 所需非敏感配置。

    输入参数：
        无；使用完整 CLI parser 产生 Qwen 默认参数。
    输出返回值：
        无；返回 Qwen Agent System、稳定标识和不含 endpoint/key
        值的可复现配置。
    """

    agent, agent_id, public_config = _build_gui_only_agent(
        _parsed_run_arguments("qwen"),
        base_url="https://gateway.example/v1",
    )

    assert isinstance(agent, QwenGUIOnlyAgentSystem)
    assert agent_id == "gui_only.qwen"
    assert public_config == {
        "worker": "qwen",
        "max_steps": 18,
        "post_action_delay": 1.0,
        "max_output_tokens": 1024,
        "max_image_pixels": 4_194_304,
        "max_history_image_pixels": 1_048_576,
        "screenshot_history_limit": 2,
        "enable_thinking": False,
        "tool_protocol": "native",
    }
    assert "api_key" not in public_config
    assert "base_url" not in public_config


def test_cli_builder_assembles_kimi_qwen_without_persisting_endpoint_values() -> None:
    """验证单 VM ParaGUI 装配固定串行并仅记录非敏感配置。

    输入参数：
        无；由完整 parser 产生 Kimi/Qwen 参数。
    输出返回值：
        无；返回 ParaGUIAgentSystem、稳定标识和不含 endpoint/key
        值的可公开配置。
    """

    arguments = _parsed_run_arguments(
        "qwen",
        "--agent-system",
        "paragui-single-vm",
    )
    agent, agent_id, public_config = _build_agent(
        arguments,
        worker_base_url="https://worker.example/v1",
        planner_base_url="https://planner.example/v1",
    )

    assert isinstance(agent, ParaGUIAgentSystem)
    assert agent_id == "paragui.kimi_qwen.sequential_single_vm"
    assert public_config["execution_mode"] == "sequential_single_vm"
    assert public_config["parallel_execution"] is False
    assert public_config["environment_count"] == 1
    assert public_config["scheduler_max_workers"] == 1
    assert public_config["planner"] == {
        "model": "kimi-k2.6",
        "max_subtasks": 4,
        "max_output_tokens": 2048,
        "tool_protocol": "native_function_call",
    }
    assert public_config["worker"]["model"] == "test-model"
    serialized = json.dumps(public_config)
    assert "worker.example" not in serialized
    assert "planner.example" not in serialized
    assert "api_key" not in serialized


def test_cli_builder_rejects_seed18_for_paragui_single_vm() -> None:
    """验证未实现的 Kimi+Seed18 组合在环境启动前 fail-closed。

    输入参数：
        无；使用完整 parser 选择单 VM ParaGUI 与 Seed18。
    输出返回值：
        无；装配函数抛出不含 endpoint 值的 ``ValueError``。
    """

    arguments = _parsed_run_arguments(
        "seed18",
        "--agent-system",
        "paragui-single-vm",
    )

    with pytest.raises(ValueError, match="只支持 qwen"):
        _build_agent(
            arguments,
            worker_base_url="https://worker.example/v1",
            planner_base_url="https://planner.example/v1",
        )


def test_cli_qwen_history_overrides_are_validated_and_recorded() -> None:
    """验证视觉历史数量与像素预算可复现且在 CLI 阶段限界。

    输入参数：
        无；使用 4 张历史图和 524288 像素覆盖值。
    输出返回值：
        无；合法覆盖进入公开配置，5 张在 parser 阶段被拒绝。
    """

    _, _, public_config = _build_gui_only_agent(
        _parsed_run_arguments(
            "qwen",
            "--qwen-visual-history",
            "4",
            "--max-history-image-pixels",
            "524288",
        ),
        base_url="https://gateway.example/v1",
    )

    assert public_config["screenshot_history_limit"] == 4
    assert public_config["max_history_image_pixels"] == 524_288
    with pytest.raises(SystemExit):
        _parsed_run_arguments(
            "qwen",
            "--qwen-visual-history",
            "5",
        )


def test_cli_wires_active_tab_probe_to_the_same_container_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 active-tab evidence source 使用同一个 run 的 CDP/API 端口。

    输入参数：
        monkeypatch：将真实 Playwright/AT 探针替换为记录参数的 fake。
    输出返回值：
        无；source capture 必须传递 loopback、9222 与 5000，且返回
        探针产生的同一不可变 observation。
    """

    calls: list[dict[str, object]] = []
    expected = GoogleShoppingActiveTabObservation(
        url="https://www.google.com/search?tbm=shop&q=drip+coffee+maker",
        locale="en-US",
        filter_surface_observed=True,
        selection_enumeration_complete=True,
        selection_evidence="semantic_google_filter_state_list",
        selected_filter_labels=("Black", "$25 - $60", "On sale"),
    )

    def fake_capture(**kwargs: object) -> GoogleShoppingActiveTabObservation:
        """记录 CLI 闭包传入的宿主与端口。

        输入参数：
            kwargs：生产 probe 的具名参数。
        输出返回值：
            固定 Google Shopping observation。
        """

        calls.append(dict(kwargs))
        return expected

    monkeypatch.setattr(
        "paraguibench.cli.main.capture_google_shopping_active_tab_observation",
        fake_capture,
    )
    arguments = _parsed_run_arguments("qwen")
    source = _build_osworld_state_evidence_source(arguments)

    observations = source.capture(
        GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
        object(),
    )

    assert observations == (expected,)
    assert calls == [
        {
            "host": "127.0.0.1",
            "chromium_port": 9222,
            "server_port": 5000,
        }
    ]


def test_cli_builds_controlled_bookmark_evidence_source() -> None:
    """验证 CLI 使用无凭据、固定协议的生产 Bookmark source。

    输入参数：
        无。
    输出返回值：
        无；builder 返回生产 source，构造阶段不访问 VM、文件或网络。
    """

    source = _build_osworld_bookmark_evidence_source()

    assert isinstance(source, OSWorldChromeBookmarkEvidenceSource)


def test_cli_injects_offline_gold_resolver_into_artifact_source(
    tmp_path: Path,
) -> None:
    """验证 run builder 显式注入 task-bound 离线 gold resolver。

    输入参数：
        tmp_path：pytest 提供的尚未填充 evaluator-only cache 根。
    输出返回值：
        无；断言 builder 返回 production source 并保留同一 resolver；构造
        阶段不读取 cache、guest、artifact、网络或任何凭据。
    """

    repo_root = Path(__file__).resolve().parents[2]
    manifest = load_gold_asset_manifest(
        repo_root
        / "benchmark"
        / "gold"
        / "manifests"
        / "Operation-FileOperate-CombinationDocs-015.json"
    )
    resolver = GoldAssetResolver(
        manifest=manifest,
        cache_root=tmp_path / "unread-cache",
    )
    source = _build_osworld_artifact_evidence_source(
        gold_resolver=resolver,
    )

    assert isinstance(source, OSWorldArtifactEvidenceSource)
    assert source._gold_resolver is resolver
    assert not (tmp_path / "unread-cache").exists()


def test_cli_builds_production_artifact_finalizer_without_guest_io() -> None:
    """验证正式 run 装配显式使用安全 finalizer，而非临时替身。

    输入参数：
        无。
    输出返回值：
        无；builder 返回生产类型，构造本身不访问 guest 或 controller。
    """

    finalizer = _build_osworld_artifact_finalizer()

    assert type(finalizer) is OSWorldArtifactFinalizer


def test_cli_builds_operation_artifact_source_without_guest_io() -> None:
    """验证 CLI 显式装配 Operation 完整文件树 source。

    输入参数：
        无。
    输出返回值：
        无；builder 返回 production source，构造阶段不读取
        guest、artifact、gold、网络或任何凭据。
    """

    source = _build_osworld_operation_artifact_source()

    assert isinstance(source, OSWorldOperationArtifactSource)


def test_cli_builds_pipeline_implicit_source_without_guest_io() -> None:
    """验证 CLI 显式装配四任务完整 bundle evidence source。

    输入参数：无。
    输出返回值：
        无；builder 只构造固定任务/协议 source，不读取 manifest、guest、
        artifact、网络、凭据或 Agent 文本。
    """

    source = _build_pipeline_implicit_evidence_source()

    assert isinstance(source, PipelineImplicitArtifactEvidenceSource)


def test_searchwrite_gold_context_uses_evaluator_only_machine_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 SearchWrite 专属 gold 不会误入通用 OSWorld loader。

    输入参数：
        monkeypatch：将通用 gold loader 替换为不可达哨兵。
    输出返回值：
        无；专属 preflight 校验 input/gold/typed 机器身份后
        返回 evaluator-only ``NONE`` 绑定，gold 始终不进入通用 loader。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task = json.loads(
        (
            repo_root / "benchmark/tasks/Operation-FileOperate-SearchAndWrite-008.json"
        ).read_text(encoding="utf-8")
    )
    prepared_task = PreparedTask(
        trusted_task=task,
        agent_task={"task_id": task["task_id"]},
        audit_metadata={"task_id": task["task_id"]},
    )

    def unreachable_generic_loader(_path: Path) -> object:
        """拒绝 SearchWrite 专属 gold 误入通用 loader。

        输入参数：_path 为不应被使用的候选路径。
        输出返回值：无；调用即使测试失败。
        """

        raise AssertionError("SearchWrite gold 不得进入通用 loader")

    monkeypatch.setattr(
        "paraguibench.cli.main.load_gold_asset_manifest",
        unreachable_generic_loader,
    )

    resolved = _load_task_gold_context(
        repo_root=repo_root,
        prepared_task=prepared_task,
    )

    assert resolved.mode is TaskGoldMode.NONE
    assert resolved.manifest is None


@pytest.mark.parametrize(
    "task_id",
    (
        "Operation-FileOperate-BatchOperationExcel-008",
        "Operation-FileOperate-CombinationDocs-002",
    ),
)
def test_pipeline_implicit_ready_tasks_do_not_require_component_receipt(
    task_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证本地闭合任务的普通 preflight 不检查 component receipt。

    输入参数：
        task_id：Excel-008 或 CombinationDocs-002 canonical ID。
        monkeypatch：截获版本向量与 evaluator 构造。
    输出返回值：
        无；静态 production 能力通过后直接进入版本与 evaluator 装配，
        不因空 allowlist 或过期 receipt 失败关闭。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task = json.loads(
        (repo_root / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    prepared_task = PreparedTask(
        trusted_task=task,
        agent_task={"task_id": task_id},
        audit_metadata={"task_id": task_id},
    )
    vector = synthetic_run_version_vector()
    evaluator = object()
    calls: list[str] = []

    def build_vector(**_kwargs: object) -> object:
        """记录版本向量构造。"""

        calls.append("version")
        return vector

    def build_evaluator(_task: object, *, evaluation_protocol: str) -> object:
        """记录 evaluator 构造。"""

        del evaluation_protocol
        calls.append("evaluator")
        return evaluator

    monkeypatch.setattr(
        "paraguibench.cli.main.build_run_version_vector",
        build_vector,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.build_task_evaluator",
        build_evaluator,
    )
    observed_vector, observed_evaluator, observed_capability = (
        _preflight_osworld_runtime(
            repo_root=repo_root,
            prepared_task=prepared_task,
        )
    )

    assert observed_vector is vector
    assert observed_evaluator is evaluator
    assert isinstance(observed_capability, PipelineImplicitRuntimeCapability)
    assert calls == ["version", "evaluator"]


def test_ppt003_preflight_reaches_version_and_evaluator_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 PPT-003 组件闭合后不再被公开 live marker 永久阻断。

    输入参数：
        monkeypatch：在真实 manifest preflight 之后截获版本与 evaluator 构造。
    输出返回值：
        无；preflight 必须到达两个正式下游 seam，并返回其同一对象；测试不
        启动 Docker、guest、模型或 RunStore。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task = json.loads(
        (
            repo_root
            / "benchmark/tasks/Operation-FileOperate-BatchOperationPPT-003.json"
        ).read_text(encoding="utf-8")
    )
    prepared_task = PreparedTask(
        trusted_task=task,
        agent_task={"task_id": task["task_id"]},
        audit_metadata={"task_id": task["task_id"]},
    )
    vector = synthetic_run_version_vector()
    evaluator = object()
    calls: list[str] = []
    image_manifest = load_osworld_image_manifest(
        repo_root / "environments/osworld/image-manifest.json"
    )

    def build_vector(**kwargs: object) -> object:
        """记录版本向量构造并返回合成不可变向量。

        输入参数：忽略已由被测函数验证的仓库参数。
        输出返回值：合成 ``RunVersionVector``。
        """

        assert kwargs["environment_manifest_sha256"] == (image_manifest.manifest_sha256)
        assert kwargs["environment_protocol_ids"] == image_manifest.protocol_ids
        calls.append("version")
        return vector

    def build_evaluator(
        _task: object,
        *,
        evaluation_protocol: str,
    ) -> object:
        """记录 evaluator registry 已收到版本向量固定协议。

        输入参数：
            _task：当前 trusted task，本测试不展开。
            evaluation_protocol：必须来自上一步版本向量。
        输出返回值：合成 evaluator 哨兵。
        """

        assert evaluation_protocol == vector.evaluation_protocol
        calls.append("evaluator")
        return evaluator

    monkeypatch.setattr(
        "paraguibench.cli.main.build_run_version_vector",
        build_vector,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.build_task_evaluator",
        build_evaluator,
    )
    (
        observed_vector,
        observed_evaluator,
        observed_capability,
    ) = _preflight_osworld_runtime(
        repo_root=repo_root,
        prepared_task=prepared_task,
        image_manifest=image_manifest,
    )

    assert observed_vector is vector
    assert observed_evaluator is evaluator
    assert isinstance(observed_capability, PipelineImplicitRuntimeCapability)
    assert observed_capability.task_id == task["task_id"]
    assert observed_capability.environment_manifest_sha256 == (
        image_manifest.manifest_sha256
    )
    assert observed_capability.container_image == image_manifest.container_image
    assert calls == ["version", "evaluator"]


def test_nonpipeline_osworld_preflight_rejects_stale_image_snapshot_before_downstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证普通 OSWorld 任务不能用旧 DTO 绕过正式清单漂移。

    输入参数：tmp_path 提供仅含正式 image manifest 的隔离仓库；
        monkeypatch 将 pipeline、版本向量与评价器边界设为不可达哨兵。
    输出返回值：无；首次严格加载后将正式字节改为另一份
        合法清单，传入旧快照必须在任何下游调用前失败关闭。
    """

    repo_root = tmp_path / "repo"
    manifest_path = repo_root / "environments/osworld/image-manifest.json"
    manifest_path.parent.mkdir(parents=True)
    formal_path = (
        Path(__file__).resolve().parents[2] / "environments/osworld/image-manifest.json"
    )
    manifest_path.write_bytes(formal_path.read_bytes())
    stale_manifest = load_osworld_image_manifest(manifest_path)
    changed = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed["environment_id"] = "osworld-ubuntu-x86_64-drifted"
    manifest_path.write_text(
        json.dumps(changed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prepared_task = PreparedTask(
        trusted_task={"task_id": "InformationRetrieval-FileSearch-Readonly-001"},
        agent_task={"task_id": "InformationRetrieval-FileSearch-Readonly-001"},
        audit_metadata={"task_id": "InformationRetrieval-FileSearch-Readonly-001"},
    )
    downstream_calls: list[str] = []

    def unreachable(*_args: object, **_kwargs: object) -> object:
        """记录任何越过正式清单快照门禁的下游调用。

        输入参数：任意位置参数与具名参数，均不应被生产调用。
        输出返回值：不返回；记录后立即使测试失败。
        """

        downstream_calls.append("unreachable")
        raise AssertionError("旧 image snapshot 不得越过正式字节门禁")

    monkeypatch.setattr(
        "paraguibench.cli.main.preflight_pipeline_implicit_runtime",
        unreachable,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.build_run_version_vector",
        unreachable,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.build_task_evaluator",
        unreachable,
    )

    with pytest.raises(ValueError, match="OSWorld image manifest 快照已漂移"):
        _preflight_osworld_runtime(
            repo_root=repo_root,
            prepared_task=prepared_task,
            image_manifest=stale_manifest,
        )

    assert downstream_calls == []


@pytest.mark.parametrize(
    (
        "task_id",
        "expected_input_count",
        "evaluation_outcome",
        "score",
        "expected_exit_code",
        "expects_attempt",
    ),
    (
        (
            "Operation-FileOperate-BatchOperationPPT-003",
            20,
            EvaluationOutcome.PASSED,
            1.0,
            0,
            True,
        ),
        (
            "Operation-FileOperate-BatchOperationPPT-003",
            20,
            EvaluationOutcome.PASSED,
            0.8,
            0,
            True,
        ),
        (
            "Operation-FileOperate-BatchOperationPPT-003",
            20,
            EvaluationOutcome.FAILED,
            1.0,
            1,
            True,
        ),
        (
            "Operation-FileOperate-SearchAndWrite-008",
            2,
            EvaluationOutcome.PASSED,
            1.0,
            0,
            True,
        ),
        (
            "Operation-FileOperate-SearchAndWrite-008",
            2,
            EvaluationOutcome.FAILED,
            1.0,
            1,
            True,
        ),
    ),
)
def test_pipeline_synthetic_live_run_reaches_attempt_without_secret_leak(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
    task_id: str,
    expected_input_count: int,
    evaluation_outcome: EvaluationOutcome,
    score: float,
    expected_exit_code: int,
    expects_attempt: bool,
) -> None:
    """验证 PPT/Search 有效 receipt 均可装配正式 Attempt。

    输入参数：
        tmp_path：pytest 提供的 qcow2 与 RunStore 隔离根。
        capsys：捕获 CLI 的公开诊断输出。
        monkeypatch：只替换主机 doctor、付费 Agent 与 Attempt 执行边界；
            release、canonical、manifest、版本向量和 evaluator registry 走真实代码。
        task_id：PPT-003 或 SearchAndWrite-008 正式 canonical ID。
        expected_input_count：对应固定 input manifest 的精确条目数。
        evaluation_outcome：合成 evaluator 终态，覆盖 PASSED 与
            九格满分但完整性失败的 FAILED。
        score：与评价终态一致的有限得分，同时覆盖阈值型
            evaluator 可能返回的 PASSED+0.8。
        expected_exit_code：与评价终态对应的 CLI 退出码。
        expects_attempt：是否应在正式组件门禁后到达 Attempt seam。
    输出返回值：
        无；两项通过显式合成 current component 授权后装配正式
        evidence/evaluator 并到达 Attempt seam；均不持久化 raw key、
        endpoint、gold 或 Agent final text。
    """

    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    qcow2_path = tmp_path / "browser.qcow2"
    qcow2_path.write_bytes(b"synthetic-browser-image")
    runs_root = tmp_path / "runs"
    model_key = "PRIVATE-PPT-MODEL-KEY"
    model_endpoint = "https://private-ppt-model.example.invalid/v1"
    monkeypatch.setenv("PARAGUIBENCH_MODEL_API_KEY", model_key)
    monkeypatch.setenv("PARAGUIBENCH_MODEL_BASE_URL", model_endpoint)
    if expects_attempt:
        monkeypatch.setattr(
            "paraguibench.runtime.pipeline_implicit_binding.load_trusted_pipeline_implicit_component_receipts",
            lambda _root, **_kwargs: frozenset({task_id}),
        )

    def fake_doctor(config: object) -> DoctorReport:
        """确认 CLI 已绑定20项 input且没有伪造 external gold cache。

        输入参数：
            config：真实 canonical/preflight 形成的 OSWorld doctor 配置。
        输出返回值：
            一项通过的合成主机报告；不访问 Docker、网络或资产缓存。
        """

        assert config.qcow2_path == qcow2_path
        assert config.task_assets.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
        assert config.task_assets.manifest is not None
        assert len(config.task_assets.manifest.files) == expected_input_count
        assert config.task_gold.mode is TaskGoldMode.NONE
        return DoctorReport(checks=(DoctorCheck("osworld_browser", True),))

    def fake_agent(
        arguments: argparse.Namespace,
        *,
        worker_base_url: str,
        planner_base_url: str | None,
    ) -> tuple[object, str, dict[str, str]]:
        """验证 endpoint 只在内存边界出现并返回无 I/O Agent。

        输入参数：
            arguments：CLI run 参数。
            worker_base_url：由环境变量引用读取的私有 endpoint。
            planner_base_url：GUI-only 路径必须为 ``None``。
        输出返回值：
            合成 Agent、系统 ID 与无 secret 的公开配置。
        """

        assert arguments.model == "synthetic-model"
        assert worker_base_url == model_endpoint
        assert planner_base_url is None
        return object(), "gui_only.synthetic", {"worker": "synthetic"}

    def forbidden_docker_start(_session: object) -> None:
        """保证本测试到达 Attempt seam 前后均未启动 Docker。

        输入参数：_session 为误调用时的 Docker session。
        输出返回值：不返回；调用即使测试失败。
        """

        raise AssertionError("synthetic CLI tracer must not start Docker")

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        fake_doctor,
    )
    monkeypatch.setattr("paraguibench.cli.main._build_agent", fake_agent)
    monkeypatch.setattr(
        "paraguibench.cli.main.OSWorldDockerSession.start",
        forbidden_docker_start,
    )
    reached_attempt = False

    class _FakeAttemptRunner:
        """检查正式 runtime 组件，不启动环境或执行 Agent。"""

        def __init__(self, store: RunStore) -> None:
            """接收已创建的版本化 RunStore。

            输入参数：store 为 CLI 当前运行的持久化入口。
            输出返回值：无。
            """

            assert isinstance(store, RunStore)

        def run(self, **kwargs: object) -> RuntimeAttemptResult:
            """验证 production environment/evaluator 后返回合成结果。

            输入参数：kwargs 为 CLI 交给 AttemptRunner 的完整依赖。
            输出返回值：执行成功、携带参数化 score，但评价终态
                由参数控制的合成结果。
            """

            nonlocal reached_attempt
            reached_attempt = True
            environment = kwargs["environment"]
            evaluator = kwargs["evaluator"]
            prepared = kwargs["prepared_task"]
            assert isinstance(environment, OSWorldTaskEnvironment)
            assert isinstance(
                environment._pipeline_implicit_evidence_source,
                PipelineImplicitArtifactEvidenceSource,
            )
            assert isinstance(
                environment._pipeline_implicit_runtime_capability,
                PipelineImplicitRuntimeCapability,
            )
            assert environment._pipeline_implicit_runtime_capability.task_id == task_id
            assert isinstance(evaluator, PipelineImplicitTaskEvaluator)
            assert "asset_manifest" not in prepared.agent_task
            assert "gold_manifest" not in prepared.agent_task
            return RuntimeAttemptResult(
                execution_outcome=ExecutionOutcome.SUCCEEDED,
                evaluation_outcome=evaluation_outcome,
                score=score,
            )

    monkeypatch.setattr(
        "paraguibench.cli.main.AttemptRunner",
        _FakeAttemptRunner,
    )

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--task-id",
            task_id,
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(qcow2_path),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            str(runs_root),
            "--model",
            "synthetic-model",
            "--run-id",
            "run-pipeline-native",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == expected_exit_code, (captured.out, captured.err)
    assert reached_attempt is expects_attempt
    if not expects_attempt:
        assert captured.out == ""
        assert captured.err == "error=PipelineImplicitRuntimeBlockedError\n"
        assert not runs_root.exists()
        return
    assert captured.err == ""
    assert f"evaluation={evaluation_outcome.value}" in captured.out
    persisted = b"\n".join(
        path.read_bytes() for path in runs_root.rglob("*") if path.is_file()
    )
    for forbidden in (
        model_key,
        model_endpoint,
        "PRIVATE PIPELINE FINAL SENTINEL",
        "920c257be076389b03fe784a05181d91d45f3f679b554d4717b5786880b8ccba",
    ):
        assert forbidden.encode() not in persisted


def test_combinationdocs_run_fails_gold_doctor_before_agent_or_runstore(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 015 缺失 gold 在 Agent、VM 和 RunStore 创建前阻断。

    输入参数：
        tmp_path：pytest 提供的未使用缓存、qcow2 与 runs 路径。
        capsys：标准输出与错误输出捕获 fixture。
        monkeypatch：把多项系统 doctor 替换为只检查接线顺序的 fake。
    输出返回值：
        无；runtime preflight 已选择原生 evaluator，doctor 收到 pinned gold，
        返回 FAIL 后不读取模型环境变量、不构造 Agent 或 RunStore。
    """

    runs_root = tmp_path / "runs-must-not-exist"
    gold_cache_root = tmp_path / "missing-gold-cache"
    calls = 0

    def fake_doctor(config: object) -> DoctorReport:
        """确认 production doctor config 收到 task-bound gold。

        输入参数：
            config：CLI 在 evaluator preflight 后构造的 OSWorld 配置。
        输出返回值：
            只含失败 gold_cache 的脱敏报告。
        """

        nonlocal calls
        calls += 1
        assert config.task_gold.mode is TaskGoldMode.PINNED_DOWNLOAD_MANIFEST
        assert config.gold_cache_root == gold_cache_root.absolute()
        assert not config.gold_cache_root.exists()
        return DoctorReport(checks=(DoctorCheck("gold_cache", False),))

    def forbidden_agent(*_: object, **__: object) -> object:
        """标记 doctor 失败后不应发生的 Agent 装配。

        输入参数：
            _/__：未读取的位置和关键字参数。
        输出返回值：
            永不返回；调用即使测试失败。
        """

        raise AssertionError("Agent must follow a passing gold doctor")

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        fake_doctor,
    )
    monkeypatch.setattr("paraguibench.cli.main._build_agent", forbidden_agent)
    monkeypatch.delenv("PARAGUIBENCH_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("PARAGUIBENCH_MODEL_BASE_URL", raising=False)
    repo_root = Path(__file__).resolve().parents[2]

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-FileOperate-CombinationDocs-015",
            "--asset-cache-root",
            str(tmp_path / "unused-input-cache"),
            "--gold-cache-root",
            str(gold_cache_root),
            "--qcow2-path",
            str(tmp_path / "missing.qcow2"),
            "--server-port",
            "5101",
            "--vnc-port",
            "8101",
            "--chromium-port",
            "9222",
            "--runs-root",
            str(runs_root),
            "--model",
            "must-not-be-used",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == 1
    assert captured.out.splitlines() == ["FAIL gold_cache", "doctor=FAIL"]
    assert captured.err == ""
    assert not runs_root.exists()
    assert not gold_cache_root.exists()


def test_assets_verify_accepts_task_without_external_files(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证零资产任务可通过公开 CLI 预检且不创建缓存目录。

    输入参数：
        tmp_path：pytest 提供的仓库外资产缓存占位目录。
        capsys：pytest 标准输出与错误输出捕获 fixture。
    输出返回值：
        无；真实 release task 返回 ``PASS`` 和零文件，且不访问网络或创建
        无意义的空资产目录。
    """

    repo_root = Path(__file__).resolve().parents[2]
    cache_root = tmp_path / "unused-assets"

    exit_code = main(
        [
            "assets",
            "verify",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "InformationRetrieval-WebSearch-ConditionalSearch-001",
            "--asset-cache-root",
            str(cache_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.splitlines() == [
        "asset_set=NONE",
        "files=0",
        "status=PASS",
    ]
    assert captured.err == ""
    assert not cache_root.exists()


def test_assets_verify_reports_missing_readonly_cache_safely(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证已迁 Readonly 固定资产在缓存缺失时安全失败。

    输入参数：
        tmp_path：pytest 提供的未使用资产缓存根目录。
        capsys：pytest 标准输出与错误输出捕获 fixture。
    输出返回值：
        无；CLI 公开固定 asset-set、文件数与 FAIL，不回显来源 URL、任务
        正文、文件名或缓存路径，也不把固定资产回退为零资产。
    """

    repo_root = Path(__file__).resolve().parents[2]
    cache_root = tmp_path / "must-not-exist"

    exit_code = main(
        [
            "assets",
            "verify",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "InformationRetrieval-FileSearch-ReadonlyPPT-002",
            "--asset-cache-root",
            str(cache_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out.splitlines() == [
        "asset_set=InformationRetrieval-FileSearch-ReadonlyPPT-002",
        "files=1",
        "status=FAIL",
    ]
    assert captured.err == ""
    assert "huggingface" not in captured.out.lower()
    assert not cache_root.exists()


@pytest.mark.parametrize("command", ["doctor", "run"])
def test_cart_live_commands_reach_doctor_but_keep_reference_probe_blocked(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    """验证 Cart 已迁移到原生协议，但 114 同会话实测门禁仍阻断 live。

    输入参数：
        tmp_path：pytest 提供的未使用 cache、qcow2 与 RunStore 父目录。
        capsys：pytest 标准输出/错误输出捕获 fixture。
        monkeypatch：合成 OSWorld browser 通过并提供四店 origin。
        command：分别覆盖 doctor 与 run 两个 live 入口。
    输出返回值：
        无；AddToCart 能完成 evaluator/runtime identity 预检，doctor
        明确显示 Cart reader 静态合同通过而 reference live validation
        失败；run 不创建模型、容器或 RunStore。
    """

    def passed_osworld_probe(_config: object) -> DoctorReport:
        """把本用例范围外的 OSWorld browser 门禁固定为通过。

        输入参数：
            _config：CLI 构造的 doctor 配置，本测试不展开。
        输出返回值：
            含一项通过检查的报告。
        """

        return DoctorReport(checks=(DoctorCheck("osworld_browser", True),))

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        passed_osworld_probe,
    )
    for index in range(1, 5):
        monkeypatch.setenv(
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN",
            f"https://cart-private-store-{index}.example.invalid",
        )
    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    runs_root = tmp_path / "runs-must-not-exist"
    arguments = [
        command,
        "--repo-root",
        str(repo_root),
        "--task-id",
        "Operation-OnlineShopping-AddToCart-001",
        "--asset-cache-root",
        str(tmp_path / "assets"),
        "--qcow2-path",
        str(tmp_path / "missing.qcow2"),
        "--server-port",
        "5000",
        "--vnc-port",
        "5900",
        "--chromium-port",
        "9222",
    ]
    if command == "run":
        arguments.extend(["--runs-root", str(runs_root), "--model", "synthetic-model"])

    exit_code = main(arguments)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err == ""
    assert captured.out.splitlines() == [
        "PASS osworld_browser",
        "PASS webmall_manifest",
        "PASS webmall_store_1_origin",
        "PASS webmall_store_2_origin",
        "PASS webmall_store_3_origin",
        "PASS webmall_store_4_origin",
        "PASS webmall_cart_reader_contract",
        "FAIL webmall_cart_reader_reference_live_validation",
        "doctor=FAIL",
    ]
    assert not runs_root.exists()


def test_webmall_checkout_doctor_lists_all_deployment_bindings(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证已迁移 Checkout 使用 WebMall manifest 并聚合列出缺口。

    输入参数：
        tmp_path：pytest 提供的未使用 cache/qcow2 路径。
        capsys：标准输出与错误输出捕获 fixture。
        monkeypatch：使底层 OSWorld browser 门禁合成通过，并清空
            所有 WebMall 部署变量。
    输出返回值：
        无；doctor 返回 2，同时列出 manifest PASS、四店 origin/
        reader、WP-CLI 与 lease 的全部 FAIL，而非误入 OSWorld-only
        ``RunVersioningError``。
    """

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        lambda _config: DoctorReport(checks=(DoctorCheck("osworld_browser", True),)),
    )
    for name in (
        *(f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN" for index in range(1, 5)),
        *(f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET" for index in range(1, 5)),
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL",
        "PARAGUIBENCH_WEBMALL_LEASE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    exit_code = main(
        [
            "doctor",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-OnlineShopping-Checkout-001",
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(tmp_path / "unused.qcow2"),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert lines[0] == "PASS osworld_browser"
    assert "PASS webmall_manifest" in lines
    assert "FAIL webmall_store_1_origin" in lines
    assert "FAIL webmall_store_4_reader_target" in lines
    assert "FAIL webmall_wp_cli" in lines
    assert "FAIL webmall_lease_endpoint" in lines
    assert "FAIL webmall_lease_credential" in lines
    assert lines[-1] == "doctor=FAIL"


def test_webmall_url_doctor_does_not_require_reader_or_lease_bindings(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 URL-multiset doctor 只聚合 browser、manifest 和四店 origin。

    输入参数：
        tmp_path：pytest 提供的未使用资产与 qcow2 路径。
        capsys：CLI 标准输出与错误输出捕获 fixture。
        monkeypatch：合成通过 OSWorld browser 门禁，只绑定四店
            origin，并显式删除 reader/lease 变量。
    输出返回值：
        无；doctor 通过，输出不含 WP-CLI、reader 或 lease 检查。
    """

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        lambda _config: DoctorReport(checks=(DoctorCheck("osworld_browser", True),)),
    )
    for index in range(1, 5):
        monkeypatch.setenv(
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN",
            f"https://url-store-{index}.example.invalid",
        )
        monkeypatch.delenv(
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET",
            raising=False,
        )
    monkeypatch.delenv(
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL",
        raising=False,
    )
    monkeypatch.delenv(
        "PARAGUIBENCH_WEBMALL_LEASE_TOKEN",
        raising=False,
    )
    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    exit_code = main(
        [
            "doctor",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-OnlineShopping-SingleProductSearch-001",
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(tmp_path / "unused.qcow2"),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert lines == [
        "PASS osworld_browser",
        "PASS webmall_manifest",
        "PASS webmall_store_1_origin",
        "PASS webmall_store_2_origin",
        "PASS webmall_store_3_origin",
        "PASS webmall_store_4_origin",
        "doctor=PASS",
    ]


def test_webmall_url_run_rejects_missing_origin_before_external_probe(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 URL run 在 browser 探测、模型与 RunStore 副作前闭合 origin。

    输入参数：
        tmp_path：pytest 提供的未使用运行路径。
        capsys：CLI 标准输出与错误输出捕获 fixture。
        monkeypatch：只绑定前三店 origin，并把 browser 探测
            替换为不得调用的哨兵。
    输出返回值：
        无；CLI 以固定 manifest 异常失败，不探测外部环境也不
        创建 RunStore。
    """

    probe_called = False

    def forbidden_probe(_config: object) -> DoctorReport:
        """拒绝 preflight 未完成时访问 browser 环境。

        输入参数：
            _config：误调用时的 OSWorld doctor 配置。
        输出返回值：
            不返回；调用即使测试失败。
        """

        nonlocal probe_called
        probe_called = True
        raise AssertionError("browser probe must follow WebMall preflight")

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        forbidden_probe,
    )
    for index in range(1, 4):
        monkeypatch.setenv(
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN",
            f"https://url-store-{index}.example.invalid",
        )
    monkeypatch.delenv(
        "PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN",
        raising=False,
    )
    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    runs_root = tmp_path / "runs-must-not-exist"

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-OnlineShopping-SingleProductSearch-001",
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(tmp_path / "missing.qcow2"),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            str(runs_root),
            "--model",
            "synthetic-model",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "error=WebMallEnvironmentManifestError\n"
    assert probe_called is False
    assert not runs_root.exists()


def test_webmall_checkout_run_uses_native_runtime_binding_and_version_vector(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Checkout CLI 真正装配 WebMall wrapper 而不再走 OSWorld-only 分支。

    输入参数：
        tmp_path：pytest 提供的 qcow2 占位文件与 RunStore 根。
        capsys：CLI 标准输出与错误捕获 fixture。
        monkeypatch：使两组 doctor 门禁通过，替换付费 Agent 与
            AttemptRunner 执行，但保留真实 manifest/source/lease/session 装配。
    输出返回值：
        无；RunStore 记录 WebMall v2 版本向量，AttemptRunner 收到
        ``WebMallTaskEnvironment`` 和仅 Agent instruction 已物化的 task，
        磁盘不含 origin/reader/token 值。
    """

    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    qcow2_path = tmp_path / "browser.qcow2"
    qcow2_path.write_bytes(b"synthetic-browser-image")
    runs_root = tmp_path / "runs"
    lease_credential = "private-" + ("x" * 32)
    deployment = {
        **{
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
                f"https://private-store-{index}.example.invalid"
            )
            for index in range(1, 5)
        },
        **{
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET": (
                f"docker:private-reader-{index}"
            )
            for index in range(1, 5)
        },
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL": (
            "https://private-lease.example.invalid"
        ),
        "PARAGUIBENCH_WEBMALL_LEASE_TOKEN": lease_credential,
        "PARAGUIBENCH_MODEL_API_KEY": "private-model-key",
        "PARAGUIBENCH_MODEL_BASE_URL": "https://model.example.invalid/v1",
    }
    for name, value in deployment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        lambda _config: DoctorReport(checks=(DoctorCheck("osworld_browser", True),)),
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_webmall_prerequisites",
        lambda _manifest, **_kwargs: DoctorReport(
            checks=(DoctorCheck("webmall_runtime", True),)
        ),
    )
    monkeypatch.setattr(
        "paraguibench.cli.main._build_agent",
        lambda *_args, **_kwargs: (
            object(),
            "gui_only.synthetic",
            {"worker": "synthetic"},
        ),
    )
    captured_runtime: dict[str, object] = {}

    class _FakeAttemptRunner:
        """记录 CLI 装配结果而不启动 Docker 或模型。"""

        def __init__(self, store: RunStore) -> None:
            """保存已创建的 RunStore 以证明装配顺序。

            输入参数：
                store：CLI 已建立版本化 Run/Attempt 的 RunStore。
            输出返回值：
                无。
            """

            captured_runtime["store"] = store

        def run(self, **kwargs: object) -> RuntimeAttemptResult:
            """检查 WebMall environment/task 三投影并返回合成通过。

            输入参数：
                kwargs：CLI 传入的 attempt、prepared task、environment、
                    agent 与 evaluator。
            输出返回值：
                执行与评价都成功、得分 1.0 的终态。
            """

            environment = kwargs["environment"]
            prepared = kwargs["prepared_task"]
            assert isinstance(environment, WebMallTaskEnvironment)
            assert "webmall://" not in prepared.agent_task["instruction"]
            assert prepared.trusted_task["expected_urls"][0].startswith(
                "webmall://store-3/"
            )
            captured_runtime.update(kwargs)
            return RuntimeAttemptResult(
                execution_outcome=ExecutionOutcome.SUCCEEDED,
                evaluation_outcome=EvaluationOutcome.PASSED,
                score=1.0,
            )

    monkeypatch.setattr(
        "paraguibench.cli.main.AttemptRunner",
        _FakeAttemptRunner,
    )

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-OnlineShopping-Checkout-001",
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(qcow2_path),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            str(runs_root),
            "--model",
            "synthetic-model",
            "--run-id",
            "run-webmall-native",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, (captured.out, captured.err)
    assert captured.err == ""
    assert "evaluation=PASSED" in captured.out
    run_record = json.loads(
        (runs_root / "run-webmall-native" / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["version_vector"]["environment_protocol"] == "webmall.browser.v1"
    assert (
        run_record["version_vector"]["evaluation_protocol"]
        == "paraguibench.webmall.checkout.closed-world.v2"
    )
    persisted = b"\n".join(
        path.read_bytes() for path in runs_root.rglob("*") if path.is_file()
    )
    for forbidden in deployment.values():
        assert forbidden.encode() not in persisted


def test_webmall_url_run_uses_unprivileged_wrapper_and_omits_order_references(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 URL-multiset run 不装配订单证据且不落盘未使用引用。

    输入参数：
        tmp_path：pytest 提供的 qcow2 占位文件和 RunStore 根。
        capsys：CLI 标准输出与错误输出捕获 fixture。
        monkeypatch：合成通过 browser/WebMall doctor，替换付费
            Agent/AttemptRunner，并将特权 binding 设为不得调用。
    输出返回值：
        无；AttemptRunner 收到无特权 URL wrapper，run.json 只保留
        WebMall 环境身份，没有 order-reader/lease 协议或引用。
    """

    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    qcow2_path = tmp_path / "browser.qcow2"
    qcow2_path.write_bytes(b"synthetic-browser-image")
    runs_root = tmp_path / "runs"
    origins = {
        f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
            f"https://url-private-store-{index}.example.invalid"
        )
        for index in range(1, 5)
    }
    deployment = {
        **origins,
        "PARAGUIBENCH_MODEL_API_KEY": "private-model-key",
        "PARAGUIBENCH_MODEL_BASE_URL": "https://model.example.invalid/v1",
    }
    for name, value in deployment.items():
        monkeypatch.setenv(name, value)
    for index in range(1, 5):
        monkeypatch.delenv(
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET",
            raising=False,
        )
    monkeypatch.delenv(
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL",
        raising=False,
    )
    monkeypatch.delenv(
        "PARAGUIBENCH_WEBMALL_LEASE_TOKEN",
        raising=False,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        lambda _config: DoctorReport(checks=(DoctorCheck("osworld_browser", True),)),
    )

    def inspect_url_only(_manifest: object, **kwargs: object) -> DoctorReport:
        """确认 CLI 将无特权需求传入 WebMall doctor。

        输入参数：
            _manifest：已经身份预检的 WebMall manifest。
            kwargs：CLI 传入的 environment 与特权需求。
        输出返回值：
            一项合成通过的 DoctorReport。
        """

        assert kwargs["requires_privileged_order_evidence"] is False
        assert kwargs["requires_cart_evidence"] is False
        return DoctorReport(checks=(DoctorCheck("webmall_url", True),))

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_webmall_prerequisites",
        inspect_url_only,
    )

    def forbidden_privileged_binding(**_kwargs: object) -> object:
        """拒绝 URL run 误装配 reader 或租约。

        输入参数：
            _kwargs：误调用时的 binding 参数。
        输出返回值：
            不返回；调用即使测试失败。
        """

        raise AssertionError("URL task must not build privileged runtime")

    monkeypatch.setattr(
        "paraguibench.cli.main.bind_webmall_privileged_runtime",
        forbidden_privileged_binding,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main._build_agent",
        lambda *_args, **_kwargs: (
            object(),
            "gui_only.synthetic",
            {"worker": "synthetic"},
        ),
    )

    class _FakeAttemptRunner:
        """检查 URL runtime 装配而不启动 Docker 或模型。"""

        def __init__(self, store: RunStore) -> None:
            """接受 CLI 已创建的 RunStore。

            输入参数：
                store：已写入版本向量的 RunStore。
            输出返回值：无。
            """

            assert isinstance(store, RunStore)

        def run(self, **kwargs: object) -> RuntimeAttemptResult:
            """确认 wrapper/task 投影后返回合成通过结果。

            输入参数：
                kwargs：CLI 传入的 AttemptRunner 公开参数。
            输出返回值：
                执行与评价均通过的合成结果。
            """

            environment = kwargs["environment"]
            prepared = kwargs["prepared_task"]
            assert isinstance(environment, WebMallURLTaskEnvironment)
            assert not hasattr(environment, "checkout_observation")
            assert "webmall://" not in prepared.agent_task["instruction"]
            assert prepared.trusted_task["expected_urls"][0].startswith("webmall://")
            return RuntimeAttemptResult(
                execution_outcome=ExecutionOutcome.SUCCEEDED,
                evaluation_outcome=EvaluationOutcome.PASSED,
                score=1.0,
            )

    monkeypatch.setattr(
        "paraguibench.cli.main.AttemptRunner",
        _FakeAttemptRunner,
    )

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-OnlineShopping-SingleProductSearch-001",
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(qcow2_path),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            str(runs_root),
            "--model",
            "synthetic-model",
            "--run-id",
            "run-webmall-url-native",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    run_record = json.loads(
        (runs_root / "run-webmall-url-native" / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["version_vector"]["evaluation_protocol"] == (
        "paraguibench.webmall.url-multiset.v1"
    )
    for forbidden_key in (
        "webmall_order_reader_protocol",
        "webmall_lease_protocol",
        "webmall_lease_coordinator_reference",
        "webmall_lease_credential_reference",
    ):
        assert forbidden_key not in run_record
    persisted = b"\n".join(
        path.read_bytes() for path in runs_root.rglob("*") if path.is_file()
    )
    for origin in origins.values():
        assert origin.encode() not in persisted


def test_webmall_cart_run_builds_browser_source_without_order_or_report_seam(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Cart run 选择第三种同浏览器 evidence 路径并保持脱敏。

    输入参数：
        tmp_path：pytest 提供的 qcow2 占位文件与 RunStore 根。
        capsys：CLI 标准输出与错误输出捕获 fixture。
        monkeypatch：显式越过 pending live gate，并替换模型、source 与 runner
            系统边界；不连接 114、Docker、CDP 或模型服务。
    输出返回值：
        无；Cart source 获得 manifest 合同和 loopback CDP，AttemptRunner
        收到专属环境；RunStore 只含协议/已验证位派生状态，不含 origin、worker、
        slug、Cart 内容或订单租约引用。
    """

    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    synthetic_manifest_root = tmp_path / "synthetic-live-image-manifests"
    synthetic_webmall_sha256 = hashlib.sha256(
        (synthetic_manifest_root / "webmall/environment-manifest.json").read_bytes()
    ).hexdigest()
    synthetic_browser_sha256 = hashlib.sha256(
        (synthetic_manifest_root / "osworld/image-manifest.json").read_bytes()
    ).hexdigest()
    monkeypatch.setattr(
        "paraguibench.runtime.webmall_binding.load_trusted_webmall_cart_reference_receipt",
        lambda _repo_root: SimpleNamespace(
            webmall_manifest_sha256=synthetic_webmall_sha256,
            browser_image_manifest_sha256=synthetic_browser_sha256,
        ),
    )
    qcow2_path = tmp_path / "browser.qcow2"
    qcow2_path.write_bytes(b"synthetic-browser-image")
    runs_root = tmp_path / "runs"
    origins = {
        f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
            f"https://cart-private-store-{index}.example.invalid"
        )
        for index in range(1, 5)
    }
    deployment = {
        **origins,
        "PARAGUIBENCH_MODEL_API_KEY": "private-model-key",
        "PARAGUIBENCH_MODEL_BASE_URL": "https://model.example.invalid/v1",
    }
    for name, value in deployment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        lambda _config: DoctorReport(checks=(DoctorCheck("osworld_browser", True),)),
    )

    def inspect_cart_only(_manifest: object, **kwargs: object) -> DoctorReport:
        """确认 CLI 以互斥 Cart 模式调用 WebMall doctor。

        输入参数：_manifest 为静态合同；kwargs 为 evidence 模式与环境。
        输出返回值：合成通过报告，以便仅测试后续装配分支。
        """

        assert kwargs["requires_privileged_order_evidence"] is False
        assert kwargs["requires_cart_evidence"] is True
        assert kwargs["cart_reference_validation_verified"] is True
        return DoctorReport(checks=(DoctorCheck("webmall_cart", True),))

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_webmall_prerequisites",
        inspect_cart_only,
    )

    def forbidden_privileged_binding(**_kwargs: object) -> object:
        """拒绝 Cart run 误装配 WP-CLI 或分布式租约。

        输入参数：_kwargs 为误调用的特权 binding 参数。
        输出返回值：不返回；任何调用都使测试失败。
        """

        raise AssertionError("Cart task must not build privileged runtime")

    monkeypatch.setattr(
        "paraguibench.cli.main.bind_webmall_privileged_runtime",
        forbidden_privileged_binding,
    )
    source_binding: dict[str, object] = {}

    class _FakeCartSource:
        """满足 Cart environment 构造契约但不执行任何 I/O 的 source。"""

        evidence_protocol_id = "paraguibench.webmall.cart-authoritative-state.v1"

        def prepare(self, _controller: object) -> None:
            """拒绝本 tracer 真正进入生命周期。

            输入参数：_controller 为未使用 controller。
            输出返回值：无；FakeAttemptRunner 不调用。
            """

        def read_cart(self, _worker: str, _store: str) -> object:
            """拒绝本 tracer 读取 Cart。

            输入参数：_worker/_store 为未使用身份。
            输出返回值：不返回；调用即表示 FakeAttemptRunner 越界。
            """

            raise AssertionError("Cart tracer must not perform browser I/O")

        def close(self) -> None:
            """提供环境构造所需的幂等清理 seam。

            输入参数：无。
            输出返回值：无。
            """

    def build_cart_source(**kwargs: object) -> _FakeCartSource:
        """记录 CLI 传入的非敏感 Cart source 绑定。

        输入参数：kwargs 为 registry、reader、worker 与 loopback CDP。
        输出返回值：不执行 I/O 的 source fake。
        """

        source_binding.update(kwargs)
        return _FakeCartSource()

    monkeypatch.setattr(
        "paraguibench.cli.main.WebMallBrowserCartSource",
        build_cart_source,
    )
    monkeypatch.setattr(
        "paraguibench.cli.main._build_agent",
        lambda *_args, **_kwargs: (
            object(),
            "gui_only.synthetic",
            {"worker": "synthetic"},
        ),
    )

    class _FakeAttemptRunner:
        """检查 Cart wrapper 装配而不启动 Docker、CDP 或模型。"""

        def __init__(self, store: RunStore) -> None:
            """验证 CLI 已建立版本化 RunStore。

            输入参数：store 为当前 run 的持久化入口。
            输出返回值：无。
            """

            assert isinstance(store, RunStore)

        def run(self, **kwargs: object) -> RuntimeAttemptResult:
            """检查环境/任务投影后返回合成通过结果。

            输入参数：kwargs 为 CLI 传给 AttemptRunner 的完整依赖。
            输出返回值：执行与评价均通过的合成结果。
            """

            environment = kwargs["environment"]
            prepared = kwargs["prepared_task"]
            assert isinstance(environment, WebMallCartTaskEnvironment)
            assert hasattr(environment, "cart_observation")
            assert not hasattr(environment, "checkout_observation")
            assert not hasattr(
                environment,
                "canonicalize_reported_product_urls",
            )
            assert "webmall://" not in prepared.agent_task["instruction"]
            assert prepared.trusted_task["expected_urls"][0].startswith("webmall://")
            return RuntimeAttemptResult(
                execution_outcome=ExecutionOutcome.SUCCEEDED,
                evaluation_outcome=EvaluationOutcome.PASSED,
                score=1.0,
            )

    monkeypatch.setattr(
        "paraguibench.cli.main.AttemptRunner",
        _FakeAttemptRunner,
    )

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-OnlineShopping-AddToCart-001",
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(qcow2_path),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            str(runs_root),
            "--model",
            "synthetic-model",
            "--run-id",
            "run-webmall-cart-native",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert source_binding["worker_id"] == "worker-1"
    assert source_binding["host"] == "127.0.0.1"
    assert source_binding["chromium_port"] == 9222
    assert getattr(source_binding["cart_reader"], "reader_kind") == (
        "woocommerce_store_api"
    )
    run_record = json.loads(
        (runs_root / "run-webmall-cart-native" / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["version_vector"]["evaluation_protocol"] == (
        "paraguibench.webmall.cart.closed-world.v1"
    )
    public_run = run_record["run"]
    assert public_run["webmall_cart_reader_protocol"] == (
        "paraguibench.webmall.woocommerce-store-api-cart.v1"
    )
    assert public_run["webmall_cart_evidence_protocol"] == (
        "paraguibench.webmall.cart-authoritative-state.v1"
    )
    assert public_run["webmall_cart_reference_live_validation_status"] == (
        "live_validated"
    )
    for forbidden_key in (
        "webmall_order_reader_protocol",
        "webmall_lease_protocol",
        "webmall_lease_coordinator_reference",
        "webmall_lease_credential_reference",
    ):
        assert forbidden_key not in public_run
    persisted = b"\n".join(
        path.read_bytes() for path in runs_root.rglob("*") if path.is_file()
    )
    for forbidden in (
        *origins.values(),
        "worker-1",
        "kingston-fury-beast-16gb-ddr5",
    ):
        assert forbidden.encode() not in persisted


def test_bookmark_run_uses_native_runtime_binding_without_persisting_secrets(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Bookmark run 装配原生证据源、评价器和版本向量。

    输入参数：
        tmp_path：pytest 提供的 qcow2 占位文件与 RunStore 根目录。
        capsys：CLI 标准输出与错误输出捕获 fixture。
        monkeypatch：替换 doctor、Agent 和 AttemptRunner，避免任何
            Docker、VM、网络或付费模型副作用。
    输出返回值：
        无；AttemptRunner 收到原生 OSWorld Bookmark runtime，
        ``run.json`` 记录固定评价协议且不持久化凭据或 endpoint 值。
    """

    repo_root = Path(__file__).resolve().parents[2]
    _install_synthetic_live_ready_image(
        repo_root=repo_root,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )
    qcow2_path = tmp_path / "browser.qcow2"
    qcow2_path.write_bytes(b"synthetic-browser-image")
    runs_root = tmp_path / "runs"
    model_sentinel = "-".join(("private", "bookmark", "model", "key"))
    base_url = "https://private-bookmark-model.example.invalid/v1"
    monkeypatch.setenv("PARAGUIBENCH_MODEL_API_KEY", model_sentinel)
    monkeypatch.setenv("PARAGUIBENCH_MODEL_BASE_URL", base_url)
    prepare_binding = ArtifactFamilyTaskPrepareBinding(
        task_id="synthetic-plumbing-task",
        input_draft_sha256="a" * 64,
        asset_manifest_sha256="b" * 64,
        relative_paths=("synthetic-input.bin",),
    )
    monkeypatch.setattr(
        "paraguibench.cli.main.preflight_artifact_family_task_prepare",
        lambda **_kwargs: prepare_binding,
    )

    def fake_doctor(config: object) -> DoctorReport:
        """替代真实主机门禁并证明 CLI 已构造 OSWorld 配置。

        输入参数：
            config：CLI 根据镜像、端口与任务资产构造的配置。
        输出返回值：
            只含一项通过检查的脱敏 doctor 报告。
        """

        assert config.qcow2_path == qcow2_path
        return DoctorReport(checks=(DoctorCheck("osworld_browser", True),))

    def fake_agent(
        arguments: argparse.Namespace,
        *,
        worker_base_url: str,
        planner_base_url: str | None,
    ) -> tuple[object, str, dict[str, str]]:
        """替代付费模型 Agent 并校验 endpoint 只停留在内存中。

        输入参数：
            arguments：CLI 解析后的 run 参数。
            worker_base_url：由环境变量引用解析得到的 endpoint 值。
            planner_base_url：GUI-only 路径不使用的 planner endpoint。
        输出返回值：
            合成 Agent、稳定系统 ID 与不含凭据的公开配置。
        """

        assert arguments.model == "synthetic-model"
        assert worker_base_url == base_url
        assert planner_base_url is None
        return object(), "gui_only.synthetic", {"worker": "synthetic"}

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        fake_doctor,
    )
    monkeypatch.setattr("paraguibench.cli.main._build_agent", fake_agent)
    captured_runtime: dict[str, object] = {}

    class _FakeAttemptRunner:
        """检查 Bookmark CLI 装配结果而不启动 VM 或 Agent。"""

        def __init__(self, store: RunStore) -> None:
            """接收 CLI 已建立的 RunStore。

            输入参数：
                store：已写入版本向量和 run 元数据的 RunStore。
            输出返回值：
                无。
            """

            assert isinstance(store, RunStore)

        def run(self, **kwargs: object) -> RuntimeAttemptResult:
            """断言原生 Bookmark environment/evaluator 后返回合成通过。

            输入参数：
                kwargs：CLI 传入的 attempt、task、environment、
                    agent 与 evaluator。
            输出返回值：
                执行与评价都成功、得分 1.0 的合成结果。
            """

            environment = kwargs["environment"]
            evaluator = kwargs["evaluator"]
            assert isinstance(environment, OSWorldTaskEnvironment)
            assert environment._artifact_family_task_prepare_binding is prepare_binding
            assert isinstance(
                environment._bookmark_evidence_source,
                OSWorldChromeBookmarkEvidenceSource,
            )
            assert isinstance(evaluator, OSWorldBookmarkTaskEvaluator)
            captured_runtime.update(kwargs)
            return RuntimeAttemptResult(
                execution_outcome=ExecutionOutcome.SUCCEEDED,
                evaluation_outcome=EvaluationOutcome.PASSED,
                score=1.0,
            )

    monkeypatch.setattr(
        "paraguibench.cli.main.AttemptRunner",
        _FakeAttemptRunner,
    )

    exit_code = main(
        [
            "run",
            "--repo-root",
            str(repo_root),
            "--task-id",
            "Operation-WebOperate-WebNavigate-001",
            "--asset-cache-root",
            str(tmp_path / "unused-assets"),
            "--qcow2-path",
            str(qcow2_path),
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
            "--runs-root",
            str(runs_root),
            "--model",
            "synthetic-model",
            "--run-id",
            "run-bookmark-native",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, (captured.out, captured.err)
    assert captured.err == ""
    assert "evaluation=PASSED" in captured.out
    assert captured_runtime["prepared_task"].trusted_task["task_id"] == (
        "Operation-WebOperate-WebNavigate-001"
    )
    run_record = json.loads(
        (runs_root / "run-bookmark-native" / "run.json").read_text(encoding="utf-8")
    )
    assert run_record["version_vector"]["evaluation_protocol"] == (
        "paraguibench.osworld.chrome-bookmarks.v1"
    )
    assert run_record["run"]["credential_reference"] == ("PARAGUIBENCH_MODEL_API_KEY")
    assert run_record["run"]["endpoint_reference"] == ("PARAGUIBENCH_MODEL_BASE_URL")
    persisted = b"\n".join(
        path.read_bytes() for path in runs_root.rglob("*") if path.is_file()
    )
    assert model_sentinel.encode() not in persisted
    assert base_url.encode() not in persisted
    assert b"synthetic-input.bin" not in persisted


def test_inspect_prints_only_outcomes_and_score(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证 inspect 不打印 details、task snapshot 或任意模型最终输出。

    输入参数：
        tmp_path：pytest 提供的合成 RunStore 根目录。
        capsys：pytest 标准输出捕获 fixture。
    输出返回值：
        无；终端只有 execution/evaluation/score 三项。
    """

    secret_fragment = "private-model-output"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-001",
        task_id="task-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("task-001"),
    )
    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.SUCCEEDED,
        evaluation_outcome=EvaluationOutcome.PASSED,
        score=1.0,
        details={"raw_output": secret_fragment},
    )

    exit_code = main(
        [
            "inspect",
            "--runs-root",
            str(tmp_path),
            "--run-id",
            "run-001",
            "--task-id",
            "task-001",
            "--attempt-id",
            "attempt-001",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "execution=SUCCEEDED" in output
    assert "evaluation=PASSED" in output
    assert "score=1.0" in output
    assert secret_fragment not in output


def test_inspect_diagnostics_prints_allowlisted_stage_and_versions(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证显式 diagnostics 只扩展稳定阶段与版本身份。

    输入参数：
        tmp_path：pytest 提供的合成 RunStore 根目录。
        capsys：pytest 标准输出与错误输出捕获 fixture。
    输出返回值：
        无；六字段版本向量和枚举 failure stage 可见，details 中的异常消息
        与 raw output sentinel 不得出现。
    """

    sentinel = "must-not-be-printed-by-diagnostics"
    vector = synthetic_run_version_vector()
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-diagnostics-001",
        run_record={"test": True},
        version_vector=vector,
    )
    attempt = store.start_attempt(
        run_id="run-diagnostics-001",
        task_id="task-diagnostics-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("task-diagnostics-001"),
    )
    store.finish_attempt(
        attempt=attempt,
        execution_outcome=ExecutionOutcome.INFRA_ERROR,
        evaluation_outcome=EvaluationOutcome.NOT_REQUESTED,
        score=None,
        failure_stage=AttemptFailureStage.ENVIRONMENT_PREPARE,
        details={
            "exception_message": sentinel,
            "raw_output": sentinel,
        },
    )

    exit_code = main(
        [
            "inspect",
            "--runs-root",
            str(tmp_path),
            "--run-id",
            "run-diagnostics-001",
            "--task-id",
            "task-diagnostics-001",
            "--attempt-id",
            "attempt-001",
            "--diagnostics",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "failure_stage=environment.prepare" in output
    assert "provenance=versioned" in output
    assert f"source_revision={vector.source_revision}" in output
    assert f"agent_code_revision={vector.agent_code_revision}" in output
    assert f"evaluator_revision={vector.evaluator_revision}" in output
    assert f"evaluation_protocol={vector.evaluation_protocol}" in output
    assert f"environment_protocol={vector.environment_protocol}" in output
    assert f"environment_revision={vector.environment_revision}" in output
    assert sentinel not in output


def test_inspect_missing_runs_root_is_strictly_read_only(
    tmp_path: Path,
) -> None:
    """验证拼错的 inspect 根目录不会被只读命令创建或修改。

    输入参数：
        tmp_path：pytest 提供的现有父目录。
    输出返回值：
        无；inspect 返回门禁错误，目标 runs-root 在调用后仍不存在。
    """

    missing_root = tmp_path / "mistyped-runs-root"

    exit_code = main(
        [
            "inspect",
            "--runs-root",
            str(missing_root),
            "--run-id",
            "run-001",
            "--task-id",
            "task-001",
            "--attempt-id",
            "attempt-001",
        ]
    )

    assert exit_code == 2
    assert not missing_root.exists()
