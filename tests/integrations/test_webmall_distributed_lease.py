"""WebMall 跨进程/跨 host 分布式租约客户端契约测试。"""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import pytest

from paraguibench.integrations.webmall import distributed_lease as lease_module
from paraguibench.integrations.webmall.distributed_lease import (
    HTTPJSONLeaseTransport,
    LEASE_PROTOCOL_ID,
    WebMallDistributedLeaseClient,
    WebMallDistributedLeaseError,
    build_webmall_distributed_lease_client,
)
from paraguibench.integrations.webmall.environment_manifest import (
    WebMallLeaseContract,
)

_TEST_BEARER_TOKEN = "".join(("synthetic-", "lease-", "credential-", "value-0001"))


class _RecordingTransport:
    """记录请求并返回指定 JSON object 的合成远端 transport。"""

    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        """保存按调用顺序返回的响应。

        输入参数：
            responses：每次 ``post_json`` 返回的合成 JSON object。
        输出返回值：
            无；构造后可通过 ``calls`` 检查边界请求。
        """

        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        *,
        action: str,
        payload: Mapping[str, object],
        credential: str,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        """记录一次权威协调器请求并返回下一个合成响应。

        输入参数：
            action：租约协议动作。
            payload：已脱离本地锁的 JSON 请求字段。
            credential：仅交给 transport 的认证值。
            timeout_seconds：单次请求超时上限。
            max_response_bytes：响应体最大字节数。
        输出返回值：
            下一个合成 JSON object。
        """

        self.calls.append(
            {
                "action": action,
                "payload": dict(payload),
                "credential": credential,
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            }
        )
        return self._responses.pop(0)


