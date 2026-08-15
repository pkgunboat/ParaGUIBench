"""Settings-001 evaluator-only derived gold 的私有物化边界。"""

from __future__ import annotations

from contextlib import ExitStack
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
from typing import BinaryIO

from PIL import Image

from paraguibench.runtime.assets import load_asset_manifest_bytes
from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    GoldAvailability,
    GoldAvailabilityStatus,
    GoldAssetError,
    GoldAssetResolver,
    GoldIntegrityError,
    GoldManifestError,
    GoldUnavailableError,
    _create_private_temporary_file,
    _open_private_fetch_parent,
    _write_all,
    validate_derived_gold_asset_manifest,
)


_CHUNK_SIZE = 1024 * 1024
_MAX_PROBE_STDOUT = 1024 * 1024
_MAX_TOOL_STDERR = 64 * 1024
_MAX_VERSION_STDOUT = 64 * 1024
_PTS_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.[0-9]{6}$")


class DerivedGoldMaterializationError(RuntimeError):
    """表示派生 gold 没有满足固定媒体与发布合同。"""

    def __init__(self) -> None:
        """构造不包含路径、工具输出或媒体内容的固定异常。"""

        super().__init__("DERIVED_GOLD_MATERIALIZATION_FAILED")


class _VerifiedTool:
    """保存工具调用名、解析目标与两者冻结身份。"""

    def __init__(
        self,
        *,
        invocation_path: Path,
        executable_path: Path,
        link_identity: tuple[int, int, int, int, int, int, int, int, int],
        executable_identity: tuple[int, int, int, int, int, int, int, int, int],
    ) -> None:
        """保存已经版本校验的不可变工具身份。

        输入参数：invocation_path 是调用方提供的路径；executable_path
            是其当时解析的真实普通文件；两个 identity 为对应快照。
        输出返回值：无。
        """

        self.invocation_path = invocation_path
        self.executable_path = executable_path
        self.link_identity = link_identity
        self.executable_identity = executable_identity

    def verify_continuity(self) -> None:
        """比较当前调用名、实路径与冻结工具身份。

        输入参数：无。
        输出返回值：链接目标或真实文件任一漂移时固定失败。
        """

        current_link = os.lstat(self.invocation_path)
        current_resolved = self.invocation_path.resolve(strict=True)
        current_executable = os.stat(current_resolved, follow_symlinks=False)
        if (
            current_resolved != self.executable_path
            or _tool_identity(current_link) != self.link_identity
            or _tool_identity(current_executable) != self.executable_identity
        ):
            raise DerivedGoldMaterializationError


