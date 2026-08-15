"""WebMall 跨进程权威租约协调器的专项行为测试。"""

from __future__ import annotations

from collections.abc import Mapping
from http.server import ThreadingHTTPServer
import json
import multiprocessing
from pathlib import Path
import sqlite3
import threading
from typing import Any

import pytest

from paraguibench.integrations.webmall import lease_coordinator as lease_module
from paraguibench.integrations.webmall.lease_coordinator import (
    LEASE_PROTOCOL_ID,
    LeaseCoordinatorError,
    SQLiteLeaseCoordinator,
    build_lease_server_argument_parser,
    create_lease_http_application,
    create_lease_http_server,
)
from paraguibench.integrations.webmall.distributed_lease import (
    WebMallDistributedLeaseClient,
)

_TEST_BEARER_TOKEN = "".join(("synthetic-", "server-", "lease-", "credential-0001"))


class _Clock:
    """提供可控的 Unix 毫秒时钟。"""

    def __init__(self, now_ms: int = 1_800_000_000_000) -> None:
        """初始化测试时间。

        输入参数：
            now_ms：初始 Unix 毫秒。
        输出返回值：
            无。
        """

        self.now_ms = now_ms

    def __call__(self) -> int:
        """返回当前合成时间。

        输入参数：
            无。
        输出返回值：
            当前 Unix 毫秒。
        """

        return self.now_ms


class _TransactionRaceClock:
    """用可控阻塞复现“先读时钟、后等 SQLite 锁”的竞态。"""

    def __init__(self, now_ms: int = 1_800_000_000_000) -> None:
        """初始化时间、第二次读入事件与放行门。

        输入参数：
            now_ms：初始合成 Unix 毫秒。
        输出返回值：无。
        """

        self.now_ms = now_ms
        self.call_count = 0
        self.second_call_entered = threading.Event()
        self.allow_second_call_to_return = threading.Event()

    def __call__(self) -> int:
        """返回调用入口时的时间，并在第二次读取时可控阻塞。

        输入参数：无。
        输出返回值：
            进入本次调用时冻结的 Unix 毫秒；测试据此区分
            时钟读取在 SQLite 写锁之前还是之后。
        """

        self.call_count += 1
        captured = self.now_ms
        if self.call_count == 2:
            self.second_call_entered.set()
            assert self.allow_second_call_to_return.wait(timeout=5)
        return captured


