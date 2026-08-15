"""RunStore 的私有目录与原子 JSON 持久化实现。"""

from __future__ import annotations

import fcntl
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import RunStoreConflictError


@contextmanager
def hold_private_file_lock(path: Path) -> Iterator[None]:
    """独占持有一个不可跟随 symlink 的私有进程间文件锁。

    输入参数：
        path：锁文件路径；父目录必须属于 RunStore，锁文件会以
            ``0600`` 创建或收紧权限。
    输出返回值：
        上下文管理器在进入时阻塞直至取得独占锁，退出时释放锁且不
        删除稳定锁文件。锁路径为 symlink 或非普通文件时抛出
        ``ValueError``。
    """

    ensure_private_directory(path.parent)
    open_flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW

    try:
        file_descriptor = os.open(path, open_flags, 0o600)
    except OSError as error:
        if path.is_symlink():
            raise ValueError(f"RunStore lock path is a symlink: {path}") from error
        raise

    try:
        lock_status = os.fstat(file_descriptor)
        if not stat.S_ISREG(lock_status.st_mode):
            raise ValueError(f"RunStore lock path is not a regular file: {path}")
        os.fchmod(file_descriptor, 0o600)
        fcntl.flock(file_descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
    finally:
        os.close(file_descriptor)


def read_private_json_if_exists(path: Path) -> Any | None:
    """安全读取私有 JSON，并拒绝 symlink 与非普通文件。

    输入参数：
        path：待读取的 RunStore JSON 文件路径。
    输出返回值：
        文件不存在时返回 ``None``；存在时返回 ``json.load`` 解码后的
        对象。路径为 symlink、目录或其他非普通文件时抛出
        ``ValueError``，JSON 损坏时传播 ``JSONDecodeError``，不会跟随
        链接读取外部内容。
    """

    if path.is_symlink():
        raise ValueError(f"RunStore JSON path is a symlink: {path}")

    open_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(path, open_flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        if path.is_symlink():
            raise ValueError(f"RunStore JSON path is a symlink: {path}") from error
        raise

    try:
        file_status = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_status.st_mode):
            raise ValueError(f"RunStore JSON path is not a regular file: {path}")
        with os.fdopen(file_descriptor, "r", encoding="utf-8") as file_handle:
            file_descriptor = -1
            return json.load(file_handle)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)


def ensure_private_directory(path: Path) -> None:
    """创建仅当前用户可访问的 RunStore 目录。

    输入参数：
        path：需要创建或收紧权限的目录路径。
    输出返回值：
        无；目录存在且权限被设置为 ``0700``，否则向调用方传播 I/O 异常。
    """

    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def ensure_private_subdirectory(root: Path, *parts: str) -> Path:
    """逐级建立私有子目录并拒绝 symlink 或根目录逃逸。

    输入参数：
        root：已经由 RunStore 控制的可信根目录。
        parts：按顺序追加的安全目录名；调用方应先完成标识字符验证。
    输出返回值：
        完整子目录路径。任一级为 symlink、非目录或解析到 root 外部时抛出
        ``ValueError``，不会继续创建后续路径。
    """

    ensure_private_directory(root)
    resolved_root = root.resolve(strict=True)
    current = root
    for part in parts:
        candidate = current / part
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            pass

        try:
            path_status = candidate.lstat()
        except FileNotFoundError as error:
            raise ValueError(
                f"RunStore directory disappeared during creation: {candidate}"
            ) from error
        if stat.S_ISLNK(path_status.st_mode):
            raise ValueError(f"RunStore path contains a symlink: {candidate}")
        if not stat.S_ISDIR(path_status.st_mode):
            raise ValueError(f"RunStore path is not a directory: {candidate}")

        resolved_candidate = candidate.resolve(strict=True)
        if not resolved_candidate.is_relative_to(resolved_root):
            raise ValueError(f"RunStore path resolves outside root: {candidate}")
        candidate.chmod(0o700)
        current = candidate
    return current


