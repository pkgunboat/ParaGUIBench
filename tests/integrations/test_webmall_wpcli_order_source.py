"""WebMall 生产 WP-CLI 订单证据源的安全边界测试。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time

import pytest

from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest,
)
from paraguibench.integrations.webmall.wpcli_order_source import (
    BoundedProcessRequest,
    BoundedProcessResult,
    SubprocessBoundedProcessRunner,
    WebMallWPCLIOrderEvidenceSource,
    WebMallWPCLIOrderSourceError,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"


def _reader_environment() -> dict[str, str]:
    """构造只包含四店受信 reader target 的部署绑定。

    输入参数：
        无。
    输出返回值：
        四个 manifest ``reader_target_env`` 到合成 target 的映射。
    """

    return {
        f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET": (
            f"docker:paraguibench-webmall-store-{index}"
        )
        for index in range(1, 5)
    }


def _payload() -> bytes:
    """构造 parser 可接受的最小订单 JSON。

    输入参数：
        无。
    输出返回值：
        一笔已完成信用卡订单的 UTF-8 bytes。
    """

    return json.dumps(
        {
            "schema_version": 1,
            "complete": True,
            "orders": [
                {
                    "order_id": 42,
                    "status": "completed",
                    "payment_method": "mock_card",
                    "billing": {
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "email": "ada@example.invalid",
                        "address_1": "1 Analytical Engine Way",
                        "postcode": "SW1A 1AA",
                        "city": "London",
                        "state": "London",
                        "country": "GB",
                    },
                    "items": [
                        {
                            "product_id": 7,
                            "variation_id": 0,
                            "quantity": 2,
                            "canonical_slug": "analytical-engine",
                        }
                    ],
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _identity_payload(*order_ids: int) -> bytes:
    """构造仅携带订单 ID 的 WebMall v2 identity 载荷。

    输入参数：
        order_ids：权威商店返回的正整数订单 ID。
    输出返回值：
        不含 billing、payment 或 items 的 UTF-8 JSON bytes。
    """

    return json.dumps(
        {
            "schema_version": 2,
            "mode": "identities",
            "complete": True,
            "order_ids": list(order_ids),
        },
        separators=(",", ":"),
    ).encode("utf-8")


class _RecordingRunner:
    """记录有界进程请求并返回合成 WP-CLI 输出。"""

    def __init__(self, stdout: bytes) -> None:
        """保存合成 stdout 并初始化调用记录。

        输入参数：
            stdout：模拟 WP-CLI 成功输出的原始 bytes。
        输出返回值：
            无。
        """

        self.stdout = stdout
        self.requests: list[BoundedProcessRequest] = []

    def run(self, request: BoundedProcessRequest) -> BoundedProcessResult:
        """记录公开边界请求并返回成功结果。

        输入参数：
            request：证据源生成的有界进程请求。
        输出返回值：
            退出码为零、stderr 为空的合成结果。
        """

        self.requests.append(request)
        return BoundedProcessResult(
            returncode=0,
            stdout=self.stdout,
            stderr=b"",
        )


class _FixedResultRunner:
    """返回调用方指定的子进程结果。"""

    def __init__(self, result: BoundedProcessResult) -> None:
        """保存用于失败路径测试的固定结果。

        输入参数：
            result：每次 ``run`` 都返回的有界结果。
        输出返回值：
            无。
        """

        self.result = result

    def run(self, request: BoundedProcessRequest) -> BoundedProcessResult:
        """忽略请求并返回固定结果。

        输入参数：
            request：证据源生成的进程请求。
        输出返回值：
            构造时提供的同一结果。
        """

        del request
        return self.result


class _DetailsEchoRunner:
    """把固定 details argv 中的数字 ID 映射为合法 v2 载荷。"""

    def __init__(self) -> None:
        """初始化进程请求记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.requests: list[BoundedProcessRequest] = []

    def run(self, request: BoundedProcessRequest) -> BoundedProcessResult:
        """为当前分块的每个 ID 返回一笔严格订单。

        输入参数：
            request：source 生成的固定 details 请求。
        输出返回值：
            identity 与 argv 完全一致的成功进程结果。
        """

        self.requests.append(request)
        assert request.argv[5] == "details"
        base_order = json.loads(_payload())["orders"][0]
        orders = []
        for raw_id in request.argv[6:]:
            order = copy.deepcopy(base_order)
            order["order_id"] = int(raw_id)
            orders.append(order)
        stdout = json.dumps(
            {
                "schema_version": 2,
                "mode": "details",
                "complete": True,
                "orders": orders,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return BoundedProcessResult(returncode=0, stdout=stdout, stderr=b"")


def test_source_reads_identity_mode_without_historical_details() -> None:
    """验证新 source API 通过固定 v2 模式只读取历史 identity。

    输入参数：
        无。
    输出返回值：
        无；断言返回完整 identity 批次且 argv 不含详情请求。
    """

    runner = _RecordingRunner(_identity_payload(41, 42))
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=load_webmall_environment_manifest(_MANIFEST_PATH),
        manifest_path=_MANIFEST_PATH,
        environment=_reader_environment(),
        runner=runner,
    )

    batch = source.read_order_identities("store-2")

    assert tuple(identity.order_identity for identity in batch.identities) == (
        "41",
        "42",
    )
    assert batch.complete is True
    assert runner.requests[0].argv == (
        "wp",
        "--ssh=docker:paraguibench-webmall-store-2",
        "--quiet",
        "eval-file",
        "-",
        "identities",
    )


def test_source_reads_requested_details_in_bounded_exact_set_chunks() -> None:
    """验证新订单详情按有界 numeric argv 分块且完整合并。

    输入参数：
        无。
    输出返回值：
        无；断言每块最多 128 个 ID，且返回闭集无缺失。
    """

    requested_ids = tuple(str(value) for value in range(1, 258))
    runner = _DetailsEchoRunner()
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=load_webmall_environment_manifest(_MANIFEST_PATH),
        manifest_path=_MANIFEST_PATH,
        environment=_reader_environment(),
        runner=runner,
    )

    orders = source.read_checkout_orders_by_identity(
        "store-1",
        requested_ids,
    )

    assert tuple(order.order_identity for order in orders) == requested_ids
    assert len(runner.requests) == 3
    assert all(
        request.argv[:6]
        == (
            "wp",
            "--ssh=docker:paraguibench-webmall-store-1",
            "--quiet",
            "eval-file",
            "-",
            "details",
        )
        and 1 <= len(request.argv[6:]) <= 128
        for request in runner.requests
    )


