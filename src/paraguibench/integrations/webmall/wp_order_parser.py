"""WebMall WP-CLI 闭合 JSON 的纯解析与 DTO 映射。"""

from __future__ import annotations

from collections.abc import Collection
import json

from paraguibench.integrations.webmall.evidence_contracts import (
    LOGICAL_STORE_ID_PATTERN,
    ObservedCheckoutOrder,
    ObservedCheckoutProduct,
    ObservedCheckoutProfile,
    ObservedOrderIdentity,
    OrderIdentityBatch,
    WebMallEvidenceContractError,
)


MAX_WP_CLI_ORDER_PAYLOAD_BYTES = 4 * 1024 * 1024
"""WP-CLI 订单载荷的固定最大字节数。"""

_PUBLIC_ERROR = "WebMall WP-CLI order payload 合同无效"
_ROOT_KEYS = frozenset({"schema_version", "complete", "orders"})
_IDENTITY_ROOT_KEYS = frozenset({"schema_version", "mode", "complete", "order_ids"})
_DETAIL_ROOT_KEYS = frozenset({"schema_version", "mode", "complete", "orders"})
_ORDER_KEYS = frozenset({"order_id", "status", "payment_method", "billing", "items"})
_BILLING_KEYS = frozenset(
    {
        "first_name",
        "last_name",
        "email",
        "address_1",
        "postcode",
        "city",
        "state",
        "country",
    }
)
_ITEM_KEYS = frozenset({"product_id", "variation_id", "quantity", "canonical_slug"})


def parse_wp_cli_order_identity_payload(
    *,
    logical_store_id: str,
    payload: bytes,
) -> OrderIdentityBatch:
    """将 WebMall v2 identity JSON 转换为脱离历史详情的批次。

    输入参数：
        logical_store_id：订单所属的稳定 logical store 身份。
        payload：已由调用方有界收集的 UTF-8 JSON bytes。
    输出返回值：
        完整、唯一且不携带 billing/payment/items 的 identity 批次。
    异常：
        WebMallEvidenceContractError：编码、schema、完整性或订单 ID 无效。
    """

    try:
        if (
            not isinstance(logical_store_id, str)
            or LOGICAL_STORE_ID_PATTERN.fullmatch(logical_store_id) is None
        ):
            raise _WPOrderPayloadError
        raw = _decode_payload(payload)
        _require_exact_keys(raw, _IDENTITY_ROOT_KEYS)
        if (
            raw["schema_version"] != 2
            or isinstance(raw["schema_version"], bool)
            or raw["mode"] != "identities"
            or raw["complete"] is not True
            or not isinstance(raw["order_ids"], list)
        ):
            raise _WPOrderPayloadError
        identities = tuple(
            _parse_order_identity(logical_store_id, order_id)
            for order_id in raw["order_ids"]
        )
        return OrderIdentityBatch(
            logical_store_id=logical_store_id,
            complete=True,
            identities=identities,
        )
    except (RecursionError, TypeError, ValueError):
        pass
    raise WebMallEvidenceContractError(_PUBLIC_ERROR)


