"""WebMall doctor 一次聚合四店、reader 与分布式租约配置测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from paraguibench.integrations.webmall import (
    load_webmall_environment_manifest,
)
from paraguibench.runtime.webmall_doctor import (
    inspect_webmall_prerequisites,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = load_webmall_environment_manifest(
    _REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"
)
_SYNTHETIC_LEASE_CREDENTIAL = "test-" + ("x" * 32)


def _complete_environment() -> dict[str, str]:
    """构造所有 WebMall 部署引用都存在的合成环境。

    输入参数：
        无。
    输出返回值：
        四店 origin/reader target 与 loopback 租约 endpoint/token。
    """

    return {
        **{
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
                f"https://store-{index}.example.invalid"
            )
            for index in range(1, 5)
        },
        **{
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET": (
                f"docker:reference-store-{index}"
            )
            for index in range(1, 5)
        },
        "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL": "http://127.0.0.1:8765",
        "PARAGUIBENCH_WEBMALL_LEASE_TOKEN": _SYNTHETIC_LEASE_CREDENTIAL,
    }


def test_doctor_lists_every_missing_webmall_binding_without_short_circuit() -> None:
    """验证空环境一次列出四店、WP-CLI 与租约全部缺口。

    输入参数：
        无；环境映射为空，可执行文件探针固定失败。
    输出返回值：
        无；report 不通过，但包含固定顺序的全部 12 项检查。
    """

    report = inspect_webmall_prerequisites(
        _MANIFEST,
        environment={},
        executable_probe=lambda _: False,
    )

    assert report.ok is False
    assert [check.name for check in report.checks] == [
        "webmall_manifest",
        "webmall_store_1_origin",
        "webmall_store_2_origin",
        "webmall_store_3_origin",
        "webmall_store_4_origin",
        "webmall_store_1_reader_target",
        "webmall_store_2_reader_target",
        "webmall_store_3_reader_target",
        "webmall_store_4_reader_target",
        "webmall_wp_cli",
        "webmall_lease_endpoint",
        "webmall_lease_credential",
    ]
    assert [check.passed for check in report.checks] == [
        True,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    ]


def test_doctor_accepts_complete_bindings_without_persisting_values() -> None:
    """验证完整部署引用全部通过，报告仅含名称和布尔值。

    输入参数：
        无；使用合成 origin、target、loopback endpoint 与 token。
    输出返回值：
        无；report 通过，其 repr 不含 endpoint、token 或 origin。
    """

    environment = _complete_environment()
    report = inspect_webmall_prerequisites(
        _MANIFEST,
        environment=environment,
        executable_probe=lambda executable: executable == "wp",
    )

    assert report.ok is True
    serialized = repr(report)
    assert _SYNTHETIC_LEASE_CREDENTIAL not in serialized
    assert "127.0.0.1" not in serialized
    assert "store-1.example.invalid" not in serialized


def test_doctor_rejects_non_loopback_plaintext_lease_endpoint() -> None:
    """验证 Bearer credential 不会通过非 loopback 明文 HTTP 发送。

    输入参数：
        无；将租约 endpoint 改为内网明文地址。
    输出返回值：
        无；其他项通过，仅 endpoint 检查失败。
    """

    environment = _complete_environment()
    environment["PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL"] = "http://192.0.2.10:8765"

    report = inspect_webmall_prerequisites(
        _MANIFEST,
        environment=environment,
        executable_probe=lambda _: True,
    )

    checks = {check.name: check.passed for check in report.checks}
    assert checks["webmall_lease_endpoint"] is False
    assert sum(not check.passed for check in report.checks) == 1


def test_url_multiset_doctor_requires_only_manifest_and_four_origins() -> None:
    """验证 URL 任务不把订单 reader、WP-CLI 或租约变量当作前置。

    输入参数：
        无；只提供 manifest 声明的四个测试 origin，其余环境为空。
    输出返回值：
        无；report 通过且精确包含 manifest 与四店 origin
        五项检查，可执行文件探针不被调用。
    """

    origins_only = {
        key: value
        for key, value in _complete_environment().items()
        if key.endswith("_ORIGIN")
    }

    def forbidden_probe(_: str) -> object:
        """拒绝 URL-only doctor 误查 WP-CLI。

        输入参数：
            _：误调用时的可执行文件名。
        输出返回值：
            不返回；调用即使测试失败。
        """

        raise AssertionError("URL-only doctor must not probe wp")

    report = inspect_webmall_prerequisites(
        _MANIFEST,
        requires_privileged_order_evidence=False,
        environment=origins_only,
        executable_probe=forbidden_probe,
    )

    assert report.ok is True
    assert [check.name for check in report.checks] == [
        "webmall_manifest",
        "webmall_store_1_origin",
        "webmall_store_2_origin",
        "webmall_store_3_origin",
        "webmall_store_4_origin",
    ]


def test_cart_doctor_keeps_reference_live_probe_gate_closed() -> None:
    """验证 Cart 只检查浏览器 reader，但 pending 实测门禁仍使报告失败。

    输入参数：
        无；只提供四店 origin，并禁止任何 WP-CLI 探针。
    输出返回值：
        无；静态 reader 合同通过，但 reference live validation 明确失败。
    """

    origins_only = {
        key: value
        for key, value in _complete_environment().items()
        if key.endswith("_ORIGIN")
    }

    def forbidden_probe(_: str) -> object:
        """拒绝 Cart doctor 把 WP-CLI 错当作浏览器 cart 前置。

        输入参数：
            _：误调用时的可执行文件名。
        输出返回值：
            不返回；任何调用均使测试失败。
        """

        raise AssertionError("Cart doctor must not probe wp")

    report = inspect_webmall_prerequisites(
        _MANIFEST,
        requires_privileged_order_evidence=False,
        requires_cart_evidence=True,
        environment=origins_only,
        executable_probe=forbidden_probe,
    )

    assert report.ok is False
    assert [check.name for check in report.checks] == [
        "webmall_manifest",
        "webmall_store_1_origin",
        "webmall_store_2_origin",
        "webmall_store_3_origin",
        "webmall_store_4_origin",
        "webmall_cart_reader_contract",
        "webmall_cart_reader_reference_live_validation",
    ]
    assert [check.passed for check in report.checks] == [
        True,
        True,
        True,
        True,
        True,
        True,
        False,
    ]


def test_cart_doctor_trusts_receipt_not_manifest_status() -> None:
    """验证 Cart doctor 只信任当前 component receipt 的验证结果。

    输入参数：无；同时使用字段合法的合成
        ``live_validated`` manifest 和正式 ``pending`` manifest。
    输出返回值：无；仅修改 manifest 不能绕过门禁；
        上层已重新验证当前 receipt 时，``pending`` manifest
        可以直接通过，避免证据自失效。
    """

    live_manifest = replace(
        _MANIFEST,
        cart_reader=replace(
            _MANIFEST.cart_reader,
            reference_live_validation_status="live_validated",
        ),
    )
    origins_only = {
        key: value
        for key, value in _complete_environment().items()
        if key.endswith("_ORIGIN")
    }

    untrusted = inspect_webmall_prerequisites(
        live_manifest,
        requires_privileged_order_evidence=False,
        requires_cart_evidence=True,
        environment=origins_only,
    )
    trusted = inspect_webmall_prerequisites(
        _MANIFEST,
        requires_privileged_order_evidence=False,
        requires_cart_evidence=True,
        cart_reference_validation_verified=True,
        environment=origins_only,
    )

    untrusted_checks = {check.name: check.passed for check in untrusted.checks}
    trusted_checks = {check.name: check.passed for check in trusted.checks}
    assert untrusted_checks["webmall_cart_reader_reference_live_validation"] is False
    assert trusted_checks["webmall_cart_reader_reference_live_validation"] is True
    assert untrusted.ok is False
    assert trusted.ok is True


def test_cart_doctor_rejects_ambiguous_order_and_cart_evidence_modes() -> None:
    """验证 Cart 与特权订单 evidence 不能在同一 doctor 调用中混装。

    输入参数：无；同时打开两个互斥 evidence 标记。
    输出返回值：无；在读取环境或执行探针前以 ``TypeError`` 失败关闭。
    """

    probe_calls: list[str] = []

    with pytest.raises(TypeError):
        inspect_webmall_prerequisites(
            _MANIFEST,
            requires_privileged_order_evidence=True,
            requires_cart_evidence=True,
            environment={},
            executable_probe=lambda value: probe_calls.append(value),
        )

    assert probe_calls == []
