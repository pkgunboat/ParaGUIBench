"""从 OSWorld Chrome 同一 BrowserContext 读取 WebMall 购物车终态。"""

from __future__ import annotations

from collections.abc import Callable
from collections import Counter
import ipaddress
import json
import re
import time
from typing import Any
from urllib.request import ProxyHandler, build_opener
from urllib.parse import urlsplit

from paraguibench.integrations.webmall.cart_contracts import (
    ObservedCartItem,
    ObservedCartStore,
)
from paraguibench.integrations.webmall.cart_evidence import (
    CART_EVIDENCE_SOURCE_PROTOCOL_ID,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceCaptureProof,
)
from paraguibench.integrations.webmall.environment_manifest import (
    WebMallCartReaderContract,
)
from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
    WebMallEvidenceContractError,
    contains_control,
    normalize_product_slug,
)
from paraguibench.integrations.webmall.registry import (
    WebMallURLRegistry,
    WebMallURLRegistryError,
)


_WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_CDP_VERSION_RESPONSE_MAX_BYTES = 65_536
_CART_READER_PROTOCOL_ID = "paraguibench.webmall.woocommerce-store-api-cart.v1"
_CART_READER_KIND = "woocommerce_store_api"
_CART_ENDPOINT_PATH = "/wp-json/wc/store/v1/cart"
_CART_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CART_MAX_ITEMS = 1024
_CART_MAX_QUANTITY = 10_000
_CART_TIMEOUT_SECONDS = 10
_CART_LIVE_VALIDATION_STATES = frozenset({"pending", "live_validated"})
CartSnapshotLoader = Callable[
    [str, WebMallURLRegistry, WebMallCartReaderContract],
    tuple[ObservedCartStore, ...],
]
CDPReadyProbe = Callable[[str, float], None]
PlaywrightFactory = Callable[[], Any]
_CART_FETCH_SCRIPT = r"""
async (options) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeout_ms);
  try {
    const response = await fetch(options.endpoint_path, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    if (!response.ok || response.redirected || response.status !== 200) {
      throw new Error("cart_response_invalid");
    }
    const contentLength = response.headers.get("content-length");
    if (
      contentLength !== null &&
      (!/^[0-9]+$/.test(contentLength) ||
        Number(contentLength) > options.max_response_bytes)
    ) {
      throw new Error("cart_response_limit_invalid");
    }
    if (response.body === null) {
      throw new Error("cart_response_body_missing");
    }
    const reader = response.body.getReader();
    const chunks = [];
    let total = 0;
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      total += result.value.byteLength;
      if (total > options.max_response_bytes) {
        controller.abort();
        throw new Error("cart_response_limit_exceeded");
      }
      chunks.push(result.value);
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    const body = new TextDecoder("utf-8", {fatal: true}).decode(bytes);
    return {
      status: "success",
      http_status: response.status,
      response_url: response.url,
      content_type: response.headers.get("content-type") || "",
      body,
    };
  } finally {
    clearTimeout(timer);
  }
}
"""


class WebMallBrowserCartSourceError(RuntimeError):
    """表示浏览器 Cart reader 的配置、准备或完整读取失败。"""

    code = "WEBMALL_BROWSER_CART_SOURCE_INVALID"

    def __init__(self) -> None:
        """构造不回显 worker、端点、URL、slug 或底层异常的错误。

        输入参数：无。
        输出返回值：无；异常公开文本固定为稳定 code。
        """

        super().__init__(self.code)