@pytest.mark.parametrize(
    "order_identities",
    [
        ("42;touch-private",),
        ("42\n43",),
        ("+42",),
        ("042",),
        ("0",),
        ("9223372036854775808",),
        ("42", "42"),
    ],
)
def test_source_rejects_noncanonical_or_injectable_detail_ids_before_process(
    order_identities: tuple[str, ...],
) -> None:
    """验证 details argv 不能被换行、shell 片段或非规范数字注入。

    输入参数：
        order_identities：应在子进程前被拒绝的候选 ID 元组。
    输出返回值：
        无；断言 runner 未执行且固定错误不回显任一 ID。
    """

    runner = _RecordingRunner(_payload())
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=load_webmall_environment_manifest(_MANIFEST_PATH),
        manifest_path=_MANIFEST_PATH,
        environment=_reader_environment(),
        runner=runner,
    )

    with pytest.raises(WebMallWPCLIOrderSourceError) as captured:
        source.read_checkout_orders_by_identity(
            "store-1",
            order_identities,
        )

    assert str(captured.value) == "WEBMALL_WPCLI_ORDER_SOURCE_FAILED"
    assert all(value not in str(captured.value) for value in order_identities)
    assert runner.requests == []


def test_php_identity_mode_never_reads_historical_order_details() -> None:
    """验证 PHP identity 模式与可失效的历史详情读取结构隔离。

    输入参数：
        无。
    输出返回值：
        无；断言 identity 函数仅请求 ID，details 仍经过严格
        order/item 转换，因此已删商品只会使新订单详情失败。
    """

    script = (_MANIFEST_PATH.parent / "wp-order-evidence.php").read_text(
        encoding="utf-8"
    )
    identity_start = script.index("function paraguibench_webmall_emit_order_identities")
    details_start = script.index("function paraguibench_webmall_emit_order_details")
    identity_body = script[identity_start:details_start]
    details_body = script[details_start:]

    assert "'return' => 'ids'" in identity_body
    assert all(
        forbidden not in identity_body
        for forbidden in (
            "paraguibench_webmall_order_to_array",
            "get_billing_",
            "get_payment_method",
            "get_items",
            "get_post_field",
        )
    )
    assert "paraguibench_webmall_order_to_array" in details_body
    assert "get_post_field('post_name', $product_id)" in script
    assert "if ($mode === 'identities')" in script
    assert "if ($mode === 'details')" in script