def materialize_derived_gold(
    *,
    manifest: DerivedGoldAssetManifest,
    repo_root: Path,
    asset_cache_root: Path,
    gold_cache_root: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    timeout_seconds: float,
) -> GoldAvailability:
    """从固定 input MP4 私有物化 Settings-001 唯一 gold PNG。

    输入参数：
        manifest：严格 v2 loader 产生的 derived manifest。
        repo_root：包含固定 input manifest 的仓库根。
        asset_cache_root：已验证任务 input 的私有缓存根。
        gold_cache_root：只允许 evaluator 读取的私有输出根。
        ffmpeg_path/ffprobe_path：已安装工具的绝对路径。
        timeout_seconds：每个受限子进程的正有限超时秒数。
    输出返回值：
        输入、PTS、PNG 编码与 RGB 身份全部闭合后，返回不含
        路径或摘要的 ``AVAILABLE`` 结果。
    异常：
        GoldManifestError：调用方绕过严格 loader 或参数类型无效。
        DerivedGoldMaterializationError：任一文件、工具链、PTS、输出
            或私有发布边界不一致。
    """

    _validate_public_arguments(
        manifest=manifest,
        repo_root=repo_root,
        asset_cache_root=asset_cache_root,
        gold_cache_root=gold_cache_root,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        timeout_seconds=timeout_seconds,
    )
    output = manifest.entries[0]
    resolver = GoldAssetResolver(manifest=manifest, cache_root=gold_cache_root)
    try:
        return resolver.verify_required((output.logical_key,))
    except GoldUnavailableError:
        pass
    except GoldAssetError:
        raise DerivedGoldMaterializationError from None
    try:
        with ExitStack() as stack:
            manifest_file = stack.enter_context(
                _open_regular_nofollow(repo_root / manifest.asset_manifest)
            )
            manifest_payload = _read_all_held(
                manifest_file,
                maximum=4 * 1024 * 1024,
            )
            if hashlib.sha256(manifest_payload).hexdigest() != (
                manifest.asset_manifest_sha256
            ):
                raise DerivedGoldMaterializationError
            input_manifest = load_asset_manifest_bytes(manifest_payload)
            matching = tuple(
                entry
                for entry in input_manifest.files
                if entry.path == manifest.source_input.path
            )
            if (
                input_manifest.asset_set_id != manifest.asset_set_id
                or len(matching) != 1
                or matching[0].size != manifest.source_input.size
                or matching[0].sha256 != manifest.source_input.sha256
                or matching[0].media_type != manifest.source_input.media_type
            ):
                raise DerivedGoldMaterializationError

            source_handle = stack.enter_context(
                _open_private_source(
                    asset_cache_root=asset_cache_root,
                    asset_set_id=manifest.asset_set_id,
                    relative_path=manifest.source_input.path,
                )
            )
            source_file = source_handle.file
            source_snapshot = os.fstat(source_file.fileno())
            if source_snapshot.st_size != manifest.source_input.size:
                raise DerivedGoldMaterializationError
            source_sha256, source_size = _hash_held_file(
                source_file,
                maximum=manifest.source_input.size,
            )
            if (
                source_size != manifest.source_input.size
                or source_sha256 != manifest.source_input.sha256
            ):
                raise DerivedGoldMaterializationError

            ffmpeg_tool = _verify_tool_identity(
                ffmpeg_path,
                expected_program="ffmpeg",
                expected_version=manifest.derivation.ffmpeg_version,
                timeout_seconds=timeout_seconds,
            )
            ffprobe_tool = _verify_tool_identity(
                ffprobe_path,
                expected_program="ffprobe",
                expected_version=manifest.derivation.ffprobe_version,
                timeout_seconds=timeout_seconds,
            )
            probe_payload = _run_media_tool(
                tool=ffprobe_tool,
                arguments=(
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    manifest.derivation.protocol_whitelist,
                    "-select_streams",
                    manifest.derivation.stream_selector,
                    "-show_entries",
                    f"frame={manifest.derivation.timestamp_field}",
                    "-of",
                    "csv=p=0",
                    "-i",
                    "pipe:0",
                ),
                source_file=source_file,
                timeout_seconds=timeout_seconds,
                maximum_stdout=_MAX_PROBE_STDOUT,
            )
            _verify_probe_contract(probe_payload, manifest)

            png_payload = _run_media_tool(
                tool=ffmpeg_tool,
                arguments=(
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-protocol_whitelist",
                    manifest.derivation.protocol_whitelist,
                    "-threads",
                    str(manifest.derivation.threads),
                    "-hwaccel",
                    "none",
                    "-i",
                    "pipe:0",
                    "-map",
                    "0:v:0",
                    "-vf",
                    f"select=eq(n\\,{manifest.derivation.selected_frame_index})",
                    "-frames:v",
                    "1",
                    "-fps_mode",
                    "passthrough",
                    "-c:v",
                    "png",
                    "-f",
                    "image2pipe",
                    "pipe:1",
                ),
                source_file=source_file,
                timeout_seconds=timeout_seconds,
                maximum_stdout=output.size,
            )
            _verify_png_payload(png_payload, manifest)
            _verify_held_source_continuity(
                source_file,
                source_snapshot,
                expected_size=manifest.source_input.size,
                expected_sha256=manifest.source_input.sha256,
            )
            source_handle.verify_named_continuity()
            _publish_private_output(
                cache_root=gold_cache_root,
                manifest_id=manifest.manifest_id,
                runtime_relative_path=output.runtime_locator.value,
                payload=png_payload,
            )
            availability = resolver.verify_required((output.logical_key,))
    except (GoldManifestError, DerivedGoldMaterializationError):
        raise
    except Exception:
        raise DerivedGoldMaterializationError from None

    if (
        availability.status is not GoldAvailabilityStatus.AVAILABLE
        or availability.requested_count != 1
    ):
        raise DerivedGoldMaterializationError
    return availability


