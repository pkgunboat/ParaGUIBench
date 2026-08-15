"""WebMall 生产 WP-CLI 订单证据源与有界子进程边界。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from typing import Protocol

from paraguibench.integrations.webmall.environment_manifest import (
    WebMallEnvironmentManifest,
)
from paraguibench.integrations.webmall.evidence_contracts import (
    WEBMALL_LOGICAL_STORE_IDS,
    WEBMALL_STORE_UNIVERSE_ID,
    ObservedCheckoutOrder,
    OrderIdentityBatch,
)
from paraguibench.integrations.webmall.wp_order_parser import (
    parse_wp_cli_order_details_payload,
    parse_wp_cli_order_identity_payload,
    parse_wp_cli_order_payload,
)


MAX_WPCLI_STDERR_BYTES = 64 * 1024
"""WP-CLI stderr 的固定最大接收字节数。"""

MAX_WPCLI_DETAIL_IDS_PER_REQUEST = 128
"""单次 details 调用允许的最大数字订单 ID 数。"""

_MAX_WP_ORDER_ID = 9_223_372_036_854_775_807
_MAX_READER_SCRIPT_BYTES = 1024 * 1024
_PUBLIC_ERROR = "WEBMALL_WPCLI_ORDER_SOURCE_FAILED"
_PROCESS_ENVIRONMENT_ALLOWLIST = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SSH_AUTH_SOCK",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "WP_CLI_CONFIG_PATH",
    "WP_CLI_CACHE_DIR",
    "WP_CLI_PHP",
    "WP_CLI_PHP_ARGS",
    "WP_CLI_DOCKER_NO_TTY",
)


class WebMallWPCLIOrderSourceError(RuntimeError):
    """表示 WP-CLI 证据源配置、执行或解析失败。"""

    code = _PUBLIC_ERROR

    def __init__(self) -> None:
        """构造不回显 target、路径、订单或子进程输出的固定错误。

        输入参数：
            无。
        输出返回值：
            无；异常字符串始终为公开 code。
        """

        super().__init__(self.code)


@dataclass(frozen=True, slots=True, repr=False)
class BoundedProcessRequest:
    """描述一次不经 shell 且输入输出有界的进程请求。"""

    argv: tuple[str, ...]
    stdin: bytes
    timeout_seconds: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    shell: bool = False

    def __post_init__(self) -> None:
        """验证 argv、stdin、超时和字节上限不能扩大进程能力。

        输入参数：
            无；读取数据类字段。
        输出返回值：
            无；合法请求正常完成构造。
        异常：
            WebMallWPCLIOrderSourceError：字段不是固定的有界形式。
        """

        if (
            not isinstance(self.argv, tuple)
            or not self.argv
            or any(
                not isinstance(value, str) or not value or "\x00" in value
                for value in self.argv
            )
            or not isinstance(self.stdin, bytes)
            or not self.stdin
            or len(self.stdin) > _MAX_READER_SCRIPT_BYTES
            or not _bounded_integer(self.timeout_seconds, 1, 300)
            or not _bounded_integer(
                self.max_stdout_bytes,
                1,
                16 * 1024 * 1024,
            )
            or not _bounded_integer(
                self.max_stderr_bytes,
                1,
                1024 * 1024,
            )
            or self.shell is not False
        ):
            raise WebMallWPCLIOrderSourceError


@dataclass(frozen=True, slots=True, repr=False)
class BoundedProcessResult:
    """保存不向 repr 暴露的有界子进程结果。"""

    returncode: int
    stdout: bytes
    stderr: bytes


class BoundedProcessRunner(Protocol):
    """定义可注入的有界子进程系统边界。"""

    def run(self, request: BoundedProcessRequest) -> BoundedProcessResult:
        """执行一次已验证的有界请求。

        输入参数：
            request：包含固定 argv、stdin 和资源上限的请求。
        输出返回值：
            不超过请求上限的进程结果。
        """

        ...


class SubprocessBoundedProcessRunner:
    """使用标准库子进程执行有界请求。"""

    def run(self, request: BoundedProcessRequest) -> BoundedProcessResult:
        """以 ``shell=False`` 执行请求并拒绝超限输出。

        输入参数：
            request：已验证的有界进程请求。
        输出返回值：
            保留原始 bytes 的退出码与双流结果。
        异常：
            WebMallWPCLIOrderSourceError：启动、超时或输出超限。
        """

        process: subprocess.Popen[bytes] | None = None
        try:
            with tempfile.TemporaryFile(mode="w+b") as stdin_file:
                stdin_file.write(request.stdin)
                stdin_file.seek(0)
                process = subprocess.Popen(
                    request.argv,
                    stdin=stdin_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    env=_build_subprocess_environment(os.environ),
                    shell=False,
                    start_new_session=True,
                )
                return _collect_bounded_process_output(process, request)
        except Exception:
            pass
        finally:
            if process is not None and process.poll() is None:
                _terminate_process_group(process)
        raise WebMallWPCLIOrderSourceError


class WebMallWPCLIOrderEvidenceSource:
    """从四个受信 WP-CLI target 读取权威订单证据。"""

    def __init__(
        self,
        *,
        manifest: WebMallEnvironmentManifest,
        manifest_path: Path,
        environment: Mapping[str, str],
        runner: BoundedProcessRunner | None = None,
    ) -> None:
        """固定 manifest、reader 脚本位置与四店 target 快照。

        输入参数：
            manifest：已严格验证的 WebMall 四店环境 manifest。
            manifest_path：用于定位同目录固定 reader 脚本的 manifest 路径。
            environment：受信部署绑定；只读取 manifest 列出的
                四个 ``reader_target_env``。
            runner：可选系统边界；测试可注入不启动真实进程的 fake。
        输出返回值：
            无；构造一个只读订单证据源。
        异常：
            WebMallWPCLIOrderSourceError：manifest、路径或 target 绑定无效。
        """

        try:
            if (
                not isinstance(manifest, WebMallEnvironmentManifest)
                or not isinstance(manifest_path, Path)
                or not isinstance(environment, Mapping)
                or manifest.store_universe_id != WEBMALL_STORE_UNIVERSE_ID
                or tuple(store.logical_store_id for store in manifest.stores)
                != WEBMALL_LOGICAL_STORE_IDS
            ):
                raise _SourceConfigurationFailure
            bindings: dict[str, tuple[str, tuple[str, ...]]] = {}
            targets: list[str] = []
            for store in manifest.stores:
                target = environment.get(store.reader_target_env)
                if not _valid_reader_target(target):
                    raise _SourceConfigurationFailure
                targets.append(target)
                bindings[store.logical_store_id] = (
                    target,
                    store.credit_card_payment_method_ids,
                )
            if len(set(targets)) != len(WEBMALL_LOGICAL_STORE_IDS):
                raise _SourceConfigurationFailure
            script_path = manifest_path.parent / manifest.order_reader.script_path
            self._manifest = manifest
            self._script_path = script_path
            self._bindings = bindings
            self._runner = (
                runner if runner is not None else SubprocessBoundedProcessRunner()
            )
        except Exception:
            pass
        else:
            return
        raise WebMallWPCLIOrderSourceError

    def read_order_identities(
        self,
        logical_store_id: str,
    ) -> OrderIdentityBatch:
        """只读取一个 logical store 的完整历史订单 identity。

        输入参数：
            logical_store_id：固定四店 universe 中的 store identity。
        输出返回值：
            不含 billing、payment、items 或 slug 的完整 identity 批次。
        异常：
            WebMallWPCLIOrderSourceError：store、脚本、进程或载荷无效；
                错误不回显 target、stdout/stderr 或订单身份。
        """

        try:
            binding = self._bindings.get(logical_store_id)
            if binding is None:
                raise _SourceConfigurationFailure
            target, _credit_card_ids = binding
            script = _read_pinned_script(
                self._script_path,
                self._manifest.order_reader.script_sha256,
            )
            request = BoundedProcessRequest(
                argv=(
                    "wp",
                    f"--ssh={target}",
                    "--quiet",
                    "eval-file",
                    "-",
                    "identities",
                ),
                stdin=script,
                timeout_seconds=self._manifest.order_reader.timeout_seconds,
                max_stdout_bytes=(self._manifest.order_reader.max_stdout_bytes),
                max_stderr_bytes=MAX_WPCLI_STDERR_BYTES,
                shell=False,
            )
            result = self._runner.run(request)
            _validate_process_result(result, request)
            return parse_wp_cli_order_identity_payload(
                logical_store_id=logical_store_id,
                payload=result.stdout,
            )
        except Exception:
            pass
        raise WebMallWPCLIOrderSourceError

    def read_orders(
        self,
        logical_store_id: str,
    ) -> tuple[ObservedCheckoutOrder, ...]:
        """读取一个 logical store 的全部相关状态订单。

        输入参数：
            logical_store_id：固定四店 universe 中的 store identity。
        输出返回值：
            由现有 WP-CLI parser 构造的不可变订单 DTO 元组。
        异常：
            WebMallWPCLIOrderSourceError：store、脚本、进程或载荷无效；
                错误不回显 target、stdout/stderr 或订单内容。
        """

        try:
            binding = self._bindings.get(logical_store_id)
            if binding is None:
                raise _SourceConfigurationFailure
            target, credit_card_ids = binding
            script = _read_pinned_script(
                self._script_path,
                self._manifest.order_reader.script_sha256,
            )
            request = BoundedProcessRequest(
                argv=(
                    "wp",
                    f"--ssh={target}",
                    "--quiet",
                    "eval-file",
                    "-",
                ),
                stdin=script,
                timeout_seconds=self._manifest.order_reader.timeout_seconds,
                max_stdout_bytes=(self._manifest.order_reader.max_stdout_bytes),
                max_stderr_bytes=MAX_WPCLI_STDERR_BYTES,
                shell=False,
            )
            result = self._runner.run(request)
            _validate_process_result(result, request)
            return parse_wp_cli_order_payload(
                logical_store_id=logical_store_id,
                payload=result.stdout,
                credit_card_payment_method_ids=credit_card_ids,
            )
        except Exception:
            pass
        raise WebMallWPCLIOrderSourceError

    def read_checkout_orders_by_identity(
        self,
        logical_store_id: str,
        order_identities: tuple[str, ...],
    ) -> tuple[ObservedCheckoutOrder, ...]:
        """按有界分块读取指定新订单的完整严格证据。

        输入参数：
            logical_store_id：固定四店 universe 中的 store identity。
            order_identities：由 identity 差集产生的唯一正整数
                十进制字符串元组；不接受任务文本或 shell 片段。
        输出返回值：
            按请求分块合并的严格 ``ObservedCheckoutOrder`` 元组。
        异常：
            WebMallWPCLIOrderSourceError：store、ID、脚本、进程、载荷或
                exact-set 无效；错误不回显 ID 或订单详情。
        """

        try:
            binding = self._bindings.get(logical_store_id)
            if binding is None:
                raise _SourceConfigurationFailure
            target, credit_card_ids = binding
            normalized_ids = _normalize_numeric_order_identities(order_identities)
            if not normalized_ids:
                return ()
            script = _read_pinned_script(
                self._script_path,
                self._manifest.order_reader.script_sha256,
            )
            observed: list[ObservedCheckoutOrder] = []
            for offset in range(
                0,
                len(normalized_ids),
                MAX_WPCLI_DETAIL_IDS_PER_REQUEST,
            ):
                chunk = normalized_ids[
                    offset : offset + MAX_WPCLI_DETAIL_IDS_PER_REQUEST
                ]
                request = BoundedProcessRequest(
                    argv=(
                        "wp",
                        f"--ssh={target}",
                        "--quiet",
                        "eval-file",
                        "-",
                        "details",
                        *chunk,
                    ),
                    stdin=script,
                    timeout_seconds=(self._manifest.order_reader.timeout_seconds),
                    max_stdout_bytes=(self._manifest.order_reader.max_stdout_bytes),
                    max_stderr_bytes=MAX_WPCLI_STDERR_BYTES,
                    shell=False,
                )
                result = self._runner.run(request)
                _validate_process_result(result, request)
                observed.extend(
                    parse_wp_cli_order_details_payload(
                        logical_store_id=logical_store_id,
                        payload=result.stdout,
                        credit_card_payment_method_ids=credit_card_ids,
                        expected_order_identities=chunk,
                    )
                )
            return tuple(observed)
        except Exception:
            pass
        raise WebMallWPCLIOrderSourceError


class _SourceConfigurationFailure(ValueError):
    """表示源内部检测到不可用的受信配置。"""


class _ProcessBoundaryFailure(RuntimeError):
    """表示源内部检测到不合法的进程结果。"""


def _validate_process_result(
    result: object,
    request: BoundedProcessRequest,
) -> None:
    """验证 runner 结果完整、未超限且成功退出。

    输入参数：
        result：可注入 runner 返回的候选结果。
        request：声明 stdout/stderr 独立上限的原始请求。
    输出返回值：
        无；进程成功且双流类型、大小合法时正常返回。
    异常：
        _ProcessBoundaryFailure：退出码、结果类型或资源边界无效。
    """

    if (
        not isinstance(result, BoundedProcessResult)
        or not isinstance(result.returncode, int)
        or isinstance(result.returncode, bool)
        or not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or len(result.stdout) > request.max_stdout_bytes
        or len(result.stderr) > request.max_stderr_bytes
        or result.returncode != 0
    ):
        raise _ProcessBoundaryFailure


def _collect_bounded_process_output(
    process: subprocess.Popen[bytes],
    request: BoundedProcessRequest,
) -> BoundedProcessResult:
    """同时有界收集 stdout/stderr，并在超限或超时时终止进程组。

    输入参数：
        process：已以独立 session 启动、且双流为 PIPE 的子进程。
        request：提供总超时和双流独立字节上限的请求。
    输出返回值：
        双流都完整 EOF 且未超限时的不可变结果。
    异常：
        _ProcessBoundaryFailure：管道无效、超时或任一流超限。
    """

    if process.stdout is None or process.stderr is None:
        raise _ProcessBoundaryFailure
    buffers = {
        "stdout": bytearray(),
        "stderr": bytearray(),
    }
    limits = {
        "stdout": request.max_stdout_bytes,
        "stderr": request.max_stderr_bytes,
    }
    deadline = time.monotonic() + request.timeout_seconds
    with selectors.DefaultSelector() as selector:
        selector.register(
            process.stdout,
            selectors.EVENT_READ,
            data="stdout",
        )
        selector.register(
            process.stderr,
            selectors.EVENT_READ,
            data="stderr",
        )
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(process)
                raise _ProcessBoundaryFailure
            for key, _mask in selector.select(remaining):
                stream_name = key.data
                buffer = buffers[stream_name]
                limit = limits[stream_name]
                available = limit - len(buffer)
                chunk = os.read(key.fd, min(64 * 1024, available + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if len(chunk) > available:
                    _terminate_process_group(process)
                    raise _ProcessBoundaryFailure
                buffer.extend(chunk)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _terminate_process_group(process)
        raise _ProcessBoundaryFailure
    try:
        returncode = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        raise _ProcessBoundaryFailure from None
    return BoundedProcessResult(
        returncode=returncode,
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """最大努力终止 runner 创建的独立进程组并回收直接子进程。

    输入参数：
        process：由当前 runner 以 ``start_new_session=True`` 创建的进程。
    输出返回值：
        无；函数不向公开错误传递底层信息。
    """

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _build_subprocess_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    """从父进程环境构造 WP/SSH/Docker 执行所需的最小白名单。

    输入参数：
        environment：当前进程环境；可能同时含模型凭据与部署参数。
    输出返回值：
        仅含命令查找、用户目录、locale、SSH agent、Docker
        与 WP-CLI 本身配置的新字典；不传递 API key/token。
    """

    result: dict[str, str] = {}
    for name in _PROCESS_ENVIRONMENT_ALLOWLIST:
        value = environment.get(name)
        if isinstance(value, str) and "\x00" not in value:
            result[name] = value
    return result


def _read_pinned_script(path: Path, expected_sha256: str) -> bytes:
    """通过 nofollow descriptor 有界读取并校验固定 PHP 脚本。

    输入参数：
        path：manifest 目录与固定 ``script_path`` 合成的路径。
        expected_sha256：manifest 声明的小写 SHA-256。
    输出返回值：
        与声明摘要一致的非空脚本 bytes。
    异常：
        OSError/_SourceConfigurationFailure：文件不安全、超限或摘要不一致。
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_READER_SCRIPT_BYTES
        ):
            raise _SourceConfigurationFailure
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise _SourceConfigurationFailure
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise _SourceConfigurationFailure
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise _SourceConfigurationFailure
    return payload


