"""WebMall Attempt 级订单基线、终态采集和全局租约契约测试。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from paraguibench.evaluation.webmall import (
    ObservedCheckoutOrder,
    ObservedCheckoutProfile,
    ObservedCheckoutProduct,
)
from paraguibench.integrations.webmall.order_evidence import (
    WEBMALL_LOGICAL_STORE_IDS,
    WebMallOrderEvidenceContractError,
    WebMallOrderEvidenceSession,
)


def _order(
    store_id: str,
    order_id: str,
    *products: tuple[str, int],
) -> ObservedCheckoutOrder:
    """构造一个不含订单 URL 或商品显示名的完整测试订单。

    输入参数：
        store_id：订单所属 logical store。
        order_id：仅在可信内存中使用的稳定订单身份。
        products：``(canonical_slug, quantity)`` 商品行。
    输出返回值：
        含 synthetic billing profile 但只供可信内存使用的不可变订单。
    """

    return ObservedCheckoutOrder(
        logical_store_id=store_id,
        order_identity=order_id,
        products=tuple(
            ObservedCheckoutProduct(canonical_slug=slug, quantity=quantity)
            for slug, quantity in products
        ),
        checkout_state="completed",
        payment_kind="credit_card",
        billing_profile=ObservedCheckoutProfile(
            full_name="ParaGUI Test User",
            email="checkout-v1@example.invalid",
            address_line_1="100 Benchmark Avenue",
            postcode="94107",
            city="San Francisco",
            state="CA",
            country="US",
        ),
    )


class _Lease:
    """记录全局 WebMall Attempt 租约的合成实现。"""

    def __init__(
        self,
        calls: list[str],
        *,
        fail_assert_at: int | None = None,
    ) -> None:
        """绑定共享调用记录。

        输入参数：
            calls：测试断言生命周期顺序的可变列表。
            fail_assert_at：从 1 开始计数的 ownership 检查失败位置。
        输出返回值：
            无。
        """

        self._calls = calls
        self.held = False
        self._assert_count = 0
        self._fail_assert_at = fail_assert_at

    def acquire(self) -> None:
        """获取测试租约并拒绝重复获取。

        输入参数：
            无。
        输出返回值：
            无。
        """

        assert self.held is False
        self.held = True
        self._calls.append("lease.acquire")

    def release(self) -> None:
        """释放测试租约并拒绝无持有释放。

        输入参数：
            无。
        输出返回值：
            无。
        """

        assert self.held is True
        self._calls.append("lease.release")
        self.held = False

    def assert_held(self) -> None:
        """验证当前测试调用仍持有同一 fencing ownership。

        输入参数：
            无。
        输出返回值：
            无；未持有或命中失败注入点时抛异常。
        """

        self._assert_count += 1
        self._calls.append("lease.assert_held")
        if not self.held or self._assert_count == self._fail_assert_at:
            raise RuntimeError("synthetic lease ownership lost")


class _Source:
    """按 baseline/final 两轮返回四店快照的合成数据源。"""

    def __init__(
        self,
        calls: list[str],
        rounds: Sequence[dict[str, tuple[ObservedCheckoutOrder, ...]]],
        *,
        fail_on_call: int | None = None,
    ) -> None:
        """保存确定性轮次数据和可选失败注入点。

        输入参数：
            calls：共享调用顺序记录。
            rounds：baseline 与 final 的逐店完整订单元组。
            fail_on_call：从 1 开始计数的读取失败位置。
        输出返回值：
            无。
        """

        self._calls = calls
        self._rounds = tuple(rounds)
        self._read_count = 0
        self._fail_on_call = fail_on_call

    def read_orders(
        self,
        logical_store_id: str,
    ) -> tuple[ObservedCheckoutOrder, ...]:
        """返回当前轮次中一个 store 的完整全状态订单快照。

        输入参数：
            logical_store_id：固定四店中的一个 logical identity。
        输出返回值：
            当前轮次的不可变订单元组。
        """

        self._read_count += 1
        self._calls.append(f"source.read:{logical_store_id}")
        if self._read_count == self._fail_on_call:
            raise RuntimeError("synthetic privileged evidence failure")
        round_index = (self._read_count - 1) // len(WEBMALL_LOGICAL_STORE_IDS)
        return self._rounds[round_index][logical_store_id]


def _empty_snapshot() -> dict[str, tuple[ObservedCheckoutOrder, ...]]:
    """创建显式覆盖全部四店的空订单快照。

    输入参数：
        无。
    输出返回值：
        logical store 到空订单元组的映射。
    """

    return {store_id: () for store_id in WEBMALL_LOGICAL_STORE_IDS}


def test_session_diffs_all_stores_and_holds_lease_through_final_capture() -> None:
    """验证旧订单被基线排除，跨店新增订单完整进入 observation。

    输入参数：
        无；baseline 含历史订单，final 在两个 store 各增加一单。
    输出返回值：
        无；只返回两笔新增订单，且 final capture 后租约仍被持有到 close。
    """

    calls: list[str] = []
    baseline = _empty_snapshot()
    baseline["store-1"] = (_order("store-1", "old-1", ("old", 1)),)
    final = dict(baseline)
    final["store-2"] = (_order("store-2", "new-2", ("case", 1)),)
    final["store-3"] = (_order("store-3", "new-3", ("psu", 1)),)
    lease = _Lease(calls)
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (baseline, final)),
        lease=lease,
    )

    session.begin()
    observation = session.capture_final()

    assert lease.held is True
    assert observation.complete is True
    assert {
        (order.logical_store_id, order.order_identity) for order in observation.orders
    } == {("store-2", "new-2"), ("store-3", "new-3")}
    session.close()
    assert lease.held is False
    assert calls == [
        "lease.acquire",
        "lease.assert_held",
        "source.read:store-1",
        "source.read:store-2",
        "source.read:store-3",
        "source.read:store-4",
        "lease.assert_held",
        "lease.assert_held",
        "source.read:store-1",
        "source.read:store-2",
        "source.read:store-3",
        "source.read:store-4",
        "lease.assert_held",
        "lease.release",
    ]


def test_close_captures_final_before_release_when_agent_never_evaluates() -> None:
    """验证 Agent 异常跳过 evaluator 时，close 仍先补拍终态再释放租约。

    输入参数：
        无；模拟 begin 后直接进入 finally/close。
    输出返回值：
        无；close 返回并缓存新增订单 observation，读取发生在 release 前。
    """

    calls: list[str] = []
    baseline = _empty_snapshot()
    final = _empty_snapshot()
    final["store-4"] = (_order("store-4", "new-4", ("keyboard", 1)),)
    lease = _Lease(calls)
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (baseline, final)),
        lease=lease,
    )

    session.begin()
    observation = session.close()

    assert observation is not None
    assert tuple(order.order_identity for order in observation.orders) == ("new-4",)
    assert calls[-3:] == [
        "source.read:store-4",
        "lease.assert_held",
        "lease.release",
    ]
    assert session.observation is observation


def test_incomplete_final_scan_releases_lease_and_never_returns_empty_success() -> None:
    """验证任一 store 读取失败属于证据错误，并在失败后释放全局租约。

    输入参数：
        无；在 final 的第三个 store 注入特权读取失败。
    输出返回值：
        无；close 重新抛出原错误且不会生成 ``complete=True`` 空批次。
    """

    calls: list[str] = []
    empty = _empty_snapshot()
    lease = _Lease(calls)
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (empty, empty), fail_on_call=7),
        lease=lease,
    )
    session.begin()

    with pytest.raises(RuntimeError, match="privileged evidence failure"):
        session.close()

    assert lease.held is False
    assert session.observation is None
    assert calls[-1] == "lease.release"


@pytest.mark.parametrize("mutation", ["removed", "wrong_store"])
def test_snapshot_drift_is_evaluator_contract_error(mutation: str) -> None:
    """验证订单消失、既有订单改变或 store 错配均 fail-closed。

    输入参数：
        mutation：需要注入的快照契约破坏类型。
    输出返回值：
        无；capture_final 抛证据契约错误，不把异常环境状态记为 Agent 零分。
    """

    calls: list[str] = []
    old = _order("store-1", "old-1", ("stable", 1))
    baseline = _empty_snapshot()
    baseline["store-1"] = (old,)
    final = dict(baseline)
    if mutation == "removed":
        final["store-1"] = ()
    else:
        final["store-1"] = (_order("store-2", "wrong-1", ("stable", 1)),)
    lease = _Lease(calls)
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (baseline, final)),
        lease=lease,
    )
    session.begin()

    with pytest.raises(WebMallOrderEvidenceContractError):
        session.capture_final()
    session.close()

    assert lease.held is False


def test_historical_order_field_drift_is_excluded_by_baseline_identity() -> None:
    """验证后台更新历史订单字段不会污染本 Attempt 的新增订单差分。

    输入参数：
        无；baseline 与 final 保留相同订单身份，但历史商品字段发生变化。
    输出返回值：
        无；该历史身份仍被排除，完整 observation 中没有新增订单。
    """

    calls: list[str] = []
    baseline = _empty_snapshot()
    baseline["store-1"] = (_order("store-1", "old-1", ("before", 1)),)
    final = _empty_snapshot()
    final["store-1"] = (_order("store-1", "old-1", ("after", 1)),)
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (baseline, final)),
        lease=_Lease(calls),
    )

    session.begin()
    observation = session.capture_final()
    session.close()

    assert observation.complete is True
    assert observation.orders == ()


def test_session_accepts_all_state_order_reader_interface() -> None:
    """验证 evidence source 合同枚举全部订单，而非只枚举 completed。

    输入参数：
        无；source 仅实现新 ``read_orders`` 接口并返回四店空快照。
    输出返回值：
        无；baseline/final 都成功，证明 session 不再要求误导性的旧接口名。
    """

    class AllStateSource:
        """实现固定四店全状态读取接口的合成 source。"""

        def read_orders(
            self,
            logical_store_id: str,
        ) -> tuple[ObservedCheckoutOrder, ...]:
            """返回指定固定商店的空订单闭包。

            输入参数：
                logical_store_id：session 固定顺序请求的 store identity。
            输出返回值：
                空元组；断言调用范围始终位于四店 universe。
            """

            assert logical_store_id in WEBMALL_LOGICAL_STORE_IDS
            return ()

    calls: list[str] = []
    session = WebMallOrderEvidenceSession(
        source=AllStateSource(),
        lease=_Lease(calls),
    )

    session.begin()
    observation = session.capture_final()
    session.close()

    assert observation.complete is True
    assert observation.orders == ()


def test_session_rejects_duplicate_order_identity_in_authoritative_snapshot() -> None:
    """验证权威 store 快照中的重复订单身份不会被静默去重。

    输入参数：
        无；baseline 返回相同订单两次。
    输出返回值：
        无；begin 失败并释放已获取的租约。
    """

    calls: list[str] = []
    duplicate = _order("store-1", "same", ("product", 1))
    baseline = _empty_snapshot()
    baseline["store-1"] = (duplicate, duplicate)
    lease = _Lease(calls)
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (baseline,)),
        lease=lease,
    )

    with pytest.raises(WebMallOrderEvidenceContractError, match="重复"):
        session.begin()

    assert lease.held is False
    assert calls[-1] == "lease.release"


def test_capture_final_is_idempotent_and_reads_each_store_only_twice() -> None:
    """验证 evaluator 与 close 重复取证时不会产生第三次漂移快照。

    输入参数：
        无；baseline/final 均为空，并连续两次请求 final。
    输出返回值：
        无；两次返回同一对象，每个 store 总共只被读取两次。
    """

    calls: list[str] = []
    empty = _empty_snapshot()
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (empty, empty)),
        lease=_Lease(calls),
    )
    session.begin()

    first = session.capture_final()
    second = session.capture_final()
    session.close()

    assert first is second
    assert sum(call.startswith("source.read:") for call in calls) == 8


def test_lost_global_lease_fails_before_final_snapshot_and_is_released() -> None:
    """验证跨 host 租约 ownership 丢失时不读取或生成 final 证据。

    输入参数：
        无；第三次 ownership 检查（final 读取前）注入 fencing 失败。
    输出返回值：
        无；capture_final 抛错，close 释放 handle，observation 保持为空。
    """

    calls: list[str] = []
    empty = _empty_snapshot()
    lease = _Lease(calls, fail_assert_at=3)
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (empty, empty)),
        lease=lease,
    )
    session.begin()

    with pytest.raises(RuntimeError, match="ownership lost"):
        session.capture_final()
    session.close()

    assert session.observation is None
    assert lease.held is False
    assert sum(call.startswith("source.read:") for call in calls) == 4


def test_session_exposes_fail_closed_renewal_for_guarded_controller_calls() -> None:
    """验证环境可在每个 controller 操作前通过 session 续期。

    输入参数：
        无；使用合成租约与四店空快照。
    输出返回值：
        无；``assert_held`` 在 session 活跃时委托给权威租约，
        关闭后则拒绝复用旧 ownership。
    """

    calls: list[str] = []
    empty = _empty_snapshot()
    session = WebMallOrderEvidenceSession(
        source=_Source(calls, (empty, empty)),
        lease=_Lease(calls),
    )
    session.begin()

    session.assert_held()

    assert calls[-1] == "lease.assert_held"
    session.close()
    with pytest.raises(WebMallOrderEvidenceContractError):
        session.assert_held()
