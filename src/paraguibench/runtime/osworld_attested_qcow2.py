"""OSWorld candidate 共用的 attempt-owned qcow2 稳定绑定 API。

该模块有意复用已经通过 WebMall Cart 竞态回归的 held-FD→私有
0400 snapshot→Docker→close 后完整摘要复验实现。中性别名使 OSWorld
artifact component candidate 不依赖业务命名，同时保持单一安全
实现，避免两份快照逻辑漂移。
"""

from __future__ import annotations

from paraguibench.runtime.webmall_cart_qcow2 import (
    WebMallCartAttestedDockerSession,
    WebMallCartQcow2AttestationError,
)


OSWorldAttestedDockerSession = WebMallCartAttestedDockerSession
OSWorldQcow2AttestationError = WebMallCartQcow2AttestationError


__all__ = [
    "OSWorldAttestedDockerSession",
    "OSWorldQcow2AttestationError",
]
