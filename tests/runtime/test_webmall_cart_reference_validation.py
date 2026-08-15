"""WebMall Cart 显式 reference validation 内部纵向入口测试。"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)
from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
    ObservedCartStore,
    ObservedCartWorker,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceCaptureProof,
)
from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest,
)
from paraguibench.runstore import RunVersionVector
from paraguibench.runtime.webmall_cart_component_receipts import (
    derive_webmall_cart_component_identity,
)
from paraguibench.runtime.webmall_cart_reference_validation import (
    build_webmall_cart_reference_component_revision,
    run_webmall_cart_reference_validation,
    WebMallCartReferenceRuntimeError,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = load_webmall_environment_manifest(
    _REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"
)
_TASK = {
    "task_id": "Operation-OnlineShopping-AddToCart-001",
    "task_source": "WebMall",
    "task_type": "QA",
    "answer_type": "cart",
    "evaluator_path": "evaluators/cart_evaluator.py",
    "expected_urls": ("webmall://store-1/product/private-gold",),
}


class _ReferenceEnvironment:
    """以公开生命周期驱动内部入口的无 I/O Cart 环境 fake。"""

    def __init__(self) -> None:
        """初始化严格调用序列。

        输入参数：无。
        输出返回值：无；``calls`` 初始为空。
        """

        self.calls: list[str] = []

    def start(self) -> None:
        """记录 owned 环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("start")

    def prepare(self, task: object) -> None:
        """确认内部入口传入同一个可信 task。

        输入参数：task 为 reference validation 使用的 canonical Cart task。
        输出返回值：无。
        """

        assert task is _TASK
        self.calls.append("prepare")

    def cart_observation(self) -> CartObservationBatch:
        """返回单 worker×四店完整空 Cart 观测。

        输入参数：无。
        输出返回值：脱离真实浏览器的完整测试批次。
        """

        self.calls.append("capture")
        return CartObservationBatch(
            complete=True,
            workers=(
                ObservedCartWorker(
                    worker_id="private-reference-worker",
                    complete=True,
                    stores=tuple(
                        ObservedCartStore(
                            logical_store_id=f"store-{index}",
                            complete=True,
                            items=(),
                        )
                        for index in range(1, 5)
                    ),
                ),
            ),
        )

    def reference_validation_proof(self) -> WebMallCartReferenceCaptureProof:
        """返回已完成同 context 双 sweep 的脱敏证明。

        输入参数：无。
        输出返回值：固定两次四店 sweep 的成功证明。
        """

        self.calls.append("proof")
        sweep = ("store-1", "store-2", "store-3", "store-4")
        return WebMallCartReferenceCaptureProof(
            browser_context_continuity_verified=True,
            sweep_store_ids=(sweep, sweep),
            normalized_universe_match=True,
        )

    def close(self) -> None:
        """记录 owned 环境总是被关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("close")


def test_internal_entry_runs_bound_capture_and_returns_sanitized_receipt(
    tmp_path: Path,
) -> None:
    """验证显式入口按 start→prepare→capture→proof→close 形成 receipt。

    输入参数：无；注入无 I/O 环境 fake 和固定版本身份。
    输出返回值：无；生命周期完整且 receipt 不含 task gold/worker。
    """

    environment = _ReferenceEnvironment()
    raw_image = json.loads(
        (_REPO_ROOT / "environments/osworld/image-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    raw_image["extracted_image"]["status"] = "verified_reproducible_materialization"
    image_path = tmp_path / "verified-image-manifest.json"
    image_path.write_text(
        json.dumps(raw_image, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    verified_image = load_osworld_image_manifest(image_path)

    receipt = run_webmall_cart_reference_validation(
        environment=environment,
        task=_TASK,
        manifest=_MANIFEST,
        browser_image=verified_image,
        webmall_manifest_sha256="2" * 64,
        component_revision="3" * 64,
    )

    assert environment.calls == [
        "start",
        "prepare",
        "capture",
        "proof",
        "close",
    ]
    rendered = str(receipt.to_dict())
    assert receipt.outcome == "PASSED"
    assert "private-gold" not in rendered
    assert "private-reference-worker" not in rendered


def test_component_revision_uses_shared_receipt_neutral_identity() -> None:
    """验证 P0a 候选 receipt 与 P0b loader 共用无自引用身份。

    输入参数：无；构造协议有效但 source revision 值不同的
        ``RunVersionVector``。
    输出返回值：无；结果精确等于共享 Cart component
        identity，而非包含派生 runtime-support 的 source revision。
    """

    vector = RunVersionVector(
        source_revision="tree-sha256:" + "4" * 64,
        agent_code_revision="tree-sha256:" + "4" * 64,
        evaluator_revision="tree-sha256:" + "4" * 64,
        evaluation_protocol="paraguibench.webmall.cart.closed-world.v1",
        environment_protocol="webmall.browser.v1",
        environment_revision="manifest-sha256:" + "5" * 64,
    )

    expected = derive_webmall_cart_component_identity(
        _REPO_ROOT
    ).component_identity_sha256

    assert (
        build_webmall_cart_reference_component_revision(
            vector,
            repo_root=_REPO_ROOT,
        )
        == expected
    )
    assert expected != "4" * 64
    with pytest.raises(WebMallCartReferenceRuntimeError):
        build_webmall_cart_reference_component_revision(
            replace(vector, source_revision="tree-sha256:" + "z" * 64),
            repo_root=_REPO_ROOT,
        )
