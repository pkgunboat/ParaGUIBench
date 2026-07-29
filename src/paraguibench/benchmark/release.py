"""按固定发布清单摘要加载一个 canonical benchmark task。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_DEFAULT_MANIFEST = Path("benchmark/manifests/release-v1.json")


class ReleaseTaskError(RuntimeError):
    """表示发布清单、任务路径、摘要或任务身份不满足固定契约。"""


@dataclass(frozen=True, slots=True)
class ReleaseTaskRecord:
    """保存通过 release manifest 验证的 canonical task 及其身份。

    输入参数：
        release_id：发布清单的稳定版本标识。
        task：已解析并校验内部 task_id 的 canonical task object。
        canonical_sha256：manifest 固定的任务文件摘要。
    输出返回值：
        preparation/runtime 可同时使用 task 内容和不可变发布身份。
    """

    release_id: str
    task: dict[str, Any]
    canonical_sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseFixtureRecord:
    """保存通过 release manifest 验证的版本化 fixture。

    输入参数：
        fixture_id：manifest 与 fixture 内部共享的稳定标识。
        fixture：已解析的 fixture JSON object。
        sha256：manifest 固定的 fixture 文件摘要。
    输出返回值：
        preparation 可据此物化可信内存视图并生成不含值的 audit 投影。
    """

    fixture_id: str
    fixture: dict[str, Any]
    sha256: str


def load_release_task(
    repo_root: Path,
    task_id: str,
    *,
    manifest_relative_path: Path = _DEFAULT_MANIFEST,
) -> dict[str, Any]:
    """从 release manifest 中按 task_id 加载并校验一个任务。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        task_id：要执行的稳定 canonical task ID。
        manifest_relative_path：仓库内发布清单相对路径。
    输出返回值：
        摘要、路径和内部 task_id 均通过验证的 JSON object。
    异常：
        ReleaseTaskError：输入、manifest、符号链接、路径、摘要或任务身份无效。
    """

    return load_release_task_record(
        repo_root,
        task_id,
        manifest_relative_path=manifest_relative_path,
    ).task


def load_release_task_record(
    repo_root: Path,
    task_id: str,
    *,
    manifest_relative_path: Path = _DEFAULT_MANIFEST,
) -> ReleaseTaskRecord:
    """加载 canonical task，并同时返回 release ID 与固定摘要。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        task_id：要执行的稳定 canonical task ID。
        manifest_relative_path：仓库内发布清单相对路径。
    输出返回值：
        ``ReleaseTaskRecord``，包含校验后的 task、release ID 和摘要。
    异常：
        ReleaseTaskError：输入、manifest、路径、摘要或任务身份无效。
    """

    root = repo_root.resolve()
    if not root.is_dir():
        raise ReleaseTaskError("repo_root 不是目录")
    if (
        not isinstance(task_id, str)
        or not task_id
        or len(task_id) > 200
    ):
        raise ReleaseTaskError("task_id 必须是有界非空字符串")
    manifest = _load_release_manifest(root, manifest_relative_path)
    entries = manifest.get("tasks")
    if not isinstance(entries, list):
        raise ReleaseTaskError("发布清单 tasks 必须是列表")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("task_id") == task_id
    ]
    if len(matches) != 1:
        raise ReleaseTaskError("发布清单中 task_id 必须唯一存在")
    entry = matches[0]
    relative_path = entry.get("path")
    expected_digest = entry.get("sha256")
    if not isinstance(relative_path, str) or not relative_path:
        raise ReleaseTaskError("任务条目缺少安全 path")
    if (
        not isinstance(expected_digest, str)
        or _SHA256_PATTERN.fullmatch(expected_digest) is None
    ):
        raise ReleaseTaskError("任务条目缺少有效 SHA-256")
    task_path = _safe_repo_file(root, relative_path, label="任务文件")
    if _sha256_file(task_path) != expected_digest:
        raise ReleaseTaskError("任务文件摘要与发布清单不一致")
    task = _read_json_object(task_path, label="任务文件")
    if task.get("task_id") != task_id:
        raise ReleaseTaskError("任务文件内部 task_id 与发布清单不一致")
    return ReleaseTaskRecord(
        release_id="release-v1",
        task=task,
        canonical_sha256=expected_digest,
    )


def load_release_fixture(
    repo_root: Path,
    fixture_id: str,
    *,
    manifest_relative_path: Path = _DEFAULT_MANIFEST,
) -> ReleaseFixtureRecord:
    """按 fixture_id 加载并验证 release 固定 fixture。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        fixture_id：canonical task 引用的稳定 fixture 标识。
        manifest_relative_path：仓库内发布清单相对路径。
    输出返回值：
        包含 JSON object 和固定摘要的 ``ReleaseFixtureRecord``。
    异常：
        ReleaseTaskError：fixture 引用不存在、不唯一、路径或摘要无效。
    """

    root = repo_root.resolve()
    if not root.is_dir():
        raise ReleaseTaskError("repo_root 不是目录")
    if (
        not isinstance(fixture_id, str)
        or not fixture_id
        or len(fixture_id) > 200
    ):
        raise ReleaseTaskError("fixture_id 必须是有界非空字符串")
    manifest = _load_release_manifest(root, manifest_relative_path)
    entries = manifest.get("fixtures")
    if not isinstance(entries, list):
        raise ReleaseTaskError("发布清单 fixtures 必须是列表")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("fixture_id") == fixture_id
    ]
    if len(matches) != 1:
        raise ReleaseTaskError("发布清单中 fixture_id 必须唯一存在")
    entry = matches[0]
    relative_path = entry.get("path")
    expected_digest = entry.get("sha256")
    if not isinstance(relative_path, str) or not relative_path:
        raise ReleaseTaskError("fixture 条目缺少安全 path")
    if (
        not isinstance(expected_digest, str)
        or _SHA256_PATTERN.fullmatch(expected_digest) is None
    ):
        raise ReleaseTaskError("fixture 条目缺少有效 SHA-256")
    fixture_path = _safe_repo_file(root, relative_path, label="fixture 文件")
    if _sha256_file(fixture_path) != expected_digest:
        raise ReleaseTaskError("fixture 文件摘要与发布清单不一致")
    fixture = _read_json_object(fixture_path, label="fixture 文件")
    if fixture.get("fixture_id") != fixture_id:
        raise ReleaseTaskError("fixture 内部身份与发布清单不一致")
    return ReleaseFixtureRecord(
        fixture_id=fixture_id,
        fixture=fixture,
        sha256=expected_digest,
    )


def _load_release_manifest(
    root: Path,
    manifest_relative_path: Path,
) -> dict[str, Any]:
    """读取并验证当前支持的 release manifest 根身份。

    输入参数：
        root：已 resolve 的仓库根目录。
        manifest_relative_path：仓库内 manifest 相对路径。
    输出返回值：
        schema_version 与 release_id 均合法的 manifest object。
    异常：
        ReleaseTaskError：路径、JSON 或根身份无效。
    """

    manifest_path = _safe_repo_file(
        root,
        manifest_relative_path.as_posix(),
        label="发布清单",
    )
    manifest = _read_json_object(manifest_path, label="发布清单")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("release_id") != "release-v1"
    ):
        raise ReleaseTaskError("发布清单版本无效")
    return manifest


def _safe_repo_file(root: Path, relative_path: str, *, label: str) -> Path:
    """解析仓库相对普通文件并拒绝任一符号链接路径段。

    输入参数：
        root：已 resolve 的仓库根目录。
        relative_path：待解析的 POSIX 仓库相对路径。
        label：仅用于不含路径值的安全错误消息。
    输出返回值：
        位于 root 内且不经过符号链接的普通文件绝对路径。
    异常：
        ReleaseTaskError：路径绝对、穿越、含反斜杠、符号链接或不是文件。
    """

    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
    ):
        raise ReleaseTaskError(f"{label}路径格式无效")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseTaskError(f"{label}不得越过仓库根目录")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseTaskError(f"{label}路径不得包含符号链接")
    resolved = current.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ReleaseTaskError(f"{label}不得越过仓库根目录") from error
    if not resolved.is_file():
        raise ReleaseTaskError(f"{label}不是普通文件")
    return resolved


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """读取 UTF-8 JSON object 且不在错误中回显正文。

    输入参数：
        path：已通过仓库路径门禁的普通文件。
        label：用于安全错误定位的逻辑名称。
    输出返回值：
        解析后的 dict。
    异常：
        ReleaseTaskError：I/O、JSON 或根节点类型无效。
    """

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseTaskError(
            f"{label}无法解析：{type(error).__name__}"
        ) from None
    if not isinstance(value, dict):
        raise ReleaseTaskError(f"{label}根节点必须是 JSON object")
    return value


def _sha256_file(path: Path) -> str:
    """流式计算普通文件的 SHA-256。

    输入参数：
        path：待摘要的已验证普通文件。
    输出返回值：
        64 位小写十六进制摘要。
    """

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