class WebMallBrowserCartSource:
    """为当前单 worker 原子捕获并缓存固定四店 Cart 终态。"""

    evidence_protocol_id = CART_EVIDENCE_SOURCE_PROTOCOL_ID

    def __init__(
        self,
        *,
        registry: WebMallURLRegistry,
        cart_reader: WebMallCartReaderContract,
        worker_id: str,
        host: str,
        chromium_port: int,
        snapshot_loader: CartSnapshotLoader | None = None,
        cdp_ready_probe: CDPReadyProbe | None = None,
        playwright_factory: PlaywrightFactory | None = None,
    ) -> None:
        """绑定单 worker、loopback CDP、四店 registry 与浏览器边界。

        输入参数：
            registry：本 Attempt 固定的 logical/runtime 四店注册表。
            cart_reader：manifest 已验证的 Store API reader 合同。
            worker_id：仅在受信内存中使用的单浏览器会话身份。
            host/chromium_port：当前 owned container 的 loopback CDP 入口。
            snapshot_loader：一次读取同一 BrowserContext 四店的边界函数。
            cdp_ready_probe：guest bridge 启动后的 host-side 就绪探针。
            playwright_factory：可选 Playwright 系统边界工厂；仅测试注入，
                production 省略时使用 ``sync_playwright``。
        输出返回值：
            无；构造阶段不启动 Chrome、不访问商店或 CDP。
        异常：
            WebMallBrowserCartSourceError：任一绑定或 callable 无效。
        """

        try:
            if not isinstance(registry, WebMallURLRegistry):
                raise TypeError
            if registry.logical_store_ids != WEBMALL_LOGICAL_STORE_IDS:
                raise TypeError
            if not _is_supported_cart_reader_contract(cart_reader):
                raise TypeError
            if (
                not isinstance(worker_id, str)
                or _WORKER_ID_PATTERN.fullmatch(worker_id) is None
            ):
                raise TypeError
            endpoint = _loopback_cdp_endpoint(host, chromium_port)
            resolved_cdp_probe = (
                _wait_for_cdp_ready if cdp_ready_probe is None else cdp_ready_probe
            )
            if (
                snapshot_loader is not None
                and playwright_factory is not None
                or snapshot_loader is not None
                and not callable(snapshot_loader)
                or playwright_factory is not None
                and not callable(playwright_factory)
                or not callable(resolved_cdp_probe)
            ):
                raise TypeError
        except Exception:
            raise WebMallBrowserCartSourceError from None
        self._registry = registry
        self._cart_reader = cart_reader
        self._worker_id = worker_id
        self._endpoint = endpoint
        self._snapshot_loader = snapshot_loader
        self._capture_session = (
            None
            if snapshot_loader is not None
            else _PlaywrightCartCaptureSession(
                endpoint=endpoint,
                registry=registry,
                contract=cart_reader,
                playwright_factory=playwright_factory,
            )
        )
        self._cdp_ready_probe = resolved_cdp_probe
        self._prepared = False
        self._closed = False
        self._stores_by_id: dict[str, ObservedCartStore] | None = None
        self._reference_proof: WebMallCartReferenceCaptureProof | None = None

    def __repr__(self) -> str:
        """返回不含 worker、端点、origin 或购物车内容的公开表示。

        输入参数：无。
        输出返回值：固定类名与生命周期状态。
        """

        return (
            "WebMallBrowserCartSource("
            f"prepared={self._prepared!r}, closed={self._closed!r})"
        )

    def prepare(self, controller: Any) -> None:
        """在 Agent 开始前启动 Chrome CDP 与 guest 9222 转发并探活。

        输入参数：
            controller：必须提供 shell-free ``launch`` 和
                ``wait_for_chrome_cdp`` 的当前 owned VM controller。
        输出返回值：
            无；成功后才允许读取 Cart 终态。
        异常：
            WebMallBrowserCartSourceError：重复生命周期、接口或任一步失败。
        """

        if self._prepared or self._closed:
            raise WebMallBrowserCartSourceError
        launch = getattr(controller, "launch", None)
        wait_for_chrome_cdp = getattr(
            controller,
            "wait_for_chrome_cdp",
            None,
        )
        if not callable(launch) or not callable(wait_for_chrome_cdp):
            raise WebMallBrowserCartSourceError
        try:
            launch(["google-chrome", "--remote-debugging-port=1337"])
            wait_for_chrome_cdp(port=1337, timeout=15.0)
            launch(
                [
                    "socat",
                    "tcp-listen:9222,fork,reuseaddr",
                    "tcp:localhost:1337",
                ]
            )
            self._cdp_ready_probe(self._endpoint, 15.0)
            if self._capture_session is not None:
                self._capture_session.prepare()
        except Exception:
            raise WebMallBrowserCartSourceError from None
        self._prepared = True

    def read_cart(
        self,
        worker_id: str,
        logical_store_id: str,
    ) -> ObservedCartStore:
        """从首次原子四店快照返回指定 store 的完整 Cart 终态。

        输入参数：
            worker_id：必须等于构造时绑定的唯一浏览器会话身份。
            logical_store_id：固定四店 universe 中的店铺身份。
        输出返回值：
            完整且不可变的 ``ObservedCartStore``；后续读取复用首次快照。
        异常：
            WebMallBrowserCartSourceError：生命周期、身份、浏览器读取或
                四店 coverage 不可靠。
        """

        if (
            not self._prepared
            or self._closed
            or worker_id != self._worker_id
            or logical_store_id not in WEBMALL_LOGICAL_STORE_IDS
        ):
            raise WebMallBrowserCartSourceError
        if self._stores_by_id is None:
            self._stores_by_id = self._capture_complete_store_universe()
        return self._stores_by_id[logical_store_id]

    def close(self) -> None:
        """关闭 source 的逻辑生命周期且不关闭 Agent Chrome。

        输入参数：无。
        输出返回值：无；重复关闭幂等，缓存仅在当前对象内释放。
        """

        if self._closed:
            return
        cleanup_failed = False
        if self._capture_session is not None:
            try:
                self._capture_session.close()
            except Exception:
                cleanup_failed = True
        self._stores_by_id = None
        self._reference_proof = None
        self._closed = True
        self._prepared = False
        if cleanup_failed:
            raise WebMallBrowserCartSourceError from None

    def reference_validation_proof(self) -> WebMallCartReferenceCaptureProof:
        """返回 production 同 context 双 sweep 完成后的脱敏事实。

        输入参数：无。
        输出返回值：不含 worker、Cart、URL 或 origin 的不可变证明。
        异常：WebMallBrowserCartSourceError：尚未完成 production 捕获、已关闭，
            或使用了不具备 BrowserContext 连续性证明的测试 snapshot seam。
        """

        if not self._prepared or self._closed or self._reference_proof is None:
            raise WebMallBrowserCartSourceError
        return self._reference_proof

    def _capture_complete_store_universe(
        self,
    ) -> dict[str, ObservedCartStore]:
        """调用浏览器边界一次并验证返回值精确覆盖固定四店。

        输入参数：无；使用构造时冻结的 endpoint、registry 与合同。
        输出返回值：logical store ID 到完整 observation 的内部索引。
        异常：WebMallBrowserCartSourceError：类型、顺序或完整性无效。
        """

        try:
            if self._capture_session is not None:
                stores = self._capture_session.capture()
            else:
                if self._snapshot_loader is None:
                    raise TypeError
                stores = self._snapshot_loader(
                    self._endpoint,
                    self._registry,
                    self._cart_reader,
                )
        except Exception:
            raise WebMallBrowserCartSourceError from None
        if (
            not isinstance(stores, tuple)
            or tuple(store.logical_store_id for store in stores)
            != WEBMALL_LOGICAL_STORE_IDS
            or any(
                not isinstance(store, ObservedCartStore) or not store.complete
                for store in stores
            )
        ):
            raise WebMallBrowserCartSourceError
        if self._capture_session is not None:
            sweep = WEBMALL_LOGICAL_STORE_IDS
            self._reference_proof = WebMallCartReferenceCaptureProof(
                browser_context_continuity_verified=True,
                sweep_store_ids=(sweep, sweep),
                normalized_universe_match=True,
            )
        return {store.logical_store_id: store for store in stores}