def _validate_public_arguments(
    *,
    manifest: object,
    repo_root: object,
    asset_cache_root: object,
    gold_cache_root: object,
    ffmpeg_path: object,
    ffprobe_path: object,
    timeout_seconds: object,
) -> None:
    """验证公开物化边界的强类型与单项闭集。

    输入参数：公开函数的全部不可信调用参数。
    输出返回值：全部类型、路径与超时合法时返回 ``None``。
    """

    validate_derived_gold_asset_manifest(manifest)
    for value in (
        repo_root,
        asset_cache_root,
        gold_cache_root,
        ffmpeg_path,
        ffprobe_path,
    ):
        if not isinstance(value, Path) or not value.is_absolute():
            raise GoldManifestError
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise GoldManifestError
    try:
        resolved_repo = repo_root.resolve(strict=True)
        resolved_assets = asset_cache_root.resolve(strict=True)
        resolved_gold = gold_cache_root.resolve(strict=False)
    except OSError:
        raise DerivedGoldMaterializationError from None
    if _paths_overlap(resolved_gold, resolved_repo) or _paths_overlap(
        resolved_gold,
        resolved_assets,
    ):
        raise DerivedGoldMaterializationError


def _paths_overlap(left: Path, right: Path) -> bool:
    """判断两个已解析绝对路径是否相同或存在双向包含。

    输入参数：left/right 为 ``Path.resolve`` 后的库根。
    输出返回值：任一方为另一方本身或祖先时返回 ``True``。
    """

    return left == right or left in right.parents or right in left.parents


class _VerifiedBinaryFile:
    """保存一个 no-follow 普通文件及其打开时身份。"""

    def __init__(self, path: Path, *, require_private: bool) -> None:
        """逐级打开 path，并拒绝 symlink/非普通/多链接目标。"""

        self._path = path
        self._require_private = require_private
        self._descriptor = -1
        self._file: BinaryIO | None = None
        self._snapshot: os.stat_result | None = None

    def __enter__(self) -> BinaryIO:
        """返回基于已验证 descriptor 的可 seek 二进制流。"""

        path = self._path
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise DerivedGoldMaterializationError
            if self._require_private and metadata.st_mode & 0o077:
                raise DerivedGoldMaterializationError
            path_metadata = os.stat(path, follow_symlinks=False)
            if _identity(path_metadata) != _identity(metadata):
                raise DerivedGoldMaterializationError
            self._snapshot = metadata
            self._descriptor = descriptor
            self._file = os.fdopen(descriptor, "rb", closefd=False)
            return self._file
        except Exception:
            os.close(descriptor)
            raise

    def __exit__(self, *_args: object) -> None:
        """关闭流与 descriptor；不删除任何路径。"""

        if self._file is not None:
            self._file.close()
        if self._descriptor >= 0:
            os.close(self._descriptor)


def _open_regular_nofollow(path: Path) -> _VerifiedBinaryFile:
    """构造普通固定文件的 no-follow 打开边界。"""

    return _VerifiedBinaryFile(path, require_private=False)


