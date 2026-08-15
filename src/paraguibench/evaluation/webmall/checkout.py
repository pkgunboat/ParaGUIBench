"""WebMall 按 logical store 分组的 closed-world checkout 评价协议。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import urlsplit

from paraguibench.integrations.webmall.evidence_contracts import (
    LOGICAL_STORE_ID_PATTERN,
    CheckoutObservationBatch,
    ObservedCheckoutOrder,
    ObservedCheckoutProfile,
    ObservedCheckoutProduct as ObservedCheckoutProduct,
    WebMallEvidenceContractError,
    contains_control,
    normalize_product_slug,
)

CHECKOUT_PROTOCOL_ID = "paraguibench.webmall.checkout.closed-world.v2"
FIND_AND_ORDER_PROTOCOL_ID = "paraguibench.webmall.find-and-order.closed-world.v2"

_INVALID_PERCENT_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_REASON_ORDER = (
    "REPORTED_URL_MISMATCH",
    "MISSING_ORDER",
    "UNEXPECTED_ORDER",
    "PRODUCT_MISMATCH",
    "CHECKOUT_STATE_MISMATCH",
    "PAYMENT_METHOD_MISMATCH",
    "BILLING_PROFILE_MISMATCH",
)

ProductKey = str
ProductMultiset = Counter[ProductKey]


CheckoutEvaluationContractError = WebMallEvidenceContractError


@dataclass(frozen=True, slots=True)
class _ExpectedCheckoutProfile:
    """保存从版本化 fixture 编译得到的可信 checkout 目标。"""

    full_name: str
    email: str
    street: str
    house_number: str
    postcode: str
    city: str
    state: str
    country: str
    payment_kind: str


@dataclass(frozen=True, slots=True)
class CheckoutEvaluation:
    """保存不含商品、订单 ID、URL 或 profile 值的 checkout 结果。

    输入参数：
        protocol_id：固定评价协议 ID。
        passed/score：闭集完全正确时 ``True/1.0``，否则 ``False/0.0``。
        reason_codes：稳定且无外部值的失败原因代码。
        expected_order_count/observed_order_count：期望 store 数与唯一新增订单数。
        duplicate_observation_count：被语义去重的重复 sighting 数。
        missing_order_count/unexpected_order_count：缺失和额外订单计数。
        product_mismatch_order_count：期望 store 中商品多集合不匹配的计数。
        checkout_state_mismatch_order_count：checkout 未完成的订单数。
        payment_mismatch_order_count：支付语义不符合 fixture 的订单数。
        billing_profile_mismatch_order_count：账单资料不匹配的订单数。
    输出返回值：
        可安全进入 evaluator details 的计数型不可变结果。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    expected_order_count: int
    observed_order_count: int
    duplicate_observation_count: int
    missing_order_count: int
    unexpected_order_count: int
    product_mismatch_order_count: int
    checkout_state_mismatch_order_count: int
    payment_mismatch_order_count: int
    billing_profile_mismatch_order_count: int


