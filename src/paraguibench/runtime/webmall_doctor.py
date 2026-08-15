"""WebMall 四店、WP-CLI reader 与分布式租约的聚合部署检查。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ipaddress
import os
import shutil
from urllib.parse import urlsplit

from paraguibench.integrations.webmall import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
    WebMallEnvironmentManifest,
    WebMallURLRegistry,
)
from paraguibench.integrations.webmall.distributed_lease import (
    HTTPJSONLeaseTransport,
    is_valid_lease_bearer_credential,
)
from paraguibench.runtime.doctor import DoctorCheck, DoctorReport


def inspect_webmall_prerequisites(
    manifest: WebMallEnvironmentManifest,
    *,
    requires_privileged_order_evidence: bool = True,
    requires_cart_evidence: bool = False,
    cart_reference_validation_verified: bool = False,
    environment: Mapping[str, str] | None = None,
    executable_probe: Callable[[str], object] | None = None,
) -> DoctorReport:
    """一次检查所有 WebMall 部署引用，不因单项缺失而短路。

    输入参数：
        manifest：已经校验 reader 脚本 SHA、四店与 lease contract
            的 WebMall 环境 manifest。
        requires_privileged_order_evidence：当前 evaluator 是否需要
            WP-CLI 订单源和分布式租约；URL-multiset 任务必须为假。
        requires_cart_evidence：当前 evaluator 是否需要同一浏览器会话的
            Cart Store API reader；该模式不探测 WP-CLI 或租约。
        cart_reference_validation_verified：当前独立 component receipt
            是否已与 task/environment/component 三层仓库身份重新验证。
            该布尔值是活性的唯一权威，manifest 中的 pending/live
            字段不能单独证明实机验证。
        environment：只读部署环境；本函数只读 manifest 显式列出
            的 origin、reader target、lease endpoint 与 credential。
        executable_probe：可选可执行文件探针；生产默认使用
            ``shutil.which`` 但不启动 WP-CLI。
    输出返回值：
        固定顺序、只含检查名和布尔值的 ``DoctorReport``；
        不保存任何环境变量值。
    异常：
        TypeError：manifest 或 environment 类型无效。
    """

    if not isinstance(manifest, WebMallEnvironmentManifest):
        raise TypeError("WebMall doctor manifest 类型无效")
    if not isinstance(requires_privileged_order_evidence, bool):
        raise TypeError("WebMall doctor 特权证据标记无效")
    if not isinstance(requires_cart_evidence, bool):
        raise TypeError("WebMall doctor cart 证据标记无效")
    if not isinstance(cart_reference_validation_verified, bool):
        raise TypeError("WebMall doctor cart reference 验证标记无效")
    if requires_privileged_order_evidence and requires_cart_evidence:
        raise TypeError("WebMall doctor evidence 模式必须互斥")
    env = os.environ if environment is None else environment
    if not isinstance(env, Mapping):
        raise TypeError("WebMall doctor environment 类型无效")
    probe = executable_probe or shutil.which

    manifest_ok = (
        manifest.store_universe_id == WEBMALL_STORE_UNIVERSE_ID
        and manifest.protocol_ids == ("webmall.browser.v1",)
        and tuple(store.logical_store_id for store in manifest.stores)
        == WEBMALL_LOGICAL_STORE_IDS
        and manifest.order_reader.protocol_id == "paraguibench.webmall.wp-cli-orders.v2"
        and manifest.cart_reader.protocol_id
        == "paraguibench.webmall.woocommerce-store-api-cart.v1"
        and manifest.cart_reader.evidence_protocol_id
        == "paraguibench.webmall.cart-authoritative-state.v1"
        and manifest.lease.protocol_id == "paraguibench.webmall.distributed-lease.v1"
        and manifest.reset.protocol_id == "paraguibench.webmall.baseline-delta.v1"
    )
    origin_values = [env.get(store.origin_env) for store in manifest.stores]
    origin_checks = [_valid_origin(value) for value in origin_values]
    _reject_duplicate_valid_values(origin_values, origin_checks)
    checks: list[DoctorCheck] = [
        DoctorCheck("webmall_manifest", manifest_ok),
    ]
    checks.extend(
        DoctorCheck(
            f"webmall_store_{index}_origin",
            passed,
        )
        for index, passed in enumerate(origin_checks, start=1)
    )
    if requires_cart_evidence:
        cart_reader_contract_ok = (
            manifest.cart_reader.reader_kind == "woocommerce_store_api"
            and manifest.cart_reader.endpoint_path == "/wp-json/wc/store/v1/cart"
            and manifest.cart_reader.same_browser_context_required is True
            and manifest.cart_reader.stability_read_count == 2
            and manifest.cart_reader.max_response_bytes == 2 * 1024 * 1024
            and manifest.cart_reader.max_items == 1024
            and manifest.cart_reader.max_quantity == 10_000
            and manifest.cart_reader.timeout_seconds == 10
        )
        checks.extend(
            (
                DoctorCheck(
                    "webmall_cart_reader_contract",
                    cart_reader_contract_ok,
                ),
                DoctorCheck(
                    "webmall_cart_reader_reference_live_validation",
                    cart_reference_validation_verified,
                ),
            )
        )
    if requires_privileged_order_evidence:
        reader_values = [env.get(store.reader_target_env) for store in manifest.stores]
        reader_checks = [_valid_reader_target(value) for value in reader_values]
        _reject_duplicate_valid_values(reader_values, reader_checks)
        try:
            wp_cli_ok = bool(probe("wp"))
        except Exception:
            wp_cli_ok = False
        coordinator_url = env.get(manifest.lease.coordinator_url_env)
        lease_endpoint_ok = _valid_lease_endpoint(coordinator_url)
        lease_credential_ok = _valid_lease_credential(
            env.get(manifest.lease.credential_env)
        )
        checks.extend(
            DoctorCheck(
                f"webmall_store_{index}_reader_target",
                passed,
            )
            for index, passed in enumerate(reader_checks, start=1)
        )
        checks.extend(
            (
                DoctorCheck("webmall_wp_cli", wp_cli_ok),
                DoctorCheck("webmall_lease_endpoint", lease_endpoint_ok),
                DoctorCheck(
                    "webmall_lease_credential",
                    lease_credential_ok,
                ),
            )
        )
    return DoctorReport(checks=tuple(checks))


def _valid_origin(value: object) -> bool:
    """验证单个部署 origin 是不含凭据与附加部分的 HTTP(S) 地址。

    输入参数：
        value：一个 manifest ``origin_env`` 指向的候选值。
    输出返回值：
        可被 ``WebMallURLRegistry`` 安全接受时为真。
    """

    if not isinstance(value, str) or not value:
        return False
    try:
        WebMallURLRegistry({"store-probe": value})
    except Exception:
        return False
    return True


def _valid_reader_target(value: object) -> bool:
    """验证 reader target 可作为单个 ``wp --ssh=`` argv 值。

    输入参数：
        value：manifest ``reader_target_env`` 指向的候选值。
    输出返回值：
        值非空、有界且不含空白或控制字符时为真。
    """

    return (
        isinstance(value, str)
        and 1 <= len(value) <= 1024
        and not any(character.isspace() for character in value)
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _reject_duplicate_valid_values(
    values: list[object],
    checks: list[bool],
) -> None:
    """将重复的有效 origin/target 同时标记为失败。

    输入参数：
        values：按四店顺序读取的候选值。
        checks：与 values 等长的局部合法性布尔列表。
    输出返回值：
        无；原地把所有重复的合法值位置改为 ``False``。
    """

    for index, value in enumerate(values):
        if checks[index] and sum(candidate == value for candidate in values) > 1:
            checks[index] = False


def _valid_lease_endpoint(value: object) -> bool:
    """验证租约 endpoint 为 HTTPS，或仅在 loopback 使用明文 HTTP。

    输入参数：
        value：manifest 声明的协调器 URL 环境变量值。
    输出返回值：
        endpoint 可由生产 transport 解析且不会在远程明文
        传输 Bearer credential 时为真。
    """

    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
            return False
        HTTPJSONLeaseTransport(value)
    except Exception:
        return False
    return True


def _is_loopback_host(hostname: str | None) -> bool:
    """判定主机名是否只指向当前机器。

    输入参数：
        hostname：``urlsplit`` 解析的不含括号主机名。
    输出返回值：
        ``localhost`` 或 IP loopback 地址时为真。
    """

    if not isinstance(hostname, str):
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _valid_lease_credential(value: object) -> bool:
    """验证租约 credential 存在且可安全放入 Bearer header。

    输入参数：
        value：manifest ``credential_env`` 指向的候选值。
    输出返回值：
        非空、有界且不含控制字符时为真。
    """

    return is_valid_lease_bearer_credential(value)
