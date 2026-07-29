"""RunStore 路径标识的统一验证规则。"""

from __future__ import annotations

import re

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


def validate_identifier(field_name: str, value: str) -> str:
    """验证目录标识，阻止路径穿越和平台相关保留路径。

    输入参数：
        field_name：用于错误信息的字段名称。
        value：待验证的 Run、Task、Attempt 或 producer 标识。
    输出返回值：
        验证通过的原始标识；不符合安全字符集或长度要求时抛出
        ``ValueError``。
    """

    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{field_name} must match {_SAFE_IDENTIFIER.pattern!r}"
        )
    return value
