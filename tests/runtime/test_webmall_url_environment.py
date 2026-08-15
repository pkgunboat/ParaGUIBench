"""WebMall URL-multiset 无特权 runtime 环境测试。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from paraguibench.integrations.webmall import WebMallURLRegistry
from paraguibench.runtime.webmall_url_environment import (
    WebMallURLTaskEnvironment,
    WebMallURLTaskEnvironmentError,
)


class _RawEnvironment:
    """记录 URL wrapper 应原样透传的底层 GUI 生命周期。"""

    def __init__(self) -> None:
        """初始化不访问外部资源的调用记录器。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls: list[object] = []
        self.controller = object()
        self.guest_shared_dir = "/home/oai/share"

    def start(self) -> None:
        """记录底层环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("start")

    def prepare(self, task: Mapping[str, Any]) -> None:
        """记录底层环境收到的任务投影。

        输入参数：
            task：AttemptRunner 传入的 Agent 任务投影。
        输出返回值：
            无。
        """

        self.calls.append(("prepare", task))

    def close(self) -> None:
        """记录底层环境关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("close")


def _registry() -> WebMallURLRegistry:
    """构造四店公开测试 origin 注册表。

    输入参数：无。
    输出返回值：只包含 ``example.invalid`` origin 的注册表。
    """

    return WebMallURLRegistry(
        {
            f"store-{index}": f"https://shop-{index}.example.invalid"
            for index in range(1, 5)
        }
    )


def test_url_environment_transparently_wraps_gui_without_order_evidence() -> None:
    """验证 URL 任务只透传 GUI 生命周期并提供报告规范化。

    输入参数：
        无；使用合成 GUI 环境和四店 registry。
    输出返回值：
        无；断言 start/prepare/close/controller/shared-dir 透传，
        且报告 URL 被转为 logical 多集合。
    """

    raw = _RawEnvironment()
    registry = _registry()
    environment = WebMallURLTaskEnvironment(
        environment=raw,
        registry=registry,
    )
    task = {"task_id": "Operation-OnlineShopping-SingleProductSearch-001"}

    environment.start()
    environment.prepare(task)
    logical_urls = environment.canonicalize_reported_product_urls(
        "https://shop-2.example.invalid/product/item-a"
        "###https://shop-2.example.invalid/product/item-a"
    )
    environment.close()

    assert raw.calls == ["start", ("prepare", task), "close"]
    assert environment.controller is raw.controller
    assert environment.guest_shared_dir == "/home/oai/share"
    assert environment.webmall_url_registry() is registry
    assert logical_urls == (
        "webmall://store-2/product/item-a",
        "webmall://store-2/product/item-a",
    )
    assert not hasattr(environment, "checkout_observation")


def test_url_environment_rejects_report_before_prepare() -> None:
    """验证报告规范化不能越过 GUI 环境生命周期。

    输入参数：
        无；构造但不启动、不准备 URL 环境。
    输出返回值：
        无；断言固定类型异常，且不读取外部系统。
    """

    environment = WebMallURLTaskEnvironment(
        environment=_RawEnvironment(),
        registry=_registry(),
    )

    with pytest.raises(WebMallURLTaskEnvironmentError, match="已准备"):
        environment.canonicalize_reported_product_urls(
            "https://unknown.example.invalid/private"
        )
