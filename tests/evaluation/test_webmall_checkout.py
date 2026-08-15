"""WebMall closed-world checkout 评价协议测试。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from paraguibench.evaluation.webmall import (
    FIND_AND_ORDER_PROTOCOL_ID,
    CheckoutEvaluationContractError,
    CheckoutObservationBatch,
    ObservedCheckoutOrder,
    ObservedCheckoutProfile,
    ObservedCheckoutProduct,
    evaluate_webmall_checkout,
    evaluate_webmall_find_and_order,
)
from paraguibench.evaluation.webmall.identity import product_identity_tokens
from paraguibench.integrations.webmall import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKOUT_TASK_PATHS = tuple(
    sorted(
        (_REPO_ROOT / "benchmark" / "tasks").glob(
            "Operation-OnlineShopping-Checkout-*.json"
        )
    )
    + sorted(
        (_REPO_ROOT / "benchmark" / "tasks").glob(
            "Operation-OnlineShopping-EndToEnd-*.json"
        )
    )
)
_CHECKOUT_PROFILE = json.loads(
    (
        _REPO_ROOT / "benchmark" / "fixtures" / "webmall" / "checkout-profile-v1.json"
    ).read_text(encoding="utf-8")
)["profile"]


def _observation(
    orders: tuple[ObservedCheckoutOrder, ...],
    *,
    complete: bool = True,
    scanned_store_ids: tuple[str, ...] = WEBMALL_LOGICAL_STORE_IDS,
) -> CheckoutObservationBatch:
    """构造显式携带固定四店扫描范围的测试 observation。

    输入参数：
        orders：本例 Attempt baseline 之后的新增订单元组。
        complete：是否声明扫描成功完成；默认完整。
        scanned_store_ids：按固定 universe 顺序实际扫描的商店闭集。
    输出返回值：
        经过生产数据合同验证的不可变 checkout 批次。
    """

    return CheckoutObservationBatch(
        store_universe_id=WEBMALL_STORE_UNIVERSE_ID,
        scanned_store_ids=scanned_store_ids,
        complete=complete,
        orders=orders,
    )


def _matching_billing_profile() -> ObservedCheckoutProfile:
    """构造与版本化 synthetic checkout fixture 等价的订单账单证据。

    输入参数：
        无；值来自发布的 synthetic-public fixture。
    输出返回值：
        仅在 evaluator 可信内存中使用的完整 billing observation。
    """

    return ObservedCheckoutProfile(
        full_name="ParaGUI Test User",
        email="checkout-v1@example.invalid",
        address_line_1="100 Benchmark Avenue",
        postcode="94107",
        city="San Francisco",
        state="CA",
        country="US",
    )


def _evaluate_checkout(
    expected_urls: tuple[str, ...],
    observation: CheckoutObservationBatch,
):
    """使用固定 checkout fixture 调用完整评价协议。

    输入参数：
        expected_urls：canonical logical product URL 闭集。
        observation：Attempt 终态订单闭包。
    输出返回值：
        含商品、checkout 状态、支付语义和账单资料的完整评价。
    """

    return evaluate_webmall_checkout(
        expected_urls,
        _CHECKOUT_PROFILE,
        observation,
    )


def _evaluate_find_and_order(
    expected_urls: tuple[str, ...],
    submitted_urls: tuple[str, ...],
    observation: CheckoutObservationBatch,
):
    """使用固定 checkout fixture 调用 EndToEnd 组合协议。

    输入参数：
        expected_urls：canonical logical product URL 闭集。
        submitted_urls：Agent 最终报告的 logical URL 多集合。
        observation：Attempt 终态订单闭包。
    输出返回值：
        报告 URL 与完整 checkout 状态的 AND 结果。
    """

    return evaluate_webmall_find_and_order(
        expected_urls,
        submitted_urls,
        _CHECKOUT_PROFILE,
        observation,
    )


def _load_expected_urls(task_path: Path) -> tuple[str, ...]:
    """读取测试所需的 canonical expected URL 闭集。

    输入参数：
        task_path：release-v1 中 Checkout 或 EndToEnd task JSON 路径。
    输出返回值：
        不改变顺序的 ``expected_urls`` 元组。
    """

    task = json.loads(task_path.read_text(encoding="utf-8"))
    return tuple(task["expected_urls"])


def _perfect_observation(
    expected_urls: tuple[str, ...],
) -> CheckoutObservationBatch:
    """按 logical store 把 gold 编译为仅用于测试的完整订单证据。

    输入参数：
        expected_urls：canonical logical product URL 闭集。
    输出返回值：
        每个 store 恰好一单、同店商品合并的完整 observation batch。
    """

    products_by_store: dict[str, list[ObservedCheckoutProduct]] = {}
    for logical_url in expected_urls:
        parts = urlsplit(logical_url)
        products_by_store.setdefault(parts.netloc, []).append(
            ObservedCheckoutProduct(
                canonical_slug=parts.path.removeprefix("/product/"),
                quantity=1,
            )
        )
    orders = tuple(
        ObservedCheckoutOrder(
            logical_store_id=store_id,
            order_identity=f"order-{index}",
            products=tuple(products),
            checkout_state="completed",
            payment_kind="credit_card",
            billing_profile=_matching_billing_profile(),
        )
        for index, (store_id, products) in enumerate(
            sorted(products_by_store.items()),
            start=1,
        )
    )
    return _observation(orders)


@pytest.mark.parametrize("task_path", _CHECKOUT_TASK_PATHS, ids=lambda path: path.stem)
def test_all_checkout_tasks_compile_and_accept_exact_closed_world_orders(
    task_path: Path,
) -> None:
    """验证 16 个 canonical task 都遵循统一的按 store 分组语义。

    输入参数：
        task_path：pytest 参数化提供的一个 Checkout/EndToEnd task。
    输出返回值：
        无；严格闭集证据均以二值 1.0 通过，且任务总数固定为 16。
    """

    assert len(_CHECKOUT_TASK_PATHS) == 16
    expected_urls = _load_expected_urls(task_path)

    result = _evaluate_checkout(
        expected_urls,
        _perfect_observation(expected_urls),
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.reason_codes == ()


def test_end_to_end_001_requires_two_orders_across_two_stores() -> None:
    """验证跨店任务完成两单才通过，只买旧 evaluator 的第一件会失败。

    输入参数：
        无；读取真实 EndToEnd-001 的两个 logical product URL。
    输出返回值：
        无；完美两单通过，删除 store-3 订单后得到一个缺失订单和零分。
    """

    expected_urls = _load_expected_urls(
        _REPO_ROOT
        / "benchmark"
        / "tasks"
        / "Operation-OnlineShopping-EndToEnd-001.json"
    )
    complete = _perfect_observation(expected_urls)

    passed = _evaluate_checkout(expected_urls, complete)
    missing_second_store = _evaluate_checkout(
        expected_urls,
        _observation(complete.orders[:1]),
    )

    assert passed.expected_order_count == 2
    assert passed.observed_order_count == 2
    assert missing_second_store.passed is False
    assert missing_second_store.score == 0.0
    assert missing_second_store.missing_order_count == 1
    assert missing_second_store.reason_codes == ("MISSING_ORDER",)


def test_end_to_end_005_requires_one_order_containing_both_products() -> None:
    """验证同店双商品必须合并为一单，拆成两单属于额外外部副作用。

    输入参数：
        无；读取真实 EndToEnd-005 的两个 store-3 product URL。
    输出返回值：
        无；一单两商品通过，同店两个不同 order ID 即使商品各自正确也失败。
    """

    expected_urls = _load_expected_urls(
        _REPO_ROOT
        / "benchmark"
        / "tasks"
        / "Operation-OnlineShopping-EndToEnd-005.json"
    )
    combined = _perfect_observation(expected_urls)
    split = _observation(
        tuple(
            ObservedCheckoutOrder(
                logical_store_id="store-3",
                order_identity=f"split-{index}",
                products=(
                    ObservedCheckoutProduct(
                        canonical_slug=urlsplit(url).path.removeprefix("/product/"),
                        quantity=1,
                    ),
                ),
                checkout_state="completed",
                payment_kind="credit_card",
                billing_profile=_matching_billing_profile(),
            )
            for index, url in enumerate(expected_urls, start=1)
        )
    )

    assert _evaluate_checkout(expected_urls, combined).passed is True
    split_result = _evaluate_checkout(expected_urls, split)
    assert split_result.passed is False
    assert split_result.unexpected_order_count == 1
    assert "UNEXPECTED_ORDER" in split_result.reason_codes


@pytest.mark.parametrize(
    "products",
    [
        (
            ObservedCheckoutProduct(
                canonical_slug="expected-product",
                quantity=2,
            ),
        ),
        (
            ObservedCheckoutProduct(
                canonical_slug="expected-product",
                quantity=1,
            ),
            ObservedCheckoutProduct(
                canonical_slug="extra-product",
                quantity=1,
            ),
        ),
    ],
)
def test_product_multiset_rejects_wrong_quantity_and_extra_product(
    products: tuple[ObservedCheckoutProduct, ...],
) -> None:
    """验证商品按严格多集合比较，错误数量与额外商品都不能部分通过。

    输入参数：
        products：数量为 2，或包含额外商品的观测商品元组。
    输出返回值：
        无；订单存在但商品闭集不匹配，正式得分仍为二值零分。
    """

    result = _evaluate_checkout(
        ("webmall://store-1/product/expected-product",),
        _observation(
            (
                ObservedCheckoutOrder(
                    logical_store_id="store-1",
                    order_identity="order-1",
                    products=products,
                    checkout_state="completed",
                    payment_kind="credit_card",
                    billing_profile=_matching_billing_profile(),
                ),
            )
        ),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.product_mismatch_order_count == 1
    assert result.reason_codes == ("PRODUCT_MISMATCH",)


@pytest.mark.parametrize(
    ("order_override", "reason_code", "count_field"),
    [
        (
            {"checkout_state": "pending"},
            "CHECKOUT_STATE_MISMATCH",
            "checkout_state_mismatch_order_count",
        ),
        (
            {"payment_kind": "bank_transfer"},
            "PAYMENT_METHOD_MISMATCH",
            "payment_mismatch_order_count",
        ),
        (
            {
                "billing_profile": replace(
                    _matching_billing_profile(),
                    city="private-wrong-city",
                )
            },
            "BILLING_PROFILE_MISMATCH",
            "billing_profile_mismatch_order_count",
        ),
    ],
)
def test_checkout_requires_completed_credit_card_and_exact_billing_profile(
    order_override: dict[str, object],
    reason_code: str,
    count_field: str,
) -> None:
    """验证商品正确不会掩盖 checkout、支付或账单资料错误。

    输入参数：
        order_override：只改变一项可完整观测的订单终态。
        reason_code/count_field：期望的脱敏失败原因与计数字段。
    输出返回值：
        无；完整但值错误的 Agent 状态得 0 分，不升级为 evaluator ERROR。
    """

    base = _perfect_observation(("webmall://store-1/product/expected-product",)).orders[
        0
    ]
    observation = _observation(
        (replace(base, **order_override),),
    )

    result = _evaluate_checkout(
        ("webmall://store-1/product/expected-product",),
        observation,
    )

    assert result.passed is False
    assert result.score == 0.0
    assert reason_code in result.reason_codes
    assert getattr(result, count_field) == 1
    assert "private-wrong-city" not in repr(result)


def test_duplicate_sighting_is_deduplicated_but_conflicting_sighting_errors() -> None:
    """验证多 VM 重复看到同一订单可去重，而同 ID 冲突证据失败关闭。

    输入参数：
        无；构造相同 key 的完全相同和内容冲突两类重复观测。
    输出返回值：
        无；相同重复通过并记录计数，冲突重复抛 evaluator contract error。
    """

    expected = ("webmall://store-1/product/expected-product",)
    order = ObservedCheckoutOrder(
        logical_store_id="store-1",
        order_identity="order-1",
        products=(ObservedCheckoutProduct("expected-product", 1),),
        checkout_state="completed",
        payment_kind="credit_card",
        billing_profile=_matching_billing_profile(),
    )
    duplicate_result = _evaluate_checkout(
        expected,
        _observation((order, order)),
    )

    assert duplicate_result.passed is True
    assert duplicate_result.observed_order_count == 1
    assert duplicate_result.duplicate_observation_count == 1

    conflict = ObservedCheckoutOrder(
        logical_store_id="store-1",
        order_identity="order-1",
        products=(ObservedCheckoutProduct("different-product", 1),),
        checkout_state="completed",
        payment_kind="credit_card",
        billing_profile=_matching_billing_profile(),
    )
    with pytest.raises(CheckoutEvaluationContractError, match="conflict"):
        _evaluate_checkout(
            expected,
            _observation((order, conflict)),
        )


@pytest.mark.parametrize(
    "expected_urls",
    [
        (),
        ("https://shop.example/product/item",),
        ("webmall://store-1/cart",),
        ("webmall://store-1/product/item?variant=1",),
        (
            "webmall://store-1/product/duplicate",
            "webmall://store-1/product/duplicate",
        ),
    ],
)
def test_invalid_gold_and_incomplete_observation_fail_as_evaluator_errors(
    expected_urls: tuple[str, ...],
) -> None:
    """验证 gold 形状和证据扫描不完整属于 evaluator error 而非 Agent 零分。

    输入参数：
        expected_urls：空、非 logical product、带 query 或重复的非法 gold。
    输出返回值：
        无；非法 gold 与 ``complete=False`` 均抛类型安全 contract error。
    """

    with pytest.raises(CheckoutEvaluationContractError):
        _evaluate_checkout(
            expected_urls,
            _observation(()),
        )

    with pytest.raises(CheckoutEvaluationContractError, match="complete"):
        _evaluate_checkout(
            ("webmall://store-1/product/item",),
            _observation((), complete=False, scanned_store_ids=()),
        )


def test_complete_observation_requires_full_four_store_coverage() -> None:
    """验证 complete 批次不能只声明或扫描 gold 出现的商店。

    输入参数：
        无；构造只声明 store-1 已扫描、却把 complete 设为真的批次。
    输出返回值：
        无；数据合同立即抛 evaluator error，而不是把漏扫解释为空订单。
    """

    with pytest.raises(CheckoutEvaluationContractError, match="coverage"):
        _observation((), scanned_store_ids=("store-1",))


@pytest.mark.parametrize(
    ("field_name", "unknown_value"),
    [("checkout_state", "unknown"), ("payment_kind", "unknown")],
)
def test_unknown_checkout_or_payment_state_is_evaluator_error(
    field_name: str,
    unknown_value: str,
) -> None:
    """验证无法确定的权威订单字段不能进入完整 observation。

    输入参数：
        field_name：本例变异的 checkout 状态或支付语义字段。
        unknown_value：生产 parser 不得下沉到 evaluator 的 unknown 标记。
    输出返回值：
        无；构造阶段以 evaluator contract error 失败关闭。
    """

    base = _perfect_observation(("webmall://store-1/product/expected-product",)).orders[
        0
    ]

    with pytest.raises(CheckoutEvaluationContractError):
        replace(base, **{field_name: unknown_value})


def test_order_in_non_gold_store_is_closed_world_failure() -> None:
    """验证非期望商店中的新增订单不会因 gold scope 较窄而漏检。

    输入参数：
        无；期望只在 store-1，下单证据却同时包含 store-4 额外订单。
    输出返回值：
        无；结果包含 ``UNEXPECTED_ORDER`` 并保持二值零分。
    """

    expected = ("webmall://store-1/product/expected-product",)
    correct = _perfect_observation(expected).orders[0]
    unexpected = replace(
        correct,
        logical_store_id="store-4",
        order_identity="unexpected-store-order",
    )
    result = _evaluate_checkout(
        expected,
        _observation((correct, unexpected)),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.unexpected_order_count == 1
    assert "UNEXPECTED_ORDER" in result.reason_codes


def test_identity_compatibility_and_result_privacy() -> None:
    """验证正式证据使用 canonical slug，且结果不回显订单身份。

    输入参数：
        无；期望与观测都使用可核对的 canonical slug 和私有 order ID。
    输出返回值：
        无；闭集身份通过，公开结果 repr 只含计数和 reason code。
    """

    private_order_id = "order-private-sentinel"
    private_product_slug = "private-product-sentinel"
    result = _evaluate_checkout(
        (f"webmall://store-1/product/{private_product_slug}",),
        _observation(
            (
                ObservedCheckoutOrder(
                    logical_store_id="store-1",
                    order_identity=private_order_id,
                    products=(ObservedCheckoutProduct(private_product_slug, 1),),
                    checkout_state="completed",
                    payment_kind="credit_card",
                    billing_profile=_matching_billing_profile(),
                ),
            )
        ),
    )

    assert result.passed is True
    assert private_order_id not in repr(result)
    assert private_product_slug not in repr(result)


def test_end_to_end_combines_strict_report_multiset_with_order_state() -> None:
    """验证 EndToEnd 必须同时满足报告 URL 多集合和实际订单闭集。

    输入参数：
        无；读取真实 EndToEnd-001 的跨店两个 logical URL。
    输出返回值：
        无；只有报告和订单都精确时通过，空报告、错误 URL
        或重复 URL 即使订单正确也必须得零分。
    """

    expected_urls = _load_expected_urls(
        _REPO_ROOT
        / "benchmark"
        / "tasks"
        / "Operation-OnlineShopping-EndToEnd-001.json"
    )
    observation = _perfect_observation(expected_urls)

    passed = _evaluate_find_and_order(
        expected_urls,
        expected_urls,
        observation,
    )
    empty = _evaluate_find_and_order(
        expected_urls,
        (),
        observation,
    )
    wrong = _evaluate_find_and_order(
        expected_urls,
        ("webmall://store-1/product/wrong-product",),
        observation,
    )
    duplicate = _evaluate_find_and_order(
        expected_urls,
        (*expected_urls, expected_urls[0]),
        observation,
    )

    assert passed.protocol_id == FIND_AND_ORDER_PROTOCOL_ID
    assert passed.passed is True
    assert passed.score == 1.0
    for result in (empty, wrong, duplicate):
        assert result.passed is False
        assert result.score == 0.0
        assert "REPORTED_URL_MISMATCH" in result.reason_codes
        assert result.reported_url_mismatch_count > 0


@pytest.mark.parametrize(
    "bad_url",
    [
        "webmall://store-1/product/%FFabc",
        "webmall://store-1/product/item%00suffix",
        "webmall://store-1/product/item%0Asuffix",
        "webmall://store-1/product/item%2Fsibling",
        "webmall://store-1/product/item\\sibling",
    ],
)
def test_logical_product_url_rejects_invalid_utf8_and_path_controls(
    bad_url: str,
) -> None:
    """验证 logical product URL 拒绝非法 UTF-8、控制字符和隐式分隔符。

    输入参数：
        bad_url：含非法 percent 解码结果或路径分隔语义的 URL。
    输出返回值：
        无；非 canonical gold 必须是 evaluator contract error。
    """

    with pytest.raises(CheckoutEvaluationContractError):
        _evaluate_checkout(
            (bad_url,),
            _observation(()),
        )


def test_logical_product_url_maps_unencodable_unicode_to_contract_error() -> None:
    """验证不可编码的 Unicode surrogate 被稳定映射为 evaluator 契约错误。

    输入参数：
        无；通过公开 checkout 评价入口提交含孤立 surrogate 的 logical URL。
    输出返回值：
        无；评价器必须抛出 CheckoutEvaluationContractError，不能泄漏底层编码异常。
    """

    with pytest.raises(CheckoutEvaluationContractError):
        _evaluate_checkout(
            ("webmall://store-1/product/\ud800",),
            _observation(()),
        )


def test_observed_product_requires_canonical_slug_and_nfkc_casefolds() -> None:
    """验证订单商品证据不再接受 display label，且兼容词元归一化稳定。

    输入参数：
        无；构造带空格的显示名和 Unicode 兼容大写字符。
    输出返回值：
        无；display label 在数据契约中失败，NFKC-casefold 产生小写词元。
    """

    with pytest.raises(CheckoutEvaluationContractError, match="slug"):
        ObservedCheckoutProduct(
            canonical_slug="Product Display Name",
            quantity=1,
        )

    assert product_identity_tokens("𝔸") == ("a",)
