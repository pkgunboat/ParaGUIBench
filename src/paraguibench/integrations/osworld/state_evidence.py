"""OSWorld Chrome profile/active-tab 的受控 setup 与证据采集入口。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import PurePosixPath
import time
from typing import Any

from paraguibench.integrations.osworld.state_contracts import (
    ChromeProfileNameObservation,
    GoogleShoppingActiveTabObservation,
)


CHROME_PROFILE_NAME_PROTOCOL_ID = "paraguibench.osworld.chrome-profile-name.v1"
GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID = (
    "paraguibench.osworld.google-shopping-active-tab.v1"
)
_PREFERENCES_MAX_BYTES = 1024 * 1024
_PROFILE_BINDING = {
    "task_id": "Operation-WebOperate-Settings-001",
    "evaluation_mode": "osworld_profile_state",
    "profile_state_adapter": "chrome_profile_name_v1",
    "vm_aggregation": "any_complete",
}
_ACTIVE_TAB_BINDING = {
    "task_id": "Operation-WebOperate-WebNavigate-009",
    "evaluation_mode": "osworld_active_tab",
    "active_tab_adapter": "google_shopping_selected_filters_v1",
    "vm_aggregation": "any_complete",
}


class OSWorldStateEvidenceError(RuntimeError):
    """表示状态 setup、Preferences 或 active-tab probe 无法可靠完成。"""


class OSWorldChromeStateEvidenceSource:
    """实现单 VM Chrome profile 证据并预留受控 active-tab probe seam。"""

    def __init__(
        self,
        *,
        waiter: Callable[[float], None] = time.sleep,
        active_tab_loader: (
            Callable[[Any], GoogleShoppingActiveTabObservation] | None
        ) = None,
    ) -> None:
        """构造不访问 guest 或浏览器的状态 evidence source。

        输入参数：
            waiter：Chrome 同步重启后的等待函数；测试可注入无等待 fake。
            active_tab_loader：可选 CDP probe；省略时 active-tab 捕获
                fail-closed，直到生产 probe 完成装配。
        输出返回值：
            无；保存受控依赖供 ``prepare``/``capture`` 使用。
        """

        if not callable(waiter):
            raise TypeError("waiter 必须可调用")
        if active_tab_loader is not None and not callable(active_tab_loader):
            raise TypeError("active_tab_loader 必须可调用")
        self._waiter = waiter
        self._active_tab_loader = active_tab_loader

    def prepare(self, task: Mapping[str, Any], controller: Any) -> None:
        """按两个固定 task metadata 执行 allowlist-only Chrome setup。

        输入参数：
            task：可信 canonical task；只有两个显式 state mode 会触发动作。
            controller：实现结构化 ``launch`` 的当前单 VM controller。
        输出返回值：
            无；普通非 state task 不执行任何额外 setup。
        异常：
            OSWorldStateEvidenceError：state mode 的 task metadata 漂移，或
                controller 无法接受固定命令。
        """

        if not isinstance(task, Mapping):
            raise TypeError("task 必须是 Mapping")
        mode = task.get("evaluation_mode")
        if mode not in {"osworld_profile_state", "osworld_active_tab"}:
            return
        try:
            if mode == "osworld_profile_state":
                _require_binding(task, _PROFILE_BINDING)
                controller.launch(["google-chrome", "--remote-debugging-port=1337"])
                controller.wait_for_chrome_cdp(port=1337, timeout=15.0)
            else:
                _require_binding(task, _ACTIVE_TAB_BINDING)
                controller.launch(
                    [
                        "google-chrome",
                        "--remote-debugging-port=1337",
                        "--lang=en-US",
                    ]
                )
                controller.wait_for_chrome_cdp(port=1337, timeout=15.0)
            controller.launch(
                [
                    "socat",
                    "tcp-listen:9222,fork,reuseaddr",
                    "tcp:localhost:1337",
                ]
            )
            if mode == "osworld_active_tab":
                controller.launch(
                    [
                        "google-chrome",
                        "--new-tab",
                        ("https://www.google.com/search?tbm=shop&hl=en&gl=us"),
                    ]
                )
                controller.activate_window("Google Chrome")
        except OSWorldStateEvidenceError:
            raise
        except Exception:
            raise OSWorldStateEvidenceError("OSWorld state setup 无法完成") from None

    def capture(
        self,
        protocol_id: str,
        controller: Any,
    ) -> tuple[object, ...]:
        """按版本化协议捕获单台 VM 的不可变状态 observation。

        输入参数：
            protocol_id：profile-name 或 Google Shopping active-tab 协议。
            controller：当前仍存活的单 VM controller。
        输出返回值：
            仅含当前 VM 一个 observation 的 tuple；多 VM runtime 应在更高
            层组合各 VM tuple，不能在本 source 内拼接字段。
        异常：
            OSWorldStateEvidenceError：协议未知、Preferences 无法可靠解析，
                或 active-tab probe 尚未装配/返回类型无效。
        """

        if protocol_id == CHROME_PROFILE_NAME_PROTOCOL_ID:
            return (self._capture_profile_name(controller),)
        if protocol_id == GOOGLE_SHOPPING_ACTIVE_TAB_PROTOCOL_ID:
            if self._active_tab_loader is None:
                raise OSWorldStateEvidenceError("OSWorld active-tab probe 尚未装配")
            try:
                observation = self._active_tab_loader(controller)
            except Exception:
                raise OSWorldStateEvidenceError(
                    "OSWorld active-tab probe 无法完成"
                ) from None
            if not isinstance(
                observation,
                GoogleShoppingActiveTabObservation,
            ):
                raise OSWorldStateEvidenceError("OSWorld active-tab probe 返回类型无效")
            return (observation,)
        raise OSWorldStateEvidenceError("OSWorld state protocol 不受支持")

    def _capture_profile_name(
        self,
        controller: Any,
    ) -> ChromeProfileNameObservation:
        """同步 Chrome 落盘并读取固定默认 profile 的 Preferences。

        输入参数：
            controller：当前 VM 的结构化命令与有界文件传输接口。
        输出返回值：
            文件可解析时返回完整 observation；缺少 ``profile.name`` 仍是
            完整 observation，由纯 evaluator 判正常 Agent FAIL。
        异常：
            OSWorldStateEvidenceError：关闭/重启、动态 home、文件传输、JSON
                或字段 schema 无法可靠确定。
        """

        try:
            stop_result = controller.execute(["pkill", "chrome"])
            if stop_result.returncode not in {0, 1}:
                raise OSWorldStateEvidenceError("Chrome 同步关闭失败")
            controller.wait_for_chrome_exit(timeout=15.0)
            controller.launch(["google-chrome", "--remote-debugging-port=1337"])
            controller.wait_for_chrome_cdp(port=1337, timeout=15.0)
            self._waiter(3.0)
            desktop_path = PurePosixPath(controller.get_desktop_path())
            if not desktop_path.is_absolute() or ".." in desktop_path.parts:
                raise OSWorldStateEvidenceError("guest home 无法可靠推导")
            preferences_path = (
                desktop_path.parent
                / ".config"
                / "google-chrome"
                / "Default"
                / "Preferences"
            )
            content = controller.read_file(
                str(preferences_path),
                max_bytes=_PREFERENCES_MAX_BYTES,
            )
        except OSWorldStateEvidenceError:
            raise
        except Exception:
            raise OSWorldStateEvidenceError("Chrome Preferences 无法可靠读取") from None

        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise OSWorldStateEvidenceError("Chrome Preferences 无法解析") from None
        if not isinstance(payload, dict):
            raise OSWorldStateEvidenceError("Chrome Preferences schema 无效")
        profile = payload.get("profile", {})
        if not isinstance(profile, dict):
            raise OSWorldStateEvidenceError("Chrome Preferences schema 无效")
        name = profile.get("name")
        if name is not None and not isinstance(name, str):
            raise OSWorldStateEvidenceError("Chrome Preferences schema 无效")
        return ChromeProfileNameObservation(
            profile_name=name,
            complete=True,
        )


def _require_binding(
    task: Mapping[str, Any],
    expected: Mapping[str, str],
) -> None:
    """验证 state task 与固定 adapter/setup spec 精确绑定。

    输入参数：
        task：可信 canonical task metadata。
        expected：协议冻结的字段→值闭集。
    输出返回值：
        无；全部字段精确相等时返回。
    异常：
        OSWorldStateEvidenceError：任一 metadata 缺失或漂移。
    """

    if any(task.get(field) != value for field, value in expected.items()):
        raise OSWorldStateEvidenceError(
            "OSWorld state task metadata 与 setup spec 不一致"
        )
