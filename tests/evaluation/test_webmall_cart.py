"""WebMall cart 纯评价协议测试。"""

from __future__ import annotations

from dataclasses import asdict
import json

import pytest

from paraguibench.evaluation.webmall.cart import evaluate_webmall_cart
from paraguibench.evaluation.webmall.cart import WebMallCartEvaluationError
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartItem,
    ObservedCartStore,
    ObservedCartWorker,
)


def _batch(
    *worker_carts: dict[str, tuple[tuple[str, int], ...]],
    complete: bool = True,
) -> CartObservationBatch:
    """从测试用店铺商品映射构造四店 cart 观测。

    输入参数：
        worker_carts：每个 worker 的 store ID 到 ``(slug, quantity)`` 元组映射。
        complete：批次和 worker 是否声明完整。
    输出返回值：
        按固定四店顺序构造的不可变观测批次。
    """

    store_ids = ("store-1", "store-2", "store-3", "store-4")
    return CartObservationBatch(
        complete=complete,
        workers=tuple(
            ObservedCartWorker(
                worker_id=f"worker-{index}",
                complete=complete,
                stores=tuple(
                    ObservedCartStore(
                        logical_store_id=store_id,
                        complete=complete,
                        items=tuple(
                            ObservedCartItem(slug, quantity)
                            for slug, quantity in cart.get(store_id, ())
                        ),
                    )
                    for store_id in store_ids
                ),
            )
            for index, cart in enumerate(worker_carts, start=1)
        ),
    )


