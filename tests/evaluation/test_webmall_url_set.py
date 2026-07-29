"""WebMall string 任务的 logical URL 精确集合评价测试。"""

from __future__ import annotations

from paraguibench.evaluation.webmall import evaluate_webmall_url_set
from paraguibench.integrations.webmall import WebMallURLRegistry


def test_same_store_survives_origin_change_but_other_store_does_not_match() -> None:
    """验证评价语义绑定 store ID，而不是旧机器 host 或单独 pathname。

    输入参数：
        无；两个 store 使用相同商品路径和不同的当前部署 origin。
    输出返回值：
        无；同 store 的当前 URL 通过，不同 store 的同路径 URL 失败。
    """

    registry = WebMallURLRegistry(
        {
            "store-1": "https://new-shop-one.example.test:9443",
            "store-2": "https://new-shop-two.example.test:9444",
        }
    )
    expected = ["webmall://store-1/product/shared-item"]

    same_store = evaluate_webmall_url_set(
        expected,
        ["https://new-shop-one.example.test:9443/product/shared-item"],
        registry,
    )
    other_store = evaluate_webmall_url_set(
        expected,
        ["https://new-shop-two.example.test:9444/product/shared-item"],
        registry,
    )

    assert same_store.passed is True
    assert same_store.score == 1.0
    assert other_store.passed is False
    assert other_store.score == 0.0
    assert other_store.missing == tuple(expected)
