"""WebMall 环境、订单证据窗口与底层 GUI 环境的生命周期测试。"""

from __future__ import annotations

from typing import Any

import pytest

from paraguibench.evaluation.webmall import CheckoutObservationBatch
from paraguibench.integrations.webmall import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
)
from paraguibench.integrations.webmall import WebMallURLRegistry
from paraguibench.runtime.webmall_environment import (
    WebMallTaskEnvironment,
    WebMallTaskEnvironmentError,
)


class _Controller:
    """记录 Agent 可调用操作并公开一个只读属性的合成 controller。"""

    def __init__(self, calls: list[str]) -> None:
        """绑定共享调用记录。

        输入参数：
            calls：用于断言 guard 与 GUI 副作用顺序的列表。
        输出返回值：
            无。
        """

        self._calls = calls
        self.read_only_label = "synthetic-controller"

    def click(self, target: str) -> str:
        """记录一次可产生 GUI 副作用的合成点击。

        输入参数：
            target：合成点击目标。
        输出返回值：
            稳定的合成操作结果。
        """

        self._calls.append(f"controller.click:{target}")
        return "clicked"


class _BaseEnvironment:
    """记录底层 GUI 环境生命周期的合成实现。"""

    def __init__(
        self,
        calls: list[str],
        *,
        fail_prepare: bool = False,
        fail_close: bool = False,
    ) -> None:
        """绑定共享调用记录和失败注入开关。

        输入参数：
            calls：测试断言顺序的可变列表。
            fail_prepare/fail_close：对应阶段是否抛合成异常。
        输出返回值：
            无。
        """

        self.calls = calls
        self.controller = _Controller(calls)
        self.guest_shared_dir = "/home/oai/share"
        self._fail_prepare = fail_prepare
        self._fail_close = fail_close

    def start(self) -> None:
        """记录底层环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("base.start")

    def prepare(self, task: dict[str, Any]) -> None:
        """记录可信任务准备并可注入失败。

        输入参数：
            task：AttemptRunner 提供的 canonical task。
        输出返回值：
            无。
        """

        assert task["task_id"] == "webmall-task"
        self.calls.append("base.prepare")
        if self._fail_prepare:
            raise RuntimeError("synthetic base prepare failure")

    def close(self) -> None:
        """记录底层环境关闭并可注入失败。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("base.close")
        if self._fail_close:
            raise RuntimeError("synthetic base close failure")


