"""解析 WebMall 四店环境 manifest 并绑定部署期 origin。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
)
from paraguibench.integrations.webmall.registry import (
    WebMallURLRegistry,
    WebMallURLRegistryError,
)


_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{1,127}")
_PAYMENT_ID_PATTERN = re.compile(r"[a-z0-9_]{1,64}")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_READER_SCRIPT_BYTES = 1_048_576
_READER_STDOUT_BYTES = 4 * 1024 * 1024
_CART_RESPONSE_BYTES = 2 * 1024 * 1024
_CART_MAX_ITEMS = 1024
_CART_MAX_QUANTITY = 10_000
_TOP_LEVEL_FIELDS = frozenset(
    {
        "$schema",
        "schema_version",
        "manifest_id",
        "environment_id",
        "protocol_ids",
        "store_universe_id",
        "browser_runtime",
        "service_images",
        "software",
        "stores",
        "order_reader",
        "cart_reader",
        "lease",
        "reset",
    }
)
_EXPECTED_PAYMENT_IDS = {
    "store-1": ("mock_card", "credit_card"),
    "store-2": ("mock_card", "credit_card"),
    "store-3": ("mock_card", "credit_card_onsite"),
    "store-4": ("mock_card", "cc_gateway"),
}


class WebMallEnvironmentManifestError(ValueError):
    """表示 WebMall 环境身份、脚本或部署 origin 绑定无效。"""

    code = "WEBMALL_ENVIRONMENT_MANIFEST_INVALID"

    def __init__(self) -> None:
        """构造不携带环境值、host、路径或底层异常的固定错误。

        输入参数：
            无。
        输出返回值：
            无；异常字符串固定为公开 code。
        """

        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class WebMallBrowserRuntime:
    """保存 WebMall 浏览器所复用的 OSWorld Chrome 环境身份。"""

    kind: str
    image_manifest_ref: str
    image_manifest_sha256: str
    required_protocol_id: str


@dataclass(frozen=True, slots=True)
class WebMallServiceImages:
    """保存四店共享的 WordPress 与 MariaDB 镜像 digest 引用。"""

    wordpress: str
    mariadb: str


@dataclass(frozen=True, slots=True)
class WebMallSoftwareVersions:
    """保存 reader 已核对的 WordPress、WooCommerce 与订单存储版本。"""

    wordpress_version: str
    woocommerce_version: str
    order_storage: str


@dataclass(frozen=True, slots=True)
class WebMallStoreBinding:
    """保存一个 logical store 的非敏感部署绑定名与信用卡方法闭集。"""

    logical_store_id: str
    origin_env: str
    reader_target_env: str
    credit_card_payment_method_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WebMallOrderReaderContract:
    """保存 WP-CLI reader 的协议、固定脚本与资源上限。"""

    protocol_id: str
    script_path: str
    script_sha256: str
    max_stdout_bytes: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class WebMallCartReaderContract:
    """保存浏览器同会话 WooCommerce Cart Store API 读取合同。

    输入参数：
        protocol_id/evidence_protocol_id：生产 reader 与不可变证据协议身份。
        reader_kind/endpoint_path：固定 Store API reader 类型和只读端点。
        same_browser_context_required：是否强制复用 Agent 的 BrowserContext。
        stability_read_count：每店必须一致的连续读取次数。
        max_response_bytes/max_items/max_quantity/timeout_seconds：资源闭集。
        reference_live_validation_status：参考部署实测门禁状态。
    输出返回值：
        不执行 I/O 的不可变 Cart reader 合同。
    """

    protocol_id: str
    evidence_protocol_id: str
    reader_kind: str
    endpoint_path: str
    same_browser_context_required: bool
    stability_read_count: int
    max_response_bytes: int
    max_items: int
    max_quantity: int
    timeout_seconds: int
    reference_live_validation_status: str


@dataclass(frozen=True, slots=True)
class WebMallLeaseContract:
    """保存跨 host 租约的非敏感协调器引用与时限。"""

    protocol_id: str
    coordinator_url_env: str
    credential_env: str
    namespace: str
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class WebMallResetContract:
    """保存 Attempt baseline/delta 的非破坏性状态隔离策略。"""

    protocol_id: str
    strategy: str


@dataclass(frozen=True, slots=True)
class WebMallEnvironmentManifest:
    """保存 WebMall browser、四店、reader、lease 与 reset 的版本闭包。"""

    manifest_id: str
    environment_id: str
    protocol_ids: tuple[str, ...]
    store_universe_id: str
    browser_runtime: WebMallBrowserRuntime
    service_images: WebMallServiceImages
    software: WebMallSoftwareVersions
    stores: tuple[WebMallStoreBinding, ...]
    order_reader: WebMallOrderReaderContract
    cart_reader: WebMallCartReaderContract
    lease: WebMallLeaseContract
    reset: WebMallResetContract


def load_webmall_environment_manifest(
    path: Path,
) -> WebMallEnvironmentManifest:
    """读取并严格验证 WebMall v1 环境 manifest 与 reader 脚本摘要。

    输入参数：
        path：仓库内或隔离测试目录中的 ``environment-manifest.json``。
    输出返回值：
        只含固定身份、digest 和环境变量名的不可变 manifest。
    异常：
        WebMallEnvironmentManifestError：文件、JSON、字段、四店闭包、镜像、
            软件版本或 reader 脚本完整性无效。
    """

    manifest, _manifest_sha256 = load_webmall_environment_manifest_with_sha256(path)
    return manifest


def load_webmall_environment_manifest_with_sha256(
    path: Path,
) -> tuple[WebMallEnvironmentManifest, str]:
    """从同一次稳定字节读取构造 WebMall manifest 与 SHA-256。

    输入参数：path 为仓库内或隔离测试目录中的 manifest。
    输出返回值：严格解析对象及产生该对象的同一份原始字节
        的 64 位小写 SHA-256。
    异常：WebMallEnvironmentManifestError：读取、解析或引用闭包无效。
    """

    raw, manifest_sha256 = _read_json_object_with_sha256(path)
    _require_exact_fields(raw, _TOP_LEVEL_FIELDS)
    if (
        raw["$schema"]
        != "../../benchmark/schemas/webmall-environment-manifest-v1.schema.json"
        or raw["schema_version"] != 1
        or isinstance(raw["schema_version"], bool)
        or raw["manifest_id"] != "webmall.reference-four-stores.v1"
        or raw["environment_id"] != "webmall-woocommerce-four-stores"
        or raw["protocol_ids"] != ["webmall.browser.v1"]
        or raw["store_universe_id"] != WEBMALL_STORE_UNIVERSE_ID
    ):
        raise WebMallEnvironmentManifestError

    browser_runtime = _parse_browser_runtime(raw["browser_runtime"])
    service_images = _parse_service_images(raw["service_images"])
    software = _parse_software(raw["software"])
    stores = _parse_stores(raw["stores"])
    order_reader = _parse_order_reader(raw["order_reader"])
    cart_reader = _parse_cart_reader(raw["cart_reader"])
    lease = _parse_lease(raw["lease"])
    reset = _parse_reset(raw["reset"])
    _verify_reader_script(
        manifest_path=path,
        contract=order_reader,
    )
    _verify_browser_manifest(
        manifest_path=path,
        contract=browser_runtime,
    )
    manifest = WebMallEnvironmentManifest(
        manifest_id=raw["manifest_id"],
        environment_id=raw["environment_id"],
        protocol_ids=tuple(raw["protocol_ids"]),
        store_universe_id=raw["store_universe_id"],
        browser_runtime=browser_runtime,
        service_images=service_images,
        software=software,
        stores=stores,
        order_reader=order_reader,
        cart_reader=cart_reader,
        lease=lease,
        reset=reset,
    )
    return manifest, manifest_sha256


def bind_webmall_origins(
    manifest: WebMallEnvironmentManifest,
    environment: Mapping[str, str],
) -> WebMallURLRegistry:
    """从显式环境变量引用建立四店 logical/runtime URL 注册表。

    输入参数：
        manifest：已经验证 reader 与四店闭包的环境 manifest。
        environment：部署进程环境；只读取四个 ``origin_env`` 引用。
    输出返回值：
        四个 logical store 到纯 HTTP(S) origin 的双向 registry。
    异常：
        WebMallEnvironmentManifestError：manifest/Mapping 类型无效，绑定缺失、
            重复或不是纯 origin；异常不会回显变量值。
    """

    if not isinstance(manifest, WebMallEnvironmentManifest) or not isinstance(
        environment,
        Mapping,
    ):
        raise WebMallEnvironmentManifestError
    origins: dict[str, str] = {}
    for store in manifest.stores:
        value = environment.get(store.origin_env)
        if not isinstance(value, str) or not value:
            raise WebMallEnvironmentManifestError
        origins[store.logical_store_id] = value
    try:
        return WebMallURLRegistry(origins)
    except (TypeError, ValueError, WebMallURLRegistryError):
        raise WebMallEnvironmentManifestError from None


def _read_json_object_with_sha256(path: Path) -> tuple[dict[str, Any], str]:
    """通过 nofollow descriptor 稳定读取 JSON 并返回同字节摘要。

    输入参数：
        path：候选 manifest 文件。
    输出返回值：
        UTF-8 JSON object 及与解析完全同源的 SHA-256。
    异常：
        WebMallEnvironmentManifestError：路径、文件类型、大小、编码或 JSON
            结构不合法。
    """

    if not isinstance(path, Path):
        raise WebMallEnvironmentManifestError
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise WebMallEnvironmentManifestError from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > _MAX_MANIFEST_BYTES
        ):
            raise WebMallEnvironmentManifestError
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise WebMallEnvironmentManifestError
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise WebMallEnvironmentManifestError
    except (OSError, WebMallEnvironmentManifestError):
        raise WebMallEnvironmentManifestError from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    try:
        payload = b"".join(chunks)
        decoded = payload.decode("utf-8", errors="strict")
        raw = json.loads(decoded, object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError, WebMallEnvironmentManifestError):
        raise WebMallEnvironmentManifestError from None
    if not isinstance(raw, dict):
        raise WebMallEnvironmentManifestError
    return raw, hashlib.sha256(payload).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """把 JSON object pairs 转成字典并拒绝重复字段。

    输入参数：
        pairs：``json.loads`` 按原顺序提供的字段和值。
    输出返回值：
        无重复 key 的普通字典。
    异常：
        WebMallEnvironmentManifestError：任一 key 重复或不是字符串。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise WebMallEnvironmentManifestError
        result[key] = value
    return result