class _FakeSocket:
    """记录服务端入场超时与过载关闭的合成 socket。"""

    def __init__(self) -> None:
        """初始化空的 socket 观测记录。

        输入参数：无。
        输出返回值：无。
        """

        self.timeouts: list[float] = []
        self.shutdown_calls: list[int] = []
        self.closed = False

    def settimeout(self, timeout_seconds: float) -> None:
        """记录服务端为已接入连接设置的 socket 读超时。

        输入参数：
            timeout_seconds：防止 slowloris 长期占用 worker 的秒数。
        输出返回值：无。
        """

        self.timeouts.append(timeout_seconds)

    def shutdown(self, how: int) -> None:
        """记录过载连接被 server 主动 shutdown。

        输入参数：
            how：``socket.SHUT_WR`` 等关闭模式。
        输出返回值：无。
        """

        self.shutdown_calls.append(how)

    def close(self) -> None:
        """记录 socket 已关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.closed = True


class _ApplicationTransport:
    """将已冻结客户端请求直接送入服务 application 边界。"""

    def __init__(self, application: Any) -> None:
        """保存待集成验证的服务 application。

        输入参数：
            application：实现 ``handle_request`` 的租约服务。
        输出返回值：
            无。
        """

        self._application = application

    def post_json(
        self,
        *,
        action: str,
        payload: Mapping[str, object],
        credential: str,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        """以客户端 transport 契约调用服务并解析 JSON。

        输入参数：
            action/payload/credential：已冻结客户端生成的 wire 请求。
            timeout_seconds/max_response_bytes：客户端资源上限，本地适配器仅验证响应长度。
        输出返回值：
            服务器产生的严格 JSON mapping。
        """

        del timeout_seconds
        response = self._application.handle_request(
            method="POST",
            path=f"/v1/leases/{action}",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {credential}",
            },
            body=json.dumps(dict(payload)).encode("utf-8"),
        )
        if response.status != 200 or len(response.body) > max_response_bytes:
            raise RuntimeError("synthetic transport failure")
        decoded = json.loads(response.body)
        if not isinstance(decoded, dict):
            raise RuntimeError("synthetic transport failure")
        return decoded


class _DropFirstCommittedResponseTransport(_ApplicationTransport):
    """模拟服务已提交、但首个成功响应在传输层丢失。"""

    def __init__(self, application: Any, *, action: str) -> None:
        """绑定服务边界与需丢失首次响应的 action。

        输入参数：
            application：实际执行 SQLite 事务的 HTTP application。
            action：``acquire``、``assert-held`` 或 ``release``。
        输出返回值：
            无；构造后仅丢失目标 action 的第一个成功响应。
        """

        super().__init__(application)
        self._drop_action = action
        self._dropped = False
        self.calls: list[str] = []

    def post_json(
        self,
        *,
        action: str,
        payload: Mapping[str, object],
        credential: str,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> Mapping[str, Any]:
        """先提交服务事务，再对首个目标响应注入丢失。

        输入参数：
            action/payload/credential：客户端的完整 wire request。
            timeout_seconds/max_response_bytes：客户端资源上限。
        输出返回值：
            未丢失时返回服务端 JSON；首次目标响应抛合成传输错误。
        """

        self.calls.append(action)
        response = super().post_json(
            action=action,
            payload=payload,
            credential=credential,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        if action == self._drop_action and not self._dropped:
            self._dropped = True
            raise RuntimeError("synthetic committed response lost")
        return response


def _acquire_from_child_process(
    database_path: str,
    owner_id: str,
    start_event: Any,
    result_queue: Any,
) -> None:
    """在独立 Python 进程中竞争同一 namespace。

    输入参数：
        database_path：两个进程共享的 SQLite 文件。
        owner_id：当前子进程稳定身份。
        start_event：用于同时放行两个竞争者的跨进程事件。
        result_queue：回传 acquired 或稳定错误码的队列。
    输出返回值：
        无；结果写入 queue。
    """

    coordinator = SQLiteLeaseCoordinator(database_path)
    if not start_event.wait(timeout=10):
        result_queue.put(("timeout", 0))
        return
    try:
        grant = coordinator.acquire(
            namespace="webmall.four-stores.v1",
            attempt_id=f"attempt-{owner_id}",
            owner_id=owner_id,
            ttl_seconds=60,
        )
    except LeaseCoordinatorError as error:
        result_queue.put((error.code, 0))
    else:
        result_queue.put(("acquired", grant.fencing_token))


def test_acquire_exclusively_owns_namespace(tmp_path: Path) -> None:
    """验证一个 namespace 同时只能有一个权威持有者。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；首个 Attempt 获得租约，第二个 Attempt 收到固定冲突。
    """

    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )

    grant = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )

    assert grant.namespace == "webmall.four-stores.v1"
    assert grant.attempt_id == "attempt-001"
    assert grant.owner_id == "worker-001"
    assert grant.fencing_token == 1
    assert grant.expires_at_unix_ms == 1_800_000_060_000
    with pytest.raises(LeaseCoordinatorError) as captured:
        coordinator.acquire(
            namespace="webmall.four-stores.v1",
            attempt_id="attempt-002",
            owner_id="worker-002",
            ttl_seconds=60,
        )
    assert captured.value.code == "namespace_busy"
    assert str(captured.value) == "lease namespace 已被占用"


def test_same_acquire_identity_is_idempotent_and_renews_original_grant(
    tmp_path: Path,
) -> None:
    """验证 acquire 响应丢失后重试不会创建第二个 grant。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；相同 namespace/Attempt/owner 在未过期时返回原
        lease ID 和 fencing token，并按原 grant TTL 续期。
    """

    clock = _Clock()
    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=clock,
    )
    first = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )
    clock.now_ms += 50_000

    retried = coordinator.acquire(
        namespace=first.namespace,
        attempt_id=first.attempt_id,
        owner_id=first.owner_id,
        ttl_seconds=7_200,
    )

    assert retried.lease_id == first.lease_id
    assert retried.fencing_token == first.fencing_token
    assert retried.expires_at_unix_ms == clock.now_ms + 60_000


def test_two_processes_cannot_both_acquire_same_namespace(
    tmp_path: Path,
) -> None:
    """验证 ``BEGIN IMMEDIATE`` 在真实独立进程间保持 namespace 单占。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；两个竞争进程中恰好一个成功、一个 busy。
    """

    database_path = tmp_path / "leases.sqlite3"
    SQLiteLeaseCoordinator(database_path)
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_acquire_from_child_process,
            args=(
                str(database_path),
                f"worker-{index}",
                start_event,
                result_queue,
            ),
        )
        for index in (1, 2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    results = [result_queue.get(timeout=5) for _ in processes]

    assert sorted(status for status, _ in results) == [
        "acquired",
        "namespace_busy",
    ]
    assert [token for status, token in results if status == "acquired"] == [1]


def test_expired_lease_can_be_reacquired_with_durable_fencing(
    tmp_path: Path,
) -> None:
    """验证 TTL 过期后可重新获取，且重启不重用 fencing token。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；新持有者获得更大 token 与新 lease ID。
    """

    database_path = tmp_path / "leases.sqlite3"
    clock = _Clock()
    first_coordinator = SQLiteLeaseCoordinator(
        database_path,
        clock_ms=clock,
    )
    first = first_coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=1,
    )
    clock.now_ms += 1_000

    restarted_coordinator = SQLiteLeaseCoordinator(
        database_path,
        clock_ms=clock,
    )
    second = restarted_coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-002",
        owner_id="worker-002",
        ttl_seconds=1,
    )

    assert second.fencing_token == first.fencing_token + 1
    assert second.lease_id != first.lease_id


def test_assert_held_requires_complete_current_fenced_identity(
    tmp_path: Path,
) -> None:
    """验证 assert-held 只接受当前 Attempt/owner/lease/fencing 闭包。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；完整身份返回当前租约，旧 token 被稳定拒绝。
    """

    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )
    grant = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )

    held = coordinator.assert_held(
        namespace=grant.namespace,
        attempt_id=grant.attempt_id,
        owner_id=grant.owner_id,
        lease_id=grant.lease_id,
        fencing_token=grant.fencing_token,
    )

    assert held == grant
    with pytest.raises(LeaseCoordinatorError) as captured:
        coordinator.assert_held(
            namespace=grant.namespace,
            attempt_id=grant.attempt_id,
            owner_id=grant.owner_id,
            lease_id=grant.lease_id,
            fencing_token=grant.fencing_token + 1,
        )
    assert captured.value.code == "lease_not_held"
    assert str(captured.value) == "lease 未由该身份持有"


def test_assert_held_atomically_renews_using_the_grants_original_ttl(
    tmp_path: Path,
) -> None:
    """验证 ownership 复核会按 grant 原 TTL 原子续期。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；在首次到期前复核后，新到期时间为
        ``now + ttl``，而不是仅返回旧到期时间。
    """

    clock = _Clock()
    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=clock,
    )
    grant = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )
    clock.now_ms += 50_000

    renewed = coordinator.assert_held(
        namespace=grant.namespace,
        attempt_id=grant.attempt_id,
        owner_id=grant.owner_id,
        lease_id=grant.lease_id,
        fencing_token=grant.fencing_token,
    )

    assert renewed.expires_at_unix_ms == clock.now_ms + 60_000
    assert renewed.expires_at_unix_ms > grant.expires_at_unix_ms


def test_assert_held_reads_clock_only_after_obtaining_sqlite_write_lock(
    tmp_path: Path,
) -> None:
    """验证等待 SQLite 写锁期间过期的 grant 不会被旧时间续命。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；assert-held 在其他事务占用写锁时等待，放行时以
        新时间发现 grant 已过期并返回 ``lease_not_held``。
    """

    database_path = tmp_path / "leases.sqlite3"
    clock = _TransactionRaceClock()
    coordinator = SQLiteLeaseCoordinator(database_path, clock_ms=clock)
    grant = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=1,
    )
    blocker = sqlite3.connect(database_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    outcomes: list[str] = []

    def assert_in_worker() -> None:
        """在独立线程中执行可能等待 SQLite 锁的 ownership 复核。

        输入参数：无。
        输出返回值：
            无；将 held 或脱敏错误码追加到 ``outcomes``。
        """

        try:
            coordinator.assert_held(
                namespace=grant.namespace,
                attempt_id=grant.attempt_id,
                owner_id=grant.owner_id,
                lease_id=grant.lease_id,
                fencing_token=grant.fencing_token,
            )
        except LeaseCoordinatorError as error:
            outcomes.append(error.code)
        else:
            outcomes.append("held")

    worker = threading.Thread(target=assert_in_worker)
    worker.start()
    clock.second_call_entered.wait(timeout=0.2)
    clock.now_ms += 1_000
    clock.allow_second_call_to_return.set()
    blocker.execute("ROLLBACK")
    blocker.close()
    worker.join(timeout=5)

    assert worker.is_alive() is False
    assert outcomes == ["lease_not_held"]


def test_existing_database_schema_is_migrated_without_resetting_fencing(
    tmp_path: Path,
) -> None:
    """验证不含 TTL 列的已有数据库可安全就地升级。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；升级后旧的未过期 grant 仍可复核和续期，且
        持久化 fencing 高水位不被重置。
    """

    database_path = tmp_path / "legacy-leases.sqlite3"
    clock = _Clock()
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE lease_metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                last_fencing_token INTEGER NOT NULL
            );
            INSERT INTO lease_metadata VALUES (1, 7);
            CREATE TABLE active_leases (
                namespace TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                lease_id TEXT NOT NULL UNIQUE,
                fencing_token INTEGER NOT NULL UNIQUE,
                expires_at_unix_ms INTEGER NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO active_leases VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "webmall.four-stores.v1",
                "attempt-legacy",
                "worker-legacy",
                "a" * 64,
                7,
                clock.now_ms + 60_000,
            ),
        )

    coordinator = SQLiteLeaseCoordinator(database_path, clock_ms=clock)
    renewed = coordinator.assert_held(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-legacy",
        owner_id="worker-legacy",
        lease_id="a" * 64,
        fencing_token=7,
    )
    coordinator.release(
        namespace=renewed.namespace,
        attempt_id=renewed.attempt_id,
        owner_id=renewed.owner_id,
        lease_id=renewed.lease_id,
        fencing_token=renewed.fencing_token,
    )
    next_grant = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-next",
        owner_id="worker-next",
        ttl_seconds=60,
    )

    assert renewed.expires_at_unix_ms == clock.now_ms + 60_000
    assert next_grant.fencing_token == 8


def test_release_requires_fenced_identity_and_never_reuses_token(
    tmp_path: Path,
) -> None:
    """验证 release 仅删除完整匹配的租约且不回收 token。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；释放后原身份失效，下一个租约使用更大 token。
    """

    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )
    first = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )

    coordinator.release(
        namespace=first.namespace,
        attempt_id=first.attempt_id,
        owner_id=first.owner_id,
        lease_id=first.lease_id,
        fencing_token=first.fencing_token,
    )
    with pytest.raises(LeaseCoordinatorError) as captured:
        coordinator.assert_held(
            namespace=first.namespace,
            attempt_id=first.attempt_id,
            owner_id=first.owner_id,
            lease_id=first.lease_id,
            fencing_token=first.fencing_token,
        )
    assert captured.value.code == "lease_not_held"

    second = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-002",
        owner_id="worker-002",
        ttl_seconds=60,
    )
    assert second.fencing_token == first.fencing_token + 1


def test_release_retry_is_idempotent_without_deleting_a_new_holder(
    tmp_path: Path,
) -> None:
    """验证已提交 release 可重试且绝不会删除后续 holder。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；首次 release 响应丢失后重试仍成功，即使新
        holder 已取得更大 fencing token，旧重试也不影响新 grant。
    """

    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )
    released = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )
    coordinator.release(
        namespace=released.namespace,
        attempt_id=released.attempt_id,
        owner_id=released.owner_id,
        lease_id=released.lease_id,
        fencing_token=released.fencing_token,
    )
    current = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-002",
        owner_id="worker-002",
        ttl_seconds=60,
    )

    coordinator.release(
        namespace=released.namespace,
        attempt_id=released.attempt_id,
        owner_id=released.owner_id,
        lease_id=released.lease_id,
        fencing_token=released.fencing_token,
    )

    held = coordinator.assert_held(
        namespace=current.namespace,
        attempt_id=current.attempt_id,
        owner_id=current.owner_id,
        lease_id=current.lease_id,
        fencing_token=current.fencing_token,
    )
    assert held.lease_id == current.lease_id
    assert held.fencing_token > released.fencing_token


def test_expired_holder_cannot_release_reacquired_namespace(
    tmp_path: Path,
) -> None:
    """验证过期 holder 无法以旧 fencing 身份删除新租约。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
    输出返回值：
        无；旧 release 失败后新 holder 仍通过 assert-held。
    """

    clock = _Clock()
    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=clock,
    )
    stale = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=1,
    )
    clock.now_ms += 1_000
    current = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-002",
        owner_id="worker-002",
        ttl_seconds=7_200,
    )

    with pytest.raises(LeaseCoordinatorError) as captured:
        coordinator.release(
            namespace=stale.namespace,
            attempt_id=stale.attempt_id,
            owner_id=stale.owner_id,
            lease_id=stale.lease_id,
            fencing_token=stale.fencing_token,
        )
    assert captured.value.code == "lease_not_held"
    assert (
        coordinator.assert_held(
            namespace=current.namespace,
            attempt_id=current.attempt_id,
            owner_id=current.owner_id,
            lease_id=current.lease_id,
            fencing_token=current.fencing_token,
        )
        == current
    )


def test_storage_failure_does_not_expose_database_path(
    tmp_path: Path,
) -> None:
    """验证 SQLite 打开失败时仅返回固定存储错误。

    输入参数：
        tmp_path：故意作为 database file 传入的目录路径。
    输出返回值：
        无；异常不包含实际路径或 SQLite 原始文本。
    """

    with pytest.raises(LeaseCoordinatorError) as captured:
        SQLiteLeaseCoordinator(tmp_path)

    assert captured.value.code == "storage_unavailable"
    assert str(captured.value) == "lease storage 不可用"
    assert str(tmp_path) not in str(captured.value)


def test_http_rejects_wrong_bearer_with_closed_non_sensitive_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 HTTP 边界以环境 Bearer 认证且错误不回显请求。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：仅在当前测试设置服务凭据环境变量。
    输出返回值：
        无；返回固定 401 JSON，不含 token 或 payload 字段值。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    coordinator = SQLiteLeaseCoordinator(tmp_path / "leases.sqlite3")
    payload = {
        "protocol_id": LEASE_PROTOCOL_ID,
        "namespace": "sensitive-namespace",
        "attempt_id": "sensitive-attempt",
        "owner_id": "sensitive-owner",
        "ttl_seconds": 60,
    }

    application = create_lease_http_application(coordinator)
    http_response = application.handle_request(
        method="POST",
        path="/v1/leases/acquire",
        headers={
            "Content-Type": "application/json",
            "Authorization": ("Bearer wrong-secret-with-at-least-thirty-two-bytes"),
        },
        body=json.dumps(payload).encode("utf-8"),
    )
    response = json.loads(http_response.body)

    assert http_response.status == 401
    assert response == {
        "protocol_id": LEASE_PROTOCOL_ID,
        "ok": False,
        "error": {
            "code": "unauthorized",
            "message": "authorization failed",
        },
    }
    assert secret.encode("utf-8") not in http_response.body
    assert b"sensitive-namespace" not in http_response.body


def test_http_startup_requires_environment_credential_without_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证服务不接受缺失的环境凭据且错误不回显变量名。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：确保当前进程不存在服务凭据。
    输出返回值：
        无；启动 fail closed，对外仅有固定错误。
    """

    environment_name = "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN"
    monkeypatch.delenv(environment_name, raising=False)
    coordinator = SQLiteLeaseCoordinator(tmp_path / "leases.sqlite3")

    with pytest.raises(LeaseCoordinatorError) as captured:
        create_lease_http_application(coordinator)

    assert captured.value.code == "credential_unavailable"
    assert str(captured.value) == "lease credential 不可用"
    assert environment_name not in str(captured.value)


