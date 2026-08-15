"""WebMall 四店证据的跨进程持久化租约协调器。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time
from typing import Any

from paraguibench.integrations.webmall.distributed_lease import (
    is_valid_lease_bearer_credential,
)

LEASE_PROTOCOL_ID = "paraguibench.webmall.distributed-lease.v1"
LEASE_BEARER_TOKEN_ENV = "PARAGUIBENCH_WEBMALL_LEASE_BEARER_TOKEN"
DEFAULT_LEASE_SERVER_HOST = "127.0.0.1"
DEFAULT_LEASE_SERVER_PORT = 8765
DEFAULT_LEASE_DATABASE = "webmall-lease.sqlite3"
DEFAULT_MAX_CONCURRENT_REQUESTS = 16
DEFAULT_REQUEST_READ_TIMEOUT_SECONDS = 10.0
MAX_HTTP_REQUEST_BYTES = 16 * 1024
MAX_HTTP_HEADER_BYTES = 16 * 1024
MAX_HTTP_RESPONSE_BYTES = 16 * 1024
_NAMESPACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_LEASE_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAX_TTL_SECONDS = 86_400


class LeaseCoordinatorError(RuntimeError):
    """表示租约请求被一个固定、不含敏感信息的错误拒绝。"""

    def __init__(self, code: str, message: str) -> None:
        """保存稳定错误码和对外消息。

        输入参数：
            code：不含请求值的稳定机器错误码。
            message：不含 token、请求体或数据库路径的固定消息。
        输出返回值：
            无。
        """

        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    """保存一个权威 namespace 租约及其 fencing 身份。"""

    namespace: str
    attempt_id: str
    owner_id: str
    lease_id: str
    fencing_token: int
    expires_at_unix_ms: int


def _system_clock_ms() -> int:
    """返回当前 Unix 毫秒。

    输入参数：
        无。
    输出返回值：
        由系统时钟转换的 Unix 毫秒整数。
    """

    return time.time_ns() // 1_000_000


def _validate_identity(value: str, field_name: str) -> str:
    """验证租约公开身份字段。

    输入参数：
        value：待验证的 namespace、Attempt 或 owner 身份。
        field_name：仅用于选择固定错误消息的字段名。
    输出返回值：
        经验证的原始字符串。
    异常：
        LeaseCoordinatorError：身份类型、长度或字符集不合法。
    """

    pattern = _NAMESPACE_PATTERN if field_name == "namespace" else _IDENTIFIER_PATTERN
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise LeaseCoordinatorError(
            "invalid_request",
            f"lease {field_name} 无效",
        )
    return value


class _SQLiteLeaseStorage:
    """用 SQLite 写事务实现跨进程 namespace 单占。"""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock_ms: Callable[[], int] = _system_clock_ms,
    ) -> None:
        """初始化持久化 schema，不预先占用任何 namespace。

        输入参数：
            database_path：协调器专用 SQLite 文件路径。
            clock_ms：返回 Unix 毫秒的时钟；测试可注入。
        输出返回值：
            无。
        异常：
            LeaseCoordinatorError：路径或 SQLite 持久化不可用。
        """

        if not isinstance(database_path, (str, Path)):
            raise LeaseCoordinatorError(
                "storage_unavailable",
                "lease storage 不可用",
            )
        if not callable(clock_ms):
            raise TypeError("clock_ms 必须可调用")
        self._database_path = str(database_path)
        self._clock_ms = clock_ms
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        """打开一个有界等待的独立 SQLite 连接。

        输入参数：
            无。
        输出返回值：
            启用显式事务和行访问的 SQLite 连接。
        异常：
            sqlite3.Error：由外层转换为固定存储错误。
        """

        connection = sqlite3.connect(
            self._database_path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        """创建单调 token 元数据和当前租约表。

        输入参数：
            无。
        输出返回值：
            无；schema 已持久化。
        异常：
            LeaseCoordinatorError：SQLite 不可用，对外不暴露路径。
        """

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS lease_metadata (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        last_fencing_token INTEGER NOT NULL
                            CHECK (last_fencing_token >= 0)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO lease_metadata (
                        singleton, last_fencing_token
                    ) VALUES (1, 0)
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS active_leases (
                        namespace TEXT PRIMARY KEY,
                        attempt_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        lease_id TEXT NOT NULL UNIQUE,
                        fencing_token INTEGER NOT NULL UNIQUE,
                        ttl_seconds INTEGER NOT NULL
                            CHECK (ttl_seconds BETWEEN 1 AND 86400),
                        expires_at_unix_ms INTEGER NOT NULL
                    )
                    """
                )
                self._migrate_active_lease_ttl(connection)
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS released_lease_tombstones (
                        namespace TEXT PRIMARY KEY,
                        attempt_id TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        lease_id TEXT NOT NULL,
                        fencing_token INTEGER NOT NULL
                            CHECK (fencing_token >= 1),
                        released_at_unix_ms INTEGER NOT NULL
                    )
                    """
                )
                connection.execute("COMMIT")
        except sqlite3.Error:
            raise LeaseCoordinatorError(
                "storage_unavailable",
                "lease storage 不可用",
            ) from None

    def _migrate_active_lease_ttl(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """为旧版 ``active_leases`` 表就地补齐 grant TTL。

        输入参数：
            connection：已进入 ``BEGIN IMMEDIATE`` 的 schema 初始化
                连接。
        输出返回值：
            无；旧表新增 ``ttl_seconds``，并按升级时的剩余
            有效期推导 1 至 86400 秒的保守 TTL。
        异常：
            LeaseCoordinatorError：注入时钟无法用于安全迁移。
            sqlite3.Error：交由外层转为脱敏存储错误。
        """

        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(active_leases)").fetchall()
        }
        if "ttl_seconds" in columns:
            return
        now_ms = self._clock_ms()
        if not isinstance(now_ms, int) or isinstance(now_ms, bool):
            raise LeaseCoordinatorError(
                "clock_unavailable",
                "lease clock 不可用",
            )
        connection.execute("ALTER TABLE active_leases ADD COLUMN ttl_seconds INTEGER")
        connection.execute(
            """
            UPDATE active_leases
            SET ttl_seconds = CASE
                WHEN expires_at_unix_ms <= ? THEN 1
                WHEN expires_at_unix_ms - ? >= ? THEN 86400
                ELSE (expires_at_unix_ms - ? + 999) / 1000
            END
            """,
            (
                now_ms,
                now_ms,
                _MAX_TTL_SECONDS * 1000,
                now_ms,
            ),
        )
        invalid = connection.execute(
            """
            SELECT 1 FROM active_leases
            WHERE ttl_seconds IS NULL OR ttl_seconds < 1
               OR ttl_seconds > 86400
            LIMIT 1
            """
        ).fetchone()
        if invalid is not None:
            raise sqlite3.DatabaseError("invalid migrated lease ttl")


def _encode_closed_json(payload: dict[str, object]) -> bytes:
    """将闭合响应序列化为有界 UTF-8 JSON。

    输入参数：
        payload：服务端以固定字段构造的 JSON object。
    输出返回值：
        不含多余空白的有界 UTF-8 响应。
    异常：
        LeaseCoordinatorError：内部响应超过固定上限。
    """

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_HTTP_RESPONSE_BYTES:
        raise LeaseCoordinatorError(
            "internal_error",
            "lease response 不可用",
        )
    return encoded


def _error_payload(code: str, message: str) -> dict[str, object]:
    """构造不回显请求或凭据的固定错误对象。

    输入参数：
        code：由服务端选择的稳定错误码。
        message：由服务端选择的固定安全消息。
    输出返回值：
        distributed-lease v1 闭合错误 JSON object。
    """

    return {
        "protocol_id": LEASE_PROTOCOL_ID,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _closed_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """构造不允许重复 key 的 JSON object。

    输入参数：
        pairs：JSON decoder 按原始顺序提供的 key/value 列表。
    输出返回值：
        key 唯一的 dict。
    异常：
        ValueError：请求中存在重复 key。
    """

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    """拒绝 JSON 标准之外的 NaN 和 Infinity 常量。

    输入参数：
        value：Python JSON decoder 识别的非标准常量名。
    输出返回值：
        不返回。
    异常：
        ValueError：始终拒绝非标准常量。
    """

    del value
    raise ValueError("invalid JSON constant")


def _decode_request_object(
    body: bytes,
    expected_keys: frozenset[str],
) -> dict[str, object]:
    """解析一个字段集必须精确匹配的有界 JSON object。

    输入参数：
        body：已经 HTTP 长度门禁的 UTF-8 请求体。
        expected_keys：endpoint 允许且必需的闭合 key 集合。
    输出返回值：
        key 无缺失、无额外、无重复的 JSON object。
    异常：
        LeaseCoordinatorError：编码、JSON 或字段闭包无效。
    """

    try:
        decoded = json.loads(
            body.decode("utf-8", errors="strict"),
            object_pairs_hook=_closed_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise LeaseCoordinatorError(
            "invalid_request",
            "lease request JSON 无效",
        ) from None
    if not isinstance(decoded, dict) or frozenset(decoded) != expected_keys:
        raise LeaseCoordinatorError(
            "invalid_request",
            "lease request fields 无效",
        )
    return decoded


def _lease_payload(grant: LeaseGrant) -> dict[str, object]:
    """将租约 DTO 转换为 v1 响应的闭合 JSON object。

    输入参数：
        grant：SQLite 协调器返回的权威租约。
    输出返回值：
        仅含 Attempt/owner/lease/fencing 身份与到期时间的 object。
    """

    return {
        "namespace": grant.namespace,
        "attempt_id": grant.attempt_id,
        "owner_id": grant.owner_id,
        "lease_id": grant.lease_id,
        "fencing_token": grant.fencing_token,
        "expires_at_unix_ms": grant.expires_at_unix_ms,
    }


def _lease_wire_payload(
    *,
    status: str,
    namespace: object,
    attempt_id: object,
    owner_id: object,
    lease_id: object,
    fencing_token: object,
) -> dict[str, object]:
    """构造与 WebMall 已冻结客户端一致的平铺 7 字段响应。

    输入参数：
        status：``acquired``、``held`` 或 ``released``。
        namespace/attempt_id/owner_id/lease_id/fencing_token：已由
            SQLite 操作验证的完整 fencing 身份。
    输出返回值：
        客户端 ``_LEASE_RESPONSE_FIELDS`` 严格接受的 object。
    """

    return {
        "protocol_id": LEASE_PROTOCOL_ID,
        "status": status,
        "namespace": namespace,
        "lease_id": lease_id,
        "attempt_id": attempt_id,
        "owner_id": owner_id,
        "fencing_token": fencing_token,
    }


@dataclass(frozen=True, slots=True)
class LeaseHTTPResponse:
    """保存 HTTP adapter 将要写回的有界闭合响应。"""

    status: int
    body: bytes


def _read_bearer_credential(environment_name: str) -> bytes:
    """仅从指定环境变量读取服务 Bearer credential。

    输入参数：
        environment_name：凭据环境变量名，而非凭据值。
    输出返回值：
        长度 32 至 4089 字节的无填充 base64url credential。
    异常：
        LeaseCoordinatorError：环境名或 credential 不可用。
    """

    if not isinstance(environment_name, str) or not environment_name:
        raise LeaseCoordinatorError(
            "credential_unavailable",
            "lease credential 不可用",
        )
    credential = os.environ.get(environment_name)
    if not isinstance(credential, str):
        raise LeaseCoordinatorError(
            "credential_unavailable",
            "lease credential 不可用",
        )
    if not is_valid_lease_bearer_credential(credential):
        raise LeaseCoordinatorError(
            "credential_unavailable",
            "lease credential 不可用",
        )
    return credential.encode("ascii")


class LeaseHTTPApplication:
    """在无 socket 状态下实现可测试的 distributed-lease HTTP 边界。"""

    def __init__(
        self,
        coordinator: SQLiteLeaseCoordinator,
        expected_bearer_token: bytes,
    ) -> None:
        """绑定协调器与启动时读取的内存凭据。

        输入参数：
            coordinator：SQLite 权威租约协调器。
            expected_bearer_token：仅从环境读取的 credential 字节。
        输出返回值：
            无。
        """

        self._coordinator = coordinator
        self._expected_bearer_token = expected_bearer_token

    def handle_request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> LeaseHTTPResponse:
        """验证一个有界 HTTP 请求并返回闭合 JSON 响应。

        输入参数：
            method：HTTP method。
            path：不含 host 的 endpoint 路径。
            headers：已解析的请求 header mapping。
            body：尚未解析的有界请求字节。
        输出返回值：
            status 与有界 JSON body；不回显凭据或请求值。
        """

        if not isinstance(method, str) or not isinstance(path, str):
            return LeaseHTTPResponse(
                status=400,
                body=_encode_closed_json(
                    _error_payload("invalid_request", "request invalid")
                ),
            )
        if not isinstance(headers, Mapping):
            return LeaseHTTPResponse(
                status=400,
                body=_encode_closed_json(
                    _error_payload("invalid_request", "request invalid")
                ),
            )
        normalized_headers: dict[str, str] = {}
        header_bytes = 0
        for raw_name, raw_value in headers.items():
            if not isinstance(raw_name, str) or not isinstance(raw_value, str):
                return LeaseHTTPResponse(
                    status=400,
                    body=_encode_closed_json(
                        _error_payload("invalid_request", "request invalid")
                    ),
                )
            header_bytes += len(raw_name.encode("utf-8"))
            header_bytes += len(raw_value.encode("utf-8"))
            normalized_headers[raw_name.lower()] = raw_value
        if header_bytes > MAX_HTTP_HEADER_BYTES:
            return LeaseHTTPResponse(
                status=431,
                body=_encode_closed_json(
                    _error_payload(
                        "headers_too_large",
                        "request headers too large",
                    )
                ),
            )
        authorization = normalized_headers.get("authorization", "")
        if (
            not isinstance(authorization, str)
            or len(authorization) > 4096
            or not authorization.startswith("Bearer ")
        ):
            candidate = b""
        else:
            try:
                candidate = authorization[7:].encode(
                    "utf-8",
                    errors="strict",
                )
            except UnicodeError:
                candidate = b""
        if not hmac.compare_digest(
            candidate,
            self._expected_bearer_token,
        ):
            return LeaseHTTPResponse(
                status=401,
                body=_encode_closed_json(
                    _error_payload(
                        "unauthorized",
                        "authorization failed",
                    )
                ),
            )
        if not isinstance(body, bytes):
            return LeaseHTTPResponse(
                status=400,
                body=_encode_closed_json(
                    _error_payload("invalid_request", "request invalid")
                ),
            )
        if len(body) > MAX_HTTP_REQUEST_BYTES:
            return LeaseHTTPResponse(
                status=413,
                body=_encode_closed_json(
                    _error_payload(
                        "request_too_large",
                        "request body too large",
                    )
                ),
            )
        if method != "POST":
            return LeaseHTTPResponse(
                status=405,
                body=_encode_closed_json(
                    _error_payload(
                        "method_not_allowed",
                        "request method not allowed",
                    )
                ),
            )
        content_type = normalized_headers.get("content-type", "")
        if content_type.lower() not in {
            "application/json",
            "application/json; charset=utf-8",
        }:
            return LeaseHTTPResponse(
                status=415,
                body=_encode_closed_json(
                    _error_payload(
                        "unsupported_media_type",
                        "content type must be application/json",
                    )
                ),
            )
        if path not in {
            "/v1/leases/acquire",
            "/v1/leases/assert-held",
            "/v1/leases/release",
        }:
            return LeaseHTTPResponse(
                status=404,
                body=_encode_closed_json(
                    _error_payload("not_found", "lease endpoint not found")
                ),
            )
        if path == "/v1/leases/assert-held":
            try:
                request = _decode_request_object(
                    body,
                    frozenset(
                        {
                            "protocol_id",
                            "namespace",
                            "attempt_id",
                            "owner_id",
                            "lease_id",
                            "fencing_token",
                        }
                    ),
                )
                if request["protocol_id"] != LEASE_PROTOCOL_ID:
                    raise LeaseCoordinatorError(
                        "invalid_request",
                        "lease protocol_id 无效",
                    )
                grant = self._coordinator.assert_held(
                    namespace=request["namespace"],
                    attempt_id=request["attempt_id"],
                    owner_id=request["owner_id"],
                    lease_id=request["lease_id"],
                    fencing_token=request["fencing_token"],
                )
            except LeaseCoordinatorError as error:
                status_by_code = {
                    "invalid_request": 400,
                    "lease_not_held": 409,
                    "clock_unavailable": 503,
                    "storage_unavailable": 503,
                }
                return LeaseHTTPResponse(
                    status=status_by_code.get(error.code, 500),
                    body=_encode_closed_json(_error_payload(error.code, str(error))),
                )
            return LeaseHTTPResponse(
                status=200,
                body=_encode_closed_json(
                    _lease_wire_payload(
                        status="held",
                        namespace=grant.namespace,
                        attempt_id=grant.attempt_id,
                        owner_id=grant.owner_id,
                        lease_id=grant.lease_id,
                        fencing_token=grant.fencing_token,
                    )
                ),
            )
        if path == "/v1/leases/release":
            try:
                request = _decode_request_object(
                    body,
                    frozenset(
                        {
                            "protocol_id",
                            "namespace",
                            "attempt_id",
                            "owner_id",
                            "lease_id",
                            "fencing_token",
                        }
                    ),
                )
                if request["protocol_id"] != LEASE_PROTOCOL_ID:
                    raise LeaseCoordinatorError(
                        "invalid_request",
                        "lease protocol_id 无效",
                    )
                self._coordinator.release(
                    namespace=request["namespace"],
                    attempt_id=request["attempt_id"],
                    owner_id=request["owner_id"],
                    lease_id=request["lease_id"],
                    fencing_token=request["fencing_token"],
                )
            except LeaseCoordinatorError as error:
                status_by_code = {
                    "invalid_request": 400,
                    "lease_not_held": 409,
                    "clock_unavailable": 503,
                    "storage_unavailable": 503,
                }
                return LeaseHTTPResponse(
                    status=status_by_code.get(error.code, 500),
                    body=_encode_closed_json(_error_payload(error.code, str(error))),
                )
            return LeaseHTTPResponse(
                status=200,
                body=_encode_closed_json(
                    _lease_wire_payload(
                        status="released",
                        namespace=request["namespace"],
                        attempt_id=request["attempt_id"],
                        owner_id=request["owner_id"],
                        lease_id=request["lease_id"],
                        fencing_token=request["fencing_token"],
                    )
                ),
            )
        try:
            request = _decode_request_object(
                body,
                frozenset(
                    {
                        "protocol_id",
                        "namespace",
                        "attempt_id",
                        "owner_id",
                        "ttl_seconds",
                    }
                ),
            )
            if request["protocol_id"] != LEASE_PROTOCOL_ID:
                raise LeaseCoordinatorError(
                    "invalid_request",
                    "lease protocol_id 无效",
                )
            grant = self._coordinator.acquire(
                namespace=request["namespace"],
                attempt_id=request["attempt_id"],
                owner_id=request["owner_id"],
                ttl_seconds=request["ttl_seconds"],
            )
        except LeaseCoordinatorError as error:
            status_by_code = {
                "invalid_request": 400,
                "namespace_busy": 409,
                "clock_unavailable": 503,
                "storage_unavailable": 503,
            }
            return LeaseHTTPResponse(
                status=status_by_code.get(error.code, 500),
                body=_encode_closed_json(_error_payload(error.code, str(error))),
            )
        return LeaseHTTPResponse(
            status=200,
            body=_encode_closed_json(
                _lease_wire_payload(
                    status="acquired",
                    namespace=grant.namespace,
                    attempt_id=grant.attempt_id,
                    owner_id=grant.owner_id,
                    lease_id=grant.lease_id,
                    fencing_token=grant.fencing_token,
                )
            ),
        )


def create_lease_http_application(
    coordinator: SQLiteLeaseCoordinator,
    *,
    bearer_token_env: str = LEASE_BEARER_TOKEN_ENV,
) -> LeaseHTTPApplication:
    """从环境构造一个不接受明文 token 参数的 HTTP 应用。

    输入参数：
        coordinator：已初始化的 SQLite 权威协调器。
        bearer_token_env：凭据环境变量名，不是 credential 值。
    输出返回值：
        已冻结启动凭据的 HTTP application。
    """

    if not isinstance(coordinator, SQLiteLeaseCoordinator):
        raise TypeError("coordinator 类型无效")
    return LeaseHTTPApplication(
        coordinator,
        _read_bearer_credential(bearer_token_env),
    )


def _make_lease_handler(
    coordinator: SQLiteLeaseCoordinator,
    expected_bearer_token: bytes,
) -> type[BaseHTTPRequestHandler]:
    """为一个协调器和内存凭据生成 HTTP handler 类。

    输入参数：
        coordinator：服务请求共享的 SQLite 协调器。
        expected_bearer_token：仅在启动时从环境读取的凭据字节。
    输出返回值：
        关闭默认请求日志且输出闭合 JSON 的 handler 类。
    """

    application = LeaseHTTPApplication(
        coordinator,
        expected_bearer_token,
    )

    class LeaseHTTPRequestHandler(BaseHTTPRequestHandler):
        """将 v1 HTTP 请求适配到持久化协调器。"""

        server_version = "ParaGUIBenchLease"
        sys_version = ""

        def _write_payload(
            self,
            status: int,
            payload: dict[str, object],
        ) -> None:
            """写入固定 Content-Type 和有界 JSON 响应。

            输入参数：
                status：HTTP 状态码。
                payload：已由服务端构造的闭合 object。
            输出返回值：
                无；响应已写入 socket。
            """

            try:
                body = _encode_closed_json(payload)
            except LeaseCoordinatorError:
                status = 500
                body = (
                    b'{"error":{"code":"internal_error",'
                    b'"message":"lease response unavailable"},'
                    b'"ok":false,"protocol_id":"'
                    + LEASE_PROTOCOL_ID.encode("ascii")
                    + b'"}'
                )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _write_application_response(
            self,
            response: LeaseHTTPResponse,
        ) -> None:
            """将已有界的 application 响应写入 socket。

            输入参数：
                response：``LeaseHTTPApplication`` 返回的 status/body。
            输出返回值：
                无；写入 ``application/json`` 响应。
            """

            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(response.body)

        def _authorized(self) -> bool:
            """以 constant-time 比较验证 Bearer credential。

            输入参数：
                无；读取当前请求 Authorization header。
            输出返回值：
                header 严格匹配启动时环境 token 时返回 ``True``。
            """

            header = self.headers.get("Authorization")
            if not isinstance(header, str) or len(header) > 4096:
                candidate = b""
            elif header.startswith("Bearer "):
                try:
                    candidate = header[7:].encode("utf-8", errors="strict")
                except UnicodeError:
                    candidate = b""
            else:
                candidate = b""
            return hmac.compare_digest(candidate, expected_bearer_token)

        def do_POST(self) -> None:
            """处理需要 Bearer 认证的 v1 POST 请求。

            输入参数：
                无；从 handler 读取当前 HTTP 请求。
            输出返回值：
                无；当前纵向切片只建立认证门禁。
            """

            headers = dict(self.headers.items())
            if not self._authorized():
                self.close_connection = True
                response = application.handle_request(
                    method="POST",
                    path=self.path,
                    headers=headers,
                    body=b"",
                )
                self._write_application_response(response)
                return
            if self.headers.get("Transfer-Encoding") is not None:
                self.close_connection = True
                self._write_payload(
                    400,
                    _error_payload(
                        "invalid_request",
                        "request transfer encoding invalid",
                    ),
                )
                return
            content_lengths = self.headers.get_all("Content-Length", [])
            if len(content_lengths) != 1:
                self.close_connection = True
                self._write_payload(
                    411,
                    _error_payload(
                        "length_required",
                        "request content length required",
                    ),
                )
                return
            content_length_text = content_lengths[0]
            if not content_length_text.isascii() or not content_length_text.isdecimal():
                self.close_connection = True
                self._write_payload(
                    400,
                    _error_payload(
                        "invalid_request",
                        "request content length invalid",
                    ),
                )
                return
            content_length = int(content_length_text)
            if content_length > MAX_HTTP_REQUEST_BYTES:
                self.close_connection = True
                self._write_payload(
                    413,
                    _error_payload(
                        "request_too_large",
                        "request body too large",
                    ),
                )
                return
            body = self.rfile.read(content_length)
            if len(body) != content_length:
                self.close_connection = True
                self._write_payload(
                    400,
                    _error_payload(
                        "invalid_request",
                        "request body incomplete",
                    ),
                )
                return
            response = application.handle_request(
                method="POST",
                path=self.path,
                headers=headers,
                body=body,
            )
            self._write_application_response(response)

        def send_error(
            self,
            code: int,
            message: str | None = None,
            explain: str | None = None,
        ) -> None:
            """将基类协议错误改为不回显请求的 JSON。

            输入参数：
                code：基类解析器生成的 HTTP 状态码。
                message/explain：基类可能包含请求的文本；故意忽略。
            输出返回值：
                无；写入固定 protocol_error。
            """

            del message, explain
            self._write_payload(
                code,
                _error_payload("protocol_error", "invalid HTTP request"),
            )

        def log_message(self, format: str, *args: object) -> None:
            """禁用默认会记录请求行的 stderr 访问日志。

            输入参数：
                format/args：``BaseHTTPRequestHandler`` 提供的日志格式与值；全部忽略。
            输出返回值：
                无。
            """

            del format, args

    return LeaseHTTPRequestHandler


class BoundedLeaseHTTPServer(ThreadingHTTPServer):
    """为 loopback lease HTTP 连接提供 socket 超时与有界线程入场。"""

    daemon_threads = True
    block_on_close = True
    request_queue_size = DEFAULT_MAX_CONCURRENT_REQUESTS

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        *,
        max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
        request_read_timeout_seconds: float = (DEFAULT_REQUEST_READ_TIMEOUT_SECONDS),
    ) -> None:
        """在 bind 前验证资源上限并初始化非阻塞连接 slot。

        输入参数：
            server_address：已由工厂限定的 loopback host/port。
            request_handler_class：不记录请求值的 lease handler 类。
            max_concurrent_requests：同时允许的 handler 线程数，1 至 256。
            request_read_timeout_seconds：单连接 header/body 读等待上限，
                0.1 至 60 秒。
        输出返回值：
            无；成功后 socket 已 bind，但尚未进入 ``serve_forever``。
        异常：
            LeaseCoordinatorError：资源上限类型或范围无效。
            OSError：标准库 bind/listen 失败。
        """

        if (
            not isinstance(max_concurrent_requests, int)
            or isinstance(max_concurrent_requests, bool)
            or not 1 <= max_concurrent_requests <= 256
            or not isinstance(request_read_timeout_seconds, (int, float))
            or isinstance(request_read_timeout_seconds, bool)
            or not 0.1 <= float(request_read_timeout_seconds) <= 60
        ):
            raise LeaseCoordinatorError(
                "invalid_configuration",
                "lease server 配置无效",
            )
        self._request_slots = threading.BoundedSemaphore(max_concurrent_requests)
        self._request_read_timeout_seconds = float(request_read_timeout_seconds)
        super().__init__(server_address, request_handler_class)

    def process_request(self, request: Any, client_address: Any) -> None:
        """非阻塞接入一个连接，并在创建线程前设置读超时。

        输入参数：
            request：标准库 accept 的 socket-like 连接。
            client_address：仅交给基类 handler，不记录。
        输出返回值：
            无；有 slot 时交给线程基类，过载时立即关闭连接。
        """

        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(self._request_read_timeout_seconds)
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            try:
                self.shutdown_request(request)
            except OSError:
                pass
            raise

    def process_request_thread(
        self,
        request: Any,
        client_address: Any,
    ) -> None:
        """在 handler 无论成功或失败退出时归还唯一并发 slot。

        输入参数：
            request：已设 socket 读超时的入场连接。
            client_address：交给标准库 handler 的客户地址。
        输出返回值：
            无；基类清理完成后 slot 已归还。
        """

        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def create_lease_http_server(
    coordinator: SQLiteLeaseCoordinator,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    bearer_token_env: str = LEASE_BEARER_TOKEN_ENV,
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
    request_read_timeout_seconds: float = (DEFAULT_REQUEST_READ_TIMEOUT_SECONDS),
) -> BoundedLeaseHTTPServer:
    """从环境读取唯一凭据并创建默认 loopback HTTP 服务。

    输入参数：
        coordinator：已初始化的 SQLite 权威协调器。
        host：绑定地址，默认仅 ``127.0.0.1``。
        port：绑定端口，``0`` 可用于测试随机端口。
        bearer_token_env：凭据环境变量名；不接受 token 值参数。
        max_concurrent_requests：同时 handler 线程上限。
        request_read_timeout_seconds：单连接 header/body 读超时。
    输出返回值：
        尚未运行的有界 ``BoundedLeaseHTTPServer``。
    异常：
        LeaseCoordinatorError：凭据缺失、太短或太长。
    """

    if not isinstance(coordinator, SQLiteLeaseCoordinator):
        raise TypeError("coordinator 类型无效")
    if (
        host != DEFAULT_LEASE_SERVER_HOST
        or not isinstance(port, int)
        or isinstance(port, bool)
        or not 0 <= port <= 65_535
    ):
        raise LeaseCoordinatorError(
            "invalid_configuration",
            "lease server 配置无效",
        )
    credential_bytes = _read_bearer_credential(bearer_token_env)
    handler = _make_lease_handler(coordinator, credential_bytes)
    return BoundedLeaseHTTPServer(
        (host, port),
        handler,
        max_concurrent_requests=max_concurrent_requests,
        request_read_timeout_seconds=request_read_timeout_seconds,
    )


class _AcquiringSQLiteLeaseCoordinator(_SQLiteLeaseStorage):
    """将持久化存储封装为 distributed-lease v1 公开操作。"""

    def acquire(
        self,
        *,
        namespace: str,
        attempt_id: str,
        owner_id: str,
        ttl_seconds: int,
    ) -> LeaseGrant:
        """原子获取 namespace，并分配全局单调 fencing token。

        输入参数：
            namespace：要串行化的四店环境身份。
            attempt_id：benchmark Attempt 稳定身份。
            owner_id：当前调用进程或 worker 稳定身份。
            ttl_seconds：1 至 86400 秒的租约生存期。
        输出返回值：
            包含 lease ID、单调 token 和到期时间的不可变租约。
        异常：
            LeaseCoordinatorError：请求无效、namespace 未过期或存储不可用。
        """

        namespace = _validate_identity(namespace, "namespace")
        attempt_id = _validate_identity(attempt_id, "attempt_id")
        owner_id = _validate_identity(owner_id, "owner_id")
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS
        ):
            raise LeaseCoordinatorError(
                "invalid_request",
                "lease ttl_seconds 无效",
            )
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                now_ms = self._clock_ms()
                if not isinstance(now_ms, int) or isinstance(now_ms, bool):
                    connection.execute("ROLLBACK")
                    raise LeaseCoordinatorError(
                        "clock_unavailable",
                        "lease clock 不可用",
                    )
                current = connection.execute(
                    """
                    SELECT namespace, attempt_id, owner_id, lease_id,
                           fencing_token, ttl_seconds, expires_at_unix_ms
                    FROM active_leases WHERE namespace = ?
                    """,
                    (namespace,),
                ).fetchone()
                if current is not None and current["expires_at_unix_ms"] > now_ms:
                    if (
                        current["attempt_id"] != attempt_id
                        or current["owner_id"] != owner_id
                    ):
                        connection.execute("ROLLBACK")
                        raise LeaseCoordinatorError(
                            "namespace_busy",
                            "lease namespace 已被占用",
                        )
                    expires_at_ms = now_ms + int(current["ttl_seconds"]) * 1000
                    cursor = connection.execute(
                        """
                        UPDATE active_leases
                        SET expires_at_unix_ms = ?
                        WHERE namespace = ? AND attempt_id = ? AND owner_id = ?
                          AND lease_id = ? AND fencing_token = ?
                          AND expires_at_unix_ms > ?
                        """,
                        (
                            expires_at_ms,
                            namespace,
                            attempt_id,
                            owner_id,
                            current["lease_id"],
                            current["fencing_token"],
                            now_ms,
                        ),
                    )
                    if cursor.rowcount != 1:
                        connection.execute("ROLLBACK")
                        raise LeaseCoordinatorError(
                            "namespace_busy",
                            "lease namespace 已被占用",
                        )
                    connection.execute("COMMIT")
                    return LeaseGrant(
                        namespace=namespace,
                        attempt_id=attempt_id,
                        owner_id=owner_id,
                        lease_id=current["lease_id"],
                        fencing_token=current["fencing_token"],
                        expires_at_unix_ms=expires_at_ms,
                    )
                token_row = connection.execute(
                    """
                    UPDATE lease_metadata
                    SET last_fencing_token = last_fencing_token + 1
                    WHERE singleton = 1
                    RETURNING last_fencing_token
                    """
                ).fetchone()
                if token_row is None:
                    raise sqlite3.DatabaseError("missing metadata")
                fencing_token = int(token_row[0])
                lease_id = secrets.token_hex(32)
                expires_at_ms = now_ms + ttl_seconds * 1000
                connection.execute(
                    """
                    INSERT INTO active_leases (
                        namespace, attempt_id, owner_id, lease_id,
                        fencing_token, ttl_seconds, expires_at_unix_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(namespace) DO UPDATE SET
                        attempt_id = excluded.attempt_id,
                        owner_id = excluded.owner_id,
                        lease_id = excluded.lease_id,
                        fencing_token = excluded.fencing_token,
                        ttl_seconds = excluded.ttl_seconds,
                        expires_at_unix_ms = excluded.expires_at_unix_ms
                    """,
                    (
                        namespace,
                        attempt_id,
                        owner_id,
                        lease_id,
                        fencing_token,
                        ttl_seconds,
                        expires_at_ms,
                    ),
                )
                connection.execute("COMMIT")
            finally:
                connection.close()
        except LeaseCoordinatorError:
            raise
        except sqlite3.Error:
            raise LeaseCoordinatorError(
                "storage_unavailable",
                "lease storage 不可用",
            ) from None
        return LeaseGrant(
            namespace=namespace,
            attempt_id=attempt_id,
            owner_id=owner_id,
            lease_id=lease_id,
            fencing_token=fencing_token,
            expires_at_unix_ms=expires_at_ms,
        )


def _parse_tcp_port(value: str) -> int:
    """将 CLI 端口文本解析为有效 TCP 端口。

    输入参数：
        value：``argparse`` 提供的端口文本。
    输出返回值：
        1 至 65535 之间的端口整数。
    异常：
        argparse.ArgumentTypeError：文本不是规范十进制端口。
    """

    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise argparse.ArgumentTypeError("port 无效")
    port = int(value)
    if not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port 无效")
    return port


def _parse_loopback_host(value: str) -> str:
    """把协调器监听地址限制为确定的 IPv4 loopback。

    输入参数：
        value：``argparse`` 提供的监听地址文本。
    输出返回值：
        仅当值严格为 ``127.0.0.1`` 时返回原值。
    异常：
        argparse.ArgumentTypeError：任意可把明文 Bearer 流量暴露到
            网络的地址，包括 wildcard、主机名和非 loopback IP。
    """

    if value != DEFAULT_LEASE_SERVER_HOST:
        raise argparse.ArgumentTypeError("host 必须为 127.0.0.1")
    return value


def build_lease_server_argument_parser() -> argparse.ArgumentParser:
    """构造不接受 credential 参数的协调器 CLI parser。

    输入参数：
        无。
    输出返回值：
        默认绑定 ``127.0.0.1:8765`` 的 ArgumentParser；
        Bearer credential 始终由固定环境变量提供。
    """

    parser = argparse.ArgumentParser(
        prog="python -m paraguibench.integrations.webmall.lease_coordinator",
        description="Run the WebMall distributed-lease v1 coordinator.",
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_LEASE_DATABASE,
        help="SQLite state file (default: %(default)s)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_LEASE_SERVER_HOST,
        type=_parse_loopback_host,
        help="listen address (default: loopback %(default)s)",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_LEASE_SERVER_PORT,
        type=_parse_tcp_port,
        help="listen port (default: %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """启动无三方依赖的 WebMall distributed-lease HTTP 服务。

    输入参数：
        argv：可选 CLI 参数列表；``None`` 时读取当前进程参数。
    输出返回值：
        正常停止返回 0，安全启动失败返回 2，中断返回 130。
    """

    arguments = build_lease_server_argument_parser().parse_args(argv)
    server: ThreadingHTTPServer | None = None
    try:
        coordinator = SQLiteLeaseCoordinator(arguments.database)
        server = create_lease_http_server(
            coordinator,
            host=arguments.host,
            port=arguments.port,
        )
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    except (LeaseCoordinatorError, OSError, ValueError):
        print("webmall lease coordinator 启动失败", file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.server_close()
    return 0


class SQLiteLeaseCoordinator(_AcquiringSQLiteLeaseCoordinator):
    """在 acquire 存储边界上补齐 assert-held 与 release。"""

    def assert_held(
        self,
        *,
        namespace: str,
        attempt_id: str,
        owner_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> LeaseGrant:
        """核对请求者仍持有当前未过期的完整租约身份。

        输入参数：
            namespace：四店环境身份。
            attempt_id：获取租约的 Attempt 身份。
            owner_id：获取租约的进程或 worker 身份。
            lease_id：获取时生成的随机租约身份。
            fencing_token：获取时分配的持久化单调整数。
        输出返回值：
            当前未过期的权威租约。
        异常：
            LeaseCoordinatorError：任一身份不匹配、租约过期或存储不可用。
        """

        namespace = _validate_identity(namespace, "namespace")
        attempt_id = _validate_identity(attempt_id, "attempt_id")
        owner_id = _validate_identity(owner_id, "owner_id")
        if (
            not isinstance(lease_id, str)
            or _LEASE_ID_PATTERN.fullmatch(lease_id) is None
        ):
            raise LeaseCoordinatorError(
                "invalid_request",
                "lease lease_id 无效",
            )
        if (
            not isinstance(fencing_token, int)
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise LeaseCoordinatorError(
                "invalid_request",
                "lease fencing_token 无效",
            )
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                now_ms = self._clock_ms()
                if not isinstance(now_ms, int) or isinstance(now_ms, bool):
                    connection.execute("ROLLBACK")
                    raise LeaseCoordinatorError(
                        "clock_unavailable",
                        "lease clock 不可用",
                    )
                current = connection.execute(
                    """
                    SELECT namespace, attempt_id, owner_id, lease_id,
                           fencing_token, ttl_seconds, expires_at_unix_ms
                    FROM active_leases WHERE namespace = ?
                    """,
                    (namespace,),
                ).fetchone()
                expected_identity = (
                    attempt_id,
                    owner_id,
                    lease_id,
                    fencing_token,
                )
                actual_identity = (
                    (
                        current["attempt_id"],
                        current["owner_id"],
                        current["lease_id"],
                        current["fencing_token"],
                    )
                    if current is not None
                    else None
                )
                if (
                    current is None
                    or actual_identity != expected_identity
                    or current["expires_at_unix_ms"] <= now_ms
                ):
                    connection.execute("ROLLBACK")
                    raise LeaseCoordinatorError(
                        "lease_not_held",
                        "lease 未由该身份持有",
                    )
                renewed_expires_at_ms = now_ms + int(current["ttl_seconds"]) * 1000
                cursor = connection.execute(
                    """
                    UPDATE active_leases
                    SET expires_at_unix_ms = ?
                    WHERE namespace = ? AND attempt_id = ? AND owner_id = ?
                      AND lease_id = ? AND fencing_token = ?
                      AND expires_at_unix_ms > ?
                    """,
                    (
                        renewed_expires_at_ms,
                        namespace,
                        attempt_id,
                        owner_id,
                        lease_id,
                        fencing_token,
                        now_ms,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.execute("ROLLBACK")
                    raise LeaseCoordinatorError(
                        "lease_not_held",
                        "lease 未由该身份持有",
                    )
                connection.execute("COMMIT")
            finally:
                connection.close()
        except LeaseCoordinatorError:
            raise
        except sqlite3.Error:
            raise LeaseCoordinatorError(
                "storage_unavailable",
                "lease storage 不可用",
            ) from None
        return LeaseGrant(
            namespace=current["namespace"],
            attempt_id=current["attempt_id"],
            owner_id=current["owner_id"],
            lease_id=current["lease_id"],
            fencing_token=current["fencing_token"],
            expires_at_unix_ms=renewed_expires_at_ms,
        )

    def release(
        self,
        *,
        namespace: str,
        attempt_id: str,
        owner_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> None:
        """原子释放一个完整匹配且未过期的租约。

        输入参数：
            namespace：四店环境身份。
            attempt_id：获取租约的 Attempt 身份。
            owner_id：获取租约的进程或 worker 身份。
            lease_id：获取时生成的随机租约身份。
            fencing_token：获取时分配的持久化单调整数。
        输出返回值：
            无；匹配行已删除，fencing token 高水位保留。
        异常：
            LeaseCoordinatorError：身份不匹配、租约过期或存储不可用。
        """

        namespace = _validate_identity(namespace, "namespace")
        attempt_id = _validate_identity(attempt_id, "attempt_id")
        owner_id = _validate_identity(owner_id, "owner_id")
        if (
            not isinstance(lease_id, str)
            or _LEASE_ID_PATTERN.fullmatch(lease_id) is None
        ):
            raise LeaseCoordinatorError(
                "invalid_request",
                "lease lease_id 无效",
            )
        if (
            not isinstance(fencing_token, int)
            or isinstance(fencing_token, bool)
            or fencing_token < 1
        ):
            raise LeaseCoordinatorError(
                "invalid_request",
                "lease fencing_token 无效",
            )
        try:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                now_ms = self._clock_ms()
                if not isinstance(now_ms, int) or isinstance(now_ms, bool):
                    connection.execute("ROLLBACK")
                    raise LeaseCoordinatorError(
                        "clock_unavailable",
                        "lease clock 不可用",
                    )
                cursor = connection.execute(
                    """
                    DELETE FROM active_leases
                    WHERE namespace = ? AND attempt_id = ? AND owner_id = ?
                      AND lease_id = ? AND fencing_token = ?
                      AND expires_at_unix_ms > ?
                    """,
                    (
                        namespace,
                        attempt_id,
                        owner_id,
                        lease_id,
                        fencing_token,
                        now_ms,
                    ),
                )
                if cursor.rowcount != 1:
                    tombstone = connection.execute(
                        """
                        SELECT attempt_id, owner_id, lease_id, fencing_token
                        FROM released_lease_tombstones
                        WHERE namespace = ?
                        """,
                        (namespace,),
                    ).fetchone()
                    released_identity = (
                        (
                            tombstone["attempt_id"],
                            tombstone["owner_id"],
                            tombstone["lease_id"],
                            tombstone["fencing_token"],
                        )
                        if tombstone is not None
                        else None
                    )
                    if released_identity == (
                        attempt_id,
                        owner_id,
                        lease_id,
                        fencing_token,
                    ):
                        connection.execute("ROLLBACK")
                        return
                    connection.execute("ROLLBACK")
                    raise LeaseCoordinatorError(
                        "lease_not_held",
                        "lease 未由该身份持有",
                    )
                connection.execute(
                    """
                    INSERT INTO released_lease_tombstones (
                        namespace, attempt_id, owner_id, lease_id,
                        fencing_token, released_at_unix_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(namespace) DO UPDATE SET
                        attempt_id = excluded.attempt_id,
                        owner_id = excluded.owner_id,
                        lease_id = excluded.lease_id,
                        fencing_token = excluded.fencing_token,
                        released_at_unix_ms = excluded.released_at_unix_ms
                    """,
                    (
                        namespace,
                        attempt_id,
                        owner_id,
                        lease_id,
                        fencing_token,
                        now_ms,
                    ),
                )
                connection.execute("COMMIT")
            finally:
                connection.close()
        except LeaseCoordinatorError:
            raise
        except sqlite3.Error:
            raise LeaseCoordinatorError(
                "storage_unavailable",
                "lease storage 不可用",
            ) from None


if __name__ == "__main__":
    raise SystemExit(main())