def parse_wp_cli_order_details_payload(
    *,
    logical_store_id: str,
    payload: bytes,
    credit_card_payment_method_ids: Collection[str],
    expected_order_identities: tuple[str, ...],
) -> tuple[ObservedCheckoutOrder, ...]:
    """将 WebMall v2 details JSON 严格映射为指定新订单闭集。

    输入参数：
        logical_store_id：订单所属的稳定 logical store 身份。
        payload：已由调用方有界收集的 UTF-8 JSON bytes。
        credit_card_payment_method_ids：该店信用卡网关 ID 闭集。
        expected_order_identities：当前分块唯一允许返回的数字订单 ID。
    输出返回值：
        identity 集合与请求完全一致的严格订单 DTO 元组。
    异常：
        WebMallEvidenceContractError：载荷、新订单详情或 exact-set 无效。
    """

    try:
        if (
            not isinstance(logical_store_id, str)
            or LOGICAL_STORE_ID_PATTERN.fullmatch(logical_store_id) is None
        ):
            raise _WPOrderPayloadError
        expected_ids = _normalize_expected_order_identities(expected_order_identities)
        card_ids = _normalize_credit_card_ids(credit_card_payment_method_ids)
        raw = _decode_payload(payload)
        _require_exact_keys(raw, _DETAIL_ROOT_KEYS)
        if (
            raw["schema_version"] != 2
            or isinstance(raw["schema_version"], bool)
            or raw["mode"] != "details"
            or raw["complete"] is not True
            or not isinstance(raw["orders"], list)
        ):
            raise _WPOrderPayloadError
        orders = tuple(
            _parse_order(
                logical_store_id=logical_store_id,
                raw=order,
                credit_card_payment_method_ids=card_ids,
            )
            for order in raw["orders"]
        )
        observed_ids = tuple(order.order_identity for order in orders)
        if (
            len(observed_ids) != len(set(observed_ids))
            or frozenset(observed_ids) != expected_ids
        ):
            raise _WPOrderPayloadError
        return orders
    except (RecursionError, TypeError, ValueError):
        pass
    raise WebMallEvidenceContractError(_PUBLIC_ERROR)


def _decode_payload(payload: bytes) -> object:
    """有界解码且拒绝重复键的 WP-CLI JSON 文档。

    输入参数：
        payload：候选 UTF-8 JSON bytes。
    输出返回值：
        保留 JSON 容器类型的已解码值。
    异常：
        _WPOrderPayloadError/UnicodeError/ValueError：载荷为空、超限或编码无效。
    """

    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > MAX_WP_CLI_ORDER_PAYLOAD_BYTES
    ):
        raise _WPOrderPayloadError
    return json.loads(
        payload.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_json_object,
    )


def _parse_order_identity(
    logical_store_id: str,
    raw_order_id: object,
) -> ObservedOrderIdentity:
    """将 JSON 正整数订单 ID 转换为不可变 identity DTO。

    输入参数：
        logical_store_id：已验证的 logical store 身份。
        raw_order_id：identity envelope 中的 JSON 标量。
    输出返回值：
        以十进制字符串保存的订单身份。
    异常：
        _WPOrderPayloadError：订单 ID 不是非布尔正整数。
    """

    if not _is_positive_integer(raw_order_id):
        raise _WPOrderPayloadError
    return ObservedOrderIdentity(logical_store_id, str(raw_order_id))


def _normalize_expected_order_identities(
    values: tuple[str, ...],
) -> frozenset[str]:
    """验证 details 请求的数字 identity 是唯一闭集。

    输入参数：
        values：当前详情分块期望返回的订单 ID 元组。
    输出返回值：
        用于 exact-set 比较的不可变唯一集合。
    异常：
        _WPOrderPayloadError：容器、数字编码或唯一性无效。
    """

    if not isinstance(values, tuple):
        raise _WPOrderPayloadError
    normalized = frozenset(values)
    if len(normalized) != len(values) or any(
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or int(value) <= 0
        for value in normalized
    ):
        raise _WPOrderPayloadError
    return normalized