@pytest.mark.parametrize(
    "credential",
    [
        "A" * 31,
        "A" * 4_090,
        "A" * 31 + "=",
    ],
)
def test_http_startup_uses_the_same_bearer_contract_as_the_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    credential: str,
) -> None:
    """验证服务端启动阶段与客户端共用同一 token 闭包。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：注入仅对当前测试可见的合成凭据。
        credential：客户端也会拒绝的过短、过长或非 base64url 值。
    输出返回值：
        无；服务不会进入“可启动但所有客户请求必定
        401”的不一致状态。
    """

    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        credential,
    )

    with pytest.raises(LeaseCoordinatorError) as captured:
        create_lease_http_application(
            SQLiteLeaseCoordinator(tmp_path / "leases.sqlite3")
        )

    assert captured.value.code == "credential_unavailable"


def test_maximum_shared_bearer_length_authenticates_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证计入 ``Bearer `` 前缀后恰好 4096 字符的凭据可用。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：注入 4089 字符的合成 base64url token。
    输出返回值：
        无；服务启动、Authorization 校验和客户端 acquire 均成功，
        证明上限不再出现启动成功但必定 401 的区间。
    """

    credential = "".join(("A" * 4_089,))
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        credential,
    )
    application = create_lease_http_application(
        SQLiteLeaseCoordinator(
            tmp_path / "leases.sqlite3",
            clock_ms=_Clock(),
        )
    )
    client = WebMallDistributedLeaseClient(
        coordinator_url="http://127.0.0.1:8765",
        credential=credential,
        namespace="webmall.four-stores.v1",
        ttl_seconds=60,
        attempt_id="attempt-001",
        owner_id="worker-001",
        transport=_ApplicationTransport(application),
    )

    assert client.acquire().fencing_token == 1


@pytest.mark.parametrize(
    "body",
    [
        (
            b'{"protocol_id":"'
            + LEASE_PROTOCOL_ID.encode("ascii")
            + b'","namespace":"webmall.four-stores.v1",'
            b'"attempt_id":"attempt-001","owner_id":"worker-001",'
            b'"ttl_seconds":60,"unexpected":"secret-value"}'
        ),
        (
            b'{"protocol_id":"'
            + LEASE_PROTOCOL_ID.encode("ascii")
            + b'","namespace":"webmall.four-stores.v1",'
            b'"namespace":"secret-value",'
            b'"attempt_id":"attempt-001","owner_id":"worker-001",'
            b'"ttl_seconds":60}'
        ),
    ],
)
def test_http_rejects_non_closed_json_without_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    """验证额外字段与重复 key 均被固定 JSON 错误拒绝。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置临时 Bearer 凭据。
        body：包含额外字段或重复 key 的原始 JSON。
    输出返回值：
        无；响应不含字段值或原始 body。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    application = create_lease_http_application(
        SQLiteLeaseCoordinator(tmp_path / "leases.sqlite3")
    )

    response = application.handle_request(
        method="POST",
        path="/v1/leases/acquire",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        body=body,
    )

    assert response.status == 400
    assert b"secret-value" not in response.body
    assert set(json.loads(response.body)) == {
        "protocol_id",
        "ok",
        "error",
    }


