"""WebMall logical URL 与部署 origin 的双向映射测试。"""

from __future__ import annotations

import pytest

from paraguibench.integrations.webmall import (
    WebMallURLRegistry,
    WebMallURLRegistryError,
)


def test_registry_round_trip_preserves_store_and_encoded_path() -> None:
    """验证 logical URL 往返映射不丢失商店身份和编码路径。

    输入参数：
        无；测试使用 RFC 保留的 ``example.test`` 合成域名。
    输出返回值：
        无；物化 URL 与重新 canonicalize 的 URL 必须逐字符合预期。
    """

    registry = WebMallURLRegistry(
        {"store-1": "https://shop-one.example.test:9443"}
    )
    logical_url = (
        "webmall://store-1/product/Example%20Name?ref=abc#details"
    )

    runtime_url = registry.materialize_url(logical_url)

    assert runtime_url == (
        "https://shop-one.example.test:9443/"
        "product/Example%20Name?ref=abc#details"
    )
    assert registry.canonicalize_url(runtime_url) == logical_url


def test_registry_rejects_duplicate_origins_without_leaking_origin() -> None:
    """验证两个 store 不能共享 origin，且异常不回显部署地址。

    输入参数：
        无；测试使用合成 origin 同时绑定两个不同 store。
    输出返回值：
        无；构造注册表必须 fail-closed，并只报告配置类别。
    """

    sentinel_origin = "https://duplicate-origin.example.test:9443"

    with pytest.raises(WebMallURLRegistryError) as captured:
        WebMallURLRegistry(
            {
                "store-1": sentinel_origin,
                "store-2": sentinel_origin,
            }
        )

    assert sentinel_origin not in str(captured.value)


@pytest.mark.parametrize(
    "invalid_origin",
    [
        "ftp://shop.example.test",
        "https://user:password@shop.example.test",
        "https://shop.example.test/catalog",
        "https://shop.example.test?tenant=one",
        "https://shop.example.test#fragment",
    ],
)
def test_registry_rejects_values_that_are_not_safe_http_origins(
    invalid_origin: str,
) -> None:
    """验证 registry 配置只能包含无凭据和附加部分的 HTTP(S) origin。

    输入参数：
        invalid_origin：协议、用户信息、路径、查询或 fragment 不合规的值。
    输出返回值：
        无；构造时必须拒绝，且异常不得包含原始配置值。
    """

    with pytest.raises(WebMallURLRegistryError) as captured:
        WebMallURLRegistry({"store-1": invalid_origin})

    assert invalid_origin not in str(captured.value)


def test_registry_rejects_unknown_runtime_origin_without_leaking_url() -> None:
    """验证 evaluator 遇到未知 runtime origin 时安全失败。

    输入参数：
        无；registry 与待转换 URL 使用两个不同的合成 origin。
    输出返回值：
        无；异常不得包含 Agent 返回的完整未知 URL。
    """

    registry = WebMallURLRegistry(
        {"store-1": "https://known-shop.example.test"}
    )
    unknown_url = "https://unknown-shop.example.test/product/private-query"

    with pytest.raises(WebMallURLRegistryError) as captured:
        registry.canonicalize_url(unknown_url)

    assert unknown_url not in str(captured.value)


def test_registry_materializes_every_logical_origin_in_instruction_text() -> None:
    """验证一段 instruction 中多个 store origin 使用同一 registry 物化。

    输入参数：
        无；文本同时包含两个 store，并保留路径编码与句末标点。
    输出返回值：
        无；仅 logical origin 被替换，路径及普通文本不得变化。
    """

    registry = WebMallURLRegistry(
        {
            "store-1": "https://shop-one.example.test:9443",
            "store-2": "http://shop-two.example.test:9082",
        }
    )
    logical_text = (
        "Compare webmall://store-1/product/a%20b with "
        "webmall://store-2/product/a%20b."
    )

    assert registry.materialize_text(logical_text) == (
        "Compare https://shop-one.example.test:9443/product/a%20b with "
        "http://shop-two.example.test:9082/product/a%20b."
    )
