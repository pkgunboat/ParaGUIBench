"""组合 GUI 环境、四店订单证据窗口与 WebMall URL registry。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from paraguibench.evaluation.webmall import CheckoutObservationBatch
from paraguibench.integrations.webmall import (
    WEBMALL_LOGICAL_STORE_IDS,
    WebMallOrderEvidenceSession,
    WebMallURLRegistry,
    extract_reported_logical_product_urls,
)


class WebMallTaskEnvironmentError(RuntimeError):
    """表示 WebMall 组合环境生命周期或接口契约被违反。"""


class _LeaseGuardedControllerProxy:
    """以只读透传和每次调用前续期包装 GUI controller。"""

    __slots__ = ("_controller", "_assert_held")

    def __init__(self, controller: Any, assert_held: Any) -> None:
        """绑定 raw controller 与 evidence session ownership guard。

        输入参数：
            controller：仅在本代理内部保存的底层 GUI controller。
            assert_held：每个可调用操作前执行的权威租约
                复核/续期方法。
        输出返回值：
            无；构造后公开表面不提供 raw controller 属性。
        """

        object.__setattr__(self, "_controller", controller)
        object.__setattr__(self, "_assert_held", assert_held)

    def __getattribute__(self, name: str) -> Any:
        """隐藏 proxy 内部 raw controller 与 guard 存储属性。

        输入参数：
            name：请求访问的 proxy 属性名。
        输出返回值：
            非内部属性由常规 Python 属性解析返回。
        异常：
            AttributeError：Agent 企图通过常规属性访问获取 raw
                controller、guard 或包装元数据。
        """

        if name in {
            "_controller",
            "_assert_held",
            "__dict__",
            "__wrapped__",
        }:
            raise AttributeError("controller 内部属性不可用")
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> Any:
        """只读透传非可调用属性，为可调用操作返回延迟 guard。

        输入参数：
            name：Agent 请求的公开 controller 属性名。
        输出返回值：
            非可调用值原样返回；可调用值返回一个在实际
            调用时才复核租约的包装函数。
        异常：
            AttributeError：私有名、raw controller 别名或底层缺失属性。
        """

        if (
            not isinstance(name, str)
            or name.startswith("_")
            or name in {"controller", "raw_controller", "wrapped"}
        ):
            raise AttributeError("controller 属性不可用")
        controller = object.__getattribute__(self, "_controller")
        value = getattr(controller, name)
        if value is controller:
            return self
        if not callable(value):
            return value

        def guarded_operation(*args: Any, **kwargs: Any) -> Any:
            """在真实 controller 操作前即时复核并续期租约。

            输入参数：
                args/kwargs：Agent 传给底层 controller 方法的参数。
            输出返回值：
                底层 controller 操作的原始返回值。
            异常：
                租约或 controller 异常：原样传播；租约失败时绝不
                    执行底层 GUI 副作用。
            """

            assert_held = object.__getattribute__(self, "_assert_held")
            assert_held()
            current_controller = object.__getattribute__(self, "_controller")
            operation = getattr(current_controller, name)
            if not callable(operation):
                raise WebMallTaskEnvironmentError(
                    "WebMall controller 操作在调用前发生变化"
                )
            return operation(*args, **kwargs)

        return guarded_operation

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """在 raw controller 本身可调用时应用相同的租约 guard。

        输入参数：
            args/kwargs：Agent 传给可调用 controller 对象的参数。
        输出返回值：
            raw controller 可调用接口的原始返回值。
        异常：
            TypeError：raw controller 不可调用。
            租约异常：在底层调用前原样传播。
        """

        controller = object.__getattribute__(self, "_controller")
        if not callable(controller):
            raise TypeError("controller 不可调用")
        assert_held = object.__getattribute__(self, "_assert_held")
        assert_held()
        return controller(*args, **kwargs)

    def __setattr__(self, name: str, value: Any) -> None:
        """拒绝 Agent 通过 proxy 覆盖 controller 状态或 guard。

        输入参数：
            name/value：企图赋值的属性名与值。
        输出返回值：
            不返回。
        异常：
            AttributeError：始终拒绝赋值。
        """

        del name, value
        raise AttributeError("WebMall controller proxy 为只读")

    def __delattr__(self, name: str) -> None:
        """拒绝 Agent 通过 proxy 删除 controller 状态或 guard。

        输入参数：
            name：企图删除的属性名。
        输出返回值：
            不返回。
        异常：
            AttributeError：始终拒绝删除。
        """

        del name
        raise AttributeError("WebMall controller proxy 为只读")


class WebMallTaskEnvironment:
    """在全局 WebMall 租约内管理 GUI 环境准备、取证与清理。"""

    def __init__(
        self,
        *,
        environment: Any,
        evidence_session: WebMallOrderEvidenceSession,
        registry: WebMallURLRegistry,
    ) -> None:
        """绑定底层 GUI 环境、订单证据 session 与地址注册表。

        输入参数：
            environment：拥有 ``start/prepare/close/controller`` 的底层任务
                环境；WebMall reset 若存在，由其 ``prepare`` 在租约内执行。
            evidence_session：四店 baseline/final 与全局 lease 的状态机。
            registry：本次部署固定 logical store 到 runtime origin 的映射。
        输出返回值：
            无；构造阶段不启动环境、不获取租约或读取商店。
        异常：
            TypeError：任一依赖缺少必需接口或类型不匹配。
        """

        for method_name in ("start", "prepare", "close"):
            if not callable(getattr(environment, method_name, None)):
                raise TypeError(f"WebMall 底层环境缺少 {method_name}")
        if not hasattr(environment, "controller"):
            raise TypeError("WebMall 底层环境缺少 controller")
        for method_name in (
            "acquire",
            "assert_held",
            "capture_baseline",
            "capture_final",
            "close",
        ):
            if not callable(getattr(evidence_session, method_name, None)):
                raise TypeError(f"WebMall evidence session 缺少 {method_name}")
        if not isinstance(registry, WebMallURLRegistry):
            raise TypeError("WebMall 环境需要 URL registry")
        evidence_store_ids = getattr(evidence_session, "logical_store_ids", None)
        if (
            not isinstance(evidence_store_ids, tuple)
            or evidence_store_ids != WEBMALL_LOGICAL_STORE_IDS
            or registry.logical_store_ids != WEBMALL_LOGICAL_STORE_IDS
            or registry.logical_store_ids != evidence_store_ids
        ):
            raise TypeError("WebMall registry、evidence 与固定四店 universe 不一致")
        self._environment = environment
        self._evidence_session = evidence_session
        self._registry = registry
        self._controller_proxy = _LeaseGuardedControllerProxy(
            environment.controller,
            evidence_session.assert_held,
        )
        self._started = False
        self._prepared = False
        self._evidence_acquired = False
        self._evidence_released = False
        self._closed = False

    @property
    def controller(self) -> Any:
        """返回 Agent worker 使用的底层 GUI controller。

        输入参数：
            无。
        输出返回值：
            底层环境的 controller 对象。
        """

        return self._controller_proxy

    @property
    def guest_shared_dir(self) -> str | None:
        """返回底层环境准备出的 guest shared 目录。

        输入参数：
            无。
        输出返回值：
            底层环境公开字符串路径时返回该值，否则为 ``None``。
        """

        value = getattr(self._environment, "guest_shared_dir", None)
        return value if isinstance(value, str) else None

    def start(self) -> None:
        """启动本 Attempt 独占的底层 GUI 环境。

        输入参数：
            无。
        输出返回值：
            无；成功后允许 ``prepare``。
        异常：
            WebMallTaskEnvironmentError：重复启动或环境已关闭。
            底层启动异常：原样传播；``close`` 仍会尝试清理已拥有资源。
        """

        if self._started or self._closed:
            raise WebMallTaskEnvironmentError("WebMall 环境不能重复 start")
        self._started = True
        self._environment.start()

    def prepare(self, task: Mapping[str, Any]) -> None:
        """先获取全局租约，再准备 backend/GUI 并冻结四店 baseline。

        输入参数：
            task：AttemptRunner 提供的可信 canonical task。
        输出返回值：
            无；成功返回后 Agent 才能产生订单副作用。
        异常：
            WebMallTaskEnvironmentError：生命周期或 task 类型无效。
            底层准备、baseline 或租约异常：完成可行的租约释放后传播。
        """

        if not self._started or self._prepared or self._closed:
            raise WebMallTaskEnvironmentError("WebMall 环境未启动、已准备或已经关闭")
        if not isinstance(task, Mapping):
            raise WebMallTaskEnvironmentError("WebMall task 必须是 Mapping")
        self._evidence_session.acquire()
        self._evidence_acquired = True
        try:
            self._environment.prepare(task)
            self._evidence_session.capture_baseline()
        except BaseException as prepare_error:
            cleanup_error = self._release_evidence_once()
            if cleanup_error is not None:
                raise ExceptionGroup(
                    "WebMall prepare 与租约释放同时失败",
                    [prepare_error, cleanup_error],
                ) from prepare_error
            raise
        self._prepared = True

    def checkout_observation(self) -> CheckoutObservationBatch:
        """在环境仍存活且租约仍持有时冻结并返回订单增量。

        输入参数：
            无。
        输出返回值：
            Attempt baseline 之后四店新增订单的完整不可变批次。
        异常：
            WebMallTaskEnvironmentError：环境尚未准备或已经关闭。
            evidence session 异常：原样传播，由 AttemptRunner 记 evaluator
                ``ERROR``，随后 close 仍负责环境清理和租约释放。
        """

        if not self._prepared or self._closed:
            raise WebMallTaskEnvironmentError(
                "WebMall checkout observation 需要已准备环境"
            )
        return self._evidence_session.capture_final()

    def canonicalize_reported_product_urls(
        self,
        final_output: str,
    ) -> tuple[str, ...]:
        """把 Agent 报告中的部署 URL 转为 logical URL 多集合。

        输入参数：
            final_output：Agent 最终文本，仅在内存中传入严格解析器。
        输出返回值：
            保留顺序与重复项、未知值已替换为固定非法标记的 logical URL
            元组。
        """

        if not self._prepared or self._closed:
            raise WebMallTaskEnvironmentError("WebMall report 解析需要已准备环境")
        return extract_reported_logical_product_urls(
            final_output,
            self._registry,
        )

    def close(self) -> None:
        """补拍订单终态、关闭底层环境并最后释放全局租约。

        输入参数：
            无。
        输出返回值：
            无；幂等关闭。
        异常：
            final capture、底层 close 或 lease release 的单一异常原样传播；
            多阶段同时失败时以 ``ExceptionGroup`` 完整保留，避免资源错误
            静默覆盖主错误。
        """

        if self._closed:
            return
        errors: list[BaseException] = []
        if self._prepared and not bool(
            getattr(self._evidence_session, "capture_attempted", False)
        ):
            try:
                self._evidence_session.capture_final()
            except BaseException as error:
                errors.append(error)
        if self._started:
            try:
                self._environment.close()
            except BaseException as error:
                errors.append(error)
        release_error = self._release_evidence_once()
        if release_error is not None:
            errors.append(release_error)

        self._closed = True
        self._started = False
        self._prepared = False
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("WebMall 环境关闭存在多个错误", errors)

    def _release_evidence_once(self) -> BaseException | None:
        """至多一次关闭 evidence session 并捕获释放异常。

        输入参数：
            无。
        输出返回值：
            正常释放返回 ``None``；失败返回原异常，由调用方与其他生命周期
            错误统一处理。
        """

        if not self._evidence_acquired or self._evidence_released:
            return None
        try:
            self._evidence_session.close()
        except BaseException as error:
            return error
        finally:
            self._evidence_released = True
        return None
