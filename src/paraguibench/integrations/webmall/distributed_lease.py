"""WebMall 共享四店后端的跨进程/跨 host 权威租约客户端。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import http.client
import json
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from paraguibench.integrations.webmall.environment_manifest import (
    WebMallLeaseContract,
)

LEASE_PROTOCOL_ID = "paraguibench.webmall.distributed-lease.v1"

_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
_NAMESPACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_LEASE_RESPONSE_FIELDS = frozenset(
    {
        "protocol_id",
        "status",
        "namespace",
        "lease_id",
        "attempt_id",
        "owner_id",
        "fencing_token",
    }
)
_URL_PATH_PATTERN = re.compile(r"/[A-Za-z0-9._~/-]*\Z")
_MAX_REQUEST_BYTES = 65_536
_MAX_ALLOWED_RESPONSE_BYTES = 1_048_576
_BEARER_HEADER_PREFIX = "Bearer "
LEASE_BEARER_TOKEN_MAX_CHARACTERS = 4_096 - len(_BEARER_HEADER_PREFIX)
LEASE_BEARER_TOKEN_MIN_CHARACTERS = 32
_CREDENTIAL_PATTERN = re.compile(
    rf"[A-Za-z0-9_-]{{{LEASE_BEARER_TOKEN_MIN_CHARACTERS},"
    rf"{LEASE_BEARER_TOKEN_MAX_CHARACTERS}}}\Z"
)
_PLAINTEXT_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class _HTTPConnectionLike(Protocol):
    """定义生产 http.client 与纯测试连接的最小交集。"""

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes,
        headers: Mapping[str, str],
    ) -> None:
        """发送一次已经有界编码的 HTTP request。

        输入参数：
            method/url/body/headers：标准库连接所需的方法、路径、
                JSON bytes 与认证 header。
        输出返回值：
            无；连接错误由调用方固定脱敏。
        """

    def getresponse(self) -> Any:
        """返回支持 status/getheader/read/close 的 HTTP 响应。

        输入参数：
            无。
        输出返回值：
            可进行 MIME、状态码和有界 body 检查的响应对象。
        """

    def close(self) -> None:
        """关闭底层连接。

        输入参数：
            无。
        输出返回值：
            无；用于成功和失败路径的确定性资源清理。
        """


class _HTTPConnectionFactory(Protocol):
    """定义用于测试注入的 HTTP(S) 连接工厂。"""

    def __call__(
        self,
        scheme: str,
        host: str,
        port: int | None,
        timeout_seconds: float,
    ) -> _HTTPConnectionLike:
        """按已验证 endpoint 建立一个有超时上限的连接。

        输入参数：
            scheme/host/port：已经安全解析的 endpoint 身份。
            timeout_seconds：单次连接和 socket 读写超时。
        输出返回值：
            生产标准库连接或测试合成连接。
        """


class WebMallDistributedLeaseError(RuntimeError):
    """表示租约配置、远程协议或本地状态无法安全确认。"""

    code = "WEBMALL_DISTRIBUTED_LEASE_ERROR"

    def __init__(self) -> None:
        """构造不回显 endpoint、credential 或远程响应的固定错误。

        输入参数：
            无。
        输出返回值：
            无；异常文本恒为公开错误码。
        """

        super().__init__(self.code)


def is_valid_lease_bearer_credential(value: object) -> bool:
    """检查 lease Bearer token 是否满足客户端/服务端共享契约。

    输入参数：
        value：待验证的 credential 候选值。
    输出返回值：
        仅当值是 32 至 4089 个无填充 base64url 可打印 ASCII
        字符时返回 ``True``；上限已为 ``Bearer `` header 前缀
        保留 7 个字符。
    """

    return isinstance(value, str) and _CREDENTIAL_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class WebMallLeaseGrant:
    """保存协调器签发的 Attempt/owner/fencing 所有权。"""

    lease_id: str
    attempt_id: str
    owner_id: str
    fencing_token: int


class WebMallLeaseTransport(Protocol):
    """定义可注入的 JSON 协调器系统边界。"""

    def post_json(
        self,
        *,
        action: str,
        payload: Mapping[str, object],
        credential: str,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        """向权威协调器发送有界 JSON 请求。

        输入参数：
            action：``acquire``、``assert-held`` 或 ``release``。
            payload：协议 v1 请求 object。
            credential：协调器 Bearer credential，不得记录。
            timeout_seconds：单次网络操作超时上限。
            max_response_bytes：响应体最大字节数。
        输出返回值：
            已解码的 JSON object；网络、HTTP 或解码失败必须抛异常。
        """


class HTTPJSONLeaseTransport:
    """通过无重定向的标准库 HTTP(S) POST 调用租约协调器。"""

    def __init__(
        self,
        coordinator_url: str,
        *,
        connection_factory: _HTTPConnectionFactory | None = None,
    ) -> None:
        """验证并固定协调器 endpoint，但不立即打开连接。

        输入参数：
            coordinator_url：不含 userinfo、query 或 fragment 的 HTTP(S)
                协调器基地址；可以含纯 ASCII path prefix。
            connection_factory：可选的 socket 系统边界，仅用于纯测试；
                生产默认使用 ``http.client`` 的 HTTPS/HTTP 连接。
        输出返回值：
            无；端点身份保存为不公开字段。
        异常：
            WebMallDistributedLeaseError：endpoint 可能触发重定向、header
                注入或不确定主机解析。
        """

        if (
            not isinstance(coordinator_url, str)
            or not coordinator_url
            or len(coordinator_url) > 2_048
            or any(
                ord(character) <= 32 or ord(character) == 127
                for character in coordinator_url
            )
        ):
            raise WebMallDistributedLeaseError
        try:
            parsed = urlsplit(coordinator_url)
            port = parsed.port
        except (AttributeError, TypeError, ValueError):
            raise WebMallDistributedLeaseError from None
        path = parsed.path.rstrip("/")
        path_segments = path.split("/")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or (
                parsed.scheme == "http"
                and parsed.hostname.lower() not in _PLAINTEXT_LOOPBACK_HOSTS
            )
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (path and _URL_PATH_PATTERN.fullmatch(path) is None)
            or any(segment in {".", ".."} for segment in path_segments)
            or "//" in path
            or (connection_factory is not None and not callable(connection_factory))
        ):
            raise WebMallDistributedLeaseError
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = port
        self._base_path = path
        self._connection_factory = (
            connection_factory or _default_http_connection_factory
        )

    def post_json(
        self,
        *,
        action: str,
        payload: Mapping[str, object],
        credential: str,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        """发送一次不跟随重定向的 Bearer JSON POST 并有界解码响应。

        输入参数：
            action：仅允许 ``acquire``、``assert-held`` 或 ``release``。
            payload：可确定编码为 JSON object 的协议请求。
            credential：无控制字符的有界 Bearer credential。
            timeout_seconds：连接与响应读取的秒级上限，最大 60 秒。
            max_response_bytes：响应体最大字节数，最大 1 MiB。
        输出返回值：
            拒绝重复 key 后解码的 UTF-8 JSON object。
        异常：
            WebMallDistributedLeaseError：参数、网络、HTTP status/MIME、
                响应大小或 JSON 无效；异常不回显 endpoint、
                credential 或 body。
        """

        if (
            action not in {"acquire", "assert-held", "release"}
            or not isinstance(payload, Mapping)
            or not is_valid_lease_bearer_credential(credential)
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < float(timeout_seconds) <= 60
            or not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or not 1 <= max_response_bytes <= _MAX_ALLOWED_RESPONSE_BYTES
        ):
            raise WebMallDistributedLeaseError
        try:
            request_body = json.dumps(
                dict(payload),
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise WebMallDistributedLeaseError from None
        if not request_body or len(request_body) > _MAX_REQUEST_BYTES:
            raise WebMallDistributedLeaseError

        connection: _HTTPConnectionLike | None = None
        response: Any = None
        try:
            connection = self._connection_factory(
                self._scheme,
                self._host,
                self._port,
                float(timeout_seconds),
            )
            connection.request(
                "POST",
                f"{self._base_path}/v1/leases/{action}",
                body=request_body,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"{_BEARER_HEADER_PREFIX}{credential}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                if (
                    not content_length.isascii()
                    or not content_length.isdecimal()
                    or int(content_length) > max_response_bytes
                ):
                    raise WebMallDistributedLeaseError
            content_type = response.getheader("Content-Type")
            content_encoding = response.getheader("Content-Encoding")
            if (
                not isinstance(content_type, str)
                or content_type.split(";", 1)[0].strip().lower() != "application/json"
                or content_encoding not in {None, "", "identity"}
            ):
                raise WebMallDistributedLeaseError
            body = response.read(max_response_bytes + 1)
            if (
                response.status != 200
                or not isinstance(body, bytes)
                or not body
                or len(body) > max_response_bytes
            ):
                raise WebMallDistributedLeaseError
            decoded = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonfinite_json_number,
            )
            if not isinstance(decoded, dict):
                raise WebMallDistributedLeaseError
            return decoded
        except Exception:
            raise WebMallDistributedLeaseError from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def _default_http_connection_factory(
    scheme: str,
    host: str,
    port: int | None,
    timeout_seconds: float,
) -> _HTTPConnectionLike:
    """用标准库建立不自动跟随重定向的 HTTP(S) 连接。

    输入参数：
        scheme：已验证的 ``http`` 或 ``https``。
        host/port：已从纯 endpoint 拆分的网络地址。
        timeout_seconds：连接与 socket 读写超时上限。
    输出返回值：
        HTTPS 时使用默认系统 CA/主机名校验的标准库连接；
        HTTP 时返回明文连接，上层构造器已将其限制为
        ``127.0.0.1``、``::1`` 或 ``localhost`` loopback。
    """

    connection_class = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    return connection_class(host, port=port, timeout=timeout_seconds)


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """把 JSON object pairs 转为映射并拒绝任意层级的重复 key。

    输入参数：
        pairs：JSON decoder 传入的保序 key/value 列表。
    输出返回值：
        key 唯一的 dict。
    异常：
        WebMallDistributedLeaseError：出现可导致解释分歧的重复 key。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WebMallDistributedLeaseError
        result[key] = value
    return result


