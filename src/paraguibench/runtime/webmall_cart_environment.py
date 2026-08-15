"""组合单 VM GUI 环境与同一浏览器会话 WebMall Cart 证据源。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from paraguibench.integrations.webmall.cart_contracts import (
    CartObservationBatch,
)
from paraguibench.integrations.webmall.cart_evidence import (
    CART_EVIDENCE_SOURCE_PROTOCOL_ID,
    capture_webmall_cart_observation,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceCaptureProof,
)


_WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class WebMallCartTaskEnvironmentError(RuntimeError):
    """表示 Cart 环境依赖、生命周期、任务身份或证据捕获无效。"""

    code = "WEBMALL_CART_TASK_ENVIRONMENT_INVALID"

    def __init__(self) -> None:
        """构造不回显 worker、URL、slug、Cart 或底层异常的固定错误。

        输入参数：无。
        输出返回值：无；异常公开文本固定为稳定 code。
        """

        super().__init__(self.code)


class WebMallCartTaskEnvironment:
    """管理单 VM Cart reader 准备、一次终态冻结与 owned cleanup。"""

    def __init__(
        self,
        *,
        environment: Any,
        evidence_source: Any,
        worker_id: str,
    ) -> None:
        """绑定底层 GUI 环境、版本化 Cart source 与单 worker 身份。

        输入参数：
            environment：实现 ``start/prepare/close/controller`` 的底层
                OSWorld GUI 环境。
            evidence_source：实现 ``prepare/read_cart/close`` 且声明
                Cart authoritative-state 协议的生产 source。
            worker_id：当前唯一 BrowserContext 的受信内存身份。
        输出返回值：
            无；构造阶段不启动 VM、Chrome 或读取 Cart。
        异常：
            WebMallCartTaskEnvironmentError：依赖接口、协议或身份无效。
        """

        try:
            for method_name in ("start", "prepare", "close"):
                if not callable(getattr(environment, method_name, None)):
                    raise TypeError
            if not hasattr(environment, "controller"):
                raise TypeError
            for method_name in ("prepare", "read_cart", "close"):
                if not callable(getattr(evidence_source, method_name, None)):
                    raise TypeError
            if (
                getattr(evidence_source, "evidence_protocol_id", None)
                != CART_EVIDENCE_SOURCE_PROTOCOL_ID
            ):
                raise TypeError
            if (
                not isinstance(worker_id, str)
                or _WORKER_ID_PATTERN.fullmatch(worker_id) is None
            ):
                raise TypeError
        except Exception:
            raise WebMallCartTaskEnvironmentError from None
        self._environment = environment
        self._evidence_source = evidence_source
        self._worker_id = worker_id
        self._started = False
        self._prepared = False
        self._closed = False
        self._observation: CartObservationBatch | None = None

    def __repr__(self) -> str:
        """返回不含 worker、controller 或 Cart 内容的生命周期表示。

        输入参数：无。
        输出返回值：固定类名与三个布尔状态。
        """

        return (
            "WebMallCartTaskEnvironment("
            f"started={self._started!r}, prepared={self._prepared!r}, "
            f"closed={self._closed!r})"
        )

    @property
    def controller(self) -> Any:
        """返回 Agent worker 使用的底层 GUI controller。

        输入参数：无。
        输出返回值：底层环境公开的同一 controller 对象。
        """

        return self._environment.controller

    @property
    def guest_shared_dir(self) -> str | None:
        """返回底层环境已准备的 guest shared 目录。

        输入参数：无。
        输出返回值：底层字段为字符串时原样返回，否则返回 ``None``。
        """

        value = getattr(self._environment, "guest_shared_dir", None)
        return value if isinstance(value, str) else None

    def start(self) -> None:
        """启动当前 Attempt 唯一的底层 GUI 环境。

        输入参数：无。
        输出返回值：无；成功后允许 ``prepare``。
        异常：WebMallCartTaskEnvironmentError：重复启动或环境已经关闭。
        """

        if self._started or self._closed:
            raise WebMallCartTaskEnvironmentError
        self._started = True
        try:
            self._environment.start()
        except Exception:
            raise WebMallCartTaskEnvironmentError from None

    def prepare(self, task: Mapping[str, Any]) -> None:
        """先准备 canonical Cart task，再在 Agent 前启动并验证 CDP source。

        输入参数：
            task：AttemptRunner 提供的可信 WebMall ``answer_type=cart`` 任务。
        输出返回值：
            无；两层准备均完成后允许 Agent 与最终 Cart 读取。
        异常：
            WebMallCartTaskEnvironmentError：生命周期、任务身份或准备失败。
        """

        if (
            not self._started
            or self._prepared
            or self._closed
            or not _is_cart_task(task)
        ):
            raise WebMallCartTaskEnvironmentError
        try:
            self._environment.prepare(task)
            self._evidence_source.prepare(self._environment.controller)
        except Exception:
            raise WebMallCartTaskEnvironmentError from None
        self._prepared = True

    def cart_observation(self) -> CartObservationBatch:
        """在环境仍存活时捕获并缓存单 worker×固定四店终态。

        输入参数：无。
        输出返回值：首次调用产生的完整不可变 ``CartObservationBatch``。
        异常：
            WebMallCartTaskEnvironmentError：环境未准备、已关闭或任一四店
                evidence 无法完整读取。
        """

        if not self._prepared or self._closed:
            raise WebMallCartTaskEnvironmentError
        if self._observation is None:
            try:
                observation = capture_webmall_cart_observation(
                    self._evidence_source,
                    (self._worker_id,),
                )
            except Exception:
                raise WebMallCartTaskEnvironmentError from None
            if not isinstance(observation, CartObservationBatch):
                raise WebMallCartTaskEnvironmentError
            self._observation = observation
        return self._observation

    def reference_validation_proof(self) -> WebMallCartReferenceCaptureProof:
        """返回显式 reference-validation 捕获产生的脱敏连续性证明。

        输入参数：无。
        输出返回值：production source 的同 context 双 sweep 证明。
        异常：WebMallCartTaskEnvironmentError：环境未捕获终态、已关闭，
            source 不支持该显式入口或返回错误类型。
        """

        if not self._prepared or self._closed or self._observation is None:
            raise WebMallCartTaskEnvironmentError
        proof_reader = getattr(
            self._evidence_source,
            "reference_validation_proof",
            None,
        )
        if not callable(proof_reader):
            raise WebMallCartTaskEnvironmentError
        try:
            proof = proof_reader()
        except Exception:
            raise WebMallCartTaskEnvironmentError from None
        if not isinstance(proof, WebMallCartReferenceCaptureProof):
            raise WebMallCartTaskEnvironmentError
        return proof

    def close(self) -> None:
        """关闭 source 逻辑生命周期并清理底层 owned VM，不自动补拍 Cart。

        输入参数：无。
        输出返回值：无；重复关闭幂等。
        异常：
            单一清理错误抛固定环境错误；两层同时失败抛仅含固定错误对象的
            ``ExceptionGroup``，不保留底层敏感消息。
        """

        if self._closed:
            return
        errors: list[BaseException] = []
        try:
            self._evidence_source.close()
        except BaseException:
            errors.append(WebMallCartTaskEnvironmentError())
        if self._started:
            try:
                self._environment.close()
            except BaseException:
                errors.append(WebMallCartTaskEnvironmentError())
        self._closed = True
        self._started = False
        self._prepared = False
        self._observation = None
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("WebMall Cart environment cleanup failed", errors)


def _is_cart_task(task: object) -> bool:
    """验证环境只接受 canonical WebMall Cart 任务身份闭集。

    输入参数：task 为 AttemptRunner 候选可信任务。
    输出返回值：来源、类型、答案模式、evaluator 路径和 gold 均合法时为真。
    """

    if not isinstance(task, Mapping):
        return False
    expected_urls = task.get("expected_urls")
    return (
        isinstance(task.get("task_id"), str)
        and bool(task.get("task_id"))
        and task.get("task_source") == "WebMall"
        and task.get("task_type") == "QA"
        and task.get("answer_type") == "cart"
        and task.get("evaluator_path") == "evaluators/cart_evaluator.py"
        and isinstance(expected_urls, Sequence)
        and not isinstance(expected_urls, (str, bytes))
        and bool(expected_urls)
        and all(isinstance(value, str) and value for value in expected_urls)
    )
