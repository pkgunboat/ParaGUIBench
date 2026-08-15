"""显式执行 WebMall Cart reader 参考部署验证而不运行 Agent。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceReceipt,
    build_webmall_cart_reference_receipt,
)
from paraguibench.integrations.webmall.environment_manifest import (
    WebMallEnvironmentManifest,
)
from paraguibench.runstore import RunVersionVector
from paraguibench.runtime.webmall_cart_component_receipts import (
    derive_webmall_cart_component_identity,
)


class WebMallCartReferenceRuntimeError(RuntimeError):
    """表示显式参考验证的生命周期、捕获或清理无法闭合。"""

    code = "WEBMALL_CART_REFERENCE_RUNTIME_INVALID"

    def __init__(self) -> None:
        """构造不回显 task、Cart、origin、worker 或底层异常的错误。

        输入参数：无。
        输出返回值：无；公开文本固定为稳定 code。
        """

        super().__init__(self.code)


def build_webmall_cart_reference_component_revision(
    version_vector: RunVersionVector,
    *,
    repo_root: Path,
) -> str:
    """经 RunVersionVector 协议门禁导出 receipt-neutral 组件摘要。

    输入参数：version_vector 为 WebMall Cart preflight 构造的版本
        向量，只用于确认 Cart/WebMall 协议与三份代码版本一致；
        repo_root 用于派生与 P0b loader 共享的稳定组件身份。
    输出返回值：不包含派生 runtime-support、receipt 或 allowlist
        的 64 位小写组件 SHA-256。
    异常：WebMallCartReferenceRuntimeError：向量格式、协议、三份
        代码 revision 或共享组件闭集无效。
    """

    if not isinstance(version_vector, RunVersionVector) or not isinstance(
        repo_root, Path
    ):
        raise WebMallCartReferenceRuntimeError
    matched = re.fullmatch(
        r"tree-sha256:([0-9a-f]{64})",
        version_vector.source_revision,
    )
    if (
        matched is None
        or version_vector.agent_code_revision != version_vector.source_revision
        or version_vector.evaluator_revision != version_vector.source_revision
        or version_vector.evaluation_protocol
        != "paraguibench.webmall.cart.closed-world.v1"
        or version_vector.environment_protocol != "webmall.browser.v1"
        or re.fullmatch(
            r"manifest-sha256:[0-9a-f]{64}",
            version_vector.environment_revision,
        )
        is None
    ):
        raise WebMallCartReferenceRuntimeError
    try:
        identity = derive_webmall_cart_component_identity(repo_root)
    except Exception:
        raise WebMallCartReferenceRuntimeError from None
    return identity.component_identity_sha256


def run_webmall_cart_reference_validation(
    *,
    environment: Any,
    task: Mapping[str, Any],
    manifest: WebMallEnvironmentManifest,
    browser_image: OSWorldImageManifest,
    webmall_manifest_sha256: str,
    component_revision: str,
) -> WebMallCartReferenceReceipt:
    """在 owned 环境中只执行 Cart prepare/capture 并构造 component receipt。

    输入参数：
        environment：实现 ``start/prepare/cart_observation/``
            ``reference_validation_proof/close`` 的 Cart 专属环境。
        task：可信 canonical WebMall Cart task；仅用于准备，不进入 receipt。
        manifest/browser_image：当前版本化环境身份。
        webmall_manifest_sha256/component_revision：当前 manifest 与组件摘要。
    输出返回值：环境成功关闭后返回脱敏 component receipt。
    异常：WebMallCartReferenceRuntimeError：接口、生命周期、捕获、receipt
        构造或清理任一步失败；错误不保留底层敏感 cause。
    """

    try:
        if not isinstance(task, Mapping):
            raise TypeError
        for method_name in (
            "start",
            "prepare",
            "cart_observation",
            "reference_validation_proof",
            "close",
        ):
            if not callable(getattr(environment, method_name, None)):
                raise TypeError
    except Exception:
        raise WebMallCartReferenceRuntimeError from None

    receipt: WebMallCartReferenceReceipt | None = None
    failed = False
    try:
        environment.start()
        environment.prepare(task)
        observation = environment.cart_observation()
        proof = environment.reference_validation_proof()
        receipt = build_webmall_cart_reference_receipt(
            manifest=manifest,
            browser_image=browser_image,
            webmall_manifest_sha256=webmall_manifest_sha256,
            component_revision=component_revision,
            observation=observation,
            capture_proof=proof,
        )
    except Exception:
        failed = True
    finally:
        try:
            environment.close()
        except Exception:
            failed = True
    if failed or receipt is None:
        raise WebMallCartReferenceRuntimeError from None
    return receipt