def test_exact_cart_across_stores_on_one_worker_passes() -> None:
    """验证同一 worker 可在多店完成购物车闭集。

    输入参数：无；构造两个 logical store 各一件商品的完整证据。
    输出返回值：无；纯评价结果必须为二值满分且不泄露 slug。
    """

    secret_slug_a = "private-widget-alpha"
    secret_slug_b = "private-widget-beta"
    observation = CartObservationBatch(
        complete=True,
        workers=(
            ObservedCartWorker(
                worker_id="worker-1",
                complete=True,
                stores=(
                    ObservedCartStore(
                        logical_store_id="store-1",
                        complete=True,
                        items=(ObservedCartItem(secret_slug_a, 1),),
                    ),
                    ObservedCartStore(
                        logical_store_id="store-2",
                        complete=True,
                        items=(),
                    ),
                    ObservedCartStore(
                        logical_store_id="store-3",
                        complete=True,
                        items=(ObservedCartItem(secret_slug_b, 1),),
                    ),
                    ObservedCartStore(
                        logical_store_id="store-4",
                        complete=True,
                        items=(),
                    ),
                ),
            ),
        ),
    )

    result = evaluate_webmall_cart(
        (
            f"webmall://store-1/product/{secret_slug_a}",
            f"webmall://store-3/product/{secret_slug_b}",
        ),
        observation,
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.matched_product_quantity == 2
    assert result.missing_product_quantity == 0
    assert result.unexpected_product_quantity == 0
    assert secret_slug_a not in repr(result)
    assert secret_slug_b not in repr(result)


def test_unexpected_extra_product_fails_closed_world() -> None:
    """验证期望商品已命中时仍不得忽略额外商品。

    输入参数：无；同店购物车含期望商品和一件额外商品。
    输出返回值：无；结果必须为零分且只暴露额外数量。
    """

    expected_slug = "private-expected-widget"
    extra_slug = "private-extra-widget"

    result = evaluate_webmall_cart(
        (f"webmall://store-2/product/{expected_slug}",),
        _batch(
            {
                "store-2": (
                    (expected_slug, 1),
                    (extra_slug, 1),
                )
            }
        ),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.reason_codes == ("UNEXPECTED_PRODUCT",)
    assert result.matched_product_quantity == 1
    assert result.unexpected_product_quantity == 1
    assert expected_slug not in repr(result)
    assert extra_slug not in repr(result)
    public_details = json.dumps(asdict(result), ensure_ascii=False)
    assert expected_slug not in public_details
    assert extra_slug not in public_details
    assert "store-2" not in public_details
    assert "worker-1" not in public_details


def test_same_slug_in_wrong_store_is_missing_and_unexpected() -> None:
    """验证商品 slug 相同也不得跨 logical store 命中。

    输入参数：无；gold 指向 store-2，可靠终态只在 store-3 有同 slug。
    输出返回值：无；必须同时计一件缺失和一件额外商品。
    """

    private_slug = "private-shared-offer"
    result = evaluate_webmall_cart(
        (f"webmall://store-2/product/{private_slug}",),
        _batch({"store-3": ((private_slug, 1),)}),
    )

    assert result.passed is False
    assert result.reason_codes == (
        "MISSING_PRODUCT",
        "UNEXPECTED_PRODUCT",
    )
    assert result.matched_product_quantity == 0
    assert result.missing_product_quantity == 1
    assert result.unexpected_product_quantity == 1
    assert private_slug not in repr(result)


def test_amp_token_is_the_only_historical_product_identity_tolerance() -> None:
    """验证保留旧修复版唯一的 ``&``/``amp`` 商品身份容差。

    输入参数：无；gold slug 不含 amp，权威购物车 slug 在同位置含 amp。
    输出返回值：无；去除独立 amp 词元后其余全部词元一致时通过。
    """

    expected_slug = "private-glass-side-front-widget"
    observed_slug = "private-glass-side-amp-front-widget"
    result = evaluate_webmall_cart(
        (f"webmall://store-1/product/{expected_slug}",),
        _batch({"store-1": ((observed_slug, 1),)}),
    )

    assert result.passed is True


def test_numeric_model_suffix_remains_part_of_product_identity() -> None:
    """验证控制 amp 容差不会放宽数字型号。

    输入参数：无；gold 型号为 4070，购物车商品型号为 4070-3。
    输出返回值：无；必须计为一件缺失和一件额外商品。
    """

    result = evaluate_webmall_cart(
        ("webmall://store-3/product/private-model-4070",),
        _batch({"store-3": (("private-model-4070-3", 1),)}),
    )

    assert result.passed is False
    assert result.missing_product_quantity == 1
    assert result.unexpected_product_quantity == 1


def test_same_product_quantity_greater_than_gold_is_rejected() -> None:
    """验证同店同商品的数量也属于闭集身份。

    输入参数：无；gold 要求一件，终态购物车中数量为二。
    输出返回值：无；命中一件但额外一件，并记数量不匹配。
    """

    private_slug = "private-quantity-widget"
    result = evaluate_webmall_cart(
        (f"webmall://store-4/product/{private_slug}",),
        _batch({"store-4": ((private_slug, 2),)}),
    )

    assert result.passed is False
    assert result.reason_codes == (
        "UNEXPECTED_PRODUCT",
        "PRODUCT_QUANTITY_MISMATCH",
    )
    assert result.matched_product_quantity == 1
    assert result.missing_product_quantity == 0
    assert result.unexpected_product_quantity == 1
    assert result.quantity_mismatch_identity_count == 1


def test_repeated_gold_url_can_require_quantity_two() -> None:
    """验证 logical URL 多集合可明确表达同商品数量二。

    输入参数：无；gold 重复同一 URL 两次，可靠证据数量为二。
    输出返回值：无；严格多集合比较必须满分通过。
    """

    private_slug = "private-double-widget"
    url = f"webmall://store-1/product/{private_slug}"
    result = evaluate_webmall_cart(
        (url, url),
        _batch({"store-1": ((private_slug, 2),)}),
    )

    assert result.passed is True
    assert result.expected_product_quantity == 2
    assert result.observed_product_quantity == 2
    assert result.matched_product_quantity == 2


def test_same_expected_product_on_two_workers_is_an_extra_side_effect() -> None:
    """验证同一期望商品跨 worker 重复加购不能通过。

    输入参数：无；两个 worker 的同一店都含同一期望商品。
    输出返回值：无；一个 worker 可匹配，另一个必须计额外副作用。
    """

    private_slug = "private-duplicated-widget"
    cart = {"store-2": ((private_slug, 1),)}
    result = evaluate_webmall_cart(
        (f"webmall://store-2/product/{private_slug}",),
        _batch(cart, cart),
    )

    assert result.passed is False
    assert result.reason_codes == (
        "UNEXPECTED_PRODUCT",
        "MULTI_WORKER_SIDE_EFFECT",
    )
    assert result.matched_product_quantity == 1
    assert result.unexpected_product_quantity == 1
    assert result.nonselected_worker_product_quantity == 1


def test_complementary_worker_carts_are_not_merged() -> None:
    """验证两个 worker 的互补购物车不得跨 worker 拼成完成态。

    输入参数：无；两件期望商品被分别加入两个 worker。
    输出返回值：无；最佳单 worker 只命中一件，整体必须失败。
    """

    private_slug_a = "private-split-alpha"
    private_slug_b = "private-split-beta"
    result = evaluate_webmall_cart(
        (
            f"webmall://store-1/product/{private_slug_a}",
            f"webmall://store-3/product/{private_slug_b}",
        ),
        _batch(
            {"store-1": ((private_slug_a, 1),)},
            {"store-3": ((private_slug_b, 1),)},
        ),
    )

    assert result.passed is False
    assert result.reason_codes == (
        "MISSING_PRODUCT",
        "UNEXPECTED_PRODUCT",
        "MULTI_WORKER_SIDE_EFFECT",
    )
    assert result.matched_product_quantity == 1
    assert result.missing_product_quantity == 1
    assert result.unexpected_product_quantity == 1


def test_any_incomplete_worker_snapshot_is_evaluator_error() -> None:
    """验证任一 worker 读取不完整时不得伪装成 Agent 零分。

    输入参数：无；构造 ``complete=false`` 的四店批次。
    输出返回值：无；纯评价器必须抛不回显购物车值的固定错误。
    """

    private_slug = "private-incomplete-widget"
    observation = _batch(
        {"store-1": ((private_slug, 1),)},
        complete=False,
    )

    with pytest.raises(
        WebMallCartEvaluationError,
        match="observation 不完整",
    ) as captured:
        evaluate_webmall_cart(
            (f"webmall://store-1/product/{private_slug}",),
            observation,
        )

    assert private_slug not in str(captured.value)


@pytest.mark.parametrize(
    "invalid_gold",
    [
        (),
        "webmall://store-1/product/private-widget",
        ("https://private.invalid/product/private-widget",),
        ("webmall://store-9/product/private-widget",),
        ("webmall://store-1/product/private-widget?quantity=1",),
        ("webmall://store-1/product/private%GGwidget",),
    ],
)
def test_invalid_gold_contract_is_sanitized_evaluator_error(
    invalid_gold: object,
) -> None:
    """验证 cart gold 必须是非空、固定四店的严格 logical URL 序列。

    输入参数：
        invalid_gold：空序列、裸字符串、runtime URL、未知 store 或损坏编码。
    输出返回值：
        无；所有非法 gold 均抛 evaluator error，错误不回显原值。
    """

    with pytest.raises(WebMallCartEvaluationError) as captured:
        evaluate_webmall_cart(
            invalid_gold,  # type: ignore[arg-type]
            _batch({}),
        )

    assert "private-widget" not in str(captured.value)
    assert "private.invalid" not in str(captured.value)