class _VerifiedPrivateAssetFile:
    """逐级 no-follow 固定私有 asset 目录链与单链接源文件。"""

    def __init__(
        self,
        *,
        asset_cache_root: Path,
        asset_set_id: str,
        relative_path: str,
    ) -> None:
        """保存尚未打开的绝对 cache 根与严格 manifest 路径。

        输入参数：asset_cache_root 为私有 input cache 根；asset_set_id
            为单路径任务身份；relative_path 为清单内的相对媒体路径。
        输出返回值：无；实际打开延迟到上下文进入。
        """

        self._root = asset_cache_root
        self._asset_set_id = asset_set_id
        self._relative_path = relative_path
        self._directories: list[int] = []
        self._named_directories: list[tuple[int, str, int, os.stat_result]] = []
        self._descriptor = -1
        self._stream: BinaryIO | None = None
        self._snapshot: os.stat_result | None = None
        self._leaf_name = ""

    def __enter__(self) -> _VerifiedPrivateAssetFile:
        """以 openat/O_NOFOLLOW 逐级打开根、任务目录与媒体文件。

        输入参数：无。
        输出返回值：返回保持全部父目录 FD 的本对象。
        """

        if (
            not self._root.is_absolute()
            or "/" in self._asset_set_id
            or self._asset_set_id in {"", ".", ".."}
        ):
            raise DerivedGoldMaterializationError
        relative_parts = Path(self._relative_path).parts
        if (
            not relative_parts
            or Path(self._relative_path).is_absolute()
            or any(part in {"", ".", ".."} for part in relative_parts)
        ):
            raise DerivedGoldMaterializationError
        directory_flags = os.O_RDONLY
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._root.anchor, directory_flags)
            self._directories.append(descriptor)
            for part in self._root.parts[1:]:
                parent_descriptor = descriptor
                descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
                snapshot = os.fstat(descriptor)
                self._directories.append(descriptor)
                self._named_directories.append(
                    (parent_descriptor, part, descriptor, snapshot)
                )
            _require_private_directory(os.fstat(descriptor))
            parent_descriptor = descriptor
            descriptor = os.open(
                self._asset_set_id,
                directory_flags,
                dir_fd=parent_descriptor,
            )
            snapshot = os.fstat(descriptor)
            self._directories.append(descriptor)
            self._named_directories.append(
                (parent_descriptor, self._asset_set_id, descriptor, snapshot)
            )
            _require_private_directory(snapshot)
            for part in relative_parts[:-1]:
                parent_descriptor = descriptor
                descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
                snapshot = os.fstat(descriptor)
                self._directories.append(descriptor)
                self._named_directories.append(
                    (parent_descriptor, part, descriptor, snapshot)
                )
                _require_private_directory(snapshot)
            file_flags = os.O_RDONLY
            file_flags |= getattr(os, "O_CLOEXEC", 0)
            file_flags |= getattr(os, "O_NOFOLLOW", 0)
            file_flags |= getattr(os, "O_NONBLOCK", 0)
            self._leaf_name = relative_parts[-1]
            self._descriptor = os.open(
                self._leaf_name,
                file_flags,
                dir_fd=descriptor,
            )
            metadata = os.fstat(self._descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
            ):
                raise DerivedGoldMaterializationError
            named = os.stat(
                self._leaf_name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if _identity(named) != _identity(metadata):
                raise DerivedGoldMaterializationError
            self._snapshot = metadata
            self._stream = os.fdopen(self._descriptor, "rb", closefd=False)
            return self
        except Exception:
            self.__exit__()
            raise

    @property
    def file(self) -> BinaryIO:
        """返回基于已固定源 FD 的可 seek 二进制流。"""

        if self._stream is None:
            raise DerivedGoldMaterializationError
        return self._stream

    def verify_named_continuity(self) -> None:
        """在发布前比较父目录内当前名称与 held FD 身份。

        输入参数：无。
        输出返回值：名称仍指向原单链接私有文件时返回
            ``None``；任何 ABA 替换均固定失败。
        """

        if self._descriptor < 0 or not self._directories:
            raise DerivedGoldMaterializationError
        for parent, name, descriptor, snapshot in self._named_directories:
            held_directory = os.fstat(descriptor)
            named_directory = os.stat(
                name,
                dir_fd=parent,
                follow_symlinks=False,
            )
            if _directory_identity(held_directory) != _directory_identity(
                snapshot
            ) or _directory_identity(named_directory) != _directory_identity(
                held_directory
            ):
                raise DerivedGoldMaterializationError
        held = os.fstat(self._descriptor)
        named = os.stat(
            self._leaf_name,
            dir_fd=self._directories[-1],
            follow_symlinks=False,
        )
        if (
            self._snapshot is None
            or _identity(held) != _identity(self._snapshot)
            or _identity(named) != _identity(held)
        ):
            raise DerivedGoldMaterializationError

    def __exit__(self, *_args: object) -> None:
        """关闭源文件流、文件 FD 和所有祖先目录 FD。"""

        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1
        for descriptor in reversed(self._directories):
            os.close(descriptor)
        self._directories.clear()
        self._named_directories.clear()


def _require_private_directory(metadata: os.stat_result) -> None:
    """验证一个 held 目录属于当前用户且精确为 0700。

    输入参数：metadata 为 ``fstat`` 结果。
    输出返回值：私有目录合法时返回 ``None``。
    """

    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.getuid()
    ):
        raise DerivedGoldMaterializationError