def parse_wp_cli_order_payload(
    *,
    logical_store_id: str,
    payload: bytes,
    credit_card_payment_method_ids: Collection[str],
) -> tuple[ObservedCheckoutOrder, ...]:
    """将有界 WP-CLI JSON 载荷转换为现有订单 DTO。

    输入参数：
        logical_store_id：订单所属的稳定 logical store 身份。
        payload：已由调用方有界收集的 UTF-8 JSON bytes。
        credit_card_payment_method_ids：该店被确认为信用卡
            网关的 payment method ID 闭集。
    输出返回值：
        保持载荷顺序的 ``ObservedCheckoutOrder`` 元组。
    异常：
        WebMallEvidenceContractError：载荷、映射配置或 DTO 不符合
            闭合契约。错误文本固定，不回显私密数据。
    """

    try:
        if (
            not isinstance(logical_store_id, str)
            or LOGICAL_STORE_ID_PATTERN.fullmatch(logical_store_id) is None
            or not isinstance(payload, bytes)
            or not payload
            or len(payload) > MAX_WP_CLI_ORDER_PAYLOAD_BYTES
        ):
            raise _WPOrderPayloadError
        card_ids = _normalize_credit_card_ids(credit_card_payment_method_ids)
        raw = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
        )
        _require_exact_keys(raw, _ROOT_KEYS)
        if (
            raw["schema_version"] != 1
            or isinstance(raw["schema_version"], bool)
            or raw["complete"] is not True
        ):
            raise _WPOrderPayloadError
        orders = raw["orders"]
        if not isinstance(orders, list):
            raise _WPOrderPayloadError
        return tuple(
            _parse_order(
                logical_store_id=logical_store_id,
                raw=order,
                credit_card_payment_method_ids=card_ids,
            )
            for order in orders
        )
    except (RecursionError, TypeError, ValueError):
        pass
    # 在 except 块之外构造公开异常，避免 ``__context__``
    # 保留 JSON decoder 的 ``doc`` 或 Unicode decoder 的原始 bytes。
    raise WebMallEvidenceContractError(_PUBLIC_ERROR)


class _WPOrderPayloadError(ValueError):
    """表示解析器内部检测到不合法的闭合字段。"""


def _strict_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """构造不允许重复键的 JSON object。

    输入参数：
        pairs：``json`` 解析器按输入顺序交付的键值对。
    输出返回值：
        键唯一的普通字典。
    异常：
        _WPOrderPayloadError：同一 object 中出现重复键。
    """

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _WPOrderPayloadError
        result[key] = value
    return result


def _normalize_credit_card_ids(values: Collection[str]) -> frozenset[str]:
    """将调用方提供的信用卡网关 ID 验证为闭集。

    输入参数：
        values：店铺配置声明的 payment method ID 集合。
    输出返回值：
        去重后的不可变 ID 闭集。
    异常：
        _WPOrderPayloadError：容器或 ID 类型、长度无效。
    """

    if isinstance(values, (str, bytes)) or not isinstance(values, Collection):
        raise _WPOrderPayloadError
    normalized = frozenset(values)
    if any(
        not isinstance(value, str) or not value or len(value) > 128
        for value in normalized
    ):
        raise _WPOrderPayloadError
    return normalized


def _parse_order(
    *,
    logical_store_id: str,
    raw: object,
    credit_card_payment_method_ids: frozenset[str],
) -> ObservedCheckoutOrder:
    """解析单个已解码订单并构造现有 DTO。

    输入参数：
        logical_store_id：目标 logical store 身份。
        raw：JSON ``orders`` 数组的一个成员。
        credit_card_payment_method_ids：已验证的信用卡网关闭集。
    输出返回值：
        一个完整的 ``ObservedCheckoutOrder``。
    异常：
        _WPOrderPayloadError：订单结构或映射字段无效。
    """

    _require_exact_keys(raw, _ORDER_KEYS)
    order_id = raw["order_id"]
    status = raw["status"]
    payment_method = raw["payment_method"]
    billing = raw["billing"]
    items = raw["items"]
    if (
        not _is_positive_integer(order_id)
        or not isinstance(status, str)
        or not isinstance(payment_method, str)
        or not payment_method
        or not isinstance(billing, dict)
        or not isinstance(items, list)
    ):
        raise _WPOrderPayloadError
    return ObservedCheckoutOrder(
        logical_store_id=logical_store_id,
        order_identity=str(order_id),
        products=tuple(_parse_item(item) for item in items),
        checkout_state=("pending" if status == "on-hold" else status),
        payment_kind=_map_payment_kind(
            payment_method,
            credit_card_payment_method_ids,
        ),
        billing_profile=_parse_billing(billing),
    )


