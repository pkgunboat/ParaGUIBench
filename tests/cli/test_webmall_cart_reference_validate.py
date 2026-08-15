"""显式 ``webmall-cart reference-validate`` 候选 CLI 测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from paraguibench.benchmark import prepare_release_task
from paraguibench.cli.main import build_parser, main
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceReceipt,
)
from paraguibench.runstore import RunVersionVector
from paraguibench.runtime.doctor import DoctorCheck, DoctorReport
from paraguibench.runtime.webmall_binding import WebMallEvidenceMode
from paraguibench.runtime.webmall_binding import preflight_webmall_runtime


def _receipt() -> WebMallCartReferenceReceipt:
    """构造一个字段完整的脱敏 component receipt。

    输入参数：无。
    输出返回值：仅供 CLI 输出与 production orchestration 测试使用的 receipt。
    """

    sweep = ("store-1", "store-2", "store-3", "store-4")
    return WebMallCartReferenceReceipt(
        schema_version=1,
        receipt_kind=("paraguibench.webmall.cart-reader-reference-validation.v1"),
        outcome="PASSED",
        component_revision="1" * 64,
        webmall_manifest_id="webmall.reference-four-stores.v1",
        webmall_manifest_sha256="2" * 64,
        webmall_environment_id="webmall-woocommerce-four-stores",
        store_universe_id="webmall.four-stores.v1",
        browser_environment_id="osworld-ubuntu-x86_64",
        browser_image_manifest_sha256="3" * 64,
        browser_extracted_sha256="4" * 64,
        browser_container_image="example.invalid/image@sha256:" + "5" * 64,
        cart_reader_protocol_id=("paraguibench.webmall.woocommerce-store-api-cart.v1"),
        cart_evidence_protocol_id=("paraguibench.webmall.cart-authoritative-state.v1"),
        browser_context_continuity_verified=True,
        sweep_store_ids=(sweep, sweep),
        normalized_universe_match=True,
    )


def _version_vector() -> RunVersionVector:
    """构造候选验证绑定的正式全源码版本向量。

    输入参数：无。
    输出返回值：与 ``_receipt`` component revision 一致的向量。
    """

    return RunVersionVector(
        source_revision="tree-sha256:" + "1" * 64,
        agent_code_revision="tree-sha256:" + "1" * 64,
        evaluator_revision="tree-sha256:" + "1" * 64,
        evaluation_protocol="paraguibench.webmall.cart.closed-world.v1",
        environment_protocol="webmall.browser.v1",
        environment_revision="manifest-sha256:" + "6" * 64,
    )


def test_explicit_reference_validate_prints_only_canonical_component_receipt(
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 candidate 命令独立于普通 run，并仅输出 canonical JSON receipt。

    输入参数：capsys 捕获公开输出；monkeypatch 替换唯一 live 内部边界，
        避免启动浏览器、Docker、VM 或读取部署环境。
    输出返回值：无；命令返回 0，输出只有排序后的脱敏 receipt JSON。
    """

    calls: list[object] = []
    receipt = _receipt()

    def execute(arguments: object) -> object:
        """记录 argparse namespace 并返回脱敏 component receipt fake。

        输入参数：arguments 为 candidate command 的完整非敏感参数。
        输出返回值：拥有 ``to_dict`` 的 receipt fake。
        """

        calls.append(arguments)
        return receipt

    monkeypatch.setattr(
        "paraguibench.cli.main._execute_webmall_cart_reference_validation",
        execute,
        raising=False,
    )

    exit_code = main(
        [
            "webmall-cart",
            "reference-validate",
            "--repo-root",
            "/synthetic/repo",
            "--task-id",
            "Operation-OnlineShopping-AddToCart-001",
            "--asset-cache-root",
            "/synthetic/assets",
            "--qcow2-path",
            "/synthetic/Ubuntu.qcow2",
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
    assert len(calls) == 1
    assert captured.err == ""
    assert captured.out == (
        json.dumps(
            receipt.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )


def test_production_candidate_bypasses_only_pending_gate_and_runs_no_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 candidate 复用正式 task/runtime identity 后只执行 reference 环境。

    输入参数：monkeypatch 替换所有 VM/浏览器系统边界和摘要读取。
    输出返回值：无；pending gate 由专属 inspector 处理，内部入口收到同一
        Cart task/environment，且不需要模型或 Agent 参数。
    """

    from paraguibench.cli import main as cli_main_module

    repo_root = Path("/synthetic/repo")
    task = {
        "task_id": "Operation-OnlineShopping-AddToCart-001",
        "task_source": "WebMall",
        "task_type": "QA",
        "answer_type": "cart",
        "evaluator_path": "evaluators/cart_evaluator.py",
        "expected_urls": ("webmall://store-1/product/private-gold",),
    }
    prepared = SimpleNamespace(trusted_task=task)
    task_assets = object()
    task_gold = object()
    manifest = SimpleNamespace(cart_reader=object())
    browser_image = SimpleNamespace(live_run_ready=True)
    runtime = SimpleNamespace(
        evidence_mode=WebMallEvidenceMode.BROWSER_CART,
        manifest=manifest,
        webmall_manifest_sha256="2" * 64,
        browser_image=browser_image,
        browser_image_manifest_sha256="3" * 64,
        registry=object(),
        prepared_task=prepared,
        version_vector=_version_vector(),
    )
    environment = object()
    receipt = _receipt()
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        cli_main_module,
        "_load_task_context",
        lambda _arguments: (repo_root, prepared, task_assets, None),
    )
    monkeypatch.setattr(
        cli_main_module,
        "_load_task_gold_context",
        lambda **_kwargs: task_gold,
    )
    monkeypatch.setattr(
        cli_main_module,
        "preflight_webmall_cart_reference_candidate_runtime",
        lambda **_kwargs: runtime,
        raising=False,
    )

    def forbidden_normal_preflight(**_kwargs: object) -> object:
        """拒绝 candidate 误用会被过期 receipt 锁死的普通 preflight。

        输入参数：_kwargs 为误调用时的仓库、任务与环境。
        输出返回值：不返回；任何调用都使测试失败。
        """

        raise AssertionError("candidate must use refresh-safe preflight")

    monkeypatch.setattr(
        cli_main_module,
        "preflight_webmall_runtime",
        forbidden_normal_preflight,
    )

    def inspect(**kwargs: object) -> DoctorReport:
        """确认专属 inspector 得到全部静态身份并返回通过。

        输入参数：kwargs 为 arguments/runtime/assets/gold。
        输出返回值：单项通过的脱敏报告。
        """

        calls["inspect"] = kwargs
        return DoctorReport((DoctorCheck("reference_candidate", True),))

    monkeypatch.setattr(
        cli_main_module,
        "_inspect_webmall_cart_reference_prerequisites",
        inspect,
        raising=False,
    )

    def build_environment(**kwargs: object) -> object:
        """记录 production 环境装配参数且不启动任何外部资源。

        输入参数：kwargs 为 repo/runtime/assets/VM 配置。
        输出返回值：固定环境 identity object。
        """

        calls["environment_binding"] = kwargs
        return environment

    monkeypatch.setattr(
        cli_main_module,
        "_build_webmall_cart_reference_environment",
        build_environment,
        raising=False,
    )

    def component_revision(
        version_vector: RunVersionVector,
        *,
        repo_root: Path,
    ) -> str:
        """记录 receipt 使用 preflight 协议与共享仓库身份。

        输入参数：version_vector 为 runtime 持有的同一对象；
            repo_root 为派生 receipt-neutral component identity 的仓库。
        输出返回值：固定 component revision。
        """

        calls["component_version_vector"] = version_vector
        calls["component_repo_root"] = repo_root
        return "1" * 64

    monkeypatch.setattr(
        cli_main_module,
        "build_webmall_cart_reference_component_revision",
        component_revision,
        raising=False,
    )

    def verify_repository_identity(**kwargs: object) -> None:
        """记录 live capture 后的 manifest/browser/source 同源复验。

        输入参数：kwargs 为 repo、canonical task 和初始 runtime。
        输出返回值：无；合成身份稳定。
        """

        calls["repository_identity"] = kwargs

    monkeypatch.setattr(
        cli_main_module,
        "_verify_webmall_cart_reference_repository_identity",
        verify_repository_identity,
        raising=False,
    )

    def run_reference(**kwargs: object) -> WebMallCartReferenceReceipt:
        """记录 production 内部入口绑定并返回成功 receipt。

        输入参数：kwargs 为环境、task、manifest、image 与两个摘要。
        输出返回值：固定脱敏 receipt。
        """

        calls["run"] = kwargs
        return receipt

    monkeypatch.setattr(
        cli_main_module,
        "run_webmall_cart_reference_validation",
        run_reference,
        raising=False,
    )

    def validate_receipt(value: object, **kwargs: object) -> object:
        """记录 post-run receipt 重新绑定当前 manifest 与 component revision。

        输入参数：value 为 receipt payload；kwargs 为四项当前版本身份。
        输出返回值：原 receipt identity。
        """

        calls["validate_receipt"] = {"value": value, **kwargs}
        return receipt

    monkeypatch.setattr(
        cli_main_module,
        "validate_webmall_cart_reference_receipt",
        validate_receipt,
        raising=False,
    )
    arguments = build_parser().parse_args(
        [
            "webmall-cart",
            "reference-validate",
            "--repo-root",
            str(repo_root),
            "--task-id",
            task["task_id"],
            "--asset-cache-root",
            "/synthetic/assets",
            "--qcow2-path",
            "/synthetic/Ubuntu.qcow2",
            "--server-port",
            "5000",
            "--vnc-port",
            "5900",
            "--chromium-port",
            "9222",
        ]
    )

    result = cli_main_module._execute_webmall_cart_reference_validation(arguments)

    assert result is receipt
    assert calls["run"]["environment"] is environment  # type: ignore[index]
    assert calls["run"]["task"] is task  # type: ignore[index]
    assert calls["run"]["webmall_manifest_sha256"] == "2" * 64  # type: ignore[index]
    assert calls["run"]["component_revision"] == "1" * 64  # type: ignore[index]
    assert calls["component_version_vector"] is runtime.version_vector
    assert calls["component_repo_root"] == repo_root
    assert calls["repository_identity"] == {
        "repo_root": repo_root,
        "prepared_task": prepared,
        "runtime": runtime,
    }
    assert calls["validate_receipt"]["value"] == receipt.to_dict()  # type: ignore[index]


def test_repository_identity_recheck_rejects_manifest_sha_aba(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 manifest 解析对象相同但原始字节 SHA 不同时仍失败关闭。

    输入参数：monkeypatch 使 post-run 同源 loader 返回原对象与
        不同摘要，模拟 A→B→A 中的错配。
    输出返回值：无；候选验证不会为错配身份返回 receipt。
    """

    from paraguibench.cli import main as cli_main_module

    repo_root = Path(__file__).resolve().parents[2]
    prepared = prepare_release_task(
        repo_root,
        "Operation-OnlineShopping-AddToCart-001",
        environment_bindings={},
    )
    origins = {
        f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
            f"https://store-{index}.example.invalid"
        )
        for index in range(1, 5)
    }
    runtime = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepared,
        environment=origins,
    )
    monkeypatch.setattr(
        cli_main_module,
        "load_webmall_environment_manifest_with_sha256",
        lambda _path: (runtime.manifest, "9" * 64),
    )
    monkeypatch.setattr(
        cli_main_module,
        "load_osworld_image_manifest_with_sha256",
        lambda _path: (
            runtime.browser_image,
            runtime.browser_image_manifest_sha256,
        ),
    )
    monkeypatch.setattr(
        cli_main_module,
        "build_run_version_vector",
        lambda **_kwargs: runtime.version_vector,
    )

    with pytest.raises(ValueError, match="version identity"):
        cli_main_module._verify_webmall_cart_reference_repository_identity(
            repo_root=repo_root,
            prepared_task=prepared,
            runtime=runtime,
        )


def test_reference_inspector_ignores_exactly_model_and_pending_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 candidate 不放宽镜像、端口、origin 或 reader 静态合同门禁。

    输入参数：monkeypatch 注入含两项模型失败与一项 pending 失败的报告。
    输出返回值：无；结果仅移除这三项，其他检查保持原顺序和状态。
    """

    from paraguibench.cli import main as cli_main_module

    osworld_report = DoctorReport(
        (
            DoctorCheck("python_version", True),
            DoctorCheck("qcow2_digest", True),
            DoctorCheck("api_key", False),
            DoctorCheck("model_base_url", False),
        )
    )
    webmall_report = DoctorReport(
        (
            DoctorCheck("webmall_manifest", True),
            DoctorCheck("webmall_store_1_origin", True),
            DoctorCheck("webmall_cart_reader_contract", True),
            DoctorCheck(
                "webmall_cart_reader_reference_live_validation",
                False,
            ),
        )
    )
    monkeypatch.setattr(
        cli_main_module,
        "_doctor_config_from_context",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        cli_main_module,
        "inspect_osworld_prerequisites",
        lambda _config, **_kwargs: osworld_report,
    )

    def inspect_candidate_webmall(
        *_args: object,
        **kwargs: object,
    ) -> DoctorReport:
        """确认 candidate doctor 不伪造已完成的 component receipt。

        输入参数：_args/kwargs 为 CLI 传给 WebMall doctor 的全部上下文。
        输出返回值：预置的 pending 失败报告。
        """

        assert kwargs["cart_reference_validation_verified"] is False
        return webmall_report

    monkeypatch.setattr(
        cli_main_module,
        "inspect_webmall_prerequisites",
        inspect_candidate_webmall,
    )
    runtime = SimpleNamespace(
        browser_image=object(),
        manifest=object(),
        cart_reference_validation_verified=False,
    )
    arguments = SimpleNamespace()

    report = cli_main_module._inspect_webmall_cart_reference_prerequisites(
        arguments=arguments,
        runtime=runtime,
        task_assets=object(),
        task_gold=object(),
    )

    assert report.ok is True
    assert tuple(check.name for check in report.checks) == (
        "python_version",
        "qcow2_digest",
        "webmall_manifest",
        "webmall_store_1_origin",
        "webmall_cart_reader_contract",
    )


def test_reference_inspector_rejects_duplicate_ignored_check_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证被排除的门禁名称必须在预期报告中各出现一次。

    输入参数：monkeypatch 注入额外同名失败检查。
    输出返回值：无；专属 inspector 失败关闭，不会误删新增门禁。
    """

    from paraguibench.cli import main as cli_main_module

    osworld_report = DoctorReport(
        (
            DoctorCheck("api_key", False),
            DoctorCheck("api_key", False),
            DoctorCheck("model_base_url", False),
        )
    )
    webmall_report = DoctorReport(
        (
            DoctorCheck(
                "webmall_cart_reader_reference_live_validation",
                False,
            ),
        )
    )
    monkeypatch.setattr(
        cli_main_module,
        "_doctor_config_from_context",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        cli_main_module,
        "inspect_osworld_prerequisites",
        lambda _config, **_kwargs: osworld_report,
    )
    monkeypatch.setattr(
        cli_main_module,
        "inspect_webmall_prerequisites",
        lambda *_args, **_kwargs: webmall_report,
    )

    with pytest.raises(ValueError, match="WebMall Cart reference doctor"):
        cli_main_module._inspect_webmall_cart_reference_prerequisites(
            arguments=SimpleNamespace(),
            runtime=SimpleNamespace(
                browser_image=object(),
                manifest=object(),
                cart_reference_validation_verified=False,
            ),
            task_assets=object(),
            task_gold=object(),
        )


def test_standard_doctor_forwards_verified_component_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证普通 doctor 把 identity preflight 已验证位传给 Cart 实测门禁。

    输入参数：monkeypatch 注入不含私有值的已验证 runtime identity
        及两份合成通过报告。
    输出返回值：无；doctor 返回成功，且 WebMall inspector 看到
        ``cart_reference_validation_verified=True``。
    """

    from paraguibench.cli import main as cli_main_module

    prepared = SimpleNamespace(trusted_task={"task_source": "WebMall"})
    identity = SimpleNamespace(
        browser_image=object(),
        manifest=object(),
        evidence_mode=WebMallEvidenceMode.BROWSER_CART,
        cart_reference_validation_verified=True,
    )
    monkeypatch.setattr(
        cli_main_module,
        "_load_task_context",
        lambda _arguments: (Path("/synthetic/repo"), prepared, object(), None),
    )
    monkeypatch.setattr(
        cli_main_module,
        "_load_task_gold_context",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        cli_main_module,
        "preflight_webmall_identity",
        lambda **_kwargs: identity,
    )
    monkeypatch.setattr(
        cli_main_module,
        "_doctor_config_from_context",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        cli_main_module,
        "inspect_osworld_prerequisites",
        lambda _config: DoctorReport((DoctorCheck("osworld", True),)),
    )

    def inspect_standard_doctor(
        _manifest: object,
        **kwargs: object,
    ) -> DoctorReport:
        """确认普通 doctor 传入 trusted loader 产生的真实已验证位。

        输入参数：_manifest 为已闭合 manifest；kwargs 为证据模式、
            component receipt 位与环境。
        输出返回值：一项合成通过的脱敏报告。
        """

        assert kwargs["requires_cart_evidence"] is True
        assert kwargs["cart_reference_validation_verified"] is True
        return DoctorReport((DoctorCheck("webmall", True),))

    monkeypatch.setattr(
        cli_main_module,
        "inspect_webmall_prerequisites",
        inspect_standard_doctor,
    )

    assert cli_main_module._handle_doctor(SimpleNamespace()) == 0


def test_reference_environment_builder_uses_owned_cart_only_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 candidate 只装配 owned OSWorld VM 与同 worker Cart source。

    输入参数：monkeypatch 将全部构造器替换为无 I/O 记录器。
    输出返回值：无；端口、固定镜像、缓存和同一 worker identity 精确传递，
        不装配 Agent、RunStore、WP-CLI 或 lease。
    """

    from paraguibench.cli import main as cli_main_module

    calls: dict[str, object] = {}
    docker_config = object()
    attested_session = object()
    controller = object()
    raw_environment = SimpleNamespace(controller=controller)
    source = object()
    wrapped_environment = object()

    def build_docker_config(**kwargs: object) -> object:
        """记录 owned Docker 固定配置。

        输入参数：kwargs 为镜像、qcow2、端口和资源限制。
        输出返回值：固定 config identity。
        """

        calls["docker_config"] = kwargs
        return docker_config

    monkeypatch.setattr(
        cli_main_module,
        "OSWorldDockerConfig",
        build_docker_config,
    )

    def build_attested_session(**kwargs: object) -> object:
        """记录 candidate 把已固定摘要与原配置交给 pinning session。

        输入参数：kwargs 为 config 与 expected_qcow2_sha256。
        输出返回值：不写磁盘的合成 attested session。
        """

        calls["attested_session"] = kwargs
        return attested_session

    monkeypatch.setattr(
        cli_main_module,
        "WebMallCartAttestedDockerSession",
        build_attested_session,
        raising=False,
    )
    monkeypatch.setattr(
        cli_main_module,
        "OSWorldController",
        lambda endpoint: calls.setdefault("controller_endpoint", endpoint)
        and controller,
    )

    def build_raw_environment(**kwargs: object) -> object:
        """记录未启动的 OSWorld environment 构造参数。

        输入参数：kwargs 为仓库、缓存、session、controller 与 timeout。
        输出返回值：固定 raw environment。
        """

        calls["raw_environment"] = kwargs
        return raw_environment

    monkeypatch.setattr(
        cli_main_module,
        "OSWorldTaskEnvironment",
        build_raw_environment,
    )

    def build_source(**kwargs: object) -> object:
        """记录同 worker Cart source 绑定且不连接 CDP。

        输入参数：kwargs 为 registry、reader、worker 与 loopback CDP。
        输出返回值：固定 source identity。
        """

        calls["source"] = kwargs
        return source

    monkeypatch.setattr(
        cli_main_module,
        "WebMallBrowserCartSource",
        build_source,
    )

    def wrap_environment(**kwargs: object) -> object:
        """记录 Cart 专属环境的 raw/source/worker 同一性。

        输入参数：kwargs 为 Cart wrapper 构造参数。
        输出返回值：固定 wrapped environment。
        """

        calls["wrapper"] = kwargs
        return wrapped_environment

    monkeypatch.setattr(
        cli_main_module,
        "WebMallCartTaskEnvironment",
        wrap_environment,
    )
    arguments = SimpleNamespace(
        asset_cache_root="/synthetic/assets",
        qcow2_path="/synthetic/Ubuntu.qcow2",
        server_port=5000,
        vnc_port=5900,
        chromium_port=9222,
        ram_size="8G",
        cpu_cores=4,
        ready_timeout=360.0,
    )
    runtime = SimpleNamespace(
        browser_image=SimpleNamespace(
            container_image="image@sha256:" + "1" * 64,
            extracted_sha256="2" * 64,
        ),
        registry=object(),
        manifest=SimpleNamespace(cart_reader=object()),
    )

    result = cli_main_module._build_webmall_cart_reference_environment(
        arguments=arguments,
        repo_root=Path("/synthetic/repo"),
        runtime=runtime,
        task_assets=object(),
        artifact_prepare_binding=None,
    )

    assert result is wrapped_environment
    assert calls["source"]["worker_id"] == "worker-1"  # type: ignore[index]
    assert calls["source"]["host"] == "127.0.0.1"  # type: ignore[index]
    assert calls["source"]["chromium_port"] == 9222  # type: ignore[index]
    assert calls["attested_session"] == {
        "config": docker_config,
        "expected_qcow2_sha256": "2" * 64,
    }
    assert calls["raw_environment"]["docker_session"] is attested_session  # type: ignore[index]
    assert calls["wrapper"] == {  # type: ignore[comparison-overlap]
        "environment": raw_environment,
        "evidence_source": source,
        "worker_id": "worker-1",
    }