def _require_exact_fields(value: object, fields: frozenset[str]) -> dict[str, Any]:
    """验证一个 manifest object 的字段集合精确等于协议闭集。

    输入参数：
        value：候选嵌套值。
        fields：本层允许且必需的字段集合。
    输出返回值：
        通过验证的原字典。
    异常：
        WebMallEnvironmentManifestError：类型或字段集合不匹配。
    """

    if not isinstance(value, dict) or set(value) != fields:
        raise WebMallEnvironmentManifestError
    return value


def _parse_browser_runtime(value: object) -> WebMallBrowserRuntime:
    """验证浏览器 runtime 精确复用固定 OSWorld Chrome 协议。

    输入参数：
        value：manifest ``browser_runtime`` object。
    输出返回值：
        不可变浏览器绑定。
    """

    raw = _require_exact_fields(
        value,
        frozenset(
            {
                "kind",
                "image_manifest_ref",
                "image_manifest_sha256",
                "required_protocol_id",
            }
        ),
    )
    if (
        raw["kind"] != "osworld_chrome"
        or raw["image_manifest_ref"] != "../osworld/image-manifest.json"
        or not isinstance(raw["image_manifest_sha256"], str)
        or _SHA256_PATTERN.fullmatch(raw["image_manifest_sha256"]) is None
        or raw["required_protocol_id"] != "osworld.chrome.v1"
    ):
        raise WebMallEnvironmentManifestError
    return WebMallBrowserRuntime(
        kind=raw["kind"],
        image_manifest_ref=raw["image_manifest_ref"],
        image_manifest_sha256=raw["image_manifest_sha256"],
        required_protocol_id=raw["required_protocol_id"],
    )


