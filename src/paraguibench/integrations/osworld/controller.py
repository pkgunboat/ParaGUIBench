"""连接本机端口映射后的 OSWorld guest agent server。

该模块基于 OSWorld PythonController 的 HTTP 协议重新实现最小接口；不迁移
旧 controller 的隐式 shell、完整环境变量日志或远程 SSH 路径。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path, PurePosixPath
import time
from typing import Any
from urllib.parse import urlsplit


class OSWorldControllerError(RuntimeError):
    """表示 controller 配置、传输或 guest 返回契约异常。"""


class OSWorldGuestPathMissingError(OSWorldControllerError):
    """表示受限 getter 确认 guest 最终目标路径不存在。"""


_DIRECTORY_LISTING_SCHEMA = "paraguibench.osworld.directory-listing.v1"
_DIRECTORY_LISTING_GUEST_PROGRAM = r"""
import json
import os
import sys

SCHEMA_VERSION = "paraguibench.osworld.directory-listing.v1"


def emit(payload, max_response_bytes):
    '''
    功能：以严格 UTF-8 输出有界 JSON，超限时改为固定错误。
    输入：payload 为不含路径的结构化对象；max_response_bytes
    为总字节上限。
    输出：无返回值；仅向 stdout 写入一个 JSON 对象。
    '''
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", "strict")
    if len(encoded) > max_response_bytes:
        encoded = json.dumps(
            {
                "error_code": "limit_exceeded",
                "schema_version": SCHEMA_VERSION,
                "status": "error",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", "strict")
    sys.stdout.buffer.write(encoded)


def has_unsafe_character(value):
    '''
    功能：检测文件名中不可接受的 Unicode 控制/格式字符。
    输入：value 为单个目录成员名。
    输出：存在不可打印字符时返回 True，否则返回 False。
    '''
    return any(not character.isprintable() for character in value)


def fail(error_code, max_response_bytes):
    '''
    功能：输出不回显路径或成员的固定错误对象。
    输入：error_code 为固定错误码；max_response_bytes 为输出上限。
    输出：无返回值；通过 emit 写入错误 JSON。
    '''
    emit(
        {
            "error_code": error_code,
            "schema_version": SCHEMA_VERSION,
            "status": "error",
        },
        max_response_bytes,
    )


def open_directory_without_symlinks(guest_path):
    '''
    功能：以 openat/O_NOFOLLOW 逐级打开目录，阻断符号链接穿越。
    输入：guest_path 为 controller 已校验的 POSIX 绝对路径。
    输出：返回由调用方负责关闭的最终目录文件描述符。
    '''
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open("/", flags)
    try:
        components = [] if guest_path == "/" else guest_path.split("/")[1:]
        for component in components:
            next_fd = os.open(
                component,
                flags,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def main():
    '''
    功能：枚举一层目录并生成 directory-listing.v1 闭集响应。
    输入：sys.argv 中的路径、成员数、名称字节和响应字节上限。
    输出：无返回值；成功或失败都只输出脱敏有界 JSON。
    '''
    directory_fd = None
    try:
        guest_path = sys.argv[1]
        max_entries = int(sys.argv[2])
        max_name_bytes = int(sys.argv[3])
        max_response_bytes = int(sys.argv[4])
        names = []
        directory_fd = open_directory_without_symlinks(guest_path)
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                name = entry.name
                encoded_name = name.encode("utf-8", "strict")
                if (
                    name in {".", ".."}
                    or "/" in name
                    or has_unsafe_character(name)
                    or len(encoded_name) > max_name_bytes
                ):
                    fail("invalid_entry", max_response_bytes)
                    return
                names.append(name)
                if len(names) > max_entries:
                    fail("limit_exceeded", max_response_bytes)
                    return
        names.sort(key=lambda value: value.encode("utf-8", "strict"))
        emit(
            {
                "entries": names,
                "schema_version": SCHEMA_VERSION,
                "status": "success",
            },
            max_response_bytes,
        )
    except Exception:
        try:
            fail("listing_failed", max_response_bytes)
        except Exception:
            pass
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


main()
""".strip()

_IMAGE_PIXEL_HASH_SCHEMA = "paraguibench.osworld.image-pixel-hashes.v1"
_MIN_IMAGE_PIXEL_HASH_TIMEOUT_SECONDS = 0.001
_IMAGE_PIXEL_HASH_GUEST_PROGRAM = r"""
import signal
import sys


def handle_timeout(_signum, _frame):
    '''
    功能：将 Unix 真实时间计时器信号转为可脱敏捕获的异常。
    输入：_signum 与 _frame 由 Python signal 机制传入，故意忽略。
    输出：不返回；始终抛出 TimeoutError 中止当前收集。
    '''
    raise TimeoutError("image collection timed out")


HELPER_TIMEOUT_SECONDS = float(sys.argv[10])
signal.signal(signal.SIGALRM, handle_timeout)
signal.setitimer(signal.ITIMER_REAL, HELPER_TIMEOUT_SECONDS)

import hashlib
import io
import json
import os
import stat
import warnings

from PIL import Image, ImageMode

SCHEMA_VERSION = "paraguibench.osworld.image-pixel-hashes.v1"


class FinalDirectoryMissing(Exception):
    '''表示仅最终目录分量在 openat 时不存在。'''


def emit(payload, max_response_bytes):
    '''
    功能：以严格 UTF-8 输出有界图像哈希 JSON。
    输入：payload 为不含路径或内容的结构化对象；
    max_response_bytes 为总响应上限。
    输出：无返回值；仅向 stdout 写入一个 JSON object。
    '''
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", "strict")
    if len(encoded) > max_response_bytes:
        encoded = json.dumps(
            {
                "error_code": "limit_exceeded",
                "schema_version": SCHEMA_VERSION,
                "status": "error",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", "strict")
    sys.stdout.buffer.write(encoded)


def fail(error_code, max_response_bytes):
    '''
    功能：输出不回显路径、名称或图像内容的错误。
    输入：error_code 为固定错误码；max_response_bytes 为上限。
    输出：无返回值；通过 emit 写入脱敏 JSON。
    '''
    emit(
        {
            "error_code": error_code,
            "schema_version": SCHEMA_VERSION,
            "status": "error",
        },
        max_response_bytes,
    )


def has_unsafe_character(value):
    '''
    功能：检测目录成员名中的不可打印 Unicode 字符。
    输入：value 为单个直接成员名。
    输出：存在不可打印字符时返回 True。
    '''
    return any(not character.isprintable() for character in value)


def open_directory_without_symlinks(guest_path):
    '''
    功能：以 openat/O_NOFOLLOW 逐级打开目录。
    输入：guest_path 为 host 已校验的 POSIX 绝对路径。
    输出：返回由调用方关闭的目录文件描述符。
    '''
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open("/", flags)
    try:
        components = [] if guest_path == "/" else guest_path.split("/")[1:]
        for index, component in enumerate(components):
            try:
                next_fd = os.open(component, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                if index == len(components) - 1:
                    raise FinalDirectoryMissing() from None
                raise
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def decoded_upper_bound(image):
    '''
    功能：在解码前估算 Pillow ``tobytes`` 的安全字节上界。
    输入：image 为已读取 header 但尚未完整解码的 Pillow 图像。
    输出：返回基于像素、band 和 mode 标量宽度的整数上界。
    '''
    descriptor = ImageMode.getmode(image.mode)
    item_size_text = descriptor.typestr[-1:]
    if item_size_text not in {"1", "2", "4", "8"}:
        raise ValueError("unsupported image scalar width")
    width, height = image.size
    return (
        int(width)
        * int(height)
        * max(1, len(descriptor.bands))
        * int(item_size_text)
    )


def main():
    '''
    功能：按 scandir 观察顺序对所有直接成员计算像素 SHA-256。
    输入：sys.argv 中的目录和八项固定资源上限。
    输出：无返回值；成功或失败均只输出有界脱敏 JSON。
    '''
    directory_fd = None
    max_response_bytes = 128
    timer_armed = True
    try:
        guest_path = sys.argv[1]
        max_entries = int(sys.argv[2])
        max_name_bytes = int(sys.argv[3])
        max_compressed_item_bytes = int(sys.argv[4])
        max_total_compressed_bytes = int(sys.argv[5])
        max_pixels_per_image = int(sys.argv[6])
        max_decoded_item_bytes = int(sys.argv[7])
        max_total_decoded_bytes = int(sys.argv[8])
        max_response_bytes = int(sys.argv[9])
        Image.MAX_IMAGE_PIXELS = max_pixels_per_image
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        directory_fd = open_directory_without_symlinks(guest_path)
        records = []
        total_compressed_bytes = 0
        total_decoded_bytes = 0
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                if len(records) >= max_entries:
                    raise ValueError("entry limit exceeded")
                name = entry.name
                encoded_name = name.encode("utf-8", "strict")
                if (
                    not encoded_name
                    or len(encoded_name) > max_name_bytes
                    or name in {".", ".."}
                    or "/" in name
                    or has_unsafe_character(name)
                ):
                    raise ValueError("invalid entry")
                before = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError("entry is not regular")
                file_fd = os.open(
                    name,
                    os.O_RDONLY
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC
                    | os.O_NONBLOCK,
                    dir_fd=directory_fd,
                )
                try:
                    after = os.fstat(file_fd)
                    if (
                        not stat.S_ISREG(after.st_mode)
                        or (before.st_dev, before.st_ino)
                        != (after.st_dev, after.st_ino)
                        or after.st_size < 0
                        or after.st_size > max_compressed_item_bytes
                        or total_compressed_bytes + after.st_size
                        > max_total_compressed_bytes
                    ):
                        raise ValueError("compressed input limit exceeded")
                    encoded_image = io.BytesIO()
                    compressed_size = 0
                    while True:
                        remaining = min(
                            max_compressed_item_bytes - compressed_size,
                            max_total_compressed_bytes
                            - total_compressed_bytes
                            - compressed_size,
                        )
                        chunk = os.read(
                            file_fd,
                            min(64 * 1024, max(1, remaining + 1)),
                        )
                        if not chunk:
                            break
                        compressed_size += len(chunk)
                        if (
                            compressed_size > max_compressed_item_bytes
                            or total_compressed_bytes + compressed_size
                            > max_total_compressed_bytes
                        ):
                            raise ValueError(
                                "compressed input limit exceeded"
                            )
                        encoded_image.write(chunk)
                    os.close(file_fd)
                    file_fd = None
                    total_compressed_bytes += compressed_size
                    encoded_image.seek(0)
                    with Image.open(encoded_image) as image:
                        width, height = image.size
                        pixel_count = int(width) * int(height)
                        if (
                            width <= 0
                            or height <= 0
                            or pixel_count > max_pixels_per_image
                        ):
                            raise ValueError("pixel limit exceeded")
                        decoded_bound = decoded_upper_bound(image)
                        if (
                            decoded_bound > max_decoded_item_bytes
                            or total_decoded_bytes + decoded_bound
                            > max_total_decoded_bytes
                        ):
                            raise ValueError("decoded limit exceeded")
                        image.load()
                        pixel_bytes = image.tobytes()
                        decoded_size = len(pixel_bytes)
                        if (
                            decoded_size > max_decoded_item_bytes
                            or total_decoded_bytes + decoded_size
                            > max_total_decoded_bytes
                        ):
                            raise ValueError("decoded limit exceeded")
                        total_decoded_bytes += decoded_size
                        records.append(
                            {
                                "name": name,
                                "sha256": hashlib.sha256(
                                    pixel_bytes
                                ).hexdigest(),
                            }
                        )
                finally:
                    if file_fd is not None:
                        os.close(file_fd)
        emit(
            {
                "records": records,
                "schema_version": SCHEMA_VERSION,
                "status": "success",
            },
            max_response_bytes,
        )
    except FinalDirectoryMissing:
        try:
            emit(
                {
                    "records": [],
                    "schema_version": SCHEMA_VERSION,
                    "status": "missing",
                },
                max_response_bytes,
            )
        except Exception:
            pass
    except Exception:
        try:
            fail("collection_failed", max_response_bytes)
        except Exception:
            pass
    finally:
        if timer_armed:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
        if directory_fd is not None:
            os.close(directory_fd)


main()
""".strip()

_ARTIFACT_TREE_MANIFEST_SCHEMA = "paraguibench.osworld.artifact-tree-manifest.v1"
_ARTIFACT_TREE_MANIFEST_GUEST_PROGRAM = r"""
import signal
import sys


def handle_timeout(_signum, _frame):
    '''
    功能：将 guest 真实时间超时转为可脱敏处理的异常。
    输入：_signum 与 _frame 由 signal 机制传入，故意忽略。
    输出：不返回；始终抛出 TimeoutError 中止枚举或哈希。
    '''
    raise TimeoutError("artifact tree collection timed out")


HELPER_TIMEOUT_SECONDS = float(sys.argv[9])
signal.signal(signal.SIGALRM, handle_timeout)
signal.setitimer(signal.ITIMER_REAL, HELPER_TIMEOUT_SECONDS)

import hashlib
import json
import os
import stat

SCHEMA_VERSION = "paraguibench.osworld.artifact-tree-manifest.v1"


def emit(payload, max_response_bytes):
    '''
    功能：以严格 UTF-8 输出有界 JSON，超限时改为固定错误。
    输入：payload 为 manifest 或脱敏错误；max_response_bytes
    为 stdout 总字节上限。
    输出：无返回值；仅向 stdout 写入一个 JSON object。
    '''
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", "strict")
    if len(encoded) > max_response_bytes:
        encoded = json.dumps(
            {
                "error_code": "limit_exceeded",
                "schema_version": SCHEMA_VERSION,
                "status": "error",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", "strict")
    sys.stdout.buffer.write(encoded)


def fail(error_code, max_response_bytes):
    '''
    功能：输出不回显 guest 路径、名称或内容的固定错误。
    输入：error_code 为固定错误码；max_response_bytes 为输出上限。
    输出：无返回值；通过 emit 写入脱敏 JSON。
    '''
    emit(
        {
            "error_code": error_code,
            "schema_version": SCHEMA_VERSION,
            "status": "error",
        },
        max_response_bytes,
    )


def has_unsafe_character(value):
    '''
    功能：检测目录成员名中的不可打印 Unicode 字符。
    输入：value 为单个直接成员名。
    输出：存在控制或格式字符时返回 True，否则返回 False。
    '''
    return any(not character.isprintable() for character in value)


def stable_signature(file_stat):
    '''
    功能：投影能检测枚举/读取期间替换或修改的元数据。
    输入：file_stat 为 fstat 返回的当前打开对象元数据。
    输出：设备、inode、类型/权限、大小、mtime 与 ctime 元组。
    '''
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def open_directory_without_symlinks(guest_path):
    '''
    功能：从根开始以 openat/O_NOFOLLOW 逐级打开目标目录。
    输入：guest_path 为 controller 已验证的规范 POSIX 绝对路径。
    输出：返回由调用方关闭的最终目录文件描述符。
    '''
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open("/", flags)
    try:
        components = [] if guest_path == "/" else guest_path.split("/")[1:]
        for component in components:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def hash_regular_file(directory_fd, name, max_file_bytes):
    '''
    功能：通过父目录 fd 以 O_NOFOLLOW 打开并完整哈希普通文件。
    输入：directory_fd/name 定位当前成员；max_file_bytes
    为单文件原始字节上限。
    输出：返回经前后 fstat 稳定性校验的 ``(size, sha256)``。
    '''
    file_fd = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_file_bytes:
            raise ValueError("invalid artifact file")
        digest = hashlib.sha256()
        observed_size = 0
        while True:
            chunk = os.read(file_fd, min(65536, max_file_bytes + 1))
            if not chunk:
                break
            observed_size += len(chunk)
            if observed_size > max_file_bytes:
                raise ValueError("artifact file limit exceeded")
            digest.update(chunk)
        after = os.fstat(file_fd)
        if (
            observed_size != before.st_size
            or stable_signature(before) != stable_signature(after)
        ):
            raise ValueError("unstable artifact file")
        return observed_size, digest.hexdigest()
    finally:
        os.close(file_fd)


def walk_directory(
    directory_fd,
    relative_parts,
    records,
    counters,
    max_files,
    max_nodes,
    max_depth,
    max_name_bytes,
    max_file_bytes,
    max_total_bytes,
):
    '''
    功能：递归枚举一个已打开目录，拒绝软链和特殊文件。
    输入：directory_fd/relative_parts 定位当前目录；records
    与 counters 聚合文件闭集；其余参数为文件数、
    总成员节点数、深度、名称、单文件与总字节上限。
    输出：无返回值；完整结果追加到 records，任何越界即抛异常。
    '''
    before = os.fstat(directory_fd)
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError("invalid artifact directory")
    names = []
    with os.scandir(directory_fd) as iterator:
        for entry in iterator:
            counters["nodes"] += 1
            if counters["nodes"] > max_nodes:
                raise ValueError("artifact node limit exceeded")
            names.append(entry.name)
    validated_names = []
    for name in names:
        encoded_name = name.encode("utf-8", "strict")
        if (
            name in {".", ".."}
            or "/" in name
            or has_unsafe_character(name)
            or not encoded_name
            or len(encoded_name) > max_name_bytes
        ):
            raise ValueError("invalid artifact name")
        validated_names.append((encoded_name, name))
    validated_names.sort(key=lambda item: item[0])
    for _encoded_name, name in validated_names:
        next_parts = relative_parts + (name,)
        if len(next_parts) > max_depth:
            raise ValueError("artifact depth limit exceeded")
        member_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(member_stat.st_mode):
            raise ValueError("artifact symlink rejected")
        if stat.S_ISDIR(member_stat.st_mode):
            child_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
            try:
                walk_directory(
                    child_fd,
                    next_parts,
                    records,
                    counters,
                    max_files,
                    max_nodes,
                    max_depth,
                    max_name_bytes,
                    max_file_bytes,
                    max_total_bytes,
                )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(member_stat.st_mode):
            size_bytes, sha256 = hash_regular_file(
                directory_fd,
                name,
                max_file_bytes,
            )
            counters["files"] += 1
            counters["bytes"] += size_bytes
            if (
                counters["files"] > max_files
                or counters["bytes"] > max_total_bytes
            ):
                raise ValueError("artifact tree limit exceeded")
            records.append(["/".join(next_parts), size_bytes, sha256])
        else:
            raise ValueError("artifact special file rejected")
    after = os.fstat(directory_fd)
    if stable_signature(before) != stable_signature(after):
        raise ValueError("unstable artifact directory")


def main():
    '''
    功能：生成一个完整、有界、nofollow 的递归 artifact manifest。
    输入：sys.argv 中的根路径、文件数、总成员节点数、
    深度、名称、单文件、总字节、响应字节与截止时间上限。
    输出：无返回值；成功输出完整 records，失败只输出固定错误。
    '''
    directory_fd = None
    max_response_bytes = 512
    try:
        guest_path = sys.argv[1]
        max_files = int(sys.argv[2])
        max_nodes = int(sys.argv[3])
        max_depth = int(sys.argv[4])
        max_name_bytes = int(sys.argv[5])
        max_file_bytes = int(sys.argv[6])
        max_total_bytes = int(sys.argv[7])
        max_response_bytes = int(sys.argv[8])
        directory_fd = open_directory_without_symlinks(guest_path)
        records = []
        walk_directory(
            directory_fd,
            (),
            records,
            {"bytes": 0, "files": 0, "nodes": 0},
            max_files,
            max_nodes,
            max_depth,
            max_name_bytes,
            max_file_bytes,
            max_total_bytes,
        )
        records.sort(key=lambda record: record[0].encode("utf-8", "strict"))
        emit(
            {
                "records": records,
                "schema_version": SCHEMA_VERSION,
                "status": "success",
            },
            max_response_bytes,
        )
    except Exception:
        try:
            fail("collection_failed", max_response_bytes)
        except Exception:
            pass
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


main()
""".strip()

_SINGLE_FILE_SCHEMA = "paraguibench.osworld.single-file.v1"
_SINGLE_FILE_GUEST_PROGRAM = r"""
import signal
import sys


def handle_timeout(_signum, _frame):
    '''
    功能：将 Unix 实时计时器信号转为中止单文件收集的异常。
    输入：_signum 与 _frame 由 signal 机制传入，故意忽略。
    输出：不返回；始终抛出 TimeoutError。
    '''
    raise TimeoutError("single file collection timed out")


HELPER_TIMEOUT_SECONDS = float(sys.argv[4])
signal.signal(signal.SIGALRM, handle_timeout)
signal.setitimer(signal.ITIMER_REAL, HELPER_TIMEOUT_SECONDS)

import base64
import json
import os
import stat

SCHEMA_VERSION = "paraguibench.osworld.single-file.v1"


class GuestPathMissing(Exception):
    '''表示目标文件或任一祖先目录以 ENOENT 缺失。'''


def emit(payload, max_response_bytes):
    '''
    功能：向 stdout 写入严格 UTF-8 且有界的 JSON object。
    输入：payload 为不含 guest 路径的闭集对象；
    max_response_bytes 为 guest stdout 字节上限。
    输出：无返回值；超限时改为固定错误对象。
    '''
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", "strict")
    if len(encoded) > max_response_bytes:
        encoded = json.dumps(
            {
                "error_code": "limit_exceeded",
                "schema_version": SCHEMA_VERSION,
                "status": "error",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", "strict")
    sys.stdout.buffer.write(encoded)


def fail(max_response_bytes):
    '''
    功能：输出不回显路径或文件内容的固定错误。
    输入：max_response_bytes 为 guest stdout 字节上限。
    输出：无返回值；通过 emit 写入错误 JSON。
    '''
    emit(
        {
            "error_code": "collection_failed",
            "schema_version": SCHEMA_VERSION,
            "status": "error",
        },
        max_response_bytes,
    )


def open_file_without_symlinks(guest_path):
    '''
    功能：以同一 directory fd 逐段打开祖先，再 nofollow 打开文件。
    输入：guest_path 为 host 已校验的 POSIX 绝对文件路径。
    输出：返回由调用方关闭的最终文件描述符。
    '''
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    directory_fd = os.open("/", directory_flags)
    try:
        components = guest_path.split("/")[1:]
        for component in components[:-1]:
            try:
                next_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                raise GuestPathMissing() from None
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            return os.open(
                components[-1],
                os.O_RDONLY
                | os.O_NOFOLLOW
                | os.O_CLOEXEC
                | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            raise GuestPathMissing() from None
    finally:
        os.close(directory_fd)


def main():
    '''
    功能：有界读取单个普通文件并输出 strict base64 JSON。
    输入：sys.argv 中的文件路径、原始字节、响应字节与时间上限。
    输出：无返回值；成功、缺失或失败都只写脱敏 JSON。
    '''
    file_fd = None
    max_response_bytes = 512
    try:
        guest_path = sys.argv[1]
        max_bytes = int(sys.argv[2])
        max_response_bytes = int(sys.argv[3])
        file_fd = open_file_without_symlinks(guest_path)
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 0
            or metadata.st_size > max_bytes
        ):
            raise ValueError("single file is not bounded regular file")
        content = bytearray()
        while True:
            remaining = max_bytes - len(content)
            chunk = os.read(
                file_fd,
                min(64 * 1024, max(1, remaining + 1)),
            )
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ValueError("single file byte limit exceeded")
        encoded_size = 4 * ((len(content) + 2) // 3)
        if encoded_size > max_response_bytes:
            raise ValueError("single file response limit exceeded")
        encoded_content = base64.b64encode(content).decode("ascii", "strict")
        emit(
            {
                "content_base64": encoded_content,
                "encoding": "base64",
                "schema_version": SCHEMA_VERSION,
                "size_bytes": len(content),
                "status": "success",
            },
            max_response_bytes,
        )
    except GuestPathMissing:
        try:
            emit(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "missing",
                },
                max_response_bytes,
            )
        except Exception:
            pass
    except Exception:
        try:
            fail(max_response_bytes)
        except Exception:
            pass
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        if file_fd is not None:
            os.close(file_fd)


main()
""".strip()


@dataclass(frozen=True)
class CommandResult:
    """保存一次 guest argv 命令的结构化结果。"""

    returncode: int
    stdout: str
    stderr: str


class OSWorldController:
    """通过仅绑定 loopback 的 HTTP endpoint 控制单个 OSWorld guest。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        session: Any | None = None,
    ) -> None:
        """构造 controller 并限制 endpoint 为本机 HTTP 端口。

        输入参数：
            base_url：形如 ``http://127.0.0.1:<port>`` 的 agent-server 地址。
            timeout：每个 HTTP 请求的超时秒数。
            session：可选 requests-compatible session；测试可注入 fake。
        输出返回值：
            无；实例保存受限 endpoint 与会话。
        异常：
            OSWorldControllerError：URL 非 loopback、含凭据或附加路径。
        """

        _validate_loopback_base_url(base_url)
        if timeout <= 0:
            raise OSWorldControllerError("controller timeout 必须大于零")
        uses_production_session = session is None
        if session is None:
            import requests

            session = requests.Session()
            # Controller 始终访问 host loopback。不允许 HTTP_PROXY
            # 等宿主环境变量把 guest 命令或证据转发给代理。
            session.trust_env = False
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session
        self._uses_production_session = uses_production_session

    def uses_production_transport(self) -> bool:
        """判断 controller 是否使用内部创建的直连 loopback 会话。

        输入参数：无。
        输出返回值：仅当构造时未注入 session，且当前会话
            明确 ``trust_env=False`` 时返回 ``True``；注入 fake、代理
            可用或属性异常均返回 ``False``。
        """

        try:
            return (
                self._uses_production_session is True
                and self._session.trust_env is False
            )
        except Exception:
            return False

    def get_screenshot(self) -> bytes:
        """读取带光标的当前 guest 截图。

        输入参数：
            无。
        输出返回值：
            agent server 返回的 PNG/JPEG 原始字节。
        异常：
            requests HTTP 异常由底层抛出；空响应转为
            ``OSWorldControllerError``。
        """

        response = self._session.get(
            f"{self._base_url}/screenshot",
            timeout=self._timeout,
        )
        response.raise_for_status()
        content = bytes(response.content)
        if not content:
            raise OSWorldControllerError("guest screenshot 响应为空")
        return content

    def execute(self, command: Sequence[str]) -> CommandResult:
        """在 guest 内以 ``shell=False`` 执行一个 argv 命令。

        输入参数：
            command：非空字符串参数序列；不会拼接成 shell 文本。
        输出返回值：
            guest 的退出码、标准输出和标准错误。
        异常：
            OSWorldControllerError：命令字段或 guest JSON 响应无效。
        """

        argv = _validate_argv(command, label="guest command")
        response = self._session.post(
            f"{self._base_url}/execute",
            json={"command": argv, "shell": False},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise OSWorldControllerError("guest execute 返回失败状态")
        returncode = payload.get("returncode")
        stdout = payload.get("output", "")
        stderr = payload.get("error", "")
        if (
            not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
            raise OSWorldControllerError("guest execute 返回字段类型异常")
        return CommandResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def execute_with_timeout(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        """在 guest 内以 ``shell=False`` 和单次超时执行 argv。

        输入参数：
            command：非空字符串参数序列；不会拼接成 shell 文本。
            timeout_seconds：当次 HTTP/guest 动作允许的超时秒数。
        输出返回值：
            guest 的退出码、标准输出和标准错误。
        异常：
            OSWorldControllerError：命令字段或 guest JSON 响应无效。
            requests HTTP 异常：guest 传输、超时或状态失败。
        """

        argv = _validate_argv(command, label="timed guest command")
        validated_timeout = _validate_call_scoped_timeout(timeout_seconds)
        response = self._session.post(
            f"{self._base_url}/execute",
            json={"command": argv, "shell": False},
            timeout=validated_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise OSWorldControllerError("timed guest execute 返回失败状态")
        returncode = payload.get("returncode")
        stdout = payload.get("output", "")
        stderr = payload.get("error", "")
        if (
            not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
            raise OSWorldControllerError("timed guest execute 返回字段类型异常")
        return CommandResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def launch(self, command: Sequence[str]) -> None:
        """在 guest 图形会话中以 ``shell=False`` 异步启动固定 argv。

        输入参数：
            command：非空字符串参数序列；调用方应来自版本化 setup spec。
        输出返回值：
            无；HTTP 成功表示 agent server 已接受启动请求。
        异常：
            OSWorldControllerError：命令字段无效。
            requests HTTP 异常：guest 拒绝或无法启动进程。
        """

        argv = _validate_argv(command, label="guest launch command")
        response = self._session.post(
            f"{self._base_url}/setup/launch",
            json={"command": argv, "shell": False},
            timeout=self._timeout,
        )
        response.raise_for_status()

    def read_file(
        self,
        guest_path: str,
        *,
        max_bytes: int,
    ) -> bytes:
        """从 guest 读取一个安全绝对路径且限制响应大小。

        输入参数：
            guest_path：由受信 evidence adapter 推导、无 ``..`` 的 POSIX
                绝对文件路径。
            max_bytes：调用方协议声明的最大允许字节数，范围 1–16 MiB。
        输出返回值：
            agent server ``/file`` 返回的原始文件字节；空文件允许返回。
        异常：
            OSWorldControllerError：路径、大小上限或响应类型/长度无效。
            requests HTTP 异常：文件不存在、不可读或传输失败。
        """

        if not isinstance(guest_path, str) or "\x00" in guest_path:
            raise OSWorldControllerError("guest file path 类型无效")
        parsed = PurePosixPath(guest_path)
        if not parsed.is_absolute() or ".." in parsed.parts or guest_path.endswith("/"):
            raise OSWorldControllerError("guest file path 必须是安全绝对文件")
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or not 1 <= max_bytes <= 16 * 1024 * 1024
        ):
            raise OSWorldControllerError("guest file max_bytes 超出安全范围")
        response = self._session.post(
            f"{self._base_url}/file",
            data={"file_path": guest_path},
            timeout=self._timeout,
            stream=True,
        )
        try:
            response.raise_for_status()
            headers = getattr(response, "headers", {})
            content_length = headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError):
                    raise OSWorldControllerError(
                        "guest file Content-Length 无效"
                    ) from None
                if declared_length < 0 or declared_length > max_bytes:
                    raise OSWorldControllerError("guest file 响应超过协议大小上限")

            iterator = getattr(response, "iter_content", None)
            if not callable(iterator):
                raise OSWorldControllerError("guest file 响应不支持有界流式读取")
            chunks: list[bytes] = []
            total = 0
            for chunk in iterator(chunk_size=min(64 * 1024, max_bytes + 1)):
                if not isinstance(chunk, (bytes, bytearray)):
                    raise OSWorldControllerError("guest file 响应分块类型无效")
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise OSWorldControllerError("guest file 响应超过协议大小上限")
                chunks.append(bytes(chunk))
            return b"".join(chunks)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        *,
        max_files: int,
        max_nodes: int,
        max_depth: int,
        max_name_bytes: int,
        max_file_bytes: int,
        max_total_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> tuple[tuple[str, int, str], ...]:
        """以单次固定 guest helper 捕获完整 Operation artifact manifest。

        输入参数：
            guest_directory：prepare 阶段冻结的规范 POSIX 绝对目录。
            max_files/max_nodes/max_depth/max_name_bytes：常规文件数、
                文件加目录总成员数、相对路径深度与单分量
                UTF-8 名称上限。
            max_file_bytes/max_total_bytes：单文件与整树原始字节上限。
            max_response_bytes/timeout_seconds：guest JSON、HTTP envelope
                与 helper 真实时间截止。
        输出返回值：
            按相对路径 UTF-8 字节序排列的
            ``(relative_path, size_bytes, sha256)`` 不可变闭集。
        异常：
            OSWorldControllerError：路径、上限、超时、传输、guest
                文件类型/稳定性或返回 schema 不可信；错误不回显
                目录、成员名、摘要或内容。
        """

        validated_path = _validate_guest_directory_path(guest_directory)
        limits = _validate_artifact_tree_manifest_limits(
            max_files=max_files,
            max_nodes=max_nodes,
            max_depth=max_depth,
            max_name_bytes=max_name_bytes,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            max_response_bytes=max_response_bytes,
        )
        validated_timeout_seconds = _validate_image_pixel_hash_timeout(timeout_seconds)
        effective_timeout_seconds = _validate_image_pixel_hash_timeout(
            min(float(self._timeout), validated_timeout_seconds)
        )
        stdout = self._execute_bounded_json_stdout(
            [
                "python",
                "-I",
                "-c",
                _ARTIFACT_TREE_MANIFEST_GUEST_PROGRAM,
                validated_path,
                *(str(limit) for limit in limits),
                str(effective_timeout_seconds),
            ],
            max_stdout_bytes=limits[6],
            timeout_seconds=effective_timeout_seconds,
            max_envelope_bytes=limits[6],
        )
        try:
            payload = _load_strict_directory_listing_json(stdout)
        except Exception:
            raise OSWorldControllerError(
                "guest artifact tree manifest JSON 无效"
            ) from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"records", "schema_version", "status"}
            or payload.get("schema_version") != _ARTIFACT_TREE_MANIFEST_SCHEMA
            or payload.get("status") != "success"
        ):
            raise OSWorldControllerError("guest artifact tree manifest schema 无效")
        return _validate_artifact_tree_manifest_records(
            payload.get("records"),
            max_files=limits[0],
            max_depth=limits[2],
            max_name_bytes=limits[3],
            max_file_bytes=limits[4],
            max_total_bytes=limits[5],
        )

    def collect_file_bytes(
        self,
        guest_path: str,
        *,
        max_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        """以单次固定 guest helper 收集一个普通文件。

        输入参数：
            guest_path：受信 evidence adapter 推导的规范 POSIX
                绝对文件路径。
            max_bytes：文件原始字节硬上限。
            max_response_bytes：guest JSON 与 HTTP envelope 的调用方
                响应字节上限。
            timeout_seconds：HTTP 与 guest helper 共用的截止秒数。
        输出返回值：
            严格 base64 解码后的原始 ``bytes``；空文件返回
            ``b""``，不做文本编码假设。
        异常：
            OSWorldGuestPathMissingError：文件或任一祖先目录以
                ENOENT 缺失。
            OSWorldControllerError：路径、类型、大小、timeout、传输、
                base64 或 schema 不符合契约。错误不回显路径或内容。
        """

        validated_path = _validate_guest_single_file_path(guest_path)
        (
            validated_max_bytes,
            validated_max_response_bytes,
        ) = _validate_single_file_limits(
            max_bytes=max_bytes,
            max_response_bytes=max_response_bytes,
        )
        validated_timeout_seconds = _validate_image_pixel_hash_timeout(timeout_seconds)
        effective_timeout_seconds = _validate_image_pixel_hash_timeout(
            min(float(self._timeout), validated_timeout_seconds)
        )
        stdout = self._execute_bounded_json_stdout(
            [
                "python",
                "-I",
                "-c",
                _SINGLE_FILE_GUEST_PROGRAM,
                validated_path,
                str(validated_max_bytes),
                str(validated_max_response_bytes),
                str(effective_timeout_seconds),
            ],
            max_stdout_bytes=validated_max_response_bytes,
            timeout_seconds=effective_timeout_seconds,
            max_envelope_bytes=validated_max_response_bytes,
        )
        try:
            payload = _load_strict_directory_listing_json(stdout)
        except Exception:
            raise OSWorldControllerError("guest single file JSON 无效") from None
        if not isinstance(payload, dict):
            raise OSWorldControllerError("guest single file schema 无效")
        if set(payload) == {"schema_version", "status"}:
            if (
                payload.get("schema_version") == _SINGLE_FILE_SCHEMA
                and payload.get("status") == "missing"
            ):
                raise OSWorldGuestPathMissingError("guest single file 未产生")
            raise OSWorldControllerError("guest single file schema 无效")
        if (
            set(payload)
            != {
                "content_base64",
                "encoding",
                "schema_version",
                "size_bytes",
                "status",
            }
            or payload.get("schema_version") != _SINGLE_FILE_SCHEMA
            or payload.get("status") != "success"
            or payload.get("encoding") != "base64"
        ):
            raise OSWorldControllerError("guest single file schema 无效")
        encoded_content = payload.get("content_base64")
        size_bytes = payload.get("size_bytes")
        if (
            not isinstance(encoded_content, str)
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 0 <= size_bytes <= validated_max_bytes
        ):
            raise OSWorldControllerError("guest single file payload 无效")
        try:
            decoded_content = base64.b64decode(
                encoded_content.encode("ascii", "strict"),
                validate=True,
            )
        except (binascii.Error, UnicodeEncodeError, ValueError):
            raise OSWorldControllerError("guest single file payload 无效") from None
        canonical_content = base64.b64encode(decoded_content).decode("ascii")
        if len(decoded_content) != size_bytes or canonical_content != encoded_content:
            raise OSWorldControllerError("guest single file payload 无效")
        return decoded_content

    def list_directory(
        self,
        guest_path: str,
        *,
        max_entries: int,
        max_name_bytes: int,
        max_response_bytes: int,
    ) -> tuple[str, ...]:
        """以固定 guest Python argv 枚举一层目录成员名。

        输入参数：
            guest_path：由受信 evidence adapter 推导的 POSIX 绝对
                目录路径。
            max_entries：允许返回的直接成员数上限，范围
                1–4096。
            max_name_bytes：每个成员名的 UTF-8 字节上限，范围
                1–255。
            max_response_bytes：客户机列表 JSON 的 UTF-8 总字节上限，
                范围 128–1 MiB。
        输出返回值：
            按 UTF-8 字节升序排列的直接成员名 tuple；不包含
            完整路径、文件内容或 guest 错误文本。
        异常：
            OSWorldControllerError：输入、UTF-8、JSON schema 或安全
                上限不成立。异常消息不回显路径或成员名。
        """

        validated_guest_path = _validate_guest_directory_path(guest_path)
        (
            validated_max_entries,
            validated_max_name_bytes,
            validated_max_response_bytes,
        ) = _validate_directory_listing_limits(
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
            max_response_bytes=max_response_bytes,
        )
        try:
            result = self.execute(
                [
                    "python",
                    "-I",
                    "-c",
                    _DIRECTORY_LISTING_GUEST_PROGRAM,
                    validated_guest_path,
                    str(validated_max_entries),
                    str(validated_max_name_bytes),
                    str(validated_max_response_bytes),
                ]
            )
            if result.returncode != 0 or result.stderr:
                raise OSWorldControllerError("guest directory listing 执行失败")
            encoded_response = result.stdout.encode("utf-8", "strict")
            if len(encoded_response) > validated_max_response_bytes:
                raise OSWorldControllerError("guest directory listing 超过响应上限")
            payload = _load_strict_directory_listing_json(result.stdout)
        except OSWorldControllerError:
            raise
        except Exception:
            raise OSWorldControllerError(
                "guest directory listing 请求或编码无效"
            ) from None

        if (
            not isinstance(payload, dict)
            or set(payload) != {"entries", "schema_version", "status"}
            or payload.get("schema_version") != _DIRECTORY_LISTING_SCHEMA
            or payload.get("status") != "success"
        ):
            raise OSWorldControllerError("guest directory listing schema 无效")
        return _validate_directory_listing_entries(
            payload.get("entries"),
            max_entries=validated_max_entries,
            max_name_bytes=validated_max_name_bytes,
        )

    def collect_image_pixel_hashes(
        self,
        guest_directory: str,
        *,
        max_entries: int,
        max_name_bytes: int,
        max_compressed_item_bytes: int,
        max_total_compressed_bytes: int,
        max_pixels_per_image: int,
        max_decoded_item_bytes: int,
        max_total_decoded_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> tuple[tuple[str, str], ...]:
        """按 guest 目录观察顺序收集 Pillow 像素 SHA-256。

        输入参数：
            guest_directory：受信 evidence adapter 推导的规范
                POSIX 绝对目录。
            max_entries/max_name_bytes：直接成员数和单名 UTF-8
                字节上限。
            max_compressed_item_bytes/max_total_compressed_bytes：单文件与
                本次收集的压缩文件字节上限。
            max_pixels_per_image：单图像宽乘高的像素上限。
            max_decoded_item_bytes/max_total_decoded_bytes：``Image.tobytes``
                单项与总解码字节上限。
            max_response_bytes：guest 证据 JSON 的 UTF-8 字节上限。
            timeout_seconds：guest helper 与 HTTP 请求的共同秒级
                截止上限。
        输出返回值：
            ``(pixel_sha256, member_name)`` 记录的不可变 tuple；
            保留 ``os.scandir`` 观察顺序，不按名称或哈希排序。
        异常：
            OSWorldGuestPathMissingError：最终目录以 ENOENT 缺失。
            OSWorldControllerError：路径、上限、HTTP envelope、Pillow
                解码或结果 schema 无效。错误不回显路径、名称或内容。
        """

        validated_directory = _validate_guest_directory_path(guest_directory)
        limits = _validate_image_pixel_hash_limits(
            max_entries=max_entries,
            max_name_bytes=max_name_bytes,
            max_compressed_item_bytes=max_compressed_item_bytes,
            max_total_compressed_bytes=max_total_compressed_bytes,
            max_pixels_per_image=max_pixels_per_image,
            max_decoded_item_bytes=max_decoded_item_bytes,
            max_total_decoded_bytes=max_total_decoded_bytes,
            max_response_bytes=max_response_bytes,
        )
        validated_timeout_seconds = _validate_image_pixel_hash_timeout(timeout_seconds)
        effective_timeout_seconds = _validate_image_pixel_hash_timeout(
            min(
                float(self._timeout),
                validated_timeout_seconds,
            )
        )
        command = [
            "python",
            "-I",
            "-c",
            _IMAGE_PIXEL_HASH_GUEST_PROGRAM,
            validated_directory,
            *(str(value) for value in limits),
            str(effective_timeout_seconds),
        ]
        stdout = self._execute_bounded_json_stdout(
            command,
            max_stdout_bytes=limits[-1],
            timeout_seconds=effective_timeout_seconds,
        )
        try:
            payload = _load_strict_directory_listing_json(stdout)
        except Exception:
            raise OSWorldControllerError("guest image pixel hash JSON 无效") from None
        if not isinstance(payload, dict) or set(payload) != {
            "records",
            "schema_version",
            "status",
        }:
            raise OSWorldControllerError("guest image pixel hash schema 无效")
        if (
            payload.get("schema_version") == _IMAGE_PIXEL_HASH_SCHEMA
            and payload.get("status") == "missing"
            and payload.get("records") == []
        ):
            raise OSWorldGuestPathMissingError("guest image directory 未产生")
        if (
            payload.get("schema_version") != _IMAGE_PIXEL_HASH_SCHEMA
            or payload.get("status") != "success"
        ):
            raise OSWorldControllerError("guest image pixel hash schema 无效")
        return _validate_image_pixel_hash_records(
            payload.get("records"),
            max_entries=limits[0],
            max_name_bytes=limits[1],
        )

    def _execute_bounded_json_stdout(
        self,
        command: Sequence[str],
        *,
        max_stdout_bytes: int,
        timeout_seconds: float,
        max_envelope_bytes: int | None = None,
    ) -> str:
        """执行固定 argv，并在 JSON envelope 解码前限制 HTTP 正文。

        输入参数：
            command：仅由 production getter 构造的固定 guest argv。
            max_stdout_bytes：guest stdout JSON 的已校验字节上限。
            timeout_seconds：已校验的当次 getter 截止秒数。
            max_envelope_bytes：可选的完整 HTTP JSON envelope 硬上限；
                ``None`` 时保留既有 getter 的兼容预算。
        输出返回值：
            agent-server 成功 envelope 中的严格 UTF-8 stdout。
        异常：
            OSWorldControllerError：HTTP 总量、UTF-8、envelope schema、
                returncode/stderr 或 stdout 上限不成立。不回显 guest 值。
        """

        argv = _validate_argv(command, label="bounded guest command")
        if max_envelope_bytes is None:
            envelope_byte_limit = 4096 + 4 * max_stdout_bytes
        elif (
            not isinstance(max_envelope_bytes, int)
            or isinstance(max_envelope_bytes, bool)
            or max_envelope_bytes < 1
        ):
            raise OSWorldControllerError("bounded guest execute envelope 上限无效")
        else:
            envelope_byte_limit = max_envelope_bytes
        stream_deadline = time.monotonic() + timeout_seconds
        try:
            response = self._session.post(
                f"{self._base_url}/execute",
                json={"command": argv, "shell": False},
                timeout=min(self._timeout, timeout_seconds),
                stream=True,
            )
        except Exception:
            raise OSWorldControllerError("bounded guest execute 传输失败") from None
        try:
            response.raise_for_status()
            content_length = getattr(response, "headers", {}).get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError):
                    raise OSWorldControllerError(
                        "bounded guest execute Content-Length 无效"
                    ) from None
                if declared_length < 0 or declared_length > envelope_byte_limit:
                    raise OSWorldControllerError("bounded guest execute envelope 超限")
            iterator = getattr(response, "iter_content", None)
            if not callable(iterator):
                raise OSWorldControllerError("bounded guest execute 不支持流式读取")
            chunks: list[bytes] = []
            total = 0
            for chunk in iterator(chunk_size=min(64 * 1024, envelope_byte_limit + 1)):
                if time.monotonic() >= stream_deadline:
                    raise OSWorldControllerError("bounded guest execute 响应超时")
                if not isinstance(chunk, (bytes, bytearray)):
                    raise OSWorldControllerError("bounded guest execute 响应分块无效")
                if not chunk:
                    continue
                total += len(chunk)
                if total > envelope_byte_limit:
                    raise OSWorldControllerError("bounded guest execute envelope 超限")
                chunks.append(bytes(chunk))
            if time.monotonic() >= stream_deadline:
                raise OSWorldControllerError("bounded guest execute 响应超时")
            serialized = b"".join(chunks).decode("utf-8", "strict")
            envelope = _load_strict_directory_listing_json(serialized)
        except OSWorldControllerError:
            raise
        except Exception:
            raise OSWorldControllerError(
                "bounded guest execute envelope 无效"
            ) from None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"status", "output", "error", "returncode"}
            or envelope.get("status") != "success"
            or not isinstance(envelope.get("returncode"), int)
            or isinstance(envelope.get("returncode"), bool)
            or envelope.get("returncode") != 0
            or envelope.get("error") != ""
            or not isinstance(envelope.get("output"), str)
        ):
            raise OSWorldControllerError("bounded guest execute envelope schema 无效")
        stdout = envelope["output"]
        try:
            encoded_stdout = stdout.encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise OSWorldControllerError(
                "bounded guest execute stdout 编码无效"
            ) from None
        if len(encoded_stdout) > max_stdout_bytes:
            raise OSWorldControllerError("bounded guest execute stdout 超限")
        return stdout

    def get_desktop_path(self) -> str:
        """查询 guest 当前用户的 Desktop 绝对路径。

        输入参数：
            无。
        输出返回值：
            经过 POSIX 绝对路径校验的 Desktop 路径；runtime 可由其父目录推导
            当前 guest home，避免硬编码用户名。
        异常：
            OSWorldControllerError：响应缺失或路径含父目录跳转。
        """

        response = self._session.post(
            f"{self._base_url}/desktop_path",
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        desktop_path = (
            payload.get("desktop_path") if isinstance(payload, dict) else None
        )
        if not isinstance(desktop_path, str):
            raise OSWorldControllerError("guest 未返回 desktop_path")
        parsed = PurePosixPath(desktop_path)
        if not parsed.is_absolute() or ".." in parsed.parts:
            raise OSWorldControllerError("guest desktop_path 不是安全绝对路径")
        return desktop_path

    def wait_for_chrome_cdp(
        self,
        *,
        port: int,
        timeout: float = 15.0,
        interval: float = 0.25,
    ) -> None:
        """等待 guest Chrome CDP ``/json/version`` 返回有效浏览器身份。

        输入参数：
            port：Chrome guest-local remote-debugging 端口。
            timeout：总等待上限，范围 0–120 秒。
            interval：两次结构化探测之间的间隔，范围 0–5 秒。
        输出返回值：
            无；CDP 返回 ``Browser`` 或 ``webSocketDebuggerUrl`` 时结束。
        异常：
            OSWorldControllerError：参数无效，或期限内始终未就绪。
        """

        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
            or not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= 120
            or not isinstance(interval, (int, float))
            or isinstance(interval, bool)
            or not 0 < interval <= 5
        ):
            raise OSWorldControllerError("Chrome CDP 等待参数无效")
        probe_code = (
            "import json,sys,urllib.request;"
            "port=int(sys.argv[1]);"
            "url=f'http://127.0.0.1:{port}/json/version';"
            "opener=urllib.request.build_opener("
            "urllib.request.ProxyHandler({}));"
            "payload=json.load(opener.open(url,timeout=1));"
            "raise SystemExit(0 if "
            "(payload.get('Browser') or payload.get('webSocketDebuggerUrl')) "
            "else 2)"
        )
        deadline = time.monotonic() + float(timeout)
        while True:
            try:
                result = self.execute(["python", "-c", probe_code, str(port)])
                if result.returncode == 0:
                    return
            except Exception:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSWorldControllerError(
                    "guest Chrome CDP 未在期限内就绪"
                ) from None
            time.sleep(min(float(interval), remaining))

    def wait_for_chrome_exit(
        self,
        *,
        timeout: float = 15.0,
        interval: float = 0.25,
    ) -> None:
        """等待 guest 中所有名为 ``chrome`` 的进程退出。

        输入参数：
            timeout：总等待上限，范围 0–120 秒。
            interval：两次结构化 ``pgrep`` 之间的间隔，范围 0–5 秒。
        输出返回值：
            无；``pgrep -x chrome`` 明确返回未找到进程时结束。
        异常：
            OSWorldControllerError：参数无效、探针执行异常，或期限内仍有
                Chrome 进程存活。
        """

        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < timeout <= 120
            or not isinstance(interval, (int, float))
            or isinstance(interval, bool)
            or not 0 < interval <= 5
        ):
            raise OSWorldControllerError("Chrome 退出等待参数无效")
        deadline = time.monotonic() + float(timeout)
        while True:
            try:
                result = self.execute(["pgrep", "-x", "chrome"])
            except Exception as error:
                raise OSWorldControllerError("guest Chrome 退出状态无法探测") from error
            if result.returncode == 1:
                return
            if result.returncode not in {0, 1}:
                raise OSWorldControllerError("guest Chrome 退出状态无法探测")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSWorldControllerError("guest Chrome 未在期限内退出")
            time.sleep(min(float(interval), remaining))

    def activate_window(self, window_name: str) -> None:
        """通过 OSWorld 结构化 setup endpoint 激活一个固定桌面窗口。

        输入参数：
            window_name：由版本化 setup spec 提供的非空窗口名。
        输出返回值：
            无；HTTP 成功表示 guest 已找到并激活窗口。
        异常：
            OSWorldControllerError：窗口名类型、长度或控制字符无效。
            requests HTTP 异常：guest 未找到窗口或激活失败。
        """

        if (
            not isinstance(window_name, str)
            or not window_name.strip()
            or window_name != window_name.strip()
            or len(window_name) > 256
            or any(ord(character) < 32 for character in window_name)
        ):
            raise OSWorldControllerError("guest window name 无效")
        response = self._session.post(
            f"{self._base_url}/setup/activate_window",
            json={
                "window_name": window_name,
                "strict": False,
                "by_class": False,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()

    def upload_file(self, local_path: Path, guest_path: str) -> None:
        """把已在 host 校验的普通文件上传到安全 guest 绝对路径。

        输入参数：
            local_path：host 上已验证大小与 SHA-256 的普通文件。
            guest_path：guest 内无 ``..`` 的 POSIX 绝对目标路径。
        输出返回值：
            无；成功时 agent server 已完整接收 multipart 文件。
        异常：
            OSWorldControllerError：本地文件、guest 路径或 mkdir 结果无效。
        """

        if not local_path.is_file() or local_path.is_symlink():
            raise OSWorldControllerError("上传来源必须是普通且非符号链接文件")
        parsed_guest_path = PurePosixPath(guest_path)
        if (
            not parsed_guest_path.is_absolute()
            or ".." in parsed_guest_path.parts
            or guest_path.endswith("/")
        ):
            raise OSWorldControllerError("guest upload 目标必须是安全绝对文件路径")
        mkdir_result = self.execute(["mkdir", "-p", str(parsed_guest_path.parent)])
        if mkdir_result.returncode != 0:
            raise OSWorldControllerError("guest 无法创建资产目标目录")
        with local_path.open("rb") as file:
            response = self._session.post(
                f"{self._base_url}/setup/upload",
                data={"file_path": guest_path},
                files={
                    "file_data": (
                        local_path.name,
                        file,
                        "application/octet-stream",
                    )
                },
                timeout=self._timeout,
            )
            response.raise_for_status()

    def wait_until_ready(
        self,
        *,
        timeout: float,
        interval: float = 2.0,
    ) -> None:
        """轮询截图 endpoint，直到 guest 图形环境可用或超时。

        输入参数：
            timeout：总等待上限秒数。
            interval：失败请求之间的等待秒数。
        输出返回值：
            无；首次取得非空截图即返回。
        异常：
            OSWorldControllerError：参数无效或期限内始终未就绪。
        """

        if timeout <= 0 or interval <= 0:
            raise OSWorldControllerError("readiness timeout/interval 必须大于零")
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.get_screenshot()
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise OSWorldControllerError(
                        "OSWorld guest 未在期限内就绪"
                    ) from None
                time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    def open_path(self, guest_path: str) -> None:
        """请求 guest 使用默认桌面应用打开安全绝对路径。

        输入参数：
            guest_path：要向 Agent 展示的文件或目录 POSIX 绝对路径。
        输出返回值：
            无；agent server 接受请求后返回。
        异常：
            OSWorldControllerError：路径不是安全绝对路径。
        """

        parsed = PurePosixPath(guest_path)
        if not parsed.is_absolute() or ".." in parsed.parts:
            raise OSWorldControllerError("open_path 需要安全 guest 绝对路径")
        response = self._session.post(
            f"{self._base_url}/setup/open_file",
            json={"path": guest_path},
            timeout=self._timeout,
        )
        response.raise_for_status()


def _validate_loopback_base_url(base_url: str) -> None:
    """验证 agent-server URL 只暴露在 loopback。

    输入参数：
        base_url：待验证的 controller endpoint。
    输出返回值：
        无；安全 URL 正常返回。
    异常：
        OSWorldControllerError：协议、主机、端口、凭据或路径不符合要求。
    """

    parts = urlsplit(base_url)
    is_loopback = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    has_userinfo = parts.username is not None or parts.password is not None
    has_extra = parts.path not in {"", "/"} or bool(parts.query) or bool(parts.fragment)
    try:
        has_port = parts.port is not None
    except ValueError as error:
        raise OSWorldControllerError("controller endpoint 端口无效") from error
    if (
        parts.scheme != "http"
        or not is_loopback
        or not has_port
        or has_userinfo
        or has_extra
    ):
        raise OSWorldControllerError(
            "controller endpoint 必须是无凭据的 loopback HTTP origin"
        )


def _validate_guest_directory_path(guest_path: object) -> str:
    """校验供受限目录枚举使用的规范 POSIX 绝对路径。

    输入参数：
        guest_path：待校验的未信任路径值。
    输出返回值：
        原样的规范 UTF-8 路径字符串，可作为固定 argv 的独立
        参数。
    异常：
        OSWorldControllerError：值非字符串、非绝对路径、过长，
            或含 NUL、控制字符、空分量、``.``/``..`` 分量。
            错误文本绝不包含输入值。
    """

    if not isinstance(guest_path, str):
        raise OSWorldControllerError("guest directory path 无效")
    try:
        encoded_path = guest_path.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise OSWorldControllerError("guest directory path 无效") from None
    if (
        not 1 <= len(encoded_path) <= 4096
        or "\x00" in guest_path
        or any(not character.isprintable() for character in guest_path)
    ):
        raise OSWorldControllerError("guest directory path 无效")
    if guest_path == "/":
        return guest_path
    components = guest_path.split("/")
    if (
        not guest_path.startswith("/")
        or guest_path.endswith("/")
        or components[0] != ""
        or any(component in {"", ".", ".."} for component in components[1:])
        or not PurePosixPath(guest_path).is_absolute()
    ):
        raise OSWorldControllerError("guest directory path 无效")
    return guest_path


def _validate_directory_listing_limits(
    *,
    max_entries: object,
    max_name_bytes: object,
    max_response_bytes: object,
) -> tuple[int, int, int]:
    """把目录枚举的三项资源限制收窄到安全闭区间。

    输入参数：
        max_entries：最大直接成员数。
        max_name_bytes：单个成员名 UTF-8 字节上限。
        max_response_bytes：列表 JSON 的 UTF-8 总字节上限。
    输出返回值：
        三项已验证的原生整数，顺序与输入一致。
    异常：
        OSWorldControllerError：任一值为 ``bool``/非整数，或不在
            1–4096、1–255、128–1 MiB 的对应闭区间。
            错误不回显配置值或 guest 路径。
    """

    values_and_bounds = (
        (max_entries, 1, 4096),
        (max_name_bytes, 1, 255),
        (max_response_bytes, 128, 1024 * 1024),
    )
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or not lower <= value <= upper
        for value, lower, upper in values_and_bounds
    ):
        raise OSWorldControllerError("guest directory listing 资源上限无效")
    return (
        int(max_entries),
        int(max_name_bytes),
        int(max_response_bytes),
    )


def _validate_directory_listing_entries(
    entries: object,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> tuple[str, ...]:
    """独立复核 guest 返回的一层目录成员集。

    输入参数：
        entries：从已解码 JSON 取得的未信任成员列表。
        max_entries：已校验的最大成员数。
        max_name_bytes：已校验的单名 UTF-8 字节上限。
    输出返回值：
        按 UTF-8 字节升序、无重复且可安全作为单个 POSIX
        路径分量的不可变名称 tuple。
    异常：
        OSWorldControllerError：结构、数量、排序、UTF-8、长度
            或路径分量契约无效。错误不回显任何成员名。
    """

    if not isinstance(entries, list) or len(entries) > max_entries:
        raise OSWorldControllerError("guest directory listing 成员集无效")
    validated: list[tuple[str, bytes]] = []
    for entry in entries:
        if not isinstance(entry, str):
            raise OSWorldControllerError("guest directory listing 成员集无效")
        try:
            encoded_entry = entry.encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise OSWorldControllerError("guest directory listing 成员集无效") from None
        if (
            not encoded_entry
            or len(encoded_entry) > max_name_bytes
            or entry in {".", ".."}
            or "/" in entry
            or any(not character.isprintable() for character in entry)
        ):
            raise OSWorldControllerError("guest directory listing 成员集无效")
        validated.append((entry, encoded_entry))
    encoded_entries = [encoded for _, encoded in validated]
    if len(set(encoded_entries)) != len(encoded_entries) or encoded_entries != sorted(
        encoded_entries
    ):
        raise OSWorldControllerError("guest directory listing 成员集无效")
    return tuple(entry for entry, _ in validated)


def _validate_artifact_tree_manifest_limits(
    *,
    max_files: object,
    max_nodes: object,
    max_depth: object,
    max_name_bytes: object,
    max_file_bytes: object,
    max_total_bytes: object,
    max_response_bytes: object,
) -> tuple[int, int, int, int, int, int, int]:
    """校验 Operation 递归 manifest getter 的七项资源上限。

    输入参数：
        max_files/max_nodes/max_depth/max_name_bytes：文件数、总成员
            节点数、相对路径分量数与单分量 UTF-8 字节限制。
        max_file_bytes/max_total_bytes：单文件与整树原始字节限制。
        max_response_bytes：guest stdout 与 HTTP envelope 的共享硬上限。
    输出返回值：
        按输入顺序排列的七个已验证原生整数。
    异常：
        OSWorldControllerError：任一值为 bool/非整数、越界，或单
            文件上限大于整树上限；错误不回显配置值。
    """

    values_and_bounds = (
        (max_files, 1, 4096),
        (max_nodes, 1, 8192),
        (max_depth, 1, 32),
        (max_name_bytes, 1, 255),
        (max_file_bytes, 1, 536_870_912),
        (max_total_bytes, 1, 1_073_741_824),
        (max_response_bytes, 512, 16_777_216),
    )
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or not lower <= value <= upper
        for value, lower, upper in values_and_bounds
    ):
        raise OSWorldControllerError("guest artifact tree manifest 资源上限无效")
    if int(max_files) > int(max_nodes) or int(max_file_bytes) > int(max_total_bytes):
        raise OSWorldControllerError("guest artifact tree manifest 资源上限无效")
    return (
        int(max_files),
        int(max_nodes),
        int(max_depth),
        int(max_name_bytes),
        int(max_file_bytes),
        int(max_total_bytes),
        int(max_response_bytes),
    )


def _validate_artifact_tree_manifest_records(
    records: object,
    *,
    max_files: int,
    max_depth: int,
    max_name_bytes: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[tuple[str, int, str], ...]:
    """独立复核 guest 返回的完整 artifact 文件记录闭集。

    输入参数：
        records：从严格 JSON 取得的未信任三元记录列表。
        max_files/max_depth/max_name_bytes/max_file_bytes/max_total_bytes：
            公开 getter 入口已验证的硬上限。
    输出返回值：
        路径规范、唯一、无前缀冲突、严格排序，且大小与
        SHA-256 合法的 ``(path, size, digest)`` 不可变元组。
    异常：
        OSWorldControllerError：任何 schema、路径、排序、摘要或上限
            不成立；错误不回显 guest 记录值。
    """

    if not isinstance(records, list) or len(records) > max_files:
        raise OSWorldControllerError("guest artifact tree manifest 记录无效")
    validated: list[tuple[str, int, str, bytes]] = []
    observed_paths: set[str] = set()
    total_bytes = 0
    for record in records:
        if not isinstance(record, list) or len(record) != 3:
            raise OSWorldControllerError("guest artifact tree manifest 记录无效")
        relative_name, size_bytes, sha256 = record
        if not isinstance(relative_name, str):
            raise OSWorldControllerError("guest artifact tree manifest 记录无效")
        try:
            encoded_path = relative_name.encode("utf-8", "strict")
            encoded_components = tuple(
                component.encode("utf-8", "strict")
                for component in relative_name.split("/")
            )
        except UnicodeEncodeError:
            raise OSWorldControllerError(
                "guest artifact tree manifest 记录无效"
            ) from None
        components = relative_name.split("/")
        path = PurePosixPath(relative_name)
        if (
            not encoded_path
            or relative_name.startswith("/")
            or relative_name.endswith("/")
            or "//" in relative_name
            or len(components) > max_depth
            or any(component in {"", ".", ".."} for component in components)
            or any(
                not encoded_component or len(encoded_component) > max_name_bytes
                for encoded_component in encoded_components
            )
            or any(not character.isprintable() for character in relative_name)
            or path.is_absolute()
            or path.as_posix() != relative_name
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 0 <= size_bytes <= max_file_bytes
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise OSWorldControllerError("guest artifact tree manifest 记录无效")
        if relative_name in observed_paths or any(
            parent.as_posix() in observed_paths
            for parent in path.parents
            if parent.as_posix() != "."
        ):
            raise OSWorldControllerError("guest artifact tree manifest 路径冲突")
        if any(existing.startswith(f"{relative_name}/") for existing in observed_paths):
            raise OSWorldControllerError("guest artifact tree manifest 路径冲突")
        observed_paths.add(relative_name)
        total_bytes += size_bytes
        if total_bytes > max_total_bytes:
            raise OSWorldControllerError("guest artifact tree manifest 超过大小上限")
        validated.append((relative_name, size_bytes, sha256, encoded_path))
    encoded_paths = [item[3] for item in validated]
    if encoded_paths != sorted(encoded_paths):
        raise OSWorldControllerError("guest artifact tree manifest 排序无效")
    return tuple((name, size, digest) for name, size, digest, _ in validated)


def _validate_image_pixel_hash_limits(
    *,
    max_entries: object,
    max_name_bytes: object,
    max_compressed_item_bytes: object,
    max_total_compressed_bytes: object,
    max_pixels_per_image: object,
    max_decoded_item_bytes: object,
    max_total_decoded_bytes: object,
    max_response_bytes: object,
) -> tuple[int, int, int, int, int, int, int, int]:
    """校验图像像素摘要 getter 的全部资源上限。

    输入参数：
        max_entries/max_name_bytes：直接成员数与单名 UTF-8
            字节上限。
        max_compressed_item_bytes/max_total_compressed_bytes：单个及总
            压缩输入字节上限。
        max_pixels_per_image：单张图像的像素数上限。
        max_decoded_item_bytes/max_total_decoded_bytes：单个及总
            Pillow 解码字节上限。
        max_response_bytes：guest JSON stdout 字节上限。
    输出返回值：
        按 guest 固定 argv 顺序排列的八项原生整数。
    异常：
        OSWorldControllerError：任一值非严格整数、超过安全闭
            区间，或单项上限大于对应总上限。错误不回显值。
    """

    values_and_bounds = (
        (max_entries, 1, 4_096),
        (max_name_bytes, 1, 255),
        (max_compressed_item_bytes, 1, 536_870_912),
        (max_total_compressed_bytes, 1, 1_073_741_824),
        (max_pixels_per_image, 1, 268_435_456),
        (max_decoded_item_bytes, 1, 536_870_912),
        (max_total_decoded_bytes, 1, 2_147_483_648),
        (max_response_bytes, 128, 1_048_576),
    )
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or not lower <= value <= upper
        for value, lower, upper in values_and_bounds
    ):
        raise OSWorldControllerError("guest image pixel hash 资源上限无效")
    if (
        max_compressed_item_bytes > max_total_compressed_bytes
        or max_decoded_item_bytes > max_total_decoded_bytes
    ):
        raise OSWorldControllerError("guest image pixel hash 资源上限无效")
    return (
        int(max_entries),
        int(max_name_bytes),
        int(max_compressed_item_bytes),
        int(max_total_compressed_bytes),
        int(max_pixels_per_image),
        int(max_decoded_item_bytes),
        int(max_total_decoded_bytes),
        int(max_response_bytes),
    )


def _validate_call_scoped_timeout(timeout_seconds: object) -> float:
    """校验 controller 单次命令的有界实时超时。

    输入参数：
        timeout_seconds：调用方提供的整数或浮点秒数。
    输出返回值：
        范围 ``0 < value <= 300`` 的有限 float。
    异常：
        OSWorldControllerError：值为 bool、非数值、非有限或越界。
    """

    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise OSWorldControllerError("timed guest execute timeout 无效")
    try:
        normalized_timeout = float(timeout_seconds)
    except (OverflowError, TypeError, ValueError):
        raise OSWorldControllerError("timed guest execute timeout 无效") from None
    if (
        not math.isfinite(normalized_timeout)
        or normalized_timeout <= 0
        or normalized_timeout > 300.0
    ):
        raise OSWorldControllerError("timed guest execute timeout 无效")
    return normalized_timeout


def _validate_image_pixel_hash_timeout(timeout_seconds: object) -> float:
    """校验专用图像 getter 的实时截止秒数。

    输入参数：
        timeout_seconds：来自版本化 evidence spec 的整数或浮点
            秒数。
    输出返回值：
        范围 ``0.001 <= value <= 300`` 的有限 float，可同时传给
        requests timeout 与 guest ``setitimer``。
    异常：
        OSWorldControllerError：值为 bool、非数值、非有限数或
            超出安全闭区间。错误不回显输入值。
    """

    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise OSWorldControllerError("guest image pixel hash timeout 无效")
    try:
        normalized_timeout = float(timeout_seconds)
    except (OverflowError, TypeError, ValueError):
        raise OSWorldControllerError("guest image pixel hash timeout 无效") from None
    if (
        not math.isfinite(normalized_timeout)
        or normalized_timeout < _MIN_IMAGE_PIXEL_HASH_TIMEOUT_SECONDS
        or normalized_timeout > 300.0
    ):
        raise OSWorldControllerError("guest image pixel hash timeout 无效")
    return normalized_timeout


def _validate_image_pixel_hash_records(
    records: object,
    *,
    max_entries: int,
    max_name_bytes: int,
) -> tuple[tuple[str, str], ...]:
    """独立复核 guest 返回的像素摘要记录。

    输入参数：
        records：未信任 JSON 中的记录列表。
        max_entries/max_name_bytes：已校验的成员数与名称
            UTF-8 字节上限。
    输出返回值：
        保留 guest 观察顺序的 ``(sha256, name)`` 不可变 tuple。
    异常：
        OSWorldControllerError：列表、字段闭集、名称、摘要或
            数量不符合契约。错误不回显任何 guest 值。
    """

    if not isinstance(records, list) or len(records) > max_entries:
        raise OSWorldControllerError("guest image pixel hash 记录无效")
    validated: list[tuple[str, str]] = []
    observed_names: set[bytes] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"name", "sha256"}:
            raise OSWorldControllerError("guest image pixel hash 记录无效")
        name = record.get("name")
        digest = record.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise OSWorldControllerError("guest image pixel hash 记录无效")
        try:
            encoded_name = name.encode("utf-8", "strict")
        except UnicodeEncodeError:
            raise OSWorldControllerError("guest image pixel hash 记录无效") from None
        if (
            not encoded_name
            or len(encoded_name) > max_name_bytes
            or name in {".", ".."}
            or "/" in name
            or any(not character.isprintable() for character in name)
            or encoded_name in observed_names
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise OSWorldControllerError("guest image pixel hash 记录无效")
        observed_names.add(encoded_name)
        validated.append((digest, name))
    return tuple(validated)


def _validate_guest_single_file_path(guest_path: object) -> str:
    """校验受限单文件 getter 的规范 POSIX 绝对路径。

    输入参数：
        guest_path：待校验的未信任路径值。
    输出返回值：
        不含空分量、``.``/``..``、控制字符或尾随分隔符的
        UTF-8 绝对文件路径。
    异常：
        OSWorldControllerError：路径非规范绝对文件路径。错误
            不回显输入值。
    """

    try:
        validated_path = _validate_guest_directory_path(guest_path)
    except OSWorldControllerError:
        raise OSWorldControllerError("guest single file path 无效") from None
    if validated_path == "/":
        raise OSWorldControllerError("guest single file path 无效")
    return validated_path


def _validate_single_file_limits(
    *,
    max_bytes: object,
    max_response_bytes: object,
) -> tuple[int, int]:
    """校验单文件 getter 的原始字节与响应字节上限。

    输入参数：
        max_bytes：文件原始字节数上限。
        max_response_bytes：guest JSON 与 HTTP envelope 字节上限。
    输出返回值：
        两项已校验的原生整数，顺序与输入一致。
    异常：
        OSWorldControllerError：任一值为 bool、非整数或超出
            ``1–512 MiB`` 与 ``512 B–16 MiB`` 的对应闭区间。
    """

    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or not 1 <= max_bytes <= 536_870_912
        or not isinstance(max_response_bytes, int)
        or isinstance(max_response_bytes, bool)
        or not 512 <= max_response_bytes <= 16_777_216
    ):
        raise OSWorldControllerError("guest single file 资源上限无效")
    return int(max_bytes), int(max_response_bytes)


def _load_strict_directory_listing_json(serialized: str) -> object:
    """解码目录列表 JSON，并拒绝重复键与非标准常量。

    输入参数：
        serialized：已经过 UTF-8 严格可编码检查的 guest stdout。
    输出返回值：
        Python JSON 值；顶层 object 的完整 schema 由调用方继续
        校验。
    异常：
        ValueError/JSONDecodeError：文本非唯一、标准的 JSON；调用
            方将其统一转为不回显证据值的 controller 错误。
    """

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        """将 object pair 序列转为字典，且禁止同名键。

        输入参数：
            pairs：JSON decoder 为当前 object 保留的有序键值对。
        输出返回值：
            键唯一的字典。
        异常：
            ValueError：发现重复键；异常不包含键名或值。
        """

        keys = [key for key, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("directory listing JSON 键不唯一")
        return dict(pairs)

    def reject_nonstandard_constant(_: str) -> object:
        """拒绝 JSON 规范外的 NaN/Infinity 常量。

        输入参数：
            _：解码器发现的非标准常量；故意不使用。
        输出返回值：
            不返回；始终拒绝。
        异常：
            ValueError：始终抛出，且不回显常量文本。
        """

        raise ValueError("directory listing JSON 含非标准常量")

    return json.loads(
        serialized,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_nonstandard_constant,
    )


def _validate_argv(
    command: Sequence[str],
    *,
    label: str,
) -> list[str]:
    """把 execute/launch 共用命令收敛为安全 argv 列表。

    输入参数：
        command：待校验的参数序列。
        label：不含命令值的错误类别名称。
    输出返回值：
        复制后的字符串 argv；调用方可安全放入结构化 JSON。
    异常：
        OSWorldControllerError：空命令、字符串伪序列、NUL 或非字符串字段。
    """

    if isinstance(command, (str, bytes)) or not command:
        raise OSWorldControllerError(f"{label} 必须是非空 argv")
    argv = list(command)
    if not all(isinstance(item, str) and "\x00" not in item for item in argv):
        raise OSWorldControllerError(f"{label} 只能包含无 NUL 的字符串")
    return argv
