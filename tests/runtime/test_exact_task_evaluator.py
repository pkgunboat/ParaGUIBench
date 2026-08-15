"""runtime exact evaluator adapter 的统一结果契约测试。"""

from __future__ import annotations

import pytest

from paraguibench.evaluation.webmall import (
    CheckoutObservationBatch,
    ObservedCheckoutOrder,
    ObservedCheckoutProfile,
    ObservedCheckoutProduct,
)
from paraguibench.evaluation.webmall.cart import CART_PROTOCOL_ID
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartItem,
    ObservedCartStore,
    ObservedCartWorker,
)
from paraguibench.runtime.evaluators import (
    AnswerTaskEvaluator,
    ExactTaskEvaluator,
    UnsupportedTaskEvaluatorError,
    WebMallCheckoutTaskEvaluator,
    WebMallURLSetTaskEvaluator,
    build_task_evaluator,
)
from paraguibench.integrations.webmall import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
    WebMallURLRegistry,
)


def test_exact_evaluator_adapter_returns_runtime_evaluation_without_gold() -> None:
    """验证确定性评价只返回 match type、通过状态和分数。

    输入参数：
        无；使用合成 canonical task 与完整 answer 标签。
    输出返回值：
        无；details 不得复制主答案或别名。
    """

    task = {
        "answer_match_mode": "exact",
        "answer": "private-gold",
        "accepted_answers": ["alias"],
    }

    result = ExactTaskEvaluator().evaluate(
        task,
        "<answer>alias</answer>",
        object(),
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {"match_type": "strict_exact_via_alias"}
    assert "private-gold" not in repr(result)


def test_answer_evaluator_adapter_supports_numeric_without_persisting_gold() -> None:
    """验证通用 QA adapter 支持 numeric 且诊断中不包含答案原文。

    输入参数：
        无；使用数值等价但文本表示不同的合成任务和模型输出。
    输出返回值：
        无；统一 runtime 结果必须通过，且 details 只含安全匹配类型。
    """

    task = {
        "task_type": "QA",
        "task_source": "self",
        "answer_match_mode": "numeric",
        "answer": "2000",
        "accepted_answers": [],
    }

    result = AnswerTaskEvaluator().evaluate(
        task,
        "<answer>2000.0</answer>",
        object(),
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {"match_type": "numeric_value"}
    assert "2000" not in repr(result.details)


def test_task_evaluator_registry_accepts_answer_qa_and_rejects_webmall() -> None:
    """验证 runtime registry 只把已迁移的非 WebMall QA 路由到答案评价器。

    输入参数：
        无；分别提供已迁移 QA 和仍需浏览器状态评价的 WebMall QA。
    输出返回值：
        无；前者返回通用 adapter，后者以类型安全异常拒绝，且异常不回显
        任务答案。
    """

    migrated_task = {
        "task_type": "QA",
        "task_source": "self",
        "answer_match_mode": "exact",
        "answer": "private-answer",
    }
    webmall_task = {
        "task_type": "QA",
        "task_source": "WebMall",
        "answer": "private-webmall-state",
    }

    assert isinstance(
        build_task_evaluator(
            migrated_task,
            evaluation_protocol="paraguibench.answer.exact.v1",
        ),
        AnswerTaskEvaluator,
    )
    with pytest.raises(UnsupportedTaskEvaluatorError) as captured:
        build_task_evaluator(
            webmall_task,
            evaluation_protocol="legacy.webmall.checkout.v1",
        )
    assert "private-webmall-state" not in str(captured.value)

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            migrated_task,
            evaluation_protocol="legacy.osworld.state.v1",
        )


class _WebMallEvidenceEnvironment:
    """向 runtime evaluator 提供冻结订单证据与 logical 报告的合成环境。"""

    def __init__(
        self,
        observation: CheckoutObservationBatch,
        submitted_logical_urls: tuple[str, ...] = (),
    ) -> None:
        """保存 evaluator 可读取但不得持久化的内存证据。

        输入参数：
            observation：Attempt baseline 之后的完整订单闭包。
            submitted_logical_urls：环境从 Agent 报告可信转换出的 URL 序列。
        输出返回值：
            无。
        """

        self._observation = observation
        self._submitted_logical_urls = submitted_logical_urls
        self.report_calls = 0

    def checkout_observation(self) -> CheckoutObservationBatch:
        """返回冻结的完整订单 observation。

        输入参数：无。
        输出返回值：构造时提供的不可变订单批次。
        """

        return self._observation

    def canonicalize_reported_product_urls(
        self,
        final_output: str,
    ) -> tuple[str, ...]:
        """模拟环境将 runtime URL 报告转为 logical URL 多集合。

        输入参数：
            final_output：Agent 最终文本，仅验证 evaluator 确实传入。
        输出返回值：
            构造时固定的 logical URL 元组。
        """

        assert isinstance(final_output, str)
        self.report_calls += 1
        return self._submitted_logical_urls


class _WebMallURLEnvironment:
    """只向 URL-multiset evaluator 提供报告与 registry seam。"""

    def __init__(self, submitted_logical_urls: tuple[str, ...]) -> None:
        """保存已由受信环境规范化的 logical URL 多集合。

        输入参数：
            submitted_logical_urls：保留顺序与重复项的报告结果。
        输出返回值：
            无。
        """

        self._submitted_logical_urls = submitted_logical_urls
        self._registry = WebMallURLRegistry(
            {
                f"store-{index}": f"https://shop-{index}.example.invalid"
                for index in range(1, 5)
            }
        )

    def canonicalize_reported_product_urls(
        self,
        final_output: str,
    ) -> tuple[str, ...]:
        """返回构造时的 logical URL 多集合。

        输入参数：
            final_output：完整 Agent 报告；只验证 runtime 确实传入。
        输出返回值：
            构造时的不可变 logical URL 元组。
        """

        assert isinstance(final_output, str)
        return self._submitted_logical_urls

    def webmall_url_registry(self) -> WebMallURLRegistry:
        """返回 pure evaluator 用于对齐 logical gold 的 registry。

        输入参数：无。
        输出返回值：只在内存中使用的四店 registry。
        """

        return self._registry


class _WebMallCartEnvironment:
    """只向 Cart runtime evaluator 提供冻结终态证据。"""

    def __init__(self, observation: CartObservationBatch) -> None:
        """保存不可变 Cart 观测并初始化读取计数。

        输入参数：observation 为单 worker×固定四店完整证据。
        输出返回值：无。
        """

        self._observation = observation
        self.read_count = 0

    def cart_observation(self) -> CartObservationBatch:
        """返回 Cart evaluator 唯一允许读取的终态证据。

        输入参数：无。
        输出返回值：构造时冻结的 ``CartObservationBatch``。
        """

        self.read_count += 1
        return self._observation


def test_webmall_cart_runtime_ignores_final_output_and_returns_safe_counts() -> None:
    """验证 Cart adapter 仅评价四店终态且公开结果完全脱敏。

    输入参数：无；构造一件 quantity=2 的正确单 worker Cart。
    输出返回值：无；结果满分、证据只读一次，final output 与 slug 不出现。
    """

    private_slug = "private-cart-product"
    task = {
        "task_id": "Operation-OnlineShopping-AddToCart-005",
        "task_type": "QA",
        "task_source": "WebMall",
        "task_tag": "AddToCart",
        "answer_type": "cart",
        "evaluator_path": "evaluators/cart_evaluator.py",
        "expected_urls": [
            f"webmall://store-3/product/{private_slug}",
            f"webmall://store-3/product/{private_slug}",
        ],
    }
    observation = CartObservationBatch(
        complete=True,
        workers=(
            ObservedCartWorker(
                worker_id="worker-private",
                complete=True,
                stores=tuple(
                    ObservedCartStore(
                        logical_store_id=f"store-{index}",
                        complete=True,
                        items=(
                            (ObservedCartItem(private_slug, 2),) if index == 3 else ()
                        ),
                    )
                    for index in range(1, 5)
                ),
            ),
        ),
    )
    environment = _WebMallCartEnvironment(observation)
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=CART_PROTOCOL_ID,
    )

    result = evaluator.evaluate(
        task,
        "PRIVATE FINAL OUTPUT MUST BE IGNORED",
        environment,
    )

    assert type(evaluator).__name__ == "WebMallCartTaskEvaluator"
    assert result.passed is True
    assert result.score == 1.0
    assert environment.read_count == 1
    assert result.details == {
        "protocol_id": CART_PROTOCOL_ID,
        "reason_codes": (),
        "evaluated_worker_count": 1,
        "expected_product_quantity": 2,
        "observed_product_quantity": 2,
        "matched_product_quantity": 2,
        "missing_product_quantity": 0,
        "unexpected_product_quantity": 0,
        "quantity_mismatch_identity_count": 0,
        "nonselected_worker_product_quantity": 0,
    }
    rendered = repr(result.details)
    assert private_slug not in rendered
    assert "worker-private" not in rendered
    assert "PRIVATE FINAL OUTPUT" not in rendered


