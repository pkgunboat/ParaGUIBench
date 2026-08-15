"""WebMall URL-multiset 任务的无特权 GUI 环境包装。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from paraguibench.integrations.webmall import (
    WebMallURLRegistry,
    extract_reported_logical_product_urls,
)


class WebMallURLTaskEnvironmentError(RuntimeError):
    """表示 WebMall URL 环境的生命周期或依赖契约无效。"""


class WebMallURLTaskEnvironment:
    """透传 GUI 环境并在内存中规范化 WebMall 报告 URL。"""

    def __init__(
        self,
        *,
        environment: Any,
        registry: WebMallURLRegistry,
    ) -> None:
        """绑定底层 GUI 环境和本次部署的四店 registry。

        输入参数：
            environment：实现 ``start/prepare/close/controller`` 的
                底层 OSWorld GUI 环境。
            registry：本次运行固定的 logical store 与 runtime
                origin 双向注册表。
        输出返回值：
            无；构造阶段不启动 VM、不读取 WordPress，也不获取租约。
        异常：
            TypeError：底层环境或 registry 不满足最小契约。
        """

        for method_name in ("start", "prepare", "close"):
            if not callable(getattr(environment, method_name, None)):
                raise TypeError(f"WebMall URL 底层环境缺少 {method_name}")
        if not hasattr(environment, "controller"):
            raise TypeError("WebMall URL 底层环境缺少 controller")
        if not isinstance(registry, WebMallURLRegistry):
            raise TypeError("WebMall URL 环境需要 registry")
        self._environment = environment
        self._registry = registry
        self._started = False
        self._prepared = False
        self._closed = False

    @property
    def controller(self) -> Any:
        """返回 Agent worker 使用的底层 GUI controller。

        输入参数：无。
        输出返回值：底层环境的 controller 对象。
        """

        return self._environment.controller

    @property
    def guest_shared_dir(self) -> str | None:
        """返回底层环境已准备的 guest shared 目录。

        输入参数：无。
        输出返回值：底层公开字符串路径时返回该值，否则
            返回 ``None``。
        """

        value = getattr(self._environment, "guest_shared_dir", None)
        return value if isinstance(value, str) else None

    def start(self) -> None:
        """原样启动底层 GUI 环境。

        输入参数：无。
        输出返回值：无；成功后允许 ``prepare``。
        异常：
            WebMallURLTaskEnvironmentError：重复启动或环境已关闭。
            底层异常：原样传播。
        """

        if self._started or self._closed:
            raise WebMallURLTaskEnvironmentError("WebMall URL 环境不能重复 start")
        self._environment.start()
        self._started = True

    def prepare(self, task: Mapping[str, Any]) -> None:
        """原样把 Agent 任务投影交给底层 GUI 环境准备。

        输入参数：
            task：AttemptRunner 提供的 Agent 任务投影。
        输出返回值：
            无；不采集订单 baseline，不读取 WP-CLI 且不获取租约。
        异常：
            WebMallURLTaskEnvironmentError：生命周期或 task 类型无效。
            底层异常：原样传播。
        """

        if not self._started or self._prepared or self._closed:
            raise WebMallURLTaskEnvironmentError(
                "WebMall URL 环境未启动、已准备或已关闭"
            )
        if not isinstance(task, Mapping):
            raise WebMallURLTaskEnvironmentError("WebMall URL task 必须是 Mapping")
        self._environment.prepare(task)
        self._prepared = True

    def canonicalize_reported_product_urls(
        self,
        final_output: str,
    ) -> tuple[str, ...]:
        """把 Agent 报告中的部署 URL 转为 logical URL 多集合。

        输入参数：
            final_output：Agent terminal action 返回的完整文本。
        输出返回值：
            保留顺序和重复项的 logical URL 元组；未知 origin
            由纯解析层转为固定非法标记。
        异常：
            WebMallURLTaskEnvironmentError：环境尚未准备或已关闭。
        """

        if not self._prepared or self._closed:
            raise WebMallURLTaskEnvironmentError("WebMall URL 报告规范化需要已准备环境")
        return extract_reported_logical_product_urls(
            final_output,
            self._registry,
        )

    def webmall_url_registry(self) -> WebMallURLRegistry:
        """向受信 runtime evaluator 返回构造时的 registry。

        输入参数：无。
        输出返回值：仅在内存中使用的同一
            ``WebMallURLRegistry``；该对象不进入 Agent task 或 RunStore。
        """

        return self._registry

    def close(self) -> None:
        """至多一次关闭已启动的底层 GUI 环境。

        输入参数：无。
        输出返回值：无；未启动或重复关闭时安全返回。
        异常：
            底层关闭异常：原样传播，但 wrapper 仍标记为已关闭。
        """

        if self._closed:
            return
        try:
            if self._started:
                self._environment.close()
        finally:
            self._closed = True
            self._started = False
            self._prepared = False
