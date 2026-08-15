"""WebMall 同一浏览器会话 Cart Store API 生产 source 测试。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from paraguibench.integrations.webmall.cart_contracts import (
    ObservedCartItem,
    ObservedCartStore,
)
from paraguibench.integrations.webmall.cart_evidence import (
    capture_webmall_cart_observation,
)
from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest,
)
from paraguibench.integrations.webmall.browser_cart_source import (
    WebMallBrowserCartSource,
    WebMallBrowserCartSourceError,
    capture_webmall_cart_stores_with_playwright,
)
from paraguibench.integrations.webmall.registry import WebMallURLRegistry


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = load_webmall_environment_manifest(
    _REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"
)
_REGISTRY = WebMallURLRegistry(
    {
        f"store-{index}": f"https://store-{index}.example.invalid"
        for index in range(1, 5)
    }
)


class _PayloadPlaywrightBoundary:
    """以逐店逐次 payload 驱动 public Playwright reader 的系统边界 fake。"""

    def __init__(
        self,
        payloads: dict[str, tuple[dict[str, object], ...]],
    ) -> None:
        """保存每店按读取次数返回的 Cart API JSON object。

        输入参数：payloads 为 logical store 到有序 API object 元组的映射。
        输出返回值：无；初始化读取索引和清理状态。
        """

        self.payloads = payloads
        self.read_indexes = {store_id: 0 for store_id in payloads}
        self.current_store_id = ""
        self.current_cart_url = ""
        self.visited_store_ids: list[str] = []
        self.page_closed = False
        self.playwright_stopped = False

    def factory(self) -> SimpleNamespace:
        """构造可交给 production capture 的 Playwright manager fake。

        输入参数：无。
        输出返回值：拥有唯一 BrowserContext 与 evaluator page 的 manager。
        """

        boundary = self

        class _Page:
            """读取 boundary 当前 store payload 的 evaluator page fake。"""

            @property
            def url(self) -> str:
                """返回最近导航的 trusted cart URL。

                输入参数：无。
                输出返回值：当前 cart URL。
                """

                return boundary.current_cart_url

            def goto(self, url: str, **_kwargs: object) -> SimpleNamespace:
                """记录目标 store 并返回无重定向 HTTP 200。

                输入参数：url 与生产等待选项。
                输出返回值：主文档响应 fake。
                """

                boundary.current_cart_url = url
                boundary.current_store_id = next(
                    store_id
                    for store_id in boundary.payloads
                    if f"{store_id}.example.invalid" in url
                )
                boundary.visited_store_ids.append(boundary.current_store_id)
                return SimpleNamespace(status=200, url=url)

            def evaluate(
                self,
                _script: str,
                options: dict[str, object],
            ) -> dict[str, object]:
                """按当前店读取索引返回有界 Store API envelope。

                输入参数：脚本忽略；options 提供 endpoint_path。
                输出返回值：成功 JSON response envelope。
                """

                store_id = boundary.current_store_id
                index = boundary.read_indexes[store_id]
                boundary.read_indexes[store_id] = index + 1
                payload = boundary.payloads[store_id][index]
                origin = boundary.current_cart_url.removesuffix("/cart/")
                return {
                    "status": "success",
                    "http_status": 200,
                    "response_url": origin + str(options["endpoint_path"]),
                    "content_type": "application/json",
                    "body": json.dumps(payload),
                }

            def close(self) -> None:
                """记录 evaluator page 已关闭。

                输入参数：无。
                输出返回值：无。
                """

                boundary.page_closed = True

        page = _Page()
        context = SimpleNamespace(new_page=lambda: page)
        browser = SimpleNamespace(contexts=(context,))
        playwright = SimpleNamespace(
            chromium=SimpleNamespace(
                connect_over_cdp=lambda _endpoint, **_kwargs: browser
            ),
            stop=lambda: setattr(boundary, "playwright_stopped", True),
        )
        return SimpleNamespace(start=lambda: playwright)


class _Controller:
    """提供 Cart source prepare 所需的固定 guest controller 窄接口。"""

    def __init__(self) -> None:
        """初始化命令记录。

        输入参数：无。
        输出返回值：无；``launched`` 和 ``waited`` 初始为空。
        """

        self.launched: list[list[str]] = []
        self.waited: list[tuple[int, float]] = []

    def launch(self, command: list[str]) -> None:
        """记录一个 shell-free guest 启动命令。

        输入参数：command 为固定 argv。
        输出返回值：无；保存副本供测试观察。
        """

        self.launched.append(list(command))

    def wait_for_chrome_cdp(self, *, port: int, timeout: float) -> None:
        """记录 guest-local Chrome CDP 就绪等待。

        输入参数：port 与 timeout 为 source 固定的就绪参数。
        输出返回值：无；模拟立即就绪。
        """

        self.waited.append((port, timeout))


def test_source_captures_one_browser_context_as_complete_four_store_batch() -> None:
    """验证一次 source 捕获覆盖单 worker 的固定四店并被 collector 冻结。

    输入参数：
        无；注入一个返回完整四店终态的浏览器边界 fake。
    输出返回值：
        无；collector 得到完整批次，且 source 只执行一次原子四店读取。
    """

    calls: list[tuple[str, WebMallURLRegistry, object]] = []

    def load_snapshot(
        endpoint: str,
        registry: WebMallURLRegistry,
        contract: object,
    ) -> tuple[ObservedCartStore, ...]:
        """返回一个仅 store-2 含商品的完整浏览器快照。

        输入参数：endpoint、registry 与 contract 来自生产 source 绑定。
        输出返回值：固定顺序的四个完整 store observation。
        """

        calls.append((endpoint, registry, contract))
        return tuple(
            ObservedCartStore(
                logical_store_id=f"store-{index}",
                complete=True,
                items=(
                    (ObservedCartItem("private-cart-widget", 2),) if index == 2 else ()
                ),
            )
            for index in range(1, 5)
        )

    source = WebMallBrowserCartSource(
        registry=_REGISTRY,
        cart_reader=_MANIFEST.cart_reader,
        worker_id="worker-1",
        host="127.0.0.1",
        chromium_port=59222,
        snapshot_loader=load_snapshot,
        cdp_ready_probe=lambda _endpoint, _timeout: None,
    )
    controller: Any = _Controller()
    source.prepare(controller)

    observation = capture_webmall_cart_observation(
        source,
        ("worker-1",),
    )

    assert observation.complete is True
    assert len(observation.workers) == 1
    assert len(observation.workers[0].stores) == 4
    assert observation.workers[0].stores[1].items[0].quantity == 2
    assert len(calls) == 1
    assert calls[0][0] == "http://127.0.0.1:59222"
    assert calls[0][1] is _REGISTRY
    assert calls[0][2] is _MANIFEST.cart_reader
    assert controller.launched == [
        ["google-chrome", "--remote-debugging-port=1337"],
        [
            "socat",
            "tcp-listen:9222,fork,reuseaddr",
            "tcp:localhost:1337",
        ],
    ]
    assert controller.waited == [(1337, 15.0)]
    assert "private-cart-widget" not in repr(source)
    assert "worker-1" not in repr(source)


def test_source_construction_with_production_boundaries_performs_no_io() -> None:
    """验证 production 默认边界在构造阶段不连接 CDP 或读取商店。

    输入参数：
        无；省略测试 boundary injection，仅构造真实 source。
    输出返回值：
        无；构造成功且脱敏 repr 表示尚未 prepare。
    """

    source = WebMallBrowserCartSource(
        registry=_REGISTRY,
        cart_reader=_MANIFEST.cart_reader,
        worker_id="worker-1",
        host="127.0.0.1",
        chromium_port=59222,
    )

    assert repr(source) == ("WebMallBrowserCartSource(prepared=False, closed=False)")


def test_source_rejects_browser_context_replacement_between_prepare_and_capture() -> (
    None
):
    """验证 prepare 绑定的 Attempt BrowserContext 不能在终态捕获前被替换。

    输入参数：无；Playwright fake 在第二次读取 ``browser.contexts`` 时返回
        另一个唯一 context，因此数量检查本身仍会通过。
    输出返回值：无；source 在创建 evaluator page 或读取 Cart 前失败关闭。
    """

    state = {"context_reads": 0, "new_page_calls": 0, "stopped": False}

    def forbidden_new_page() -> object:
        """记录不应发生的 evaluator page 创建。

        输入参数：无。
        输出返回值：空对象；正确路径在 context identity 漂移处先失败。
        """

        state["new_page_calls"] += 1
        return object()

    first_context = SimpleNamespace(new_page=forbidden_new_page)
    replacement_context = SimpleNamespace(new_page=forbidden_new_page)

    class _Browser:
        """每次观察只暴露一个 context，但第二次替换其对象身份。"""

        @property
        def contexts(self) -> tuple[object, ...]:
            """按 prepare/capture 阶段返回不同的唯一 context。

            输入参数：无。
            输出返回值：只含当前阶段 context 的元组。
            """

            state["context_reads"] += 1
            if state["context_reads"] == 1:
                return (first_context,)
            return (replacement_context,)

    browser = _Browser()
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda _endpoint, **_kwargs: browser),
        stop=lambda: state.__setitem__("stopped", True),
    )
    manager = SimpleNamespace(start=lambda: playwright)
    source = WebMallBrowserCartSource(
        registry=_REGISTRY,
        cart_reader=_MANIFEST.cart_reader,
        worker_id="worker-1",
        host="127.0.0.1",
        chromium_port=59222,
        cdp_ready_probe=lambda _endpoint, _timeout: None,
        playwright_factory=lambda: manager,
    )
    source.prepare(_Controller())

    with pytest.raises(WebMallBrowserCartSourceError):
        source.read_cart("worker-1", "store-1")

    source.close()
    assert state == {
        "context_reads": 2,
        "new_page_calls": 0,
        "stopped": True,
    }


def test_source_rejects_browser_context_replacement_during_capture() -> None:
    """验证双 sweep 结束后还需复验 prepare 绑定的 context 未被替换。

    输入参数：无；前两次 context 读取保持同一对象，第三次返回替代对象；
        evaluator page 本身可成功完成八次空 Cart API 读取。
    输出返回值：无；source 不接受读取窗口中发生的 context identity 漂移。
    """

    state: dict[str, object] = {
        "context_reads": 0,
        "url": "",
        "stopped": False,
    }

    class _Page:
        """提供稳定空 Cart 的 evaluator page fake。"""

        @property
        def url(self) -> str:
            """返回最近一次无重定向 cart URL。

            输入参数：无。
            输出返回值：当前 URL。
            """

            return str(state["url"])

        def goto(self, url: str, **_kwargs: object) -> SimpleNamespace:
            """记录目标并返回 HTTP 200 主文档响应。

            输入参数：url 与 production 导航选项。
            输出返回值：状态、URL 均匹配的响应 fake。
            """

            state["url"] = url
            return SimpleNamespace(status=200, url=url)

        def evaluate(
            self,
            _script: str,
            options: dict[str, object],
        ) -> dict[str, object]:
            """返回当前店精确 API URL 的空 Cart envelope。

            输入参数：脚本忽略；options 提供固定 endpoint path。
            输出返回值：严格 JSON 成功 envelope。
            """

            origin = str(state["url"]).removesuffix("/cart/")
            return {
                "status": "success",
                "http_status": 200,
                "response_url": origin + str(options["endpoint_path"]),
                "content_type": "application/json",
                "body": '{"items":[],"items_count":0}',
            }

        def close(self) -> None:
            """提供成功清理接口。

            输入参数：无。
            输出返回值：无。
            """

    first_context = SimpleNamespace(new_page=lambda: _Page())
    replacement_context = SimpleNamespace(new_page=lambda: _Page())

    class _Browser:
        """在 capture 结束后的第三次身份复验才替换 context。"""

        @property
        def contexts(self) -> tuple[object, ...]:
            """前两次返回 prepare context，之后返回替代 context。

            输入参数：无。
            输出返回值：始终只有一个 context 的元组。
            """

            context_reads = int(state["context_reads"]) + 1
            state["context_reads"] = context_reads
            if context_reads <= 2:
                return (first_context,)
            return (replacement_context,)

    browser = _Browser()
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda _endpoint, **_kwargs: browser),
        stop=lambda: state.__setitem__("stopped", True),
    )
    source = WebMallBrowserCartSource(
        registry=_REGISTRY,
        cart_reader=_MANIFEST.cart_reader,
        worker_id="worker-1",
        host="127.0.0.1",
        chromium_port=59222,
        cdp_ready_probe=lambda _endpoint, _timeout: None,
        playwright_factory=lambda: SimpleNamespace(start=lambda: playwright),
    )
    source.prepare(_Controller())

    with pytest.raises(WebMallBrowserCartSourceError):
        source.read_cart("worker-1", "store-1")

    source.close()
    assert state["context_reads"] == 3
    assert state["stopped"] is True


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("protocol_id", "paraguibench.webmall.untrusted-cart.v1"),
        ("reader_kind", "untrusted_reader"),
        ("endpoint_path", "/untrusted/cart"),
        ("max_response_bytes", 8 * 1024 * 1024),
        ("max_items", 4096),
        ("max_quantity", 100_000),
        ("timeout_seconds", 120),
        ("reference_live_validation_status", "unreviewed"),
    ),
)
def test_source_rejects_any_cart_reader_contract_drift_before_io(
    field_name: str,
    invalid_value: object,
) -> None:
    """验证 source 只接受 manifest 精确冻结的 Cart reader 合同。

    输入参数：
        field_name/invalid_value：被手工漂移的合同字段和值。
    输出返回值：
        无；构造在 snapshot/probe 系统边界前失败。
    """

    calls: list[str] = []
    drifted_contract = replace(
        _MANIFEST.cart_reader,
        **{field_name: invalid_value},
    )

    with pytest.raises(WebMallBrowserCartSourceError):
        WebMallBrowserCartSource(
            registry=_REGISTRY,
            cart_reader=drifted_contract,
            worker_id="worker-1",
            host="127.0.0.1",
            chromium_port=59222,
            snapshot_loader=lambda *_args: calls.append("loader"),
            cdp_ready_probe=lambda *_args: calls.append("probe"),
        )

    assert calls == []


def test_source_rejects_remote_cdp_binding_before_any_browser_io() -> None:
    """验证无认证 CDP 只能绑定数值 loopback，并在副作用前拒绝远程地址。

    输入参数：
        无；提供文档测试网 IP，并放置不得调用的 loader/probe 哨兵。
    输出返回值：
        无；构造抛固定错误，两个系统边界均未被调用。
    """

    calls: list[str] = []

    def forbidden_loader(
        _endpoint: str,
        _registry: WebMallURLRegistry,
        _contract: object,
    ) -> tuple[ObservedCartStore, ...]:
        """记录越界快照 I/O 并立即失败。

        输入参数：三个 production binding 入参均忽略。
        输出返回值：不返回；任何调用都表示 preflight 顺序错误。
        """

        calls.append("loader")
        raise AssertionError

    with pytest.raises(WebMallBrowserCartSourceError):
        WebMallBrowserCartSource(
            registry=_REGISTRY,
            cart_reader=_MANIFEST.cart_reader,
            worker_id="worker-1",
            host="192.0.2.10",
            chromium_port=59222,
            snapshot_loader=forbidden_loader,
            cdp_ready_probe=lambda _endpoint, _timeout: calls.append("probe"),
        )

    assert calls == []


def test_playwright_reader_requires_exactly_one_existing_browser_context() -> None:
    """验证 reader 不会猜测多个 context 中哪一个持有 Agent Cart session。

    输入参数：
        无；CDP browser 暴露两个 context，均不允许被读取。
    输出返回值：
        无；在创建 evaluator page 前失败，并正常停止 Playwright 客户端。
    """

    state = {"new_page_calls": 0, "stopped": False}

    def new_page() -> object:
        """记录不应发生的 evaluator page 创建。

        输入参数：无。
        输出返回值：空对象；正常逻辑不会调用。
        """

        state["new_page_calls"] += 1
        return object()

    context = SimpleNamespace(new_page=new_page)
    browser = SimpleNamespace(contexts=(context, context))
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda _endpoint, **_kwargs: browser),
        stop=lambda: state.__setitem__("stopped", True),
    )
    manager = SimpleNamespace(start=lambda: playwright)

    with pytest.raises(WebMallBrowserCartSourceError):
        capture_webmall_cart_stores_with_playwright(
            "http://127.0.0.1:59222",
            _REGISTRY,
            _MANIFEST.cart_reader,
            playwright_factory=lambda: manager,
        )

    assert state == {"new_page_calls": 0, "stopped": True}


def test_playwright_reader_rejects_redirected_or_stale_cart_page() -> None:
    """验证主文档未停在目标店精确 cart URL 时不能读取 API。

    输入参数：
        无；goto 返回另一店 cart URL，并放置不得调用的 evaluate 哨兵。
    输出返回值：
        无；page identity 检查失败且不读取任何 Cart API body。
    """

    state = {"evaluate_calls": 0, "closed": False, "stopped": False}

    class _Page:
        """模拟被重定向到错误店的 evaluator page。"""

        url = "https://store-2.example.invalid/cart/"

        def goto(self, _url: str, **_kwargs: object) -> SimpleNamespace:
            """返回错误店主文档响应。

            输入参数：目标 URL 与选项均忽略。
            输出返回值：状态 200 但 URL 错误的响应。
            """

            return SimpleNamespace(status=200, url=self.url)

        def evaluate(self, *_args: object, **_kwargs: object) -> object:
            """记录不应发生的 API 读取。

            输入参数：任意 evaluate 参数。
            输出返回值：空对象；正常逻辑不会调用。
            """

            state["evaluate_calls"] += 1
            return {}

        def close(self) -> None:
            """记录 evaluator page 清理。

            输入参数：无。
            输出返回值：无。
            """

            state["closed"] = True

    page = _Page()
    browser = SimpleNamespace(contexts=(SimpleNamespace(new_page=lambda: page),))
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=lambda _endpoint, **_kwargs: browser),
        stop=lambda: state.__setitem__("stopped", True),
    )
    manager = SimpleNamespace(start=lambda: playwright)

    with pytest.raises(WebMallBrowserCartSourceError):
        capture_webmall_cart_stores_with_playwright(
            "http://127.0.0.1:59222",
            _REGISTRY,
            _MANIFEST.cart_reader,
            playwright_factory=lambda: manager,
        )

    assert state == {"evaluate_calls": 0, "closed": True, "stopped": True}


def test_playwright_reader_uses_existing_context_and_exact_store_api_state() -> None:
    """验证生产 reader 复用现有 context、双读四店且不关闭 Agent Chrome。

    输入参数：
        无；以 fake Playwright 外部边界返回稳定 Store API payload。
    输出返回值：
        无；四店完整返回，quantity 精确保留，临时页与客户端被关闭。
    """

    state = {
        "current_cart_url": "",
        "page_closed": False,
        "playwright_stopped": False,
        "connect_endpoint": "",
        "evaluate_count": 0,
    }

    class _Response:
        """模拟 Playwright 主文档响应的只读表面。"""

        def __init__(self, url: str) -> None:
            """保存本次无重定向导航 URL。

            输入参数：url 为 trusted cart URL。
            输出返回值：无。
            """

            self.url = url

        @property
        def status(self) -> int:
            """返回成功主文档状态。

            输入参数：无。
            输出返回值：固定 HTTP 200。
            """

            return 200

    class _Page:
        """模拟同一 BrowserContext 内专供 evaluator 使用的临时页面。"""

        @property
        def url(self) -> str:
            """返回最近一次 cart 导航地址。

            输入参数：无。
            输出返回值：当前 trusted cart URL。
            """

            return state["current_cart_url"]

        def goto(self, url: str, **_kwargs: object) -> _Response:
            """完成一个无重定向 cart GET 导航。

            输入参数：url 为目标；其余为生产超时/等待参数。
            输出返回值：HTTP 200 的主文档响应。
            """

            state["current_cart_url"] = url
            return _Response(url)

        def evaluate(
            self,
            _script: str,
            options: dict[str, object],
        ) -> dict[str, object]:
            """返回当前店同会话 Store API 的有界成功 envelope。

            输入参数：脚本内容忽略；options 提供固定 API 路径与上限。
            输出返回值：可由生产 parser 严格读取的 response envelope。
            """

            state["evaluate_count"] += 1
            cart_url = str(state["current_cart_url"])
            origin = cart_url.removesuffix("/cart/")
            store_index = int(origin.rsplit("-", 1)[-1].split(".", 1)[0])
            items = []
            if store_index == 3:
                items = [
                    {
                        "id": 37,
                        "key": "private-cart-line-key",
                        "quantity": 2,
                        "permalink": origin + "/product/private-api-widget/",
                    }
                ]
            return {
                "status": "success",
                "http_status": 200,
                "response_url": origin + str(options["endpoint_path"]),
                "content_type": "application/json; charset=UTF-8",
                "body": json.dumps(
                    {
                        "items": items,
                        "items_count": sum(int(item["quantity"]) for item in items),
                    }
                ),
            }

        def close(self) -> None:
            """记录 evaluator 临时页已关闭。

            输入参数：无。
            输出返回值：无。
            """

            state["page_closed"] = True

    page = _Page()
    context = SimpleNamespace(new_page=lambda: page)

    class _Browser:
        """模拟 attach 到 Agent Chrome 的 CDP browser。"""

        contexts = (context,)

        def close(self) -> None:
            """拒绝生产 reader 关闭 Agent Chrome。

            输入参数：无。
            输出返回值：不返回；任何调用都使测试失败。
            """

            raise AssertionError("reader must not close Agent Chrome")

    def connect_over_cdp(endpoint: str, **_kwargs: object) -> _Browser:
        """记录 CDP endpoint 并返回唯一 context 的 browser。

        输入参数：endpoint 与生产连接选项。
        输出返回值：单 context browser fake。
        """

        state["connect_endpoint"] = endpoint
        return _Browser()

    playwright = SimpleNamespace(
        chromium=SimpleNamespace(connect_over_cdp=connect_over_cdp),
        stop=lambda: state.__setitem__("playwright_stopped", True),
    )
    manager = SimpleNamespace(start=lambda: playwright)

    stores = capture_webmall_cart_stores_with_playwright(
        "http://127.0.0.1:59222",
        _REGISTRY,
        _MANIFEST.cart_reader,
        playwright_factory=lambda: manager,
    )

    assert tuple(store.logical_store_id for store in stores) == (
        "store-1",
        "store-2",
        "store-3",
        "store-4",
    )
    assert stores[2].items == (ObservedCartItem("private-api-widget", 2),)
    assert state["evaluate_count"] == 8
    assert state["connect_endpoint"] == "http://127.0.0.1:59222"
    assert state["page_closed"] is True
    assert state["playwright_stopped"] is True


def test_playwright_reader_rejects_cart_change_between_two_reads() -> None:
    """验证同店两次规范化 Cart 读取不一致时整批失败关闭。

    输入参数：
        无；store-1 第一次为空、第二次出现一件商品，其余店稳定为空。
    输出返回值：
        无；抛固定脱敏错误，不返回已读取的部分四店批次。
    """

    empty = {"items": [], "items_count": 0}
    changed = {
        "items": [
            {
                "id": 1,
                "key": "private-line-key",
                "quantity": 1,
                "permalink": ("https://store-1.example.invalid/product/private-race/"),
            }
        ],
        "items_count": 1,
    }
    boundary = _PayloadPlaywrightBoundary(
        {
            "store-1": (empty, changed),
            "store-2": (empty, empty),
            "store-3": (empty, empty),
            "store-4": (empty, empty),
        }
    )

    with pytest.raises(WebMallBrowserCartSourceError) as captured:
        capture_webmall_cart_stores_with_playwright(
            "http://127.0.0.1:59222",
            _REGISTRY,
            _MANIFEST.cart_reader,
            playwright_factory=boundary.factory,
        )

    assert str(captured.value) == "WEBMALL_BROWSER_CART_SOURCE_INVALID"
    assert captured.value.__cause__ is None
    assert "private-race" not in repr(captured.value)
    assert boundary.page_closed is True
    assert boundary.playwright_stopped is True


def test_playwright_reader_reads_two_complete_four_store_sweeps() -> None:
    """验证稳定性窗口必须是两次完整四店 sweep，而不是逐店连续双读。

    输入参数：无；四店均返回两次稳定空购物车。
    输出返回值：无；可观察导航顺序严格为 1,2,3,4,1,2,3,4。
    """

    empty = {"items": [], "items_count": 0}
    boundary = _PayloadPlaywrightBoundary(
        {f"store-{index}": (empty, empty) for index in range(1, 5)}
    )

    stores = capture_webmall_cart_stores_with_playwright(
        "http://127.0.0.1:59222",
        _REGISTRY,
        _MANIFEST.cart_reader,
        playwright_factory=boundary.factory,
    )

    assert tuple(store.logical_store_id for store in stores) == (
        "store-1",
        "store-2",
        "store-3",
        "store-4",
    )
    assert boundary.visited_store_ids == [
        "store-1",
        "store-2",
        "store-3",
        "store-4",
        "store-1",
        "store-2",
        "store-3",
        "store-4",
    ]


def test_source_exposes_sanitized_reference_proof_only_after_bound_capture() -> None:
    """验证 source 仅在同 context 完整双 sweep 后产生 component proof。

    输入参数：无；production 会话边界由稳定空 Cart Playwright fake 提供。
    输出返回值：无；捕获前失败，捕获后 proof 只有连续性、顺序和一致性。
    """

    empty = {"items": [], "items_count": 0}
    boundary = _PayloadPlaywrightBoundary(
        {f"store-{index}": (empty, empty) for index in range(1, 5)}
    )
    source = WebMallBrowserCartSource(
        registry=_REGISTRY,
        cart_reader=_MANIFEST.cart_reader,
        worker_id="private-worker-id",
        host="127.0.0.1",
        chromium_port=59222,
        cdp_ready_probe=lambda _endpoint, _timeout: None,
        playwright_factory=boundary.factory,
    )
    source.prepare(_Controller())

    with pytest.raises(WebMallBrowserCartSourceError):
        source.reference_validation_proof()

    capture_webmall_cart_observation(source, ("private-worker-id",))
    proof = source.reference_validation_proof()
    source.close()

    expected_sweep = ("store-1", "store-2", "store-3", "store-4")
    assert proof.browser_context_continuity_verified is True
    assert proof.sweep_store_ids == (expected_sweep, expected_sweep)
    assert proof.normalized_universe_match is True
    assert "private-worker-id" not in repr(proof)


def test_playwright_reader_rejects_product_permalink_from_wrong_store() -> None:
    """验证同 slug 的错误店 permalink 不能进入当前 store observation。

    输入参数：
        无；store-1 payload 故意返回 store-2 的商品 permalink。
    输出返回值：
        无；整个四店证据失败，错误不回显 origin、slug 或 line key。
    """

    empty = {"items": [], "items_count": 0}
    wrong_store = {
        "items": [
            {
                "id": 9,
                "key": "private-wrong-store-key",
                "quantity": 1,
                "permalink": (
                    "https://store-2.example.invalid/product/private-wrong-store-slug/"
                ),
            }
        ],
        "items_count": 1,
    }
    boundary = _PayloadPlaywrightBoundary(
        {
            "store-1": (wrong_store, wrong_store),
            "store-2": (empty, empty),
            "store-3": (empty, empty),
            "store-4": (empty, empty),
        }
    )

    with pytest.raises(WebMallBrowserCartSourceError) as captured:
        capture_webmall_cart_stores_with_playwright(
            "http://127.0.0.1:59222",
            _REGISTRY,
            _MANIFEST.cart_reader,
            playwright_factory=boundary.factory,
        )

    rendered = f"{captured.value!s}|{captured.value!r}"
    assert "private-wrong-store" not in rendered
    assert "store-2.example.invalid" not in rendered
    assert boundary.page_closed is True


def test_playwright_reader_aggregates_duplicate_slug_rows_and_quantity() -> None:
    """验证同店同 slug 多个 API item 的数量不会被 Set 去重丢失。

    输入参数：
        无；store-4 返回两个不同 line key、相同 permalink、数量 1 和 2。
    输出返回值：
        无；不可变 store observation 只含该 canonical slug 且数量为 3。
    """

    empty = {"items": [], "items_count": 0}
    duplicate_slug = {
        "items": [
            {
                "id": 41,
                "key": "private-line-a",
                "quantity": 1,
                "permalink": (
                    "https://store-4.example.invalid/product/private-duplicate/"
                ),
            },
            {
                "id": 41,
                "key": "private-line-b",
                "quantity": 2,
                "permalink": (
                    "https://store-4.example.invalid/product/private-duplicate/"
                ),
            },
        ],
        "items_count": 3,
    }
    boundary = _PayloadPlaywrightBoundary(
        {
            "store-1": (empty, empty),
            "store-2": (empty, empty),
            "store-3": (empty, empty),
            "store-4": (duplicate_slug, duplicate_slug),
        }
    )

    stores = capture_webmall_cart_stores_with_playwright(
        "http://127.0.0.1:59222",
        _REGISTRY,
        _MANIFEST.cart_reader,
        playwright_factory=boundary.factory,
    )

    assert stores[3].items == (ObservedCartItem("private-duplicate", 3),)
    assert "private-duplicate" not in repr(stores[3])


def test_playwright_reader_rejects_inconsistent_items_count() -> None:
    """验证 API 自报总数量与完整 items 数量不一致时不得当作闭集。

    输入参数：
        无；store-2 唯一 item 数量为 2，但 ``items_count`` 故意写 1。
    输出返回值：
        无；生产 reader 抛 evaluator-safe 固定错误。
    """

    empty = {"items": [], "items_count": 0}
    incomplete = {
        "items": [
            {
                "id": 22,
                "key": "private-incomplete-line",
                "quantity": 2,
                "permalink": (
                    "https://store-2.example.invalid/product/private-incomplete/"
                ),
            }
        ],
        "items_count": 1,
    }
    boundary = _PayloadPlaywrightBoundary(
        {
            "store-1": (empty, empty),
            "store-2": (incomplete, incomplete),
            "store-3": (empty, empty),
            "store-4": (empty, empty),
        }
    )

    with pytest.raises(WebMallBrowserCartSourceError) as captured:
        capture_webmall_cart_stores_with_playwright(
            "http://127.0.0.1:59222",
            _REGISTRY,
            _MANIFEST.cart_reader,
            playwright_factory=boundary.factory,
        )

    assert str(captured.value) == "WEBMALL_BROWSER_CART_SOURCE_INVALID"
