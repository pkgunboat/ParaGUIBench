"""WebMall 从 Agent 输出、订单证据到 RunStore 的纵向隐私测试。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import prepare_release_task
from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
    CheckoutObservationBatch,
    ObservedCheckoutOrder,
    ObservedCheckoutProduct,
    ObservedCheckoutProfile,
)
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartItem,
    ObservedCartStore,
    ObservedCartWorker,
)
from paraguibench.evaluation.webmall.cart import (
    WebMallCartEvaluationError,
)
from paraguibench.runstore import (
    AttemptFailureStage,
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
)
from paraguibench.runtime.attempt_runner import AttemptRunner
from paraguibench.runtime.webmall_binding import preflight_webmall_runtime


class _SensitiveAgent:
    """返回不得落盘的模型最终文本。"""

    def __init__(self, final_output: str) -> None:
        """保存合成敏感最终文本。

        输入参数：
            final_output：只应在 evaluator 调用期间留在内存的文本。
        输出返回值：
            无。
        """

        self._final_output = final_output

    def run(self, task_view: dict[str, object], environment: object) -> AgentRunResult:
        """返回合法 Agent 结果但不主动持久化正文。

        输入参数：
            task_view：已物化且不含 gold 的 Agent 投影。
            environment：AttemptRunner 提供的当前 WebMall 环境。
        输出返回值：
            含敏感 final output、一步和固定终止码的结果。
        """

        assert "webmall://" not in str(task_view["instruction"])
        del environment
        return AgentRunResult(
            final_output=self._final_output,
            step_count=1,
            termination="finished",
        )


class _SensitiveEvidenceEnvironment:
    """只在内存中提供敏感 WebMall 订单证据的合成环境。"""

    def __init__(self, observation: CheckoutObservationBatch) -> None:
        """绑定 evaluator 将读取的完整观测。

        输入参数：
            observation：包含敏感订单、商品和账单字段的四店批次。
        输出返回值：
            无。
        """

        self._observation = observation

    def start(self) -> None:
        """启动无 I/O 合成环境。

        输入参数：无。
        输出返回值：无。
        """

    def prepare(self, task: dict[str, object]) -> None:
        """验证收到 trusted task 但不保存其 profile。

        输入参数：
            task：包含 evaluator-only fixture 的可信 task。
        输出返回值：
            无。
        """

        assert task["task_source"] == "WebMall"

    def checkout_observation(self) -> CheckoutObservationBatch:
        """向可信 evaluator 返回同一个冻结观测。

        输入参数：无。
        输出返回值：完整四店观测。
        """

        return self._observation

    def close(self) -> None:
        """关闭无 I/O 合成环境。

        输入参数：无。
        输出返回值：无。
        """


class _SensitiveCartEnvironment:
    """仅在内存中保存 Cart slug/worker 的合成生命周期环境。"""

    def __init__(self, observation: CartObservationBatch) -> None:
        """绑定 evaluator 将读取的一次 Cart 终态。

        输入参数：observation 为完整或故意不完整的 Cart 批次。
        输出返回值：无。
        """

        self._observation = observation

    def start(self) -> None:
        """启动无 I/O 合成环境。

        输入参数：无。
        输出返回值：无。
        """

    def prepare(self, task: dict[str, object]) -> None:
        """验证收到可信 Cart task 而不保存 gold。

        输入参数：task 为 AttemptRunner 的 canonical task。
        输出返回值：无。
        """

        assert task["answer_type"] == "cart"

    def cart_observation(self) -> CartObservationBatch:
        """向可信 evaluator 返回冻结 Cart 观测。

        输入参数：无。
        输出返回值：构造时提供的不可变批次。
        """

        return self._observation

    def close(self) -> None:
        """关闭无 I/O 合成环境。

        输入参数：无。
        输出返回值：无。
        """


def _cart_observation_from_expected(
    expected_urls: list[str],
    *,
    worker_id: str,
) -> CartObservationBatch:
    """把测试 gold 投影为同一 worker 的完整四店 Cart 证据。

    输入参数：expected_urls 为 logical URL 多集合；worker_id 为敏感哨兵。
    输出返回值：按店聚合 slug/quantity 的完整不可变批次。
    """

    quantities: dict[str, dict[str, int]] = {
        store_id: {} for store_id in WEBMALL_LOGICAL_STORE_IDS
    }
    for url in expected_urls:
        parts = urlsplit(url)
        slug = parts.path.removeprefix("/product/")
        store_quantities = quantities[parts.netloc]
        store_quantities[slug] = store_quantities.get(slug, 0) + 1
    return CartObservationBatch(
        complete=True,
        workers=(
            ObservedCartWorker(
                worker_id=worker_id,
                complete=True,
                stores=tuple(
                    ObservedCartStore(
                        logical_store_id=store_id,
                        complete=True,
                        items=tuple(
                            ObservedCartItem(slug, quantity)
                            for slug, quantity in sorted(quantities[store_id].items())
                        ),
                    )
                    for store_id in WEBMALL_LOGICAL_STORE_IDS
                ),
            ),
        ),
    )


def test_webmall_sensitive_evidence_and_model_output_never_reach_runstore(
    tmp_path: Path,
) -> None:
    """验证 WebMall 纵向运行只落盘理由码、布尔值与计数。

    输入参数：
        tmp_path：pytest 提供的任务级 RunStore 根。
    输出返回值：
        无；Attempt 正常评价为 Agent 失败，而订单 ID、slug、
        billing、runtime origin 和模型文本在全部文件中均不存在。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task_id = "Operation-OnlineShopping-Checkout-001"
    runtime_origin = "https://runtime-private-origin.example.invalid"
    origin_environment = {
        f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
            runtime_origin
            if index == 1
            else f"https://runtime-private-{index}.example.invalid"
        )
        for index in range(1, 5)
    }
    binding = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepare_release_task(
            repo_root,
            task_id,
            environment_bindings={},
        ),
        environment=origin_environment,
    )
    sentinels = (
        "private-order-998",
        "private-product-secret",
        "Private Billing Person",
        "billing-secret@example.invalid",
        "99 Private Evidence Street",
        runtime_origin,
        "private-model-final-output",
    )
    observation = CheckoutObservationBatch(
        store_universe_id=WEBMALL_STORE_UNIVERSE_ID,
        scanned_store_ids=WEBMALL_LOGICAL_STORE_IDS,
        complete=True,
        orders=(
            ObservedCheckoutOrder(
                logical_store_id="store-1",
                order_identity=sentinels[0],
                products=(
                    ObservedCheckoutProduct(
                        canonical_slug=sentinels[1],
                        quantity=3,
                    ),
                ),
                checkout_state="failed",
                payment_kind="other",
                billing_profile=ObservedCheckoutProfile(
                    full_name=sentinels[2],
                    email=sentinels[3],
                    address_line_1=sentinels[4],
                    postcode="PRIVATE-POSTCODE",
                    city="Private City",
                    state="Private State",
                    country="ZZ",
                ),
            ),
        ),
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-webmall-privacy",
        run_record={"environment_id": binding.manifest.environment_id},
        version_vector=binding.version_vector,
    )
    attempt = store.start_attempt(
        run_id="run-webmall-privacy",
        task_id=task_id,
        attempt_id="attempt-001",
        task_record=binding.prepared_task.audit_metadata,
    )

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=binding.prepared_task,
        environment=_SensitiveEvidenceEnvironment(observation),
        agent=_SensitiveAgent(sentinels[-1]),
        evaluator=binding.evaluator,
    )

    assert result.evaluation_outcome is EvaluationOutcome.FAILED
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for sentinel in sentinels:
        assert sentinel.encode() not in persisted
    assert b"UNEXPECTED_ORDER" in persisted


