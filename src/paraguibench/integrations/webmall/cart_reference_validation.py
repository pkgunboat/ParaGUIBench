"""WebMall Cart reader 参考部署验证的脱敏 component receipt。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
)
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
)
from paraguibench.integrations.webmall.environment_manifest import (
    WebMallEnvironmentManifest,
)
from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
)


WEBMALL_CART_REFERENCE_RECEIPT_KIND = (
    "paraguibench.webmall.cart-reader-reference-validation.v1"
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_RECEIPT_FIELDS = frozenset(
    {
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
)


class WebMallCartReferenceValidationError(ValueError):
    """表示参考部署证明或脱敏 component receipt 无法严格闭合。"""

    code = "WEBMALL_CART_REFERENCE_VALIDATION_INVALID"

    def __init__(self) -> None:
        """构造不回显 Cart、origin、worker、路径或底层异常的错误。

        输入参数：无。
        输出返回值：无；公开异常文本固定为稳定 code。
        """

        super().__init__(self.code)


@dataclass(frozen=True, slots=True, repr=False)
class WebMallCartReferenceCaptureProof:
    """保存一次内存捕获中可公开验证、不可推导 Cart 内容的事实。

    输入参数：
        browser_context_continuity_verified：prepare 与 capture 是否绑定同一
            Attempt-owned BrowserContext。
        sweep_store_ids：每个完整 sweep 的 logical store 顺序。
        normalized_universe_match：两个标准化四店 universe 是否完全相同。
    输出返回值：不可变且 ``repr`` 不展开的安全证明对象。
    """

    browser_context_continuity_verified: bool
    sweep_store_ids: tuple[tuple[str, ...], ...]
    normalized_universe_match: bool

    def __post_init__(self) -> None:
        """验证证明只表达固定两次四店 sweep 的成功闭包。

        输入参数：无；读取数据类字段。
        输出返回值：无；严格成功事实正常返回。
        异常：WebMallCartReferenceValidationError：类型、顺序或状态无效。
        """

        expected_sweeps = (
            WEBMALL_LOGICAL_STORE_IDS,
            WEBMALL_LOGICAL_STORE_IDS,
        )
        if (
            self.browser_context_continuity_verified is not True
            or self.normalized_universe_match is not True
            or not isinstance(self.sweep_store_ids, tuple)
            or self.sweep_store_ids != expected_sweeps
        ):
            raise WebMallCartReferenceValidationError


@dataclass(frozen=True, slots=True, repr=False)
class WebMallCartReferenceReceipt:
    """保存字段闭合、无 Cart 内容的参考部署 component receipt。"""

    schema_version: int
    receipt_kind: str
    outcome: str
    component_revision: str
    webmall_manifest_id: str
    webmall_manifest_sha256: str
    webmall_environment_id: str
    store_universe_id: str
    browser_environment_id: str
    browser_image_manifest_sha256: str
    browser_extracted_sha256: str
    browser_container_image: str
    cart_reader_protocol_id: str
    cart_evidence_protocol_id: str
    browser_context_continuity_verified: bool
    sweep_store_ids: tuple[tuple[str, ...], ...]
    normalized_universe_match: bool

    def to_dict(self) -> dict[str, Any]:
        """返回可严格 JSON 序列化且不含运行时私有值的字段闭集。

        输入参数：无。
        输出返回值：仅含固定 schema 字段的新字典；双 sweep 转为 JSON list。
        """

        return {
            "schema_version": self.schema_version,
            "receipt_kind": self.receipt_kind,
            "outcome": self.outcome,
            "component_revision": self.component_revision,
            "webmall_manifest_id": self.webmall_manifest_id,
            "webmall_manifest_sha256": self.webmall_manifest_sha256,
            "webmall_environment_id": self.webmall_environment_id,
            "store_universe_id": self.store_universe_id,
            "browser_environment_id": self.browser_environment_id,
            "browser_image_manifest_sha256": (self.browser_image_manifest_sha256),
            "browser_extracted_sha256": self.browser_extracted_sha256,
            "browser_container_image": self.browser_container_image,
            "cart_reader_protocol_id": self.cart_reader_protocol_id,
            "cart_evidence_protocol_id": self.cart_evidence_protocol_id,
            "browser_context_continuity_verified": (
                self.browser_context_continuity_verified
            ),
            "sweep_store_ids": [list(sweep) for sweep in self.sweep_store_ids],
            "normalized_universe_match": self.normalized_universe_match,
        }


def build_webmall_cart_reference_receipt(
    *,
    manifest: WebMallEnvironmentManifest,
    browser_image: OSWorldImageManifest,
    webmall_manifest_sha256: str,
    component_revision: str,
    observation: CartObservationBatch,
    capture_proof: WebMallCartReferenceCaptureProof,
) -> WebMallCartReferenceReceipt:
    """从可信内存观测构造不含 worker、Cart 或 origin 的 component receipt。

    输入参数：
        manifest/browser_image：当前仓库已严格解析的 WebMall 与浏览器身份。
        webmall_manifest_sha256/component_revision：完整 manifest 字节摘要与
            本次 Cart reader 组件闭包摘要。
        observation：同一 Attempt 的单 worker×四店完整内存观测。
        capture_proof：prepare→capture context 连续性和两次 sweep 事实。
    输出返回值：字段闭合的不可变 ``WebMallCartReferenceReceipt``。
    异常：WebMallCartReferenceValidationError：任一身份或证据不完整。
    """

    try:
        if (
            not isinstance(manifest, WebMallEnvironmentManifest)
            or not isinstance(browser_image, OSWorldImageManifest)
            or _SHA256_PATTERN.fullmatch(webmall_manifest_sha256) is None
            or _SHA256_PATTERN.fullmatch(component_revision) is None
            or not isinstance(observation, CartObservationBatch)
            or not isinstance(capture_proof, WebMallCartReferenceCaptureProof)
            or not browser_image.live_run_ready
            or manifest.browser_runtime.required_protocol_id
            not in browser_image.protocol_ids
            or not observation.complete
            or len(observation.workers) != 1
            or not observation.workers[0].complete
            or tuple(store.logical_store_id for store in observation.workers[0].stores)
            != WEBMALL_LOGICAL_STORE_IDS
            or any(not store.complete for store in observation.workers[0].stores)
        ):
            raise TypeError
        extracted_sha256 = browser_image.extracted_sha256
        if (
            not isinstance(extracted_sha256, str)
            or _SHA256_PATTERN.fullmatch(extracted_sha256) is None
        ):
            raise TypeError
    except Exception:
        raise WebMallCartReferenceValidationError from None
    return WebMallCartReferenceReceipt(
        schema_version=1,
        receipt_kind=WEBMALL_CART_REFERENCE_RECEIPT_KIND,
        outcome="PASSED",
        component_revision=component_revision,
        webmall_manifest_id=manifest.manifest_id,
        webmall_manifest_sha256=webmall_manifest_sha256,
        webmall_environment_id=manifest.environment_id,
        store_universe_id=manifest.store_universe_id,
        browser_environment_id=browser_image.environment_id,
        browser_image_manifest_sha256=(manifest.browser_runtime.image_manifest_sha256),
        browser_extracted_sha256=extracted_sha256,
        browser_container_image=browser_image.container_image,
        cart_reader_protocol_id=manifest.cart_reader.protocol_id,
        cart_evidence_protocol_id=manifest.cart_reader.evidence_protocol_id,
        browser_context_continuity_verified=(
            capture_proof.browser_context_continuity_verified
        ),
        sweep_store_ids=capture_proof.sweep_store_ids,
        normalized_universe_match=capture_proof.normalized_universe_match,
    )


def validate_webmall_cart_reference_receipt(
    value: object,
    *,
    manifest: WebMallEnvironmentManifest,
    browser_image: OSWorldImageManifest,
    expected_webmall_manifest_sha256: str,
    expected_component_revision: str,
) -> WebMallCartReferenceReceipt:
    """严格验证 component receipt 字段闭集及其当前版本身份。

    输入参数：
        value：已由 duplicate-key-safe JSON parser 读取的 receipt object。
        manifest/browser_image：当前受信仓库解析出的两个环境 manifest。
        expected_webmall_manifest_sha256/expected_component_revision：当前
            manifest 字节与 Cart reader 组件闭包的预期摘要。
    输出返回值：身份完全匹配的不可变 ``WebMallCartReferenceReceipt``。
    异常：WebMallCartReferenceValidationError：额外/缺失字段、类型、成功
        事实或任一版本身份漂移；错误不回显候选值。
    """

    try:
        if (
            not isinstance(value, Mapping)
            or set(value) != _RECEIPT_FIELDS
            or not isinstance(manifest, WebMallEnvironmentManifest)
            or not isinstance(browser_image, OSWorldImageManifest)
            or _SHA256_PATTERN.fullmatch(expected_webmall_manifest_sha256) is None
            or _SHA256_PATTERN.fullmatch(expected_component_revision) is None
            or not browser_image.live_run_ready
            or manifest.browser_runtime.required_protocol_id
            not in browser_image.protocol_ids
        ):
            raise TypeError
        raw_sweeps = value["sweep_store_ids"]
        if (
            not isinstance(raw_sweeps, list)
            or any(not isinstance(sweep, list) for sweep in raw_sweeps)
            or any(
                any(not isinstance(store_id, str) for store_id in sweep)
                for sweep in raw_sweeps
            )
        ):
            raise TypeError
        proof = WebMallCartReferenceCaptureProof(
            browser_context_continuity_verified=value[
                "browser_context_continuity_verified"
            ],
            sweep_store_ids=tuple(tuple(sweep) for sweep in raw_sweeps),
            normalized_universe_match=value["normalized_universe_match"],
        )
        extracted_sha256 = browser_image.extracted_sha256
        if (
            not isinstance(extracted_sha256, str)
            or not isinstance(value["schema_version"], int)
            or isinstance(value["schema_version"], bool)
            or value["schema_version"] != 1
            or value["receipt_kind"] != WEBMALL_CART_REFERENCE_RECEIPT_KIND
            or value["outcome"] != "PASSED"
            or value["component_revision"] != expected_component_revision
            or value["webmall_manifest_id"] != manifest.manifest_id
            or value["webmall_manifest_sha256"] != expected_webmall_manifest_sha256
            or value["webmall_environment_id"] != manifest.environment_id
            or value["store_universe_id"] != manifest.store_universe_id
            or value["browser_environment_id"] != browser_image.environment_id
            or value["browser_image_manifest_sha256"]
            != manifest.browser_runtime.image_manifest_sha256
            or value["browser_extracted_sha256"] != extracted_sha256
            or value["browser_container_image"] != browser_image.container_image
            or value["cart_reader_protocol_id"] != manifest.cart_reader.protocol_id
            or value["cart_evidence_protocol_id"]
            != manifest.cart_reader.evidence_protocol_id
        ):
            raise TypeError
    except Exception:
        raise WebMallCartReferenceValidationError from None
    return WebMallCartReferenceReceipt(
        schema_version=1,
        receipt_kind=WEBMALL_CART_REFERENCE_RECEIPT_KIND,
        outcome="PASSED",
        component_revision=expected_component_revision,
        webmall_manifest_id=manifest.manifest_id,
        webmall_manifest_sha256=expected_webmall_manifest_sha256,
        webmall_environment_id=manifest.environment_id,
        store_universe_id=manifest.store_universe_id,
        browser_environment_id=browser_image.environment_id,
        browser_image_manifest_sha256=(manifest.browser_runtime.image_manifest_sha256),
        browser_extracted_sha256=extracted_sha256,
        browser_container_image=browser_image.container_image,
        cart_reader_protocol_id=manifest.cart_reader.protocol_id,
        cart_evidence_protocol_id=manifest.cart_reader.evidence_protocol_id,
        browser_context_continuity_verified=(proof.browser_context_continuity_verified),
        sweep_store_ids=proof.sweep_store_ids,
        normalized_universe_match=proof.normalized_universe_match,
    )
