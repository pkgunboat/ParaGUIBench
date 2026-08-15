"""校验 OpenAI-compatible 模型 endpoint，不读取、不回显密钥。"""

from __future__ import annotations

from urllib.parse import urlsplit


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_hostname(hostname: str | None) -> bool:
    """判断 hostname 是否为显式回环地址。

    输入参数：
        hostname：``urlsplit`` 解析出的主机名，可为 ``None``。
    输出返回值：
        仅 ``localhost`` / ``127.0.0.1`` / ``::1`` 为 True。
    """

    if not isinstance(hostname, str) or not hostname:
        return False
    return hostname.lower().rstrip(".") in _LOOPBACK_HOSTS


def is_allowed_model_base_url(value: str | None) -> bool:
    """判断模型 base URL 是否满足最小防泄露与本地评测约定。

    公网必须 HTTPS；``localhost`` / ``127.0.0.1`` / ``::1`` 允许 HTTP。
    禁止 userinfo、query、fragment、首尾空白，以及非法或越界端口，
    避免把密钥写进 URL，也避免 doctor 把普通配置错误报成 PASS。

    输入参数：
        value：待检查的 endpoint 字符串。
    输出返回值：
        合法为 True；不回显原值。
    """

    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    if (
        not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        return False
    if is_loopback_hostname(parts.hostname):
        return parts.scheme in {"http", "https"}
    return parts.scheme == "https"


def validate_model_base_url(value: str, *, field_name: str = "base_url") -> str:
    """校验模型 base URL；失败时抛出不含原 URL 的 ``ValueError``。

    输入参数：
        value：调用方注入的 endpoint。
        field_name：用于错误消息的字段名，例如 ``base_url`` 或
            ``planner base_url``。
    输出返回值：
        原字符串。
    异常：
        ValueError：公网非 HTTPS、回环非 http/https、含 userinfo/query/fragment、
            含首尾空白，或端口非法/越界。
    """

    if not is_allowed_model_base_url(value):
        raise ValueError(
            f"{field_name} 必须是无凭据、query 和 fragment 的 URL；"
            "公网仅允许 HTTPS，localhost/127.0.0.1/::1 允许 HTTP"
        )
    return value