def capture_webmall_cart_stores_with_playwright(
    endpoint: str,
    registry: WebMallURLRegistry,
    contract: WebMallCartReaderContract,
    *,
    playwright_factory: PlaywrightFactory | None = None,
) -> tuple[ObservedCartStore, ...]:
    """在 Agent 的唯一 BrowserContext 中对固定四店执行稳定双读。

    输入参数：
        endpoint：当前 owned container 的 loopback CDP HTTP endpoint。
        registry：本 Attempt 固定的四店 logical/runtime URL 注册表。
        contract：manifest 明确选择的 Store API reader 与资源上限。
        playwright_factory：可选 Playwright 系统边界工厂；测试可注入 fake。
    输出返回值：
        按固定 store-1 至 store-4 顺序、每店 ``complete=True`` 的不可变
        observation 元组；同 slug 多行已按数量聚合。
    异常：
        WebMallBrowserCartSourceError：CDP、context、导航、API、JSON、
            schema、商品身份、双读一致性或清理无法可靠完成。
    安全边界：
        函数只关闭自己创建的 evaluator page 与 Playwright 客户端，绝不调用
        ``browser.close()``；错误不回显 endpoint、URL、slug、cookie 或 body。
    """

    session = _PlaywrightCartCaptureSession(
        endpoint=endpoint,
        registry=registry,
        contract=contract,
        playwright_factory=playwright_factory,
    )
    try:
        session.prepare()
        return session.capture()
    finally:
        session.close()


