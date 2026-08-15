"""Evaluator 专用 pinned gold manifest 与离线读取边界。"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import errno
from enum import StrEnum
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
from typing import BinaryIO
from typing import Any
import unicodedata
from urllib.parse import quote
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.request import urlopen


_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_PATTERN_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_PATTERN = re.compile(rf"^{_UUID_PATTERN_TEXT}$")
_LOGICAL_KEY_PATTERN = re.compile(
    rf"^osworld-gold:({_UUID_PATTERN_TEXT}):expected:(0|[1-9][0-9]*):v1$"
)
_PROVENANCE_REF_PATTERN = re.compile(
    rf"^osworld:evaluator:({_UUID_PATTERN_TEXT}):expected:(0|[1-9][0-9]*)$"
)
_MANIFEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
)
_SPDX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$")
_ALLOWED_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/x-bibtex",
        "application/zip",
        "image/png",
        "text/csv",
    }
)
_MAX_ENTRY_COUNT = 4_096
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_SINGLE_ENTRY_BYTES = 536_870_912
_MAX_TOTAL_BYTES = 1_073_741_824
_SETTINGS001_TASK_ID = "Operation-FileOperate-Settings-001"
_SETTINGS001_GOLD_MANIFEST_ID = f"{_SETTINGS001_TASK_ID}-gold-v2"
_SETTINGS001_GOLD_LOGICAL_KEY = (
    "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v2"
)


class GoldAssetError(RuntimeError):
    """表示 gold manifest 或离线资产无法安全使用。"""

    code = "GOLD_ERROR"

    def __init__(self) -> None:
        """使用固定公开代码构造不携带私密值的异常。

        输入参数：
            无；代码由具体异常类的 ``code`` 类属性固定。
        输出返回值：
            无；异常文本与稳定 code 完全相同。
        """

        super().__init__(self.code)


class GoldManifestError(GoldAssetError, ValueError):
    """表示 gold manifest 的 schema 或语义无效。"""

    code = "GOLD_MANIFEST_INVALID"


class GoldUnavailableError(GoldAssetError):
    """表示必需 gold 尚未配置到离线缓存。"""

    code = "GOLD_NOT_PROVISIONED"


class GoldIntegrityError(GoldAssetError):
    """表示缓存 gold 与 manifest 固定的字节身份不一致。"""

    code = "GOLD_CACHE_INTEGRITY_ERROR"


class GoldMediaTypeError(GoldAssetError):
    """表示 evaluator 请求的媒体家族与 manifest 不一致。"""

    code = "GOLD_MEDIA_TYPE_ERROR"


class GoldLimitExceededError(GoldAssetError):
    """表示 gold 条目或实际读取超过 evaluator 资源上限。"""

    code = "GOLD_LIMIT_EXCEEDED"


class GoldReadError(GoldAssetError):
    """表示 gold 缓存在完整性校验期间无法可靠读取。"""

    code = "GOLD_READ_ERROR"


class GoldFetchError(GoldAssetError):
    """表示 evaluator-only gold 显式预置过程失败。"""

    code = "GOLD_FETCH_ERROR"


class GoldAvailabilityStatus(StrEnum):
    """表示指定 gold 闭集已完成离线完整性校验。"""

    AVAILABLE = "AVAILABLE"


@dataclass(frozen=True, slots=True)
class GoldAvailability:
    """保存不含 locator、摘要或内容的 gold preflight 结果。"""

    status: GoldAvailabilityStatus
    requested_count: int


@dataclass(frozen=True, slots=True)
class GoldSourceLocator:
    """保存一个固定版本的外部 gold 来源定位信息。"""

    provider: str
    repository: str
    revision: str
    path: str


@dataclass(frozen=True, slots=True)
class GoldRuntimeLocator:
    """保存 gold 在私有离线缓存中的逻辑定位信息。"""

    kind: str
    value: str


@dataclass(frozen=True, slots=True)
class GoldLicense:
    """保存数据资产本身的许可状态与直接证据。"""

    status: str
    spdx_expression: str | None
    evidence_ref: str
    distribution: str


@dataclass(frozen=True, slots=True)
class DerivedGoldLicense:
    """保存私有派生 gold 对源 input 许可证据的严格继承声明。

    输入参数：status/SPDX/evidence 固定来源数据集许可；basis 明确许可
        依据来自 canonical source input；distribution 只允许私有物化。
    输出返回值：不可变许可合同；它不授予派生 PNG 的公开再分发权。
    """

    status: str
    spdx_expression: str
    evidence_ref: str
    basis: str
    distribution: str


@dataclass(frozen=True, slots=True)
class GoldProvenance:
    """保存 gold 与上游 task/evaluator contract 的可追溯绑定。"""

    source_benchmark: str
    source_task_id: str
    source_evaluator_id: str
    expected_index: int
    source_contract_sha256: str
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class GoldAssetEntry:
    """保存一个按 logical key 寻址的 pinned gold 条目。"""

    logical_key: str
    source_locator: GoldSourceLocator
    runtime_locator: GoldRuntimeLocator
    size: int
    sha256: str
    media_type: str
    license: GoldLicense
    provenance: GoldProvenance


@dataclass(frozen=True, slots=True)
class GoldAssetManifest:
    """保存 evaluator 可见、Agent 不可见的 gold 资产闭集。"""

    manifest_id: str
    distribution_policy: str
    entries: tuple[GoldAssetEntry, ...]


@dataclass(frozen=True, slots=True)
class DerivedGoldSourceInput:
    """保存 derived gold 唯一允许读取的固定 input 资产身份。

    输入参数：path/size/sha256/media_type 来自 canonical strict input manifest。
    输出返回值：不可变、不可联网的 source identity；源视频仍是 canonical
        guest-visible input，只有派生过程与输出 PNG 保持 host-only。
    """

    path: str
    size: int
    sha256: str
    media_type: str


@dataclass(frozen=True, slots=True)
class DerivedGoldDerivation:
    """保存 Settings-001 视频帧派生协议的确定性工具链身份。

    输入参数：protocol/PTS/frame/toolchain 字段均来自真实 fixture 的固定证据。
    输出返回值：供 materializer 在启动子进程前逐字段复核的不可变合同。
    """

    protocol_id: str
    stream_selector: str
    timestamp_field: str
    frame_order: str
    index_origin: int
    timestamp_decimal_places: int
    requested_pts: str
    selected_frame_index: int
    selected_pts: str
    previous_pts: str
    source_frame_count: int
    ffmpeg_version: str
    ffprobe_version: str
    protocol_whitelist: str
    threads: int
    software_only: bool


@dataclass(frozen=True, slots=True)
class DerivedGoldAssetEntry:
    """保存 derived PNG 的 evaluator-only 固定输出身份。

    输入参数：logical key、私有缓存定位、编码/解码摘要与像素尺寸。
    输出返回值：production resolver 可复核的单项不可变输出合同。
    """

    logical_key: str
    runtime_locator: GoldRuntimeLocator
    size: int
    sha256: str
    decoded_rgb_sha256: str
    width: int
    height: int
    media_type: str
    provenance: GoldProvenance


@dataclass(frozen=True, slots=True)
class DerivedGoldAssetManifest:
    """保存 v2 ``derived_from_input`` evaluator-only gold 闭集。

    输入参数：manifest/task/input/derivation/output 均由严格 v2 loader 产生。
    输出返回值：与 v1 下载型 ``GoldAssetManifest`` 类型隔离的不可变合同。
    """

    schema_version: int
    manifest_id: str
    distribution_policy: str
    license: DerivedGoldLicense
    asset_manifest: str
    asset_manifest_sha256: str
    asset_set_id: str
    source_input: DerivedGoldSourceInput
    derivation: DerivedGoldDerivation
    entries: tuple[DerivedGoldAssetEntry, ...]


class GoldAssetResolver:
    """从私有缓存离线解析并快照化 pinned evaluator gold。"""

    def __init__(
        self,
        *,
        manifest: GoldAssetManifest | DerivedGoldAssetManifest,
        cache_root: Path,
    ) -> None:
        """绑定已校验 manifest 和离线缓存根。

        输入参数：
            manifest：已经严格 loader 验证的 gold 资产闭集。
            cache_root：由显式 prefetch 流程预先填充的私有根目录。
        输出返回值：
            无；构造期不创建目录、不读文件且不访问网络。
        异常：
            GoldManifestError：derived v2 实例未与唯一 canonical
                合同逐字段闭合。
            TypeError：其他 manifest 或 cache_root 类型无效。
        """

        if isinstance(manifest, DerivedGoldAssetManifest):
            manifest = validate_derived_gold_asset_manifest(manifest)
        elif isinstance(manifest, GoldAssetManifest):
            manifest = validate_gold_asset_manifest(manifest)
        else:
            raise TypeError("manifest 必须是严格 gold manifest")
        if not isinstance(cache_root, Path):
            raise TypeError("cache_root 必须是 Path")
        self._manifest = manifest
        self._cache_root = cache_root
        self._entries = {entry.logical_key: entry for entry in manifest.entries}

    def verify_required(
        self,
        logical_keys: tuple[str, ...],
    ) -> GoldAvailability:
        """离线验证一个 logical gold key 闭集的所有字节身份。

        输入参数：
            logical_keys：当前 evaluator metric 要求的唯一、非空逻辑键
                tuple；该序列不会进入返回值。
        输出返回值：
            全部条目通过后返回仅含 ``AVAILABLE`` 与请求数量的
            ``GoldAvailability``。
        异常：
            GoldManifestError：请求不是非空、唯一的 tuple，或键未声明。
            GoldUnavailableError/GoldIntegrityError/GoldReadError：任一缓存条目
                缺失、字节不一致或无法可靠读取。
        """

        if (
            not isinstance(logical_keys, tuple)
            or not logical_keys
            or any(not isinstance(item, str) for item in logical_keys)
            or len(logical_keys) != len(set(logical_keys))
        ):
            raise GoldManifestError
        for logical_key in logical_keys:
            entry = self._entries.get(logical_key)
            if entry is None:
                raise GoldManifestError
            with self.open_verified(
                logical_key,
                max_bytes=entry.size,
                expected_media_types=frozenset({entry.media_type}),
            ):
                pass
        return GoldAvailability(
            status=GoldAvailabilityStatus.AVAILABLE,
            requested_count=len(logical_keys),
        )

    def is_bound_to_manifest(
        self,
        manifest: GoldAssetManifest | DerivedGoldAssetManifest,
    ) -> bool:
        """判断 resolver 是否绑定同一冻结 gold manifest。

        输入参数：manifest 为当前 canonical task 重新安全加载的正式清单。
        输出返回值：类型正确且与构造时完整不可变 manifest 相等时返回
            ``True``；不暴露 logical key、路径、摘要或缓存位置。
        """

        if isinstance(manifest, DerivedGoldAssetManifest):
            try:
                manifest = validate_derived_gold_asset_manifest(manifest)
            except GoldManifestError:
                return False
        elif isinstance(manifest, GoldAssetManifest):
            try:
                manifest = validate_gold_asset_manifest(manifest)
            except GoldManifestError:
                return False
        else:
            return False
        return self._manifest == manifest

    @contextmanager
    def open_verified(
        self,
        logical_key: str,
        *,
        max_bytes: int,
        expected_media_types: frozenset[str],
    ) -> Iterator[BinaryIO]:
        """以有界读取、大小和 SHA-256 校验产生不可变 gold 快照。

        输入参数：
            logical_key：evidence spec 中的稳定 gold 逻辑键。
            max_bytes：当前 evaluator contract 允许的最大字节数。
            expected_media_types：当前 metric 允许的 manifest 媒体类型闭集。
        输出返回值：
            context manager 中的可 seek 二进制快照；退出时自动关闭。
        异常：
            GoldManifestError：logical key 未在闭集中。
            GoldMediaTypeError：媒体类型不匹配。
            GoldLimitExceededError：大小上限无效或 manifest 条目超限。
            GoldUnavailableError：离线缓存文件不存在。
            GoldIntegrityError/GoldReadError：字节身份不匹配或无法可靠读取。
        """

        if not isinstance(logical_key, str):
            raise GoldManifestError
        entry = self._entries.get(logical_key)
        if entry is None:
            raise GoldManifestError
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes <= 0
            or entry.size > max_bytes
        ):
            raise GoldLimitExceededError
        if (
            not isinstance(expected_media_types, frozenset)
            or not expected_media_types
            or any(not isinstance(item, str) for item in expected_media_types)
            or entry.media_type not in expected_media_types
        ):
            raise GoldMediaTypeError

        try:
            snapshot = tempfile.SpooledTemporaryFile(
                max_size=min(max_bytes, 8 * 1024 * 1024),
                mode="w+b",
            )
        except OSError:
            raise GoldReadError from None
        primary_error = False
        try:
            digest = hashlib.sha256()
            byte_count = 0
            source = _open_regular_cache_file(
                self._cache_root,
                manifest_id=self._manifest.manifest_id,
                runtime_relative_path=entry.runtime_locator.value,
            )
            try:
                with source:
                    while True:
                        chunk = source.read(min(1024 * 1024, entry.size + 1))
                        if not chunk:
                            break
                        byte_count += len(chunk)
                        if byte_count > entry.size or byte_count > max_bytes:
                            raise GoldIntegrityError
                        digest.update(chunk)
                        written = snapshot.write(chunk)
                        if written != len(chunk):
                            raise GoldReadError
            except OSError:
                raise GoldReadError from None
            if byte_count != entry.size or digest.hexdigest() != entry.sha256:
                raise GoldIntegrityError
            try:
                snapshot.seek(0)
            except OSError:
                raise GoldReadError from None
            yield snapshot
        except BaseException:
            primary_error = True
            raise
        finally:
            try:
                snapshot.close()
            except OSError:
                if not primary_error:
                    raise GoldReadError from None


def fetch_gold_assets(
    manifest: GoldAssetManifest | DerivedGoldAssetManifest,
    cache_root: Path,
    *,
    opener: Callable[..., Any] | None = None,
    timeout: float = 30.0,
) -> GoldAvailability:
    """显式下载并原子预置 manifest 中的 evaluator-only gold。

    输入参数：
        manifest：已经过严格 loader 校验的 pinned gold manifest。
        cache_root：仅供 evaluator 使用的私有离线缓存根目录。
        opener：可选 HTTP 系统边界；默认使用 ``urllib``，测试可注入
            等价 opener，且调用时不会附加认证 header。
        timeout：每个固定 commit 请求的有限超时秒数，默认 30 秒。
    输出返回值：
        所有条目安装并由离线 resolver 复核后，返回不含 locator、摘要
        或内容的 ``GoldAvailability``。
    异常：
        GoldManifestError：输入对象或固定 source locator 无效。
        GoldIntegrityError：缓存路径不安全，或下载字节身份不匹配。
        GoldFetchError：网络、HTTP 或本地持久化失败。

    说明：
        这是唯一允许联网的显式预置入口；``GoldAssetResolver`` 本身仍然
        只读取本地缓存，不会调用本函数或任何网络 API。
    """

    if isinstance(manifest, DerivedGoldAssetManifest):
        raise GoldFetchError
    if isinstance(manifest, GoldAssetManifest):
        manifest = validate_gold_asset_manifest(manifest)
    else:
        raise GoldManifestError
    if not isinstance(cache_root, Path):
        raise GoldManifestError
    if opener is not None and not callable(opener):
        raise GoldManifestError
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise GoldManifestError

    http_open = urlopen if opener is None else opener
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)
    for entry in manifest.entries:
        try:
            resolver.verify_required((entry.logical_key,))
        except (GoldUnavailableError, GoldIntegrityError):
            pass
        else:
            continue
        _fetch_one_gold_asset(
            entry,
            cache_root=cache_root,
            manifest_id=manifest.manifest_id,
            opener=http_open,
            timeout=float(timeout),
        )
    return resolver.verify_required(
        tuple(entry.logical_key for entry in manifest.entries)
    )


def _fetch_one_gold_asset(
    entry: GoldAssetEntry,
    *,
    cache_root: Path,
    manifest_id: str,
    opener: Callable[..., Any],
    timeout: float,
) -> None:
    """下载、校验并原子发布单个 pinned gold 条目。

    输入参数：
        entry：包含固定 HF commit、路径、大小和 SHA-256 的条目。
        cache_root：evaluator 私有缓存根。
        manifest_id：目标 manifest 的安全单路径分量。
        opener：HTTP 系统边界调用函数。
        timeout：已经验证为正有限值的请求超时秒数。
    输出返回值：
        无；成功时目标文件已以 0600 权限原子安装并完成目录 fsync。
    异常：
        GoldIntegrityError：本地路径门禁或下载字节身份校验失败。
        GoldFetchError：网络、HTTP 或文件系统持久化失败。
    """

    parent_descriptor, target_name = _open_private_fetch_parent(
        cache_root,
        manifest_id=manifest_id,
        runtime_relative_path=entry.runtime_locator.value,
    )
    temporary_descriptor = -1
    temporary_name: str | None = None
    try:
        temporary_descriptor, temporary_name = _create_private_temporary_file(
            parent_descriptor
        )
        request = Request(_build_huggingface_commit_url(entry), method="GET")
        try:
            response_context = opener(request, timeout=timeout)
            with response_context as response:
                _stream_verified_response(
                    response,
                    destination_descriptor=temporary_descriptor,
                    expected_size=entry.size,
                    expected_sha256=entry.sha256,
                )
        except GoldAssetError:
            raise
        except Exception:
            raise GoldFetchError from None

        try:
            os.fsync(temporary_descriptor)
            os.close(temporary_descriptor)
            temporary_descriptor = -1
            os.replace(
                temporary_name,
                target_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            temporary_name = None
            os.fsync(parent_descriptor)
        except OSError:
            raise GoldFetchError from None
    except GoldAssetError:
        raise
    except Exception:
        raise GoldFetchError from None
    finally:
        if temporary_descriptor >= 0:
            try:
                os.close(temporary_descriptor)
            except OSError:
                pass
        if temporary_name is not None:
            _unlink_at_best_effort(parent_descriptor, temporary_name)
        try:
            os.close(parent_descriptor)
        except OSError:
            pass


def _build_huggingface_commit_url(entry: GoldAssetEntry) -> str:
    """构造不含认证信息的不可变 Hugging Face dataset commit URL。

    输入参数：
        entry：严格 manifest 中的 gold 条目。
    输出返回值：
        repository、40 位 commit 与逐段安全编码路径组成的 HTTPS URL。
    异常：
        GoldManifestError：provider 不是受支持的 Hugging Face dataset，
            或调用方绕过 loader 构造了不安全 locator。
    """

    source = entry.source_locator
    if (
        source.provider != "huggingface_dataset"
        or _REPOSITORY_PATTERN.fullmatch(source.repository) is None
        or _REVISION_PATTERN.fullmatch(source.revision) is None
    ):
        raise GoldManifestError
    _validate_safe_relative_path(source.path, allow_hash=True)
    encoded_path = quote(
        source.path,
        safe="/",
        encoding="utf-8",
        errors="strict",
    )
    return (
        "https://huggingface.co/datasets/"
        f"{source.repository}/resolve/{source.revision}/{encoded_path}"
    )


def _open_private_fetch_parent(
    cache_root: Path,
    *,
    manifest_id: str,
    runtime_relative_path: str,
) -> tuple[int, str]:
    """逐级 no-follow 创建并打开 gold 目标的私有父目录。

    输入参数：
        cache_root：允许从该层开始创建目录的 evaluator 私有缓存根。
        manifest_id：严格 manifest 中的安全单路径分量。
        runtime_relative_path：严格 manifest 中的 POSIX 相对目标路径。
    输出返回值：
        调用方拥有的目标父目录 descriptor，以及最终文件名。
    异常：
        GoldIntegrityError：路径链包含 symlink、非目录、不安全权限，
            或既有目标不是私有单链接普通文件。
        GoldFetchError：安全路径链无法创建或打开。
    """

    _validate_safe_relative_path(runtime_relative_path)
    if _MANIFEST_ID_PATTERN.fullmatch(manifest_id) is None:
        raise GoldManifestError
    root = Path(os.path.abspath(os.fspath(cache_root)))
    root_parts = root.parts[1:]
    if not root_parts:
        raise GoldIntegrityError
    runtime_parts = tuple(runtime_relative_path.split("/"))
    directory_parts = (*root_parts, manifest_id, *runtime_parts[:-1])
    private_depth = len(root_parts)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_descriptor = os.open(root.anchor, directory_flags)
    except OSError:
        raise GoldFetchError from None

    try:
        for depth, part in enumerate(directory_parts, start=1):
            created = False
            try:
                child_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
            except FileNotFoundError:
                if depth < private_depth:
                    raise GoldFetchError from None
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory_descriptor)
                    created = True
                    child_descriptor = os.open(
                        part,
                        directory_flags,
                        dir_fd=directory_descriptor,
                    )
                except FileExistsError:
                    try:
                        child_descriptor = os.open(
                            part,
                            directory_flags,
                            dir_fd=directory_descriptor,
                        )
                    except OSError as error:
                        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                            raise GoldIntegrityError from None
                        raise GoldFetchError from None
                except OSError as error:
                    if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise GoldIntegrityError from None
                    raise GoldFetchError from None
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise GoldIntegrityError from None
                raise GoldFetchError from None

            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
            try:
                if created:
                    os.fchmod(directory_descriptor, 0o700)
                metadata = os.fstat(directory_descriptor)
            except OSError:
                raise GoldFetchError from None
            if not stat.S_ISDIR(metadata.st_mode):
                raise GoldIntegrityError
            if depth >= private_depth and metadata.st_mode & 0o077:
                raise GoldIntegrityError

        _validate_existing_fetch_target(
            directory_descriptor,
            runtime_parts[-1],
        )
        return directory_descriptor, runtime_parts[-1]
    except Exception:
        try:
            os.close(directory_descriptor)
        except OSError:
            pass
        raise


def _validate_existing_fetch_target(
    parent_descriptor: int,
    target_name: str,
) -> None:
    """在联网前以 no-follow 元数据检查既有目标文件。

    输入参数：
        parent_descriptor：已经固定身份的私有父目录 descriptor。
        target_name：安全的单路径文件名。
    输出返回值：
        无；目标不存在或为 0600 范围内的单链接普通文件时返回。
    异常：
        GoldIntegrityError：目标是 symlink、目录、hardlink 或权限开放。
        GoldFetchError：除不存在外的元数据读取失败。
    """

    try:
        metadata = os.stat(
            target_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    except OSError:
        raise GoldFetchError from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o077
    ):
        raise GoldIntegrityError


def _create_private_temporary_file(parent_descriptor: int) -> tuple[int, str]:
    """在目标目录内创建 0600、nofollow 的唯一临时文件。

    输入参数：
        parent_descriptor：已通过私有路径门禁的目标父目录 descriptor。
    输出返回值：
        调用方拥有的临时文件 descriptor 与同目录文件名。
    异常：
        GoldFetchError：有限次唯一名尝试后仍无法安全创建文件。
    """

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _ in range(16):
        name = f".gold-download-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        except OSError:
            raise GoldFetchError from None
        try:
            os.fchmod(descriptor, 0o600)
            return descriptor, name
        except OSError:
            try:
                os.close(descriptor)
            except OSError:
                pass
            _unlink_at_best_effort(parent_descriptor, name)
            raise GoldFetchError from None
    raise GoldFetchError


def _stream_verified_response(
    response: Any,
    *,
    destination_descriptor: int,
    expected_size: int,
    expected_sha256: str,
) -> None:
    """以 ``expected_size + 1`` 上限流式写入并核对下载身份。

    输入参数：
        response：HTTP opener 返回的 context-managed 二进制响应。
        destination_descriptor：同目录 0600 临时文件 descriptor。
        expected_size：manifest 固定的精确字节数。
        expected_sha256：manifest 固定的 SHA-256 小写十六进制摘要。
    输出返回值：
        无；成功时临时文件包含且仅包含完整匹配的 gold 字节。
    异常：
        GoldFetchError：HTTP 状态或响应读取接口无效。
        GoldIntegrityError：响应过长、过短或摘要不匹配。
    """

    try:
        status_code = response.getcode()
    except Exception:
        raise GoldFetchError from None
    if status_code != 200:
        raise GoldFetchError

    digest = hashlib.sha256()
    byte_count = 0
    while True:
        read_limit = min(1024 * 1024, expected_size - byte_count + 1)
        try:
            chunk = response.read(read_limit)
        except Exception:
            raise GoldFetchError from None
        if not isinstance(chunk, bytes):
            raise GoldFetchError
        if not chunk:
            break
        byte_count += len(chunk)
        if byte_count > expected_size:
            raise GoldIntegrityError
        digest.update(chunk)
        _write_all(destination_descriptor, chunk)
    if byte_count != expected_size or digest.hexdigest() != expected_sha256:
        raise GoldIntegrityError


def _write_all(descriptor: int, content: bytes) -> None:
    """处理短写，将一个已校验边界内的 chunk 完整写入 descriptor。

    输入参数：
        descriptor：目标临时文件 descriptor。
        content：本轮 HTTP 流读取到的有限字节块。
    输出返回值：
        无；所有字节成功写入后返回。
    异常：
        GoldFetchError：底层写入失败或未取得进展。
    """

    view = memoryview(content)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError:
            raise GoldFetchError from None
        if written <= 0:
            raise GoldFetchError
        view = view[written:]


def _unlink_at_best_effort(parent_descriptor: int, name: str) -> None:
    """在固定父目录内尽力清理临时或失败发布的文件。

    输入参数：
        parent_descriptor：目标父目录 descriptor。
        name：安全的单路径文件名。
    输出返回值：
        无；清理失败被吞掉，以保留最初的固定公开异常。
    """

    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except OSError:
        pass


def _open_regular_cache_file(
    cache_root: Path,
    *,
    manifest_id: str,
    runtime_relative_path: str,
) -> BinaryIO:
    """逐级使用 no-follow descriptor 打开私有缓存普通文件。

    输入参数：
        cache_root：resolver 配置的离线缓存根。
        manifest_id：已校验、只含单路径分量的 manifest 身份。
        runtime_relative_path：已校验的 POSIX 缓存相对路径。
    输出返回值：
        基于已验证 descriptor 的二进制读流；调用方负责关闭。
    异常：
        GoldUnavailableError：目标不存在。
        GoldIntegrityError：目标是 symlink、非普通文件、存在多个
            hard link，或权限向 group/other 开放。
        GoldReadError：其他打开或元数据读取失败。
    """

    root = Path(os.path.abspath(os.fspath(cache_root)))
    if not root.is_absolute():
        raise GoldReadError
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_descriptor = os.open(root.anchor, directory_flags)
    except OSError:
        raise GoldReadError from None

    root_parts = root.parts[1:]
    relative_parts = tuple(runtime_relative_path.split("/"))
    directory_parts = (*root_parts, manifest_id, *relative_parts[:-1])
    private_depth = len(root_parts)
    try:
        for depth, part in enumerate(directory_parts, start=1):
            try:
                child_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
            except FileNotFoundError:
                raise GoldUnavailableError from None
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise GoldIntegrityError from None
                raise GoldReadError from None
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
            try:
                metadata = os.fstat(directory_descriptor)
            except OSError:
                raise GoldReadError from None
            if not stat.S_ISDIR(metadata.st_mode):
                raise GoldIntegrityError
            if depth >= private_depth and metadata.st_mode & 0o077:
                raise GoldIntegrityError

        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            descriptor = os.open(
                relative_parts[-1],
                flags,
                dir_fd=directory_descriptor,
            )
        except FileNotFoundError:
            raise GoldUnavailableError from None
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise GoldIntegrityError from None
            raise GoldReadError from None
    finally:
        os.close(directory_descriptor)

    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o077
        ):
            raise GoldIntegrityError
        return os.fdopen(descriptor, "rb", closefd=True)
    except GoldAssetError:
        os.close(descriptor)
        raise
    except OSError:
        os.close(descriptor)
        raise GoldReadError from None


def load_gold_asset_manifest(
    path: Path,
) -> GoldAssetManifest | DerivedGoldAssetManifest:
    """读取一个 pinned gold manifest 并投影为不可变类型。

    输入参数：
        path：待读取的 UTF-8 JSON manifest 文件。
    输出返回值：
        不可变 ``GoldAssetManifest``；条目保留固定来源、字节
        身份、许可和 provenance。
    """

    return load_gold_asset_manifest_bytes(_read_manifest_bytes_nofollow(path))


def load_gold_asset_manifest_bytes(
    payload: bytes,
) -> GoldAssetManifest | DerivedGoldAssetManifest:
    """从同一份已捕获字节严格解析 gold manifest。

    输入参数：payload 为上限 1 MiB 的完整 UTF-8 JSON 字节；
        调用方可把它同时用于摘要与语义校验，避免重新
        打开路径引入 cross-open ABA。
    输出返回：严格 v1 ``GoldAssetManifest`` 或唯一注册的
        v2 ``DerivedGoldAssetManifest``。
    异常：GoldManifestError：输入不是精确 ``bytes``、超限、
        非严格 UTF-8/JSON，或 schema/语义不闭合。
    """

    if type(payload) is not bytes or len(payload) > _MAX_MANIFEST_BYTES:
        raise GoldManifestError
    try:
        raw: dict[str, Any] = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_non_standard_json_constant,
        )
    except GoldManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, TypeError):
        raise GoldManifestError from None
    try:
        if not isinstance(raw, dict):
            raise GoldManifestError
        if raw.get("schema_version") == 2:
            return _parse_derived_manifest(raw)
        return _parse_manifest(raw)
    except GoldManifestError:
        raise
    except (KeyError, RecursionError, TypeError, ValueError, UnicodeError):
        raise GoldManifestError from None


def _read_manifest_bytes_nofollow(path: Path) -> bytes:
    """逐级拒绝 symlink 并有界读取普通 manifest 文件。

    输入参数：
        path：待读取的 manifest 文件路径；可为相对路径。
    输出返回值：
        最多 1 MiB 的完整原始字节；UTF-8/JSON 由同一公开
        bytes loader 统一校验。
    异常：
        GoldManifestError：路径链包含 symlink/非目录，目标不是单链接
            普通文件，内容超限，或无法严格解码。
    """

    absolute = Path(os.path.abspath(os.fspath(path)))
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        directory_descriptor = os.open(absolute.anchor, directory_flags)
    except OSError:
        raise GoldManifestError from None
    try:
        for part in absolute.parts[1:-1]:
            try:
                child_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                raise GoldManifestError from None
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
            try:
                if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                    raise GoldManifestError
            except OSError:
                raise GoldManifestError from None

        file_flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            file_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(
                absolute.name,
                file_flags,
                dir_fd=directory_descriptor,
            )
        except OSError:
            raise GoldManifestError from None
    finally:
        os.close(directory_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > _MAX_MANIFEST_BYTES
        ):
            raise GoldManifestError
        file = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        with file:
            data = file.read(_MAX_MANIFEST_BYTES + 1)
        if len(data) > _MAX_MANIFEST_BYTES:
            raise GoldManifestError
        return data
    except GoldManifestError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, UnicodeError):
        if descriptor >= 0:
            os.close(descriptor)
        raise GoldManifestError from None


def _parse_manifest(raw: object) -> GoldAssetManifest:
    """校验顶层 gold manifest 与条目闭集不变量。

    输入参数：
        raw：已完成严格 JSON 解码、但尚未信任的顶层值。
    输出返回值：
        schema、资源上限、逻辑键与 locator 闭集均合法的不可变
        ``GoldAssetManifest``。
    异常：
        GoldManifestError：任一顶层或跨条目不变量无效。
    """

    _require_exact_keys(
        raw,
        {"schema_version", "manifest_id", "distribution_policy", "entries"},
    )
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        raise GoldManifestError
    manifest_id = raw["manifest_id"]
    if (
        not isinstance(manifest_id, str)
        or _MANIFEST_ID_PATTERN.fullmatch(manifest_id) is None
    ):
        raise GoldManifestError
    distribution_policy = raw["distribution_policy"]
    if distribution_policy != "download_only":
        raise GoldManifestError
    raw_entries = raw["entries"]
    if (
        not isinstance(raw_entries, list)
        or not raw_entries
        or len(raw_entries) > _MAX_ENTRY_COUNT
    ):
        raise GoldManifestError

    entries = tuple(_parse_entry(item) for item in raw_entries)
    if sum(entry.size for entry in entries) > _MAX_TOTAL_BYTES:
        raise GoldManifestError
    logical_keys = [entry.logical_key for entry in entries]
    source_locators = [
        (
            entry.source_locator.provider,
            entry.source_locator.repository,
            entry.source_locator.revision,
            entry.source_locator.path,
        )
        for entry in entries
    ]
    runtime_locators = [entry.runtime_locator.value for entry in entries]
    if (
        len(logical_keys) != len(set(logical_keys))
        or len(source_locators) != len(set(source_locators))
        or len(runtime_locators) != len(set(runtime_locators))
        or logical_keys != sorted(logical_keys)
        or any(entry.license.distribution != distribution_policy for entry in entries)
    ):
        raise GoldManifestError
    return GoldAssetManifest(
        manifest_id=manifest_id,
        distribution_policy=distribution_policy,
        entries=entries,
    )


def _parse_derived_manifest(raw: object) -> DerivedGoldAssetManifest:
    """严格加载唯一注册的 Settings-001 ``derived_from_input`` v2 合同。

    输入参数：已经拒绝重复 key 与非标准常量的 JSON object。
    输出返回值：逐字段等于固定正式 manifest 的不可变 v2 类型。
    异常：GoldManifestError：未知字段、字段缺失或任一身份发生漂移。

    该分支不复用也不放宽 v1 parser；当前只注册经真实 fixture 验证的
    Settings-001 单项闭集，因此先做完整 object 等值比较再投影类型。
    """

    expected = _settings001_derived_manifest_document()
    _validate_derived_manifest_value(raw, expected)
    if not isinstance(raw, dict):
        raise GoldManifestError
    source = raw["derived_from_input"]
    license_contract = raw["license"]
    source_input = source["input"]
    derivation = raw["derivation"]
    toolchain = derivation["toolchain"]
    output = raw["entries"][0]
    provenance = output["provenance"]
    return DerivedGoldAssetManifest(
        schema_version=2,
        manifest_id=raw["manifest_id"],
        distribution_policy=raw["distribution_policy"],
        license=DerivedGoldLicense(
            status=license_contract["status"],
            spdx_expression=license_contract["spdx_expression"],
            evidence_ref=license_contract["evidence_ref"],
            basis=license_contract["basis"],
            distribution=license_contract["distribution"],
        ),
        asset_manifest=source["asset_manifest"],
        asset_manifest_sha256=source["asset_manifest_sha256"],
        asset_set_id=source["asset_set_id"],
        source_input=DerivedGoldSourceInput(
            path=source_input["path"],
            size=source_input["size"],
            sha256=source_input["sha256"],
            media_type=source_input["media_type"],
        ),
        derivation=DerivedGoldDerivation(
            protocol_id=derivation["protocol_id"],
            stream_selector=derivation["stream_selector"],
            timestamp_field=derivation["timestamp_field"],
            frame_order=derivation["frame_order"],
            index_origin=derivation["index_origin"],
            timestamp_decimal_places=derivation["timestamp_decimal_places"],
            requested_pts=derivation["requested_pts"],
            selected_frame_index=derivation["selected_frame_index"],
            selected_pts=derivation["selected_pts"],
            previous_pts=derivation["previous_pts"],
            source_frame_count=derivation["source_frame_count"],
            ffmpeg_version=toolchain["ffmpeg_version"],
            ffprobe_version=toolchain["ffprobe_version"],
            protocol_whitelist=toolchain["protocol_whitelist"],
            threads=toolchain["threads"],
            software_only=toolchain["software_only"],
        ),
        entries=(
            DerivedGoldAssetEntry(
                logical_key=output["logical_key"],
                runtime_locator=GoldRuntimeLocator(
                    kind=output["runtime_locator"]["kind"],
                    value=output["runtime_locator"]["value"],
                ),
                size=output["size"],
                sha256=output["sha256"],
                decoded_rgb_sha256=output["decoded_rgb_sha256"],
                width=output["width"],
                height=output["height"],
                media_type=output["media_type"],
                provenance=GoldProvenance(
                    source_benchmark=provenance["source_benchmark"],
                    source_task_id=provenance["source_task_id"],
                    source_evaluator_id=provenance["source_evaluator_id"],
                    expected_index=provenance["expected_index"],
                    source_contract_sha256=provenance["source_contract_sha256"],
                    evidence_ref=provenance["evidence_ref"],
                ),
            ),
        ),
    )


def _validate_derived_manifest_value(value: object, expected: object) -> None:
    """逐层执行字段闭集、精确 JSON 类型与固定值校验。

    输入参数：value 为不可信 v2 JSON 节点；expected 为可信固定节点。
    输出返回值：结构、类型和值全部精确一致时返回 ``None``。
    异常：GoldManifestError：未知/缺失字段、list 长度、bool/int/float/string
        混淆或固定值漂移。该比较不使用 Python 宽松数值等值语义。
    """

    if isinstance(expected, dict):
        if not isinstance(value, dict) or set(value) != set(expected):
            raise GoldManifestError
        for key, expected_child in expected.items():
            _validate_derived_manifest_value(value[key], expected_child)
        return
    if isinstance(expected, list):
        if not isinstance(value, list) or len(value) != len(expected):
            raise GoldManifestError
        for child, expected_child in zip(value, expected, strict=True):
            _validate_derived_manifest_value(child, expected_child)
        return
    if type(value) is not type(expected) or value != expected:
        raise GoldManifestError


def validate_derived_gold_asset_manifest(
    manifest: object,
) -> DerivedGoldAssetManifest:
    """将不可信 dataclass 实例重建为严格 v2 JSON 值并验证。

    输入参数：manifest 可能是调用方手工构造或通过
        ``dataclasses.replace`` 漂移的任意对象。
    输出返回值：仅当顶层、全部嵌套 dataclass、tuple
        闭集及每个原语字段都与唯一 canonical Settings-001
        v2 合同精确同类且同值时，返回原不可变实例。
    异常：GoldManifestError：对象类型、嵌套类型、字段值或
        Python 宽松数值等值中的任一项不精确；不回显私密值。

    该边界先用 ``type(...) is ...`` 固定容器和数据类型，
    再投影为 JSON 原语并复用 loader 的逐层 exact-type
    validator；因此 ``True == 1`` 和 ``2216858.0 == 2216858``
    不能绕过 canonical 合同。
    """

    if type(manifest) is not DerivedGoldAssetManifest:
        raise GoldManifestError
    if (
        type(manifest.license) is not DerivedGoldLicense
        or type(manifest.source_input) is not DerivedGoldSourceInput
        or type(manifest.derivation) is not DerivedGoldDerivation
        or type(manifest.entries) is not tuple
        or len(manifest.entries) != 1
    ):
        raise GoldManifestError
    output = manifest.entries[0]
    if (
        type(output) is not DerivedGoldAssetEntry
        or type(output.runtime_locator) is not GoldRuntimeLocator
        or type(output.provenance) is not GoldProvenance
    ):
        raise GoldManifestError

    license_contract = manifest.license
    source_input = manifest.source_input
    derivation = manifest.derivation
    provenance = output.provenance
    document = {
        "schema_version": manifest.schema_version,
        "manifest_id": manifest.manifest_id,
        "manifest_role": "gold",
        "derivation_kind": "derived_from_input",
        "distribution_policy": manifest.distribution_policy,
        "license": {
            "status": license_contract.status,
            "spdx_expression": license_contract.spdx_expression,
            "evidence_ref": license_contract.evidence_ref,
            "basis": license_contract.basis,
            "distribution": license_contract.distribution,
        },
        "derived_from_input": {
            "asset_manifest": manifest.asset_manifest,
            "asset_manifest_sha256": manifest.asset_manifest_sha256,
            "asset_set_id": manifest.asset_set_id,
            "input": {
                "path": source_input.path,
                "size": source_input.size,
                "sha256": source_input.sha256,
                "media_type": source_input.media_type,
            },
        },
        "derivation": {
            "protocol_id": derivation.protocol_id,
            "stream_selector": derivation.stream_selector,
            "timestamp_field": derivation.timestamp_field,
            "frame_order": derivation.frame_order,
            "index_origin": derivation.index_origin,
            "timestamp_decimal_places": derivation.timestamp_decimal_places,
            "requested_pts": derivation.requested_pts,
            "selected_frame_index": derivation.selected_frame_index,
            "selected_pts": derivation.selected_pts,
            "previous_pts": derivation.previous_pts,
            "source_frame_count": derivation.source_frame_count,
            "toolchain": {
                "ffmpeg_version": derivation.ffmpeg_version,
                "ffprobe_version": derivation.ffprobe_version,
                "protocol_whitelist": derivation.protocol_whitelist,
                "threads": derivation.threads,
                "software_only": derivation.software_only,
            },
        },
        "entries": [
            {
                "logical_key": output.logical_key,
                "runtime_locator": {
                    "kind": output.runtime_locator.kind,
                    "value": output.runtime_locator.value,
                },
                "size": output.size,
                "sha256": output.sha256,
                "decoded_rgb_sha256": output.decoded_rgb_sha256,
                "width": output.width,
                "height": output.height,
                "media_type": output.media_type,
                "provenance": {
                    "source_benchmark": provenance.source_benchmark,
                    "source_task_id": provenance.source_task_id,
                    "source_evaluator_id": provenance.source_evaluator_id,
                    "expected_index": provenance.expected_index,
                    "source_contract_sha256": provenance.source_contract_sha256,
                    "evidence_ref": provenance.evidence_ref,
                },
            }
        ],
    }
    _validate_derived_manifest_value(
        document,
        _settings001_derived_manifest_document(),
    )
    return manifest


def validate_gold_asset_manifest(manifest: object) -> GoldAssetManifest:
    """将不可信 v1 dataclass 实例重建为严格 JSON 值并验证。

    输入参数：manifest 可能是调用方手工构造、继承或通过
        ``dataclasses.replace`` 漂移的任意对象。
    输出返回：仅当顶层与全部嵌套 dataclass/container/
        primitive 类型精确，且完整满足 v1 loader 语义时返回
        原不可变实例。
    异常：GoldManifestError：对象类型、容器闭集、字段类型或
        v1 逻辑键/locator/字节/许可/provenance 任一不合法。

    该边界先使用 ``type(...) is ...`` 拒绝 subclass 及
    ``True == 1`` 等 Python 宽松等值，再复用唯一 v1 parser，
    使 loader、resolver、fetch 和身份比较共享同一语义。
    """

    if type(manifest) is not GoldAssetManifest:
        raise GoldManifestError
    if (
        type(manifest.manifest_id) is not str
        or type(manifest.distribution_policy) is not str
        or type(manifest.entries) is not tuple
        or not manifest.entries
        or len(manifest.entries) > _MAX_ENTRY_COUNT
    ):
        raise GoldManifestError

    raw_entries: list[dict[str, object]] = []
    for entry in manifest.entries:
        if type(entry) is not GoldAssetEntry:
            raise GoldManifestError
        source = entry.source_locator
        runtime = entry.runtime_locator
        license_contract = entry.license
        provenance = entry.provenance
        if (
            type(source) is not GoldSourceLocator
            or type(runtime) is not GoldRuntimeLocator
            or type(license_contract) is not GoldLicense
            or type(provenance) is not GoldProvenance
        ):
            raise GoldManifestError
        if (
            type(entry.logical_key) is not str
            or type(entry.size) is not int
            or type(entry.sha256) is not str
            or type(entry.media_type) is not str
            or type(source.provider) is not str
            or type(source.repository) is not str
            or type(source.revision) is not str
            or type(source.path) is not str
            or type(runtime.kind) is not str
            or type(runtime.value) is not str
            or type(license_contract.status) is not str
            or (
                license_contract.spdx_expression is not None
                and type(license_contract.spdx_expression) is not str
            )
            or type(license_contract.evidence_ref) is not str
            or type(license_contract.distribution) is not str
            or type(provenance.source_benchmark) is not str
            or type(provenance.source_task_id) is not str
            or type(provenance.source_evaluator_id) is not str
            or type(provenance.expected_index) is not int
            or type(provenance.source_contract_sha256) is not str
            or type(provenance.evidence_ref) is not str
        ):
            raise GoldManifestError
        raw_entries.append(
            {
                "logical_key": entry.logical_key,
                "source_locator": {
                    "provider": source.provider,
                    "repository": source.repository,
                    "revision": source.revision,
                    "path": source.path,
                },
                "runtime_locator": {
                    "kind": runtime.kind,
                    "value": runtime.value,
                },
                "size": entry.size,
                "sha256": entry.sha256,
                "media_type": entry.media_type,
                "license": {
                    "status": license_contract.status,
                    "spdx_expression": license_contract.spdx_expression,
                    "evidence_ref": license_contract.evidence_ref,
                    "distribution": license_contract.distribution,
                },
                "provenance": {
                    "source_benchmark": provenance.source_benchmark,
                    "source_task_id": provenance.source_task_id,
                    "source_evaluator_id": provenance.source_evaluator_id,
                    "expected_index": provenance.expected_index,
                    "source_contract_sha256": provenance.source_contract_sha256,
                    "evidence_ref": provenance.evidence_ref,
                },
            }
        )

    _parse_manifest(
        {
            "schema_version": 1,
            "manifest_id": manifest.manifest_id,
            "distribution_policy": manifest.distribution_policy,
            "entries": raw_entries,
        }
    )
    return manifest


def _settings001_derived_manifest_document() -> dict[str, Any]:
    """返回 Settings-001 v2 derived gold 的唯一可信 JSON object。

    输入参数：无。
    输出返回值：包含输入 manifest、源视频、首个 PTS≥8 秒帧、固定
        ffmpeg/ffprobe 8.1.1 软件解码协议及 PNG/RGB 输出身份的新字典。
    """

    return {
        "schema_version": 2,
        "manifest_id": _SETTINGS001_GOLD_MANIFEST_ID,
        "manifest_role": "gold",
        "derivation_kind": "derived_from_input",
        "distribution_policy": "private_materialization_only",
        "license": {
            "status": "verified",
            "spdx_expression": "Apache-2.0",
            "evidence_ref": (
                "https://huggingface.co/datasets/xlangai/ubuntu_osworld_file_cache"
            ),
            "basis": "derived_from_source_input",
            "distribution": "private_materialization_only",
        },
        "derived_from_input": {
            "asset_manifest": (
                "benchmark/assets/manifests/Operation-FileOperate-Settings-001.json"
            ),
            "asset_manifest_sha256": (
                "8de1a8fa801bc0aa26cca86033a6f8370f1efe011369229ad821f8240922f6cf"
            ),
            "asset_set_id": _SETTINGS001_TASK_ID,
            "input": {
                "path": "landscape.mp4",
                "size": 9_362_831,
                "sha256": (
                    "d39162e1d519e978261ad4ae824d4446f511936c80d5ce2e085cf617eae04c35"
                ),
                "media_type": "video/mp4",
            },
        },
        "derivation": {
            "protocol_id": "paraguibench.gold.first-video-frame-pts-gte.v1",
            "stream_selector": "v:0",
            "timestamp_field": "best_effort_timestamp_time",
            "frame_order": "ffprobe_emitted_display_order",
            "index_origin": 0,
            "timestamp_decimal_places": 6,
            "requested_pts": "8.000000",
            "selected_frame_index": 240,
            "selected_pts": "8.008000",
            "previous_pts": "7.974633",
            "source_frame_count": 419,
            "toolchain": {
                "ffmpeg_version": "8.1.1",
                "ffprobe_version": "8.1.1",
                "protocol_whitelist": "pipe",
                "threads": 1,
                "software_only": True,
            },
        },
        "entries": [
            {
                "logical_key": _SETTINGS001_GOLD_LOGICAL_KEY,
                "runtime_locator": {
                    "kind": "cache-relative-path",
                    "value": "landscape.png",
                },
                "size": 2_216_858,
                "sha256": (
                    "b383ffccf666a2dfe83100b392e1d4e2dbb744e1034b2e200be72621cbe52fc3"
                ),
                "decoded_rgb_sha256": (
                    "70138e557d112dfb79e890c42311a5037ee99b014b2facd13b4e2a78a631cd7c"
                ),
                "width": 1920,
                "height": 1080,
                "media_type": "image/png",
                "provenance": {
                    "source_benchmark": "OSWorld",
                    "source_task_id": "47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5",
                    "source_evaluator_id": "9b5220d5-f1f0-4db9-902d-ad41aae4d775",
                    "expected_index": 0,
                    "source_contract_sha256": (
                        "5f3ebcf626c74ac25b31c54c186166064c8a62edec23a87efbf1655a854ff66d"
                    ),
                    "evidence_ref": (
                        "osworld:evaluator:"
                        "9b5220d5-f1f0-4db9-902d-ad41aae4d775:expected:0"
                    ),
                },
            }
        ],
    }


def _parse_entry(raw: object) -> GoldAssetEntry:
    """校验一个 gold entry 的字节身份与跨字段追溯关系。

    输入参数：
        raw：``entries`` 中尚未信任的候选 object。
    输出返回值：
        logical key、字节元数据、许可和 provenance 一致的
        ``GoldAssetEntry``。
    异常：
        GoldManifestError：字段、类型、上限、媒体或追溯绑定无效。
    """

    _require_exact_keys(
        raw,
        {
            "logical_key",
            "source_locator",
            "runtime_locator",
            "size",
            "sha256",
            "media_type",
            "license",
            "provenance",
        },
    )
    logical_key = raw["logical_key"]
    match = (
        _LOGICAL_KEY_PATTERN.fullmatch(logical_key)
        if isinstance(logical_key, str)
        else None
    )
    if match is None:
        raise GoldManifestError
    size = raw["size"]
    sha256 = raw["sha256"]
    media_type = raw["media_type"]
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 < size <= _MAX_SINGLE_ENTRY_BYTES
        or not isinstance(sha256, str)
        or _SHA256_PATTERN.fullmatch(sha256) is None
        or media_type not in _ALLOWED_MEDIA_TYPES
    ):
        raise GoldManifestError

    source_locator = _parse_source_locator(raw["source_locator"])
    runtime_locator = _parse_runtime_locator(raw["runtime_locator"])
    license_contract = _parse_license(raw["license"])
    provenance = _parse_provenance(raw["provenance"])
    logical_task_id, logical_index_text = match.groups()
    if (
        provenance.source_task_id != logical_task_id
        or provenance.expected_index != int(logical_index_text)
        or provenance.source_task_id not in source_locator.path.split("/")
    ):
        raise GoldManifestError
    return GoldAssetEntry(
        logical_key=logical_key,
        source_locator=source_locator,
        runtime_locator=runtime_locator,
        size=size,
        sha256=sha256,
        media_type=media_type,
        license=license_contract,
        provenance=provenance,
    )


def _parse_runtime_locator(raw: object) -> GoldRuntimeLocator:
    """校验并构造 evaluator 私有缓存 locator。

    输入参数：
        raw：manifest entry 中尚未信任的 ``runtime_locator``。
    输出返回值：
        仅保留 schema 定义字段的 ``GoldRuntimeLocator``。
    异常：
        GoldManifestError：字段闭集不精确。
    """

    _require_exact_keys(raw, {"kind", "value"})
    kind = raw["kind"]
    value = raw["value"]
    if kind != "cache-relative-path":
        raise GoldManifestError
    _validate_safe_relative_path(value)
    return GoldRuntimeLocator(kind=kind, value=value)


def _parse_license(raw: object) -> GoldLicense:
    """校验并构造数据资产许可证据 contract。

    输入参数：
        raw：manifest entry 中尚未信任的 ``license`` 对象。
    输出返回值：
        仅保留 schema 定义字段的 ``GoldLicense``。
    异常：
        GoldManifestError：字段闭集不精确。
    """

    _require_exact_keys(
        raw,
        {"status", "spdx_expression", "evidence_ref", "distribution"},
    )
    status = raw["status"]
    spdx_expression = raw["spdx_expression"]
    evidence_ref = raw["evidence_ref"]
    distribution = raw["distribution"]
    if status not in {"verified", "author-provided", "unverified"}:
        raise GoldManifestError
    if distribution != "download_only":
        raise GoldManifestError
    if status == "verified":
        if (
            not isinstance(spdx_expression, str)
            or _SPDX_PATTERN.fullmatch(spdx_expression) is None
            or not _is_public_https_evidence_ref(evidence_ref)
        ):
            raise GoldManifestError
    elif spdx_expression is not None:
        raise GoldManifestError
    if not isinstance(evidence_ref, str) or not evidence_ref:
        raise GoldManifestError
    return GoldLicense(
        status=status,
        spdx_expression=spdx_expression,
        evidence_ref=evidence_ref,
        distribution=distribution,
    )


def _parse_provenance(raw: object) -> GoldProvenance:
    """校验并构造上游 evaluator 追溯 contract。

    输入参数：
        raw：manifest entry 中尚未信任的 ``provenance`` 对象。
    输出返回值：
        仅保留 schema 定义字段的 ``GoldProvenance``。
    异常：
        GoldManifestError：字段闭集不精确。
    """

    _require_exact_keys(
        raw,
        {
            "source_benchmark",
            "source_task_id",
            "source_evaluator_id",
            "expected_index",
            "source_contract_sha256",
            "evidence_ref",
        },
    )
    source_benchmark = raw["source_benchmark"]
    source_task_id = raw["source_task_id"]
    source_evaluator_id = raw["source_evaluator_id"]
    expected_index = raw["expected_index"]
    source_contract_sha256 = raw["source_contract_sha256"]
    evidence_ref = raw["evidence_ref"]
    reference_match = (
        _PROVENANCE_REF_PATTERN.fullmatch(evidence_ref)
        if isinstance(evidence_ref, str)
        else None
    )
    if (
        source_benchmark != "OSWorld"
        or not isinstance(source_task_id, str)
        or _UUID_PATTERN.fullmatch(source_task_id) is None
        or not isinstance(source_evaluator_id, str)
        or _UUID_PATTERN.fullmatch(source_evaluator_id) is None
        or not isinstance(expected_index, int)
        or isinstance(expected_index, bool)
        or not 0 <= expected_index < _MAX_ENTRY_COUNT
        or not isinstance(source_contract_sha256, str)
        or _SHA256_PATTERN.fullmatch(source_contract_sha256) is None
        or reference_match is None
        or reference_match.group(1) != source_evaluator_id
        or int(reference_match.group(2)) != expected_index
    ):
        raise GoldManifestError
    return GoldProvenance(
        source_benchmark=source_benchmark,
        source_task_id=source_task_id,
        source_evaluator_id=source_evaluator_id,
        expected_index=expected_index,
        source_contract_sha256=source_contract_sha256,
        evidence_ref=evidence_ref,
    )


def _is_public_https_evidence_ref(value: object) -> bool:
    """验证许可证据是不含凭据或查询值的公开 HTTPS URL。

    输入参数：
        value：待验证的 license ``evidence_ref``。
    输出返回值：
        仅当 scheme 为 HTTPS、host 存在，且无 userinfo、query
        或 fragment 时返回 ``True``。
    """

    if not isinstance(value, str) or len(value) > 2_048:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and port in {None, 443}
    )


def _parse_source_locator(raw: object) -> GoldSourceLocator:
    """校验并构造不含可变版本或 URL 注入的来源 locator。

    输入参数：
        raw：manifest entry 中尚未信任的 ``source_locator``。
    输出返回值：
        provider/repository/commit/path 均已通过闭集验证的
        ``GoldSourceLocator``。
    异常：
        GoldManifestError：字段、provider、repository、revision 或路径无效。
    """

    _require_exact_keys(raw, {"provider", "repository", "revision", "path"})
    provider = raw["provider"]
    repository = raw["repository"]
    revision = raw["revision"]
    source_path = raw["path"]
    if provider != "huggingface_dataset":
        raise GoldManifestError
    if (
        not isinstance(repository, str)
        or _REPOSITORY_PATTERN.fullmatch(repository) is None
    ):
        raise GoldManifestError
    if not isinstance(revision, str) or _REVISION_PATTERN.fullmatch(revision) is None:
        raise GoldManifestError
    _validate_safe_relative_path(source_path, allow_hash=True)
    return GoldSourceLocator(
        provider=provider,
        repository=repository,
        revision=revision,
        path=source_path,
    )


def _validate_safe_relative_path(
    value: object,
    *,
    allow_hash: bool = False,
) -> None:
    """验证 locator 是未编码、规范化且不穿越边界的 POSIX 路径。

    输入参数：
        value：待验证的 source 或 runtime 相对路径。
        allow_hash：仅 source locator 可设为 ``True``，允许固定数据集
            文件名中的字面 ``#``；构造 HTTP URL 时仍必须编码为
            ``%23``。runtime cache locator 继续拒绝该字符。
    输出返回值：
        无；每个分量安全、UTF-8 有界且文本为 NFC 时返回。
    异常：
        GoldManifestError：路径为绝对路径，包含穿越、编码/URL
            分隔、控制字符、反斜杠，或超过长度上限。
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8", "strict")) > 1_024
        or value.startswith("/")
        or "\\" in value
        or any(character in value for character in ("%", "?", "\x00"))
        or (not allow_hash and "#" in value)
        or any(not character.isprintable() for character in value)
        or unicodedata.normalize("NFC", value) != value
    ):
        raise GoldManifestError
    parts = value.split("/")
    if any(
        part in {"", ".", ".."} or len(part.encode("utf-8", "strict")) > 255
        for part in parts
    ):
        raise GoldManifestError


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """构造一个不允许重复 key 的 JSON object。

    输入参数：
        pairs：JSON decoder 按原始顺序交付的 key/value 序列。
    输出返回值：
        键唯一时返回新建字典。
    异常：
        GoldManifestError：任一 key 在同一 object 内重复。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GoldManifestError
        result[key] = value
    return result


def _reject_non_standard_json_constant(value: str) -> None:
    """拒绝 NaN 和 Infinity 等非标准 JSON 常量。

    输入参数：
        value：JSON decoder 识别的非标准常量文本。
    输出返回值：
        无；本函数总是失败关闭。
    异常：
        GoldManifestError：无条件抛出，且不回显原值。
    """

    del value
    raise GoldManifestError


def _require_exact_keys(raw: object, expected: set[str]) -> None:
    """验证一个 JSON object 的字段闭集与 schema 完全相同。

    输入参数：
        raw：待验证的 JSON 对象候选值。
        expected：当前 schema 层允许且必需的字段闭集。
    输出返回值：
        无；字段闭集精确相同时正常返回。
    异常：
        GoldManifestError：输入不是 object，或存在缺失/未知字段。
    """

    if not isinstance(raw, dict) or set(raw) != expected:
        raise GoldManifestError


__all__ = [
    "DerivedGoldAssetEntry",
    "DerivedGoldAssetManifest",
    "DerivedGoldDerivation",
    "DerivedGoldLicense",
    "DerivedGoldSourceInput",
    "GoldAssetEntry",
    "GoldAssetError",
    "GoldAssetManifest",
    "GoldAssetResolver",
    "GoldAvailability",
    "GoldAvailabilityStatus",
    "GoldFetchError",
    "GoldIntegrityError",
    "GoldLicense",
    "GoldLimitExceededError",
    "GoldMediaTypeError",
    "GoldProvenance",
    "GoldReadError",
    "GoldRuntimeLocator",
    "GoldSourceLocator",
    "GoldManifestError",
    "GoldUnavailableError",
    "fetch_gold_assets",
    "load_gold_asset_manifest",
    "load_gold_asset_manifest_bytes",
    "validate_derived_gold_asset_manifest",
    "validate_gold_asset_manifest",
]