def _reject_nonfinite_json_number(value: str) -> Any:
    """拒绝 JSON 标准之外的 NaN 与无穷数字面量。

    输入参数：
        value：decoder 遇到的非有限数字文本。
    输出返回值：
        不返回，始终抛出固定租约错误。
    """

    del value
    raise WebMallDistributedLeaseError


class WebMallDistributedLeaseClient:
    """通过远程权威 grant 管理单个 Attempt 的四店独占租约。"""

    def __init__(
        self,
        *,
        coordinator_url: str,
        credential: str,
        namespace: str,
        ttl_seconds: int,
        attempt_id: str,
        owner_id: str,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 65_536,
        transport: WebMallLeaseTransport | None = None,
    ) -> None:
        """绑定一个 Attempt 的远程协调器身份与资源上限。

        输入参数：
            coordinator_url：由部署层传入的 HTTP(S) 协调器基地址。
            credential：仅交给 transport 的 Bearer credential。
            namespace：固定 WebMall 环境的全局租约命名空间。
            ttl_seconds：协调器租约 TTL。
            attempt_id：RunStore 中当前 Attempt 的稳定身份。
            owner_id：本次 worker 进程的跨 host 唯一身份。
            timeout_seconds：单次远程请求超时上限，默认 10 秒。
            max_response_bytes：单次响应体字节上限，默认 64 KiB。
            transport：可选的协调器系统边界；未传入时必定由
                ``coordinator_url`` 构造生产 HTTP(S) transport，测试可
                注入不联网的合成实现。
        输出返回值：
            无；构造阶段不发网络请求，也不以本地锁声称所有权。
        异常：
            WebMallDistributedLeaseError：配置、身份或 transport 无效。
        """

        if (
            not isinstance(coordinator_url, str)
            or not coordinator_url
            or len(coordinator_url) > 2_048
            or not is_valid_lease_bearer_credential(credential)
            or not isinstance(namespace, str)
            or _NAMESPACE_PATTERN.fullmatch(namespace) is None
            or not isinstance(attempt_id, str)
            or _IDENTIFIER_PATTERN.fullmatch(attempt_id) is None
            or not isinstance(owner_id, str)
            or _IDENTIFIER_PATTERN.fullmatch(owner_id) is None
            or not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not 1 <= ttl_seconds <= 86_400
            or not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < float(timeout_seconds) <= 60
            or not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or not 1 <= max_response_bytes <= _MAX_ALLOWED_RESPONSE_BYTES
            or (
                transport is not None
                and not callable(getattr(transport, "post_json", None))
            )
        ):
            raise WebMallDistributedLeaseError
        if transport is None:
            transport = HTTPJSONLeaseTransport(coordinator_url)
        self._credential = credential
        self._namespace = namespace
        self._ttl_seconds = ttl_seconds
        self._attempt_id = attempt_id
        self._owner_id = owner_id
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._transport = transport
        self._grant: WebMallLeaseGrant | None = None
        self._state = "new"

    def acquire(self) -> WebMallLeaseGrant:
        """从权威协调器获取并验证当前 Attempt 的 fencing grant。

        输入参数：
            无。
        输出返回值：
            经身份回绑验证的不可变 grant。
        异常：
            WebMallDistributedLeaseError：重复获取、transport 失败或响应
                无法证明当前 Attempt/owner 拥有该 fencing token。
        """

        if self._state != "new":
            raise WebMallDistributedLeaseError
        self._state = "acquire-failed"
        payload: dict[str, object] = {
            "protocol_id": LEASE_PROTOCOL_ID,
            "namespace": self._namespace,
            "attempt_id": self._attempt_id,
            "owner_id": self._owner_id,
            "ttl_seconds": self._ttl_seconds,
        }
        try:
            grant = self._request_grant_with_retry(
                action="acquire",
                payload=payload,
                expected_status="acquired",
            )
        except Exception:
            raise WebMallDistributedLeaseError from None
        self._grant = grant
        self._state = "held"
        return grant

    def assert_held(self) -> WebMallLeaseGrant:
        """向协调器重新确认当前客户端仍持有原 fencing grant。

        输入参数：
            无。
        输出返回值：
            acquire 时保存的同一个不可变 grant。
        异常：
            WebMallDistributedLeaseError：客户端未持有租约、协调器
                不可达，或 lease ID/fencing token 已不再属于当前
                Attempt/owner。失败后客户端永久进入 fail-closed 状态。
        """

        if self._state != "held" or self._grant is None:
            raise WebMallDistributedLeaseError
        grant = self._grant
        self._state = "ownership-uncertain"
        payload: dict[str, object] = {
            "protocol_id": LEASE_PROTOCOL_ID,
            "namespace": self._namespace,
            "attempt_id": self._attempt_id,
            "owner_id": self._owner_id,
            "lease_id": grant.lease_id,
            "fencing_token": grant.fencing_token,
        }
        try:
            confirmed = self._request_grant_with_retry(
                action="assert-held",
                payload=payload,
                expected_status="held",
            )
            if confirmed != grant:
                raise WebMallDistributedLeaseError
        except Exception:
            raise WebMallDistributedLeaseError from None
        self._state = "held"
        return grant

    def release(self) -> WebMallLeaseGrant:
        """通过原 lease ID 与 fencing token 请求协调器释放租约。

        输入参数：
            无。
        输出返回值：
            协调器已确认释放的原 grant。
        异常：
            WebMallDistributedLeaseError：客户端未持有租约、协调器
                不可达，或远程没有证明原 fencing ownership 已释放。
                结果不确定时不会在本地伪装成功。
        """

        if self._state == "released" and self._grant is not None:
            return self._grant
        if self._state not in {"held", "ownership-uncertain"} or self._grant is None:
            raise WebMallDistributedLeaseError
        grant = self._grant
        self._state = "release-uncertain"
        payload: dict[str, object] = {
            "protocol_id": LEASE_PROTOCOL_ID,
            "namespace": self._namespace,
            "attempt_id": self._attempt_id,
            "owner_id": self._owner_id,
            "lease_id": grant.lease_id,
            "fencing_token": grant.fencing_token,
        }
        try:
            released = self._request_grant_with_retry(
                action="release",
                payload=payload,
                expected_status="released",
            )
            if released != grant:
                raise WebMallDistributedLeaseError
        except Exception:
            raise WebMallDistributedLeaseError from None
        self._state = "released"
        return grant

    def _request_grant_with_retry(
        self,
        *,
        action: str,
        payload: Mapping[str, object],
        expected_status: str,
    ) -> WebMallLeaseGrant:
        """对幂等服务操作执行至多两次的脱敏不确定重试。

        输入参数：
            action：``acquire``、``assert-held`` 或 ``release``。
            payload：已绑定完整 Attempt/owner/fencing 身份的请求。
            expected_status：当前 action 唯一接受的成功状态。
        输出返回值：
            身份与当前客户端严格一致的 grant。
        异常：
            WebMallDistributedLeaseError：两次 transport 均失败，或任一
                已收到的协议响应无法确认身份；不回显
                endpoint、credential 或响应。

        注意：
            首次请求可能已在服务端提交。因此本重试仅依赖
            协议服务端对完整身份的 acquire/assert/release 幂等性。
        """

        for attempt_index in range(2):
            try:
                response = self._transport.post_json(
                    action=action,
                    payload=payload,
                    credential=self._credential,
                    timeout_seconds=self._timeout_seconds,
                    max_response_bytes=self._max_response_bytes,
                )
            except Exception:
                if attempt_index == 1:
                    raise WebMallDistributedLeaseError from None
                continue
            return self._parse_grant(
                response,
                expected_status=expected_status,
            )
        raise WebMallDistributedLeaseError

    def _parse_grant(
        self,
        response: Mapping[str, Any],
        *,
        expected_status: str,
    ) -> WebMallLeaseGrant:
        """严格验证协调器响应与当前客户端身份一致。

        输入参数：
            response：transport 返回的 JSON object。
            expected_status：当前动作唯一允许的成功状态。
        输出返回值：
            经完整字段、身份与 fencing token 校验的 grant。
        异常：
            WebMallDistributedLeaseError：响应不是严格的当前 Attempt grant。
        """

        if (
            not isinstance(response, Mapping)
            or set(response) != _LEASE_RESPONSE_FIELDS
            or response.get("protocol_id") != LEASE_PROTOCOL_ID
            or response.get("status") != expected_status
            or response.get("namespace") != self._namespace
            or response.get("attempt_id") != self._attempt_id
            or response.get("owner_id") != self._owner_id
            or not isinstance(response.get("lease_id"), str)
            or _IDENTIFIER_PATTERN.fullmatch(response["lease_id"]) is None
            or not isinstance(response.get("fencing_token"), int)
            or isinstance(response.get("fencing_token"), bool)
            or response["fencing_token"] < 1
        ):
            raise WebMallDistributedLeaseError
        return WebMallLeaseGrant(
            lease_id=response["lease_id"],
            attempt_id=self._attempt_id,
            owner_id=self._owner_id,
            fencing_token=response["fencing_token"],
        )