def _directory_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int]:
    """投影不受目录成员变化影响的目录路径身份。

    输入参数：metadata 为 held 或 no-follow named 目录的 stat。
    输出返回值：返回 dev/inode/type/mode/uid；不包含会因无关并发成员
        变化而漂移的 nlink/size。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
    )


def _open_private_source(
    *,
    asset_cache_root: Path,
    asset_set_id: str,
    relative_path: str,
) -> _VerifiedPrivateAssetFile:
    """构造逐级 no-follow 的私有 input 源打开边界。"""

    return _VerifiedPrivateAssetFile(
        asset_cache_root=asset_cache_root,
        asset_set_id=asset_set_id,
        relative_path=relative_path,
    )


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    """投影文件类型、inode、链接、owner、mode 与大小身份。"""

    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
    )


def _read_all_held(file: BinaryIO, *, maximum: int) -> bytes:
    """从 held file 有界读取全部字节并恢复偏移。"""

    file.seek(0)
    payload = file.read(maximum + 1)
    if len(payload) > maximum:
        raise DerivedGoldMaterializationError
    file.seek(0)
    return payload


def _hash_held_file(file: BinaryIO, *, maximum: int) -> tuple[str, int]:
    """以 ``maximum + 1`` 上限对 held file 做 SHA-256 并恢复偏移。

    输入参数：file 为已固定可 seek 文件；maximum 为 manifest
        声明的精确最大字节数。
    输出返回值：未超限时返回 SHA-256 与实际字节数；即使源在
        首次 fstat 后并发增长，也最多读 ``maximum + 1`` 并失败。
    """

    digest = hashlib.sha256()
    byte_count = 0
    file.seek(0)
    while True:
        remaining = maximum + 1 - byte_count
        if remaining <= 0:
            raise DerivedGoldMaterializationError
        chunk = file.read(min(_CHUNK_SIZE, remaining))
        if not chunk:
            break
        byte_count += len(chunk)
        if byte_count > maximum:
            raise DerivedGoldMaterializationError
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest(), byte_count


def _verify_tool_identity(
    executable: Path,
    *,
    expected_program: str,
    expected_version: str,
    timeout_seconds: float,
) -> _VerifiedTool:
    """验证工具链接与真实文件身份，并返回固定目标路径。

    输入参数：executable 可以是包管理器建立的单层或多层 symlink；
        version 与摘要必须匹配 manifest。
    输出返回值：已解析、打开前后身份不变的普通可执行文件路径。
    """

    link_before = os.lstat(executable)
    resolved = executable.resolve(strict=True)
    before = os.stat(resolved, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise DerivedGoldMaterializationError
    if before.st_mode & 0o022:
        raise DerivedGoldMaterializationError
    stdout = _run_bounded_process(
        argv=(os.fspath(resolved), "-version"),
        stdin_descriptor=None,
        timeout_seconds=timeout_seconds,
        maximum_stdout=_MAX_VERSION_STDOUT,
        maximum_stderr=_MAX_TOOL_STDERR,
    )
    link_after = os.lstat(executable)
    after = os.stat(resolved, follow_symlinks=False)
    if _tool_identity(link_before) != _tool_identity(link_after) or _tool_identity(
        before
    ) != _tool_identity(after):
        raise DerivedGoldMaterializationError
    first_line = stdout.splitlines()[0].decode("ascii", "strict")
    expected_prefix = f"{expected_program} version {expected_version} "
    if not first_line.startswith(expected_prefix):
        raise DerivedGoldMaterializationError
    return _VerifiedTool(
        invocation_path=executable,
        executable_path=resolved,
        link_identity=_tool_identity(link_before),
        executable_identity=_tool_identity(before),
    )


def _tool_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int, int]:
    """投影工具的路径、权限、大小与纳秒变更身份。

    输入参数：metadata 为工具 symlink 或真实普通文件的 stat。
    输出返回值：返回 dev/inode/type/mode/nlink/uid/size/mtime_ns/
        ctime_ns，可拒绝同 inode 同长度原地改写。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _run_media_tool(
    *,
    tool: _VerifiedTool,
    arguments: tuple[str, ...],
    source_file: BinaryIO,
    timeout_seconds: float,
    maximum_stdout: int,
) -> bytes:
    """以 held source FD 作为 stdin 运行受限媒体工具。

    输入参数：tool 是已冻结版本与路径身份的工具；arguments 是
        固定参数；source_file 为 held MP4；timeout/maximum 是资源上限。
    输出返回值：工具身份在启动前后都稳定且运行成功时返回
        有界 stdout。
    """

    tool.verify_continuity()
    source_file.seek(0)
    descriptor = os.dup(source_file.fileno())
    try:
        return _run_bounded_process(
            argv=(os.fspath(tool.executable_path), *arguments),
            stdin_descriptor=descriptor,
            timeout_seconds=timeout_seconds,
            maximum_stdout=maximum_stdout,
            maximum_stderr=_MAX_TOOL_STDERR,
        )
    finally:
        os.close(descriptor)
        tool.verify_continuity()


