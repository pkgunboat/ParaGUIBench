"""OSWorld Chrome 状态 setup 与 evidence source 契约测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.osworld import (
    CHROME_PROFILE_NAME_PROTOCOL_ID,
    GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
    GoogleShoppingActiveTabObservation,
)
from paraguibench.integrations.osworld import CommandResult
from paraguibench.integrations.osworld.state_evidence import (
    OSWorldChromeStateEvidenceSource,
    OSWorldStateEvidenceError,
)


class _StateController:
    """记录固定 setup/capture I/O 的单 VM controller fake。"""

    def __init__(self, preferences: bytes) -> None:
        """保存待返回的 Preferences 内容并初始化调用记录。

        输入参数：
            preferences：``read_file`` 应返回的合成 JSON bytes。
        输出返回值：
            无。
        """

        self.preferences = preferences
        self.launched: list[list[str]] = []
        self.executed: list[list[str]] = []
        self.reads: list[tuple[str, int]] = []
        self.cdp_waits: list[tuple[int, float]] = []
        self.chrome_exit_waits: list[float] = []
        self.activated_windows: list[str] = []

    def get_desktop_path(self) -> str:
        """返回动态 guest home 下的 Desktop 路径。

        输入参数：无。
        输出返回值：合成 guest Desktop 绝对路径。
        """

        return "/guest-profile/Desktop"

    def launch(self, command: list[str]) -> None:
        """记录版本化 setup spec 发出的异步 argv。

        输入参数：
            command：待启动 argv。
        输出返回值：
            无。
        """

        self.launched.append(list(command))

    def execute(self, command: list[str]) -> CommandResult:
        """记录同步 argv 并模拟成功关闭 Chrome。

        输入参数：
            command：待执行 argv。
        输出返回值：
            returncode=0 的固定命令结果。
        """

        self.executed.append(list(command))
        return CommandResult(returncode=0, stdout="", stderr="")

    def read_file(self, guest_path: str, *, max_bytes: int) -> bytes:
        """记录固定 Preferences 路径和上限并返回合成内容。

        输入参数：
            guest_path：evidence source 推导的 guest 文件路径。
            max_bytes：版本化协议的大小上限。
        输出返回值：
            构造时保存的 JSON bytes。
        """

        self.reads.append((guest_path, max_bytes))
        return self.preferences

    def wait_for_chrome_cdp(self, *, port: int, timeout: float) -> None:
        """记录 setup/capture 在继续前等待 guest Chrome CDP。

        输入参数：
            port：guest Chrome 调试端口。
            timeout：最长等待秒数。
        输出返回值：无。
        """

        self.cdp_waits.append((port, timeout))

    def wait_for_chrome_exit(self, *, timeout: float) -> None:
        """记录 profile capture 等待旧 Chrome 完整退出。

        输入参数：
            timeout：最长等待秒数。
        输出返回值：无。
        """

        self.chrome_exit_waits.append(timeout)

    def activate_window(self, window_name: str) -> None:
        """记录结构化 Chrome 窗口激活请求。

        输入参数：
            window_name：固定窗口名。
        输出返回值：无。
        """

        self.activated_windows.append(window_name)


def _profile_task() -> dict[str, object]:
    """返回正式 Chrome profile 任务的 setup 路由字段。

    输入参数：无。
    输出返回值：最小 canonical task 映射。
    """

    return {
        "task_id": "Operation-WebOperate-Settings-001",
        "evaluation_mode": "osworld_profile_state",
        "profile_state_adapter": "chrome_profile_name_v1",
        "vm_aggregation": "any_complete",
    }


def _active_tab_task() -> dict[str, object]:
    """返回正式 Google Shopping 任务的 setup 路由字段。

    输入参数：无。
    输出返回值：最小 canonical task 映射。
    """

    return {
        "task_id": "Operation-WebOperate-WebNavigate-009",
        "evaluation_mode": "osworld_active_tab",
        "active_tab_adapter": "google_shopping_selected_filters_v1",
        "vm_aggregation": "any_complete",
    }


def test_profile_setup_and_capture_use_fixed_commands_and_dynamic_home() -> None:
    """验证 profile 垂直切片无需硬编码用户名或任意 shell。

    输入参数：
        无；使用动态 Desktop path 与合法 Preferences JSON。
    输出返回值：
        无；setup/capture 命令、等待和固定文件读取均符合协议。
    """

    waits: list[float] = []
    controller = _StateController(b'{"profile":{"name":"Thomas"}}')
    source = OSWorldChromeStateEvidenceSource(waiter=waits.append)

    source.prepare(_profile_task(), controller)
    observations = source.capture(
        CHROME_PROFILE_NAME_PROTOCOL_ID,
        controller,
    )

    assert controller.launched[:2] == [
        ["google-chrome", "--remote-debugging-port=1337"],
        [
            "socat",
            "tcp-listen:9222,fork,reuseaddr",
            "tcp:localhost:1337",
        ],
    ]
    assert controller.cdp_waits == [(1337, 15.0), (1337, 15.0)]
    assert controller.chrome_exit_waits == [15.0]
    assert controller.executed == [["pkill", "chrome"]]
    assert controller.launched[2] == [
        "google-chrome",
        "--remote-debugging-port=1337",
    ]
    assert waits == [3.0]
    assert controller.reads == [
        (
            ("/guest-profile/.config/google-chrome/Default/Preferences"),
            1024 * 1024,
        )
    ]
    assert len(observations) == 1
    assert observations[0].profile_name == "Thomas"
    assert observations[0].complete is True


def test_valid_preferences_without_profile_name_remains_complete_observation() -> None:
    """验证合法 JSON 缺目标字段由评价器记 Agent FAIL，而非传输 ERROR。

    输入参数：
        无；使用合法但没有 ``profile.name`` 的 Preferences。
    输出返回值：
        无；capture 返回 ``profile_name=None, complete=True``。
    """

    controller = _StateController(b'{"profile":{}}')
    source = OSWorldChromeStateEvidenceSource(waiter=lambda _seconds: None)

    observation = source.capture(
        CHROME_PROFILE_NAME_PROTOCOL_ID,
        controller,
    )[0]

    assert observation.profile_name is None
    assert observation.complete is True


@pytest.mark.parametrize(
    "preferences",
    [
        b"not-json",
        b"[]",
        b'{"profile":"wrong-schema"}',
        b'{"profile":{"name":123}}',
    ],
)
def test_invalid_preferences_are_evaluator_evidence_errors(
    preferences: bytes,
) -> None:
    """验证损坏或漂移的 Preferences schema 不伪装成名称不匹配。

    输入参数：
        preferences：故意损坏的 JSON 或字段类型。
    输出返回值：
        无；capture 抛不回显内容的 evidence error。
    """

    source = OSWorldChromeStateEvidenceSource(waiter=lambda _seconds: None)

    with pytest.raises(OSWorldStateEvidenceError, match="Preferences"):
        source.capture(
            CHROME_PROFILE_NAME_PROTOCOL_ID,
            _StateController(preferences),
        )


def test_active_tab_setup_opens_fixed_shopping_page_and_uses_one_loader() -> None:
    """验证 active-tab setup 固定英文 Shopping 起始页并冻结单次证据。

    输入参数：
        无；注入不访问浏览器的合成 snapshot loader。
    输出返回值：
        无；capture 返回一个 observation，loader 只调用一次。
    """

    loader_calls: list[object] = []
    expected = GoogleShoppingActiveTabObservation(
        url=("https://www.google.com/search?tbm=shop&q=drip+coffee+maker"),
        locale="en-US",
        filter_surface_observed=True,
        selection_enumeration_complete=True,
        selection_evidence="semantic_google_filter_state_list",
        selected_filter_labels=("Black", "$25 - $60", "On sale"),
    )

    def loader(controller: object) -> GoogleShoppingActiveTabObservation:
        """记录 controller 并返回固定活动页快照。

        输入参数：
            controller：source 捕获时收到的 controller fake。
        输出返回值：
            固定 active-tab observation。
        """

        loader_calls.append(controller)
        return expected

    controller = _StateController(b"{}")
    source = OSWorldChromeStateEvidenceSource(
        waiter=lambda _seconds: None,
        active_tab_loader=loader,
    )

    source.prepare(_active_tab_task(), controller)
    observations = source.capture(
        GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
        controller,
    )

    assert controller.launched[0] == [
        "google-chrome",
        "--remote-debugging-port=1337",
        "--lang=en-US",
    ]
    assert controller.launched[1] == [
        "socat",
        "tcp-listen:9222,fork,reuseaddr",
        "tcp:localhost:1337",
    ]
    assert controller.launched[2] == [
        "google-chrome",
        "--new-tab",
        "https://www.google.com/search?tbm=shop&hl=en&gl=us",
    ]
    assert controller.cdp_waits == [(1337, 15.0)]
    assert controller.activated_windows == ["Google Chrome"]
    assert observations == (expected,)
    assert loader_calls == [controller]


def test_active_tab_capture_without_probe_fails_closed() -> None:
    """验证尚未装配 CDP probe 时 active-tab 不能返回空证据或零分。

    输入参数：
        无；使用未注入 active-tab loader 的生产默认 source。
    输出返回值：
        无；capture 抛固定 evidence error。
    """

    source = OSWorldChromeStateEvidenceSource(waiter=lambda _seconds: None)

    with pytest.raises(OSWorldStateEvidenceError, match="active-tab"):
        source.capture(
            GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID,
            _StateController(b"{}"),
        )


def test_unknown_state_metadata_or_protocol_is_rejected() -> None:
    """验证状态 task adapter 漂移和未知协议均在 I/O 前失败关闭。

    输入参数：
        无；故意修改 profile adapter 并传入未知协议。
    输出返回值：
        无；source 抛配置错误且 controller 未执行命令。
    """

    controller = _StateController(b"{}")
    source = OSWorldChromeStateEvidenceSource(waiter=lambda _seconds: None)

    with pytest.raises(OSWorldStateEvidenceError, match="metadata"):
        source.prepare(
            {**_profile_task(), "profile_state_adapter": "unknown"},
            controller,
        )
    with pytest.raises(OSWorldStateEvidenceError, match="protocol"):
        source.capture("paraguibench.osworld.unknown.v1", controller)
    assert controller.launched == []
    assert controller.executed == []
