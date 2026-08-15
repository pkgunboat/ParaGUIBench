"""WebMall Cart reference 专用的 attempt-owned qcow2 稳定绑定。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from paraguibench.integrations.osworld.docker_session import (
    OSWorldDockerConfig,
    OSWorldDockerSession,
)


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{12,64}")
_PIN_DIRECTORY_PREFIX = ".paraguibench-qcow2-"
_PINNED_FILENAME = "System.qcow2"
_HASH_CHUNK_BYTES = 4 * 1024 * 1024
DockerSessionFactory = Callable[[OSWorldDockerConfig], Any]


class WebMallCartQcow2AttestationError(RuntimeError):
    """表示 qcow2 无法以稳定隔离快照贯穿候选验证。"""

    code = "WEBMALL_CART_QCOW2_ATTESTATION_INVALID"

    def __init__(self) -> None:
        """构造不回显路径、摘要或 Docker 底层异常的固定错误。

        输入参数：无。
        输出返回值：无；公开文本只含稳定 code。
        """

        super().__init__(self.code)


class WebMallCartAttestedDockerSession:
    """在随机私有路径上物化 qcow2 隔离快照后委托 Docker。"""

    def __init__(
        self,
        *,
        config: OSWorldDockerConfig,
        expected_qcow2_sha256: str,
        session_factory: DockerSessionFactory | None = None,
    ) -> None:
        """绑定原始配置、manifest 摘要与可测试 Docker 工厂。

        输入参数：
            config：已通过 OSWorld 静态检查的 Docker 配置。
            expected_qcow2_sha256：browser image manifest 固定的解压镜像摘要。
            session_factory：可选边界工厂；production 使用
                ``OSWorldDockerSession``。
        输出返回值：无；构造阶段不写磁盘、不读镜像、不启动 Docker。
        异常：WebMallCartQcow2AttestationError：类型、摘要或工厂无效。
        """

        factory = OSWorldDockerSession if session_factory is None else session_factory
        if (
            not isinstance(config, OSWorldDockerConfig)
            or not isinstance(expected_qcow2_sha256, str)
            or _SHA256_PATTERN.fullmatch(expected_qcow2_sha256) is None
            or not callable(factory)
        ):
            raise WebMallCartQcow2AttestationError
        self._config = config
        self._expected_sha256 = expected_qcow2_sha256
        self._session_factory = factory
        self._delegate: Any | None = None
        self._parent_fd: int | None = None
        self._pin_directory_fd: int | None = None
        self._pinned_fd: int | None = None
        self._pin_directory_name: str | None = None
        self._pinned_path: Path | None = None
        self._pinned_identity: tuple[int, int, int, int, int, int, int] | None = None
        self._started = False
        self._closed = False

    def __repr__(self) -> str:
        """返回不含源路径、pin 路径或摘要的生命周期表示。

        输入参数：无。
        输出返回值：固定类名与 started/closed 布尔值。
        """

        return (
            "WebMallCartAttestedDockerSession("
            f"started={self._started!r}, closed={self._closed!r})"
        )

    def start(self) -> str:
        """复制并验证 qcow2，再仅把私有 snapshot path 交给 Docker。

        输入参数：无。
        输出返回值：已由底层 session 验证的 Docker 容器 ID。
        异常：WebMallCartQcow2AttestationError：重复启动、源摘要、
            inode/path 身份、Docker 启动或失败清理无法闭合。
        """

        if self._started or self._closed:
            raise WebMallCartQcow2AttestationError
        delegate_started = False
        try:
            self._pin_verified_qcow2()
            if self._pinned_path is None:
                raise TypeError
            pinned_config = replace(
                self._config,
                qcow2_path=self._pinned_path,
            )
            delegate = self._session_factory(pinned_config)
            if not callable(getattr(delegate, "start", None)) or not callable(
                getattr(delegate, "close", None)
            ):
                raise TypeError
            self._delegate = delegate
            container_id = delegate.start()
            delegate_started = True
            if (
                not isinstance(container_id, str)
                or _CONTAINER_ID_PATTERN.fullmatch(container_id) is None
            ):
                raise TypeError
            self._verify_pinned_identity(hash_content=False)
        except Exception:
            cleanup_failed = False
            if self._delegate is not None and delegate_started:
                try:
                    self._delegate.close()
                except Exception:
                    cleanup_failed = True
            if not cleanup_failed:
                try:
                    self._release_pin()
                except Exception:
                    cleanup_failed = True
            self._delegate = None
            self._started = False
            self._closed = True
            raise WebMallCartQcow2AttestationError from None
        self._started = True
        return container_id

    def close(self) -> None:
        """先清理 owned Docker，再复验持有 inode 字节并删除 pin。

        输入参数：无。
        输出返回值：无；重复关闭幂等。
        异常：WebMallCartQcow2AttestationError：容器清理、终态摘要或
            pin 清理失败。
        """

        if self._closed:
            return
        failed = False
        if self._delegate is not None:
            try:
                self._delegate.close()
            except Exception:
                failed = True
        if not failed:
            try:
                self._verify_pinned_identity(hash_content=True)
            except Exception:
                failed = True
            try:
                self._release_pin()
            except Exception:
                failed = True
        self._delegate = None
        self._started = False
        self._closed = True
        if failed:
            raise WebMallCartQcow2AttestationError from None

    def attests_closed_manifest(
        self,
        *,
        container_image: str,
        extracted_qcow2_sha256: str,
    ) -> bool:
        """检查成功 close 的会话是否精确绑定当前镜像清单。

        输入参数：container_image 为同一份 current manifest 中的
            OCI digest 引用；extracted_qcow2_sha256 为其解压镜像摘要。
        输出返回值：仅当本实例使用未注入的正式 Docker session，
            OCI/qcow2 两个身份精确相等，且 Docker 与私有 snapshot
            已成功关闭清理时返回 ``True``；否则返回 ``False``。
        """

        return (
            isinstance(container_image, str)
            and container_image == self._config.image
            and isinstance(extracted_qcow2_sha256, str)
            and _SHA256_PATTERN.fullmatch(extracted_qcow2_sha256) is not None
            and extracted_qcow2_sha256 == self._expected_sha256
            and self._session_factory is OSWorldDockerSession
            and self._closed
            and not self._started
            and self._delegate is None
            and self._parent_fd is None
            and self._pin_directory_fd is None
            and self._pinned_fd is None
            and self._pin_directory_name is None
            and self._pinned_path is None
            and self._pinned_identity is None
        )

    def _pin_verified_qcow2(self) -> None:
        """以 held FD 读取源字节并建立内容隔离快照。

        输入参数：无；使用构造时固定的源路径与预期摘要。
        输出返回值：无；成功后保留 parent/pin/file 三个 FD 到 close。
        异常：WebMallCartQcow2AttestationError：路径链、源文件、摘要、
            隔离复制或 snapshot 身份验证无效。
        """

        directory_fds: list[int] = []
        source_fd: int | None = None
        pin_directory_fd: int | None = None
        snapshot_write_fd: int | None = None
        pinned_fd: int | None = None
        pin_directory_name: str | None = None
        pinned_created = False
        try:
            source_path = self._config.qcow2_path
            if not source_path.is_absolute() or not source_path.name:
                raise OSError
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            directory = getattr(os, "O_DIRECTORY", 0)
            cloexec = getattr(os, "O_CLOEXEC", 0)
            if nofollow == 0 or directory == 0:
                raise OSError
            root_fd = os.open(
                source_path.anchor,
                os.O_RDONLY | directory | nofollow | cloexec,
            )
            directory_fds.append(root_fd)
            for part in source_path.parts[1:-1]:
                directory_fds.append(
                    os.open(
                        part,
                        os.O_RDONLY | directory | nofollow | cloexec,
                        dir_fd=directory_fds[-1],
                    )
                )
            parent_fd = directory_fds[-1]
            source_fd = os.open(
                source_path.name,
                os.O_RDONLY | nofollow | cloexec,
                dir_fd=parent_fd,
            )
            source_before = os.fstat(source_fd)
            if not stat.S_ISREG(source_before.st_mode) or source_before.st_size <= 0:
                raise OSError
            pin_directory_name = _create_private_pin_directory(parent_fd)
            pin_directory_fd = os.open(
                pin_directory_name,
                os.O_RDONLY | directory | nofollow | cloexec,
                dir_fd=parent_fd,
            )
            snapshot_write_fd = os.open(
                _PINNED_FILENAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | cloexec,
                0o600,
                dir_fd=pin_directory_fd,
            )
            pinned_created = True
            source_digest = _copy_fd_to_snapshot(
                source_fd=source_fd,
                snapshot_fd=snapshot_write_fd,
                expected_size=source_before.st_size,
            )
            source_after = os.fstat(source_fd)
            if (
                _strict_file_identity(source_before)
                != _strict_file_identity(source_after)
                or source_digest != self._expected_sha256
            ):
                raise OSError
            os.fsync(snapshot_write_fd)
            os.fchmod(snapshot_write_fd, 0o400)
            os.fsync(snapshot_write_fd)
            snapshot_written = os.fstat(snapshot_write_fd)
            if (
                not stat.S_ISREG(snapshot_written.st_mode)
                or stat.S_IMODE(snapshot_written.st_mode) != 0o400
                or snapshot_written.st_nlink != 1
                or snapshot_written.st_size != source_before.st_size
            ):
                raise OSError
            pinned_fd = os.open(
                _PINNED_FILENAME,
                os.O_RDONLY | nofollow | cloexec,
                dir_fd=pin_directory_fd,
            )
            pinned_metadata = os.fstat(pinned_fd)
            if (
                _snapshot_file_identity(pinned_metadata)
                != _snapshot_file_identity(snapshot_written)
                or _stable_fd_sha256(pinned_fd) != self._expected_sha256
            ):
                raise OSError
            os.close(snapshot_write_fd)
            snapshot_write_fd = None
            resolved_parent = source_path.parent.resolve(strict=True)
            resolved_parent_metadata = resolved_parent.stat()
            parent_metadata = os.fstat(parent_fd)
            if (
                resolved_parent_metadata.st_dev,
                resolved_parent_metadata.st_ino,
            ) != (
                parent_metadata.st_dev,
                parent_metadata.st_ino,
            ):
                raise OSError
            pinned_path = resolved_parent / pin_directory_name / _PINNED_FILENAME
            pinned_path_metadata = pinned_path.stat(follow_symlinks=False)
            if (
                pinned_path_metadata.st_dev,
                pinned_path_metadata.st_ino,
            ) != (
                pinned_metadata.st_dev,
                pinned_metadata.st_ino,
            ):
                raise OSError
            self._parent_fd = parent_fd
            self._pin_directory_fd = pin_directory_fd
            self._pinned_fd = pinned_fd
            self._pin_directory_name = pin_directory_name
            self._pinned_path = pinned_path
            self._pinned_identity = _snapshot_file_identity(pinned_metadata)
            for descriptor in reversed(directory_fds[:-1]):
                _close_quietly(descriptor)
            directory_fds = [parent_fd]
            os.close(source_fd)
            source_fd = None
            pin_directory_fd = None
            pinned_fd = None
            directory_fds = []
        except Exception:
            if pinned_fd is not None:
                _close_quietly(pinned_fd)
            if snapshot_write_fd is not None:
                _close_quietly(snapshot_write_fd)
            if pinned_created and pin_directory_fd is not None:
                try:
                    os.unlink(_PINNED_FILENAME, dir_fd=pin_directory_fd)
                except OSError:
                    pass
            if pin_directory_fd is not None:
                _close_quietly(pin_directory_fd)
            if pin_directory_name is not None and directory_fds:
                try:
                    os.rmdir(pin_directory_name, dir_fd=directory_fds[-1])
                except OSError:
                    pass
            if source_fd is not None:
                _close_quietly(source_fd)
            for descriptor in reversed(directory_fds):
                _close_quietly(descriptor)
            raise WebMallCartQcow2AttestationError from None

    def _verify_pinned_identity(self, *, hash_content: bool) -> None:
        """复验 held FD、私有目录 leaf 与 Docker 路径是同一稳定 inode。

        输入参数：hash_content 为真时还对 held FD 重算完整摘要。
        输出返回值：无；身份与可选字节摘要全部一致。
        异常：WebMallCartQcow2AttestationError：任一 FD/path/content 漂移。
        """

        if (
            self._pin_directory_fd is None
            or self._pinned_fd is None
            or self._pinned_path is None
            or self._pinned_identity is None
        ):
            raise WebMallCartQcow2AttestationError
        try:
            held = os.fstat(self._pinned_fd)
            leaf = os.stat(
                _PINNED_FILENAME,
                dir_fd=self._pin_directory_fd,
                follow_symlinks=False,
            )
            public_path = self._pinned_path.stat(follow_symlinks=False)
            if (
                _snapshot_file_identity(held) != self._pinned_identity
                or _snapshot_file_identity(leaf) != self._pinned_identity
                or _snapshot_file_identity(public_path) != self._pinned_identity
                or not stat.S_ISREG(leaf.st_mode)
                or stat.S_IMODE(leaf.st_mode) != 0o400
                or leaf.st_nlink != 1
            ):
                raise OSError
            if hash_content and _stable_fd_sha256(self._pinned_fd) != (
                self._expected_sha256
            ):
                raise OSError
        except Exception:
            raise WebMallCartQcow2AttestationError from None

    def _release_pin(self) -> None:
        """关闭 held FD 并以 dirfd 删除本 Attempt 的单文件私有目录。

        输入参数：无。
        输出返回值：无；全部句柄与命名资源清空。
        异常：WebMallCartQcow2AttestationError：unlink/rmdir/close 任一失败。
        """

        failed = False
        if self._pin_directory_fd is not None:
            try:
                os.unlink(_PINNED_FILENAME, dir_fd=self._pin_directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                failed = True
        for descriptor in (self._pinned_fd, self._pin_directory_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    failed = True
        if self._parent_fd is not None and self._pin_directory_name is not None:
            try:
                os.rmdir(self._pin_directory_name, dir_fd=self._parent_fd)
            except OSError:
                failed = True
        if self._parent_fd is not None:
            try:
                os.close(self._parent_fd)
            except OSError:
                failed = True
        self._parent_fd = None
        self._pin_directory_fd = None
        self._pinned_fd = None
        self._pin_directory_name = None
        self._pinned_path = None
        self._pinned_identity = None
        if failed:
            raise WebMallCartQcow2AttestationError


def _create_private_pin_directory(parent_fd: int) -> str:
    """在 held parent dirfd 下原子创建随机 0700 单 Attempt 目录。

    输入参数：parent_fd 为 qcow2 真实父目录句柄。
    输出返回值：新目录单分量名称。
    异常：OSError：八次随机名均冲突或创建失败。
    """

    for _attempt in range(8):
        name = _PIN_DIRECTORY_PREFIX + secrets.token_hex(16)
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        return name
    raise OSError


def _stable_fd_sha256(descriptor: int) -> str:
    """在同一 FD 上重置 offset、流式哈希并验证元数据稳定。

    输入参数：descriptor 为持有的普通 qcow2 文件 FD。
    输出返回值：完整字节的 64 位小写 SHA-256。
    异常：OSError：类型、读取或哈希前后元数据漂移。
    """

    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise OSError
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = before.st_size
    while remaining:
        chunk = os.read(descriptor, min(_HASH_CHUNK_BYTES, remaining))
        if not chunk:
            raise OSError
        digest.update(chunk)
        remaining -= len(chunk)
    after = os.fstat(descriptor)
    if _strict_file_identity(before) != _strict_file_identity(after):
        raise OSError
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _copy_fd_to_snapshot(
    *,
    source_fd: int,
    snapshot_fd: int,
    expected_size: int,
) -> str:
    """从 held 源 FD 精确复制固定尺寸并同时计算摘要。

    输入参数：source_fd/snapshot_fd 分别为只读源句柄与新建
        ``O_EXCL`` 快照写句柄；expected_size 为源文件初始尺寸。
    输出返回值：实际复制字节的 SHA-256。
    异常：OSError：尺寸、读取或短写无法完整闭合。
    """

    if expected_size <= 0:
        raise OSError
    os.lseek(source_fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = expected_size
    while remaining:
        chunk = os.read(source_fd, min(_HASH_CHUNK_BYTES, remaining))
        if not chunk:
            raise OSError
        digest.update(chunk)
        offset = 0
        while offset < len(chunk):
            written = os.write(snapshot_fd, chunk[offset:])
            if written <= 0:
                raise OSError
            offset += written
        remaining -= len(chunk)
    os.lseek(source_fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _strict_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    """投影用于单次哈希前后竞态检测的严格文件身份。

    输入参数：metadata 为 ``os.fstat`` 结果。
    输出返回值：device、inode、size、mtime 与 ctime 纳秒元组。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_file_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int]:
    """投影内容隔离 snapshot 的严格稳定身份。

    输入参数：metadata 为 snapshot FD/path 元数据。
    输出返回值：device、inode、size、mtime、ctime、权限与
        link count；快照与源 inode 独立，因此任一字段漂移均失败。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
    )


def _close_quietly(descriptor: int) -> None:
    """在错误回滚路径尽力关闭一个 FD。

    输入参数：descriptor 为待关闭句柄。
    输出返回值：无；close 错误被本地吸收，主错误保持固定。
    """

    try:
        os.close(descriptor)
    except OSError:
        pass