def build_webmall_distributed_lease_client(
    *,
    contract: WebMallLeaseContract,
    environment: Mapping[str, str],
    attempt_id: str,
    owner_id: str,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 65_536,
    transport: WebMallLeaseTransport | None = None,
) -> WebMallDistributedLeaseClient:
    """按 manifest 中的公开环境变量引用安全绑定生产租约客户端。

    输入参数：
        contract：已由 WebMall environment manifest loader 验证的 v1
            协调器变量名、namespace 和 TTL。
        environment：部署进程的显式 Mapping；只读取 contract 指定的
            endpoint 与 credential 两个值。
        attempt_id：RunStore 当前 Attempt 的稳定身份。
        owner_id：当前 worker 进程的跨 host 唯一身份。
        timeout_seconds：单次协调器请求超时上限。
        max_response_bytes：单次协调器响应体上限。
        transport：可选测试边界；生产留空时由 endpoint 构造
            默认 HTTP(S) transport。
    输出返回值：
        尚未 acquire 的 Attempt 级权威租约客户端。
    异常：
        WebMallDistributedLeaseError：contract、Mapping 或引用值缺失/无效；
            错误不回显变量名、endpoint 或 credential。
    """

    if (
        not isinstance(contract, WebMallLeaseContract)
        or contract.protocol_id != LEASE_PROTOCOL_ID
        or not isinstance(environment, Mapping)
    ):
        raise WebMallDistributedLeaseError
    try:
        coordinator_url = environment.get(contract.coordinator_url_env)
        credential = environment.get(contract.credential_env)
    except Exception:
        raise WebMallDistributedLeaseError from None
    if (
        not isinstance(coordinator_url, str)
        or not coordinator_url
        or not isinstance(credential, str)
        or not credential
    ):
        raise WebMallDistributedLeaseError
    return WebMallDistributedLeaseClient(
        coordinator_url=coordinator_url,
        credential=credential,
        namespace=contract.namespace,
        ttl_seconds=contract.ttl_seconds,
        attempt_id=attempt_id,
        owner_id=owner_id,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        transport=transport,
    )