def test_http_rejects_oversized_body_before_json_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证已认证请求体超过固定上限时立即拒绝。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置临时 Bearer 凭据。
    输出返回值：
        无；返回 413 且不回显请求体。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    application = create_lease_http_application(
        SQLiteLeaseCoordinator(tmp_path / "leases.sqlite3")
    )
    body = b"sensitive" * 2_049

    response = application.handle_request(
        method="POST",
        path="/v1/leases/acquire",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        body=body,
    )

    assert response.status == 413
    assert b"sensitive" not in response.body


def test_http_acquire_returns_closed_fenced_lease_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 acquire endpoint 仅接收固定 v1 object 并返回闭合租约。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置临时服务 Bearer 环境变量。
    输出返回值：
        无；响应只含 protocol/result 和完整 fencing 身份。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )
    application = create_lease_http_application(coordinator)
    request = {
        "protocol_id": LEASE_PROTOCOL_ID,
        "namespace": "webmall.four-stores.v1",
        "attempt_id": "attempt-001",
        "owner_id": "worker-001",
        "ttl_seconds": 60,
    }

    http_response = application.handle_request(
        method="POST",
        path="/v1/leases/acquire",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        body=json.dumps(request).encode("utf-8"),
    )
    response = json.loads(http_response.body)

    assert http_response.status == 200
    assert response == {
        "protocol_id": LEASE_PROTOCOL_ID,
        "status": "acquired",
        "namespace": "webmall.four-stores.v1",
        "attempt_id": "attempt-001",
        "owner_id": "worker-001",
        "lease_id": response["lease_id"],
        "fencing_token": 1,
    }
    assert len(response["lease_id"]) == 64