def _parse_item(raw: object) -> ObservedCheckoutProduct:
    """验证单个订单行的 ID，并映射 slug 与数量。

    输入参数：
        raw：JSON ``items`` 数组的一个成员。
    输出返回值：
        不包含商品显示名的 ``ObservedCheckoutProduct``。
    异常：
        _WPOrderPayloadError：ID、数量或 canonical slug 缺失。
    """

    _require_exact_keys(raw, _ITEM_KEYS)
    product_id = raw["product_id"]
    variation_id = raw["variation_id"]
    quantity = raw["quantity"]
    canonical_slug = raw["canonical_slug"]
    if (
        not _is_non_negative_integer(product_id)
        or not _is_non_negative_integer(variation_id)
        or not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or quantity < 1
        or not isinstance(canonical_slug, str)
        or not canonical_slug
    ):
        raise _WPOrderPayloadError
    return ObservedCheckoutProduct(
        canonical_slug=canonical_slug,
        quantity=quantity,
    )


def _parse_billing(raw: dict[object, object]) -> ObservedCheckoutProfile:
    """将账单字段映射为评价器的完整 profile DTO。

    输入参数：
        raw：已确认为 JSON object 的 billing 字段。
    输出返回值：
        姓名合并后的 ``ObservedCheckoutProfile``。
    异常：
        _WPOrderPayloadError：必需字段缺失或不是字符串。
    """

    _require_exact_keys(raw, _BILLING_KEYS)
    names = (raw["first_name"], raw["last_name"])
    fields = (
        *names,
        raw["email"],
        raw["address_1"],
        raw["postcode"],
        raw["city"],
        raw["state"],
        raw["country"],
    )
    if any(not isinstance(value, str) or not value for value in fields):
        raise _WPOrderPayloadError
    return ObservedCheckoutProfile(
        full_name=f"{names[0]} {names[1]}",
        email=fields[2],
        address_line_1=fields[3],
        postcode=fields[4],
        city=fields[5],
        state=fields[6],
        country=fields[7],
    )


def _map_payment_kind(
    payment_method: str,
    credit_card_payment_method_ids: frozenset[str],
) -> str:
    """把 WooCommerce payment method ID 映射为闭合支付语义。

    输入参数：
        payment_method：非空 WooCommerce payment method ID。
        credit_card_payment_method_ids：该店信用卡网关 ID 闭集。
    输出返回值：
        ``credit_card``、``bank_transfer``、``cash`` 或 ``other``。
    """

    if payment_method == "bacs":
        return "bank_transfer"
    if payment_method == "cod":
        return "cash"
    if payment_method in credit_card_payment_method_ids:
        return "credit_card"
    return "other"


def _is_non_negative_integer(value: object) -> bool:
    """判定 JSON 值是否为非布尔的非负整数。

    输入参数：
        value：待验证 JSON 标量。
    输出返回值：
        仅当值为非负 ``int`` 且不是 ``bool`` 时返回真。
    """

    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_integer(value: object) -> bool:
    """判定 JSON 值是否为非布尔的正整数。

    输入参数：
        value：待验证 JSON 标量。
    输出返回值：
        仅当值为大于零的 ``int`` 且不是 ``bool`` 时返回真。
    """

    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require_exact_keys(
    value: object,
    expected: frozenset[str],
) -> None:
    """验证 JSON object 的键集与 schema 声明完全相等。

    输入参数：
        value：待验证的 JSON 值。
        expected：当前层级允许且必需的字段闭集。
    输出返回值：
        无；键集完全相等时正常返回。
    异常：
        _WPOrderPayloadError：值不是 object，或存在缺失/未知字段。
    """

    if not isinstance(value, dict) or frozenset(value) != expected:
        raise _WPOrderPayloadError
