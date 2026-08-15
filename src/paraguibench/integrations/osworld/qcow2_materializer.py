"""OSWorld 固定 ZIP 归档到只读 qcow2 的安全物化边界。

生产协议限定 Linux：输入通过从 ``/`` 逐级 nofollow 打开的
held-FD 链读取；输出使用 ``O_TMPFILE`` 匿名 inode，在完整
fsync、0400 和两次落盘摘要校验后，由 ``linkat(AT_EMPTY_PATH)``
直接从 held FD 排他发布为 ``Ubuntu.qcow2``。失败时仅关闭
匿名 FD，因此不存在 temp 路径的 stat→unlink/rename TOCTOU。

该模块不覆盖、移动或删除归档、已有 qcow2 或非本次资源。
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass, field
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
from typing import Any, BinaryIO, NoReturn, Protocol
import weakref
import zipfile


MATERIALIZATION_PROTOCOL = "paraguibench.osworld.qcow2-zip-materializer.v1"
MATERIALIZATION_PROTOCOL_VERSION = 1
_ARCHIVE_FILENAME = "Ubuntu.qcow2.zip"
_OUTPUT_FILENAME = "Ubuntu.qcow2"
_FORMAL_MANIFEST_RELATIVE_PARTS = (
    "environments",
    "osworld",
    "image-manifest.json",
)
_COPY_CHUNK_BYTES = 4 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 16 * 1024 * 1024 * 1024
_MAX_IMAGE_BYTES = 64 * 1024 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_MAX_MANIFEST_BYTES = 1_048_576
_AT_FDCWD = -100
_AT_SYMLINK_FOLLOW = 0x400
_AT_EMPTY_PATH = 0x1000


class OSWorldQcow2MaterializationError(RuntimeError):
    """表示归档、ZIP 闭集、输出或文件系统竞态校验失败。"""


@dataclass
class _HeldAbsolutePath:
    """保存从根目录逐级 nofollow 打开的 held-FD 链。

    输入参数：component_names 是根后路径分量；descriptors
        保存根与每个分量的 FD；entry_identities 只绑定
        dev/inode/type/uid，避免无关目录 mtime 变化造成误拒绝。
    输出返回值：可在操作期间持续验证路径连续性的句柄。
    """

    component_names: tuple[str, ...]
    descriptors: list[int]
    entry_identities: tuple[tuple[int, ...], ...]
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def leaf_descriptor(self) -> int:
        """返回最后路径分量的 held descriptor。

        输入参数：无。
        输出返回值：未关闭 FD 链的最后一项。
        """

        if self._closed:
            raise OSWorldQcow2MaterializationError("OSWorld held path 已关闭")
        return self.descriptors[-1]

    def verify_continuity(self) -> None:
        """从 held 根目录逐级重开并校验每个路径 entry。

        输入参数：无；使用初始 held 目录 FD 作为 dirfd。
        输出返回值：无；任一 symlink/ABA/类型漂移均抛错。
        """

        if self._closed:
            raise OSWorldQcow2MaterializationError("OSWorld held path 已关闭")
        transient: list[int] = []
        try:
            parent = self.descriptors[0]
            if _entry_identity(os.fstat(parent)) != self.entry_identities[0]:
                raise OSError
            for index, name in enumerate(self.component_names, start=1):
                expected = self.entry_identities[index]
                expected_type = expected[2]
                flags = os.O_RDONLY | _required_flag("O_NOFOLLOW")
                if expected_type == stat.S_IFDIR:
                    flags |= _required_flag("O_DIRECTORY")
                else:
                    flags |= getattr(os, "O_NONBLOCK", 0)
                descriptor = os.open(
                    name,
                    flags | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent,
                )
                transient.append(descriptor)
                if _entry_identity(os.fstat(descriptor)) != expected:
                    raise OSError
                parent = descriptor
        except OSError:
            raise OSWorldQcow2MaterializationError(
                "OSWorld 路径 held-FD 连续性校验失败"
            ) from None
        finally:
            for descriptor in reversed(transient):
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def close(self) -> None:
        """幂等地逆序关闭 held-FD 链。

        输入参数：无。
        输出返回值：无；重复调用不会关闭被内核复用的 FD。
        """

        if self._closed:
            return
        self._closed = True
        while self.descriptors:
            descriptor = self.descriptors.pop()
            try:
                os.close(descriptor)
            except OSError:
                pass


@dataclass(frozen=True)
class OSWorldQcow2MaterializationSpec:
    """保存 ZIP→qcow2 物化所需的完整不可变身份。

    输入参数：协议 ID/版本，archive/member/output 路径，
        大小、SHA-256、压缩法、flags、creator system、external attributes
        和 CRC32。
    输出返回值：不可变规格；该类本身不执行 I/O。
    """

    protocol: str
    protocol_version: int
    archive_path: str
    archive_size: int
    archive_sha256: str
    member_path: str
    member_compression_method: int
    member_flags: int
    member_creator_system: int
    member_external_attributes: int
    member_local_extra_hex: str
    member_central_extra_hex: str
    member_compressed_size: int
    member_uncompressed_size: int
    member_crc32: int
    output_path: str
    output_size: int
    output_sha256: str


def _close_materialized_capability(
    final_descriptor: int,
    output_handle: _HeldAbsolutePath,
    archive_handle: _HeldAbsolutePath,
    manifest_handle: _HeldAbsolutePath | None,
) -> None:
    """释放物化结果拥有的 output/archive/manifest capability。

    输入参数：final_descriptor 是最终只读 inode 的私有权威 FD；
        output_handle 是从根目录到输出父目录的 held-FD 链；
        archive_handle 始终持有固定 ZIP；manifest_handle 在正式
        repo-root 路径中持有固定 image manifest，低层 spec 测试可为空。
    输出返回值：无；先关闭输出文件 FD，再幂等关闭三类路径链。
    注意：该函数既由显式 ``close`` 调用，也作为 ``weakref.finalize``
        的泄漏兜底；正确性仍要求调用方显式关闭或使用上下文管理器。
    """

    try:
        os.close(final_descriptor)
    except OSError:
        pass
    output_handle.close()
    archive_handle.close()
    if manifest_handle is not None:
        manifest_handle.close()


@dataclass(eq=False)
class MaterializedOSWorldQcow2:
    """持有已核验 qcow2 inode 与最终名称连续性的可关闭 capability。

    输入参数：``_image_path`` 仅是便于部署定位的非权威路径；
        ``_sha256``/``_size`` 是 recipe 固定身份；``_final_descriptor``
        是始终指向已完整求摘要 inode 的只读权威 FD；``_output_handle``
        持有从根目录到输出父目录的 nofollow 路径链；
        ``_expected_identity`` 是首次完整摘要结束时的 full-stat 快照；
        archive 字段持有产生该输出的同一归档 FD/路径/摘要身份；正式
        repo-root 字段还持有解析 recipe 的同一 manifest FD/路径/摘要身份。
    输出返回值：支持 ``with``、显式幂等 ``close``、轻量
        ``verify_current`` 与重新完整求摘要的 ``verify_full``。
    注意：权威身份是 held FD，不是 ``image_path``。对象关闭后所有
        身份与路径访问 API 均失败关闭；``weakref.finalize`` 只防泄漏，
        不替代确定性的上下文管理。
    """

    _image_path: Path = field(repr=False)
    _sha256: str = field(repr=False)
    _size: int = field(repr=False)
    _final_descriptor: int = field(repr=False)
    _output_handle: _HeldAbsolutePath = field(repr=False)
    _expected_identity: tuple[int, ...] = field(repr=False)
    _archive_handle: _HeldAbsolutePath = field(repr=False)
    _archive_expected_identity: tuple[int, ...] = field(repr=False)
    _archive_sha256: str = field(repr=False)
    _archive_size: int = field(repr=False)
    _manifest_handle: _HeldAbsolutePath | None = field(repr=False)
    _manifest_expected_identity: tuple[int, ...] | None = field(repr=False)
    _manifest_sha256: str | None = field(repr=False)
    _manifest_size: int | None = field(repr=False)
    _finalizer: weakref.finalize = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """登记不依赖析构器顺序的 FD 泄漏兜底。

        输入参数：无；使用构造阶段取得所有权的私有 FD/held path。
        输出返回值：无；保存可由 ``close`` 确定触发的 finalizer。
        """

        self._finalizer = weakref.finalize(
            self,
            _close_materialized_capability,
            self._final_descriptor,
            self._output_handle,
            self._archive_handle,
            self._manifest_handle,
        )

    @property
    def closed(self) -> bool:
        """报告 capability 是否已释放。

        输入参数：无。
        输出返回值：显式关闭、上下文退出或 finalizer 已执行时为真。
        """

        return not self._finalizer.alive

    def _require_open(self) -> None:
        """拒绝在 capability 释放后继续读取身份或执行验证。

        输入参数：无。
        输出返回值：仍持有权威 FD 时正常返回；否则抛出固定错误。
        """

        if self.closed:
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 物化 capability 已关闭"
            )

    @property
    def image_path(self) -> Path:
        """返回当前部署定位用的非权威 pathname。

        输入参数：无。
        输出返回值：固定输出路径；调用方不得将其替代 held-FD 身份。
        """

        self._require_open()
        return self._image_path

    @property
    def output_name(self) -> str:
        """返回不含宿主目录信息的固定输出文件名。

        输入参数：无。
        输出返回值：recipe 固定的单段文件名。
        """

        self._require_open()
        return self._image_path.name

    @property
    def sha256(self) -> str:
        """返回 capability 所持 inode 的预期完整 SHA-256。

        输入参数：无。
        输出返回值：物化与首次完整重读均已验证的摘要。
        """

        self._require_open()
        return self._sha256

    @property
    def size(self) -> int:
        """返回 capability 所持 inode 的预期字节数。

        输入参数：无。
        输出返回值：recipe 固定且已验证的正整数字节数。
        """

        self._require_open()
        return self._size

    def verify_current(self) -> None:
        """轻量重验 held FD 完整 stat 与最终 pathname 连续性。

        输入参数：无；使用对象私有的最终只读 FD、输出父目录
            held-FD 链和首次完整摘要结束时的 full-stat 快照。
        输出返回值：无；FD stat、父目录权限/路径链或最终名称
            指向的 inode 任一漂移均抛错。该方法不重新读取 24 GB 内容。
        """

        self._require_open()
        # 先核对 pathname capability，使目录项替换被明确归类为路径漂移；
        # rename/unlink 在部分文件系统也会改变 held inode 的 ctime，若先
        # 比较 full-stat，会把同一次 pathname 攻击模糊成 FD 身份漂移。
        _verify_held_source_current(
            self._archive_handle,
            self._archive_expected_identity,
            label="ZIP",
        )
        if self._manifest_handle is not None:
            if self._manifest_expected_identity is None:
                raise OSWorldQcow2MaterializationError(
                    "OSWorld image manifest capability 身份缺失"
                )
            _verify_held_source_current(
                self._manifest_handle,
                self._manifest_expected_identity,
                label="image manifest",
            )
        self._output_handle.verify_continuity()
        parent_descriptor = self._output_handle.leaf_descriptor
        _verify_private_output_parent(parent_descriptor)
        _verify_final_name_continuity(
            parent_descriptor,
            self._image_path.name,
            self._expected_identity,
        )
        try:
            current_identity = _full_file_identity(os.fstat(self._final_descriptor))
        except OSError:
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 权威 FD 无法重验"
            ) from None
        if current_identity != self._expected_identity:
            raise OSWorldQcow2MaterializationError("OSWorld qcow2 权威 FD 身份漂移")
        _verify_held_source_current(
            self._archive_handle,
            self._archive_expected_identity,
            label="ZIP",
        )
        if self._manifest_handle is not None:
            _verify_held_source_current(
                self._manifest_handle,
                self._manifest_expected_identity,
                label="image manifest",
            )

    def verify_full(self) -> None:
        """从权威 held FD 重新完整核验 qcow2 摘要与路径连续性。

        输入参数：无；使用构造时保存的大小、SHA-256 与 stat 身份。
        输出返回值：无；先轻量重验，再完整读取同一 held FD，最后
            再重验路径；用于正式证据输出前的 24 GB 级强校验。
        """

        self.verify_current()
        archive_hashed_identity = _hash_exact_descriptor(
            self._archive_handle.leaf_descriptor,
            expected_size=self._archive_size,
            expected_sha256=self._archive_sha256,
            label="ZIP capability",
        )
        if archive_hashed_identity != self._archive_expected_identity:
            raise OSWorldQcow2MaterializationError(
                "OSWorld ZIP capability 完整重验身份漂移"
            )
        if self._manifest_handle is not None:
            if (
                self._manifest_expected_identity is None
                or self._manifest_sha256 is None
                or self._manifest_size is None
            ):
                raise OSWorldQcow2MaterializationError(
                    "OSWorld image manifest capability 身份缺失"
                )
            manifest_hashed_identity = _hash_exact_descriptor(
                self._manifest_handle.leaf_descriptor,
                expected_size=self._manifest_size,
                expected_sha256=self._manifest_sha256,
                label="image manifest capability",
            )
            if manifest_hashed_identity != self._manifest_expected_identity:
                raise OSWorldQcow2MaterializationError(
                    "OSWorld image manifest capability 完整重验身份漂移"
                )
        output_hashed_identity = _hash_exact_descriptor(
            self._final_descriptor,
            expected_size=self._size,
            expected_sha256=self._sha256,
            label="qcow2 capability",
        )
        if output_hashed_identity != self._expected_identity:
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 权威 FD 完整重验身份漂移"
            )
        self.verify_current()

    def close(self) -> None:
        """幂等释放 output、archive 与可选 manifest 的全部 capability。

        输入参数：无。
        输出返回值：无；重复调用不会关闭被内核复用的 descriptor。
        """

        self._finalizer()

    def __enter__(self) -> MaterializedOSWorldQcow2:
        """进入物化 capability 上下文。

        输入参数：无。
        输出返回值：仍处于打开状态的当前对象。
        """

        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """退出上下文并确定性释放 capability。

        输入参数：标准上下文异常三元组；本方法不抑制异常。
        输出返回值：无。
        """

        del exc_type, exc_value, traceback
        self.close()


class _AnonymousOutputBoundary(Protocol):
    """定义可在测试中替换的匿名 inode 系统边界。"""

    prepublish_nlink: int

    def open_anonymous(self, parent_descriptor: int) -> int:
        """创建无 pathname 的 0600 O_RDWR inode 并返回 FD。"""

    def reopen_readonly(self, descriptor: int) -> int:
        """从 held inode 重开 O_RDONLY FD并返回。"""

    def publish_noreplace(
        self,
        source_descriptor: int,
        parent_descriptor: int,
        output_name: str,
    ) -> None:
        """从 held FD 将匿名 inode 原子排他发布到 output_name。"""

    def discard_unpublished(
        self,
        source_descriptor: int,
        parent_descriptor: int,
    ) -> None:
        """清理测试边界的未发布资源；Linux 匿名 inode 为空操作。"""


class _LinuxAnonymousOutputBoundary:
    """使用 Linux O_TMPFILE + linkat 的生产输出边界。"""

    prepublish_nlink = 0

    def open_anonymous(self, parent_descriptor: int) -> int:
        """在 held parent 内创建 0600 O_TMPFILE inode。

        输入参数：parent_descriptor 为 owner-only 目录 FD。
        输出返回值：O_RDWR、nlink=0 的匿名 inode FD。
        """

        if not sys.platform.startswith("linux"):
            raise OSWorldQcow2MaterializationError(
                "OSWorld O_TMPFILE 生产物化仅支持 Linux"
            )
        temporary = getattr(os, "O_TMPFILE", 0)
        if temporary == 0:
            raise OSWorldQcow2MaterializationError("OSWorld O_TMPFILE 不可用")
        try:
            descriptor = os.open(
                ".",
                temporary | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
        except OSError:
            raise OSWorldQcow2MaterializationError(
                "OSWorld O_TMPFILE 创建失败"
            ) from None
        try:
            status = os.fstat(descriptor)
            valid = (
                stat.S_ISREG(status.st_mode)
                and status.st_nlink == 0
                and status.st_uid == os.geteuid()
                and stat.S_IMODE(status.st_mode) == 0o600
            )
        except OSError:
            os.close(descriptor)
            raise OSWorldQcow2MaterializationError(
                "OSWorld O_TMPFILE 初始身份无效"
            ) from None
        if not valid:
            os.close(descriptor)
            raise OSWorldQcow2MaterializationError("OSWorld O_TMPFILE 初始身份无效")
        return descriptor

    def reopen_readonly(self, descriptor: int) -> int:
        """通过内核 ``/proc/self/fd`` 重开同 inode 的 O_RDONLY FD。

        输入参数：descriptor 为匿名 O_RDWR FD。
        输出返回值：已校验 dev/inode 一致的 O_RDONLY FD。
        """

        path = f"/proc/self/fd/{descriptor}"
        try:
            readonly = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except OSError:
            raise OSWorldQcow2MaterializationError(
                "OSWorld /proc/self/fd 只读重开失败"
            ) from None
        try:
            same_inode = _ownership_identity(os.fstat(readonly)) == _ownership_identity(
                os.fstat(descriptor)
            )
        except OSError:
            os.close(readonly)
            raise OSWorldQcow2MaterializationError(
                "OSWorld 匿名 inode 只读重开身份漂移"
            ) from None
        if not same_inode:
            os.close(readonly)
            raise OSWorldQcow2MaterializationError(
                "OSWorld 匿名 inode 只读重开身份漂移"
            )
        return readonly

    def publish_noreplace(
        self,
        source_descriptor: int,
        parent_descriptor: int,
        output_name: str,
    ) -> None:
        """通过 linkat 从 held FD 原子排他发布匿名 inode。

        输入参数：source_descriptor 为已核验只读 FD；
            parent_descriptor 为 held 输出目录；output_name 为固定单段名。
        输出返回值：无；目标存在时以 EEXIST 失败，绝不覆盖。
        """

        try:
            libc = ctypes.CDLL(None, use_errno=True)
            linkat = libc.linkat
        except (AttributeError, OSError):
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 linkat 原子发布不可用"
            ) from None
        linkat.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        linkat.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = linkat(
            source_descriptor,
            b"",
            parent_descriptor,
            os.fsencode(output_name),
            _AT_EMPTY_PATH,
        )
        saved_errno = ctypes.get_errno()
        if result != 0 and saved_errno == errno.EPERM:
            source = os.fsencode(f"/proc/self/fd/{source_descriptor}")
            ctypes.set_errno(0)
            result = linkat(
                _AT_FDCWD,
                source,
                parent_descriptor,
                os.fsencode(output_name),
                _AT_SYMLINK_FOLLOW,
            )
            saved_errno = ctypes.get_errno()
        if result == 0:
            return
        if saved_errno == errno.EEXIST:
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 最终名已存在，拒绝覆盖"
            )
        raise OSWorldQcow2MaterializationError("OSWorld qcow2 linkat 原子发布失败")

    def discard_unpublished(
        self,
        source_descriptor: int,
        parent_descriptor: int,
    ) -> None:
        """对 O_TMPFILE 未发布 inode 执行空操作。

        输入参数：source_descriptor/parent_descriptor 仅用于与测试
            边界保持统一形状；实际删除由最后一个 FD
            close 自动完成。
        输出返回值：无。
        """

        del source_descriptor, parent_descriptor


def _materialize_osworld_qcow2_from_spec(
    *,
    archive_path: Path,
    output_parent: Path,
    spec: OSWorldQcow2MaterializationSpec,
) -> MaterializedOSWorldQcow2:
    """以调用方提供的 typed spec 执行非正式测试级物化。

    输入参数：archive_path 为待核验绝对路径；output_parent
        为 owner-only 绝对目录；spec 为版本化闭集身份。
    输出返回值：持有已发布 inode 的可关闭 capability。
    异常：任一不确定性抛出 OSWorldQcow2MaterializationError；
        发布前失败只关闭匿名 FD，发布后失败保留可审计残余。
    注意：该私有入口只服务低层测试与 manifest loader 组合；操作者
        提供的 spec 不能作为正式 trust anchor，也不构成回执入口。
    """

    return _materialize_osworld_qcow2_core(
        archive_path=archive_path,
        output_parent=output_parent,
        spec=spec,
        manifest_handle=None,
        manifest_expected_identity=None,
        manifest_sha256=None,
        manifest_size=None,
    )


def _materialize_osworld_qcow2_core(
    *,
    archive_path: Path,
    output_parent: Path,
    spec: OSWorldQcow2MaterializationSpec,
    manifest_handle: _HeldAbsolutePath | None,
    manifest_expected_identity: tuple[int, ...] | None,
    manifest_sha256: str | None,
    manifest_size: int | None,
) -> MaterializedOSWorldQcow2:
    """执行物化核心并把 provenance/output capability 转移给结果。

    输入参数：archive_path/output_parent/spec 与公开低层入口相同；
        四个 manifest 字段要么全空（仅低层 spec 测试），要么共同绑定
        正式 repo-root manifest 的 held path、full stat、SHA 与大小。
    输出返回值：持有 archive/output 以及可选 manifest 全部权威 FD/路径
        的可关闭 capability；异常分支由本函数释放已接管的句柄。
    """

    manifest_values = (
        manifest_handle,
        manifest_expected_identity,
        manifest_sha256,
        manifest_size,
    )
    if any(value is None for value in manifest_values) and not all(
        value is None for value in manifest_values
    ):
        if manifest_handle is not None:
            manifest_handle.close()
        raise OSWorldQcow2MaterializationError(
            "OSWorld image manifest capability 字段不完整"
        )
    _validate_spec(spec)
    if (
        not isinstance(archive_path, Path)
        or not archive_path.is_absolute()
        or archive_path.name != spec.archive_path
        or not isinstance(output_parent, Path)
        or not output_parent.is_absolute()
    ):
        raise OSWorldQcow2MaterializationError(
            "OSWorld qcow2 安全边界绝对路径与 recipe 不一致"
        )
    boundary = _create_system_boundary()
    archive_handle = _open_attested_archive(archive_path, spec)
    output_handle: _HeldAbsolutePath | None = None
    writer: int | None = None
    readonly: int | None = None
    final_readonly: int | None = None
    published = False
    try:
        archive_before = os.fstat(archive_handle.leaf_descriptor)
        _verify_archive_digest(archive_handle.leaf_descriptor, spec)
        member = _load_unique_member(archive_handle.leaf_descriptor, spec)
        output_handle = _open_private_output_parent(output_parent)
        parent_descriptor = output_handle.leaf_descriptor
        writer = boundary.open_anonymous(parent_descriptor)
        initial = os.fstat(writer)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != boundary.prepublish_nlink
            or initial.st_uid != os.geteuid()
        ):
            raise OSWorldQcow2MaterializationError("OSWorld 匿名输出初始身份无效")
        streamed_sha256, streamed_size = _stream_member_to_output(
            archive_descriptor=archive_handle.leaf_descriptor,
            member=member,
            output_descriptor=writer,
            expected_size=spec.output_size,
        )
        if streamed_size != spec.output_size or streamed_sha256 != spec.output_sha256:
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 解压输出完整身份不匹配"
            )
        os.fsync(writer)
        os.fchmod(writer, 0o400)
        os.fsync(writer)
        readonly = boundary.reopen_readonly(writer)
        if _ownership_identity(os.fstat(readonly)) != _ownership_identity(
            os.fstat(writer)
        ):
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 只读 FD 与 writer inode 不一致"
            )
        os.close(writer)
        writer = None
        _verify_readonly_image(
            readonly,
            spec,
            expected_nlink=boundary.prepublish_nlink,
        )
        # 将 12 GB 级归档二次完整摘要和两条路径链复核
        # 放在发布前；失败时仍只需 close 匿名 inode。
        archive_hashed_identity = _verify_archive_stable(
            archive_handle,
            archive_before,
            spec,
        )
        output_handle.verify_continuity()
        _verify_private_output_parent(parent_descriptor)
        boundary.publish_noreplace(readonly, parent_descriptor, spec.output_path)
        published = True
        os.fsync(parent_descriptor)
        _verify_private_output_parent(parent_descriptor)
        final_readonly = _open_final_readonly(parent_descriptor, spec.output_path)
        if _ownership_identity(os.fstat(final_readonly)) != _ownership_identity(
            os.fstat(readonly)
        ):
            raise OSWorldQcow2MaterializationError(
                "OSWorld qcow2 发布后 inode 身份漂移"
            )
        final_hashed_identity = _verify_readonly_image(
            final_readonly,
            spec,
            expected_nlink=1,
        )
        _verify_final_name_continuity(
            parent_descriptor,
            spec.output_path,
            final_hashed_identity,
        )
        # 发布后不再执行长时 I/O；两个 continuity 校验紧邻 return。
        output_handle.verify_continuity()
        _verify_private_output_parent(parent_descriptor)
        _verify_archive_return_identity(
            archive_handle,
            archive_hashed_identity,
        )
        if _full_file_identity(os.fstat(final_readonly)) != final_hashed_identity:
            raise OSWorldQcow2MaterializationError("OSWorld qcow2 最终 FD 身份漂移")
        _verify_final_name_continuity(
            parent_descriptor,
            spec.output_path,
            final_hashed_identity,
        )
        if manifest_handle is not None:
            _verify_held_source_current(
                manifest_handle,
                manifest_expected_identity,
                label="image manifest",
            )
        # 正式 manifest 复核后再次核对归档和输出；三个 provenance
        # capability 随结果跨越 Python return/finally 窗口继续 held。
        output_handle.verify_continuity()
        _verify_private_output_parent(parent_descriptor)
        _verify_archive_return_identity(
            archive_handle,
            archive_hashed_identity,
        )
        if _full_file_identity(os.fstat(final_readonly)) != final_hashed_identity:
            raise OSWorldQcow2MaterializationError("OSWorld qcow2 最终 FD 身份漂移")
        _verify_final_name_continuity(
            parent_descriptor,
            spec.output_path,
            final_hashed_identity,
        )
        result = MaterializedOSWorldQcow2(
            _image_path=output_parent / spec.output_path,
            _sha256=spec.output_sha256,
            _size=spec.output_size,
            _final_descriptor=final_readonly,
            _output_handle=output_handle,
            _expected_identity=final_hashed_identity,
            _archive_handle=archive_handle,
            _archive_expected_identity=archive_hashed_identity,
            _archive_sha256=spec.archive_sha256,
            _archive_size=spec.archive_size,
            _manifest_handle=manifest_handle,
            _manifest_expected_identity=manifest_expected_identity,
            _manifest_sha256=manifest_sha256,
            _manifest_size=manifest_size,
        )
        # 成功结果取得 final/output/archive/manifest 的唯一所有权；
        # ``finally`` 只关闭 writer/旧 readonly 等非结果资源。
        final_readonly = None
        output_handle = None
        archive_handle = None
        manifest_handle = None
        return result
    except OSWorldQcow2MaterializationError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError):
        residue = "已发布残余保留" if published else "未发布匿名 inode 将关闭"
        raise OSWorldQcow2MaterializationError(
            f"OSWorld qcow2 物化失败（{residue}）"
        ) from None
    finally:
        if (
            not published
            and output_handle is not None
            and (readonly is not None or writer is not None)
        ):
            boundary.discard_unpublished(
                readonly if readonly is not None else int(writer),
                output_handle.leaf_descriptor,
            )
        for descriptor in (final_readonly, readonly, writer):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if archive_handle is not None:
            archive_handle.close()
        if output_handle is not None:
            output_handle.close()
        if manifest_handle is not None:
            manifest_handle.close()


def _create_system_boundary() -> _AnonymousOutputBoundary:
    """创建唯一生产匿名输出边界。

    输入参数：无。
    输出返回值：Linux O_TMPFILE + held-FD linkat 边界。
    注意：该 factory 是模块私有单元测试 seam；公共物化函数
        不接受任何 boundary 注入参数。
    """

    return _LinuxAnonymousOutputBoundary()


def _validate_spec(spec: OSWorldQcow2MaterializationSpec) -> None:
    """验证物化规格的类型、范围与固定字段。

    输入参数：spec 为公共接口收到的候选规格。
    输出返回值：无；严格闭集有效时正常返回。
    """

    if type(spec) is not OSWorldQcow2MaterializationSpec:
        raise OSWorldQcow2MaterializationError("OSWorld qcow2 物化规格无效")
    digests = (spec.archive_sha256, spec.output_sha256)
    valid = (
        spec.protocol == MATERIALIZATION_PROTOCOL
        and type(spec.protocol_version) is int
        and spec.protocol_version == MATERIALIZATION_PROTOCOL_VERSION
        and spec.archive_path == _ARCHIVE_FILENAME
        and type(spec.archive_size) is int
        and 0 < spec.archive_size <= _MAX_ARCHIVE_BYTES
        and all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in digests
        )
        and spec.member_path == _OUTPUT_FILENAME
        and type(spec.member_compression_method) is int
        and spec.member_compression_method == zipfile.ZIP_DEFLATED
        and type(spec.member_flags) is int
        and spec.member_flags == 0
        and type(spec.member_creator_system) is int
        and spec.member_creator_system == 3
        and type(spec.member_external_attributes) is int
        and stat.S_IFMT(spec.member_external_attributes >> 16) == stat.S_IFREG
        and _zip_extra_recipe_is_valid(spec)
        and type(spec.member_compressed_size) is int
        and 0 < spec.member_compressed_size <= spec.archive_size
        and type(spec.member_uncompressed_size) is int
        and 0 < spec.member_uncompressed_size <= _MAX_IMAGE_BYTES
        and spec.member_uncompressed_size
        <= spec.member_compressed_size * _MAX_COMPRESSION_RATIO
        and type(spec.member_crc32) is int
        and 0 <= spec.member_crc32 <= 0xFFFFFFFF
        and spec.output_path == _OUTPUT_FILENAME
        and type(spec.output_size) is int
        and spec.output_size == spec.member_uncompressed_size
    )
    if not valid:
        raise OSWorldQcow2MaterializationError("OSWorld qcow2 物化规格无效")


def _zip_extra_recipe_is_valid(spec: OSWorldQcow2MaterializationSpec) -> bool:
    """验证 recipe 对 local/central ZIP extra 原始字节的完整绑定。

    输入参数：spec 为待验证的 typed materialization recipe。
    输出返回值：两份 extra 均为空，或严格符合
        ``0x5455 -> 0x7875 -> 0x0001`` 闭集且 ZIP64 尺寸与
        member 身份一致时返回 ``True``；其余返回 ``False``。
    """

    values = (spec.member_local_extra_hex, spec.member_central_extra_hex)
    if any(
        type(value) is not str
        or len(value) > 131_070
        or len(value) % 2
        or any(character not in "0123456789abcdef" for character in value)
        for value in values
    ):
        return False
    try:
        local_extra, central_extra = (bytes.fromhex(value) for value in values)
        if not local_extra and not central_extra:
            return True
        local_records = _parse_zip_extra_records(local_extra)
        central_records = _parse_zip_extra_records(central_extra)
        if tuple(
            (identifier, len(payload)) for identifier, payload in local_records
        ) != (
            (0x5455, 9),
            (0x7875, 11),
            (0x0001, 16),
        ) or tuple(
            (identifier, len(payload)) for identifier, payload in central_records
        ) != (
            (0x5455, 5),
            (0x7875, 11),
            (0x0001, 16),
        ):
            return False
        expected_zip64 = struct.pack(
            "<QQ",
            spec.member_uncompressed_size,
            spec.member_compressed_size,
        )
        return (
            local_records[-1][1] == expected_zip64
            and central_records[-1][1] == expected_zip64
        )
    except (TypeError, ValueError, struct.error):
        return False


def validate_osworld_qcow2_materialization_spec(
    spec: OSWorldQcow2MaterializationSpec,
) -> None:
    """对 typed ZIP→qcow2 recipe 执行与生产物化相同的闭集验证。

    输入参数：spec 为 manifest loader 或低层物化边界构造的
        ``OSWorldQcow2MaterializationSpec`` 精确类型对象。
    输出返回值：无；协议、路径、大小、摘要、ZIP member
        元数据与安全上限全部有效时返回。
    """

    _validate_spec(spec)


def _required_flag(name: str) -> int:
    """获取安全协议必需的 OS 打开标志。

    输入参数：name 为 ``os`` 模块常量名。
    输出返回值：非零整数标志；不可用时失败关闭。
    """

    value = getattr(os, name, 0)
    if not isinstance(value, int) or value == 0:
        raise OSWorldQcow2MaterializationError(f"OSWorld {name} 不可用")
    return value


def _entry_identity(status: os.stat_result) -> tuple[int, ...]:
    """投影路径 entry 连续性身份。

    输入参数：status 为 nofollow held descriptor 的 stat。
    输出返回值：device、inode、文件类型和 uid。
    """

    return (status.st_dev, status.st_ino, stat.S_IFMT(status.st_mode), status.st_uid)


def _ownership_identity(status: os.stat_result) -> tuple[int, ...]:
    """投影匿名/已发布 inode 的所属身份。

    输入参数：status 为 held FD 的 stat。
    输出返回值：device、inode、文件类型和 uid。
    """

    return _entry_identity(status)


def _full_file_identity(status: os.stat_result) -> tuple[int, ...]:
    """投影内容稳定性所需的完整 stat 身份。

    输入参数：status 为归档或 qcow2 held FD 的 stat。
    输出返回值：dev/inode/mode/nlink/uid/gid/size/mtime_ns/ctime_ns。
    """

    return (
        status.st_dev,
        status.st_ino,
        status.st_mode,
        status.st_nlink,
        status.st_uid,
        status.st_gid,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
    )


def _verify_held_source_current(
    handle: _HeldAbsolutePath,
    expected_identity: tuple[int, ...] | None,
    *,
    label: str,
) -> None:
    """轻量重验 provenance 文件的 held FD 与完整 pathname 连续性。

    输入参数：handle 为 archive 或 manifest 的逐级 nofollow held-FD 链；
        expected_identity 是首次完整摘要/严格解析结束时的 full-stat；
        label 是不含宿主路径和内容的固定类别名。
    输出返回值：无；先检查路径、再检查 held FD，并重复一次以缩小
        单轮验证内部的竞态窗口；任一漂移转换为不泄漏路径的固定错误。
    """

    if expected_identity is None:
        raise OSWorldQcow2MaterializationError(f"OSWorld {label} capability 身份缺失")
    for _attempt in range(2):
        try:
            handle.verify_continuity()
        except OSWorldQcow2MaterializationError:
            raise OSWorldQcow2MaterializationError(
                f"OSWorld {label} 路径连续性漂移"
            ) from None
        try:
            current_identity = _full_file_identity(os.fstat(handle.leaf_descriptor))
        except OSError:
            raise OSWorldQcow2MaterializationError(
                f"OSWorld {label} 权威 FD 无法重验"
            ) from None
        if current_identity != expected_identity:
            raise OSWorldQcow2MaterializationError(f"OSWorld {label} 权威 FD 身份漂移")


def _open_absolute_nofollow(path: Path, *, leaf_flags: int) -> _HeldAbsolutePath:
    """从根目录开始逐分量 nofollow 打开绝对路径。

    输入参数：path 为绝对路径；leaf_flags 定义最后分量类型。
    输出返回值：根与每个分量都保持打开的 held-FD 链。
    """

    nofollow = _required_flag("O_NOFOLLOW")
    directory = _required_flag("O_DIRECTORY")
    if not isinstance(path, Path) or not path.is_absolute():
        raise OSWorldQcow2MaterializationError("OSWorld qcow2 必须使用绝对路径")
    names = tuple(path.parts[1:])
    if not names or any(name in {"", ".", ".."} or "/" in name for name in names):
        raise OSWorldQcow2MaterializationError("OSWorld qcow2 绝对路径分量无效")
    descriptors: list[int] = []
    identities: list[tuple[int, ...]] = []
    common = nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        root = os.open("/", os.O_RDONLY | directory | common)
        descriptors.append(root)
        identities.append(_entry_identity(os.fstat(root)))
        for index, name in enumerate(names):
            flags = leaf_flags if index == len(names) - 1 else os.O_RDONLY | directory
            descriptor = os.open(name, flags | common, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            identities.append(_entry_identity(os.fstat(descriptor)))
    except OSError:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise OSWorldQcow2MaterializationError(
            "OSWorld qcow2 绝对路径逐级 nofollow 失败"
        ) from None
    return _HeldAbsolutePath(names, descriptors, tuple(identities))


def _open_attested_archive(
    archive_path: Path,
    spec: OSWorldQcow2MaterializationSpec,
) -> _HeldAbsolutePath:
    """以 O_NONBLOCK + 逐级 nofollow held-FD 打开固定归档。

    输入参数：archive_path 为归档路径；spec 给出预期大小。
    输出返回值：已验证普通单链接文件的 held-FD 链。
    """

    handle = _open_absolute_nofollow(
        archive_path,
        leaf_flags=os.O_RDONLY | _required_flag("O_NONBLOCK"),
    )
    try:
        status = os.fstat(handle.leaf_descriptor)
    except OSError:
        handle.close()
        raise OSWorldQcow2MaterializationError(
            "OSWorld ZIP 类型、链接或大小无效"
        ) from None
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_nlink != 1
        or status.st_size != spec.archive_size
    ):
        handle.close()
        raise OSWorldQcow2MaterializationError("OSWorld ZIP 类型、链接或大小无效")
    return handle


def _open_private_output_parent(output_parent: Path) -> _HeldAbsolutePath:
    """打开当前 uid 独占的 nofollow 输出父目录。

    输入参数：output_parent 为已存在的绝对目录路径。
    输出返回值：可用于 O_TMPFILE/linkat 的 held-FD 链。
    """

    handle = _open_absolute_nofollow(
        output_parent,
        leaf_flags=os.O_RDONLY | _required_flag("O_DIRECTORY"),
    )
    try:
        _verify_private_output_parent(handle.leaf_descriptor)
    except OSWorldQcow2MaterializationError:
        handle.close()
        raise
    return handle


def _verify_private_output_parent(parent_descriptor: int) -> None:
    """重验 held 输出父目录的私有属性。

    输入参数：parent_descriptor 为逐级 nofollow 打开的目录 FD。
    输出返回值：无；只允许当前 euid 所有的 0700 目录。
    """

    try:
        status = os.fstat(parent_descriptor)
    except OSError:
        raise OSWorldQcow2MaterializationError(
            "OSWorld qcow2 输出父目录必须 owner-only 0700"
        ) from None
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) != 0o700
    ):
        raise OSWorldQcow2MaterializationError(
            "OSWorld qcow2 输出父目录必须 owner-only 0700"
        )


def _hash_exact_descriptor(
    descriptor: int,
    *,
    expected_size: int,
    expected_sha256: str,
    label: str,
) -> tuple[int, ...]:
    """从 held FD 重读固定字节数并校验 EOF/SHA-256/stat 稳定。

    输入参数：descriptor 为可 seek 普通文件 FD；expected_size/
        expected_sha256 为完整身份；label 是不包含路径或内容的
        错误类别。
    输出返回值：摘要结束时的 full-stat 快照；两次
        full stat、字节数、EOF 与 SHA 全部一致。
    """

    before = os.fstat(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = expected_size
    while remaining:
        chunk = os.read(descriptor, min(remaining, _COPY_CHUNK_BYTES))
        if not chunk:
            raise OSWorldQcow2MaterializationError(f"OSWorld {label} 读取不完整")
        digest.update(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1) or digest.hexdigest() != expected_sha256:
        raise OSWorldQcow2MaterializationError(f"OSWorld {label} 完整摘要不匹配")
    after = os.fstat(descriptor)
    after_identity = _full_file_identity(after)
    if after_identity != _full_file_identity(before):
        raise OSWorldQcow2MaterializationError(f"OSWorld {label} 重读期间身份漂移")
    return after_identity


def _verify_archive_digest(
    descriptor: int,
    spec: OSWorldQcow2MaterializationSpec,
) -> tuple[int, ...]:
    """通过 held descriptor 对归档完整流式求摘要。

    输入参数：descriptor 为归档 FD；spec 给出大小和 SHA。
    输出返回值：摘要结束时 full-stat 快照；
        字节数/EOF/SHA/stat 一致时返回。
    """

    return _hash_exact_descriptor(
        descriptor,
        expected_size=spec.archive_size,
        expected_sha256=spec.archive_sha256,
        label="ZIP",
    )


def _load_unique_member(
    archive_descriptor: int,
    spec: OSWorldQcow2MaterializationSpec,
) -> zipfile.ZipInfo:
    """从同一 held FD 读取严格单 member ZIP central directory。

    输入参数：archive_descriptor 为已求摘要的归档；spec
        给出成员闭集完整身份。
    输出返回值：唯一、固定 deflate、Unix regular 的安全 member。
    """

    os.lseek(archive_descriptor, 0, os.SEEK_SET)
    with _open_duplicate_binary_stream(archive_descriptor) as stream:
        with zipfile.ZipFile(stream, mode="r") as archive:
            members = archive.infolist()
    if len(members) != 1:
        raise OSWorldQcow2MaterializationError("OSWorld ZIP member 闭集无效")
    member = members[0]
    valid = (
        member.filename == spec.member_path
        and not member.is_dir()
        and member.compress_type == spec.member_compression_method
        and member.flag_bits == spec.member_flags
        and member.create_system == spec.member_creator_system
        and member.external_attr == spec.member_external_attributes
        and member.extra.hex() == spec.member_central_extra_hex
        and stat.S_IFMT(member.external_attr >> 16) == stat.S_IFREG
        and member.compress_size == spec.member_compressed_size
        and member.file_size == spec.member_uncompressed_size
        and member.CRC == spec.member_crc32
    )
    if not valid:
        raise OSWorldQcow2MaterializationError("OSWorld ZIP member 身份无效")
    _validate_local_header(archive_descriptor, member, spec)
    return member


def _validate_local_header(
    archive_descriptor: int,
    member: zipfile.ZipInfo,
    spec: OSWorldQcow2MaterializationSpec,
) -> None:
    """从同一 held FD 解析 ZIP local header 并与 central recipe 交叉校验。

    输入参数：archive_descriptor 为固定归档；member 为唯一
        central entry；spec 固定 method/flags/name/CRC/压缩与解压大小。
    输出返回值：无；local header 必须从 offset 0 开始，
        不得加密/数据描述符，普通尺寸或唯一 ZIP64 extra 必须
        解析为与 central/spec 相同的完整尺寸。
    """

    try:
        if member.header_offset != 0:
            raise ValueError
        fixed = os.pread(archive_descriptor, 30, member.header_offset)
        if len(fixed) != 30:
            raise ValueError
        (
            signature,
            _version_needed,
            flags,
            compression_method,
            _modified_time,
            _modified_date,
            crc32,
            compressed_size_32,
            uncompressed_size_32,
            filename_length,
            extra_length,
        ) = struct.unpack("<IHHHHHIIIHH", fixed)
        if (
            signature != 0x04034B50
            or flags != spec.member_flags
            or flags & 0x9
            or compression_method != spec.member_compression_method
            or crc32 != spec.member_crc32
            or not 0 < filename_length <= 4096
            or extra_length > 65_535
        ):
            raise ValueError
        variable = os.pread(
            archive_descriptor,
            filename_length + extra_length,
            member.header_offset + 30,
        )
        if len(variable) != filename_length + extra_length:
            raise ValueError
        filename = variable[:filename_length]
        extra = variable[filename_length:]
        if (
            filename != spec.member_path.encode("utf-8")
            or extra.hex() != spec.member_local_extra_hex
        ):
            raise ValueError
        compressed_size, uncompressed_size = _resolve_local_zip_sizes(
            compressed_size_32=compressed_size_32,
            uncompressed_size_32=uncompressed_size_32,
            extra=extra,
        )
        data_offset = member.header_offset + 30 + filename_length + extra_length
        if (
            compressed_size != spec.member_compressed_size
            or uncompressed_size != spec.member_uncompressed_size
            or data_offset + compressed_size > spec.archive_size
        ):
            raise ValueError
    except (OSError, ValueError, struct.error):
        raise OSWorldQcow2MaterializationError(
            "OSWorld ZIP local header 与 central recipe 不一致"
        ) from None


def _resolve_local_zip_sizes(
    *,
    compressed_size_32: int,
    uncompressed_size_32: int,
    extra: bytes,
) -> tuple[int, int]:
    """解析 local header 的普通尺寸或严格唯一 ZIP64 extra。

    输入参数：两个 32-bit local size 与完整 extra bytes。
    输出返回值：解析后的 compressed/uncompressed 尺寸。
    异常：extra 截断、重复/未知 field、不必要 ZIP64 或尺寸缺失
        时抛出 ValueError，由上层折叠为 local-header 固定错误。
    """

    needs_uncompressed = uncompressed_size_32 == 0xFFFFFFFF
    needs_compressed = compressed_size_32 == 0xFFFFFFFF
    if not needs_uncompressed and not needs_compressed:
        if extra:
            raise ValueError
        return compressed_size_32, uncompressed_size_32
    records = _parse_zip_extra_records(extra)
    if tuple((field_id, len(payload)) for field_id, payload in records) != (
        (0x5455, 9),
        (0x7875, 11),
        (0x0001, 16),
    ):
        raise ValueError
    zip64_payload = records[-1][1]
    value_cursor = 0
    uncompressed = uncompressed_size_32
    compressed = compressed_size_32
    if needs_uncompressed:
        if value_cursor + 8 > len(zip64_payload):
            raise ValueError
        uncompressed = struct.unpack_from("<Q", zip64_payload, value_cursor)[0]
        value_cursor += 8
    if needs_compressed:
        if value_cursor + 8 > len(zip64_payload):
            raise ValueError
        compressed = struct.unpack_from("<Q", zip64_payload, value_cursor)[0]
        value_cursor += 8
    if value_cursor != len(zip64_payload):
        raise ValueError
    return compressed, uncompressed


def _parse_zip_extra_records(extra: bytes) -> tuple[tuple[int, bytes], ...]:
    """以严格 TLV 闭集解析 ZIP local/central extra records。

    输入参数：extra 为同一 held archive descriptor 中的完整
        extra 字节。
    输出返回值：按归档顺序返回 ``(header_id, payload)``
        不可变元组；截断或重复 header id 抛出 ``ValueError``。
    """

    if not isinstance(extra, bytes):
        raise ValueError
    cursor = 0
    records: list[tuple[int, bytes]] = []
    seen: set[int] = set()
    while cursor < len(extra):
        if cursor + 4 > len(extra):
            raise ValueError
        field_id, field_size = struct.unpack_from("<HH", extra, cursor)
        cursor += 4
        end = cursor + field_size
        if end > len(extra) or field_id in seen:
            raise ValueError
        seen.add(field_id)
        records.append((field_id, extra[cursor:end]))
        cursor = end
    if cursor != len(extra):
        raise ValueError
    return tuple(records)


def _stream_member_to_output(
    *,
    archive_descriptor: int,
    member: zipfile.ZipInfo,
    output_descriptor: int,
    expected_size: int,
) -> tuple[str, int]:
    """从 held ZIP FD 流式解压，同时求输出完整摘要与限额。

    输入参数：归档 FD、已核验 member、匿名 writer FD 和固定
        expected_size。
    输出返回值：解压字节 SHA-256 与总字节数。
    """

    digest = hashlib.sha256()
    total = 0
    os.lseek(archive_descriptor, 0, os.SEEK_SET)
    with _open_duplicate_binary_stream(archive_descriptor) as stream:
        with zipfile.ZipFile(stream, mode="r") as archive:
            with archive.open(member, mode="r") as source:
                while True:
                    chunk = source.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > expected_size or total > _MAX_IMAGE_BYTES:
                        raise OSWorldQcow2MaterializationError(
                            "OSWorld qcow2 解压输出超出上限"
                        )
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(output_descriptor, view)
                        if written <= 0:
                            raise OSError
                        view = view[written:]
    return digest.hexdigest(), total


def _verify_readonly_image(
    descriptor: int,
    spec: OSWorldQcow2MaterializationSpec,
    *,
    expected_nlink: int,
) -> tuple[int, ...]:
    """验证只读 held FD 的类型、权限、链接数和完整摘要。

    输入参数：descriptor 为 O_RDONLY FD；spec 固定 output 身份；
        expected_nlink 在发布前为 0、发布后为 1。
    输出返回值：完整摘要结束时的 full-stat 快照；
        任一属性/字节漂移都失败关闭。
    """

    flags = fcntl_getfl(descriptor)
    status = os.fstat(descriptor)
    if (
        flags & os.O_ACCMODE != os.O_RDONLY
        or not stat.S_ISREG(status.st_mode)
        or status.st_nlink != expected_nlink
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) != 0o400
        or status.st_size != spec.output_size
    ):
        raise OSWorldQcow2MaterializationError(
            "OSWorld qcow2 只读输出类型、权限或链接数无效"
        )
    return _hash_exact_descriptor(
        descriptor,
        expected_size=spec.output_size,
        expected_sha256=spec.output_sha256,
        label="qcow2",
    )


def fcntl_getfl(descriptor: int) -> int:
    """获取 held FD 的打开模式。

    输入参数：descriptor 为待核验 FD。
    输出返回值：``F_GETFL`` 的整数结果。
    """

    import fcntl

    return int(fcntl.fcntl(descriptor, fcntl.F_GETFL))


def _open_final_readonly(parent_descriptor: int, output_name: str) -> int:
    """通过 held parent nofollow 打开已发布最终 qcow2。

    输入参数：parent_descriptor 为 held owner-only 目录；output_name
        为 recipe 固定单段名。
    输出返回值：O_RDONLY|O_NOFOLLOW 的最终文件 FD。
    """

    descriptor: int | None = None
    try:
        descriptor = os.open(
            output_name,
            os.O_RDONLY
            | _required_flag("O_NOFOLLOW")
            | _required_flag("O_NONBLOCK")
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or status.st_nlink != 1
            or stat.S_IMODE(status.st_mode) != 0o400
        ):
            raise OSError
        return descriptor
    except OSError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise OSWorldQcow2MaterializationError(
            "OSWorld qcow2 最终路径 nofollow 打开失败"
        ) from None


def _verify_final_name_continuity(
    parent_descriptor: int,
    output_name: str,
    expected_identity: tuple[int, ...],
) -> None:
    """在返回前对最终单段名称重新 nofollow 校验 inode。

    输入参数：parent_descriptor/output_name 定位最终名；
        expected_identity 是已完整求摘要结束时的 full-stat 快照。
    输出返回值：无；当前 path entry 必须仍指向同 inode。
    """

    current_descriptor = _open_final_readonly(parent_descriptor, output_name)
    try:
        current_identity = _full_file_identity(os.fstat(current_descriptor))
    finally:
        os.close(current_descriptor)
    if current_identity != expected_identity:
        raise OSWorldQcow2MaterializationError("OSWorld qcow2 最终路径身份漂移")


def _verify_archive_stable(
    handle: _HeldAbsolutePath,
    before: os.stat_result,
    spec: OSWorldQcow2MaterializationSpec,
) -> tuple[int, ...]:
    """在发布后对归档同一 held FD 重算摘要并验证路径链。

    输入参数：handle 为始终未关闭的归档路径链；before
        为首次摘要前 stat；spec 提供完整归档身份。
    输出返回值：第二次摘要结束时的 full-stat 快照；
        同 FD 字节、full stat 与逐级 path entry 均稳定。
    """

    hashed_identity = _verify_archive_digest(handle.leaf_descriptor, spec)
    if hashed_identity != _full_file_identity(before):
        raise OSWorldQcow2MaterializationError("OSWorld ZIP 操作期间身份漂移")
    handle.verify_continuity()
    return hashed_identity


def _verify_archive_return_identity(
    handle: _HeldAbsolutePath,
    expected_identity: tuple[int, ...],
) -> None:
    """在返回前重验归档 held FD 和绝对路径链。

    输入参数：handle 为始终 held 的归档路径；
        expected_identity 为第二次完整 SHA 结束时的 full-stat 快照。
    输出返回值：无；发布后长时 output SHA 窗口内的
        原位写或 path entry 替换都必须拒绝。
    """

    if _full_file_identity(os.fstat(handle.leaf_descriptor)) != expected_identity:
        raise OSWorldQcow2MaterializationError("OSWorld ZIP 返回前身份漂移")
    handle.verify_continuity()


def _open_duplicate_binary_stream(descriptor: int) -> BinaryIO:
    """将 held FD 的 duplicate 安全包装为二进制流。

    输入参数：descriptor 为 ZIP 归档 held FD。
    输出返回值：拥有 duplicate FD 的 ``BinaryIO``；
        ``fdopen`` 失败时会精确关闭已取得的 raw FD。
    """

    duplicate = os.dup(descriptor)
    try:
        return os.fdopen(duplicate, "rb", closefd=True)
    except BaseException:
        try:
            os.close(duplicate)
        except OSError:
            pass
        raise


def _materialize_osworld_qcow2_from_manifest(
    *,
    manifest_path: Path,
    archive_path: Path,
    output_parent: Path,
) -> MaterializedOSWorldQcow2:
    """从指定 manifest 派生 recipe 的非正式模块内组合入口。

    输入参数：manifest_path 为绝对、逐级 nofollow 的正式
        image manifest；archive_path/output_parent 为授权 Linux 部署路径。
    输出返回值：由 manifest 唯一 typed recipe 物化并完整核验的
        0400 qcow2 capability。该入口不接受操作者手填摘要/大小/ZIP 元字段。
    注意：任意 ``manifest_path`` 不属于正式发证 trust anchor；正式调用
        必须经 ``materialize_osworld_qcow2_from_repo_root`` 固定相对路径。
    """

    (
        handle,
        manifest,
        manifest_identity,
        manifest_sha256,
        manifest_size,
    ) = _open_held_manifest_snapshot(manifest_path)
    try:
        spec = manifest.materialization_spec
        if (
            manifest.schema_version != 2
            or type(spec) is not OSWorldQcow2MaterializationSpec
            or manifest.materialization_status
            not in {
                "must_verify_before_live_run",
                "verified_reproducible_materialization",
            }
        ):
            raise OSWorldQcow2MaterializationError(
                "OSWorld image manifest 未提供可执行 typed recipe"
            )

        return _materialize_osworld_qcow2_core(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=spec,
            manifest_handle=handle,
            manifest_expected_identity=manifest_identity,
            manifest_sha256=manifest_sha256,
            manifest_size=manifest_size,
        )
    except BaseException:
        # core 一旦被调用即接管 handle；异常时它已幂等关闭，调用方
        # 再 close 只覆盖 schema 校验等尚未转移的早期失败。
        handle.close()
        raise


def materialize_osworld_qcow2_from_repo_root(
    *,
    repo_root: Path,
    archive_path: Path,
    output_parent: Path,
) -> MaterializedOSWorldQcow2:
    """从仓库固定 image-manifest trust anchor 正式物化 qcow2。

    输入参数：repo_root 为绝对仓库根目录；archive_path 是授权的固定
        ``Ubuntu.qcow2.zip`` 绝对路径；output_parent 是本次新建的
        owner-only 0700 绝对输出目录。
    输出返回值：持有最终只读 inode FD 与输出路径链的可关闭 capability。
        manifest 始终固定解析为 repo-root 下
        ``environments/osworld/image-manifest.json``，且整条绝对路径
        由底层逐级 nofollow held-FD 边界验证。
    """

    if not isinstance(repo_root, Path) or not repo_root.is_absolute():
        raise OSWorldQcow2MaterializationError("OSWorld 正式 repo-root 必须是绝对路径")
    manifest_path = repo_root.joinpath(*_FORMAL_MANIFEST_RELATIVE_PARTS)
    return _materialize_osworld_qcow2_from_manifest(
        manifest_path=manifest_path,
        archive_path=archive_path,
        output_parent=output_parent,
    )


def _open_held_manifest_snapshot(
    manifest_path: Path,
) -> tuple[_HeldAbsolutePath, Any, tuple[int, ...], str, int]:
    """从逐级 nofollow held-FD 字节构造严格 manifest 快照。

    输入参数：manifest_path 为绝对路径，不允许任一 symlink 祖先。
    输出返回值：仍保持打开的 held 路径句柄、image loader 严格 DTO、
        字节解析完成时的 full-stat 快照、完整 manifest SHA-256 与大小。
    """

    from paraguibench.integrations.osworld.image_manifest import (
        OSWorldImageManifestError,
        load_osworld_image_manifest_bytes_with_sha256,
    )

    handle = _open_absolute_nofollow(
        manifest_path,
        leaf_flags=os.O_RDONLY | _required_flag("O_NONBLOCK"),
    )
    try:
        before = os.fstat(handle.leaf_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= _MAX_MANIFEST_BYTES
        ):
            raise OSWorldQcow2MaterializationError(
                "OSWorld image manifest 物理身份无效"
            )
        os.lseek(handle.leaf_descriptor, 0, os.SEEK_SET)
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(handle.leaf_descriptor, min(remaining, 65_536))
            if not chunk:
                raise OSWorldQcow2MaterializationError(
                    "OSWorld image manifest 读取不完整"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(handle.leaf_descriptor, 1):
            raise OSWorldQcow2MaterializationError(
                "OSWorld image manifest 超出固定大小"
            )
        payload = b"".join(chunks)
        after = os.fstat(handle.leaf_descriptor)
        if _full_file_identity(after) != _full_file_identity(before):
            raise OSWorldQcow2MaterializationError(
                "OSWorld image manifest 读取期间身份漂移"
            )
        handle.verify_continuity()
        try:
            manifest, manifest_sha256 = load_osworld_image_manifest_bytes_with_sha256(
                payload
            )
        except OSWorldImageManifestError:
            raise OSWorldQcow2MaterializationError(
                "OSWorld image manifest recipe 无效"
            ) from None
        if _full_file_identity(os.fstat(handle.leaf_descriptor)) != _full_file_identity(
            after
        ):
            raise OSWorldQcow2MaterializationError(
                "OSWorld image manifest 解析期间身份漂移"
            )
        handle.verify_continuity()
        return (
            handle,
            manifest,
            _full_file_identity(after),
            manifest_sha256,
            after.st_size,
        )
    except BaseException:
        handle.close()
        raise


class _RedactedArgumentParser(argparse.ArgumentParser):
    """将 materializer 参数错误收敛为不含输入值的固定消息。"""

    def error(self, message: str) -> NoReturn:
        """拒绝无效参数且不复述 argparse 生成的原始错误。

        输入参数：message 是 argparse 生成、可能携带路径或未知参数值的
            诊断文本；本方法刻意丢弃它。
        输出返回值：不正常返回；向 stderr 写入固定错误并以 rc=2 退出。
        """

        del message
        self.exit(2, "OSWORLD_QCOW2_ARGUMENT_ERROR\n")


def main(argv: list[str] | None = None) -> int:
    """运行 Linux 正式 repo-root anchored ZIP→qcow2 物化命令。

    输入参数：argv 只包含 repo-root、archive 和 output-parent 路径；
        repo-root 内 manifest 相对位置固定，省略时使用进程命令行。
    输出返回值：成功时输出不含 host 路径的 JSON 并返回 0；
        输出前在 held-FD capability 上重新执行完整摘要/连续性验证；
        任一失败仅输出固定错误并返回 1。上下文关闭后该 JSON 只证明
        materialization-at-evidence-time，不声称 pathname 持续可信。
    """

    parser = _RedactedArgumentParser(
        prog="python -m paraguibench.cli.osworld_qcow2_materializer",
        allow_abbrev=False,
    )
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output-parent", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        with materialize_osworld_qcow2_from_repo_root(
            repo_root=arguments.repo_root,
            archive_path=arguments.archive,
            output_parent=arguments.output_parent,
        ) as result:
            result.verify_full()
            print(
                json.dumps(
                    {
                        "output_name": result.output_name,
                        "sha256": result.sha256,
                        "size": result.size,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    except OSWorldQcow2MaterializationError:
        print("OSWORLD_QCOW2_MATERIALIZATION_FAILED", file=sys.stderr)
        return 1
    return 0


__all__ = ["materialize_osworld_qcow2_from_repo_root"]


def _reject_implementation_module_execution() -> int:
    """拒绝把 canonical implementation 当作 ``python -m`` CLI 执行。

    输入参数：无；刻意不读取 ``sys.argv``，因此不会解析或回显操作者
        参数，也不会访问 manifest、归档或输出路径。
    输出返回值：向 stderr 输出不含输入值的固定迁移提示并返回 1。
    """

    print(
        "OSWORLD_QCOW2_IMPLEMENTATION_MODULE_NOT_CLI; "
        "use python -m paraguibench.cli.osworld_qcow2_materializer",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(_reject_implementation_module_execution())