def append_private_json_line(path: Path, payload: Any) -> None:
    """向单 producer 独占的 JSONL 文件追加一条私有记录。

    输入参数：
        path：producer 独占的 JSONL 文件路径。
        payload：已完成脱敏且可 JSON 序列化的事件记录。
    输出返回值：
        无；记录以一次追加打开过程写入、完成 ``fsync``，文件权限为
        ``0600``。调用方必须保证不同进程不共享同一 producer 文件。
    """

    ensure_private_directory(path.parent)
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if path.is_symlink():
        raise ValueError(f"RunStore JSONL path is a symlink: {path}")
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    try:
        file_descriptor = os.open(path, open_flags, 0o600)
    except OSError as error:
        if path.is_symlink():
            raise ValueError(f"RunStore JSONL path is a symlink: {path}") from error
        raise
    try:
        file_status = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_status.st_mode):
            raise ValueError(f"RunStore JSONL path is not a regular file: {path}")
        os.fchmod(file_descriptor, 0o600)
        remaining = memoryview(serialized)
        while remaining:
            written = os.write(file_descriptor, remaining)
            if written <= 0:
                raise OSError("failed to append JSONL event")
            remaining = remaining[written:]
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def write_private_json_atomic(path: Path, payload: Any) -> None:
    """以原子替换方式写入仅当前用户可读写的 JSON。

    输入参数：
        path：最终 JSON 文件路径。
        payload：已经完成脱敏、可被 ``json.dumps`` 序列化的数据。
    输出返回值：
        无；成功时最终文件完整可见且权限为 ``0600``。失败时清理本次唯一
        临时文件并传播异常，不留下半写终态文件。
    """

    ensure_private_directory(path.parent)
    temporary_path = _create_private_temporary_json(path, payload)

    try:
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        _sync_directory(path.parent)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def write_private_json_once_or_verify(path: Path, payload: Any) -> None:
    """首次写入不可变 JSON，后续只接受完全相同的记录。

    输入参数：
        path：不可变 JSON 的稳定路径。
        payload：已完成脱敏且可 JSON 序列化的记录。
    输出返回值：
        无；路径不存在时原子写入，已存在且内容相同则保持原文件，内容不同时
        抛出 ``RunStoreConflictError``。
    """

    try:
        write_private_json_exclusive(path, payload)
        return
    except RunStoreConflictError:
        pass

    try:
        existing_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RunStoreConflictError(
            f"immutable record cannot be verified: {path}"
        ) from error

    if existing_payload != payload:
        raise RunStoreConflictError(
            f"immutable record already exists with different content: {path}"
        )


def write_private_json_exclusive(path: Path, payload: Any) -> None:
    """原子创建不可重复使用的 JSON 身份记录。

    输入参数：
        path：必须尚不存在的稳定 JSON 路径。
        payload：已完成脱敏且可 JSON 序列化的记录。
    输出返回值：
        无；完整临时文件通过同目录硬链接原子提交。路径已存在时抛出
        ``RunStoreConflictError``，绝不覆盖现有 Attempt。
    """

    ensure_private_directory(path.parent)
    temporary_path = _create_private_temporary_json(path, payload)

    try:
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError as error:
            raise RunStoreConflictError(
                f"immutable record already exists: {path}"
            ) from error

        os.chmod(path, 0o600)
        _sync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def _create_private_temporary_json(path: Path, payload: Any) -> Path:
    """在最终文件同目录生成已经同步的私有临时 JSON。

    输入参数：
        path：最终 JSON 路径，用于确定临时文件目录与安全前缀。
        payload：已脱敏且可 JSON 序列化的数据。
    输出返回值：
        完整写入、完成文件 ``fsync`` 且权限为 ``0600`` 的唯一临时路径；
        调用方负责原子提交或清理。
    """

    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    file_descriptor = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_handle:
            file_handle.write(serialized)
            file_handle.write("\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    os.chmod(temporary_path, 0o600)
    return temporary_path


def _sync_directory(path: Path) -> None:
    """同步目录元数据，确保原子替换在崩溃后仍可恢复。

    输入参数：
        path：刚完成文件替换的父目录。
    输出返回值：
        无；在支持目录 ``fsync`` 的平台完成同步。平台不支持时安全返回，
        文件内容本身仍已完成 ``fsync``。
    """

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY

    try:
        directory_descriptor = os.open(path, flags)
    except OSError:
        return

    try:
        os.fsync(directory_descriptor)
    except OSError:
        return
    finally:
        os.close(directory_descriptor)
