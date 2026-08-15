"""WebMall WP-CLI 订单 JSON 纯解析器契约测试。"""

from __future__ import annotations

import copy
import json

import pytest

from paraguibench.integrations.webmall.evidence_contracts import (
    ObservedOrderIdentity,
    OrderIdentityBatch,
    WebMallEvidenceContractError,
)
from paraguibench.integrations.webmall.wp_order_parser import (
    MAX_WP_CLI_ORDER_PAYLOAD_BYTES,
    parse_wp_cli_order_details_payload,
    parse_wp_cli_order_identity_payload,
    parse_wp_cli_order_payload,
)


def _valid_payload() -> dict[str, object]:
    """构造一个字段闭合的 WP-CLI 订单载荷。

    输入参数：
        无。
    输出返回值：
        仅含 schema 声明字段的可变字典，便于后续测试定点改写。
    """

    return {
        "schema_version": 1,
        "complete": True,
        "orders": [
            {
                "order_id": 42,
                "status": "completed",
                "payment_method": "stripe-card",
                "billing": {
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "email": "ada@example.invalid",
                    "address_1": "1 Analytical Engine Way",
                    "postcode": "SW1A 1AA",
                    "city": "London",
                    "state": "London",
                    "country": "GB",
                },
                "items": [
                    {
                        "product_id": 7,
                        "variation_id": 0,
                        "quantity": 2,
                        "canonical_slug": "analytical-engine",
                    }
                ],
            }
        ],
    }


