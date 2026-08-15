"""WebMall cart 受控 runtime evidence source 测试。"""

from __future__ import annotations

import pytest

from paraguibench.integrations.webmall.cart_contracts import (
    ObservedCartItem,
    ObservedCartStore,
)
from paraguibench.integrations.webmall.cart_evidence import (
    CART_EVIDENCE_SOURCE_PROTOCOL_ID,
    WebMallCartEvidenceError,
    capture_webmall_cart_observation,
)


class _AuthoritativeCartSource:
    """记录 collector 访问顺序并返回合成权威购物车状态。"""

    evidence_protocol_id = CART_EVIDENCE_SOURCE_PROTOCOL_ID

    def __init__(self) -> None:
        """初始化读取记录。

        输入参数：无。
        输出返回值：无；初始记录为空。
        """

        self.calls: list[tuple[str, str]] = []

    def read_cart(
        self,
        worker_id: str,
        logical_store_id: str,
    ) -> ObservedCartStore:
        """返回一个完整 store 快照并记录调用。

        输入参数：
            worker_id：collector 指定的 runtime worker 身份。
            logical_store_id：collector 指定的固定店铺身份。
        输出返回值：
            store-2 含一件合成商品，其余店铺为空的完整快照。
        """

        self.calls.append((worker_id, logical_store_id))
        items = (
            (ObservedCartItem("private-source-widget", 1),)
            if logical_store_id == "store-2"
            else ()
        )
        return ObservedCartStore(
            logical_store_id=logical_store_id,
            complete=True,
            items=items,
        )


def test_collector_reads_every_worker_and_store_in_fixed_order() -> None:
    """验证 collector 完整扫描每个 worker 的固定四店闭集。

    输入参数：无；使用两个 worker 和可记录的受信 source fake。
    输出返回值：无；读取顺序必须为 worker 优先、店铺固定顺序。
    """

    source = _AuthoritativeCartSource()

    observation = capture_webmall_cart_observation(
        source,
        ("worker-a", "worker-b"),
    )

    assert source.calls == [
        (worker_id, store_id)
        for worker_id in ("worker-a", "worker-b")
        for store_id in ("store-1", "store-2", "store-3", "store-4")
    ]
    assert observation.complete is True
    assert len(observation.workers) == 2
    assert all(worker.complete for worker in observation.workers)
    assert "private-source-widget" not in repr(observation)
    assert "worker-a" not in repr(observation)


def test_unapproved_source_protocol_is_rejected_before_state_io() -> None:
    """验证未绑定版本化权威协议的 source 不得开始读取。

    输入参数：无；将 fake source 的协议 ID 替换为未知值。
    输出返回值：无；collector 必须在首次 ``read_cart`` 前失败。
    """

    source = _AuthoritativeCartSource()
    source.evidence_protocol_id = "untrusted.agent-report.v1"

    with pytest.raises(WebMallCartEvidenceError, match="source 协议"):
        capture_webmall_cart_observation(source, ("worker-a",))

    assert source.calls == []


def test_incomplete_store_snapshot_fails_closed() -> None:
    """验证任一店未完整枚举时 collector 不会返回部分批次。

    输入参数：无；source 在 store-3 返回 ``complete=false``。
    输出返回值：无；必须抛 coverage 错误，不得把已读店铺当成闭集。
    """

    class _IncompleteSource(_AuthoritativeCartSource):
        """在固定店铺返回不完整 observation 的 source fake。"""

        def read_cart(
            self,
            worker_id: str,
            logical_store_id: str,
        ) -> ObservedCartStore:
            """返回一个指定店铺不完整的证据。

            输入参数：
                worker_id：collector 传入的 worker 身份。
                logical_store_id：collector 传入的 store 身份。
            输出返回值：
                store-3 的 ``complete=false`` 快照，其余调用委托父类。
            """

            if logical_store_id != "store-3":
                return super().read_cart(worker_id, logical_store_id)
            self.calls.append((worker_id, logical_store_id))
            return ObservedCartStore(
                logical_store_id=logical_store_id,
                complete=False,
                items=(),
            )

    source = _IncompleteSource()

    with pytest.raises(WebMallCartEvidenceError, match="coverage"):
        capture_webmall_cart_observation(source, ("worker-a",))

    assert source.calls[-1] == ("worker-a", "store-3")
    assert ("worker-a", "store-4") not in source.calls


def test_source_failure_does_not_echo_cart_or_url_values() -> None:
    """验证 runtime source 异常中的 URL 和 cart 值不会进入上层错误。

    输入参数：无；source 抛出故意含私密 URL/slug 的底层异常。
    输出返回值：无；collector 只抛固定无值错误且不保留 cause。
    """

    private_value = "https://private.invalid/cart/private-secret-slug"

    class _FailingSource(_AuthoritativeCartSource):
        """模拟底层读取错误携带私密值的 source fake。"""

        def read_cart(
            self,
            worker_id: str,
            logical_store_id: str,
        ) -> ObservedCartStore:
            """抛出携带敏感文本的合成错误。

            输入参数：
                worker_id：未使用的 worker 身份。
                logical_store_id：未使用的 store 身份。
            输出返回值：
                无；始终抛异常。
            """

            del worker_id, logical_store_id
            raise RuntimeError(private_value)

    with pytest.raises(WebMallCartEvidenceError) as captured:
        capture_webmall_cart_observation(
            _FailingSource(),
            ("worker-a",),
        )

    assert private_value not in str(captured.value)
    assert captured.value.__cause__ is None


def test_invalid_or_duplicate_worker_ids_are_rejected_before_io() -> None:
    """验证 worker coverage 非空、编码安全且不得重复。

    输入参数：无；分别提供空、路径型和重复 worker ID。
    输出返回值：无；所有非法 coverage 均在任何 source I/O 前失败。
    """

    for invalid_ids in ((), ("../worker",), ("worker-a", "worker-a")):
        source = _AuthoritativeCartSource()
        with pytest.raises(WebMallCartEvidenceError, match="worker coverage"):
            capture_webmall_cart_observation(source, invalid_ids)
        assert source.calls == []