def _parse_service_images(value: object) -> WebMallServiceImages:
    """验证 WordPress 与 MariaDB 镜像均固定到 sha256 digest。

    输入参数：
        value：manifest ``service_images`` object。
    输出返回值：
        两个不可变 image 引用。
    """

    raw = _require_exact_fields(value, frozenset({"wordpress", "mariadb"}))
    wordpress = raw["wordpress"]
    mariadb = raw["mariadb"]
    _validate_digest_image(wordpress)
    _validate_digest_image(mariadb)
    return WebMallServiceImages(wordpress=wordpress, mariadb=mariadb)


def _validate_digest_image(value: object) -> None:
    """验证一个容器引用恰好包含单个小写 sha256 digest。

    输入参数：
        value：候选容器 image 字符串。
    输出返回值：
        无；固定引用正常返回。
    异常：
        WebMallEnvironmentManifestError：引用为空、可变或 digest 非法。
    """

    if not isinstance(value, str) or value.count("@sha256:") != 1:
        raise WebMallEnvironmentManifestError
    repository, digest = value.split("@sha256:", 1)
    if (
        not repository
        or any(character.isspace() for character in repository)
        or _SHA256_PATTERN.fullmatch(digest) is None
    ):
        raise WebMallEnvironmentManifestError


def _parse_software(value: object) -> WebMallSoftwareVersions:
    """验证 reference deployment 的应用版本与 HPOS 存储合同。

    输入参数：
        value：manifest ``software`` object。
    输出返回值：
        不可变软件版本集合。
    """

    raw = _require_exact_fields(
        value,
        frozenset({"wordpress_version", "woocommerce_version", "order_storage"}),
    )
    wordpress = raw["wordpress_version"]
    woocommerce = raw["woocommerce_version"]
    if (
        not isinstance(wordpress, str)
        or _VERSION_PATTERN.fullmatch(wordpress) is None
        or wordpress != "7.0.2"
        or not isinstance(woocommerce, str)
        or _VERSION_PATTERN.fullmatch(woocommerce) is None
        or woocommerce != "9.8.5"
        or raw["order_storage"] != "hpos"
    ):
        raise WebMallEnvironmentManifestError
    return WebMallSoftwareVersions(
        wordpress_version=wordpress,
        woocommerce_version=woocommerce,
        order_storage="hpos",
    )