def _encode(value: object) -> bytes:
    """以稳定 UTF-8 JSON 形式编码测试载荷。

    输入参数：
        value：待编码的 JSON 值。
    输出返回值：
        不包含 ASCII 转义替代的 UTF-8 bytes。
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _valid_v2_details_payload() -> dict[str, object]:
    """构造一个字段闭合的 WebMall v2 详情载荷。

    输入参数：
        无。
    输出返回值：
        仅将 v1 订单内容包装为 ``mode=details`` 的 v2 文档。
    """

    payload = _valid_payload()
    return {
        "schema_version": 2,
        "mode": "details",
        "complete": True,
        "orders": payload["orders"],
    }


def test_parse_identity_payload_returns_private_complete_batch() -> None:
    """验证 identity 模式只产生订单身份且 repr 不泄漏其值。

    输入参数：
        无。
    输出返回值：
        无；断言 v2 identity envelope 转为完整闭集 DTO。
    """

    private_order_id = "4242"
    batch = parse_wp_cli_order_identity_payload(
        logical_store_id="store-1",
        payload=_encode(
            {
                "schema_version": 2,
                "mode": "identities",
                "complete": True,
                "order_ids": [41, int(private_order_id)],
            }
        ),
    )

    assert isinstance(batch, OrderIdentityBatch)
    assert batch.complete is True
    assert batch.logical_store_id == "store-1"
    assert batch.identities == (
        ObservedOrderIdentity("store-1", "41"),
        ObservedOrderIdentity("store-1", private_order_id),
    )
    assert private_order_id not in repr(batch)
    assert private_order_id not in repr(batch.identities[1])


def test_parse_details_payload_requires_and_returns_exact_requested_set() -> None:
    """验证 v2 详情仅对调用方声明的新订单生成严格 DTO。

    输入参数：
        无。
    输出返回值：
        无；断言唯一订单身份与请求闭集完全一致。
    """

    orders = parse_wp_cli_order_details_payload(
        logical_store_id="store-1",
        payload=_encode(_valid_v2_details_payload()),
        credit_card_payment_method_ids=frozenset({"stripe-card"}),
        expected_order_identities=("42",),
    )

    assert tuple(order.order_identity for order in orders) == ("42",)
    assert orders[0].checkout_state == "completed"


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_parse_details_payload_rejects_non_exact_identity_set(
    mutation: str,
) -> None:
    """验证 details 结果缺失、夹带或重复 identity 均 fail-closed。

    输入参数：
        mutation：需要注入的 identity 闭集破坏类型。
    输出返回值：
        无；断言三种破坏都不能进入 evaluator DTO。
    """

    payload = _valid_v2_details_payload()
    orders = payload["orders"]  # type: ignore[assignment]
    expected = ("42",)
    if mutation == "missing":
        orders.clear()  # type: ignore[union-attr]
    else:
        second = copy.deepcopy(orders[0])  # type: ignore[index]
        if mutation == "extra":
            second["order_id"] = 43
        orders.append(second)  # type: ignore[union-attr]

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_details_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
            expected_order_identities=expected,
        )


@pytest.mark.parametrize(
    "mutation",
    ["empty_billing", "empty_payment", "unknown_status", "deleted_slug"],
)
def test_parse_details_payload_keeps_new_order_evidence_strict(
    mutation: str,
) -> None:
    """验证仅历史订单脱离详情，新订单仍严格 fail-closed。

    输入参数：
        mutation：新订单中的缺 billing、缺 payment、未知状态
            或已删商品 slug 模拟。
    输出返回值：
        无；断言任一无法严格评价的新订单均报合同错误。
    """

    payload = _valid_v2_details_payload()
    order = payload["orders"][0]  # type: ignore[index]
    if mutation == "empty_billing":
        order["billing"]["email"] = ""  # type: ignore[index]
    elif mutation == "empty_payment":
        order["payment_method"] = ""  # type: ignore[index]
    elif mutation == "unknown_status":
        order["status"] = "checkout-draft"  # type: ignore[index]
    else:
        order["items"][0]["canonical_slug"] = ""  # type: ignore[index]

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_details_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
            expected_order_identities=("42",),
        )


def test_parse_wp_cli_order_payload_returns_existing_order_dto() -> None:
    """验证完整载荷转换为现有不可变订单 DTO。

    输入参数：
        无。
    输出返回值：
        无；断言 store、订单、商品、状态、支付与账单字段都已映射。
    """

    orders = parse_wp_cli_order_payload(
        logical_store_id="store-1",
        payload=_encode(_valid_payload()),
        credit_card_payment_method_ids=frozenset({"stripe-card"}),
    )

    assert len(orders) == 1
    order = orders[0]
    assert order.logical_store_id == "store-1"
    assert order.order_identity == "42"
    assert order.checkout_state == "completed"
    assert order.payment_kind == "credit_card"
    assert tuple((item.canonical_slug, item.quantity) for item in order.products) == (
        ("analytical-engine", 2),
    )
    assert order.billing_profile.full_name == "Ada Lovelace"
    assert order.billing_profile.email == "ada@example.invalid"
    assert order.billing_profile.address_line_1 == "1 Analytical Engine Way"
    assert order.billing_profile.postcode == "SW1A 1AA"
    assert order.billing_profile.city == "London"
    assert order.billing_profile.state == "London"
    assert order.billing_profile.country == "GB"


@pytest.mark.parametrize("level", ["root", "order", "billing", "item"])
def test_parse_wp_cli_order_payload_rejects_unknown_fields(level: str) -> None:
    """验证根、订单、账单与商品行都是字段闭合的。

    输入参数：
        level：注入未知字段的 JSON object 层级。
    输出返回值：
        无；断言任何层级多余字段都 fail-closed。
    """

    payload = _valid_payload()
    order = payload["orders"][0]  # type: ignore[index]
    targets = {
        "root": payload,
        "order": order,
        "billing": order["billing"],  # type: ignore[index]
        "item": order["items"][0],  # type: ignore[index]
    }
    targets[level]["unexpected"] = "must-not-be-ignored"  # type: ignore[index]

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


def test_parse_wp_cli_order_payload_rejects_duplicate_json_keys() -> None:
    """验证重复 JSON 键不能以“最后一值”规则绕过契约。

    输入参数：
        无。
    输出返回值：
        无；断言任意 object 的重复键触发固定合同错误。
    """

    payload = b'{"schema_version":1,"complete":false,"complete":true,"orders":[]}'

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=payload,
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


@pytest.mark.parametrize(
    ("source_status", "expected_status"),
    [
        ("completed", "completed"),
        ("processing", "processing"),
        ("pending", "pending"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
        ("refunded", "refunded"),
        ("on-hold", "pending"),
    ],
)
def test_parse_wp_cli_order_payload_keeps_all_supported_statuses(
    source_status: str,
    expected_status: str,
) -> None:
    """验证解析器保留全部 WooCommerce 目标状态。

    输入参数：
        source_status：WP-CLI 载荷中的原始状态。
        expected_status：现有 DTO 支持的归一化状态。
    输出返回值：
        无；断言非 completed 订单不被过滤，on-hold 归一为 pending。
    """

    payload = _valid_payload()
    payload["orders"][0]["status"] = source_status  # type: ignore[index]

    orders = parse_wp_cli_order_payload(
        logical_store_id="store-1",
        payload=_encode(payload),
        credit_card_payment_method_ids=frozenset({"stripe-card"}),
    )

    assert len(orders) == 1
    assert orders[0].checkout_state == expected_status


@pytest.mark.parametrize(
    ("payment_method", "expected_kind"),
    [
        ("stripe-card", "credit_card"),
        ("bacs", "bank_transfer"),
        ("cod", "cash"),
        ("cheque", "other"),
    ],
)
def test_parse_wp_cli_order_payload_maps_payment_method_closed_semantics(
    payment_method: str,
    expected_kind: str,
) -> None:
    """验证店铺信用卡闭集与 WooCommerce 固定 ID 映射。

    输入参数：
        payment_method：订单中的 payment method ID。
        expected_kind：期望的闭合支付语义。
    输出返回值：
        无；断言配置卡网关、bacs、cod 与其他非空 ID 的映射。
    """

    payload = _valid_payload()
    payload["orders"][0]["payment_method"] = payment_method  # type: ignore[index]

    orders = parse_wp_cli_order_payload(
        logical_store_id="store-1",
        payload=_encode(payload),
        credit_card_payment_method_ids=frozenset({"stripe-card"}),
    )

    assert orders[0].payment_kind == expected_kind


def test_parse_wp_cli_order_payload_rejects_missing_payment_method() -> None:
    """验证空 payment method 不能被推测成 other。

    输入参数：
        无。
    输出返回值：
        无；断言无法确定支付方式时 fail-closed。
    """

    payload = _valid_payload()
    payload["orders"][0]["payment_method"] = ""  # type: ignore[index]

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("product_id", -1),
        ("product_id", True),
        ("product_id", "7"),
        ("variation_id", -1),
        ("variation_id", False),
        ("variation_id", 1.0),
        ("quantity", 0),
        ("quantity", -1),
        ("quantity", True),
        ("quantity", "2"),
    ],
)
def test_parse_wp_cli_order_payload_rejects_invalid_item_numbers(
    field: str,
    invalid_value: object,
) -> None:
    """验证商品与 variation ID 非负且数量为正整数。

    输入参数：
        field：被改写的订单行数值字段。
        invalid_value：应被拒绝的边界值或错误类型。
    输出返回值：
        无；断言布尔、字符串和越界数值均 fail-closed。
    """

    payload = _valid_payload()
    payload["orders"][0]["items"][0][field] = invalid_value  # type: ignore[index]

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


def test_parse_wp_cli_order_payload_never_falls_back_to_display_name() -> None:
    """验证缺失 canonical slug 时不使用商品显示名补齐。

    输入参数：
        无。
    输出返回值：
        无；即使存在可读 name，缺失 canonical_slug 仍触发合同错误。
    """

    payload = _valid_payload()
    item = payload["orders"][0]["items"][0]  # type: ignore[index]
    del item["canonical_slug"]
    item["name"] = "PRIVATE PRODUCT DISPLAY NAME"

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


@pytest.mark.parametrize("invalid_order_id", [0, -1, True, "42"])
def test_parse_wp_cli_order_payload_requires_positive_integer_order_id(
    invalid_order_id: object,
) -> None:
    """验证 WooCommerce 订单身份是非布尔的正整数。

    输入参数：
        invalid_order_id：生产导出器不可能产生的订单 ID。
    输出返回值：
        无；断言零、负数、布尔与数字字符串均被拒绝。
    """

    payload = _valid_payload()
    payload["orders"][0]["order_id"] = invalid_order_id  # type: ignore[index]

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


@pytest.mark.parametrize(
    "invalid_payload",
    [
        b"",
        b"\xff",
        b'{"schema_version":1',
        b"[]",
        b'{"schema_version":true,"complete":true,"orders":[]}',
        (b'{"schema_version":' + b"1" * 5_000 + b',"complete":true,"orders":[]}'),
        b'{"schema_version":1,"complete":false,"orders":[]}',
        b'{"schema_version":1,"complete":true,"orders":{}}',
    ],
)
def test_parse_wp_cli_order_payload_rejects_invalid_envelopes(
    invalid_payload: bytes,
) -> None:
    """验证编码、JSON、版本、完整性与 orders 容器均严格。

    输入参数：
        invalid_payload：各类应 fail-closed 的原始 bytes。
    输出返回值：
        无；断言无效 UTF-8、截断 JSON 和不完整 envelope 都被拒绝。
    """

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=invalid_payload,
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


def test_parse_wp_cli_order_payload_rejects_payload_over_fixed_limit() -> None:
    """验证超过固定字节上限的载荷在解码前被拒绝。

    输入参数：
        无。
    输出返回值：
        无；断言上限加一字节触发合同错误。
    """

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=b"x" * (MAX_WP_CLI_ORDER_PAYLOAD_BYTES + 1),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


@pytest.mark.parametrize("level", ["root", "order", "billing", "item"])
def test_parse_wp_cli_order_payload_rejects_missing_fields(level: str) -> None:
    """验证各层 schema 的必需字段不可省略。

    输入参数：
        level：删除必需字段的 JSON object 层级。
    输出返回值：
        无；断言缺字段不被 ``dict.get`` 默认值掩盖。
    """

    payload = _valid_payload()
    order = payload["orders"][0]  # type: ignore[index]
    targets_and_keys = {
        "root": (payload, "complete"),
        "order": (order, "status"),
        "billing": (order["billing"], "email"),  # type: ignore[index]
        "item": (order["items"][0], "canonical_slug"),  # type: ignore[index]
    }
    target, key = targets_and_keys[level]
    del target[key]  # type: ignore[index]

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


def test_parse_wp_cli_order_payload_error_never_echoes_private_values() -> None:
    """验证公开错误文本不回显订单、profile 或 payload。

    输入参数：
        无。
    输出返回值：
        无；将同一隐私 sentinel 放入多个字段，断言异常
        文本与参数均不包含它。
    """

    sentinel = "PRIVATE-WEBMALL-SENTINEL-9f27"
    payload = _valid_payload()
    order = payload["orders"][0]  # type: ignore[index]
    order["status"] = sentinel  # type: ignore[index]
    order["billing"]["email"] = sentinel  # type: ignore[index]
    order["items"][0]["canonical_slug"] = sentinel  # type: ignore[index]

    with pytest.raises(WebMallEvidenceContractError) as captured:
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )

    assert sentinel not in str(captured.value)
    assert sentinel not in repr(captured.value.args)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_parse_wp_cli_order_payload_validates_store_even_when_orders_empty() -> None:
    """验证空订单列表不会跳过 logical store 身份验证。

    输入参数：
        无。
    输出返回值：
        无；断言无效 store ID 即使没有订单也不能通过。
    """

    payload = _valid_payload()
    payload["orders"] = []

    with pytest.raises(WebMallEvidenceContractError):
        parse_wp_cli_order_payload(
            logical_store_id="invalid/store",
            payload=_encode(payload),
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )


def test_parse_wp_cli_order_payload_converts_decoder_recursion_failure() -> None:
    """验证有界但过深的 JSON 不泄漏 decoder 内部异常。

    输入参数：
        无。
    输出返回值：
        无；断言解码递归上限也被转换成无上下文的固定错误。
    """

    depth = 2_000
    payload = b"[" * depth + b"0" + b"]" * depth

    with pytest.raises(WebMallEvidenceContractError) as captured:
        parse_wp_cli_order_payload(
            logical_store_id="store-1",
            payload=payload,
            credit_card_payment_method_ids=frozenset({"stripe-card"}),
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
