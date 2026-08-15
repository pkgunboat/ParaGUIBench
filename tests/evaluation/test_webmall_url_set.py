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


def test_duplicate_submission_is_wrong_instead_of_collapsed_by_set() -> None:
    """验证额外重复 URL 按旧最终 evaluator 的多集合语义判错。

    输入参数：
        无；gold 只有一个商品，Agent 重复报告相同 URL 两次。
    输出返回值：
        无；一个提交消费 gold，第二个提交进入 wrong，整体失败。
    """

    registry = WebMallURLRegistry({"store-1": "https://shop-one.example.test"})
    result = evaluate_webmall_url_set(
        ["webmall://store-1/product/item"],
        [
            "https://shop-one.example.test/product/item",
            "https://shop-one.example.test/product/item",
        ],
        registry,
    )

    assert result.passed is False
    assert result.score == 1.0
    assert len(result.matched) == 1
    assert len(result.wrong) == 1
    assert result.missing == ()
    assert result.precision == 0.5
    assert result.recall == 1.0


def test_duplicate_gold_requires_two_matching_submissions() -> None:
    """验证重复 gold 也保留计数，不被集合去重后错误放宽。

    输入参数：
        无；gold 中同一 URL 出现两次，Agent 只报告一次。
    输出返回值：
        无；仅匹配一个，另一个仍属于 missing，召回和 score 均为 0.5。
    """

    registry = WebMallURLRegistry({"store-1": "https://shop-one.example.test"})
    logical_url = "webmall://store-1/product/item"
    result = evaluate_webmall_url_set(
        [logical_url, logical_url],
        ["https://shop-one.example.test/product/item"],
        registry,
    )

    assert result.passed is False
    assert result.score == 0.5
    assert result.missing == (logical_url,)
    assert result.precision == 1.0
    assert result.recall == 0.5


def test_query_order_deep_html_escape_and_scheme_follow_final_source() -> None:
    """验证迁移保留旧最终 evaluator 的 URL 规范化边界。

    输入参数：
        无；提交使用不同 HTTP scheme、逆序 query 和双层 HTML 转义。
    输出返回值：
        无；主机、路径和参数语义相同即通过，指标均为 1。
    """

    registry = WebMallURLRegistry({"store-1": "https://shop-one.example.test"})
    result = evaluate_webmall_url_set(
        ["webmall://store-1/product/item?a=1&b=2"],
        ["http://shop-one.example.test/product/item?b=2&amp;amp;a=1"],
        registry,
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0


def test_unknown_submitted_origin_is_safe_wrong_value_not_contract_error() -> None:
    """验证未知部署 origin 是 Agent 错答，且结果不回显原始 URL。

    输入参数：
        无；提交含未知 host 与敏感 query 的 URL。
    输出返回值：
        无；评价正常返回失败，wrong 仅保存固定标记。
    """

    registry = WebMallURLRegistry({"store-1": "https://shop-one.example.test"})
    submitted = "https://unknown.example.test/product/x?key=private"

    result = evaluate_webmall_url_set(
        ["webmall://store-1/product/item"],
        [submitted],
        registry,
    )

    assert result.passed is False
    assert len(result.wrong) == 1
    assert submitted not in repr(result)
    assert "private" not in repr(result)