class _PlaywrightCartCaptureSession:
    """跨 Agent 生命周期保持同一 Playwright BrowserContext 的内部会话。"""

    def __init__(
        self,
        *,
        endpoint: str,
        registry: WebMallURLRegistry,
        contract: WebMallCartReaderContract,
        playwright_factory: PlaywrightFactory | None,
    ) -> None:
        """冻结 CDP、四店、reader 合同与惰性 Playwright 工厂。

        输入参数：endpoint/registry/contract 为已固定 production binding；
            playwright_factory 是可选系统边界工厂。
        输出返回值：无；构造阶段不导入 Playwright、不连接 CDP。
        异常：WebMallBrowserCartSourceError：任一绑定或工厂无效。
        """

        _validate_playwright_capture_binding(endpoint, registry, contract)
        if playwright_factory is not None and not callable(playwright_factory):
            raise WebMallBrowserCartSourceError
        self._endpoint = endpoint
        self._registry = registry
        self._contract = contract
        self._playwright_factory = playwright_factory
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._prepared = False
        self._closed = False

    def prepare(self) -> None:
        """在 Agent 前连接 CDP 并冻结唯一 BrowserContext 对象身份。

        输入参数：无。
        输出返回值：无；成功后同一会话保持到 ``capture``/``close``。
        异常：WebMallBrowserCartSourceError：连接、数量或 context 无效。
        """

        if self._prepared or self._closed:
            raise WebMallBrowserCartSourceError
        try:
            factory = self._resolve_playwright_factory()
            manager = factory()
            start = getattr(manager, "start", None)
            if not callable(start):
                raise TypeError
            playwright = start()
            self._playwright = playwright
            chromium = getattr(playwright, "chromium", None)
            connect = getattr(chromium, "connect_over_cdp", None)
            if not callable(connect):
                raise TypeError
            browser = connect(
                self._endpoint,
                timeout=self._contract.timeout_seconds * 1000,
            )
            contexts = tuple(getattr(browser, "contexts", ()))
            if len(contexts) != 1:
                raise TypeError
            context = contexts[0]
        except Exception:
            self._close_playwright_client()
            raise WebMallBrowserCartSourceError from None
        self._browser = browser
        self._context = context
        self._prepared = True

    def capture(self) -> tuple[ObservedCartStore, ...]:
        """从 prepare 冻结的同一 context 执行两次完整四店 sweep。

        输入参数：无。
        输出返回值：固定四店顺序的完整不可变 observation。
        异常：WebMallBrowserCartSourceError：context 被替换或任一读取无效。
        """

        if (
            not self._prepared
            or self._closed
            or self._browser is None
            or self._context is None
        ):
            raise WebMallBrowserCartSourceError
        contexts = tuple(getattr(self._browser, "contexts", ()))
        if len(contexts) != 1 or contexts[0] is not self._context:
            raise WebMallBrowserCartSourceError
        stores = _capture_cart_stores_from_context(
            context=self._context,
            registry=self._registry,
            contract=self._contract,
        )
        contexts = tuple(getattr(self._browser, "contexts", ()))
        if len(contexts) != 1 or contexts[0] is not self._context:
            raise WebMallBrowserCartSourceError
        return stores

    def close(self) -> None:
        """断开 evaluator Playwright 客户端且绝不关闭 Agent Chrome。

        输入参数：无。
        输出返回值：无；重复调用幂等。
        异常：WebMallBrowserCartSourceError：客户端清理接口缺失或失败。
        """

        if self._closed:
            return
        failed = self._close_playwright_client()
        self._browser = None
        self._context = None
        self._prepared = False
        self._closed = True
        if failed:
            raise WebMallBrowserCartSourceError from None

    def _resolve_playwright_factory(self) -> PlaywrightFactory:
        """惰性解析 production Playwright 工厂。

        输入参数：无。
        输出返回值：注入工厂或 ``sync_playwright``。
        异常：WebMallBrowserCartSourceError：production 依赖不可用。
        """

        if self._playwright_factory is not None:
            return self._playwright_factory
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise WebMallBrowserCartSourceError from None
        return sync_playwright

    def _close_playwright_client(self) -> bool:
        """有界停止当前 evaluator 客户端而不调用 browser.close。

        输入参数：无。
        输出返回值：清理接口缺失或调用失败时为 ``True``。
        """

        if self._playwright is None:
            return False
        playwright = self._playwright
        self._playwright = None
        stop = getattr(playwright, "stop", None)
        if not callable(stop):
            return True
        try:
            stop()
        except Exception:
            return True
        return False


