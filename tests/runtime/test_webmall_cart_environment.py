"""WebMall Cart 专属环境生命周期与证据缓存测试。"""

from __future__ import annotations

import pytest

from paraguibench.integrations.webmall.cart_contracts import ObservedCartStore
from paraguibench.integrations.webmall.cart_evidence import (
    CART_EVIDENCE_SOURCE_PROTOCOL_ID,
)
from paraguibench.runtime.webmall_cart_environment import (
    WebMallCartTaskEnvironment,
    WebMallCartTaskEnvironmentError,
)


_CART_TASK = {
    "task_id": "Operation-OnlineShopping-AddToCart-001",
    "task_source": "WebMall",
    "task_type": "QA",
    "answer_type": "cart",
    "evaluator_path": "evaluators/cart_evaluator.py",
    "expected_urls": ("webmall://store-1/product/private-gold",),
}


class _RawEnvironment:
    """记录 Cart wrapper 透传的底层 GUI 生命周期。"""

    def __init__(self) -> None:
        """初始化公开 controller 与空调用记录。

        输入参数：无。
        输出返回值：无。
        """

        self.controller = object()
        self.guest_shared_dir = "/home/oai/share"
        self.calls: list[str] = []

    def start(self) -> None:
        """记录底层环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("start")

    def prepare(self, _task: dict[str, object]) -> None:
        """记录可信 Cart task 准备。

        输入参数：_task 为 wrapper 已验证的 canonical task。
        输出返回值：无。
        """

        self.calls.append("prepare")

    def close(self) -> None:
        """记录底层环境关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("close")


class _CartSource:
    """提供单 worker 四店完整空 Cart 的受控 source fake。"""

    evidence_protocol_id = CART_EVIDENCE_SOURCE_PROTOCOL_ID

    def __init__(self) -> None:
        """初始化 prepare/read/close 调用记录。

        输入参数：无。
        输出返回值：无。
        """

        self.calls: list[tuple[str, str]] = []

    def prepare(self, _controller: object) -> None:
        """记录 source 在 Agent 前完成浏览器准备。

        输入参数：_controller 为 raw environment 的 controller。
        输出返回值：无。
        """

        self.calls.append(("prepare", ""))

    def read_cart(
        self,
        worker_id: str,
        logical_store_id: str,
    ) -> ObservedCartStore:
        """返回当前 store 的完整空 Cart observation。

        输入参数：worker_id 与 logical_store_id 由 collector 固定传入。
        输出返回值：``complete=True`` 的空店快照。
        """

        self.calls.append((worker_id, logical_store_id))
        return ObservedCartStore(
            logical_store_id=logical_store_id,
            complete=True,
            items=(),
        )

    def close(self) -> None:
        """记录 source 生命周期关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append(("close", ""))


def test_cart_environment_prepares_before_agent_and_caches_one_observation() -> None:
    """验证 Cart 环境先准备 CDP，评价时仅冻结一次完整四店证据。

    输入参数：
        无；组合 raw GUI 环境与受控 Cart source fake。
    输出返回值：
        无；生命周期透传，重复 observation 返回同一不可变批次。
    """

    raw = _RawEnvironment()
    source = _CartSource()
    environment = WebMallCartTaskEnvironment(
        environment=raw,
        evidence_source=source,
        worker_id="worker-1",
    )

    environment.start()
    environment.prepare(_CART_TASK)
    first = environment.cart_observation()
    second = environment.cart_observation()
    environment.close()

    assert first is second
    assert first.complete is True
    assert raw.calls == ["start", "prepare", "close"]
    assert source.calls == [
        ("prepare", ""),
        ("worker-1", "store-1"),
        ("worker-1", "store-2"),
        ("worker-1", "store-3"),
        ("worker-1", "store-4"),
        ("close", ""),
    ]
    assert environment.controller is raw.controller
    assert environment.guest_shared_dir == "/home/oai/share"
    assert "worker-1" not in repr(environment)


def test_cart_environment_close_does_not_capture_unrequested_observation() -> None:
    """验证 close 不为未请求的评价偷偷补拍购物车终态。

    输入参数：
        无；环境完成 start/prepare 后直接关闭，不调用 cart_observation。
    输出返回值：
        无；source 只有 prepare/close，没有任何 worker×store 读取。
    """

    raw = _RawEnvironment()
    source = _CartSource()
    environment = WebMallCartTaskEnvironment(
        environment=raw,
        evidence_source=source,
        worker_id="worker-1",
    )

    environment.start()
    environment.prepare(_CART_TASK)
    environment.close()

    assert raw.calls == ["start", "prepare", "close"]
    assert source.calls == [("prepare", ""), ("close", "")]


def test_cart_environment_sanitizes_source_failure_without_partial_batch() -> None:
    """验证任一店读取异常只产生固定 evaluator error，不泄露原始值。

    输入参数：
        无；source 在首店抛出含 URL、worker 和 slug 的合成异常。
    输出返回值：
        无；环境错误无 cause、无敏感文本且仍可完成 owned cleanup。
    """

    private_value = "https://store-1.private.invalid/cart/worker-1/private-secret-slug"

    class _FailingCartSource(_CartSource):
        """在任何 store 读取时抛出含敏感值的系统边界异常。"""

        def read_cart(
            self,
            worker_id: str,
            logical_store_id: str,
        ) -> ObservedCartStore:
            """模拟 Cart reader 失败。

            输入参数：worker/store 仅用于模拟生产调用，故意不记录。
            输出返回值：不返回；始终抛含敏感文本的异常。
            """

            del worker_id, logical_store_id
            raise RuntimeError(private_value)

    raw = _RawEnvironment()
    source = _FailingCartSource()
    environment = WebMallCartTaskEnvironment(
        environment=raw,
        evidence_source=source,
        worker_id="worker-1",
    )
    environment.start()
    environment.prepare(_CART_TASK)

    with pytest.raises(WebMallCartTaskEnvironmentError) as captured:
        environment.cart_observation()

    rendered = f"{captured.value!s}|{captured.value!r}"
    assert str(captured.value) == "WEBMALL_CART_TASK_ENVIRONMENT_INVALID"
    assert captured.value.__cause__ is None
    assert private_value not in rendered
    assert "worker-1" not in rendered
    environment.close()
    assert raw.calls[-1] == "close"


def test_cart_environment_rejects_non_cart_task_before_prepare_side_effects() -> None:
    """验证专属 wrapper 不会被 URL 或订单任务误装配。

    输入参数：
        无；把可信任务的 ``answer_type`` 改为 string。
    输出返回值：
        无；raw/source prepare 均未执行，随后 close 仍清理已启动环境。
    """

    raw = _RawEnvironment()
    source = _CartSource()
    environment = WebMallCartTaskEnvironment(
        environment=raw,
        evidence_source=source,
        worker_id="worker-1",
    )
    wrong_task = dict(_CART_TASK)
    wrong_task["answer_type"] = "string"
    environment.start()

    with pytest.raises(WebMallCartTaskEnvironmentError):
        environment.prepare(wrong_task)

    assert raw.calls == ["start"]
    assert source.calls == []
    environment.close()
    assert raw.calls == ["start", "close"]
