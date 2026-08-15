"""RunStore 持久化前的递归脱敏规则。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
PRESENT = "[PRESENT]"
ABSENT = "[ABSENT]"

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credentials",
        "passwd",
        "password",
        "private_key",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "secret_key",
        "session",
        "session_id",
        "set_cookie",
        "ssh_password",
        "token",
        "x_api_key",
    }
)
_HEADER_CONTAINER_NAMES = frozenset(
    {
        "header",
        "headers",
        "http_headers",
        "request_headers",
        "response_headers",
    }
)
_ENVIRONMENT_CONTAINER_NAMES = frozenset(
    {
        "env_vars",
        "environment_variables",
        "os_environ",
        "process_environment",
    }
)
_SAFE_ENVIRONMENT_VALUE_NAMES = frozenset(
    {
        "lang",
        "lc_all",
        "lc_ctype",
        "pythonhashseed",
        "tz",
    }
)
_SENSITIVE_FIELD_SUFFIXES = (
    "_access_key",
    "_access_token",
    "_api_key",
    "_authorization",
    "_bearer_token",
    "_credential",
    "_credentials",
    "_password",
    "_passwd",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_secret_key",
    "_session_id",
    "_ssh_password",
    "_token",
)
_SENSITIVE_QUERY_PARAMETER_NAMES = frozenset(
    {
        "key",
        "order_key",
    }
)
_SAFE_HEADER_NAMES = frozenset(
    {
        "accept",
        "accept_encoding",
        "content_length",
        "content_type",
        "user_agent",
    }
)


def sanitize_record(value: Any) -> Any:
    """递归生成允许写入 RunStore 的脱敏副本。

    输入参数：
        value：由基本 JSON 类型、Mapping 或 Sequence 组成的待记录对象。
    输出返回值：
        与输入结构对应的新对象；敏感字段和认证 header 值替换为
        ``[REDACTED]``，未知对象只记录类型名称而不调用可能泄密的 ``repr``。
    """

    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized_key = _normalize_field_name(key)
            if _is_sensitive_field(normalized_key):
                sanitized[key] = REDACTED
            elif normalized_key in _HEADER_CONTAINER_NAMES:
                sanitized[key] = _sanitize_headers(raw_value)
            elif normalized_key in _ENVIRONMENT_CONTAINER_NAMES:
                sanitized[key] = _sanitize_environment(raw_value)
            else:
                sanitized[key] = sanitize_record(raw_value)
        return sanitized

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_record(item) for item in value]

    if isinstance(value, str):
        return _sanitize_text(value)

    if value is None or isinstance(value, (int, float, bool)):
        return value

    return f"[UNSUPPORTED_TYPE:{type(value).__name__}]"


def _sanitize_headers(value: Any) -> Any:
    """仅保留无认证语义的常见 HTTP header 值。

    输入参数：
        value：待记录的 header Mapping；非 Mapping 输入视为不可安全解释。
    输出返回值：
        header 名称到安全值或 ``[REDACTED]`` 的新 Mapping。认证、cookie
        及未知 header 默认脱敏，避免第三方 SDK 自定义 header 泄露凭据。
    """

    if not isinstance(value, Mapping):
        return REDACTED

    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        normalized_key = _normalize_field_name(key)
        if normalized_key in _SAFE_HEADER_NAMES:
            sanitized[key] = sanitize_record(raw_value)
        else:
            sanitized[key] = REDACTED
    return sanitized


def _sanitize_environment(value: Any) -> Any:
    """以 allowlist 处理进程环境变量，不序列化完整 ``os.environ``。

    输入参数：
        value：环境变量名称到值的 Mapping；非 Mapping 输入视为不可安全解释。
    输出返回值：
        安全 locale/复现变量保留脱敏后的值；凭据变量只保留变量名及
        ``[PRESENT]``/``[ABSENT]``；其他环境变量完全省略。
    """

    if not isinstance(value, Mapping):
        return REDACTED

    sanitized: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        normalized_key = _normalize_field_name(key)
        if normalized_key in _SAFE_ENVIRONMENT_VALUE_NAMES:
            sanitized[key] = sanitize_record(raw_value)
        elif _is_sensitive_field(normalized_key):
            sanitized[key] = (
                PRESENT if raw_value is not None and raw_value != "" else ABSENT
            )
    return sanitized


def _normalize_field_name(name: str) -> str:
    """把配置或 header 字段名规范化为可比较形式。

    输入参数：
        name：原始字段名。
    输出返回值：
        小写且所有非字母数字字符折叠为下划线的字段名。
    """

    normalized_chars = [
        character.lower() if character.isalnum() else "_" for character in name
    ]
    return "_".join(part for part in "".join(normalized_chars).split("_") if part)


def _is_sensitive_field(normalized_name: str) -> bool:
    """判断规范化字段名是否承载凭据或认证状态。

    输入参数：
        normalized_name：由 ``_normalize_field_name`` 生成的字段名。
    输出返回值：
        敏感字段返回 ``True``；普通模型配置、计数和公开元数据返回
        ``False``。provider 前缀通过敏感后缀匹配处理。
    """

    if normalized_name in _SENSITIVE_FIELD_NAMES:
        return True
    return normalized_name.endswith(_SENSITIVE_FIELD_SUFFIXES)


def _is_sensitive_query_parameter(normalized_name: str) -> bool:
    """判断规范化 URL query 参数名是否承载访问凭据。

    输入参数：
        normalized_name：由 ``_normalize_field_name`` 生成的 query
        参数名。
    输出返回值：
        已知访问令牌参数或通用敏感字段返回 ``True``；
        可用于复现的普通 query 参数返回 ``False``。
    """

    return normalized_name in _SENSITIVE_QUERY_PARAMETER_NAMES or _is_sensitive_field(
        normalized_name
    )


def _sanitize_text(value: str) -> str:
    """移除 HTTP(S) URL 中的 userinfo 和敏感 query 值。

    输入参数：
        value：待记录的普通字符串或 URL。
    输出返回值：
        非 URL 字符串原样返回；HTTP(S) URL 删除 userinfo，将敏感 query
        参数值替换为 ``[REDACTED]``，同时保留非敏感参数以支持复现。
    """

    try:
        parsed = urlsplit(value)
    except ValueError:
        return REDACTED if "://" in value else value

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return value

    hostname = parsed.hostname
    if not hostname:
        return REDACTED
    safe_hostname = f"[{hostname}]" if ":" in hostname else hostname

    try:
        port = parsed.port
    except ValueError:
        return REDACTED
    safe_netloc = f"{safe_hostname}:{port}" if port is not None else safe_hostname

    sanitized_query = urlencode(
        [
            (
                query_name,
                REDACTED
                if _is_sensitive_query_parameter(_normalize_field_name(query_name))
                else query_value,
            )
            for query_name, query_value in parse_qsl(
                parsed.query,
                keep_blank_values=True,
            )
        ]
    )
    return urlunsplit(
        (
            parsed.scheme,
            safe_netloc,
            parsed.path,
            sanitized_query,
            parsed.fragment,
        )
    )
