"""单 VM 顺序 ParaGUI 环境租约边界测试。"""

from __future__ import annotations

from typing import Any

import pytest

from paraguibench.runtime.single_vm_lease import (
    SingleVMEnvironmentLeaseAdapter,
    SingleVMEnvironmentLeaseError,
)


class _Environment:
    """记录生命周期调用的单桌面环境替身。"""

    def __init__(self) -> None:
        """初始化 controller 和生命周期记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.controller = object()
        self.calls: list[tuple[str, Any]] = []

    def start(self) -> None:
        """记录环境启动。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append(("start", None))

    def prepare(self, task: dict[str, Any]) -> None:
        """记录任务准备。

        输入参数：
            task：AttemptRunner 传入的可信任务。
        输出返回值：
            无。
        """

        self.calls.append(("prepare", task["task_id"]))

    def close(self) -> None:
        """记录环境关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.calls.append(("close", None))

    def osworld_state_observations(
        self,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """返回 evaluator 请求的合成 OSWorld 状态证据。

        输入参数：
            protocol_id：runtime evaluator 固定的状态协议。
        输出返回值：
            含协议身份的单元素 observation tuple。
        """

        self.calls.append(("state", protocol_id))
        return (protocol_id,)

    def osworld_bookmark_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """返回 Bookmark evaluator 请求的冻结单 VM 证据。

        输入参数：
            task_id：当前 canonical bookmark task ID。
            protocol_id：固定 Chrome Bookmarks 协议。
        输出返回值：
            便于断言身份完整透传的单元素 tuple。
        """

        self.calls.append(("bookmark", (task_id, protocol_id)))
        return ((task_id, protocol_id),)

    def osworld_artifact_state_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """返回 evaluator 请求的合成 artifact-state 证据。

        输入参数：
            task_id：当前已准备 canonical task ID。
            protocol_id：固定 artifact-state 协议。
        输出返回值：
            含两个稳定身份的单元素 observation tuple。
        """

        identity = f"{task_id}:{protocol_id}"
        self.calls.append(("artifact", identity))
        return (identity,)

    def operation_artifact_snapshot(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """返回 Operation evaluator 请求的同一快照对象。

        输入参数：
            task_id：当前已准备 canonical Operation task ID。
            protocol_id：固定 Operation eval-rules 协议。
        输出返回值：
            用于断言 adapter 不复制、不拆分的不透明对象。
        """

        snapshot = object()
        self.calls.append(("operation", (task_id, protocol_id, snapshot)))
        return snapshot

    def operation_word_text_baseline(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """返回 Word-009/010 evaluator 请求的同一 typed DTO 替身。

        输入参数：
            task_id/protocol_id：当前 Word 任务与 Operation 协议。
        输出返回值：
            用于断言 adapter 不复制、不从 post 重建的不透明对象。
        """

        baseline = object()
        self.calls.append(("word_text", (task_id, protocol_id, baseline)))
        return baseline

    def operation_word_abbreviation_baseline(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """返回 Word-012 evaluator 请求的同一 typed DTO 替身。

        输入参数：
            task_id/protocol_id：当前 Word-012 任务与 Operation 协议。
        输出返回值：
            用于断言 adapter 不复制、不从 post 重建的不透明对象。
        """

        baseline = object()
        self.calls.append(("word_abbreviation", (task_id, protocol_id, baseline)))
        return baseline

    def pipeline_implicit_observation(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """返回 pipeline-implicit evaluator 请求的同一 typed observation。

        输入参数：
            task_id：当前已准备的四任务 canonical ID。
            protocol_id：该任务唯一版本化评价协议。
        输出返回值：
            用于断言 wrapper 不复制或解析的不透明 observation。
        """

        observation = object()
        self.calls.append(("pipeline_implicit", (task_id, protocol_id, observation)))
        return observation

    def checkout_observation(self) -> object:
        """返回 WebMall evaluator 请求的合成订单观测。

        输入参数：
            无。
        输出返回值：
            固定的不透明 observation 对象。
        """

        observation = object()
        self.calls.append(("webmall_checkout", observation))
        return observation

    def cart_observation(self) -> object:
        """返回 WebMall Cart evaluator 请求的冻结终态。

        输入参数：无。
        输出返回值：固定的不透明 Cart observation 对象。
        """

        observation = object()
        self.calls.append(("webmall_cart", observation))
        return observation

    def canonicalize_reported_product_urls(
        self,
        final_output: str,
    ) -> tuple[str, ...]:
        """返回 WebMall EndToEnd evaluator 的 logical URL 报告。

        输入参数：
            final_output：尚未持久化的 Agent 最终文本。
        输出返回值：
            仅供断言透传的单元组。
        """

        self.calls.append(("webmall_report", final_output))
        return ("webmall://store-1/product/example",)

    def webmall_url_registry(self) -> object:
        """返回 URL evaluator 请求的只读 registry 替身。

        输入参数：
            无。
        输出返回值：
            可用对象身份断言原样透传的不透明对象。
        """

        registry = object()
        self.calls.append(("webmall_registry", registry))
        return registry


def test_single_vm_adapter_delegates_lifecycle_and_leases_in_sequence() -> None:
    """验证 AttemptRunner 生命周期与两个顺序 subtask 共用同一桌面。

    输入参数：
        无；使用一个合成单 VM 环境。
    输出返回值：
        无；生命周期只调用一次，两个 lease 都产生原环境。
    """

    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    task = {"task_id": "synthetic"}

    adapter.start()
    adapter.prepare(task)
    with adapter.lease("inspect-diagram") as first:
        assert first is environment
    with adapter.lease("compare-pdfs") as second:
        assert second is environment
    adapter.close()

    assert adapter.controller is environment.controller
    assert environment.calls == [
        ("start", None),
        ("prepare", "synthetic"),
        ("close", None),
    ]


def test_single_vm_adapter_closes_after_partial_underlying_start_failure() -> None:
    """验证底层启动部分成功后异常仍会清理所有资源。

    输入参数：
        无；合成环境在记录 start 后模拟 guest ready 超时。
    输出返回值：
        无；调用方 finally 中的 adapter.close 必须透传到底层一次。
    """

    class PartiallyStartedEnvironment(_Environment):
        """模拟已创建容器但 guest 就绪失败的环境。"""

        def start(self) -> None:
            """记录部分启动后抛出稳定的合成异常。

            输入参数：
                无。
            输出返回值：
                不返回；始终抛出 ``RuntimeError``。
            """

            self.calls.append(("start", None))
            raise RuntimeError("synthetic ready timeout")

    environment = PartiallyStartedEnvironment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)

    with pytest.raises(RuntimeError, match="synthetic ready timeout"):
        adapter.start()
    adapter.close()

    assert environment.calls == [("start", None), ("close", None)]


def test_single_vm_adapter_retries_transient_underlying_close_failure() -> None:
    """验证 adapter 不会在底层 close 失败后丢失重试入口。

    输入参数：
        无；合成底层环境首次 close 抛错、第二次成功。
    输出返回值：
        无；首次异常原样传播，但 adapter 仍保留清理 pending，
        第二次 close 必须再次调用同一底层环境。
    """

    class RetryCloseEnvironment(_Environment):
        """模拟可恢复的底层清理失败。"""

        def __init__(self) -> None:
            """初始化基础生命周期记录和 close 计数。

            输入参数：无。
            输出返回值：无。
            """

            super().__init__()
            self.close_calls = 0

        def close(self) -> None:
            """首次关闭抛错，第二次正常返回。

            输入参数：无。
            输出返回值：无；首次调用不返回。
            """

            self.close_calls += 1
            self.calls.append(("close", None))
            if self.close_calls == 1:
                raise RuntimeError("synthetic close failure")

    environment = RetryCloseEnvironment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    adapter.start()
    adapter.prepare({"task_id": "synthetic"})

    with pytest.raises(RuntimeError, match="close failure"):
        adapter.close()
    adapter.close()

    assert environment.close_calls == 2
    assert environment.calls == [
        ("start", None),
        ("prepare", "synthetic"),
        ("close", None),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_state_evidence_after_prepare() -> None:
    """验证 ParaGUI 包装层不会遮蔽底层状态 evaluator 接口。

    输入参数：
        无；使用实现状态 evidence seam 的合成单 VM 环境。
    输出返回值：
        无；adapter 在已准备生命周期内原样返回底层冻结 observation。
    """

    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    protocol_id = "paraguibench.osworld.chrome-profile-name.v1"
    adapter.start()
    adapter.prepare({"task_id": "synthetic"})

    observations = adapter.osworld_state_observations(protocol_id)
    adapter.close()

    assert observations == (protocol_id,)
    assert environment.calls == [
        ("start", None),
        ("prepare", "synthetic"),
        ("state", protocol_id),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_bookmark_evidence_after_prepare() -> None:
    """验证 ParaGUI 包装层不会遮蔽 Bookmark evaluator 接口。

    输入参数：
        无；使用实现 bookmark evidence seam 的合成单 VM 环境。
    输出返回值：
        无；task/protocol 身份与冻结 observation 原样透传。
    """

    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    task_id = "Operation-WebOperate-WebNavigate-008"
    protocol_id = "paraguibench.osworld.chrome-bookmarks.v1"
    adapter.start()
    adapter.prepare({"task_id": task_id})

    observations = adapter.osworld_bookmark_observations(
        task_id,
        protocol_id,
    )
    adapter.close()

    assert observations == ((task_id, protocol_id),)
    assert environment.calls == [
        ("start", None),
        ("prepare", task_id),
        ("bookmark", (task_id, protocol_id)),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_artifact_evidence_after_prepare() -> None:
    """验证 ParaGUI 包装层保留 artifact-state evaluator 接口。

    输入参数：
        无；使用实现 artifact evidence seam 的合成单 VM 环境。
    输出返回值：
        无；task/protocol 身份完整透传，未启动第二套环境或跨 VM 拼接。
    """

    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    task_id = "Operation-FileOperate-BatchOperation-001"
    protocol_id = "paraguibench.osworld.artifact-state.v1"
    adapter.start()
    adapter.prepare({"task_id": task_id})

    observations = adapter.osworld_artifact_state_observations(
        task_id,
        protocol_id,
    )
    adapter.close()

    identity = f"{task_id}:{protocol_id}"
    assert observations == (identity,)
    assert environment.calls == [
        ("start", None),
        ("prepare", task_id),
        ("artifact", identity),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_operation_snapshot_after_prepare() -> None:
    """验证 ParaGUI 包装层不遮蔽 Operation artifact 快照接口。

    输入参数：
        无；使用实现 Operation 快照 seam 的合成单 VM 环境。
    输出返回值：
        无；task/protocol 身份完整透传，快照对象不被复制，
        且未 prepare 时仍 fail closed。
    """

    task_id = "Operation-FileOperate-CombinationDocs-005"
    protocol_id = "paraguibench.operation.eval-rules.v1"
    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    with pytest.raises(SingleVMEnvironmentLeaseError, match="尚未准备"):
        adapter.operation_artifact_snapshot(task_id, protocol_id)
    adapter.start()
    adapter.prepare({"task_id": task_id})

    snapshot = adapter.operation_artifact_snapshot(task_id, protocol_id)
    adapter.close()

    assert snapshot is environment.calls[2][1][2]
    assert environment.calls == [
        ("start", None),
        ("prepare", task_id),
        ("operation", (task_id, protocol_id, snapshot)),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_operation_word_text_baseline() -> None:
    """验证单 VM 包装层原样透传 prepare 前 typed baseline。

    输入参数：
        无；使用实现 Word typed seam 的合成底层环境。
    输出返回值：
        无；未 prepare 时拒绝，prepare 后 task/protocol 与对象
        identity 完整透传。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-009"
    protocol_id = "paraguibench.operation.eval-rules.v1"
    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    with pytest.raises(SingleVMEnvironmentLeaseError, match="尚未准备"):
        adapter.operation_word_text_baseline(task_id, protocol_id)
    adapter.start()
    adapter.prepare({"task_id": task_id})

    baseline = adapter.operation_word_text_baseline(task_id, protocol_id)
    adapter.close()

    assert baseline is environment.calls[2][1][2]
    assert environment.calls == [
        ("start", None),
        ("prepare", task_id),
        ("word_text", (task_id, protocol_id, baseline)),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_operation_word_abbreviation_baseline() -> None:
    """验证单 VM 包装层原样透传 Word-012 语义 baseline。

    输入参数：
        无；使用实现 abbreviation typed seam 的合成底层环境。
    输出返回值：
        无；未 prepare 时拒绝，prepare 后 task/protocol 与对象
        identity 必须完整透传。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-012"
    protocol_id = "paraguibench.operation.eval-rules.v1"
    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    with pytest.raises(SingleVMEnvironmentLeaseError, match="尚未准备"):
        adapter.operation_word_abbreviation_baseline(task_id, protocol_id)
    adapter.start()
    adapter.prepare({"task_id": task_id})

    baseline = adapter.operation_word_abbreviation_baseline(task_id, protocol_id)
    adapter.close()

    assert baseline is environment.calls[2][1][2]
    assert environment.calls == [
        ("start", None),
        ("prepare", task_id),
        ("word_abbreviation", (task_id, protocol_id, baseline)),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_pipeline_implicit_observation() -> None:
    """验证 ParaGUI 单 VM 包装层原样透传四任务 typed observation seam。

    输入参数：
        无；使用实现 pipeline-implicit seam 的合成底层环境。
    输出返回值：
        无；task/protocol 与对象身份不变，未准备时固定失败。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-003"
    protocol_id = "paraguibench.operation.image-classification.sha256.v1"
    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    with pytest.raises(SingleVMEnvironmentLeaseError, match="尚未准备"):
        adapter.pipeline_implicit_observation(task_id, protocol_id)
    adapter.start()
    adapter.prepare({"task_id": task_id})

    observation = adapter.pipeline_implicit_observation(
        task_id,
        protocol_id,
    )
    adapter.close()

    assert observation is environment.calls[2][1][2]
    assert environment.calls == [
        ("start", None),
        ("prepare", task_id),
        (
            "pipeline_implicit",
            (task_id, protocol_id, observation),
        ),
        ("close", None),
    ]


def test_single_vm_adapter_forwards_webmall_evaluator_seams() -> None:
    """验证 ParaGUI 外层单 VM 租约不遮蔽三类 WebMall evaluator seam。

    输入参数：
        无；使用同时实现 WebMall 两个 evaluator seam 的合成环境。
    输出返回值：
        无；checkout/cart observation 与 logical report 都在已准备生命周期
        中原样透传，不创建第二个 environment。
    """

    environment = _Environment()
    adapter = SingleVMEnvironmentLeaseAdapter(environment)
    adapter.start()
    adapter.prepare({"task_id": "webmall-task"})

    observation = adapter.checkout_observation()
    cart_observation = adapter.cart_observation()
    report = adapter.canonicalize_reported_product_urls("private-model-final-output")
    registry = adapter.webmall_url_registry()
    adapter.close()

    assert observation is environment.calls[2][1]
    assert cart_observation is environment.calls[3][1]
    assert report == ("webmall://store-1/product/example",)
    assert environment.calls == [
        ("start", None),
        ("prepare", "webmall-task"),
        ("webmall_checkout", observation),
        ("webmall_cart", cart_observation),
        ("webmall_report", "private-model-final-output"),
        ("webmall_registry", registry),
        ("close", None),
    ]
