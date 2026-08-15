"""WebMall logical URL 的 Agent-only 部署物化与审计隔离测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from paraguibench.benchmark import prepare_release_task
from paraguibench.integrations.webmall import (
    WebMallURLRegistry,
    load_webmall_environment_manifest,
)
from paraguibench.runtime.webmall_preparation import (
    WebMallPreparationError,
    materialize_webmall_prepared_task,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"


def _four_store_registry() -> WebMallURLRegistry:
    """构造与正式 manifest 同 scope 的公开测试 origin registry。

    输入参数：
        无。
    输出返回值：
        store-1 至 store-4 的双向 URL registry。
    """

    return WebMallURLRegistry(
        {
            f"store-{index}": f"https://shop-{index}.example.invalid"
            for index in range(1, 5)
        }
    )


def test_webmall_preparation_materializes_only_agent_instruction() -> None:
    """验证部署 origin 进入 Agent 指令，但 trusted gold 始终保持 logical。

    输入参数：
        无；准备真实 EndToEnd-001 与固定四店测试 origin。
    输出返回值：
        无；Agent 可导航，evaluator 仍读取 ``webmall://`` expected URLs。
    """

    prepared = prepare_release_task(
        REPO_ROOT,
        "Operation-OnlineShopping-EndToEnd-001",
        {},
    )
    manifest = load_webmall_environment_manifest(MANIFEST_PATH)

    bound = materialize_webmall_prepared_task(
        prepared,
        manifest=manifest,
        registry=_four_store_registry(),
    )

    assert "webmall://" not in bound.agent_task["instruction"]
    assert "https://shop-1.example.invalid" in bound.agent_task["instruction"]
    assert "https://shop-4.example.invalid" in bound.agent_task["instruction"]
    assert all(
        url.startswith("webmall://") for url in bound.trusted_task["expected_urls"]
    )
    assert prepared.agent_task["instruction"].count("webmall://") >= 4
    assert prepared.trusted_task == bound.trusted_task


def test_webmall_preparation_audit_contains_only_binding_identities() -> None:
    """验证 audit metadata 不持久化 runtime origin、profile 或 logical gold。

    输入参数：
        无；使用真实 Checkout-001 与含 sentinel host 的 registry。
    输出返回值：
        无；审计只增加 manifest、universe 与四个环境变量名。
    """

    prepared = prepare_release_task(
        REPO_ROOT,
        "Operation-OnlineShopping-Checkout-001",
        {},
    )
    manifest = load_webmall_environment_manifest(MANIFEST_PATH)
    registry = WebMallURLRegistry(
        {
            f"store-{index}": f"https://private-origin-{index}.example.invalid"
            for index in range(1, 5)
        }
    )

    bound = materialize_webmall_prepared_task(
        prepared,
        manifest=manifest,
        registry=registry,
    )
    rendered = repr(bound.audit_metadata)

    assert bound.audit_metadata["webmall_environment"] == {
        "manifest_id": "webmall.reference-four-stores.v1",
        "store_universe_id": "webmall.four-stores.v1",
        "origin_binding_names": [
            "PARAGUIBENCH_WEBMALL_STORE_1_ORIGIN",
            "PARAGUIBENCH_WEBMALL_STORE_2_ORIGIN",
            "PARAGUIBENCH_WEBMALL_STORE_3_ORIGIN",
            "PARAGUIBENCH_WEBMALL_STORE_4_ORIGIN",
        ],
    }
    assert "private-origin" not in rendered
    assert "4242424242424242" not in rendered
    assert "expected_urls" not in rendered


def test_webmall_preparation_rejects_partial_registry_without_values() -> None:
    """验证 registry scope 少于 manifest 四店时失败且不回显 origin。

    输入参数：
        无；只提供一个带私密 sentinel 的 store origin。
    输出返回值：
        无；固定错误不包含 host、instruction 或 checkout profile。
    """

    prepared = prepare_release_task(
        REPO_ROOT,
        "Operation-OnlineShopping-Checkout-001",
        {},
    )
    manifest = load_webmall_environment_manifest(MANIFEST_PATH)
    registry = WebMallURLRegistry(
        {"store-1": "https://private-sentinel.example.invalid"}
    )

    with pytest.raises(WebMallPreparationError) as caught:
        materialize_webmall_prepared_task(
            prepared,
            manifest=manifest,
            registry=registry,
        )

    rendered = f"{caught.value!s}|{caught.value!r}"
    assert str(caught.value) == "WEBMALL_PREPARATION_ERROR"
    assert "private-sentinel" not in rendered
    assert "example.invalid" not in rendered
