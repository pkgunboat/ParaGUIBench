"""WebMall Cart 参考部署 component receipt 的闭集与隐私测试。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    load_osworld_image_manifest_bytes_with_sha256,
)
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartItem,
    ObservedCartStore,
    ObservedCartWorker,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceCaptureProof,
    WebMallCartReferenceValidationError,
    build_webmall_cart_reference_receipt,
    validate_webmall_cart_reference_receipt,
)
from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEBMALL_MANIFEST = load_webmall_environment_manifest(
    _REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"
)


def _load_verified_browser_image() -> OSWorldImageManifest:
    """通过生产 strict loader 构造隔离的 schema-v2 verified 镜像快照。

    输入参数：无；读取仓库正式 pending manifest 的完整 recipe。
    输出返回值：由严格字节 loader 重建、语义身份完整且
        ``live_run_ready=True`` 的测试快照；不修改正式 manifest。
    """

    raw = json.loads(
        (_REPO_ROOT / "environments" / "osworld" / "image-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    raw["extracted_image"]["status"] = "verified_reproducible_materialization"
    payload = (json.dumps(raw, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    browser_image, _manifest_sha256 = load_osworld_image_manifest_bytes_with_sha256(
        payload
    )
    assert browser_image.live_run_ready is True
    return browser_image


_BROWSER_IMAGE = _load_verified_browser_image()


def _private_observation() -> CartObservationBatch:
    """构造含私有 worker/slug 的单 worker×四店完整 Cart 观测。

    输入参数：无。
    输出返回值：只有 store-2 含一条私有商品的完整批次。
    """

    return CartObservationBatch(
        complete=True,
        workers=(
            ObservedCartWorker(
                worker_id="private-attempt-worker",
                complete=True,
                stores=tuple(
                    ObservedCartStore(
                        logical_store_id=f"store-{index}",
                        complete=True,
                        items=(
                            (ObservedCartItem("private-cart-slug", 2),)
                            if index == 2
                            else ()
                        ),
                    )
                    for index in range(1, 5)
                ),
            ),
        ),
    )


def test_receipt_builder_emits_only_closed_sanitized_component_facts() -> None:
    """验证通过 receipt 只保留版本身份、context 连续性与双 sweep 事实。

    输入参数：无；提供含私有 worker/slug 的完整内存观测。
    输出返回值：无；字段严格闭合，序列化结果不含 Cart、origin 或 worker。
    """

    sweep = ("store-1", "store-2", "store-3", "store-4")
    receipt = build_webmall_cart_reference_receipt(
        manifest=_WEBMALL_MANIFEST,
        browser_image=_BROWSER_IMAGE,
        webmall_manifest_sha256="2" * 64,
        component_revision="3" * 64,
        observation=_private_observation(),
        capture_proof=WebMallCartReferenceCaptureProof(
            browser_context_continuity_verified=True,
            sweep_store_ids=(sweep, sweep),
            normalized_universe_match=True,
        ),
    )

    payload = receipt.to_dict()
    assert set(payload) == {
        "schema_version",
        "receipt_kind",
        "outcome",
        "component_revision",
        "webmall_manifest_id",
        "webmall_manifest_sha256",
        "webmall_environment_id",
        "store_universe_id",
        "browser_environment_id",
        "browser_image_manifest_sha256",
        "browser_extracted_sha256",
        "browser_container_image",
        "cart_reader_protocol_id",
        "cart_evidence_protocol_id",
        "browser_context_continuity_verified",
        "sweep_store_ids",
        "normalized_universe_match",
    }
    assert payload["receipt_kind"] == (
        "paraguibench.webmall.cart-reader-reference-validation.v1"
    )
    assert payload["outcome"] == "PASSED"
    assert payload["sweep_store_ids"] == [list(sweep), list(sweep)]
    rendered = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "private-attempt-worker",
        "private-cart-slug",
        "example.invalid",
        "webmall://",
        "/cart/",
    ):
        assert forbidden not in rendered


def test_receipt_validator_accepts_only_current_component_identity() -> None:
    """验证 receipt validator 将序列化字段重新绑定到当前两个 manifest。

    输入参数：无；使用 builder 生成当前成功 payload。
    输出返回值：无；validator 返回等价不可变 receipt。
    """

    sweep = ("store-1", "store-2", "store-3", "store-4")
    built = build_webmall_cart_reference_receipt(
        manifest=_WEBMALL_MANIFEST,
        browser_image=_BROWSER_IMAGE,
        webmall_manifest_sha256="2" * 64,
        component_revision="3" * 64,
        observation=_private_observation(),
        capture_proof=WebMallCartReferenceCaptureProof(
            browser_context_continuity_verified=True,
            sweep_store_ids=(sweep, sweep),
            normalized_universe_match=True,
        ),
    )

    validated = validate_webmall_cart_reference_receipt(
        built.to_dict(),
        manifest=_WEBMALL_MANIFEST,
        browser_image=_BROWSER_IMAGE,
        expected_webmall_manifest_sha256="2" * 64,
        expected_component_revision="3" * 64,
    )

    assert validated == built


def test_receipt_validator_rejects_browser_without_required_protocol() -> None:
    """验证 validator 不能把不含 WebMall 必需协议的镜像视为当前身份。

    输入参数：无；从合法 receipt 与 live-ready 镜像派生缺协议镜像。
    输出返回值：无；即使 receipt 字段未改，当前身份仍失败关闭。
    """

    sweep = ("store-1", "store-2", "store-3", "store-4")
    built = build_webmall_cart_reference_receipt(
        manifest=_WEBMALL_MANIFEST,
        browser_image=_BROWSER_IMAGE,
        webmall_manifest_sha256="2" * 64,
        component_revision="3" * 64,
        observation=_private_observation(),
        capture_proof=WebMallCartReferenceCaptureProof(
            browser_context_continuity_verified=True,
            sweep_store_ids=(sweep, sweep),
            normalized_universe_match=True,
        ),
    )
    incompatible_image = replace(
        _BROWSER_IMAGE,
        protocol_ids=("osworld.desktop.v1",),
    )

    with pytest.raises(WebMallCartReferenceValidationError):
        validate_webmall_cart_reference_receipt(
            built.to_dict(),
            manifest=_WEBMALL_MANIFEST,
            browser_image=incompatible_image,
            expected_webmall_manifest_sha256="2" * 64,
            expected_component_revision="3" * 64,
        )


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload.update(private_origin="https://private.invalid"),
        lambda payload: payload.pop("outcome"),
        lambda payload: payload.update(outcome="FAILED"),
        lambda payload: payload.update(component_revision="4" * 64),
        lambda payload: payload.update(browser_context_continuity_verified=False),
        lambda payload: payload.update(normalized_universe_match=False),
        lambda payload: payload.update(
            sweep_store_ids=[
                ["store-1", "store-2", "store-3", "store-4"],
                ["store-1", "store-2", "store-4", "store-3"],
            ]
        ),
    ),
)
def test_receipt_validator_rejects_field_or_success_fact_drift(
    mutation: object,
) -> None:
    """验证额外/缺失字段、版本漂移与伪成功事实全部固定失败关闭。

    输入参数：mutation 为对合法 receipt payload 的单一对抗修改。
    输出返回值：无；validator 抛固定错误且不回显私有候选值。
    """

    sweep = ("store-1", "store-2", "store-3", "store-4")
    payload = build_webmall_cart_reference_receipt(
        manifest=_WEBMALL_MANIFEST,
        browser_image=_BROWSER_IMAGE,
        webmall_manifest_sha256="2" * 64,
        component_revision="3" * 64,
        observation=_private_observation(),
        capture_proof=WebMallCartReferenceCaptureProof(
            browser_context_continuity_verified=True,
            sweep_store_ids=(sweep, sweep),
            normalized_universe_match=True,
        ),
    ).to_dict()
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(WebMallCartReferenceValidationError) as captured:
        validate_webmall_cart_reference_receipt(
            payload,
            manifest=_WEBMALL_MANIFEST,
            browser_image=_BROWSER_IMAGE,
            expected_webmall_manifest_sha256="2" * 64,
            expected_component_revision="3" * 64,
        )

    assert str(captured.value) == "WEBMALL_CART_REFERENCE_VALIDATION_INVALID"
    assert "private.invalid" not in repr(captured.value)
