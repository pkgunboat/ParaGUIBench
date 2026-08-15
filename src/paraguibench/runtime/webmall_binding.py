"""WebMall live runtime 在副作前的 manifest、版本与投影闭包。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path, PurePosixPath

from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.webmall import (
    CART_PROTOCOL_ID,
    CHECKOUT_PROTOCOL_ID,
    FIND_AND_ORDER_PROTOCOL_ID,
    URL_MULTISET_PROTOCOL_ID,
)
from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.integrations.webmall import (
    WebMallEnvironmentManifest,
    WebMallOrderEvidenceSession,
    WebMallURLRegistry,
    bind_webmall_origins,
    load_webmall_environment_manifest_with_sha256,
)
from paraguibench.integrations.webmall.distributed_lease import (
    WebMallDistributedLeaseClient,
    WebMallLeaseTransport,
    build_webmall_distributed_lease_client,
)
from paraguibench.integrations.webmall.wpcli_order_source import (
    BoundedProcessRunner,
    WebMallWPCLIOrderEvidenceSource,
)
from paraguibench.runstore import RunVersionVector
from paraguibench.runtime.attempt_runner import TaskEvaluator
from paraguibench.runtime.evaluators import build_task_evaluator
from paraguibench.runtime.run_versioning import build_run_version_vector
from paraguibench.runtime.webmall_cart_component_receipts import (
    load_trusted_webmall_cart_reference_receipt,
)
from paraguibench.runtime.webmall_preparation import (
    materialize_webmall_prepared_task,
)


_WEBMALL_MANIFEST_RELATIVE = PurePosixPath(
    "environments/webmall/environment-manifest.json"
)


class WebMallRuntimeBindingError(RuntimeError):
    """表示 WebMall 浏览器、版本或仓库路径无法安全闭合。"""

    code = "WEBMALL_RUNTIME_BINDING_INVALID"

    def __init__(self) -> None:
        """构造不回显路径、origin、task 正文或底层异常的错误。

        输入参数：
            无。
        输出返回值：
            无；异常文本只是稳定公开 code。
        """

        super().__init__(self.code)


class WebMallPrivilegedRuntimeBindingError(RuntimeError):
    """表示 WebMall reader、租约凭据或 Attempt identity 未安全闭合。"""

    code = "WEBMALL_PRIVILEGED_RUNTIME_BINDING_INVALID"

    def __init__(self) -> None:
        """构造不回显 target、endpoint、token 或底层异常的错误。

        输入参数：
            无。
        输出返回值：
            无；异常文本只是稳定公开 code。
        """

        super().__init__(self.code)


class WebMallEvidenceMode(str, Enum):
    """枚举 WebMall 三类互斥 runtime evidence 路径。"""

    PRIVILEGED_ORDER = "privileged_order"
    BROWSER_CART = "browser_cart"
    REPORTED_URL = "reported_url"


@dataclass(frozen=True, slots=True)
class WebMallRuntimeIdentity:
    """保存不依赖部署变量的 WebMall 代码、协议与 evidence 三态。"""

    prepared_task: PreparedTask
    manifest: WebMallEnvironmentManifest
    webmall_manifest_sha256: str
    browser_image: OSWorldImageManifest
    browser_image_manifest_sha256: str
    version_vector: RunVersionVector
    evaluator: TaskEvaluator
    evidence_mode: WebMallEvidenceMode
    cart_reference_validation_verified: bool

    @property
    def requires_privileged_order_evidence(self) -> bool:
        """返回当前协议是否必须装配 WP-CLI 订单源与分布式租约。

        输入参数：无。
        输出返回值：仅 ``PRIVILEGED_ORDER`` 模式返回 ``True``。
        """

        return self.evidence_mode is WebMallEvidenceMode.PRIVILEGED_ORDER

    @property
    def requires_cart_evidence(self) -> bool:
        """返回当前协议是否必须装配同浏览器会话 Cart reader。

        输入参数：无。
        输出返回值：仅 ``BROWSER_CART`` 模式返回 ``True``。
        """

        return self.evidence_mode is WebMallEvidenceMode.BROWSER_CART


@dataclass(frozen=True, slots=True)
class WebMallRuntimeBinding:
    """保存 WebMall Attempt 尚未启动时的非敏感部署与 evidence 三态。"""

    prepared_task: PreparedTask
    manifest: WebMallEnvironmentManifest
    webmall_manifest_sha256: str
    browser_image: OSWorldImageManifest
    browser_image_manifest_sha256: str
    registry: WebMallURLRegistry
    version_vector: RunVersionVector
    evaluator: TaskEvaluator
    evidence_mode: WebMallEvidenceMode
    cart_reference_validation_verified: bool

    @property
    def requires_privileged_order_evidence(self) -> bool:
        """返回当前部署是否必须装配 WP-CLI 订单源与全局租约。

        输入参数：无。
        输出返回值：仅 ``PRIVILEGED_ORDER`` 模式返回 ``True``。
        """

        return self.evidence_mode is WebMallEvidenceMode.PRIVILEGED_ORDER

    @property
    def requires_cart_evidence(self) -> bool:
        """返回当前部署是否必须装配浏览器 Cart 权威状态源。

        输入参数：无。
        输出返回值：仅 ``BROWSER_CART`` 模式返回 ``True``。
        """

        return self.evidence_mode is WebMallEvidenceMode.BROWSER_CART


@dataclass(frozen=True, slots=True, repr=False)
class WebMallPrivilegedRuntimeBinding:
    """保存一个 Attempt 的特权 reader、分布式租约与证据 session。"""

    source: WebMallWPCLIOrderEvidenceSource
    lease: WebMallDistributedLeaseClient
    session: WebMallOrderEvidenceSession


def preflight_webmall_identity(
    *,
    repo_root: Path,
    prepared_task: PreparedTask,
) -> WebMallRuntimeIdentity:
    """为普通 doctor/run 闭合 WebMall 身份并强制当前 Cart receipt。

    输入参数：repo_root 为仓库根；prepared_task 为已完成
        canonical release 与三投影验证的任务。
    输出返回值：完整 runtime identity；空 allowlist 表示当前
        缺失证明，此时 Cart 验证位为假并交由 doctor 聚合失败；
        已 allowlist 但 receipt 缺失、过期或无效则立即失败关闭。
    """

    return _preflight_webmall_identity(
        repo_root=repo_root,
        prepared_task=prepared_task,
        enforce_current_cart_reference_receipt=True,
    )


def _preflight_webmall_identity(
    *,
    repo_root: Path,
    prepared_task: PreparedTask,
    enforce_current_cart_reference_receipt: bool,
) -> WebMallRuntimeIdentity:
    """不读取部署环境地闭合 WebMall manifest、浏览器与 evaluator。

    输入参数：
        repo_root：包含 release、runtime-support 和两个环境 manifest
            的仓库根。
        prepared_task：已完成 release 摘要、fixture 与三投影的 task。
        enforce_current_cart_reference_receipt：是否为普通 Cart 入口
            强制加载当前 component receipt；仅显式刷新候选入口为假。
    输出返回值：
        仍保留 logical URL 的 task、WebMall/OSWorld 环境身份、
        版本向量与原生 evaluator；便于 doctor 后续聚合列出
        全部部署变量缺口。
    异常：
        TypeError：入参类型无效。
        WebMallEnvironmentManifestError/RunVersioningError/
        UnsupportedTaskEvaluatorError/WebMallRuntimeBindingError：仓库内
            代码、manifest、task 或协议不闭合。
    """

    if (
        not isinstance(repo_root, Path)
        or not isinstance(prepared_task, PreparedTask)
        or not isinstance(enforce_current_cart_reference_receipt, bool)
    ):
        raise TypeError("WebMall runtime identity preflight 入参无效")
    root = repo_root.resolve()
    manifest_path = _safe_repo_file(root, _WEBMALL_MANIFEST_RELATIVE)
    (
        manifest,
        webmall_manifest_sha256,
    ) = load_webmall_environment_manifest_with_sha256(manifest_path)
    browser_path = _safe_repo_file(
        root,
        PurePosixPath("environments/webmall")
        / PurePosixPath(manifest.browser_runtime.image_manifest_ref),
    )
    (
        browser_image,
        browser_image_manifest_sha256,
    ) = load_osworld_image_manifest_with_sha256(browser_path)
    if (
        manifest.browser_runtime.kind != "osworld_chrome"
        or browser_image_manifest_sha256
        != manifest.browser_runtime.image_manifest_sha256
        or manifest.browser_runtime.required_protocol_id
        not in browser_image.protocol_ids
    ):
        raise WebMallRuntimeBindingError

    task_id = prepared_task.trusted_task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise WebMallRuntimeBindingError
    version_vector = build_run_version_vector(
        repo_root=root,
        task_id=task_id,
        environment_manifest_path=manifest_path,
        environment_manifest_sha256=webmall_manifest_sha256,
        environment_protocol_ids=manifest.protocol_ids,
        nested_environment_manifest_sha256=browser_image_manifest_sha256,
        nested_environment_protocol_ids=browser_image.protocol_ids,
    )
    evaluator = build_task_evaluator(
        prepared_task.trusted_task,
        evaluation_protocol=version_vector.evaluation_protocol,
    )
    evidence_mode = _evidence_mode_for_protocol(version_vector.evaluation_protocol)
    cart_reference_validation_verified = False
    if (
        evidence_mode is WebMallEvidenceMode.BROWSER_CART
        and enforce_current_cart_reference_receipt
    ):
        component_receipt = load_trusted_webmall_cart_reference_receipt(root)
        if component_receipt is not None:
            if (
                component_receipt.webmall_manifest_sha256 != webmall_manifest_sha256
                or component_receipt.browser_image_manifest_sha256
                != browser_image_manifest_sha256
            ):
                raise WebMallRuntimeBindingError
            cart_reference_validation_verified = True
    current_manifest, current_webmall_sha256 = (
        load_webmall_environment_manifest_with_sha256(manifest_path)
    )
    current_browser_image, current_browser_sha256 = (
        load_osworld_image_manifest_with_sha256(browser_path)
    )
    if (
        current_manifest != manifest
        or current_webmall_sha256 != webmall_manifest_sha256
        or current_browser_image != browser_image
        or current_browser_sha256 != browser_image_manifest_sha256
    ):
        raise WebMallRuntimeBindingError
    return WebMallRuntimeIdentity(
        prepared_task=prepared_task,
        manifest=manifest,
        webmall_manifest_sha256=webmall_manifest_sha256,
        browser_image=browser_image,
        browser_image_manifest_sha256=browser_image_manifest_sha256,
        version_vector=version_vector,
        evaluator=evaluator,
        evidence_mode=evidence_mode,
        cart_reference_validation_verified=cart_reference_validation_verified,
    )


def preflight_webmall_runtime(
    *,
    repo_root: Path,
    prepared_task: PreparedTask,
    environment: Mapping[str, str],
) -> WebMallRuntimeBinding:
    """在 probe、模型凭据、Agent 与 RunStore 之前闭合 WebMall runtime。

    输入参数：
        repo_root：包含 release、runtime-support、WebMall 与 OSWorld
            manifest 的仓库根。
        prepared_task：已完成 release 摘要与 checkout fixture 物化的
            三投影 task，其中 WebMall URL 仍为 logical identity。
        environment：部署进程环境的只读映射；本阶段只读取 manifest
            声明的四个 origin 引用，不读模型 key 或租约 token。
    输出返回值：
        版本向量、原生 evaluator、固定环境身份、URL registry 和
        仅 Agent instruction 已物化的新 ``PreparedTask``。
    异常：
        TypeError：入参基本类型无效。
        WebMallEnvironmentManifestError/RunVersioningError/
        UnsupportedTaskEvaluatorError：manifest、四店、task 协议或
            evaluator 未闭合；错误发生于任何外部副作前。
        WebMallRuntimeBindingError：仓库路径或底层 browser manifest
            与 WebMall 声明不一致。
    """

    if not isinstance(environment, Mapping):
        raise TypeError("WebMall runtime preflight 入参无效")
    identity = preflight_webmall_identity(
        repo_root=repo_root,
        prepared_task=prepared_task,
    )
    return _materialize_webmall_runtime_identity(
        identity=identity,
        environment=environment,
    )


def preflight_webmall_cart_reference_candidate_runtime(
    *,
    repo_root: Path,
    prepared_task: PreparedTask,
    environment: Mapping[str, str],
) -> WebMallRuntimeBinding:
    """为显式 Cart reference 刷新命令建立不被旧 receipt 锁死的 runtime。

    输入参数：repo_root/prepared_task/environment 与普通 runtime
        preflight 相同；环境仍只读 manifest 声明的四店 origin。
    输出返回值：仅当任务是 ``BROWSER_CART`` 时返回完整
        binding，其 ``cart_reference_validation_verified`` 固定为假；
        候选命令将通过实测产生替换 receipt，不消费旧证明。
    异常：TypeError/WebMallRuntimeBindingError：入参、任务模式或
        除旧 receipt 外的任一仓库/环境闭集无效。
    """

    if not isinstance(environment, Mapping):
        raise TypeError("WebMall Cart reference candidate preflight 入参无效")
    identity = _preflight_webmall_identity(
        repo_root=repo_root,
        prepared_task=prepared_task,
        enforce_current_cart_reference_receipt=False,
    )
    if (
        identity.evidence_mode is not WebMallEvidenceMode.BROWSER_CART
        or identity.cart_reference_validation_verified
    ):
        raise WebMallRuntimeBindingError
    return _materialize_webmall_runtime_identity(
        identity=identity,
        environment=environment,
    )


def _materialize_webmall_runtime_identity(
    *,
    identity: WebMallRuntimeIdentity,
    environment: Mapping[str, str],
) -> WebMallRuntimeBinding:
    """将已闭合静态身份的 logical URL 物化为当前四店 runtime binding。

    输入参数：identity 为普通或 candidate 静态 preflight 结果；
        environment 为只读四店 origin 绑定。
    输出返回值：保留全部身份和 component receipt 已验证位的
        不可变 ``WebMallRuntimeBinding``。
    """

    if not isinstance(identity, WebMallRuntimeIdentity) or not isinstance(
        environment,
        Mapping,
    ):
        raise TypeError("WebMall runtime materialization 入参无效")
    registry = bind_webmall_origins(identity.manifest, environment)
    materialized = materialize_webmall_prepared_task(
        identity.prepared_task,
        manifest=identity.manifest,
        registry=registry,
    )
    return WebMallRuntimeBinding(
        prepared_task=materialized,
        manifest=identity.manifest,
        webmall_manifest_sha256=identity.webmall_manifest_sha256,
        browser_image=identity.browser_image,
        browser_image_manifest_sha256=identity.browser_image_manifest_sha256,
        registry=registry,
        version_vector=identity.version_vector,
        evaluator=identity.evaluator,
        evidence_mode=identity.evidence_mode,
        cart_reference_validation_verified=(
            identity.cart_reference_validation_verified
        ),
    )


def bind_webmall_privileged_runtime(
    *,
    repo_root: Path,
    runtime: WebMallRuntimeBinding,
    environment: Mapping[str, str],
    attempt_id: str,
    owner_id: str,
    lease_transport: WebMallLeaseTransport | None = None,
    order_runner: BoundedProcessRunner | None = None,
) -> WebMallPrivilegedRuntimeBinding:
    """在任何 I/O 前快照 WebMall reader target、lease credential 与 identity。

    输入参数：
        repo_root：包含已按 SHA 锁定 WP-CLI PHP reader 的仓库根。
        runtime：已完成四店 origin 物化和版本闭包的 runtime。
        environment：部署进程环境；只读 manifest 指定的四个
            reader target、协调器 URL 和租约 credential。
        attempt_id/owner_id：本 Attempt 的稳定身份与跨 host 唯一
            owner identity。
        lease_transport：可选协调器 transport；测试可注入不联网
            fake，生产省略时由 HTTPS URL 构造标准库 transport。
        order_runner：可选 WP-CLI 有界子进程边界；生产省略时使用
            shell-free 默认 runner。
    输出返回值：
        已装配但尚未读商店、未联系协调器的 source/lease/session。
    异常：
        WebMallPrivilegedRuntimeBindingError：类型、路径、环境变量、
            Attempt identity 或任一依赖构造失败；错误不携带值。
    """

    failed = False
    try:
        if (
            not isinstance(repo_root, Path)
            or not isinstance(runtime, WebMallRuntimeBinding)
            or not isinstance(environment, Mapping)
            or not runtime.requires_privileged_order_evidence
        ):
            raise TypeError
        manifest_path = _safe_repo_file(
            repo_root.resolve(),
            _WEBMALL_MANIFEST_RELATIVE,
        )
        source = WebMallWPCLIOrderEvidenceSource(
            manifest=runtime.manifest,
            manifest_path=manifest_path,
            environment=environment,
            runner=order_runner,
        )
        lease = build_webmall_distributed_lease_client(
            contract=runtime.manifest.lease,
            environment=environment,
            attempt_id=attempt_id,
            owner_id=owner_id,
            transport=lease_transport,
        )
        session = WebMallOrderEvidenceSession(
            source=source,
            lease=lease,
        )
    except Exception:
        failed = True
    if failed:
        raise WebMallPrivilegedRuntimeBindingError
    return WebMallPrivilegedRuntimeBinding(
        source=source,
        lease=lease,
        session=session,
    )


def _evidence_mode_for_protocol(
    evaluation_protocol: str,
) -> WebMallEvidenceMode:
    """将 WebMall evaluator 协议闭合为互斥 evidence 三态。

    输入参数：
        evaluation_protocol：已由 runtime-support 与 evaluator registry
            共同验证的版本化 WebMall 协议 ID。
    输出返回值：Checkout/FindAndOrder、Cart、URL multiset 分别返回
        ``PRIVILEGED_ORDER``、``BROWSER_CART``、``REPORTED_URL``。
    异常：
        WebMallRuntimeBindingError：出现未纳入闭集的协议，
            避免默认为无特权而误装配。
    """

    if evaluation_protocol in {
        CHECKOUT_PROTOCOL_ID,
        FIND_AND_ORDER_PROTOCOL_ID,
    }:
        return WebMallEvidenceMode.PRIVILEGED_ORDER
    if evaluation_protocol == CART_PROTOCOL_ID:
        return WebMallEvidenceMode.BROWSER_CART
    if evaluation_protocol == URL_MULTISET_PROTOCOL_ID:
        return WebMallEvidenceMode.REPORTED_URL
    raise WebMallRuntimeBindingError


def _safe_repo_file(root: Path, relative: PurePosixPath) -> Path:
    """在不跟随仓库内 symlink 的前提下解析已知 manifest 路径。

    输入参数：
        root：已 resolve 的仓库根。
        relative：由受信 manifest 结构产生的 POSIX 相对路径，
            允许在仓库内规范化 ``..``。
    输出返回值：
        仓库内存在的普通文件绝对路径。
    异常：
        WebMallRuntimeBindingError：路径越界、链中出现 symlink，
            或目标不是普通文件。
    """

    if relative.is_absolute() or "\\" in str(relative):
        raise WebMallRuntimeBindingError
    candidate = Path(os.path.normpath(str(root.joinpath(*relative.parts))))
    try:
        relative_parts = candidate.relative_to(root).parts
    except ValueError:
        raise WebMallRuntimeBindingError from None
    current = root
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise WebMallRuntimeBindingError
    if not candidate.is_file():
        raise WebMallRuntimeBindingError
    return candidate
