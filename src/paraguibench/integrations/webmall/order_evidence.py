"""WebMall Attempt 级订单快照、闭集差分与全局租约生命周期。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
    CheckoutObservationBatch,
    ObservedCheckoutOrder,
)

OrderKey = tuple[str, str]
OrderSnapshot = dict[OrderKey, ObservedCheckoutOrder]


class WebMallOrderEvidenceContractError(RuntimeError):
    """表示 WebMall 特权订单快照不完整、矛盾或违反生命周期。"""


class WebMallOrderEvidenceSource(Protocol):
    """定义固定实现的只读 WooCommerce 订单证据接口。"""

    def read_orders(
        self,
        logical_store_id: str,
    ) -> tuple[ObservedCheckoutOrder, ...]:
        """读取一个 logical store 当前全部相关状态订单。

        输入参数：
            logical_store_id：固定环境清单中的 WebMall store identity。
        输出返回值：
            含 completed、processing、pending、failed、cancelled 与
            refunded 状态的完整订单元组，以及 canonical 商品、支付语义和
            账单资料；profile 值仅供可信 evaluator 内存比较，不得写入
            RunStore、URL 或诊断输出。
        """


class WebMallGlobalAttemptLease(Protocol):
    """定义共享 WebMall backend 的跨 Attempt 独占租约接口。"""

    def acquire(self) -> None:
        """在读取 baseline 前获取全局独占租约。

        输入参数：
            无。
        输出返回值：
            无；失败必须抛异常，不能退化为进程内非独占运行。
        """

    def release(self) -> None:
        """在 final snapshot 完成后释放全局独占租约。

        输入参数：
            无。
        输出返回值：
            无；释放失败必须抛异常供 runtime 记录基础设施错误。
        """

    def assert_held(self) -> None:
        """验证当前进程仍持有同一 fencing ownership。

        输入参数：
            无。
        输出返回值：
            无；TTL 过期、ownership 改变或协调器不可达时必须抛异常。
        """


class WebMallOrderEvidenceSession:
    """在一个 Attempt 内持有全局租约并计算四店订单增量。"""

    def __init__(
        self,
        *,
        source: WebMallOrderEvidenceSource,
        lease: WebMallGlobalAttemptLease,
    ) -> None:
        """绑定只读证据源与跨 host 全局租约实现。

        输入参数：
            source：必须完整读取四个 logical store 的特权只读数据源。
            lease：由部署层提供的跨进程/跨 host WebMall 独占租约；本模块
                不以本地线程锁冒充全局协调。
        输出返回值：
            无；构造阶段不读取商店、不获取租约。
        异常：
            TypeError：source 或 lease 缺少所需可调用接口。
        """

        if not callable(getattr(source, "read_orders", None)):
            raise TypeError("WebMall evidence source 缺少只读订单接口")
        if any(
            not callable(getattr(lease, method_name, None))
            for method_name in ("acquire", "assert_held", "release")
        ):
            raise TypeError("WebMall global lease 接口不完整")
        self._source = source
        self._lease = lease
        self._baseline: OrderSnapshot | None = None
        self._observation: CheckoutObservationBatch | None = None
        self._capture_attempted = False
        self._capture_error: BaseException | None = None
        self._lease_held = False
        self._closed = False

    @property
    def logical_store_ids(self) -> tuple[str, ...]:
        """返回本 session 承诺扫描的固定 WebMall store 闭集。

        输入参数：
            无。
        输出返回值：
            按环境协议顺序排列的四个 logical store ID；该属性
            仅公开稳定 identity，不触发租约或商店 I/O。
        """

        return WEBMALL_LOGICAL_STORE_IDS

    @property
    def observation(self) -> CheckoutObservationBatch | None:
        """返回已成功捕获的 Attempt 新增订单闭包。

        输入参数：
            无。
        输出返回值：
            ``capture_final`` 或 ``close`` 成功后返回不可变批次，否则为
            ``None``；访问本属性不触发 I/O。
        """

        return self._observation

    @property
    def baseline_captured(self) -> bool:
        """说明四店 baseline 是否已完整冻结。

        输入参数：
            无。
        输出返回值：
            baseline 成功读取并验证后为 ``True``，否则为 ``False``。
        """

        return self._baseline is not None

    @property
    def capture_attempted(self) -> bool:
        """说明 final snapshot 是否已经尝试过一次。

        输入参数：
            无。
        输出返回值：
            成功或失败的首次 final capture 开始后均为 ``True``；环境 close
            据此避免重复执行特权读取。
        """

        return self._capture_attempted

    def acquire(self) -> None:
        """在任何 WebMall reset/prepare 副作用前获取全局租约。

        输入参数：
            无。
        输出返回值：
            无；成功后调用方可在租约内准备共享 backend。
        异常：
            WebMallOrderEvidenceContractError：session 已使用或重复获取。
            租约异常：原样传播。
        """

        if self._closed or self._baseline is not None or self._lease_held:
            raise WebMallOrderEvidenceContractError(
                "WebMall evidence session 不能重复 acquire"
            )
        self._lease.acquire()
        self._lease_held = True

    def capture_baseline(self) -> None:
        """在已持有租约时读取四个 store 的完整订单 baseline。

        输入参数：
            无。
        输出返回值：
            无；成功后 Agent 才可开始产生 WebMall 副作用。
        异常：
            WebMallOrderEvidenceContractError：未获取租约、重复 baseline 或
                权威快照不合法。
            证据源/租约异常：原样传播；失败时仍尝试释放租约。
        """

        if self._closed or not self._lease_held or self._baseline is not None:
            raise WebMallOrderEvidenceContractError(
                "WebMall baseline 需要已获取且未使用的全局租约"
            )
        try:
            baseline = self._read_complete_snapshot()
        except BaseException as capture_error:
            self._release_after_failed_begin(capture_error)
            raise
        self._baseline = baseline

    def begin(self) -> None:
        """获取全局租约并读取四个 store 的完整订单 baseline。

        输入参数：
            无。
        输出返回值：
            无；成功后 Agent 才可开始产生 WebMall 副作用。
        异常：
            WebMallOrderEvidenceContractError：session 被复用或快照不合法。
            证据源/租约异常：原样传播；baseline 失败时仍尝试释放租约。
        """

        self.acquire()
        self.capture_baseline()

    def assert_held(self) -> None:
        """为一次即将执行的 Agent controller 操作复核并续期。

        输入参数：
            无。
        输出返回值：
            无；权威租约确认同一 fencing ownership 后返回。
        异常：
            WebMallOrderEvidenceContractError：baseline 尚未冻结、final 已
                开始或 session 已关闭，因此不允许新的 GUI 副作用。
            租约异常：原样传播；调用方必须 fail closed。
        """

        if (
            self._closed
            or not self._lease_held
            or self._baseline is None
            or self._capture_attempted
        ):
            raise WebMallOrderEvidenceContractError(
                "WebMall controller 操作需要未取终态的活跃租约"
            )
        self._lease.assert_held()

    def capture_final(self) -> CheckoutObservationBatch:
        """在租约仍持有时读取终态并计算 Attempt 新增订单闭包。

        输入参数：
            无。
        输出返回值：
            ``complete=True`` 的四店 baseline 差分；重复调用返回同一对象。
        异常：
            WebMallOrderEvidenceContractError：未 begin、session 已关闭、订单
                消失/改变或快照结构不合法。
            证据源异常：原样传播并缓存；随后 ``close`` 只负责释放租约，
                不重复执行有副作用风险的特权读取。
        """

        if self._observation is not None:
            return self._observation
        if self._capture_attempted:
            if self._capture_error is None:
                raise WebMallOrderEvidenceContractError(
                    "WebMall final capture 状态不一致"
                )
            raise self._capture_error
        if self._closed or not self._lease_held or self._baseline is None:
            raise WebMallOrderEvidenceContractError(
                "WebMall evidence session 尚未 begin 或已经关闭"
            )

        self._capture_attempted = True
        try:
            final_snapshot = self._read_complete_snapshot()
            observation = _derive_observation(
                baseline=self._baseline,
                final_snapshot=final_snapshot,
            )
        except BaseException as error:
            self._capture_error = error
            raise
        self._observation = observation
        return observation

    def close(self) -> CheckoutObservationBatch | None:
        """补拍缺失终态、释放全局租约并关闭本次 session。

        输入参数：
            无。
        输出返回值：
            已成功捕获的 observation；begin 从未成功时或捕获失败时为
            ``None``。
        异常：
            首次 final capture 或租约释放失败时原样传播。若 evaluator 已经
            调用 ``capture_final`` 并见到错误，close 不重复抛同一错误，只
            确保租约释放；这样评价错误不会被伪装成环境清理错误。
        """

        if self._closed:
            return self._observation
        capture_error: BaseException | None = None
        if (
            self._lease_held
            and self._baseline is not None
            and not self._capture_attempted
        ):
            try:
                self.capture_final()
            except BaseException as error:
                capture_error = error

        release_error: BaseException | None = None
        if self._lease_held:
            try:
                self._lease.release()
            except BaseException as error:
                release_error = error
            finally:
                self._lease_held = False
        self._closed = True

        if capture_error is not None and release_error is not None:
            raise ExceptionGroup(
                "WebMall final capture 与租约释放同时失败",
                [capture_error, release_error],
            )
        if capture_error is not None:
            raise capture_error
        if release_error is not None:
            raise release_error
        return self._observation

    def _read_complete_snapshot(self) -> OrderSnapshot:
        """按固定顺序读取并验证全部四店权威订单闭包。

        输入参数：
            无。
        输出返回值：
            以 ``(logical_store_id, order_identity)`` 为键的完整快照。
        异常：
            WebMallOrderEvidenceContractError：返回值不是订单元组、store
                错配、订单类型无效或权威快照内身份重复。
            证据源异常：原样传播，调用方据此产生 evaluator/infra error。
        """

        snapshot: OrderSnapshot = {}
        self._lease.assert_held()
        for store_id in WEBMALL_LOGICAL_STORE_IDS:
            orders = self._source.read_orders(store_id)
            if not isinstance(orders, tuple):
                raise WebMallOrderEvidenceContractError(
                    "WebMall evidence source 必须返回订单元组"
                )
            for order in orders:
                if not isinstance(order, ObservedCheckoutOrder):
                    raise WebMallOrderEvidenceContractError(
                        "WebMall evidence source 返回非法订单类型"
                    )
                if order.logical_store_id != store_id:
                    raise WebMallOrderEvidenceContractError(
                        "WebMall evidence source 的 logical store 错配"
                    )
                key = (store_id, order.order_identity)
                if key in snapshot:
                    raise WebMallOrderEvidenceContractError(
                        "WebMall 权威订单快照存在重复身份"
                    )
                snapshot[key] = order
        self._lease.assert_held()
        return snapshot

    def _release_after_failed_begin(
        self,
        capture_error: BaseException,
    ) -> None:
        """在 baseline 失败后释放租约并保留双重失败信息。

        输入参数：
            capture_error：baseline 读取或契约验证的原始异常。
        输出返回值：
            无；租约正常释放后交由调用方继续抛原异常。
        异常：
            ExceptionGroup：baseline 与租约释放同时失败。
        """

        try:
            self._lease.release()
        except BaseException as release_error:
            raise ExceptionGroup(
                "WebMall baseline capture 与租约释放同时失败",
                [capture_error, release_error],
            ) from capture_error
        finally:
            self._lease_held = False
            self._closed = True


def _derive_observation(
    *,
    baseline: Mapping[OrderKey, ObservedCheckoutOrder],
    final_snapshot: Mapping[OrderKey, ObservedCheckoutOrder],
) -> CheckoutObservationBatch:
    """验证 baseline 单调性并生成确定性新增订单批次。

    输入参数：
        baseline：Agent 启动前的四店完整订单映射。
        final_snapshot：Agent 结束后的四店完整订单映射。
    输出返回值：
        只含新订单、按 store 和订单身份排序的完整 checkout observation。
    异常：
        WebMallOrderEvidenceContractError：既有订单身份在终态消失。
    """

    missing_keys = set(baseline) - set(final_snapshot)
    if missing_keys:
        raise WebMallOrderEvidenceContractError(
            "WebMall baseline 订单在 final snapshot 中消失"
        )
    new_keys = sorted(set(final_snapshot) - set(baseline))
    return CheckoutObservationBatch(
        store_universe_id=WEBMALL_STORE_UNIVERSE_ID,
        scanned_store_ids=WEBMALL_LOGICAL_STORE_IDS,
        complete=True,
        orders=tuple(final_snapshot[key] for key in new_keys),
    )
