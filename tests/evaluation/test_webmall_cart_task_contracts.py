"""8 个 WebMall cart canonical task 的语义清单回归。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from paraguibench.evaluation.webmall.cart import evaluate_webmall_cart
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartItem,
    ObservedCartStore,
    ObservedCartWorker,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_CART_TASK_CONTRACTS = {
    "Operation-OnlineShopping-AddToCart-001": (
        2,
        ("store-3", "store-1"),
        "b3dfa8f064bd691f4312b9bf9881b71b43b0b06800ca9bf370823c37322e0cdf",
    ),
    "Operation-OnlineShopping-AddToCart-002": (
        2,
        ("store-1", "store-1"),
        "6440b205b65b385aca40758ca8e3825e38c9763c19d8f622ef17046a054ad814",
    ),
    "Operation-OnlineShopping-AddToCart-003": (
        2,
        ("store-4", "store-1"),
        "10b71e3a55f100c5a20ff0d7d9b836be722814b0e03557b1e5d3543977edb91e",
    ),
    "Operation-OnlineShopping-AddToCart-004": (
        2,
        ("store-3", "store-1"),
        "bb83807cadf01fa132feec5a0169d0affe47962df57c7e7749e99f76889b9da0",
    ),
    "Operation-OnlineShopping-AddToCart-005": (
        1,
        ("store-3",),
        "81c7d8a213f4d8a750a7167070d3783b50406834b5edce329fb305938522e610",
    ),
    "Operation-OnlineShopping-AddToCart-006": (
        2,
        ("store-2", "store-3"),
        "4bee691f1ae312136eba6dac62bdd9a161d2d9b15f9687d030450471917b7c9d",
    ),
    "Operation-OnlineShopping-AddToCart-007": (
        2,
        ("store-2", "store-4"),
        "69f0350c202a61518411fe4fff3a72a684de2bb20b103dfcf8bfad147b8572d2",
    ),
    "Operation-OnlineShopping-CheapestProductSearch-007": (
        1,
        ("store-2",),
        "8c67dd7d7b7d4296db395f0e2443641e0edf6a42b02318fa4ddb5254abab2622",
    ),
}


def test_all_eight_cart_tasks_keep_the_audited_gold_contract() -> None:
    """验证 8 个 cart task 的 ID、数量、店铺序列与 gold digest 未漂移。

    输入参数：无；读取仓库中 8 个 canonical task JSON。
    输出返回值：无；每项必须保持 cart 类型和 2026-07-14 审计后 gold。
    """

    assert len(_CART_TASK_CONTRACTS) == 8
    for task_id, (count, stores, expected_digest) in _CART_TASK_CONTRACTS.items():
        task_path = _REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        urls = task["expected_urls"]
        canonical_bytes = (
            json.dumps(
                urls,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        assert task["task_id"] == task_id
        assert task["answer_type"] == "cart"
        assert len(urls) == count
        assert tuple(urlsplit(url).netloc for url in urls) == stores
        assert all(urlsplit(url).scheme == "webmall" for url in urls)
        assert hashlib.sha256(canonical_bytes).hexdigest() == expected_digest


def test_all_eight_audited_gold_carts_pass_native_evaluator() -> None:
    """验证 8 个已审计 gold 都能由 native cart 协议精确评价。

    输入参数：无；将每个 task 的 gold 投影为同一 worker 四店终态。
    输出返回值：无；每个任务均必须闭集满分，包括跨店同 slug 任务。
    """

    for task_id in _CART_TASK_CONTRACTS:
        task_path = _REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json"
        task = json.loads(task_path.read_text(encoding="utf-8"))
        items_by_store: dict[str, list[ObservedCartItem]] = {
            store_id: [] for store_id in ("store-1", "store-2", "store-3", "store-4")
        }
        for url in task["expected_urls"]:
            parts = urlsplit(url)
            slug = parts.path.removeprefix("/product/")
            items_by_store[parts.netloc].append(ObservedCartItem(slug, 1))
        observation = CartObservationBatch(
            complete=True,
            workers=(
                ObservedCartWorker(
                    worker_id="worker-1",
                    complete=True,
                    stores=tuple(
                        ObservedCartStore(
                            logical_store_id=store_id,
                            complete=True,
                            items=tuple(items_by_store[store_id]),
                        )
                        for store_id in (
                            "store-1",
                            "store-2",
                            "store-3",
                            "store-4",
                        )
                    ),
                ),
            ),
        )

        result = evaluate_webmall_cart(task["expected_urls"], observation)

        assert result.passed is True, task_id
        assert result.expected_product_quantity == len(task["expected_urls"])