def test_http_assert_held_returns_only_current_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 assert-held endpoint 核对完整 fencing 身份。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置临时服务 Bearer 环境变量。
    输出返回值：
        无；当前身份返回闭合 held 响应。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )
    grant = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )
    application = create_lease_http_application(coordinator)
    request = {
        "protocol_id": LEASE_PROTOCOL_ID,
        "namespace": grant.namespace,
        "attempt_id": grant.attempt_id,
        "owner_id": grant.owner_id,
        "lease_id": grant.lease_id,
        "fencing_token": grant.fencing_token,
    }

    http_response = application.handle_request(
        method="POST",
        path="/v1/leases/assert-held",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        body=json.dumps(request).encode("utf-8"),
    )

    assert http_response.status == 200
    assert json.loads(http_response.body) == {
        "protocol_id": LEASE_PROTOCOL_ID,
        "status": "held",
        "namespace": grant.namespace,
        "attempt_id": grant.attempt_id,
        "owner_id": grant.owner_id,
        "lease_id": grant.lease_id,
        "fencing_token": grant.fencing_token,
    }


def test_http_release_deletes_only_matching_fenced_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 release endpoint 在闭合响应后使当前身份失效。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置临时服务 Bearer 环境变量。
    输出返回值：
        无；返回固定 released 身份，随后 assert-held 失败。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )
    grant = coordinator.acquire(
        namespace="webmall.four-stores.v1",
        attempt_id="attempt-001",
        owner_id="worker-001",
        ttl_seconds=60,
    )
    application = create_lease_http_application(coordinator)
    request = {
        "protocol_id": LEASE_PROTOCOL_ID,
        "namespace": grant.namespace,
        "attempt_id": grant.attempt_id,
        "owner_id": grant.owner_id,
        "lease_id": grant.lease_id,
        "fencing_token": grant.fencing_token,
    }

    http_response = application.handle_request(
        method="POST",
        path="/v1/leases/release",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        body=json.dumps(request).encode("utf-8"),
    )

    assert http_response.status == 200
    assert json.loads(http_response.body) == {
        "protocol_id": LEASE_PROTOCOL_ID,
        "status": "released",
        "namespace": grant.namespace,
        "attempt_id": grant.attempt_id,
        "owner_id": grant.owner_id,
        "lease_id": grant.lease_id,
        "fencing_token": grant.fencing_token,
    }
    with pytest.raises(LeaseCoordinatorError) as captured:
        coordinator.assert_held(
            namespace=grant.namespace,
            attempt_id=grant.attempt_id,
            owner_id=grant.owner_id,
            lease_id=grant.lease_id,
            fencing_token=grant.fencing_token,
        )
    assert captured.value.code == "lease_not_held"