def _capture_cart_stores_from_context(
    *,
    context: Any,
    registry: WebMallURLRegistry,
    contract: WebMallCartReaderContract,
) -> tuple[ObservedCartStore, ...]:
    """在已冻结 context 内以两次完整 sweep 读取标准化四店 universe。

    输入参数：context 为 prepare 阶段冻结对象；registry/contract 为固定合同。
    输出返回值：按固定四店顺序的完整 Cart observation。
    异常：WebMallBrowserCartSourceError：页面、导航、API 或 sweep 不一致。
    """

    new_page = getattr(context, "new_page", None)
    if not callable(new_page):
        raise WebMallBrowserCartSourceError
    page: Any | None = None
    cleanup_failed = False
    try:
        page = new_page()
        normalized_sweeps: list[tuple[tuple[tuple[str, int], ...], ...]] = []
        for _sweep_index in range(contract.stability_read_count):
            normalized_stores: list[tuple[tuple[str, int], ...]] = []
            for logical_store_id in WEBMALL_LOGICAL_STORE_IDS:
                cart_url = registry.materialize_url(
                    f"webmall://{logical_store_id}/cart/"
                )
                api_url = registry.materialize_url(
                    f"webmall://{logical_store_id}{contract.endpoint_path}"
                )
                navigation = page.goto(
                    cart_url,
                    wait_until="load",
                    timeout=contract.timeout_seconds * 1000,
                )
                if (
                    navigation is None
                    or getattr(navigation, "status", None) != 200
                    or getattr(navigation, "url", None) != cart_url
                    or getattr(page, "url", None) != cart_url
                ):
                    raise WebMallBrowserCartSourceError
                normalized_stores.append(
                    _read_and_normalize_cart_store_api(
                        page=page,
                        registry=registry,
                        contract=contract,
                        logical_store_id=logical_store_id,
                        expected_api_url=api_url,
                    )
                )
            normalized_sweeps.append(tuple(normalized_stores))
        if any(current != normalized_sweeps[0] for current in normalized_sweeps[1:]):
            raise WebMallBrowserCartSourceError
        return tuple(
            ObservedCartStore(
                logical_store_id=logical_store_id,
                complete=True,
                items=tuple(
                    ObservedCartItem(slug, quantity)
                    for slug, quantity in normalized_sweeps[0][store_index]
                ),
            )
            for store_index, logical_store_id in enumerate(WEBMALL_LOGICAL_STORE_IDS)
        )
    except WebMallBrowserCartSourceError:
        raise
    except Exception:
        raise WebMallBrowserCartSourceError from None
    finally:
        if page is not None:
            close_page = getattr(page, "close", None)
            if callable(close_page):
                try:
                    close_page()
                except Exception:
                    cleanup_failed = True
            else:
                cleanup_failed = True
        if cleanup_failed:
            raise WebMallBrowserCartSourceError from None


