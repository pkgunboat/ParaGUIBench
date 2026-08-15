"""WebMall 购物车状态的受控 runtime evidence source seam。"""

from __future__ import annotations

from collections.abc import Sequence
import re
from typing import Protocol

from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartStore,
    ObservedCartWorker,
)
from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
)

CART_EVIDENCE_SOURCE_PROTOCOL_ID = "paraguibench.webmall.cart-authoritative-state.v1"
_WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class WebMallCartEvidenceError(RuntimeError):
    """表示购物车权威状态无法按固定闭集采集。"""


class WebMallCartEvidenceSource(Protocol):
    """定义 runtime 必须装配的购物车权威状态读取接口。"""

    evidence_protocol_id: str

    def read_cart(
        self,
        worker_id: str,
        logical_store_id: str,
    ) -> ObservedCartStore:
        """读取一个 worker 中单个 logical store 的完整 cart 终态。

        输入参数：
            worker_id：runtime 预先绑定的浏览器会话身份。
            logical_store_id：固定 WebMall 四店 universe 中的 store ID。
        输出返回值：
            只能由可靠 cart state/API 或已验证的 cart 页面完整枚举
            构造的 ``ObservedCartStore``；不得读取 Agent 报告。
        """


def capture_webmall_cart_observation(
    source: WebMallCartEvidenceSource,
    worker_ids: Sequence[str],
) -> CartObservationBatch:
    """按参与 worker 和固定四店顺序采集完整 cart 闭包。

    输入参数：
        source：明确声明版本化权威状态协议的 runtime source。
        worker_ids：本 Attempt 所有参与浏览器会话的稳定身份序列。
    输出返回值：
        ``complete=True`` 的全 worker×四店不可变观测批次。
    异常：
        WebMallCartEvidenceError：source 协议、worker 闭集、任一读取或
            store coverage 无效；错误不回显 URL、slug 或 cart 内容。
    """

    if getattr(
        source, "evidence_protocol_id", None
    ) != CART_EVIDENCE_SOURCE_PROTOCOL_ID or not callable(
        getattr(source, "read_cart", None)
    ):
        raise WebMallCartEvidenceError("WebMall cart evidence source 协议无效")
    normalized_worker_ids = _validate_worker_ids(worker_ids)
    workers: list[ObservedCartWorker] = []
    for worker_id in normalized_worker_ids:
        stores: list[ObservedCartStore] = []
        for logical_store_id in WEBMALL_LOGICAL_STORE_IDS:
            try:
                store = source.read_cart(worker_id, logical_store_id)
            except Exception:
                raise WebMallCartEvidenceError(
                    "WebMall cart evidence 无法完整读取"
                ) from None
            if (
                not isinstance(store, ObservedCartStore)
                or store.logical_store_id != logical_store_id
                or not store.complete
            ):
                raise WebMallCartEvidenceError(
                    "WebMall cart evidence store coverage 无效"
                )
            stores.append(store)
        workers.append(
            ObservedCartWorker(
                worker_id=worker_id,
                complete=True,
                stores=tuple(stores),
            )
        )
    return CartObservationBatch(complete=True, workers=tuple(workers))


def _validate_worker_ids(worker_ids: Sequence[str]) -> tuple[str, ...]:
    """在触发任何状态 I/O 前验证参与 worker 闭集。

    输入参数：
        worker_ids：runtime 提供的身份序列。
    输出返回值：
        保留 runtime 顺序的非空、唯一、有界字符串元组。
    异常：
        WebMallCartEvidenceError：容器、任一 ID 或唯一性无效。
    """

    if (
        isinstance(worker_ids, (str, bytes))
        or not isinstance(worker_ids, Sequence)
        or not worker_ids
    ):
        raise WebMallCartEvidenceError("WebMall cart worker coverage 无效")
    normalized = tuple(worker_ids)
    if any(
        not isinstance(worker_id, str)
        or _WORKER_ID_PATTERN.fullmatch(worker_id) is None
        for worker_id in normalized
    ) or len(normalized) != len(set(normalized)):
        raise WebMallCartEvidenceError("WebMall cart worker coverage 无效")
    return normalized