def test_frozen_distributed_lease_client_completes_server_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证已冻结客户端无需改造即可 acquire/assert/release。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置客户端与服务端共用的合成凭据。
    输出返回值：
        无；三个 wire action 均通过客户端严格 7 字段解析。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    coordinator = SQLiteLeaseCoordinator(
        tmp_path / "leases.sqlite3",
        clock_ms=_Clock(),
    )
    application = create_lease_http_application(coordinator)
    client = WebMallDistributedLeaseClient(
        coordinator_url="http://127.0.0.1:8765",
        credential=secret,
        namespace="webmall.four-stores.v1",
        ttl_seconds=7_200,
        attempt_id="attempt-001",
        owner_id="worker-001",
        transport=_ApplicationTransport(application),
    )

    acquired = client.acquire()
    assert client.assert_held() == acquired
    assert client.release() == acquired


def test_client_retries_acquire_after_server_commits_but_response_is_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 acquire 的不确定传输失败会安全重试一次。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置客户端与服务端共用的合成凭据。
    输出返回值：
        无；首次服务事务已提交但响应丢失后，重试返回
        同一 lease ID 与 token 1，而不会多分配 fencing token。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    application = create_lease_http_application(
        SQLiteLeaseCoordinator(
            tmp_path / "leases.sqlite3",
            clock_ms=_Clock(),
        )
    )
    transport = _DropFirstCommittedResponseTransport(
        application,
        action="acquire",
    )
    client = WebMallDistributedLeaseClient(
        coordinator_url="http://127.0.0.1:8765",
        credential=secret,
        namespace="webmall.four-stores.v1",
        ttl_seconds=60,
        attempt_id="attempt-001",
        owner_id="worker-001",
        transport=transport,
    )

    grant = client.acquire()

    assert grant.fencing_token == 1
    assert transport.calls == ["acquire", "acquire"]


