"""RunStore 版本向量的 fail-closed 格式验证。"""

from __future__ import annotations

import re

from .contracts import RunVersionVector

_CODE_REVISION_PATTERN = re.compile(r"(?:git:[0-9a-f]{40}|tree-sha256:[0-9a-f]{64})")
_ENVIRONMENT_REVISION_PATTERN = re.compile(r"manifest-sha256:[0-9a-f]{64}")
_PROTOCOL_PATTERN = re.compile(
    r"[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+\.v[1-9][0-9]*"
)


def validate_run_version_vector(vector: RunVersionVector) -> None:
    """验证 Run 版本向量不含浮动别名、占位值或不完整摘要。

    输入参数：
        vector：调用层依据实际源码、Agent、evaluator、runtime-support 和
            环境 manifest 构造的不可变版本向量。
    输出返回值：
        无；六个字段均为可审计固定身份时正常返回。
    异常：
        TypeError：输入不是 ``RunVersionVector``。
        ValueError：任一字段不是允许的完整摘要或版本化协议 ID；错误消息
            只指出 schema 区域，不回显调用方值。
    """

    if not isinstance(vector, RunVersionVector):
        raise TypeError("version_vector must be RunVersionVector")
    code_revisions = (
        vector.source_revision,
        vector.agent_code_revision,
        vector.evaluator_revision,
    )
    if any(
        not isinstance(value, str) or _CODE_REVISION_PATTERN.fullmatch(value) is None
        for value in code_revisions
    ):
        raise ValueError("version_vector code revision 无效")
    protocols = (
        vector.evaluation_protocol,
        vector.environment_protocol,
    )
    if any(
        not isinstance(value, str) or _PROTOCOL_PATTERN.fullmatch(value) is None
        for value in protocols
    ):
        raise ValueError("version_vector protocol 无效")
    if (
        not isinstance(vector.environment_revision, str)
        or _ENVIRONMENT_REVISION_PATTERN.fullmatch(vector.environment_revision) is None
    ):
        raise ValueError("version_vector environment revision 无效")