def _run_bounded_process(
    *,
    argv: tuple[str, ...],
    stdin_descriptor: int | None,
    timeout_seconds: float,
    maximum_stdout: int,
    maximum_stderr: int,
) -> bytes:
    """有界收集子进程输出，超时时终止独立进程组。"""

    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL if stdin_descriptor is None else stdin_descriptor,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        start_new_session=True,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
    )
    assert process.stdout is not None and process.stderr is not None
    selector: selectors.BaseSelector | None = None
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": maximum_stdout, "stderr": maximum_stderr}
    deadline = time.monotonic() + timeout_seconds
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DerivedGoldMaterializationError
            events = selector.select(timeout=remaining)
            if not events:
                raise DerivedGoldMaterializationError
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                destination = outputs[key.data]
                destination.extend(chunk)
                if len(destination) > limits[key.data]:
                    raise DerivedGoldMaterializationError
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DerivedGoldMaterializationError
        return_code = process.wait(timeout=remaining)
        if return_code != 0 or outputs["stderr"]:
            raise DerivedGoldMaterializationError
        return bytes(outputs["stdout"])
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
        raise
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.wait()
        if selector is not None:
            selector.close()
        process.stdout.close()
        process.stderr.close()


def _verify_probe_contract(
    payload: bytes,
    manifest: DerivedGoldAssetManifest,
) -> None:
    """验证全部视频帧的 PTS 顺序和首个不小于 8s 的唯一索引。

    输入参数：payload 是 ffprobe 固定 CSV 输出；manifest 提供帧数、
        时间精度和选帧合同。
    输出返回值：所有行均为六位小数 PTS，且选帧合同唯一成立时
        返回 ``None``。H.264 首帧可携带 side-data，ffprobe CSV 会为
        该空列追加唯一逗号；其他额外列或空行一律拒绝。
    """

    try:
        text = payload.decode("ascii", "strict")
    except UnicodeError:
        raise DerivedGoldMaterializationError from None
    if not text.endswith("\n") or "\r" in text:
        raise DerivedGoldMaterializationError
    lines = text[:-1].split("\n")
    if len(lines) != manifest.derivation.source_frame_count or not all(lines):
        raise DerivedGoldMaterializationError
    values: list[Decimal] = []
    for index, line in enumerate(lines):
        if index == 0:
            if not line.endswith(","):
                raise DerivedGoldMaterializationError
            line = line[:-1]
        if "," in line or _PTS_PATTERN.fullmatch(line) is None:
            raise DerivedGoldMaterializationError
        try:
            values.append(Decimal(line))
        except InvalidOperation:
            raise DerivedGoldMaterializationError from None
    if any(left >= right for left, right in zip(values, values[1:])):
        raise DerivedGoldMaterializationError
    requested = Decimal(manifest.derivation.requested_pts)
    selected = manifest.derivation.selected_frame_index
    if (
        selected <= 0
        or selected >= len(values)
        or values[selected - 1] >= requested
        or values[selected] < requested
        or f"{values[selected - 1]:.6f}" != manifest.derivation.previous_pts
        or f"{values[selected]:.6f}" != manifest.derivation.selected_pts
    ):
        raise DerivedGoldMaterializationError