def test_source_runs_fixed_wp_cli_request_and_returns_existing_dto() -> None:
    """验证受信 target 通过固定无 shell 请求进入现有 parser。

    输入参数：
        无。
    输出返回值：
        无；断言 argv、stdin、资源上限和 DTO 映射。
    """

    manifest = load_webmall_environment_manifest(_MANIFEST_PATH)
    runner = _RecordingRunner(_payload())
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=manifest,
        manifest_path=_MANIFEST_PATH,
        environment=_reader_environment(),
        runner=runner,
    )

    orders = source.read_orders("store-2")

    assert len(orders) == 1
    assert orders[0].logical_store_id == "store-2"
    assert orders[0].order_identity == "42"
    assert orders[0].payment_kind == "credit_card"
    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.argv == (
        "wp",
        "--ssh=docker:paraguibench-webmall-store-2",
        "--quiet",
        "eval-file",
        "-",
    )
    assert request.shell is False
    assert (
        request.stdin == (_MANIFEST_PATH.parent / "wp-order-evidence.php").read_bytes()
    )
    assert request.timeout_seconds == manifest.order_reader.timeout_seconds
    assert request.max_stdout_bytes == manifest.order_reader.max_stdout_bytes
    assert 0 < request.max_stderr_bytes <= 1024 * 1024


def test_source_rejects_unknown_store_before_process_execution() -> None:
    """验证调用方不能绕过 manifest 四店闭集注入 reader target。

    输入参数：
        无。
    输出返回值：
        无；断言非法 store 返回固定 code，且未调用进程边界。
    """

    runner = _RecordingRunner(_payload())
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=load_webmall_environment_manifest(_MANIFEST_PATH),
        manifest_path=_MANIFEST_PATH,
        environment=_reader_environment(),
        runner=runner,
    )

    with pytest.raises(WebMallWPCLIOrderSourceError) as captured:
        source.read_orders("store-5")

    assert str(captured.value) == "WEBMALL_WPCLI_ORDER_SOURCE_FAILED"
    assert runner.requests == []


def test_source_rechecks_manifest_pinned_script_sha_before_each_read(
    tmp_path: Path,
) -> None:
    """验证 reader 脚本在每次执行前都按 manifest SHA fail-closed。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；断言被替换的同名脚本不会进入子进程。
    """

    manifest = load_webmall_environment_manifest(_MANIFEST_PATH)
    fake_manifest_path = tmp_path / "environment-manifest.json"
    script_path = tmp_path / manifest.order_reader.script_path
    script_path.write_bytes(b"<?php echo 'tampered-private-order';")
    runner = _RecordingRunner(_payload())
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=manifest,
        manifest_path=fake_manifest_path,
        environment=_reader_environment(),
        runner=runner,
    )

    with pytest.raises(WebMallWPCLIOrderSourceError) as captured:
        source.read_orders("store-1")

    assert str(captured.value) == "WEBMALL_WPCLI_ORDER_SOURCE_FAILED"
    assert "tampered-private-order" not in str(captured.value)
    assert runner.requests == []


def test_source_nonzero_exit_never_echoes_process_or_binding_data() -> None:
    """验证 WP-CLI 非零退出不回显双流、订单、billing 或 target。

    输入参数：
        无。
    输出返回值：
        无；断言公开错误只含固定 code。
    """

    environment = _reader_environment()
    sensitive_values = (
        "private-order-42",
        "private-billing-ada",
        "docker:paraguibench-webmall-store-3",
    )
    runner = _FixedResultRunner(
        BoundedProcessResult(
            returncode=17,
            stdout=sensitive_values[0].encode("utf-8"),
            stderr=sensitive_values[1].encode("utf-8"),
        )
    )
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=load_webmall_environment_manifest(_MANIFEST_PATH),
        manifest_path=_MANIFEST_PATH,
        environment=environment,
        runner=runner,
    )

    with pytest.raises(WebMallWPCLIOrderSourceError) as captured:
        source.read_orders("store-3")

    message = str(captured.value)
    assert message == "WEBMALL_WPCLI_ORDER_SOURCE_FAILED"
    assert all(value not in message for value in sensitive_values)


def test_subprocess_runner_terminates_immediately_when_stdout_exceeds_limit() -> None:
    """验证默认 runner 在 stdout 超限时立即终止进程组。

    输入参数：
        无。
    输出返回值：
        无；断言失败早于总超时，证明不是事后检查。
    """

    request = BoundedProcessRequest(
        argv=(
            sys.executable,
            "-c",
            ("import os,time;os.write(1,b'x'*131072);time.sleep(10)"),
        ),
        stdin=b"ignored",
        timeout_seconds=4,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
        shell=False,
    )
    started = time.monotonic()

    with pytest.raises(WebMallWPCLIOrderSourceError):
        SubprocessBoundedProcessRunner().run(request)

    assert time.monotonic() - started < 2.5


def test_subprocess_runner_enforces_total_timeout_without_output() -> None:
    """验证不产生双流数据的挂起进程仍受总超时约束。

    输入参数：
        无。
    输出返回值：
        无；断言 runner 在一秒截止后以固定错误返回。
    """

    request = BoundedProcessRequest(
        argv=(sys.executable, "-c", "import time;time.sleep(10)"),
        stdin=b"ignored",
        timeout_seconds=1,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
        shell=False,
    )
    started = time.monotonic()

    with pytest.raises(WebMallWPCLIOrderSourceError) as captured:
        SubprocessBoundedProcessRunner().run(request)

    elapsed = time.monotonic() - started
    assert str(captured.value) == "WEBMALL_WPCLI_ORDER_SOURCE_FAILED"
    assert 0.8 <= elapsed < 2.5