@dataclass(frozen=True, slots=True)
class FindAndOrderEvaluation:
    """保存 EndToEnd 报告 URL 与订单闭集的 AND 结果。

    输入参数：
        protocol_id：固定 FindAndOrder 组合协议 ID。
        passed/score：报告多集合与 checkout 都精确时为 ``True/1.0``。
        reason_codes：仅含稳定失败代码，不含 URL 或商品值。
        reported_url_mismatch_count：缺失、额外、重复或非法提交项总数。
        checkout：已脱敏的订单闭集评价计数。
    输出返回值：
        可安全进入 evaluator details 的不可变组合结果。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    reported_url_mismatch_count: int
    checkout: CheckoutEvaluation


def evaluate_webmall_checkout(
    expected_urls: Sequence[str],
    expected_checkout_profile: Mapping[str, object],
    observation: CheckoutObservationBatch,
) -> CheckoutEvaluation:
    """按 store→一单→商品/终态/支付/账单闭集评价新增订单。

    输入参数：
        expected_urls：canonical task 的 ``webmall://store/product/slug`` 闭集。
        expected_checkout_profile：PreparedTask 从版本化 synthetic
            fixture 解析的 ``profile`` 可信投影。
        observation：环境 adapter 在 Attempt 基线后完整枚举的订单证据。
    输出返回值：
        二值正式得分与不含外部值的订单/商品差异计数。
    异常：
        CheckoutEvaluationContractError：gold URL、证据结构、扫描完整性或同一
            订单的重复证据矛盾；这些均应映射为 evaluator ERROR 而非零分。
    """

    expected_by_store = _compile_expected_orders(expected_urls)
    expected_profile = _compile_expected_checkout_profile(expected_checkout_profile)
    if not isinstance(observation, CheckoutObservationBatch):
        raise CheckoutEvaluationContractError("checkout observation batch 类型无效")
    if not observation.complete:
        raise CheckoutEvaluationContractError("checkout observation complete=false")
    unique_orders, duplicate_count = _deduplicate_orders(observation.orders)
    orders_by_store: dict[str, list[ObservedCheckoutOrder]] = {}
    for order in unique_orders:
        orders_by_store.setdefault(order.logical_store_id, []).append(order)

    expected_stores = set(expected_by_store)
    missing_order_count = 0
    unexpected_order_count = sum(
        len(orders)
        for store_id, orders in orders_by_store.items()
        if store_id not in expected_stores
    )
    product_mismatch_order_count = 0
    checkout_state_mismatch_order_count = 0
    payment_mismatch_order_count = 0
    billing_profile_mismatch_order_count = 0
    for store_id, expected_products in expected_by_store.items():
        store_orders = orders_by_store.get(store_id, [])
        if not store_orders:
            missing_order_count += 1
            continue
        if len(store_orders) > 1:
            unexpected_order_count += len(store_orders) - 1
        if not any(
            _observed_product_multiset(order) == expected_products
            for order in store_orders
        ):
            product_mismatch_order_count += 1
        if not any(order.checkout_state == "completed" for order in store_orders):
            checkout_state_mismatch_order_count += 1
        if not any(
            order.payment_kind == expected_profile.payment_kind
            for order in store_orders
        ):
            payment_mismatch_order_count += 1
        if not any(
            _billing_profile_matches(
                order.billing_profile,
                expected_profile,
            )
            for order in store_orders
        ):
            billing_profile_mismatch_order_count += 1

    reason_set: set[str] = set()
    if missing_order_count:
        reason_set.add("MISSING_ORDER")
    if unexpected_order_count:
        reason_set.add("UNEXPECTED_ORDER")
    if product_mismatch_order_count:
        reason_set.add("PRODUCT_MISMATCH")
    if checkout_state_mismatch_order_count:
        reason_set.add("CHECKOUT_STATE_MISMATCH")
    if payment_mismatch_order_count:
        reason_set.add("PAYMENT_METHOD_MISMATCH")
    if billing_profile_mismatch_order_count:
        reason_set.add("BILLING_PROFILE_MISMATCH")
    reason_codes = tuple(reason for reason in _REASON_ORDER if reason in reason_set)
    passed = not reason_codes
    return CheckoutEvaluation(
        protocol_id=CHECKOUT_PROTOCOL_ID,
        passed=passed,
        score=1.0 if passed else 0.0,
        reason_codes=reason_codes,
        expected_order_count=len(expected_by_store),
        observed_order_count=len(unique_orders),
        duplicate_observation_count=duplicate_count,
        missing_order_count=missing_order_count,
        unexpected_order_count=unexpected_order_count,
        product_mismatch_order_count=product_mismatch_order_count,
        checkout_state_mismatch_order_count=(checkout_state_mismatch_order_count),
        payment_mismatch_order_count=payment_mismatch_order_count,
        billing_profile_mismatch_order_count=(billing_profile_mismatch_order_count),
    )


def evaluate_webmall_find_and_order(
    expected_urls: Sequence[str],
    submitted_logical_urls: Sequence[str],
    expected_checkout_profile: Mapping[str, object],
    observation: CheckoutObservationBatch,
) -> FindAndOrderEvaluation:
    """对 EndToEnd 执行严格 URL 多集合 AND closed-world checkout。

    输入参数：
        expected_urls：canonical task 的 logical product URL 闭集。
        submitted_logical_urls：可信 runtime adapter 从 Agent 最终报告提取并
            将部署 origin 转为 logical store 后的 URL 序列。
        expected_checkout_profile：版本化 synthetic fixture 的可信 profile。
        observation：Attempt 基线之后完整的新增订单证据。
    输出返回值：
        二值 AND 得分、安全差异计数和订单子结果。
    异常：
        CheckoutEvaluationContractError：gold、提交容器或订单证据契约
            无效；单个 Agent URL 非法仅记为报告不匹配。
    """

    checkout = evaluate_webmall_checkout(
        expected_urls,
        expected_checkout_profile,
        observation,
    )
    expected_counter = Counter(
        _normalize_logical_product_url(url) for url in expected_urls
    )
    submitted_counter, invalid_count = _compile_submitted_report(submitted_logical_urls)
    reported_mismatch_count = (
        sum((expected_counter - submitted_counter).values())
        + sum((submitted_counter - expected_counter).values())
        + invalid_count
    )
    reason_set = set(checkout.reason_codes)
    if reported_mismatch_count:
        reason_set.add("REPORTED_URL_MISMATCH")
    reason_codes = tuple(reason for reason in _REASON_ORDER if reason in reason_set)
    passed = not reason_codes
    return FindAndOrderEvaluation(
        protocol_id=FIND_AND_ORDER_PROTOCOL_ID,
        passed=passed,
        score=1.0 if passed else 0.0,
        reason_codes=reason_codes,
        reported_url_mismatch_count=reported_mismatch_count,
        checkout=checkout,
    )


def _compile_expected_checkout_profile(
    profile: Mapping[str, object],
) -> _ExpectedCheckoutProfile:
    """从已验证 fixture 的 profile 投影编译正式 checkout 目标。

    输入参数：
        profile：包含 ``shipping_address`` 和 ``payment_method`` 的
            synthetic-public fixture profile。
    输出返回值：
        只在 evaluator 可信内存中使用的不可变期望值。
    异常：
        CheckoutEvaluationContractError：profile 层级、必需字段或
            payment type 与版本化协议不一致。
    """

    if not isinstance(profile, Mapping):
        raise CheckoutEvaluationContractError("checkout expected profile 类型无效")
    shipping = profile.get("shipping_address")
    payment = profile.get("payment_method")
    if not isinstance(shipping, Mapping) or not isinstance(payment, Mapping):
        raise CheckoutEvaluationContractError("checkout expected profile schema 无效")
    required_shipping_fields = (
        "name",
        "email",
        "street",
        "house_number",
        "zip",
        "city",
        "state",
        "country",
    )
    values = {
        field: _required_profile_text(shipping.get(field))
        for field in required_shipping_fields
    }
    payment_kind = _required_profile_text(payment.get("type"))
    if payment_kind != "credit_card":
        raise CheckoutEvaluationContractError("checkout expected payment kind 不受支持")
    return _ExpectedCheckoutProfile(
        full_name=values["name"],
        email=values["email"],
        street=values["street"],
        house_number=values["house_number"],
        postcode=values["zip"],
        city=values["city"],
        state=values["state"],
        country=values["country"],
        payment_kind=payment_kind,
    )


def _required_profile_text(value: object) -> str:
    """验证 fixture 中一个评价必需的有界文本字段。

    输入参数：
        value：待验证的 fixture 字段。
    输出返回值：
        去除边界空白后的非空字符串。
    异常：
        CheckoutEvaluationContractError：类型、长度或控制字符无效。
    """

    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or contains_control(value)
    ):
        raise CheckoutEvaluationContractError("checkout expected profile field 无效")
    return value.strip()


def _billing_profile_matches(
    observed: ObservedCheckoutProfile,
    expected: _ExpectedCheckoutProfile,
) -> bool:
    """按旧修复版的字段边界语义比较权威订单账单资料。

    输入参数：
        observed：WooCommerce 订单的完整 billing observation。
        expected：版本化 fixture 编译得到的目标。
    输出返回值：
        姓名、邮箱、城市、州、邮编、国家精确等价，且
        街道与门牌号各自以词边界存在于 address line 时返回真。
    """

    if not isinstance(observed, ObservedCheckoutProfile):
        raise CheckoutEvaluationContractError(
            "checkout billing profile observation 无效"
        )
    address = _normalize_profile_text(observed.address_line_1)
    expected_street = _normalize_profile_text(expected.street)
    expected_house_number = _normalize_profile_text(expected.house_number)
    return all(
        (
            _normalize_profile_text(observed.full_name)
            == _normalize_profile_text(expected.full_name),
            _normalize_profile_text(observed.email)
            == _normalize_profile_text(expected.email),
            _contains_bounded_text(address, expected_street),
            _contains_bounded_text(address, expected_house_number),
            _normalize_profile_text(observed.postcode)
            == _normalize_profile_text(expected.postcode),
            _normalize_profile_text(observed.city)
            == _normalize_profile_text(expected.city),
            _normalize_profile_text(observed.state)
            == _normalize_profile_text(expected.state),
            _canonical_country(observed.country)
            == _canonical_country(expected.country),
        )
    )


def _normalize_profile_text(value: str) -> str:
    """仅消除账单文本的 Unicode、大小写和布局差异。

    输入参数：
        value：已通过 profile contract 的文本。
    输出返回值：
        NFKC、casefold 并压缩空白后的比较值。
    """

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _contains_bounded_text(container: str, expected: str) -> bool:
    """以 Unicode 词边界检查地址子字段，避免短值子串误命中。

    输入参数：
        container：已规范化的完整 address line。
        expected：已规范化的街道或门牌字段。
    输出返回值：
        完整字段不嵌在更长词元中时返回真。
    """

    if not expected:
        return False
    return (
        re.search(
            rf"(?<!\w){re.escape(expected)}(?!\w)",
            container,
        )
        is not None
    )


def _canonical_country(value: str) -> str:
    """将 synthetic fixture 与 WooCommerce 的美国等价表示归一化。

    输入参数：
        value：期望或观测国家字段。
    输出返回值：
        ``US/USA/United States`` 等价值为 ``us``；其他值保留
        规范化结果，不做模糊国家映射。
    """

    normalized = _normalize_profile_text(value)
    if normalized in {
        "us",
        "usa",
        "united states",
        "united states of america",
    }:
        return "us"
    return normalized


def _compile_expected_orders(
    expected_urls: Sequence[str],
) -> dict[str, ProductMultiset]:
    """把 logical product URL 闭集编译为每店恰好一单的商品多集合。

    输入参数：
        expected_urls：canonical gold URL 序列。
    输出返回值：
        logical store ID 到期望商品身份 Counter 的映射。
    异常：
        CheckoutEvaluationContractError：序列为空、URL 不是严格 product URL、
            带 query/fragment、percent 编码损坏或出现重复商品身份。
    """

    if (
        isinstance(expected_urls, (str, bytes))
        or not isinstance(expected_urls, Sequence)
        or not expected_urls
    ):
        raise CheckoutEvaluationContractError("checkout expected URLs 必须是非空序列")
    compiled: dict[str, ProductMultiset] = {}
    seen_products: set[tuple[str, ProductKey]] = set()
    for logical_url in expected_urls:
        store_id, product_key = _parse_logical_product_url(logical_url)
        identity = (store_id, product_key)
        if identity in seen_products:
            raise CheckoutEvaluationContractError(
                "checkout expected product identity 重复"
            )
        seen_products.add(identity)
        compiled.setdefault(store_id, Counter())[product_key] = 1
    return compiled


def _parse_logical_product_url(value: object) -> tuple[str, ProductKey]:
    """解析一个无部署地址依赖的严格 WebMall logical product URL。

    输入参数：
        value：expected URL 候选值。
    输出返回值：
        ``(logical_store_id, canonical_product_slug)``。
    异常：
        CheckoutEvaluationContractError：类型、scheme、authority、路径、编码或
            商品身份无效。
    """

    if not isinstance(value, str) or not value:
        raise CheckoutEvaluationContractError("checkout expected URL 类型无效")
    if _INVALID_PERCENT_PATTERN.search(value):
        raise CheckoutEvaluationContractError(
            "checkout expected URL percent encoding 无效"
        )
    try:
        value.encode("utf-8", errors="strict")
        if contains_control(value) or "\\" in value:
            raise ValueError("unsafe logical URL")
        parts = urlsplit(value)
    except (UnicodeEncodeError, ValueError) as error:
        raise CheckoutEvaluationContractError("checkout expected URL 无效") from error
    path_parts = parts.path.split("/")
    if (
        parts.scheme != "webmall"
        or LOGICAL_STORE_ID_PATTERN.fullmatch(parts.netloc) is None
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or len(path_parts) != 3
        or path_parts[0] != ""
        or path_parts[1] != "product"
        or not path_parts[2]
    ):
        raise CheckoutEvaluationContractError(
            "checkout expected URL 必须是 logical product URL"
        )
    product_key = normalize_product_slug(path_parts[2])
    return parts.netloc, product_key


def _normalize_logical_product_url(value: object) -> str:
    """将严格 logical product URL 转为稳定多集合比较键。

    输入参数：
        value：canonical gold 或 Agent 报告的 logical URL 候选。
    输出返回值：
        store ID 与标准 percent-encoding slug 组成的 logical URL。
    异常：
        CheckoutEvaluationContractError：URL 不是严格 product URL。
    """

    store_id, canonical_slug = _parse_logical_product_url(value)
    return f"webmall://{store_id}/product/{canonical_slug}"


def _compile_submitted_report(
    submitted_urls: Sequence[str],
) -> tuple[Counter[str], int]:
    """编译 Agent 提交 URL 多集合并统计非法值。

    输入参数：
        submitted_urls：runtime adapter 提供的 logical URL 序列。
    输出返回值：
        ``(有效 URL Counter, 非法 URL 数)``；不回显原值。
    异常：
        CheckoutEvaluationContractError：输入不是字符串序列或项类型无效。
    """

    if isinstance(submitted_urls, (str, bytes)) or not isinstance(
        submitted_urls,
        Sequence,
    ):
        raise CheckoutEvaluationContractError("checkout submitted URLs 必须是序列")
    normalized: Counter[str] = Counter()
    invalid_count = 0
    for value in submitted_urls:
        if not isinstance(value, str):
            raise CheckoutEvaluationContractError("checkout submitted URL 类型无效")
        try:
            normalized[_normalize_logical_product_url(value)] += 1
        except CheckoutEvaluationContractError:
            invalid_count += 1
    return normalized, invalid_count


def _deduplicate_orders(
    orders: tuple[ObservedCheckoutOrder, ...],
) -> tuple[tuple[ObservedCheckoutOrder, ...], int]:
    """按 logical store/order ID 去重完全相同的多 VM sighting。

    输入参数：
        orders：完整扫描得到的订单观测元组。
    输出返回值：
        ``(唯一订单元组, 重复 sighting 数)``，保留首次出现顺序。
    异常：
        CheckoutEvaluationContractError：同一订单 key 的商品证据相互冲突。
    """

    by_identity: dict[tuple[str, str], ObservedCheckoutOrder] = {}
    duplicate_count = 0
    for order in orders:
        key = (order.logical_store_id, order.order_identity)
        existing = by_identity.get(key)
        if existing is None:
            by_identity[key] = order
            continue
        if existing != order:
            raise CheckoutEvaluationContractError(
                "checkout duplicate order evidence conflict"
            )
        duplicate_count += 1
    return (
        tuple(by_identity.values()),
        duplicate_count,
    )


def _observed_product_multiset(
    order: ObservedCheckoutOrder,
) -> ProductMultiset:
    """把一个订单的商品行合并为严格身份与总数量多集合。

    输入参数：
        order：已通过构造不变量的订单证据。
    输出返回值：
        canonical slug 到数量总和的 Counter。
    """

    products: ProductMultiset = Counter()
    for item in order.products:
        identity = item.canonical_slug
        products[identity] += item.quantity
    return products