def _parse_stores(value: object) -> tuple[WebMallStoreBinding, ...]:
    """验证四店顺序、binding 名与已核对信用卡 method ID 闭集。

    输入参数：
        value：manifest ``stores`` array。
    输出返回值：
        固定 store-1 至 store-4 顺序的不可变绑定元组。
    """

    if not isinstance(value, list) or len(value) != 4:
        raise WebMallEnvironmentManifestError
    stores: list[WebMallStoreBinding] = []
    for index, candidate in enumerate(value, start=1):
        raw = _require_exact_fields(
            candidate,
            frozenset(
                {
                    "logical_store_id",
                    "origin_env",
                    "reader_target_env",
                    "credit_card_payment_method_ids",
                }
            ),
        )
        store_id = f"store-{index}"
        origin_env = f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN"
        target_env = f"PARAGUIBENCH_WEBMALL_STORE_{index}_READER_TARGET"
        payment_ids = raw["credit_card_payment_method_ids"]
        if (
            raw["logical_store_id"] != store_id
            or raw["origin_env"] != origin_env
            or raw["reader_target_env"] != target_env
            or _ENV_NAME_PATTERN.fullmatch(origin_env) is None
            or _ENV_NAME_PATTERN.fullmatch(target_env) is None
            or not isinstance(payment_ids, list)
            or tuple(payment_ids) != _EXPECTED_PAYMENT_IDS[store_id]
            or any(
                not isinstance(item, str) or _PAYMENT_ID_PATTERN.fullmatch(item) is None
                for item in payment_ids
            )
        ):
            raise WebMallEnvironmentManifestError
        stores.append(
            WebMallStoreBinding(
                logical_store_id=store_id,
                origin_env=origin_env,
                reader_target_env=target_env,
                credit_card_payment_method_ids=tuple(payment_ids),
            )
        )
    if tuple(store.logical_store_id for store in stores) != WEBMALL_LOGICAL_STORE_IDS:
        raise WebMallEnvironmentManifestError
    return tuple(stores)