def test_client_retries_assert_after_renewal_response_is_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 assert-held 已续期但响应丢失时可以重试。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置服务端合成 Bearer credential。
    输出返回值：
        无；客户端重试完整原 grant，两次续期均不改变
        lease ID 或 fencing token。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    transport = _DropFirstCommittedResponseTransport(
        create_lease_http_application(
            SQLiteLeaseCoordinator(
                tmp_path / "leases.sqlite3",
                clock_ms=_Clock(),
            )
        ),
        action="assert-held",
    )
    client = WebMallDistributedLeaseClient(
        coordinator_url="http://127.0.0.1:8765",
        credential=secret,
        namespace="webmall.four-stores.v1",
        ttl_seconds=60,
        attempt_id="attempt-001",
        owner_id="worker-001",
        transport=transport,
    )
    acquired = client.acquire()

    assert client.assert_held() == acquired
    assert transport.calls == ["acquire", "assert-held", "assert-held"]


def test_client_retries_release_after_tombstone_commit_response_is_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 release tombstone 已提交但响应丢失时可以重试。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置服务端合成 Bearer credential。
    输出返回值：
        无；第二次 release 由持久化 tombstone 确认，客户端才
        进入 released 状态。
    """

    secret = _TEST_BEARER_TOKEN
    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        secret,
    )
    transport = _DropFirstCommittedResponseTransport(
        create_lease_http_application(
            SQLiteLeaseCoordinator(
                tmp_path / "leases.sqlite3",
                clock_ms=_Clock(),
            )
        ),
        action="release",
    )
    client = WebMallDistributedLeaseClient(
        coordinator_url="http://127.0.0.1:8765",
        credential=secret,
        namespace="webmall.four-stores.v1",
        ttl_seconds=60,
        attempt_id="attempt-001",
        owner_id="worker-001",
        transport=transport,
    )
    acquired = client.acquire()

    assert client.release() == acquired
    assert transport.calls == ["acquire", "release", "release"]


def test_server_cli_defaults_to_loopback_and_has_no_token_argument() -> None:
    """验证 ``python -m`` 入口默认只绑定 loopback 且不接收 token。

    输入参数：
        无。
    输出返回值：
        无；默认 host/port/database 稳定，parser 不暴露 token 选项。
    """

    parser = build_lease_server_argument_parser()
    arguments = parser.parse_args([])
    help_text = parser.format_help()

    assert arguments.host == "127.0.0.1"
    assert arguments.port == 8765
    assert arguments.database == "webmall-lease.sqlite3"
    assert "--token" not in help_text
    assert "--credential" not in help_text


def test_server_cli_rejects_non_loopback_plaintext_bind() -> None:
    """验证 CLI 不能把持有 Bearer 的明文 HTTP 服务暴露到网络。

    输入参数：
        无。
    输出返回值：
        无；非 loopback ``--host`` 在建立 socket 前由 parser 拒绝。
    """

    parser = build_lease_server_argument_parser()

    with pytest.raises(SystemExit) as captured:
        parser.parse_args(["--host", "0.0.0.0"])

    assert captured.value.code == 2


def test_server_factory_rejects_non_loopback_plaintext_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证库级 server factory 也不能绕过 CLI 的 loopback 门禁。

    输入参数：
        tmp_path：pytest 隔离的 SQLite 目录。
        monkeypatch：设置不落盘的合成 Bearer 环境变量。
    输出返回值：
        无；factory 在建立 socket 前返回固定配置错误。
    """

    monkeypatch.setenv(
        "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN",
        "server-secret-with-at-least-thirty-two-bytes",
    )
    coordinator = SQLiteLeaseCoordinator(tmp_path / "leases.sqlite3")

    with pytest.raises(LeaseCoordinatorError) as captured:
        create_lease_http_server(
            coordinator,
            host="0.0.0.0",
            port=0,
        )

    assert captured.value.code == "invalid_configuration"