def _verify_png_payload(
    payload: bytes,
    manifest: DerivedGoldAssetManifest,
) -> None:
    """验证 PNG 编码字节、解码 RGB 像素与画布尺寸。"""

    output = manifest.entries[0]
    if (
        len(payload) != output.size
        or hashlib.sha256(payload).hexdigest() != output.sha256
    ):
        raise DerivedGoldMaterializationError
    try:
        with Image.open(BytesIO(payload)) as image:
            image.load()
            rgb = image.convert("RGB")
            if rgb.size != (output.width, output.height):
                raise DerivedGoldMaterializationError
            rgb_sha256 = hashlib.sha256(rgb.tobytes()).hexdigest()
    except DerivedGoldMaterializationError:
        raise
    except Exception:
        raise DerivedGoldMaterializationError from None
    if rgb_sha256 != output.decoded_rgb_sha256:
        raise DerivedGoldMaterializationError


def _verify_held_source_continuity(
    source_file: BinaryIO,
    before: os.stat_result,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    """在发布前再次对同一 source FD 全量哈希并比较身份。"""

    sha256, size = _hash_held_file(source_file, maximum=expected_size)
    after = os.fstat(source_file.fileno())
    if (
        _identity(before) != _identity(after)
        or size != expected_size
        or sha256 != expected_sha256
    ):
        raise DerivedGoldMaterializationError


def _publish_private_output(
    *,
    cache_root: Path,
    manifest_id: str,
    runtime_relative_path: str,
    payload: bytes,
) -> None:
    """在私有 0700 目录中以 no-clobber 链接发布 0600 PNG。"""

    parent_descriptor, target_name = _open_private_fetch_parent(
        cache_root,
        manifest_id=manifest_id,
        runtime_relative_path=runtime_relative_path,
    )
    temporary_descriptor = -1
    temporary_name: str | None = None
    target_linked = False
    committed = False
    try:
        try:
            os.stat(target_name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise GoldIntegrityError
        temporary_descriptor, temporary_name = _create_private_temporary_file(
            parent_descriptor
        )
        _write_all(temporary_descriptor, payload)
        os.fchmod(temporary_descriptor, 0o600)
        os.fsync(temporary_descriptor)
        os.link(
            temporary_name,
            target_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        target_linked = True
        published = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        linked_identity = _identity(os.fstat(temporary_descriptor))
        if _identity(published) != linked_identity:
            raise DerivedGoldMaterializationError
        current_temp = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _identity(current_temp) != linked_identity:
            raise DerivedGoldMaterializationError
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        temporary_name = None
        final_metadata = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(final_metadata.st_mode)
            or stat.S_IMODE(final_metadata.st_mode) != 0o600
            or final_metadata.st_nlink != 1
            or final_metadata.st_size != len(payload)
        ):
            raise DerivedGoldMaterializationError
        os.fsync(parent_descriptor)
        committed = True
    except (GoldIntegrityError, DerivedGoldMaterializationError):
        raise
    except OSError:
        raise DerivedGoldMaterializationError from None
    finally:
        cleanup_error: Exception | None = None
        if target_linked and not committed and temporary_descriptor >= 0:
            try:
                _unlink_held_name_if_unchanged(
                    parent_descriptor,
                    target_name,
                    temporary_descriptor,
                )
            except Exception as error:
                cleanup_error = error
        if temporary_name is not None and temporary_descriptor >= 0:
            try:
                _unlink_held_name_if_unchanged(
                    parent_descriptor,
                    temporary_name,
                    temporary_descriptor,
                )
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
        if temporary_descriptor >= 0:
            try:
                os.close(temporary_descriptor)
            except OSError:
                if cleanup_error is None:
                    cleanup_error = DerivedGoldMaterializationError()
        try:
            os.close(parent_descriptor)
        except OSError:
            if cleanup_error is None:
                cleanup_error = DerivedGoldMaterializationError()
        if cleanup_error is not None:
            raise cleanup_error


def _unlink_held_name_if_unchanged(
    parent_descriptor: int,
    name: str,
    held_descriptor: int,
) -> None:
    """只删除仍与 held FD 完全同身份的本次临时文件。

    输入参数：parent_descriptor 为已固定私有父目录；name 是本次
        O_EXCL 临时名；held_descriptor 为仍打开的创建文件。
    输出返回值：名称不存在或安全删除后返回 ``None``；若名称已被
        替换，则绝不删除未知对象并固定失败。
    """

    held = os.fstat(held_descriptor)
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if _identity(current) != _identity(held):
        raise DerivedGoldMaterializationError
    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except OSError:
        raise DerivedGoldMaterializationError from None


__all__ = ["DerivedGoldMaterializationError", "materialize_derived_gold"]