def _read_and_normalize_cart_store_api(
    *,
    page: Any,
    registry: WebMallURLRegistry,
    contract: WebMallCartReaderContract,
    logical_store_id: str,
    expected_api_url: str,
) -> tuple[tuple[str, int], ...]:
    """读取一次 Store API，并形成按 canonical slug 聚合的稳定多集合。

    输入参数：
        page：同一 BrowserContext 内的 evaluator page。
        registry/contract：固定四店与 reader 合同。
        logical_store_id/expected_api_url：当前店身份和精确 API URL。
    输出返回值：
        按 slug 编码排序的 ``(canonical_slug, total_quantity)`` 元组。
    异常：WebMallBrowserCartSourceError：envelope、JSON 或 item schema 无效。
    """

    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        raise WebMallBrowserCartSourceError
    try:
        envelope = evaluate(
            _CART_FETCH_SCRIPT,
            {
                "endpoint_path": contract.endpoint_path,
                "max_response_bytes": contract.max_response_bytes,
                "timeout_ms": contract.timeout_seconds * 1000,
            },
        )
    except Exception:
        raise WebMallBrowserCartSourceError from None
    if (
        not isinstance(envelope, dict)
        or set(envelope)
        != {
            "status",
            "http_status",
            "response_url",
            "content_type",
            "body",
        }
        or envelope.get("status") != "success"
        or envelope.get("http_status") != 200
        or envelope.get("response_url") != expected_api_url
        or not isinstance(envelope.get("content_type"), str)
        or str(envelope["content_type"]).split(";", 1)[0].strip().lower()
        != "application/json"
        or not isinstance(envelope.get("body"), str)
    ):
        raise WebMallBrowserCartSourceError
    body = envelope["body"]
    try:
        encoded = body.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise WebMallBrowserCartSourceError from None
    if len(encoded) > contract.max_response_bytes:
        raise WebMallBrowserCartSourceError
    try:
        payload = json.loads(body, object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise WebMallBrowserCartSourceError from None
    return _normalize_cart_payload(
        payload,
        registry=registry,
        contract=contract,
        logical_store_id=logical_store_id,
    )


def _normalize_cart_payload(
    payload: object,
    *,
    registry: WebMallURLRegistry,
    contract: WebMallCartReaderContract,
    logical_store_id: str,
) -> tuple[tuple[str, int], ...]:
    """验证 Store API items 闭集并聚合 canonical slug 数量。

    输入参数：payload 为严格 JSON object；其余参数固定 store 与资源合同。
    输出返回值：排序后的 canonical slug/数量闭集。
    异常：WebMallBrowserCartSourceError：计数、item、permalink 或数量无效。
    """

    if not isinstance(payload, dict):
        raise WebMallBrowserCartSourceError
    items = payload.get("items")
    items_count = payload.get("items_count")
    if (
        not isinstance(items, list)
        or len(items) > contract.max_items
        or not isinstance(items_count, int)
        or isinstance(items_count, bool)
        or items_count < 0
        or items_count > contract.max_items * contract.max_quantity
    ):
        raise WebMallBrowserCartSourceError

    line_keys: set[str] = set()
    quantities: Counter[str] = Counter()
    raw_quantity_sum = 0
    for item in items:
        if not isinstance(item, dict):
            raise WebMallBrowserCartSourceError
        product_id = item.get("id")
        line_key = item.get("key")
        quantity = item.get("quantity")
        permalink = item.get("permalink")
        if (
            not isinstance(product_id, int)
            or isinstance(product_id, bool)
            or product_id < 1
            or not isinstance(line_key, str)
            or not line_key
            or len(line_key) > 1024
            or contains_control(line_key)
            or line_key in line_keys
            or not isinstance(quantity, int)
            or isinstance(quantity, bool)
            or not 1 <= quantity <= contract.max_quantity
            or not isinstance(permalink, str)
        ):
            raise WebMallBrowserCartSourceError
        line_keys.add(line_key)
        slug = _canonical_slug_from_permalink(
            permalink,
            registry=registry,
            logical_store_id=logical_store_id,
        )
        quantities[slug] += quantity
        if quantities[slug] > contract.max_quantity:
            raise WebMallBrowserCartSourceError
        raw_quantity_sum += quantity
    if raw_quantity_sum != items_count:
        raise WebMallBrowserCartSourceError
    return tuple(sorted(quantities.items(), key=lambda pair: pair[0]))


def _canonical_slug_from_permalink(
    permalink: str,
    *,
    registry: WebMallURLRegistry,
    logical_store_id: str,
) -> str:
    """把 Store API permalink 严格收敛为当前 logical store 的单层 slug。

    输入参数：permalink 为 API 商品 URL；registry/store 固定部署身份。
    输出返回值：严格 percent/UTF-8/NFC 规范化的 canonical slug。
    异常：WebMallBrowserCartSourceError：origin、路径、查询或编码无效。
    """

    try:
        logical_url = registry.canonicalize_url(permalink)
        parts = urlsplit(logical_url)
        path_parts = parts.path.split("/")
        if (
            parts.scheme != "webmall"
            or parts.netloc != logical_store_id
            or parts.query
            or parts.fragment
            or len(path_parts) not in {3, 4}
            or path_parts[:2] != ["", "product"]
            or not path_parts[2]
            or (len(path_parts) == 4 and path_parts[3] != "")
        ):
            raise ValueError
        return normalize_product_slug(path_parts[2])
    except (
        ValueError,
        WebMallEvidenceContractError,
        WebMallURLRegistryError,
    ):
        raise WebMallBrowserCartSourceError from None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """把 JSON object pairs 转为字典并拒绝重复 key。

    输入参数：pairs 为 ``json.loads`` 保序提供的键值对。
    输出返回值：无重复字符串 key 的普通字典。
    异常：ValueError：任一 key 非字符串或重复。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_playwright_capture_binding(
    endpoint: object,
    registry: object,
    contract: object,
) -> None:
    """在 Playwright 启动前验证 CDP、四店 registry 与 reader 合同。

    输入参数：endpoint、registry、contract 为生产 capture 的公开入参。
    输出返回值：无；完整安全绑定正常返回。
    异常：WebMallBrowserCartSourceError：任一绑定无效。
    """

    try:
        if not isinstance(endpoint, str):
            raise ValueError
        parts = urlsplit(endpoint)
        if (
            parts.scheme != "http"
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.hostname is None
            or not ipaddress.ip_address(parts.hostname).is_loopback
            or parts.port is None
            or not 1024 <= parts.port <= 65535
        ):
            raise ValueError
        if (
            not isinstance(registry, WebMallURLRegistry)
            or registry.logical_store_ids != WEBMALL_LOGICAL_STORE_IDS
            or not _is_supported_cart_reader_contract(contract)
        ):
            raise ValueError
    except Exception:
        raise WebMallBrowserCartSourceError from None


def _is_supported_cart_reader_contract(value: object) -> bool:
    """验证 Cart source 只执行当前版本冻结的严格 reader 合同。

    输入参数：value 为 manifest parser 产出或测试构造的合同候选。
    输出返回值：协议、端点、上下文约束和全部资源上限精确受支持时为真；
        live gate 只接受 ``pending`` 或经 114 探针确认后的
        ``live_validated`` 两个显式状态。
    """

    return (
        isinstance(value, WebMallCartReaderContract)
        and value.protocol_id == _CART_READER_PROTOCOL_ID
        and value.evidence_protocol_id == CART_EVIDENCE_SOURCE_PROTOCOL_ID
        and value.reader_kind == _CART_READER_KIND
        and value.endpoint_path == _CART_ENDPOINT_PATH
        and value.same_browser_context_required is True
        and value.stability_read_count == 2
        and not isinstance(value.stability_read_count, bool)
        and value.max_response_bytes == _CART_MAX_RESPONSE_BYTES
        and not isinstance(value.max_response_bytes, bool)
        and value.max_items == _CART_MAX_ITEMS
        and not isinstance(value.max_items, bool)
        and value.max_quantity == _CART_MAX_QUANTITY
        and not isinstance(value.max_quantity, bool)
        and value.timeout_seconds == _CART_TIMEOUT_SECONDS
        and not isinstance(value.timeout_seconds, bool)
        and value.reference_live_validation_status in _CART_LIVE_VALIDATION_STATES
    )


def _loopback_cdp_endpoint(host: object, port: object) -> str:
    """把数值 loopback host 与非特权端口格式化为 CDP endpoint。

    输入参数：host 为 IPv4/IPv6 数值地址；port 为 1024–65535 整数。
    输出返回值：不含凭据、路径或查询的 HTTP endpoint。
    异常：ValueError：host 非数值 loopback 或端口非法。
    """

    if (
        not isinstance(host, str)
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 1024 <= port <= 65535
    ):
        raise ValueError("CDP binding invalid")
    parsed = ipaddress.ip_address(host)
    if not parsed.is_loopback:
        raise ValueError("CDP binding invalid")
    rendered_host = f"[{parsed.compressed}]" if parsed.version == 6 else str(parsed)
    return f"http://{rendered_host}:{port}"


def _wait_for_cdp_ready(endpoint: str, timeout: float) -> None:
    """通过 no-proxy、有界 ``/json/version`` 轮询 host CDP bridge。

    输入参数：
        endpoint：已验证的 loopback CDP HTTP endpoint。
        timeout：总等待秒数，必须位于 0–120 秒。
    输出返回值：
        返回 ``Browser`` 或 ``webSocketDebuggerUrl`` 字段后无返回值结束。
    异常：
        WebMallBrowserCartSourceError：参数无效、响应超限/无效或期限内未就绪。
    """

    if (
        not isinstance(endpoint, str)
        or not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < float(timeout) <= 120
    ):
        raise WebMallBrowserCartSourceError
    _validate_playwright_capture_binding_endpoint(endpoint)
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + float(timeout)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebMallBrowserCartSourceError
        response: Any | None = None
        try:
            response = opener.open(
                endpoint.rstrip("/") + "/json/version",
                timeout=min(1.0, remaining),
            )
            raw = response.read(_CDP_VERSION_RESPONSE_MAX_BYTES + 1)
            if len(raw) > _CDP_VERSION_RESPONSE_MAX_BYTES:
                raise ValueError
            payload = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_unique_json_object,
            )
            if isinstance(payload, dict) and (
                isinstance(payload.get("Browser"), str)
                and bool(payload.get("Browser"))
                or isinstance(payload.get("webSocketDebuggerUrl"), str)
                and bool(payload.get("webSocketDebuggerUrl"))
            ):
                return
        except Exception:
            pass
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebMallBrowserCartSourceError
        time.sleep(min(0.25, remaining))


def _validate_playwright_capture_binding_endpoint(endpoint: str) -> None:
    """验证单独用于 CDP probe 的 endpoint 是纯 loopback HTTP origin。

    输入参数：endpoint 为待探测的 URL 字符串。
    输出返回值：无；安全 endpoint 正常返回。
    异常：WebMallBrowserCartSourceError：URL 含远程 host、凭据或附加部分。
    """

    try:
        parts = urlsplit(endpoint)
        if (
            parts.scheme != "http"
            or parts.username is not None
            or parts.password is not None
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
            or parts.hostname is None
            or not ipaddress.ip_address(parts.hostname).is_loopback
            or parts.port is None
            or not 1024 <= parts.port <= 65535
        ):
            raise ValueError
    except Exception:
        raise WebMallBrowserCartSourceError from None