def _valid_reader_target(value: object) -> bool:
    """判定受信 reader target 是否可作为单个 ``--ssh=`` argv 值。

    输入参数：
        value：从 manifest 指定环境变量读取的候选 target。
    输出返回值：
        仅当它为非空、有界、不含空白或控制字符的字符串时为真。
    """

    return (
        isinstance(value, str)
        and 1 <= len(value) <= 1024
        and not any(character.isspace() for character in value)
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _normalize_numeric_order_identities(
    values: tuple[str, ...],
) -> tuple[str, ...]:
    """验证 details argv 只由唯一、规范的正整数 ID 组成。

    输入参数：
        values：待放入 ``shell=False`` argv 的订单 identity 元组。
    输出返回值：
        保留请求顺序的原元组。
    异常：
        _SourceConfigurationFailure：容器、唯一性或数字范围无效。
    """

    if not isinstance(values, tuple) or len(values) != len(set(values)):
        raise _SourceConfigurationFailure
    for value in values:
        if (
            not isinstance(value, str)
            or not value.isascii()
            or not value.isdecimal()
            or value.startswith("0")
        ):
            raise _SourceConfigurationFailure
        numeric_value = int(value)
        if numeric_value <= 0 or numeric_value > _MAX_WP_ORDER_ID:
            raise _SourceConfigurationFailure
    return values


def _bounded_integer(value: object, minimum: int, maximum: int) -> bool:
    """判定值是否为指定闭区间内的非布尔整数。

    输入参数：
        value：待检查的值。
        minimum/maximum：允许的包含性下界与上界。
    输出返回值：
        值为非布尔 ``int`` 且处于闭区间时返回真。
    """

    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )
