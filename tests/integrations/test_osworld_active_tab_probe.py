"""Google Shopping active-tab 探针的离线契约测试。"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from types import SimpleNamespace
from urllib.request import ProxyHandler

import pytest

from paraguibench.integrations.osworld import active_tab_probe as probe_module
from paraguibench.integrations.osworld.active_tab_probe import (
    _PAGE_OBSERVATION_SCRIPT,
    _build_proxy_free_url_opener,
    _extract_active_url_from_accessibility_tree,
    _read_page_payloads_with_playwright,
    ActivePageObservation,
    OSWorldActiveTabProbeError,
    capture_google_shopping_active_tab_observation,
    select_active_page_observation,
)


def test_accessibility_opener_never_inherits_host_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 loopback accessibility 请求显式禁用宿主代理环境。

    输入参数：
        monkeypatch：pytest 提供的依赖替换工具，用于捕获生产构造参数。
    输出返回值：
        无；必须恰有一个空代理映射，不能回退到环境代理发现。
    """

    handlers: list[object] = []
    sentinel = object()

    def fake_build_opener(*provided_handlers: object) -> object:
        """记录传给标准库 opener builder 的 handler。

        输入参数：
            provided_handlers：生产 helper 提供的代理 handler。
        输出返回值：
            不发起网络请求的唯一哨兵对象。
        """

        handlers.extend(provided_handlers)
        return sentinel

    monkeypatch.setattr(probe_module, "build_opener", fake_build_opener)
    opener = _build_proxy_free_url_opener()
    proxy_handlers = [
        handler for handler in handlers if isinstance(handler, ProxyHandler)
    ]

    assert opener is sentinel
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}


def _complete_payload(**overrides: object) -> dict[str, object]:
    """构造一个符合 JavaScript→Python 固定边界的页面 payload。

    输入参数：
        overrides：覆盖默认页面字段的测试值。
    输出返回值：
        默认为英文 Google Shopping 页的完整 payload。
    """

    payload: dict[str, object] = {
        "url": (
            "https://www.google.com/search?tbm=shop&hl=en&gl=us"
            "&q=black+drip+coffee+maker+sale+between+%2425+and+%2460"
        ),
        "title": "Google Shopping",
        "locale": "en-US",
        "focused": True,
        "visibilityState": "visible",
        "filterSurfaceObserved": True,
        "selectionEnumerationComplete": True,
        "selectionEvidence": "semantic_selected_filter_list",
        "selectedFilterLabels": ["Black", "$25 - $60", "On sale"],
        "googleFilterStateLists": [],
        "captchaChallengeObserved": False,
        "consentSurfaceObserved": False,
        "blockedReason": "",
    }
    payload.update(overrides)
    return payload


def _stable_active_url_loader(url: str) -> tuple[list[tuple[str, int]], object]:
    """构造返回同一地址的可注入 accessibility loader。

    输入参数：
        url：两次地址栏采样都要返回的 URL。
    输出返回值：
        调用记录列表与可调用 loader。
    """

    calls: list[tuple[str, int]] = []

    def load(host: str, port: int) -> str:
        """记录成对 VM API 端口并返回固定地址栏文本。"""

        calls.append((host, port))
        return url

    return calls, load


def test_capture_returns_one_immutable_url_and_dom_observation() -> None:
    """验证 URL q 与三类 DOM 筛选来自同一个不可变快照。"""

    url = str(_complete_payload()["url"])
    calls, active_url_loader = _stable_active_url_loader(url)
    endpoints: list[str] = []

    def load_pages(endpoint: str) -> list[dict[str, object]]:
        """记录唯一 CDP endpoint 并返回一个完整页面快照。"""

        endpoints.append(endpoint)
        return [_complete_payload()]

    observation = capture_google_shopping_active_tab_observation(
        host="127.0.0.1",
        chromium_port=59222,
        server_port=55001,
        page_payload_loader=load_pages,
        active_url_loader=active_url_loader,
    )

    assert endpoints == ["http://127.0.0.1:59222"]
    assert calls == [("127.0.0.1", 55001), ("127.0.0.1", 55001)]
    assert "q=black+drip+coffee" in observation.url
    assert observation.selected_filter_labels == (
        "Black",
        "$25 - $60",
        "On sale",
    )
    assert observation.selection_enumeration_complete is True


def test_capture_rejects_active_url_change_during_sampling() -> None:
    """验证 AT→CDP→AT 期间切换标签页时 fail closed。"""

    urls: Iterator[str] = iter(
        [str(_complete_payload()["url"]), "chrome://new-tab-page/"]
    )
    with pytest.raises(OSWorldActiveTabProbeError, match="采样期间"):
        capture_google_shopping_active_tab_observation(
            host="127.0.0.1",
            chromium_port=59222,
            server_port=55001,
            page_payload_loader=lambda _endpoint: [_complete_payload()],
            active_url_loader=lambda _host, _port: next(urls),
        )


def test_capture_rejects_address_bar_and_cdp_mismatch() -> None:
    """验证地址栏无法与唯一 CDP 页面匹配时不猜测目标页。"""

    calls, loader = _stable_active_url_loader("https://example.com/")
    with pytest.raises(OSWorldActiveTabProbeError, match="无法唯一配对"):
        capture_google_shopping_active_tab_observation(
            host="127.0.0.1",
            chromium_port=59222,
            server_port=55001,
            page_payload_loader=lambda _endpoint: [_complete_payload()],
            active_url_loader=loader,
        )
    assert len(calls) == 2


def test_select_rejects_focus_ambiguity_without_authoritative_hint() -> None:
    """验证没有地址栏证据时不会偏好 Shopping 页。"""

    observations = [
        ActivePageObservation.from_payload(_complete_payload()),
        ActivePageObservation.from_payload(
            _complete_payload(
                url="chrome://new-tab-page/",
                title="New tab",
                locale="en",
                filterSurfaceObserved=False,
                selectionEnumerationComplete=False,
                selectionEvidence="",
                selectedFilterLabels=[],
            )
        ),
    ]
    with pytest.raises(OSWorldActiveTabProbeError, match="歧义"):
        select_active_page_observation(observations)


def test_google_filter_state_list_derives_closed_world_labels() -> None:
    """验证 2026-07 Google filter-state list 可完整枚举屏外项。"""

    payload = _complete_payload(
        selectionEnumerationComplete=False,
        selectionEvidence="legacy_fT28tf_unverified",
        selectedFilterLabels=[
            "All filters.",
            "Black filter. Selected.",
        ],
        googleFilterStateLists=[
            {
                "allFiltersControlCount": 1,
                "unknownRenderedChildCount": 0,
                "items": [
                    {"filterControlNames": ["Remove Black filter. Selected."]},
                    {"filterControlNames": ["Remove $25 - $60 filter. Selected."]},
                    {"filterControlNames": ["Remove On sale filter. Selected."]},
                    {"filterControlNames": ["Blue filter. Not selected."]},
                ],
            }
        ],
    )

    observation = ActivePageObservation.from_payload(payload)

    assert observation.selection_enumeration_complete is True
    assert observation.selection_evidence == ("semantic_google_filter_state_list")
    assert observation.selected_filter_labels == (
        "Black",
        "$25 - $60",
        "On sale",
    )


@pytest.mark.parametrize(
    "trace",
    [
        [
            {
                "allFiltersControlCount": 1,
                "unknownRenderedChildCount": 1,
                "items": [{"filterControlNames": ["Remove Black filter. Selected."]}],
            }
        ],
        [
            {
                "allFiltersControlCount": 1,
                "unknownRenderedChildCount": 0,
                "items": [
                    {
                        "filterControlNames": [
                            "Remove Black filter. Selected.",
                            "Remove On sale filter. Selected.",
                        ]
                    }
                ],
            }
        ],
    ],
)
def test_google_filter_state_ambiguity_never_claims_complete(
    trace: list[dict[str, object]],
) -> None:
    """验证未知直属节点或单项多状态链接不会产生闭集结论。"""

    observation = ActivePageObservation.from_payload(
        _complete_payload(
            selectionEnumerationComplete=True,
            googleFilterStateLists=trace,
        )
    )

    assert observation.selection_enumeration_complete is False
    assert "conflict" in observation.selection_evidence