def test_subprocess_runner_terminates_immediately_when_stderr_exceeds_limit() -> None:
    """验证 stderr 使用与 stdout 独立的流式硬上限。

    输入参数：
        无。
    输出返回值：
        无；断言大 stderr 在总超时之前被终止。
    """

    request = BoundedProcessRequest(
        argv=(
            sys.executable,
            "-c",
            ("import os,time;os.write(2,b's'*131072);time.sleep(10)"),
        ),
        stdin=b"ignored",
        timeout_seconds=4,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
        shell=False,
    )
    started = time.monotonic()

    with pytest.raises(WebMallWPCLIOrderSourceError):
        SubprocessBoundedProcessRunner().run(request)

    assert time.monotonic() - started < 2.5


def test_subprocess_runner_accepts_exact_limits_and_binary_stdin() -> None:
    """验证有界 runner 完整传递 stdin，且允许恰好等于上限的双流。

    输入参数：
        无。
    输出返回值：
        无；断言 bytes 未经 text/shell 转换且边界值不被误拒。
    """

    stdin = b"<?php\x00fixed-reader"
    request = BoundedProcessRequest(
        argv=(
            sys.executable,
            "-c",
            (
                "import sys;"
                "data=sys.stdin.buffer.read();"
                "sys.stdout.buffer.write(data);"
                "sys.stderr.buffer.write(b'err')"
            ),
        ),
        stdin=stdin,
        timeout_seconds=2,
        max_stdout_bytes=len(stdin),
        max_stderr_bytes=3,
        shell=False,
    )

    result = SubprocessBoundedProcessRunner().run(request)

    assert result.returncode == 0
    assert result.stdout == stdin
    assert result.stderr == b"err"


def test_source_snapshots_all_trusted_targets_at_construction() -> None:
    """验证构造后的外部环境映射变化不能重定向证据读取。

    输入参数：
        无。
    输出返回值：
        无；断言 argv 使用构造时的受信 target 快照。
    """

    environment = _reader_environment()
    runner = _RecordingRunner(_payload())
    source = WebMallWPCLIOrderEvidenceSource(
        manifest=load_webmall_environment_manifest(_MANIFEST_PATH),
        manifest_path=_MANIFEST_PATH,
        environment=environment,
        runner=runner,
    )
    environment["PARAGUIBENCH_WEBMALL_STORE_1_READER_TARGET"] = (
        "docker:attacker-rebound-store"
    )

    source.read_orders("store-1")

    assert runner.requests[0].argv[1] == ("--ssh=docker:paraguibench-webmall-store-1")


def test_subprocess_runner_does_not_inherit_api_secret_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 WP-CLI 子进程不继承模型/API 凭据类环境变量。

    输入参数：
        monkeypatch：pytest 提供的进程环境隔离工具。
    输出返回值：
        无；断言子进程看不到父进程中的合成 secret。
    """

    secret_name = "PARAGUIBENCH_TEST_PRIVATE_API_TOKEN"
    monkeypatch.setenv(secret_name, "must-not-reach-wp-cli")
    request = BoundedProcessRequest(
        argv=(
            sys.executable,
            "-c",
            (f"import os,sys;sys.stdout.write(os.environ.get('{secret_name}',''))"),
        ),
        stdin=b"ignored",
        timeout_seconds=2,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
        shell=False,
    )

    result = SubprocessBoundedProcessRunner().run(request)

    assert result.returncode == 0
    assert result.stdout == b""


def test_subprocess_runner_preserves_wp_cli_docker_no_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Docker 远程 WP-CLI 可继承官方的无 TTY 控制变量。

    输入参数：
        monkeypatch：pytest 提供的进程环境隔离工具。
    输出返回值：
        无；断言固定白名单仅将 ``WP_CLI_DOCKER_NO_TTY`` 原样传入。
    """

    variable_name = "WP_CLI_DOCKER_NO_TTY"
    monkeypatch.setenv(variable_name, "1")
    request = BoundedProcessRequest(
        argv=(
            sys.executable,
            "-c",
            (f"import os,sys;sys.stdout.write(os.environ.get('{variable_name}',''))"),
        ),
        stdin=b"ignored",
        timeout_seconds=2,
        max_stdout_bytes=1024,
        max_stderr_bytes=1024,
        shell=False,
    )

    result = SubprocessBoundedProcessRunner().run(request)

    assert result.returncode == 0
    assert result.stdout == b"1"
