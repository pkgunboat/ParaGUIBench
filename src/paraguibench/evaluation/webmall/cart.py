"""WebMall 购物车最终状态的纯 closed-world 评价协议。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from paraguibench.evaluation.webmall.identity import product_identity_tokens
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartWorker,
)
from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
    WebMallEvidenceContractError,
    contains_control,
    normalize_product_slug,
)

CART_PROTOCOL_ID = "paraguibench.webmall.cart.closed-world.v1"
_INVALID_PERCENT_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_REASON_ORDER = (
    "MISSING_PRODUCT",
    "UNEXPECTED_PRODUCT",
    "PRODUCT_QUANTITY_MISMATCH",
    "MULTI_WORKER_SIDE_EFFECT",
)

CartProductIdentity = tuple[str, ...]
CartProductKey = tuple[str, CartProductIdentity]
CartProductMultiset = Counter[CartProductKey]


class WebMallCartEvaluationError(RuntimeError):
    """表示 gold 或购物车证据无法可靠评价。"""


@dataclass(frozen=True, slots=True)
class CartEvaluation:
    """保存不含 URL、slug、worker ID 或购物车内容的评价结果。

    输入参数：
        protocol_id：实际执行的版本化 cart 协议。
        passed/score：闭集完全正确时为 ``True/1.0``。
        reason_codes：稳定且不含外部值的失败原因。
        evaluated_worker_count：完整采集的 worker 数。
        expected_product_quantity/observed_product_quantity：期望与全局观测数量。
        matched_product_quantity：最佳单 worker 的一对一命中数量。
        missing_product_quantity/unexpected_product_quantity：选定单
            worker 视角下的缺失与全局额外数量。
        quantity_mismatch_identity_count：同店同商品但数量不等的身份数。
        nonselected_worker_product_quantity：非选定 worker 的额外副作用数量。
    输出返回值：
        可安全投影到 RunStore evaluator details 的不可变计数结果。
    """

    protocol_id: str
    passed: bool
    score: float
    reason_codes: tuple[str, ...]
    evaluated_worker_count: int
    expected_product_quantity: int
    observed_product_quantity: int
    matched_product_quantity: int
    missing_product_quantity: int
    unexpected_product_quantity: int
    quantity_mismatch_identity_count: int
    nonselected_worker_product_quantity: int


@dataclass(frozen=True, slots=True)
class _WorkerComparison:
    """保存候选单 worker 的内部计数，不携带商品值。"""

    matched: int
    missing: int
    unexpected: int
    quantity_mismatch_identities: int
    nonselected_quantity: int


def evaluate_webmall_cart(
    expected_urls: Sequence[str],
    observation: CartObservationBatch,
) -> CartEvaluation:
    """按店铺、商品、数量和单 worker 完成语义评价 cart 闭集。

    输入参数：
        expected_urls：canonical task 中的 logical product URL 多集合。
        observation：runtime 受控 source 完整枚举的全部 worker 四店终态。
    输出返回值：
        一个 worker 独立包含全部期望商品，且全部 worker 无额外
        商品/数量时通过的二值评价结果。
    异常：
        WebMallCartEvaluationError：gold、证据类型或 coverage 不可靠。
    """

    expected = _compile_expected_cart(expected_urls)
    if not isinstance(observation, CartObservationBatch):
        raise WebMallCartEvaluationError("WebMall cart observation batch 类型无效")
    if not observation.complete or any(
        not worker.complete for worker in observation.workers
    ):
        raise WebMallCartEvaluationError("WebMall cart observation 不完整")

    worker_carts = tuple(
        _compile_observed_worker(worker) for worker in observation.workers
    )
    total_observed = sum(sum(cart.values()) for cart in worker_carts)
    comparisons = tuple(
        _compare_worker_candidate(
            expected=expected,
            candidate_index=index,
            worker_carts=worker_carts,
        )
        for index in range(len(worker_carts))
    )
    _, best = min(
        enumerate(comparisons),
        key=lambda pair: (
            pair[1].missing + pair[1].unexpected,
            -pair[1].matched,
            pair[0],
        ),
    )
    reason_set: set[str] = set()
    if best.missing:
        reason_set.add("MISSING_PRODUCT")
    if best.unexpected:
        reason_set.add("UNEXPECTED_PRODUCT")
    if best.quantity_mismatch_identities:
        reason_set.add("PRODUCT_QUANTITY_MISMATCH")
    if best.nonselected_quantity:
        reason_set.add("MULTI_WORKER_SIDE_EFFECT")
    reason_codes = tuple(reason for reason in _REASON_ORDER if reason in reason_set)
    passed = not reason_codes
    return CartEvaluation(
        protocol_id=CART_PROTOCOL_ID,
        passed=passed,
        score=1.0 if passed else 0.0,
        reason_codes=reason_codes,
        evaluated_worker_count=len(worker_carts),
        expected_product_quantity=sum(expected.values()),
        observed_product_quantity=total_observed,
        matched_product_quantity=best.matched,
        missing_product_quantity=best.missing,
        unexpected_product_quantity=best.unexpected,
        quantity_mismatch_identity_count=(best.quantity_mismatch_identities),
        nonselected_worker_product_quantity=best.nonselected_quantity,
    )


def _compile_expected_cart(
    expected_urls: Sequence[str],
) -> CartProductMultiset:
    """将 logical product URL 多集合编译为店铺和 slug 计数。

    输入参数：
        expected_urls：非空 canonical gold URL 序列；重复 URL 表示数量。
    输出返回值：
        ``(logical_store_id, canonical_slug)`` 到数量的 Counter。
    异常：
        WebMallCartEvaluationError：容器或任一 URL 无效。
    """

    if (
        isinstance(expected_urls, (str, bytes))
        or not isinstance(expected_urls, Sequence)
        or not expected_urls
    ):
        raise WebMallCartEvaluationError("WebMall cart expected URLs 必须是非空序列")
    expected: CartProductMultiset = Counter()
    for value in expected_urls:
        expected[_parse_logical_product_url(value)] += 1
    return expected


def _parse_logical_product_url(value: object) -> CartProductKey:
    """解析严格且不依赖部署地址的 WebMall 商品 URL。

    输入参数：
        value：待解析的 gold URL 候选值。
    输出返回值：
        ``(logical_store_id, canonical_slug)``。
    异常：
        WebMallCartEvaluationError：类型、编码、store 或 product path 无效。
    """

    if (
        not isinstance(value, str)
        or not value
        or _INVALID_PERCENT_PATTERN.search(value)
    ):
        raise WebMallCartEvaluationError("WebMall cart expected URL 无效")
    try:
        value.encode("utf-8", errors="strict")
        if contains_control(value) or "\\" in value:
            raise ValueError("unsafe logical URL")
        parts = urlsplit(value)
    except (UnicodeEncodeError, ValueError):
        raise WebMallCartEvaluationError("WebMall cart expected URL 无效") from None
    path_parts = parts.path.split("/")
    if (
        parts.scheme != "webmall"
        or parts.netloc not in WEBMALL_LOGICAL_STORE_IDS
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or len(path_parts) != 3
        or path_parts[:2] != ["", "product"]
        or not path_parts[2]
    ):
        raise WebMallCartEvaluationError(
            "WebMall cart expected URL 必须是 logical product URL"
        )
    try:
        slug = normalize_product_slug(path_parts[2])
    except WebMallEvidenceContractError:
        raise WebMallCartEvaluationError(
            "WebMall cart expected product identity 无效"
        ) from None
    return parts.netloc, _cart_product_identity(slug)


def _compile_observed_worker(
    worker: ObservedCartWorker,
) -> CartProductMultiset:
    """将一个 worker 四店证据聚合为商品数量多集合。

    输入参数：
        worker：已通过契约验证的完整 worker 观测。
    输出返回值：
        按 logical store 隔离的 canonical product Counter。
    """

    cart: CartProductMultiset = Counter()
    for store in worker.stores:
        for item in store.items:
            identity = _cart_product_identity(item.canonical_slug)
            cart[(store.logical_store_id, identity)] += item.quantity
    return cart


def _cart_product_identity(canonical_slug: str) -> CartProductIdentity:
    """生成仅容忍历史 ``&``/``amp`` 差异的商品比较键。

    输入参数：
        canonical_slug：已通过严格 percent/UTF-8 契约的商品 slug。
    输出返回值：
        NFKC/casefold 的完整字母数字词元，仅移除独立 ``amp``；
        全部数字型号和其余词元顺序均保留。
    异常：
        WebMallCartEvaluationError：slug 无法形成非空商品身份。
    """

    identity = tuple(
        token for token in product_identity_tokens(canonical_slug) if token != "amp"
    )
    if not identity:
        raise WebMallCartEvaluationError("WebMall cart product identity 无效")
    return identity


def _compare_worker_candidate(
    *,
    expected: CartProductMultiset,
    candidate_index: int,
    worker_carts: tuple[CartProductMultiset, ...],
) -> _WorkerComparison:
    """将一个 worker 作为唯一完成者，其他 worker 均视为副作用。

    输入参数：
        expected：期望店铺、商品和数量闭集。
        candidate_index：当前候选完成 worker 的序号。
        worker_carts：所有 worker 的购物车 Counter。
    输出返回值：
        不含商品值的命中、缺失、额外和数量差异计数。
    """

    candidate = worker_carts[candidate_index]
    matched = sum((expected & candidate).values())
    missing = sum((expected - candidate).values())
    candidate_unexpected = sum((candidate - expected).values())
    nonselected_quantity = sum(
        sum(cart.values())
        for index, cart in enumerate(worker_carts)
        if index != candidate_index
    )
    quantity_mismatch_identities = sum(
        1
        for identity in expected.keys() & candidate.keys()
        if expected[identity] != candidate[identity]
    )
    return _WorkerComparison(
        matched=matched,
        missing=missing,
        unexpected=candidate_unexpected + nonselected_quantity,
        quantity_mismatch_identities=quantity_mismatch_identities,
        nonselected_quantity=nonselected_quantity,
    )