@pytest.mark.parametrize(
    "task_drift",
    (
        {"task_id": "Operation-OnlineShopping-AddToCart-999"},
        {"task_source": "self"},
        {"task_type": "OSWorld脚本"},
        {"answer_type": "string"},
        {"evaluator_path": "evaluators/string_url_evaluator.py"},
    ),
)
def test_webmall_cart_registry_rejects_unaudited_task_contract(
    task_drift: dict[str, str],
) -> None:
    """验证 Cart 协议不能被非 8-task 闭集或身份漂移任务复用。

    输入参数：task_drift 为一个被篡改的任务身份字段。
    输出返回值：无；在环境证据读取前抛固定不支持异常。
    """

    task = {
        "task_id": "Operation-OnlineShopping-AddToCart-001",
        "task_type": "QA",
        "task_source": "WebMall",
        "task_tag": "AddToCart",
        "answer_type": "cart",
        "evaluator_path": "evaluators/cart_evaluator.py",
    }
    task.update(task_drift)

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(task, evaluation_protocol=CART_PROTOCOL_ID)


def test_webmall_url_runtime_rejects_duplicate_without_persisting_urls() -> None:
    """验证 67 条 string 任务经 runtime adapter 保留多集合语义。

    输入参数：
        无；提交一个正确 URL 两次，gold 只要求一次。
    输出返回值：
        无；评价失败，details 只含协议、计数与比例，
        不包含 URL、host 或 Agent 文本。
    """

    logical_url = "webmall://store-1/product/private-item"
    task = {
        "task_id": "Operation-OnlineShopping-SingleProductSearch-001",
        "task_type": "QA",
        "task_source": "WebMall",
        "task_tag": "SingleProductSearch",
        "evaluator_path": "evaluators/string_url_evaluator.py",
        "expected_urls": [logical_url],
    }
    environment = _WebMallURLEnvironment((logical_url, logical_url))
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol="paraguibench.webmall.url-multiset.v1",
    )

    result = evaluator.evaluate(task, "private-final-output", environment)

    assert isinstance(evaluator, WebMallURLSetTaskEvaluator)
    assert result.passed is False
    assert result.score == 1.0
    assert result.details == {
        "protocol_id": "paraguibench.webmall.url-multiset.v1",
        "matched_count": 1,
        "wrong_count": 1,
        "missing_count": 0,
        "precision": 0.5,
        "recall": 1.0,
        "f1": pytest.approx(2 / 3),
    }
    rendered = repr(result.details)
    assert "private-item" not in rendered
    assert "example.invalid" not in rendered
    assert "private-final-output" not in rendered