class _ReleaseFailureTransport(_RecordingTransport):
    """在 acquire 成功后注入一次含敏感文本的远程释放失败。"""

    def post_json(
        self,
        *,
        action: str,
        payload: Mapping[str, object],
        credential: str,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        """让 acquire 使用常规响应，release 抛出不得外泄的底层错误。

        输入参数：
            action/payload/credential/timeout_seconds/max_response_bytes：
                与生产 transport 相同的系统边界参数。
        输出返回值：
            acquire 时返回合成响应；release 时抛出底层异常。
        """

        if action == "release":
            self.calls.append(
                {
                    "action": action,
                    "payload": dict(payload),
                    "credential": credential,
                    "timeout_seconds": timeout_seconds,
                    "max_response_bytes": max_response_bytes,
                }
            )
            raise RuntimeError(
                f"https://lease.example.invalid {_TEST_BEARER_TOKEN} "
                "private-response-body"
            )
        return super().post_json(
            action=action,
            payload=payload,
            credential=credential,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )


class _HTTPResponse:
    """提供有界 read 可观测性的合成 HTTP 响应。"""

    def __init__(self, body: bytes, *, status: int = 200) -> None:
        """绑定响应体与 HTTP 状态。

        输入参数：
            body：后续 ``read`` 返回的 JSON bytes。
            status：合成 HTTP status code。
        输出返回值：
            无；构造后可检查读取上限和关闭状态。
        """

        self.status = status
        self._body = body
        self.read_sizes: list[int] = []
        self.closed = False

    def getheader(self, name: str) -> str | None:
        """返回 transport 安全检查所需的响应 header。

        输入参数：
            name：不区分大小写的 header 名。
        输出返回值：
            Content-Type/Content-Length 的合成值，其他返回 ``None``。
        """

        if name.lower() == "content-type":
            return "application/json; charset=utf-8"
        if name.lower() == "content-length":
            return str(len(self._body))
        return None

    def read(self, amount: int) -> bytes:
        """记录读取字节上限并返回有界响应体。

        输入参数：
            amount：transport 允许读取的最大字节数。
        输出返回值：
            至多 ``amount`` 字节的响应体。
        """

        self.read_sizes.append(amount)
        return self._body[:amount]

    def close(self) -> None:
        """记录响应已被 transport 关闭。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.closed = True


class _HTTPConnection:
    """记录 HTTPS request 而不打开 socket 的合成连接。"""

    def __init__(self, response: _HTTPResponse) -> None:
        """绑定 ``getresponse`` 返回的合成响应。

        输入参数：
            response：本连接的唯一响应。
        输出返回值：
            无；请求可从 ``requests`` 检查。
        """

        self._response = response
        self.requests: list[dict[str, object]] = []
        self.closed = False

    def request(
        self,
        method: str,
        target: str,
        *,
        body: bytes,
        headers: Mapping[str, str],
    ) -> None:
        """记录 transport 发送的 HTTP request。

        输入参数：
            method/target/body/headers：生产 ``http.client`` 边界参数。
        输出返回值：
            无。
        """

        self.requests.append(
            {
                "method": method,
                "target": target,
                "body": body,
                "headers": dict(headers),
            }
        )

    def getresponse(self) -> _HTTPResponse:
        """返回预置响应。

        输入参数：
            无。
        输出返回值：
            合成 HTTP 响应。
        """

        return self._response

    def close(self) -> None:
        """记录 socket 等价连接已关闭。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.closed = True


def _response(*, status: str, fencing_token: int = 41) -> dict[str, object]:
    """构造与测试租约身份一致的权威响应。

    输入参数：
        status：当前租约动作的成功状态。
        fencing_token：协调器分配的单调 fencing token。
    输出返回值：
        严格 v1 租约响应 JSON object。
    """

    return {
        "protocol_id": LEASE_PROTOCOL_ID,
        "status": status,
        "namespace": "paraguibench-reference-four-stores",
        "lease_id": "lease-001",
        "attempt_id": "attempt-001",
        "owner_id": "worker-host-a-123",
        "fencing_token": fencing_token,
    }


def _client(transport: _RecordingTransport) -> WebMallDistributedLeaseClient:
    """构造使用合成远程边界的固定测试客户端。

    输入参数：
        transport：不联网的权威协调器边界。
    输出返回值：
        尚未取得租约的客户端。
    """

    return WebMallDistributedLeaseClient(
        coordinator_url="https://lease.example.invalid/base",
        credential=_TEST_BEARER_TOKEN,
        namespace="paraguibench-reference-four-stores",
        ttl_seconds=7200,
        attempt_id="attempt-001",
        owner_id="worker-host-a-123",
        timeout_seconds=3.5,
        max_response_bytes=8192,
        transport=transport,
    )


def test_acquire_uses_authoritative_transport_and_binds_fencing_identity() -> None:
    """验证 acquire 只接受协调器签发且身份匹配的 fencing grant。

    输入参数：
        无；注入一个返回有效 grant 的合成 transport。
    输出返回值：
        无；断言请求带有 Attempt/owner/TTL，且客户端保存
        权威 lease ID 与 fencing token。
    """

    transport = _RecordingTransport([_response(status="acquired")])
    client = _client(transport)

    grant = client.acquire()

    assert grant.attempt_id == "attempt-001"
    assert grant.owner_id == "worker-host-a-123"
    assert grant.lease_id == "lease-001"
    assert grant.fencing_token == 41
    assert transport.calls == [
        {
            "action": "acquire",
            "payload": {
                "protocol_id": LEASE_PROTOCOL_ID,
                "namespace": "paraguibench-reference-four-stores",
                "attempt_id": "attempt-001",
                "owner_id": "worker-host-a-123",
                "ttl_seconds": 7200,
            },
            "credential": _TEST_BEARER_TOKEN,
            "timeout_seconds": 3.5,
            "max_response_bytes": 8192,
        }
    ]


def test_assert_held_rechecks_same_fencing_ownership_remotely() -> None:
    """验证 assert_held 每次都通过协调器复核原 fencing grant。

    输入参数：
        无；先返回 acquire grant，再返回 held 确认。
    输出返回值：
        无；断言复核请求带上原 lease ID 和 fencing token，
        且返回同一个不可变 grant。
    """

    transport = _RecordingTransport(
        [
            _response(status="acquired"),
            _response(status="held"),
        ]
    )
    client = _client(transport)
    acquired = client.acquire()

    confirmed = client.assert_held()

    assert confirmed is acquired
    assert transport.calls[1]["action"] == "assert-held"
    assert transport.calls[1]["payload"] == {
        "protocol_id": LEASE_PROTOCOL_ID,
        "namespace": "paraguibench-reference-four-stores",
        "attempt_id": "attempt-001",
        "owner_id": "worker-host-a-123",
        "lease_id": "lease-001",
        "fencing_token": 41,
    }


def test_release_is_remote_fenced_and_client_cannot_reuse_grant() -> None:
    """验证 release 由协调器确认原 fencing ownership 后才结束。

    输入参数：
        无；远程依次返回 acquired 与 released。
    输出返回值：
        无；断言 release 带原 grant，且释放后的客户端不能再用
        本地旧状态执行 ownership 检查。
    """

    transport = _RecordingTransport(
        [
            _response(status="acquired"),
            _response(status="released"),
        ]
    )
    client = _client(transport)
    client.acquire()

    released = client.release()

    assert released.fencing_token == 41
    assert transport.calls[1]["action"] == "release"
    assert transport.calls[1]["payload"]["lease_id"] == "lease-001"
    assert transport.calls[1]["payload"]["fencing_token"] == 41
    with pytest.raises(WebMallDistributedLeaseError):
        client.assert_held()


def test_release_success_is_idempotent_without_second_remote_mutation() -> None:
    """验证已获协调器确认的 release 可以安全重复清理。

    输入参数：
        无；合成 transport 只提供一次 release 响应。
    输出返回值：
        无；两次 release 返回同一 grant，第二次不再触发
        远程状态变更。
    """

    transport = _RecordingTransport(
        [
            _response(status="acquired"),
            _response(status="released"),
        ]
    )
    client = _client(transport)
    client.acquire()

    first = client.release()
    second = client.release()

    assert second is first
    assert [call["action"] for call in transport.calls] == [
        "acquire",
        "release",
    ]


def test_release_failure_is_redacted_and_permanently_fail_closed() -> None:
    """验证不确定的远程 release 不会被本地幂等逻辑伪装成功。

    输入参数：
        无；在已获取 grant 后注入含 endpoint、credential 和响应文本
        的 transport 异常。
    输出返回值：
        无；首次 release 会执行一次有界重试，两次均失败后
        只暴露固定错误码，后续调用不再发起远程变更。
    """

    transport = _ReleaseFailureTransport([_response(status="acquired")])
    client = _client(transport)
    client.acquire()

    for _ in range(2):
        with pytest.raises(WebMallDistributedLeaseError) as captured:
            client.release()
        assert str(captured.value) == "WEBMALL_DISTRIBUTED_LEASE_ERROR"
        assert "lease.example.invalid" not in str(captured.value)
        assert _TEST_BEARER_TOKEN not in str(captured.value)
        assert "private-response" not in str(captured.value)

    assert [call["action"] for call in transport.calls] == [
        "acquire",
        "release",
        "release",
    ]


def test_lost_ownership_blocks_evidence_but_still_attempts_fenced_cleanup() -> None:
    """验证 ownership 复核失败后不再宣称持有但仍可安全释放。

    输入参数：
        无；assert-held 返回不同 fencing token，随后 release 对原 token
        返回权威确认。
    输出返回值：
        无；assert-held 固定失败，release 仍向远程发送原 fencing
        grant，以免只依赖 TTL 泄漏共享环境容量。
    """

    transport = _RecordingTransport(
        [
            _response(status="acquired"),
            _response(status="held", fencing_token=42),
            _response(status="released"),
        ]
    )
    client = _client(transport)
    client.acquire()

    with pytest.raises(WebMallDistributedLeaseError):
        client.assert_held()
    released = client.release()

    assert released.fencing_token == 41
    assert [call["action"] for call in transport.calls] == [
        "acquire",
        "assert-held",
        "release",
    ]
    assert transport.calls[-1]["payload"]["fencing_token"] == 41


def test_https_transport_binds_endpoint_bearer_timeout_and_read_limit() -> None:
    """验证生产 transport 安全绑定 HTTPS endpoint 和资源上限。

    输入参数：
        无；注入不打开 socket 的 connection factory。
    输出返回值：
        无；断言 endpoint 只被组合为固定 v1 path，Bearer 仅放入
        header，并且响应只读取 ``limit + 1`` 字节。
    """

    body = json.dumps(_response(status="acquired")).encode("utf-8")
    response = _HTTPResponse(body)
    connection = _HTTPConnection(response)
    factory_calls: list[dict[str, object]] = []

    def connection_factory(
        scheme: str,
        host: str,
        port: int | None,
        timeout_seconds: float,
    ) -> _HTTPConnection:
        """记录解析后的 endpoint 与 timeout 并返回合成连接。

        输入参数：
            scheme/host/port：通过纯 endpoint 解析得到的连接身份。
            timeout_seconds：传给 socket 连接的超时。
        输出返回值：
            不联网的合成 HTTP 连接。
        """

        factory_calls.append(
            {
                "scheme": scheme,
                "host": host,
                "port": port,
                "timeout_seconds": timeout_seconds,
            }
        )
        return connection

    transport = HTTPJSONLeaseTransport(
        "https://lease.example.invalid:9443/base",
        connection_factory=connection_factory,
    )

    parsed = transport.post_json(
        action="acquire",
        payload={"protocol_id": LEASE_PROTOCOL_ID},
        credential=_TEST_BEARER_TOKEN,
        timeout_seconds=3.5,
        max_response_bytes=8192,
    )

    assert parsed == _response(status="acquired")
    assert factory_calls == [
        {
            "scheme": "https",
            "host": "lease.example.invalid",
            "port": 9443,
            "timeout_seconds": 3.5,
        }
    ]
    request = connection.requests[0]
    assert request["method"] == "POST"
    assert request["target"] == "/base/v1/leases/acquire"
    assert request["headers"] == {
        "Accept": "application/json",
        "Authorization": f"Bearer {_TEST_BEARER_TOKEN}",
        "Content-Type": "application/json",
    }
    assert response.read_sizes == [8193]
    assert response.closed is True
    assert connection.closed is True


def test_https_transport_treats_redirect_as_fixed_failure() -> None:
    """验证 3xx 响应不会把 Bearer credential 跟随到另一个 endpoint。

    输入参数：
        无；底层连接返回一个内容为有效 JSON 的 307 响应。
    输出返回值：
        无；transport 只建立一次原 endpoint 连接并返固定错误。
    """

    body = json.dumps(_response(status="acquired")).encode("utf-8")
    response = _HTTPResponse(body, status=307)
    connection = _HTTPConnection(response)
    connection_count = 0

    def connection_factory(
        scheme: str,
        host: str,
        port: int | None,
        timeout_seconds: float,
    ) -> _HTTPConnection:
        """记录重定向响应前的连接建立次数。

        输入参数：
            scheme/host/port/timeout_seconds：原 HTTPS endpoint 连接参数。
        输出返回值：
            返回 307 响应的合成连接。
        """

        nonlocal connection_count
        assert scheme == "https"
        assert host == "lease.example.invalid"
        assert port is None
        assert timeout_seconds == 2.0
        connection_count += 1
        return connection

    transport = HTTPJSONLeaseTransport(
        "https://lease.example.invalid",
        connection_factory=connection_factory,
    )

    with pytest.raises(WebMallDistributedLeaseError) as captured:
        transport.post_json(
            action="acquire",
            payload={"protocol_id": LEASE_PROTOCOL_ID},
            credential=_TEST_BEARER_TOKEN,
            timeout_seconds=2.0,
            max_response_bytes=8192,
        )

    assert str(captured.value) == "WEBMALL_DISTRIBUTED_LEASE_ERROR"
    assert connection_count == 1
    assert connection.closed is True


def test_client_uses_url_bound_https_transport_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证生产客户端默认用 coordinator URL 构造真实 HTTPS transport。

    输入参数：
        monkeypatch：只替换最底层 socket 连接工厂，保留完整生产
            URL、HTTP、JSON 和客户端校验路径。
    输出返回值：
        无；断言未显式注入 transport 仍可 acquire，且 socket 工厂
        收到 coordinator URL 中的 HTTPS host/port。
    """

    body = json.dumps(_response(status="acquired")).encode("utf-8")
    response = _HTTPResponse(body)
    connection = _HTTPConnection(response)
    factory_calls: list[tuple[str, str, int | None, float]] = []

    def connection_factory(
        scheme: str,
        host: str,
        port: int | None,
        timeout_seconds: float,
    ) -> _HTTPConnection:
        """记录默认 transport 解析的连接参数。

        输入参数：
            scheme/host/port/timeout_seconds：标准库连接构造参数。
        输出返回值：
            不打开 socket 的合成连接。
        """

        factory_calls.append((scheme, host, port, timeout_seconds))
        return connection

    monkeypatch.setattr(
        lease_module,
        "_default_http_connection_factory",
        connection_factory,
    )
    client = WebMallDistributedLeaseClient(
        coordinator_url="https://lease.example.invalid:9443/base",
        credential=_TEST_BEARER_TOKEN,
        namespace="paraguibench-reference-four-stores",
        ttl_seconds=7200,
        attempt_id="attempt-001",
        owner_id="worker-host-a-123",
    )

    grant = client.acquire()

    assert grant.fencing_token == 41
    assert factory_calls == [("https", "lease.example.invalid", 9443, 10.0)]


def test_manifest_lease_refs_bind_environment_without_exposing_values() -> None:
    """验证工厂只按 manifest 公开变量名绑定 endpoint/token。

    输入参数：
        无；传入 v1 lease contract、显式环境 Mapping 和合成 transport。
    输出返回值：
        无；断言 manifest 的 namespace/TTL 进入 acquire，token 仅到达
        transport；绑定缺失时异常不暴露变量名或值。
    """

    contract = WebMallLeaseContract(
        protocol_id=LEASE_PROTOCOL_ID,
        coordinator_url_env="PRIVATE_COORDINATOR_ENV",
        credential_env="PRIVATE_TOKEN_ENV",
        namespace="paraguibench-reference-four-stores",
        ttl_seconds=7200,
    )
    transport = _RecordingTransport([_response(status="acquired")])
    client = build_webmall_distributed_lease_client(
        contract=contract,
        environment={
            "PRIVATE_COORDINATOR_ENV": "https://lease.example.invalid/base",
            "PRIVATE_TOKEN_ENV": _TEST_BEARER_TOKEN,
        },
        attempt_id="attempt-001",
        owner_id="worker-host-a-123",
        transport=transport,
    )

    client.acquire()

    assert transport.calls[0]["payload"]["namespace"] == contract.namespace
    assert transport.calls[0]["payload"]["ttl_seconds"] == 7200
    assert transport.calls[0]["credential"] == (_TEST_BEARER_TOKEN)

    with pytest.raises(WebMallDistributedLeaseError) as captured:
        build_webmall_distributed_lease_client(
            contract=contract,
            environment={},
            attempt_id="attempt-002",
            owner_id="worker-host-a-123",
            transport=transport,
        )
    assert str(captured.value) == "WEBMALL_DISTRIBUTED_LEASE_ERROR"
    assert "PRIVATE_" not in str(captured.value)
    assert "test-secret" not in str(captured.value)


def test_plain_http_rejects_non_loopback_coordinator() -> None:
    """验证 Bearer credential 绝不会通过非 loopback 明文 HTTP 发送。

    输入参数：
        无；构造一个使用非 loopback 主机名的 HTTP coordinator URL。
    输出返回值：
        无；构造阶段即固定拒绝，错误不回显 endpoint。
    """

    with pytest.raises(WebMallDistributedLeaseError) as captured:
        HTTPJSONLeaseTransport("http://example.com:8080/base")

    assert str(captured.value) == "WEBMALL_DISTRIBUTED_LEASE_ERROR"
    assert "example.com" not in str(captured.value)


@pytest.mark.parametrize(
    "credential",
    [
        "A" * 31,
        "A" * 4_090,
        "A" * 31 + "=",
        "A" * 31 + " ",
    ],
)
def test_client_rejects_credentials_outside_shared_base64url_contract(
    credential: str,
) -> None:
    """验证客户端在发送前拒绝服务端必定不接受的凭据。

    输入参数：
        credential：过短、计入 ``Bearer `` 后过长，或不属于
            无填充 base64url 字符集的合成凭据。
    输出返回值：
        无；构造阶段以固定脱敏错误 fail closed。
    """

    with pytest.raises(WebMallDistributedLeaseError):
        WebMallDistributedLeaseClient(
            coordinator_url="https://lease.example.invalid",
            credential=credential,
            namespace="paraguibench-reference-four-stores",
            ttl_seconds=60,
            attempt_id="attempt-001",
            owner_id="worker-host-a-123",
            transport=_RecordingTransport([]),
        )


def test_endpoint_rejects_control_characters_before_url_normalization() -> None:
    """验证 URL parser 不能在安全校验前静默删除 endpoint 控制字符。

    输入参数：
        无；在 HTTPS hostname 中注入会被 ``urlsplit`` 规范化的换行。
    输出返回值：
        无；构造在解析结果被使用前固定失败。
    """

    with pytest.raises(WebMallDistributedLeaseError):
        HTTPJSONLeaseTransport("https://lease.example.invalid\n.attacker.invalid/base")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:8080/base",
        "http://[::1]:8080/base",
        "http://localhost:8080/base",
    ],
)
def test_plain_http_is_limited_to_explicit_loopback_hosts(
    endpoint: str,
) -> None:
    """验证本地协调器开发只保留三种显式 loopback 地址。

    输入参数：
        endpoint：127.0.0.1、::1 或 localhost 之一的 HTTP URL。
    输出返回值：
        无；构造 transport 成功，但不打开 socket 或发送 credential。
    """

    transport = HTTPJSONLeaseTransport(endpoint)

    assert isinstance(transport, HTTPJSONLeaseTransport)


def test_https_transport_rejects_oversized_response_before_body_read() -> None:
    """验证声明超限的协调器响应不会被读入内存或回显。

    输入参数：
        无；响应 Content-Length 大于客户端 64-byte 上限。
    输出返回值：
        无；返回固定错误，``read`` 不被调用，响应与连接仍关闭。
    """

    response = _HTTPResponse(b"{" + b"x" * 512 + b"}")
    connection = _HTTPConnection(response)

    def connection_factory(
        scheme: str,
        host: str,
        port: int | None,
        timeout_seconds: float,
    ) -> _HTTPConnection:
        """返回声明超限响应的合成连接。

        输入参数：
            scheme/host/port/timeout_seconds：已验证的连接参数。
        输出返回值：
            不打开 socket 的合成连接。
        """

        assert scheme == "https"
        assert host == "lease.example.invalid"
        assert port is None
        assert timeout_seconds == 2.0
        return connection

    transport = HTTPJSONLeaseTransport(
        "https://lease.example.invalid",
        connection_factory=connection_factory,
    )

    with pytest.raises(WebMallDistributedLeaseError) as captured:
        transport.post_json(
            action="acquire",
            payload={"protocol_id": LEASE_PROTOCOL_ID},
            credential=_TEST_BEARER_TOKEN,
            timeout_seconds=2.0,
            max_response_bytes=64,
        )

    assert str(captured.value) == "WEBMALL_DISTRIBUTED_LEASE_ERROR"
    assert response.read_sizes == []
    assert response.closed is True
    assert connection.closed is True