def test_bounded_server_sets_read_timeout_and_rejects_excess_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 slowloris 连接有 socket 读超时且不能无界创建线程。

    输入参数：
        monkeypatch：将标准库的真线程 dispatch 替换为不 bind socket
            的合成记录边界。
    输出返回值：
        无；首个连接在进入 worker 前设置超时，唯一 slot
        被占用时第二个连接直接关闭且不 dispatch。
    """

    server = object.__new__(lease_module.BoundedLeaseHTTPServer)
    server._request_slots = threading.BoundedSemaphore(1)
    server._request_read_timeout_seconds = 2.5
    dispatched: list[tuple[object, object]] = []

    def fake_dispatch(
        instance: ThreadingHTTPServer,
        request: object,
        client_address: object,
    ) -> None:
        """记录已通过入场门禁的请求，但不创建真实线程。

        输入参数：
            instance：待测服务实例。
            request/client_address：合成 socket 与客户端地址。
        输出返回值：无。
        """

        del instance
        dispatched.append((request, client_address))

    monkeypatch.setattr(
        ThreadingHTTPServer,
        "process_request",
        fake_dispatch,
    )
    admitted = _FakeSocket()
    rejected = _FakeSocket()

    server.process_request(admitted, ("127.0.0.1", 10001))
    server.process_request(rejected, ("127.0.0.1", 10002))

    assert admitted.timeouts == [2.5]
    assert dispatched == [(admitted, ("127.0.0.1", 10001))]
    assert rejected.closed is True
    assert len(rejected.shutdown_calls) == 1


def test_bounded_server_releases_slot_after_handler_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 handler 退出后并发 slot 始终在 ``finally`` 中归还。

    输入参数：
        monkeypatch：以不读 socket 的合成 handler 替换标准库边界。
    输出返回值：
        无；调用前已占用的唯一 slot 在 handler 返回后可再次
        非阻塞获取，避免长期容量泄漏。
    """

    server = object.__new__(lease_module.BoundedLeaseHTTPServer)
    server._request_slots = threading.BoundedSemaphore(1)
    assert server._request_slots.acquire(blocking=False) is True

    def fake_handler(
        instance: ThreadingHTTPServer,
        request: object,
        client_address: object,
    ) -> None:
        """模拟一个正常返回的标准库 handler 线程主体。

        输入参数：
            instance/request/client_address：标准库线程边界参数。
        输出返回值：无。
        """

        del instance, request, client_address

    monkeypatch.setattr(
        ThreadingHTTPServer,
        "process_request_thread",
        fake_handler,
    )

    server.process_request_thread(_FakeSocket(), ("127.0.0.1", 10001))

    assert server._request_slots.acquire(blocking=False) is True