def test_webmall_cart_evidence_and_final_output_never_reach_runstore(
    tmp_path: Path,
) -> None:
    """验证 Cart 成功评价只落协议和脱敏计数，不落终态原值。

    输入参数：tmp_path 为 pytest 提供的任务级 RunStore 根。
    输出返回值：无；运行满分，但 final output、gold/observed slug、worker
        identity 和 runtime origin 均不出现在任何持久化文件。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task_id = "Operation-OnlineShopping-AddToCart-001"
    origins = {
        f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
            f"https://cart-runtime-private-{index}.example.invalid"
        )
        for index in range(1, 5)
    }
    binding = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepare_release_task(
            repo_root,
            task_id,
            environment_bindings={},
        ),
        environment=origins,
    )
    expected_urls = binding.prepared_task.trusted_task["expected_urls"]
    worker_id = "private-cart-worker"
    final_output = "PRIVATE CART FINAL OUTPUT SENTINEL"
    observation = _cart_observation_from_expected(
        expected_urls,
        worker_id=worker_id,
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-webmall-cart-privacy",
        run_record={"environment_id": binding.manifest.environment_id},
        version_vector=binding.version_vector,
    )
    attempt = store.start_attempt(
        run_id="run-webmall-cart-privacy",
        task_id=task_id,
        attempt_id="attempt-001",
        task_record=binding.prepared_task.audit_metadata,
    )

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=binding.prepared_task,
        environment=_SensitiveCartEnvironment(observation),
        agent=_SensitiveAgent(final_output),
        evaluator=binding.evaluator,
    )

    assert result.evaluation_outcome is EvaluationOutcome.PASSED
    assert result.score == 1.0
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    slugs = tuple(urlsplit(url).path for url in expected_urls)
    for sentinel in (*origins.values(), worker_id, final_output, *slugs):
        assert sentinel.encode() not in persisted
    for safe_field in (
        b"paraguibench.webmall.cart.closed-world.v1",
        b"expected_product_quantity",
        b"observed_product_quantity",
        b"matched_product_quantity",
    ):
        assert safe_field in persisted


def test_webmall_cart_incomplete_store_is_evaluation_error_with_null_score(
    tmp_path: Path,
) -> None:
    """验证固定四店任一不完整不会被误判为零分普通失败。

    输入参数：tmp_path 为 pytest 提供的任务级 RunStore 根。
    输出返回值：无；AttemptRunner 重新抛 evaluator error，但持久化终态为
        execution SUCCEEDED、evaluation ERROR、score null，且不含证据原值。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task_id = "Operation-OnlineShopping-AddToCart-001"
    binding = preflight_webmall_runtime(
        repo_root=repo_root,
        prepared_task=prepare_release_task(
            repo_root,
            task_id,
            environment_bindings={},
        ),
        environment={
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN": (
                f"https://incomplete-private-{index}.example.invalid"
            )
            for index in range(1, 5)
        },
    )
    private_slug = "private-incomplete-cart-slug"
    worker_id = "private-incomplete-worker"
    observation = CartObservationBatch(
        complete=False,
        workers=(
            ObservedCartWorker(
                worker_id=worker_id,
                complete=False,
                stores=(
                    ObservedCartStore(
                        logical_store_id="store-1",
                        complete=True,
                        items=(ObservedCartItem(private_slug, 1),),
                    ),
                    ObservedCartStore(
                        logical_store_id="store-2",
                        complete=True,
                        items=(),
                    ),
                    ObservedCartStore(
                        logical_store_id="store-3",
                        complete=False,
                        items=(),
                    ),
                ),
            ),
        ),
    )
    final_output = "PRIVATE INCOMPLETE FINAL OUTPUT"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-webmall-cart-incomplete",
        run_record={"environment_id": binding.manifest.environment_id},
        version_vector=binding.version_vector,
    )
    attempt = store.start_attempt(
        run_id="run-webmall-cart-incomplete",
        task_id=task_id,
        attempt_id="attempt-001",
        task_record=binding.prepared_task.audit_metadata,
    )

    with pytest.raises(WebMallCartEvaluationError):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=binding.prepared_task,
            environment=_SensitiveCartEnvironment(observation),
            agent=_SensitiveAgent(final_output),
            evaluator=binding.evaluator,
        )

    inspection = store.inspect_attempt(
        run_id="run-webmall-cart-incomplete",
        task_id=task_id,
        attempt_id="attempt-001",
    )
    assert inspection.execution_outcome is ExecutionOutcome.SUCCEEDED
    assert inspection.evaluation_outcome is EvaluationOutcome.ERROR
    assert inspection.score is None
    assert inspection.failure_stage is AttemptFailureStage.EVALUATOR_EVALUATE
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for sentinel in (private_slug, worker_id, final_output):
        assert sentinel.encode() not in persisted
