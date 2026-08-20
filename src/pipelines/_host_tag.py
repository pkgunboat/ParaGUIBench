#!/usr/bin/env python3
"""
host_tag 工具模块。

为多机实验同步提供稳定、可移植、可入文件系统的主机标识，
使每台节点的实验输出落在 `logs/<host_tag>/...` 命名空间下，
从而避免多机同时跑同一 condition 时互相覆盖。
"""

import os
import re
import socket


_VALID_CHAR_RE = re.compile(r"[^a-z0-9_-]+")
_DEFAULT_FALLBACK = "unknown-host"


def get_host_tag() -> str:
    """
    获取当前节点的 host_tag，作为 logs/ 下的命名空间目录名。

    输入: 无
        优先级:
            1. 环境变量 PARABENCH_HOST_TAG（用户显式设置）
            2. socket.gethostname() 的短主机名（去掉域名后缀）

    输出:
        合法化后的 host_tag 字符串。规则:
            - 转小写
            - 去掉两端空白
            - 字符集 [a-z0-9_-] 之外的字符替换为 '-'
            - 折叠连续的 '-'
            - 去掉首尾的 '-'
            - 空字符串退化为 'unknown-host'
    """
    raw = os.environ.get("PARABENCH_HOST_TAG", "").strip()
    if not raw:
        try:
            raw = socket.gethostname().split(".")[0]
        except Exception:
            raw = ""

    return _normalize(raw)


def _normalize(raw: str) -> str:
    """
    将任意字符串规范化为可作为目录名的 host_tag。

    输入:
        raw: 原始字符串（可能为空、含空格、含特殊字符）

    输出:
        合法化后的字符串；若处理后为空则返回 'unknown-host'
    """
    if not raw:
        return _DEFAULT_FALLBACK
    s = raw.strip().lower()
    s = _VALID_CHAR_RE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or _DEFAULT_FALLBACK
