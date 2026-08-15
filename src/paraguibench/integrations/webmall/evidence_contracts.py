"""WebMall 环境证据与纯 evaluator 共享的最小数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import quote, unquote_to_bytes

LOGICAL_STORE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,127}")
_ORDER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_INVALID_PERCENT_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")

WEBMALL_STORE_UNIVERSE_ID = "webmall.four-stores.v1"
WEBMALL_LOGICAL_STORE_IDS = (
    "store-1",
    "store-2",
    "store-3",
    "store-4",
)


class WebMallEvidenceContractError(ValueError):
    """表示 WebMall evaluator 输入证据不完整、矛盾或编码非法。"""


@dataclass(frozen=True, slots=True, repr=False)
class ObservedOrderIdentity:
    """保存不携带订单详情的 WebMall 权威订单身份。

    输入参数：
        logical_store_id：固定四店环境中的 store identity。
        order_identity：Attempt 内稳定的订单 ID，不得使用 URL。
    输出返回值：
        不执行 I/O 且 ``repr`` 不回显订单 ID 的不可变身份。
    """

    logical_store_id: str
    order_identity: str

    def __post_init__(self) -> None:
        """验证 store 与订单身份的闭合编码。

        输入参数：
            无；读取数据类字段。
        输出返回值：
            无；字段合法时正常完成构造。
        异常：
            WebMallEvidenceContractError：任一 identity 无效。
        """

        if (
            not isinstance(self.logical_store_id, str)
            or LOGICAL_STORE_ID_PATTERN.fullmatch(self.logical_store_id) is None
            or not isinstance(self.order_identity, str)
            or _ORDER_ID_PATTERN.fullmatch(self.order_identity) is None
        ):
            raise WebMallEvidenceContractError("checkout order identity 证据无效")


@dataclass(frozen=True, slots=True, repr=False)
class OrderIdentityBatch:
    """保存一个 logical store 的完整订单身份闭集。

    输入参数：
        logical_store_id：本批次所属的稳定店铺身份。
        complete：reader 是否声明已完整枚举该店。
        identities：按权威读取顺序保存的订单身份元组。
    输出返回值：
        不执行 I/O 且 ``repr`` 不回显 identity 的不可变批次。
    """

    logical_store_id: str
    complete: bool
    identities: tuple[ObservedOrderIdentity, ...]

    def __post_init__(self) -> None:
        """验证批次类型、store 一致性与 identity 唯一性。

        输入参数：
            无；读取数据类字段。
        输出返回值：
            无；结构完整且无重复时正常完成构造。
        异常：
            WebMallEvidenceContractError：批次结构、store 或唯一性无效。
        """

        if (
            not isinstance(self.logical_store_id, str)
            or LOGICAL_STORE_ID_PATTERN.fullmatch(self.logical_store_id) is None
            or not isinstance(self.complete, bool)
            or not isinstance(self.identities, tuple)
            or any(
                not isinstance(identity, ObservedOrderIdentity)
                or identity.logical_store_id != self.logical_store_id
                for identity in self.identities
            )
        ):
            raise WebMallEvidenceContractError("checkout order identity batch 证据无效")
        values = tuple(identity.order_identity for identity in self.identities)
        if len(values) != len(set(values)):
            raise WebMallEvidenceContractError(
                "checkout order identity batch 存在重复身份"
            )


@dataclass(frozen=True, slots=True)
class ObservedCheckoutProduct:
    """保存 evidence adapter 从可信商品 ID 解析出的订单行。

    输入参数：
        canonical_slug：通过 WooCommerce product ID 解析的 canonical slug；
            不得使用 display label 代替。
        quantity：订单行中的正整数数量。
    输出返回值：
        不执行 I/O 的不可变观测；公开评价结果不会复制 slug。
    """

    canonical_slug: str
    quantity: int

    def __post_init__(self) -> None:
        """验证 canonical slug 和数量并固定编码形式。

        输入参数：
            无；读取构造参数 ``canonical_slug`` 与 ``quantity``。
        输出返回值：
            无；合法证据正常完成构造。
        异常：
            WebMallEvidenceContractError：slug 或数量无效。
        """

        normalized_slug = normalize_product_slug(self.canonical_slug)
        object.__setattr__(self, "canonical_slug", normalized_slug)
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity < 1
        ):
            raise WebMallEvidenceContractError("checkout product quantity 无效")


@dataclass(frozen=True, slots=True)
class ObservedCheckoutProfile:
    """保存 WooCommerce 权威订单中的完整账单资料。

    输入参数：
        full_name/email：订单账单姓名与邮箱。
        address_line_1：包含街道和门牌号的第一地址行。
        postcode/city/state/country：订单中分字段读取的地址值。
    输出返回值：
        不执行 I/O 的不可变证据；实际值仅存留在 evaluator
        可信内存，公开结果只保存布尔值和计数。
    """

    full_name: str
    email: str
    address_line_1: str
    postcode: str
    city: str
    state: str
    country: str

    def __post_init__(self) -> None:
        """验证账单字段已完整读取且没有控制字符。

        输入参数：
            无；读取七个构造字段。
        输出返回值：
            无；完整、有界文本正常完成构造。
        异常：
            WebMallEvidenceContractError：任一字段缺失、过长或含控制字符。
        """

        values = (
            self.full_name,
            self.email,
            self.address_line_1,
            self.postcode,
            self.city,
            self.state,
            self.country,
        )
        if any(
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 512
            or contains_control(value)
            for value in values
        ):
            raise WebMallEvidenceContractError("checkout billing profile 证据无效")


@dataclass(frozen=True, slots=True)
class ObservedCheckoutOrder:
    """保存一个 logical store 中的脱敏权威订单观测。

    输入参数：
        logical_store_id：固定环境清单中的 store identity。
        order_identity：Attempt 内稳定的订单 ID，不得使用 URL 或路径。
        products：订单中的完整且非空商品行元组。
        checkout_state：环境 adapter 归一化的 checkout 终态。
        payment_kind：环境 adapter 归一化的支付语义。
        billing_profile：订单权威账单资料闭包。
    输出返回值：
        可供 baseline 差分和 evaluator 使用的不可变订单证据。
    """

    logical_store_id: str
    order_identity: str
    products: tuple[ObservedCheckoutProduct, ...]
    checkout_state: str
    payment_kind: str
    billing_profile: ObservedCheckoutProfile

    def __post_init__(self) -> None:
        """验证订单身份与完整商品元组。

        输入参数：
            无；读取三个构造字段。
        输出返回值：
            无；合法订单正常完成构造。
        异常：
            WebMallEvidenceContractError：store/order ID 或商品闭集无效。
        """

        if (
            not isinstance(self.logical_store_id, str)
            or LOGICAL_STORE_ID_PATTERN.fullmatch(self.logical_store_id) is None
        ):
            raise WebMallEvidenceContractError("checkout logical store identity 无效")
        if (
            not isinstance(self.order_identity, str)
            or _ORDER_ID_PATTERN.fullmatch(self.order_identity) is None
        ):
            raise WebMallEvidenceContractError("checkout order identity 无效")
        if (
            not isinstance(self.products, tuple)
            or not self.products
            or any(
                not isinstance(item, ObservedCheckoutProduct) for item in self.products
            )
        ):
            raise WebMallEvidenceContractError("checkout order products 证据无效")
        if self.checkout_state not in {
            "completed",
            "processing",
            "pending",
            "failed",
            "cancelled",
            "refunded",
        }:
            raise WebMallEvidenceContractError("checkout state 证据无效")
        if self.payment_kind not in {
            "credit_card",
            "bank_transfer",
            "cash",
            "other",
        }:
            raise WebMallEvidenceContractError("checkout payment kind 证据无效")
        if not isinstance(self.billing_profile, ObservedCheckoutProfile):
            raise WebMallEvidenceContractError("checkout billing profile 证据无效")


@dataclass(frozen=True, slots=True)
class CheckoutObservationBatch:
    """保存 Attempt baseline 之后完整枚举的新增订单批次。

    输入参数：
        store_universe_id：固定四店扫描范围的版本化身份。
        scanned_store_ids：本批次实际完成扫描的 logical store 有序闭集。
        complete：evidence adapter 是否成功扫描全部相关 store。
        orders：完整新增订单元组；可以含不同 observation source 对同一
            订单的重复 sighting，纯 evaluator 负责一致性去重。
    输出返回值：
        不可变批次；``complete=False`` 必须成为 evaluator error。
    """

    store_universe_id: str
    scanned_store_ids: tuple[str, ...]
    complete: bool
    orders: tuple[ObservedCheckoutOrder, ...]

    def __post_init__(self) -> None:
        """验证批次结构，不把不完整扫描伪装成空订单。

        输入参数：
            无；读取 universe、扫描范围、``complete`` 与 ``orders``。
        输出返回值：
            无；字段类型合法时正常完成构造。
        异常：
            WebMallEvidenceContractError：字段类型无效。
        """

        if self.store_universe_id != WEBMALL_STORE_UNIVERSE_ID:
            raise WebMallEvidenceContractError(
                "checkout observation coverage identity 无效"
            )
        if (
            not isinstance(self.scanned_store_ids, tuple)
            or len(self.scanned_store_ids) != len(set(self.scanned_store_ids))
            or any(
                store_id not in WEBMALL_LOGICAL_STORE_IDS
                for store_id in self.scanned_store_ids
            )
            or tuple(
                store_id
                for store_id in WEBMALL_LOGICAL_STORE_IDS
                if store_id in self.scanned_store_ids
            )
            != self.scanned_store_ids
        ):
            raise WebMallEvidenceContractError(
                "checkout observation coverage stores 无效"
            )
        if not isinstance(self.complete, bool):
            raise WebMallEvidenceContractError("checkout observation complete 标记无效")
        if self.complete and (self.scanned_store_ids != WEBMALL_LOGICAL_STORE_IDS):
            raise WebMallEvidenceContractError("checkout observation coverage 不完整")
        if not isinstance(self.orders, tuple) or any(
            not isinstance(item, ObservedCheckoutOrder) for item in self.orders
        ):
            raise WebMallEvidenceContractError("checkout observation orders 类型无效")
        scanned = set(self.scanned_store_ids)
        if any(order.logical_store_id not in scanned for order in self.orders):
            raise WebMallEvidenceContractError(
                "checkout observation order 超出 coverage"
            )


def normalize_product_slug(value: object) -> str:
    """严格解码并重新编码 WooCommerce canonical product slug。

    输入参数：
        value：logical URL 或可信 evidence adapter 提供的单层 slug。
    输出返回值：
        NFC 规范化、UTF-8 percent-encoding 大写的稳定单层 slug。
    异常：
        WebMallEvidenceContractError：类型、percent 编码、UTF-8、控制
            字符、空白或路径分隔符无效。
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or _INVALID_PERCENT_PATTERN.search(value)
        or contains_control(value)
        or "\\" in value
        or "/" in value
        or any(character.isspace() for character in value)
    ):
        raise WebMallEvidenceContractError("checkout canonical product slug 无效")
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise WebMallEvidenceContractError(
            "checkout canonical product slug UTF-8 无效"
        ) from error
    if (
        not decoded
        or decoded in {".", ".."}
        or contains_control(decoded)
        or "/" in decoded
        or "\\" in decoded
        or any(character.isspace() for character in decoded)
    ):
        raise WebMallEvidenceContractError("checkout canonical product slug 无效")
    return quote(
        unicodedata.normalize("NFC", decoded),
        safe="-._~",
        encoding="utf-8",
        errors="strict",
    )


def contains_control(value: str) -> bool:
    """判断文本是否含 Unicode 控制字符。

    输入参数：
        value：待检查的 URL 或解码后 slug。
    输出返回值：
        任一字符的 Unicode category 为 ``Cc`` 时返回 ``True``。
    """

    return any(unicodedata.category(character) == "Cc" for character in value)