def test_webmall_url_runtime_treats_unknown_origin_as_ordinary_failure() -> None:
    """验证未知 origin 的固定标记导致普通零分而非 evaluator error。

    输入参数：
        无；环境返回 report parser 对未知 host 产生的固定标记。
    输出返回值：
        无；断言 evaluator 返回失败与计数，而不是抛出异常。
    """

    task = {
        "task_id": "Operation-OnlineShopping-CheapestProductSearch-001",
        "task_type": "QA",
        "task_source": "WebMall",
        "task_tag": "CheapestProductSearch",
        "evaluator_path": "evaluators/string_url_evaluator.py",
        "expected_urls": ["webmall://store-3/product/expected-item"],
    }
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol="paraguibench.webmall.url-multiset.v1",
    )

    result = evaluator.evaluate(
        task,
        "https://unknown.example/private?secret=value",
        _WebMallURLEnvironment(("invalid://reported-url",)),
    )

    assert result.passed is False
    assert result.score == 0.0
    assert result.details["matched_count"] == 0
    assert result.details["wrong_count"] == 1
    assert result.details["missing_count"] == 1
    assert "unknown.example" not in repr(result.details)
    assert "secret" not in repr(result.details)


@pytest.mark.parametrize(
    "mismatched_field",
    [
        {"task_source": "self"},
        {"task_type": "OSWorld脚本"},
        {"evaluator_path": "evaluators/cart_evaluator.py"},
    ],
)
def test_webmall_url_registry_rejects_tasks_outside_original_string_closure(
    mismatched_field: dict[str, str],
) -> None:
    """验证 URL 协议不会因为人工注入而误绑其他任务。

    输入参数：
        mismatched_field：分别篡改 WebMall 来源、QA 类型或原
            ``string_url_evaluator.py`` 路径的字段。
    输出返回值：
        无；registry 在运行环境或报告读取前以固定不支持
        异常失败关闭。
    """

    task = {
        "task_id": "Operation-OnlineShopping-SingleProductSearch-001",
        "task_type": "QA",
        "task_source": "WebMall",
        "task_tag": "SingleProductSearch",
        "evaluator_path": "evaluators/string_url_evaluator.py",
    }
    task.update(mismatched_field)

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            task,
            evaluation_protocol="paraguibench.webmall.url-multiset.v1",
        )


