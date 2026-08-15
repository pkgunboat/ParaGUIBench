"""为实验性单 VM 顺序 ParaGUI 提供互斥环境租约。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import re
from threading import Lock
from typing import Any

_SUBTASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class SingleVMEnvironmentLeaseError(RuntimeError):
    """表示单 VM 生命周期或互斥租约被违反。"""


class SingleVMEnvironmentLeaseAdapter:
    """透传一个任务环境的生命周期，并提供非阻塞独占租约。"""

    def __init__(self, environment: Any) -> None:
        """包装一个尚未启动的单 VM 任务环境。

        输入参数：
            environment：必须实现 ``start``、``prepare``、``close``
                并公开 ``controller`` 的原始任务环境。
        输出返回值：
            无；构造阶段不启动 VM 或创建线程。
        异常：
            TypeError：环境缺少必需生命周期接口。
        """

        for method_name in ("start", "prepare", "close"):
            if not callable(getattr(environment, method_name, None)):
                raise TypeError(f"environment 缺少 {method_name}")
        if not hasattr(environment, "controller"):
            raise TypeError("environment 缺少 controller")
        self._environment = environment
        self._lease_lock = Lock()
        self._started = False
        self._prepared = False

    @property
    def controller(self) -> Any:
        """返回 evaluator 或 runtime 可见的原始 controller。

        输入参数：
            无。
        输出返回值：
            被包装环境的 ``controller`` 对象。
        """

        return self._environment.controller

    @property
    def guest_shared_dir(self) -> str | None:
        """返回原环境已动态推导的 guest shared 目录。

        输入参数：
            无。
        输出返回值：
            原环境公开该字段时返回其值，否则返回 ``None``。
        """

        value = getattr(self._environment, "guest_shared_dir", None)
        return value if isinstance(value, str) else None

    def start(self) -> None:
        """启动唯一被包装环境。

        输入参数：
            无。
        输出返回值：
            无；成功后允许 ``prepare``。
        异常：
            SingleVMEnvironmentLeaseError：adapter 已启动。
        """

        if self._started:
            raise SingleVMEnvironmentLeaseError("单 VM adapter 已启动")
        # 底层 OSWorld 可能在容器已创建后才因 guest ready
        # 超时抛错。先进入需清理状态，使 AttemptRunner finally
        # 总能通过 adapter.close 回收底层已拥有的资源。
        self._started = True
        self._environment.start()

    def prepare(self, task: Mapping[str, Any]) -> None:
        """在唯一 VM 中准备一次可信 benchmark 任务。

        输入参数：
            task：AttemptRunner 传入的 canonical task Mapping。
        输出返回值：
            无；成功后允许 worker 租约。
        异常：
            SingleVMEnvironmentLeaseError：环境未启动或已准备。
        """

        if not self._started:
            raise SingleVMEnvironmentLeaseError("单 VM 未启动")
        if self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 已准备")
        self._environment.prepare(task)
        self._prepared = True

    @contextmanager
    def lease(self, subtask_id: str) -> Iterator[Any]:
        """为一个 subtask 非阻塞租用已准备的唯一 VM。

        输入参数：
            subtask_id：当前稳定节点身份，只用于租约门禁。
        输出返回值：
            context manager yield 被包装的原始桌面环境。
        异常：
            SingleVMEnvironmentLeaseError：身份无效、环境未准备，
                或另一 worker 正持有该 VM。
        """

        if (
            not isinstance(subtask_id, str)
            or _SUBTASK_ID_PATTERN.fullmatch(subtask_id) is None
        ):
            raise SingleVMEnvironmentLeaseError("subtask_id 无效")
        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        if not self._lease_lock.acquire(blocking=False):
            raise SingleVMEnvironmentLeaseError("单 VM 已被独占租用")
        try:
            yield self._environment
        finally:
            self._lease_lock.release()

    def osworld_state_observations(
        self,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """透传底层环境已经冻结的 OSWorld 状态 evidence。

        输入参数：
            protocol_id：runtime evaluator 固定的 profile/active-tab 协议。
        输出返回值：
            底层单 VM 环境返回的 observation tuple。
        异常：
            SingleVMEnvironmentLeaseError：环境未准备或底层缺少 evidence
                接口；底层捕获异常原样传播给 AttemptRunner。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "osworld_state_observations",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 state evidence 接口"
            )
        return reader(protocol_id)

    def osworld_bookmark_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """透传底层环境已经冻结的 Chrome Bookmarks evidence。

        输入参数：
            task_id：当前 canonical bookmark task ID。
            protocol_id：runtime evaluator 固定的 Chrome Bookmarks 协议。
        输出返回值：
            底层单 VM环境返回的 observation tuple；adapter 不合并记录，
            ``union_complete`` 仅由纯 evaluator 执行。
        异常：
            SingleVMEnvironmentLeaseError：环境未准备或底层缺少 bookmark
                evidence 接口；底层捕获异常原样传播给 AttemptRunner。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "osworld_bookmark_observations",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 bookmark evidence 接口"
            )
        return reader(task_id, protocol_id)

    def osworld_artifact_state_observations(
        self,
        task_id: str,
        protocol_id: str,
    ) -> tuple[object, ...]:
        """透传底层环境已经冻结的单 VM artifact-state evidence。

        输入参数：
            task_id：当前 canonical artifact task ID。
            protocol_id：runtime evaluator 固定的 artifact-state 协议。
        输出返回值：
            底层环境返回的 observation tuple；adapter 不拆分或拼接字段。
        异常：
            SingleVMEnvironmentLeaseError：环境未准备或底层缺少 evidence
                接口；底层捕获异常原样传播给 AttemptRunner。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "osworld_artifact_state_observations",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 artifact evidence 接口"
            )
        return reader(task_id, protocol_id)

    def operation_artifact_snapshot(
        self,
        task_id: str,
        protocol_id: str,
    ) -> Any:
        """透传底层环境冻结的 Operation artifact 快照。

        输入参数：
            task_id：当前 canonical Operation task ID。
            protocol_id：runtime evaluator 固定的 Operation 协议。
        输出返回值：
            底层单 VM 环境返回的同一 owned 快照对象；adapter
            不复制 artifact，不解析文件，也不取得清理所有权。
        异常：
            SingleVMEnvironmentLeaseError：环境未 prepare 或底层缺少
                Operation 快照接口；底层捕获异常原样传播。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "operation_artifact_snapshot",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 Operation artifact 快照接口"
            )
        return reader(task_id, protocol_id)

    def operation_word_text_baseline(
        self,
        task_id: str,
        protocol_id: str,
    ) -> Any:
        """透传底层环境 prepare 前冻结的 Word typed baseline。

        输入参数：
            task_id：Word-009/010 canonical ID；protocol_id：固定
            Operation 协议 ID。
        输出返回值：
            底层环境返回的同一 evaluator-only typed DTO；adapter
            不读取 guest/post，不复制文本或摘要。
        异常：
            SingleVMEnvironmentLeaseError：未 prepare 或底层缺少窄接口；
            底层身份/生命周期错误原样传播。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "operation_word_text_baseline",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 Operation Word typed baseline 接口"
            )
        return reader(task_id, protocol_id)

    def operation_word_abbreviation_baseline(
        self,
        task_id: str,
        protocol_id: str,
    ) -> Any:
        """透传底层环境 prepare 前冻结的 Word-012 语义 baseline。

        输入参数：
            task_id：Word-012 canonical ID；protocol_id：固定 Operation
            协议 ID。
        输出返回值：
            底层环境返回的同一 evaluator-only typed DTO；adapter
            不读 guest/post，不复制文本或语义合同。
        异常：
            SingleVMEnvironmentLeaseError：未 prepare 或底层缺少窄接口；
            底层身份/生命周期错误原样传播。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "operation_word_abbreviation_baseline",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 Operation Word abbreviation typed baseline 接口"
            )
        return reader(task_id, protocol_id)

    def pipeline_implicit_observation(
        self,
        task_id: str,
        protocol_id: str,
    ) -> Any:
        """透传底层环境冻结的 pipeline-implicit typed observation。

        输入参数：
            task_id：当前四任务 canonical ID。
            protocol_id：该任务唯一版本化评价协议。
        输出返回值：
            底层环境返回的同一 evaluator-only observation；adapter
            不复制原始 bundle、不解析 artifact，也不读取 Agent 文本。
        异常：
            SingleVMEnvironmentLeaseError：环境未 prepare 或底层缺少
                pipeline-implicit seam；底层捕获异常原样传播。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "pipeline_implicit_observation",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 pipeline-implicit evidence 接口"
            )
        return reader(task_id, protocol_id)

    def checkout_observation(self) -> Any:
        """透传底层 WebMall 环境冻结的 checkout observation。

        输入参数：
            无。
        输出返回值：
            底层 ``WebMallTaskEnvironment`` 返回的完整四店
            baseline/final 差分批次。
        异常：
            SingleVMEnvironmentLeaseError：环境未准备或底层缺少
                WebMall checkout evidence seam；底层取证异常原样传播。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(self._environment, "checkout_observation", None)
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 WebMall checkout evidence 接口"
            )
        return reader()

    def cart_observation(self) -> Any:
        """透传底层 WebMall Cart 环境冻结的完整终态批次。

        输入参数：无。
        输出返回值：底层 ``WebMallCartTaskEnvironment`` 返回的同一
            单 worker×固定四店不可变 observation；adapter 不复制、
            不拆分，也不接触 Agent final output。
        异常：
            SingleVMEnvironmentLeaseError：环境未准备或底层缺少 Cart
                evidence seam；底层取证异常原样传播给 AttemptRunner。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(self._environment, "cart_observation", None)
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 WebMall cart evidence 接口"
            )
        return reader()

    def canonicalize_reported_product_urls(
        self,
        final_output: str,
    ) -> tuple[str, ...]:
        """透传 WebMall EndToEnd 报告的 runtime-to-logical URL 解析。

        输入参数：
            final_output：Agent 最终文本，只在内存中交给底层严格
                URL parser，本 adapter 不记录或复制到 RunStore。
        输出返回值：
            底层环境返回的 logical product URL 有序多集。
        异常：
            SingleVMEnvironmentLeaseError：环境未准备或底层缺少
                WebMall report seam；底层解析异常原样传播。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(
            self._environment,
            "canonicalize_reported_product_urls",
            None,
        )
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 WebMall report 接口"
            )
        result = reader(final_output)
        if not isinstance(result, tuple) or any(
            not isinstance(value, str) for value in result
        ):
            raise SingleVMEnvironmentLeaseError("底层 WebMall report 返回值无效")
        return result

    def webmall_url_registry(self) -> Any:
        """透传底层 WebMall URL 环境的只读 registry seam。

        输入参数：
            无。
        输出返回值：
            底层 ``WebMallURLTaskEnvironment`` 固定的可信
            runtime/logical URL registry；本 adapter 不复制或持久化
            其中的部署 origin。
        异常：
            SingleVMEnvironmentLeaseError：环境未准备或底层缺少
                URL registry 接口；底层异常原样传播。
        """

        if not self._prepared:
            raise SingleVMEnvironmentLeaseError("单 VM 尚未准备")
        reader = getattr(self._environment, "webmall_url_registry", None)
        if not callable(reader):
            raise SingleVMEnvironmentLeaseError(
                "底层单 VM 环境缺少 WebMall URL registry 接口"
            )
        return reader()

    def close(self) -> None:
        """幂等关闭原环境并清理 adapter 生命周期标记。

        输入参数：
            无。
        输出返回值：
            无；未启动时不调用原环境。
        异常：
            SingleVMEnvironmentLeaseError：仍有 worker 持有租约。
        """

        if self._lease_lock.locked():
            raise SingleVMEnvironmentLeaseError("单 VM 租约尚未归还")
        if not self._started:
            return
        self._environment.close()
        self._started = False
        self._prepared = False