def test_broad_filter_surface_does_not_imply_complete_enumeration() -> None:
    """验证三个可见标签本身不足以证明没有额外筛选。"""

    observation = ActivePageObservation.from_payload(
        _complete_payload(
            selectionEnumerationComplete=False,
            selectionEvidence="partial_filter_surface",
            googleFilterStateLists=[],
        )
    )

    assert observation.selected_filter_labels == (
        "Black",
        "$25 - $60",
        "On sale",
    )
    assert observation.selection_enumeration_complete is False


@pytest.mark.parametrize("locale", ["", "zh-CN", "und", "english"])
def test_google_shopping_unknown_or_non_english_locale_is_error(
    locale: str,
) -> None:
    """验证目标 Shopping 页的语言不可确定时不得正常评分。"""

    url = str(_complete_payload()["url"])
    _calls, loader = _stable_active_url_loader(url)
    with pytest.raises(OSWorldActiveTabProbeError, match="locale"):
        capture_google_shopping_active_tab_observation(
            host="127.0.0.1",
            chromium_port=59222,
            server_port=55001,
            page_payload_loader=lambda _endpoint: [_complete_payload(locale=locale)],
            active_url_loader=loader,
        )


@pytest.mark.parametrize(
    ("url", "fields", "reason"),
    [
        (
            "https://www.google.com/sorry/index",
            {"blockedReason": "google_captcha"},
            "google_captcha",
        ),
        (
            str(_complete_payload()["url"]),
            {
                "blockedReason": "google_captcha",
                "captchaChallengeObserved": True,
            },
            "google_captcha",
        ),
        (
            "https://consent.google.com/m",
            {"blockedReason": "google_consent"},
            "google_consent",
        ),
        (
            str(_complete_payload()["url"]),
            {
                "blockedReason": "google_consent",
                "consentSurfaceObserved": True,
            },
            "google_consent",
        ),
    ],
)
def test_trusted_captcha_and_consent_are_probe_errors(
    url: str,
    fields: dict[str, object],
    reason: str,
) -> None:
    """验证受信 Google 主机的强验证码/同意页证据停止评分。"""

    _calls, loader = _stable_active_url_loader(url)
    with pytest.raises(OSWorldActiveTabProbeError, match=reason):
        capture_google_shopping_active_tab_observation(
            host="127.0.0.1",
            chromium_port=59222,
            server_port=55001,
            page_payload_loader=lambda _endpoint: [
                _complete_payload(url=url, **fields)
            ],
            active_url_loader=loader,
        )


def test_third_party_block_text_is_normal_wrong_page_observation() -> None:
    """验证第三方 ``/sorry`` 页不能伪造 Google 基础设施错误。"""

    url = "https://example.com/sorry/about-google/"
    _calls, loader = _stable_active_url_loader(url)
    observation = capture_google_shopping_active_tab_observation(
        host="127.0.0.1",
        chromium_port=59222,
        server_port=55001,
        page_payload_loader=lambda _endpoint: [
            _complete_payload(
                url=url,
                locale="",
                blockedReason="google_captcha",
                filterSurfaceObserved=False,
                selectionEnumerationComplete=False,
                selectionEvidence="",
                selectedFilterLabels=[],
            )
        ],
        active_url_loader=loader,
    )

    assert observation.url == url
    assert observation.blocked_reason == ""


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("focused", "true"),
        ("selectedFilterLabels", "Black"),
        ("selectionEnumerationComplete", 1),
        ("captchaChallengeObserved", None),
    ],
)
def test_payload_schema_drift_fails_closed(field: str, invalid: object) -> None:
    """验证 JavaScript payload 类型漂移不会被 ``bool/str`` 静默转换。"""

    with pytest.raises(OSWorldActiveTabProbeError, match="payload"):
        ActivePageObservation.from_payload(_complete_payload(**{field: invalid}))


