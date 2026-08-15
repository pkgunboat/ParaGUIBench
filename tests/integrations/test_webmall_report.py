"""WebMall Agent 最终报告 URL 提取与 logical 化测试。"""

from __future__ import annotations

from paraguibench.integrations.webmall import WebMallURLRegistry
from paraguibench.integrations.webmall.report import (
    INVALID_REPORTED_LOGICAL_URL,
    extract_reported_logical_product_urls,
)


def _registry() -> WebMallURLRegistry:
    """创建不访问网络的四店 runtime origin 注册表。

    输入参数：
        无。
    输出返回值：
        使用测试专用域名的 ``WebMallURLRegistry``。
    """

    return WebMallURLRegistry(
        {
            "store-1": "https://shop-1.invalid",
            "store-2": "https://shop-2.invalid:9443",
            "store-3": "http://shop-3.invalid",
            "store-4": "http://shop-4.invalid:9084",
        }
    )


def test_extract_report_preserves_hash_delimited_order_and_duplicates() -> None:
    """验证 ``###`` 报告中的 URL 顺序与重复项不会被 set 语义吞掉。

    输入参数：
        无；最终文本包含两个不同 URL，并重复报告第一个 URL。
    输出返回值：
        无；返回三个 logical URL，供组合 evaluator 将重复项判失败。
    """

    output = (
        "<answer>https://shop-1.invalid/product/alpha"
        "###https://shop-2.invalid:9443/product/beta"
        "###https://shop-1.invalid/product/alpha</answer>"
    )

    result = extract_reported_logical_product_urls(output, _registry())

    assert result == (
        "webmall://store-1/product/alpha",
        "webmall://store-2/product/beta",
        "webmall://store-1/product/alpha",
    )


def test_unknown_origin_becomes_stable_invalid_marker_without_echoing_url() -> None:
    """验证未知 origin 是 Agent 报告错误，而非回显外部 URL 的契约异常。

    输入参数：
        无；报告一个未注册且带敏感 query 的 URL。
    输出返回值：
        无；结果只含固定非法标记，不保留 host、路径或 query 值。
    """

    secret_url = "https://unknown.invalid/product/x?key=secret-order-key"
    result = extract_reported_logical_product_urls(secret_url, _registry())

    assert result == (INVALID_REPORTED_LOGICAL_URL,)
    assert secret_url not in repr(result)
    assert "secret-order-key" not in repr(result)


def test_logical_url_is_validated_against_registry_and_fragment_is_preserved() -> None:
    """验证 logical URL 也需存在于 registry，且 fragment 不被静默截断。

    输入参数：
        无；分别提交已配置 logical URL、未知 store 和带 fragment URL。
    输出返回值：
        无；已配置项保留，未知项变固定标记，fragment 留给 core 严格拒绝。
    """

    result = extract_reported_logical_product_urls(
        "webmall://store-3/product/gamma"
        "###webmall://store-9/product/wrong"
        "###http://shop-4.invalid:9084/product/delta#fragment",
        _registry(),
    )

    assert result == (
        "webmall://store-3/product/gamma",
        INVALID_REPORTED_LOGICAL_URL,
        "webmall://store-4/product/delta#fragment",
    )


def test_report_without_urls_returns_empty_tuple() -> None:
    """验证完成词或普通说明不会伪造 URL 证据。

    输入参数：
        无；最终文本不含任何 URL。
    输出返回值：
        无；返回空元组，由 FindAndOrder 协议判报告缺失。
    """

    assert (
        extract_reported_logical_product_urls(
            "Done; purchase completed.",
            _registry(),
        )
        == ()
    )