def _parse_order_reader(value: object) -> WebMallOrderReaderContract:
    """验证 WP-CLI reader 协议、脚本路径、摘要和资源上限。

    输入参数：
        value：manifest ``order_reader`` object。
    输出返回值：
        不可变 reader contract。
    """

    raw = _require_exact_fields(
        value,
        frozenset(
            {
                "protocol_id",
                "script_path",
                "script_sha256",
                "max_stdout_bytes",
                "timeout_seconds",
            }
        ),
    )
    if (
        raw["protocol_id"] != "paraguibench.webmall.wp-cli-orders.v2"
        or raw["script_path"] != "wp-order-evidence.php"
        or _SHA256_PATTERN.fullmatch(str(raw["script_sha256"])) is None
        or raw["max_stdout_bytes"] != _READER_STDOUT_BYTES
        or isinstance(raw["max_stdout_bytes"], bool)
        or not _bounded_integer(raw["timeout_seconds"], 1, 300)
    ):
        raise WebMallEnvironmentManifestError
    return WebMallOrderReaderContract(
        protocol_id=raw["protocol_id"],
        script_path=raw["script_path"],
        script_sha256=raw["script_sha256"],
        max_stdout_bytes=raw["max_stdout_bytes"],
        timeout_seconds=raw["timeout_seconds"],
    )


def _parse_cart_reader(value: object) -> WebMallCartReaderContract:
    """验证浏览器同会话 Cart Store API reader 的完整静态合同。

    输入参数：
        value：manifest ``cart_reader`` object。
    输出返回值：
        字段和值均精确匹配当前 v1 协议的不可变合同。
    异常：
        WebMallEnvironmentManifestError：reader 类型、端点、资源上限或
            reference live gate 状态发生漂移。
    """

    raw = _require_exact_fields(
        value,
        frozenset(
            {
                "protocol_id",
                "evidence_protocol_id",
                "reader_kind",
                "endpoint_path",
                "same_browser_context_required",
                "stability_read_count",
                "max_response_bytes",
                "max_items",
                "max_quantity",
                "timeout_seconds",
                "reference_live_validation_status",
            }
        ),
    )
    if (
        raw["protocol_id"] != "paraguibench.webmall.woocommerce-store-api-cart.v1"
        or raw["evidence_protocol_id"]
        != "paraguibench.webmall.cart-authoritative-state.v1"
        or raw["reader_kind"] != "woocommerce_store_api"
        or raw["endpoint_path"] != "/wp-json/wc/store/v1/cart"
        or raw["same_browser_context_required"] is not True
        or raw["stability_read_count"] != 2
        or isinstance(raw["stability_read_count"], bool)
        or raw["max_response_bytes"] != _CART_RESPONSE_BYTES
        or isinstance(raw["max_response_bytes"], bool)
        or raw["max_items"] != _CART_MAX_ITEMS
        or isinstance(raw["max_items"], bool)
        or raw["max_quantity"] != _CART_MAX_QUANTITY
        or isinstance(raw["max_quantity"], bool)
        or raw["timeout_seconds"] != 10
        or isinstance(raw["timeout_seconds"], bool)
        or raw["reference_live_validation_status"] not in {"pending", "live_validated"}
    ):
        raise WebMallEnvironmentManifestError
    return WebMallCartReaderContract(
        protocol_id=raw["protocol_id"],
        evidence_protocol_id=raw["evidence_protocol_id"],
        reader_kind=raw["reader_kind"],
        endpoint_path=raw["endpoint_path"],
        same_browser_context_required=raw["same_browser_context_required"],
        stability_read_count=raw["stability_read_count"],
        max_response_bytes=raw["max_response_bytes"],
        max_items=raw["max_items"],
        max_quantity=raw["max_quantity"],
        timeout_seconds=raw["timeout_seconds"],
        reference_live_validation_status=raw["reference_live_validation_status"],
    )


def _parse_lease(value: object) -> WebMallLeaseContract:
    """验证跨 host 租约只引用环境变量且固定 namespace/TTL。

    输入参数：
        value：manifest ``lease`` object。
    输出返回值：
        不可变 lease contract。
    """

    raw = _require_exact_fields(
        value,
        frozenset(
            {
                "protocol_id",
                "coordinator_url_env",
                "credential_env",
                "namespace",
                "ttl_seconds",
            }
        ),
    )
    if (
        raw["protocol_id"] != "paraguibench.webmall.distributed-lease.v1"
        or raw["coordinator_url_env"] != "PARAGUIBENCH_WEBMALL_LEASE_COORDINATOR_URL"
        or raw["credential_env"] != "PARAGUIBENCH_WEBMALL_LEASE_TOKEN"
        or raw["namespace"] != "paraguibench-reference-four-stores"
        or _ENV_NAME_PATTERN.fullmatch(raw["coordinator_url_env"]) is None
        or _ENV_NAME_PATTERN.fullmatch(raw["credential_env"]) is None
        or not _bounded_integer(raw["ttl_seconds"], 60, 86_400)
    ):
        raise WebMallEnvironmentManifestError
    return WebMallLeaseContract(
        protocol_id=raw["protocol_id"],
        coordinator_url_env=raw["coordinator_url_env"],
        credential_env=raw["credential_env"],
        namespace=raw["namespace"],
        ttl_seconds=raw["ttl_seconds"],
    )