class _EvidenceSession:
    """记录 acquire/baseline/final/release 顺序的合成证据 session。"""

    def __init__(
        self,
        calls: list[str],
        *,
        fail_final: bool = False,
        fail_release: bool = False,
    ) -> None:
        """保存共享记录、空 observation 与失败注入开关。

        输入参数：
            calls：生命周期顺序记录。
            fail_final/fail_release：终态采集或释放是否失败。
        输出返回值：
            无。
        """

        self.calls = calls
        self._fail_final = fail_final
        self._fail_release = fail_release
        self.baseline_captured = False
        self.capture_attempted = False
        self.observation: CheckoutObservationBatch | None = None
        self.allow_controller_calls = True

    @property
    def logical_store_ids(self) -> tuple[str, ...]:
        """返回合成 evidence session 固定扫描的四店 universe。

        输入参数：无。
        输出返回值：store-1 至 store-4 的有序元组。
        """

        return WEBMALL_LOGICAL_STORE_IDS

    def assert_held(self) -> None:
        """记录 controller 操作前的权威租约复核/续期。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("evidence.assert_held")
        if not self.allow_controller_calls:
            raise RuntimeError("synthetic stale fencing ownership")

    def acquire(self) -> None:
        """记录全局 lease 获取。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("evidence.acquire")

    def capture_baseline(self) -> None:
        """记录四店 baseline 捕获。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append("evidence.baseline")
        self.baseline_captured = True

    def capture_final(self) -> CheckoutObservationBatch:
        """记录并缓存 final observation，或注入特权读取失败。

        输入参数：无。
        输出返回值：空但完整的 checkout observation。
        """

        self.calls.append("evidence.final")
        self.capture_attempted = True
        if self._fail_final:
            raise RuntimeError("synthetic final evidence failure")
        self.observation = CheckoutObservationBatch(
            store_universe_id=WEBMALL_STORE_UNIVERSE_ID,
            scanned_store_ids=WEBMALL_LOGICAL_STORE_IDS,
            complete=True,
            orders=(),
        )
        return self.observation

    def close(self) -> CheckoutObservationBatch | None:
        """记录全局 lease 释放并可注入失败。

        输入参数：无。
        输出返回值：已经缓存的 observation。
        """

        self.calls.append("evidence.release")
        if self._fail_release:
            raise RuntimeError("synthetic lease release failure")
        return self.observation


def _registry() -> WebMallURLRegistry:
    """创建与 evidence session 完全一致的四店测试 registry。

    输入参数：无。
    输出返回值：测试专用 runtime origin 注册表。
    """

    return WebMallURLRegistry(
        {f"store-{index}": f"https://shop-{index}.invalid" for index in range(1, 5)}
    )


def test_environment_rejects_registry_outside_evidence_store_universe() -> None:
    """验证 registry、evidence 与固定四店 scope 不一致时无法构造环境。

    输入参数：
        无；registry 故意只配置一个 store，evidence 仍声明完整四店。
    输出返回值：
        无；在启动底层环境或获取租约前以类型错误失败关闭。
    """

    calls: list[str] = []

    with pytest.raises(TypeError, match="universe"):
        WebMallTaskEnvironment(
            environment=_BaseEnvironment(calls),
            evidence_session=_EvidenceSession(calls),
            registry=WebMallURLRegistry({"store-1": "https://partial.invalid"}),
        )

    assert calls == []


def test_prepare_holds_global_lease_before_base_prepare_and_baseline() -> None:
    """验证 reset/prepare 与 baseline 均位于同一全局租约窗口内。

    输入参数：
        无；执行 start、prepare、显式 evaluator 取证和 close。
    输出返回值：
        无；final 后先关闭底层环境，最后释放共享 backend 租约。
    """

    calls: list[str] = []
    base = _BaseEnvironment(calls)
    evidence = _EvidenceSession(calls)
    environment = WebMallTaskEnvironment(
        environment=base,
        evidence_session=evidence,
        registry=_registry(),
    )

    environment.start()
    environment.prepare({"task_id": "webmall-task"})
    observation = environment.checkout_observation()
    environment.close()

    assert observation.complete is True
    assert environment.controller is not base.controller
    assert environment.guest_shared_dir == "/home/oai/share"
    assert calls == [
        "base.start",
        "evidence.acquire",
        "base.prepare",
        "evidence.baseline",
        "evidence.final",
        "base.close",
        "evidence.release",
    ]


def test_controller_proxy_guards_every_callable_and_keeps_attributes_read_only() -> (
    None
):
    """验证 Agent 只能通过每次操作前续期的只读 controller proxy。

    输入参数：
        无；构造合成 GUI controller 和 evidence session。
    输出返回值：
        无；可调用操作前先 assert-held，非可调用属性可读但
        不可通过 proxy 覆盖，且不暴露 raw controller。
    """

    calls: list[str] = []
    base = _BaseEnvironment(calls)
    environment = WebMallTaskEnvironment(
        environment=base,
        evidence_session=_EvidenceSession(calls),
        registry=_registry(),
    )
    environment.start()
    environment.prepare({"task_id": "webmall-task"})

    controller = environment.controller
    assert controller is not base.controller
    assert controller.read_only_label == "synthetic-controller"
    assert not hasattr(controller, "raw_controller")
    with pytest.raises(AttributeError):
        getattr(controller, "_controller")
    with pytest.raises((AttributeError, WebMallTaskEnvironmentError)):
        controller.read_only_label = "mutated"
    assert controller.click("checkout") == "clicked"

    assert calls[-2:] == [
        "evidence.assert_held",
        "controller.click:checkout",
    ]
    environment.close()


def test_captured_controller_method_rechecks_ownership_when_stale_worker_resumes() -> (
    None
):
    """验证旧 worker 在暂停后恢复时不能使用预先捕获的方法。

    输入参数：
        无；在取得 method handle 后注入 fencing ownership 丢失。
    输出返回值：
        无；方法真正调用时重新 assert-held 并在失败后
        fail closed，底层 controller 不产生任何 GUI 副作用。
    """

    calls: list[str] = []
    evidence = _EvidenceSession(calls)
    environment = WebMallTaskEnvironment(
        environment=_BaseEnvironment(calls),
        evidence_session=evidence,
        registry=_registry(),
    )
    environment.start()
    environment.prepare({"task_id": "webmall-task"})
    stale_operation = environment.controller.click
    evidence.allow_controller_calls = False

    with pytest.raises(RuntimeError, match="stale fencing ownership"):
        stale_operation("stale-worker")

    assert calls[-1] == "evidence.assert_held"
    assert "controller.click:stale-worker" not in calls
    environment.close()


def test_close_captures_final_on_agent_failure_before_cleanup_and_release() -> None:
    """验证 Agent 阶段异常时环境 close 仍补拍副作用闭包。

    输入参数：
        无；prepare 后不调用 evaluator，直接模拟 AttemptRunner finally。
    输出返回值：
        无；final→底层 close→lease release 的顺序保持不变。
    """

    calls: list[str] = []
    environment = WebMallTaskEnvironment(
        environment=_BaseEnvironment(calls),
        evidence_session=_EvidenceSession(calls),
        registry=_registry(),
    )
    environment.start()
    environment.prepare({"task_id": "webmall-task"})

    environment.close()

    assert calls[-3:] == [
        "evidence.final",
        "base.close",
        "evidence.release",
    ]


def test_final_failure_still_closes_base_and_releases_lease() -> None:
    """验证终态特权扫描失败不会跳过底层清理或租约释放。

    输入参数：
        无；在 close 的补拍阶段注入 final evidence 异常。
    输出返回值：
        无；异常向 runtime 传播，但 base close 与 release 都已执行。
    """

    calls: list[str] = []
    environment = WebMallTaskEnvironment(
        environment=_BaseEnvironment(calls),
        evidence_session=_EvidenceSession(calls, fail_final=True),
        registry=_registry(),
    )
    environment.start()
    environment.prepare({"task_id": "webmall-task"})

    with pytest.raises(RuntimeError, match="final evidence failure"):
        environment.close()

    assert calls[-3:] == [
        "evidence.final",
        "base.close",
        "evidence.release",
    ]


def test_prepare_failure_releases_lease_and_allows_runner_to_close_base() -> None:
    """验证底层 prepare 异常不会遗留共享 WebMall 全局租约。

    输入参数：
        无；在获取 lease 后注入 base prepare 失败。
    输出返回值：
        无；prepare 先释放 lease，随后 close 仍清理已启动的底层环境。
    """

    calls: list[str] = []
    environment = WebMallTaskEnvironment(
        environment=_BaseEnvironment(calls, fail_prepare=True),
        evidence_session=_EvidenceSession(calls),
        registry=_registry(),
    )
    environment.start()

    with pytest.raises(RuntimeError, match="base prepare failure"):
        environment.prepare({"task_id": "webmall-task"})
    environment.close()

    assert calls == [
        "base.start",
        "evidence.acquire",
        "base.prepare",
        "evidence.release",
        "base.close",
    ]
