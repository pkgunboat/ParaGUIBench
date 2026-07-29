"""Attempt 级结构化 artifact 的脱敏归档与 manifest 管理。"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any

from .contracts import ArtifactRecord, TaskAttempt
from .errors import RunStoreConflictError
from .identifiers import validate_identifier
from .persistence import (
    ensure_private_subdirectory,
    hold_private_file_lock,
    read_private_json_if_exists,
    write_private_json_atomic,
    write_private_json_exclusive,
)
from .privacy import sanitize_record

_SCHEMA_VERSION = "1.0"
_RESERVED_ARTIFACT_RELATIVE_PATHS = frozenset(
    {
        ".manifest.lock",
        "manifest.json",
    }
)


def write_json_artifact(
    *,
    attempt: TaskAttempt,
    logical_name: str,
    relative_path: str,
    content: Any,
    media_type: str,
) -> ArtifactRecord:
    """原子归档一个脱敏 JSON artifact，并更新 Attempt manifest。

    输入参数：
        attempt：已经由 RunStore 验证归属与身份的任务 Attempt。
        logical_name：当前 Attempt 内稳定且唯一的 artifact 逻辑名称。
        relative_path：相对 Attempt ``artifacts`` 目录的安全 POSIX 路径。
        content：需要归档的结构化内容；序列化前统一经过
            ``sanitize_record``。
        media_type：必须为与实际序列化格式一致的 ``application/json``。
    输出返回值：
        ``ArtifactRecord``，包含最终路径、脱敏后 SHA-256、字节数与媒体
        类型。逻辑名或路径冲突时 fail-closed，绝不覆盖已有正文。
    """

    safe_logical_name = validate_identifier("logical_name", logical_name)
    safe_relative_path = _validate_artifact_relative_path(relative_path)
    if media_type != "application/json":
        raise ValueError(
            "structured artifacts require media_type='application/json'"
        )

    path_parts = safe_relative_path.parts
    artifact_root = ensure_private_subdirectory(
        attempt.path,
        "artifacts",
    )
    with hold_private_file_lock(artifact_root / ".manifest.lock"):
        manifest_path = artifact_root / "manifest.json"
        manifest_entries: list[dict[str, Any]] = []
        existing_manifest = read_private_json_if_exists(manifest_path)
        if existing_manifest is not None:
            manifest_entries.extend(existing_manifest["artifacts"])
        if any(
            entry.get("logical_name") == safe_logical_name
            for entry in manifest_entries
        ):
            raise RunStoreConflictError(
                "artifact logical_name is already registered: "
                f"{safe_logical_name}"
            )

        artifact_parent = ensure_private_subdirectory(
            artifact_root,
            *path_parts[:-1],
        )
        artifact_path = artifact_parent / path_parts[-1]
        write_private_json_exclusive(
            artifact_path,
            sanitize_record(content),
        )

        persisted_bytes = artifact_path.read_bytes()
        digest = hashlib.sha256(persisted_bytes).hexdigest()
        byte_count = len(persisted_bytes)
        normalized_relative_path = safe_relative_path.as_posix()
        manifest_entries.append(
            {
                "logical_name": safe_logical_name,
                "media_type": media_type,
                "relative_path": normalized_relative_path,
                "sha256": digest,
                "byte_count": byte_count,
            }
        )
        write_private_json_atomic(
            manifest_path,
            {
                "schema_version": _SCHEMA_VERSION,
                "artifacts": manifest_entries,
            },
        )
        return ArtifactRecord(
            path=artifact_path,
            logical_name=safe_logical_name,
            relative_path=normalized_relative_path,
            sha256=digest,
            byte_count=byte_count,
            media_type=media_type,
        )


def _validate_artifact_relative_path(
    relative_path: str,
) -> PurePosixPath:
    """验证 artifact 路径是无穿越语义的 POSIX 相对路径。

    输入参数：
        relative_path：调用方提供、相对 Attempt ``artifacts`` 目录的
            路径。
    输出返回值：
        规范化的 ``PurePosixPath``；空路径、绝对路径、反斜杠、任何
        ``.``/``..`` 路径段或 RunStore 保留路径会抛出 ``ValueError``。
    """

    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("relative_path must be a non-empty POSIX path")
    if "\\" in relative_path:
        raise ValueError("relative_path must not contain backslashes")

    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or any(
        part in {".", ".."} for part in candidate.parts
    ):
        raise ValueError(
            "relative_path must be relative and contain no traversal segments"
        )
    if candidate.as_posix() in _RESERVED_ARTIFACT_RELATIVE_PATHS:
        raise ValueError("relative_path is reserved for RunStore metadata")
    return candidate