def _parse_reset(value: object) -> WebMallResetContract:
    """验证 WebMall 使用 Attempt 订单 baseline/delta 而非隐式破坏性 reset。

    输入参数：
        value：manifest ``reset`` object。
    输出返回值：
        不可变 reset contract。
    """

    raw = _require_exact_fields(value, frozenset({"protocol_id", "strategy"}))
    if (
        raw["protocol_id"] != "paraguibench.webmall.baseline-delta.v1"
        or raw["strategy"] != "attempt_order_baseline_delta"
    ):
        raise WebMallEnvironmentManifestError
    return WebMallResetContract(
        protocol_id=raw["protocol_id"],
        strategy=raw["strategy"],
    )


def _bounded_integer(value: object, minimum: int, maximum: int) -> bool:
    """判断候选值是排除 bool 且位于闭区间的整数。

    输入参数：
        value：待检查 JSON 值。
        minimum/maximum：允许的闭区间边界。
    输出返回值：
        类型与范围均合法时返回 ``True``。
    """

    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _verify_reader_script(
    *,
    manifest_path: Path,
    contract: WebMallOrderReaderContract,
) -> None:
    """nofollow 读取同目录 reader 脚本并验证大小与 SHA-256。

    输入参数：
        manifest_path：当前 manifest 文件位置。
        contract：已经验证路径分量和摘要格式的 reader contract。
    输出返回值：
        无；脚本字节与 manifest 完全一致时返回。
    异常：
        WebMallEnvironmentManifestError：路径、文件类型、链接数、大小或摘要
            不匹配。
    """

    parsed = PurePosixPath(contract.script_path)
    if (
        parsed.is_absolute()
        or len(parsed.parts) != 1
        or parsed.name != contract.script_path
        or "\\" in contract.script_path
    ):
        raise WebMallEnvironmentManifestError
    path = manifest_path.parent / contract.script_path
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise WebMallEnvironmentManifestError from None
    digest = hashlib.sha256()
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_READER_SCRIPT_BYTES
        ):
            raise WebMallEnvironmentManifestError
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise WebMallEnvironmentManifestError
            digest.update(chunk)
            remaining -= len(chunk)
    except (OSError, WebMallEnvironmentManifestError):
        raise WebMallEnvironmentManifestError from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    if digest.hexdigest() != contract.script_sha256:
        raise WebMallEnvironmentManifestError


def _verify_browser_manifest(
    *,
    manifest_path: Path,
    contract: WebMallBrowserRuntime,
) -> None:
    """验证 WebMall browser 引用的 OSWorld manifest 固定字节摘要。

    输入参数：
        manifest_path：WebMall 环境 manifest 文件位置。
        contract：包含固定相对引用与预期 SHA-256 的浏览器合同。
    输出返回值：
        无；引用文件是同一 environments 根中的普通文件且摘要匹配时返回。
    异常：
        WebMallEnvironmentManifestError：引用越界、路径含 symlink、目标不是
            普通文件或摘要不一致。
    """

    environments_root = manifest_path.parent.parent.resolve()
    candidate = manifest_path.parent / contract.image_manifest_ref
    try:
        relative = candidate.absolute().relative_to(environments_root)
    except ValueError:
        raise WebMallEnvironmentManifestError from None
    current = environments_root
    try:
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise WebMallEnvironmentManifestError
        metadata = current.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise WebMallEnvironmentManifestError
        digest = hashlib.sha256(current.read_bytes()).hexdigest()
    except (OSError, WebMallEnvironmentManifestError):
        raise WebMallEnvironmentManifestError from None
    if digest != contract.image_manifest_sha256:
        raise WebMallEnvironmentManifestError
