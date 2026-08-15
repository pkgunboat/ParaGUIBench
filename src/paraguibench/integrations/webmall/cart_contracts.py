"""WebMall 购物车取证与纯评价层共享的不可变契约。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
    WebMallEvidenceContractError,
    normalize_product_slug,
)

_WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True, slots=True, repr=False)
class ObservedCartItem:
    """保存受信购物车状态中的一条商品证据。

    输入参数：
        canonical_slug：受控 adapter 读取的 WooCommerce 商品 slug。
        quantity：该商品在购物车中的正整数数量。
    输出返回值：
        ``repr`` 不回显 slug 的不可变商品观测。
    """

    canonical_slug: str
    quantity: int

    def __post_init__(self) -> None:
        """规范化 slug 并验证数量。

        输入参数：无；读取数据类构造字段。
        输出返回值：无；合法值被冻结为稳定编码。
        异常：
            WebMallEvidenceContractError：slug 或数量无效。
        """

        object.__setattr__(
            self,
            "canonical_slug",
            normalize_product_slug(self.canonical_slug),
        )
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity < 1
        ):
            raise WebMallEvidenceContractError("WebMall cart item quantity 无效")


@dataclass(frozen=True, slots=True, repr=False)
class ObservedCartStore:
    """保存一个 worker 中单个 logical store 的购物车闭包。

    输入参数：
        logical_store_id：固定四店 universe 中的店铺身份。
        complete：该店购物车是否已被完整枚举。
        items：受信 adapter 返回的商品行元组。
    输出返回值：
        不回显购物车内容的不可变 store observation。
    """

    logical_store_id: str
    complete: bool
    items: tuple[ObservedCartItem, ...]

    def __post_init__(self) -> None:
        """验证 store identity、完整性标记与商品行类型。

        输入参数：无；读取数据类字段。
        输出返回值：无；结构合法时正常返回。
        异常：
            WebMallEvidenceContractError：证据结构无效。
        """

        if (
            self.logical_store_id not in WEBMALL_LOGICAL_STORE_IDS
            or not isinstance(self.complete, bool)
            or not isinstance(self.items, tuple)
            or any(not isinstance(item, ObservedCartItem) for item in self.items)
        ):
            raise WebMallEvidenceContractError("WebMall cart store observation 无效")


@dataclass(frozen=True, slots=True, repr=False)
class ObservedCartWorker:
    """保存一个独立浏览器 worker 的四店购物车快照。

    输入参数：
        worker_id：runtime 内稳定但不向公开结果投影的身份。
        complete：四店是否均已完整枚举。
        stores：按固定 universe 顺序保存的 store 快照。
    输出返回值：
        不回显 worker 和购物车内容的不可变快照。
    """

    worker_id: str
    complete: bool
    stores: tuple[ObservedCartStore, ...]

    def __post_init__(self) -> None:
        """验证 worker ID 与固定四店 coverage。

        输入参数：无；读取数据类字段。
        输出返回值：无；结构合法时正常返回。
        异常：
            WebMallEvidenceContractError：worker 身份或 coverage 无效。
        """

        if (
            not isinstance(self.worker_id, str)
            or _WORKER_ID_PATTERN.fullmatch(self.worker_id) is None
            or not isinstance(self.complete, bool)
            or not isinstance(self.stores, tuple)
            or any(not isinstance(store, ObservedCartStore) for store in self.stores)
        ):
            raise WebMallEvidenceContractError("WebMall cart worker observation 无效")
        store_ids = tuple(store.logical_store_id for store in self.stores)
        if len(store_ids) != len(set(store_ids)):
            raise WebMallEvidenceContractError("WebMall cart worker store 身份重复")
        if self.complete and (
            store_ids != WEBMALL_LOGICAL_STORE_IDS
            or any(not store.complete for store in self.stores)
        ):
            raise WebMallEvidenceContractError("WebMall cart worker coverage 不完整")


@dataclass(frozen=True, slots=True, repr=False)
class CartObservationBatch:
    """保存一次 Attempt 全部 worker 的购物车终态闭包。

    输入参数：
        complete：所有参与 worker 和四店是否均完整采集。
        workers：非空、身份唯一的 worker 观测元组。
    输出返回值：
        仅供受信 evaluator 内存使用的不可变闭包；不得序列化到
        RunStore，公开持久化只允许使用脱敏 ``CartEvaluation``。
    """

    complete: bool
    workers: tuple[ObservedCartWorker, ...]

    def __post_init__(self) -> None:
        """验证 worker 非空、唯一且完整性声明自洽。

        输入参数：无；读取数据类字段。
        输出返回值：无；结构合法时正常返回。
        异常：
            WebMallEvidenceContractError：批次类型、身份或完整性无效。
        """

        if (
            not isinstance(self.complete, bool)
            or not isinstance(self.workers, tuple)
            or not self.workers
            or any(
                not isinstance(worker, ObservedCartWorker) for worker in self.workers
            )
        ):
            raise WebMallEvidenceContractError("WebMall cart observation batch 无效")
        worker_ids = tuple(worker.worker_id for worker in self.workers)
        if len(worker_ids) != len(set(worker_ids)):
            raise WebMallEvidenceContractError(
                "WebMall cart observation worker 身份重复"
            )
        if self.complete and any(not worker.complete for worker in self.workers):
            raise WebMallEvidenceContractError(
                "WebMall cart observation coverage 不完整"
            )