def _checkout_observation() -> CheckoutObservationBatch:
    """构造一笔 store-1/product-a 的完整新增订单证据。

    输入参数：无。
    输出返回值：不含 profile、URL 或显示名的 checkout batch。
    """

    return CheckoutObservationBatch(
        store_universe_id=WEBMALL_STORE_UNIVERSE_ID,
        scanned_store_ids=WEBMALL_LOGICAL_STORE_IDS,
        complete=True,
        orders=(
            ObservedCheckoutOrder(
                logical_store_id="store-1",
                order_identity="private-order-123",
                products=(
                    ObservedCheckoutProduct(
                        canonical_slug="product-a",
                        quantity=1,
                    ),
                ),
                checkout_state="completed",
                payment_kind="credit_card",
                billing_profile=ObservedCheckoutProfile(
                    full_name="ParaGUI Test User",
                    email="checkout-v1@example.invalid",
                    address_line_1="100 Benchmark Avenue",
                    postcode="94107",
                    city="San Francisco",
                    state="CA",
                    country="US",
                ),
            ),
        ),
    )


def _resolved_checkout_fixture() -> dict[str, object]:
    """构造 runtime evaluator 可信内存中的 synthetic fixture 投影。

    输入参数：
        无。
    输出返回值：
        与公开 fixture profile 等价的最小映射；仅 evaluator 可见。
    """

    return {
        "profile": {
            "shipping_address": {
                "name": "ParaGUI Test User",
                "email": "checkout-v1@example.invalid",
                "street": "Benchmark Avenue",
                "house_number": "100",
                "zip": "94107",
                "city": "San Francisco",
                "state": "CA",
                "country": "USA",
            },
            "payment_method": {
                "type": "credit_card",
                "card_number": "4242424242424242",
                "cvv": "123",
                "expiry_date": "12/39",
            },
        }
    }


def test_registry_builds_checkout_adapter_and_ignores_checkout_report_text() -> None:
    """验证 Checkout 协议只评价订单闭集，不读取 Agent 最终报告。

    输入参数：
        无；提供正确订单和含敏感占位文本的 final output。
    输出返回值：
        无；评价通过，报告转换接口未调用，details 只有协议与计数。
    """

    task = {
        "task_id": "Operation-OnlineShopping-Checkout-001",
        "task_type": "QA",
        "task_source": "WebMall",
        "task_tag": "Checkout",
        "expected_urls": ["webmall://store-1/product/product-a"],
        "resolved_fixtures": {"checkout_profile": _resolved_checkout_fixture()},
    }
    environment = _WebMallEvidenceEnvironment(_checkout_observation())
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol="paraguibench.webmall.checkout.closed-world.v2",
    )

    result = evaluator.evaluate(task, "secret-final-output", environment)

    assert isinstance(evaluator, WebMallCheckoutTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert environment.report_calls == 0
    assert result.details["protocol_id"] == (
        "paraguibench.webmall.checkout.closed-world.v2"
    )
    assert result.details["observed_order_count"] == 1
    assert "product-a" not in repr(result.details)
    assert "private-order-123" not in repr(result.details)
    assert "secret-final-output" not in repr(result.details)


def test_find_and_order_adapter_ands_exact_report_with_checkout() -> None:
    """验证 EndToEnd 即使订单正确，报告缺 URL 仍以组合协议判零分。

    输入参数：
        无；订单闭集正确，但 submitted logical URL 序列为空。
    输出返回值：
        无；结果失败并只记录稳定 reason code 与不匹配计数。
    """

    task = {
        "task_id": "Operation-OnlineShopping-EndToEnd-002",
        "task_type": "QA",
        "task_source": "WebMall",
        "task_tag": "EndToEnd",
        "expected_urls": ["webmall://store-1/product/product-a"],
        "resolved_fixtures": {"checkout_profile": _resolved_checkout_fixture()},
    }
    environment = _WebMallEvidenceEnvironment(_checkout_observation())
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=("paraguibench.webmall.find-and-order.closed-world.v2"),
    )

    result = evaluator.evaluate(task, "Done", environment)

    assert result.passed is False
    assert result.score == 0.0
    assert environment.report_calls == 1
    assert result.details["reason_codes"] == ("REPORTED_URL_MISMATCH",)
    assert result.details["reported_url_mismatch_count"] == 1
    assert result.details["checkout_passed"] is True


@pytest.mark.parametrize(
    ("task_tag", "protocol"),
    [
        ("Checkout", "paraguibench.webmall.find-and-order.closed-world.v2"),
        ("EndToEnd", "paraguibench.webmall.checkout.closed-world.v2"),
    ],
)
def test_registry_rejects_webmall_tag_protocol_mismatch(
    task_tag: str,
    protocol: str,
) -> None:
    """验证任务标签与 WebMall 协议交叉错配在 runtime 装配阶段失败。

    输入参数：
        task_tag/protocol：故意互换的 Checkout 与 EndToEnd 绑定。
    输出返回值：
        无；registry 抛 UnsupportedTaskEvaluatorError。
    """

    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            {
                "task_type": "QA",
                "task_source": "WebMall",
                "task_tag": task_tag,
            },
            evaluation_protocol=protocol,
        )