def test_accessibility_tree_requires_one_visible_address_bar() -> None:
    """验证 accessibility tree 只接受唯一可见非空地址栏。"""

    tree = """
    <desktop-frame xmlns:st='uri:deskat:state.at-spi.gnome.org'>
      <entry name='Address and search bar' st:showing='true'>
        google.com/search?tbm=shop&amp;q=coffee
      </entry>
      <entry name='Address and search bar' st:showing='false'>hidden</entry>
    </desktop-frame>
    """

    assert _extract_active_url_from_accessibility_tree(tree) == (
        "google.com/search?tbm=shop&q=coffee"
    )
    with pytest.raises(OSWorldActiveTabProbeError, match="唯一"):
        _extract_active_url_from_accessibility_tree(
            tree.replace("st:showing='false'", "st:showing='true'")
        )


def test_playwright_is_imported_only_when_real_loader_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证模块导入不需要 Playwright，真实 I/O 边界缺失依赖时才报错。"""

    original_import = builtins.__import__

    def reject_playwright(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: object = (),
        level: int = 0,
    ) -> object:
        """仅在真实 loader 延迟导入 Playwright 时模拟依赖缺失。"""

        if name == "playwright.sync_api":
            raise ImportError("missing in test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", reject_playwright)
    with pytest.raises(OSWorldActiveTabProbeError, match="Playwright"):
        _read_page_payloads_with_playwright("http://127.0.0.1:59222")


def test_playwright_reader_rejects_partial_live_page_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证任一仍存活页面不可读时不使用剩余后台页评分。"""

    import playwright.sync_api

    class FakePage:
        """模拟 Playwright page 的成功或失败脚本读取。"""

        def __init__(self, *, fails: bool) -> None:
            """保存该页的 ``evaluate`` 是否应失败。"""

            self._fails = fails

        def is_closed(self) -> bool:
            """返回页面仍存活，使失败成为必须拒绝的残缺采样。"""

            return False

        def evaluate(self, _script: str) -> dict[str, object]:
            """返回完整 payload 或抛出模拟 CDP 读取错误。"""

            if self._fails:
                raise RuntimeError("page raced")
            return _complete_payload()

    stopped: list[bool] = []
    fake_playwright = SimpleNamespace(
        chromium=SimpleNamespace(
            connect_over_cdp=lambda _endpoint, timeout: SimpleNamespace(
                contexts=[
                    SimpleNamespace(pages=[FakePage(fails=False), FakePage(fails=True)])
                ]
            )
        ),
        stop=lambda: stopped.append(True),
    )
    fake_manager = SimpleNamespace(start=lambda: fake_playwright)
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: fake_manager,
    )

    with pytest.raises(OSWorldActiveTabProbeError, match="不完整"):
        _read_page_payloads_with_playwright("http://127.0.0.1:59222")
    assert stopped == [True]


def test_page_script_keeps_closed_world_and_strong_block_boundaries() -> None:
    """锁定 DOM 脚本的闭集、屏外项与强阻塞信号边界。"""

    assert "selectedRoot.children" in _PAGE_OBSERVATION_SCRIPT
    assert "googleFilterStateLists" in _PAGE_OBSERVATION_SCRIPT
    assert "captchaChallengeObserved" in _PAGE_OBSERVATION_SCRIPT
    assert "consentSurfaceObserved" in _PAGE_OBSERVATION_SCRIPT
    assert "rect.top < window.innerHeight" not in _PAGE_OBSERVATION_SCRIPT
    assert "rect.left < window.innerWidth" not in _PAGE_OBSERVATION_SCRIPT
