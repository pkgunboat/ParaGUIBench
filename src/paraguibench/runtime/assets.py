"""固定版本外部任务资产的清单加载与本地闭集验证。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import BinaryIO
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ASSET_SET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MEDIA_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_MANIFEST_FIELDS = frozenset(
    {"schema_version", "asset_set_id", "source", "distribution_policy", "files"}
)
_SOURCE_FIELDS = frozenset(
    {"provider", "repository", "revision", "base_path", "license_status"}
)
_FILE_FIELDS = frozenset({"path", "size", "sha256", "media_type"})
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class AssetManifestError(ValueError):
    """表示外部资产清单结构无效或包含不安全路径。"""


class AssetFetchError(RuntimeError):
    """表示外部资产无法通过固定大小与摘要门禁。"""


class TaskAssetResolutionError(AssetManifestError):
    """表示 canonical task 的资产声明无法安全解析。"""


class UnmigratedTaskAssetsError(TaskAssetResolutionError):
    """表示任务仍依赖尚未迁移的非空 legacy 资产引用。"""


class TaskAssetMode(StrEnum):
    """表示 canonical task 的任务文件资产模式。

    输入参数：
        枚举值由任务资产解析器依据 canonical task 的显式声明选择。
    输出返回值：
        稳定字符串用于 CLI、doctor 与环境准备共享同一资产语义；``NONE``
        表示任务不需要外部任务文件，不代表 evaluator 或环境已经可运行。
    """

    NONE = "none"
    PINNED_DOWNLOAD_MANIFEST = "pinned_download_manifest"


@dataclass(frozen=True, slots=True)
class ResolvedTaskAssets:
    """保存统一解析后的任务文件资产契约。

    输入参数：
        mode：任务资产模式。
        manifest：固定下载资产模式对应的已校验 manifest；``NONE`` 模式为
            ``None``。
    输出返回值：
        该类型不执行 I/O；调用方通过不可变字段决定是否验证、下载或上传
        任务文件。
    """

    mode: TaskAssetMode
    manifest: AssetManifest | None

    def __post_init__(self) -> None:
        """验证资产模式与 manifest 的强类型配对不变量。

        输入参数：
            无；读取冻结 dataclass 已接收的 ``mode`` 与 ``manifest`` 字段。
        输出返回值：
            无；NONE+None 或 PINNED+AssetManifest 两种合法组合正常完成构造。
        异常：
            TypeError：mode 不是 ``TaskAssetMode``，或 manifest 类型错误。
            ValueError：mode 与 manifest 的有无相互矛盾。
        """

        if not isinstance(self.mode, TaskAssetMode):
            raise TypeError("task asset mode 类型无效")
        if self.manifest is not None and not isinstance(
            self.manifest,
            AssetManifest,
        ):
            raise TypeError("task asset manifest 类型无效")
        if self.mode is TaskAssetMode.NONE and self.manifest is not None:
            raise ValueError("NONE asset mode 必须没有 manifest")
        if (
            self.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
            and self.manifest is None
        ):
            raise ValueError("pinned asset mode 必须包含 manifest")


def resolve_task_assets(
    repo_root: Path,
    task: Mapping[str, Any],
) -> ResolvedTaskAssets:
    """把 canonical task 的资产声明解析为统一、不可回退的契约。

    输入参数：
        repo_root：canonical task 所属仓库根目录；零资产模式不访问该目录。
        task：可信 canonical task Mapping。
    输出返回值：
        缺少任务文件资产声明，或 legacy ``prepare_script_path`` 为显式空串
        时，返回 ``NONE`` 模式且不伪造空 manifest。
    异常：
        TypeError：task 不是 Mapping。
        AssetManifestError：声明不是当前切片可解释的零资产形状。
    """

    if not isinstance(task, Mapping):
        raise TypeError("task 必须是 Mapping")
    has_manifest_declaration = "asset_manifest" in task
    manifest_reference = task.get("asset_manifest")
    legacy_reference = task.get("prepare_script_path")
    has_legacy_exclude_declaration = "prepare_exclude_patterns" in task
    if has_manifest_declaration and manifest_reference == "":
        raise TaskAssetResolutionError("显式 asset_manifest 不得为空字符串")
    if (
        manifest_reference is None
        and legacy_reference in (None, "")
        and not has_legacy_exclude_declaration
    ):
        return ResolvedTaskAssets(
            mode=TaskAssetMode.NONE,
            manifest=None,
        )
    if (
        isinstance(manifest_reference, str)
        and manifest_reference
        and legacy_reference in (None, "")
        and not has_legacy_exclude_declaration
    ):
        manifest_path = _resolve_repository_file(
            repo_root,
            manifest_reference,
        )
        return ResolvedTaskAssets(
            mode=TaskAssetMode.PINNED_DOWNLOAD_MANIFEST,
            manifest=load_asset_manifest(manifest_path),
        )
    if (
        manifest_reference is None
        and isinstance(legacy_reference, str)
        and legacy_reference
    ):
        raise UnmigratedTaskAssetsError("任务仍依赖未迁移的 legacy 资产")
    raise TaskAssetResolutionError("任务资产声明字段或组合无效")


def _resolve_repository_file(repo_root: Path, relative_value: str) -> Path:
    """安全解析仓库内的普通资产 manifest 文件。

    输入参数：
        repo_root：仓库根目录。
        relative_value：canonical task 声明的 POSIX 相对路径。
    输出返回值：
        位于已解析仓库根内、路径链不含符号链接的普通文件绝对路径。
    异常：
        AssetManifestError：路径为绝对路径、发生目录穿越、经过符号链接或
            目标不是普通文件。
    """

    root = repo_root.expanduser().resolve()
    if "\\" in relative_value:
        raise AssetManifestError("asset_manifest 必须是 POSIX 相对路径")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise AssetManifestError("asset_manifest 不得指向仓库外部")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise AssetManifestError("asset_manifest 不是普通仓库文件")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise AssetManifestError("asset_manifest 不得指向仓库外部") from error
    if not resolved.is_file():
        raise AssetManifestError("asset_manifest 不是仓库内普通文件")
    return resolved


@dataclass(frozen=True)
class AssetFile:
    """描述一个固定大小、SHA-256 与可选 MIME 的外部文件。

    输入参数：
        path：资产集合根目录下的安全 POSIX 相对路径。
        size：固定提交中该文件的精确字节数。
        sha256：下载后必须匹配的小写 SHA-256。
        media_type：可选的已核验 IANA MIME；旧版 manifest 可省略。
    输出返回值：
        不可变文件合同；下载与闭集验证由公开 runtime 函数执行。
    """

    path: str
    size: int
    sha256: str
    media_type: str | None = None


@dataclass(frozen=True)
class AssetSource:
    """描述不可变 Hugging Face dataset 来源。"""

    provider: str
    repository: str
    revision: str
    base_path: str
    license_status: str


@dataclass(frozen=True)
class AssetManifest:
    """保存一个 download-only 资产集合的完整契约。"""

    asset_set_id: str
    source: AssetSource
    distribution_policy: str
    files: tuple[AssetFile, ...]


@dataclass(frozen=True)
class AssetVerification:
    """保存本地资产目录的闭集验证结果。"""

    missing: tuple[str, ...]
    size_mismatch: tuple[str, ...]
    hash_mismatch: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """判断资产目录是否完整且未被篡改。

        输入参数：
            无。
        输出返回值：
            四类问题均为空时返回 ``True``，否则返回 ``False``。
        """

        return not (
            self.missing or self.size_mismatch or self.hash_mismatch or self.unexpected
        )


def load_asset_manifest(path: Path) -> AssetManifest:
    """读取并严格验证一个 download-only 外部资产清单。

    输入参数：
        path：仓库内或受信配置目录中的 JSON manifest 路径。
    输出返回值：
        字段类型、固定 revision、相对路径、大小与摘要均验证后的不可变清单。
    异常：
        AssetManifestError：JSON 或任一契约字段不合法。
    """

    payload = read_manifest_bytes_nofollow(path)
    return load_asset_manifest_bytes(payload)


def read_manifest_bytes_nofollow(
    path: Path,
    *,
    max_bytes: int = _MAX_MANIFEST_BYTES,
) -> bytes:
    """通过 held descriptor 有界读取一份稳定的普通 JSON 快照。

    输入参数：
        path：可为相对或绝对路径；每一级祖先和最终文件都不得是 symlink。
        max_bytes：调用方允许的正数字节上限；不得超过统一 4 MiB 硬上限。
    输出返回值：
        最多 4 MiB、读取前后 inode 元数据一致的原始 bytes。
    异常：
        AssetManifestError：路径、类型、链接数、大小、读取或稳定性无效。
    """

    if (
        not isinstance(path, Path)
        or not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or not 0 < max_bytes <= _MAX_MANIFEST_BYTES
    ):
        raise AssetManifestError("外部资产清单路径无效")
    absolute = Path(os.path.abspath(os.fspath(path)))
    directory_flags = os.O_RDONLY
    for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        directory_flags |= getattr(os, name, 0)
    try:
        directory_descriptor = os.open(absolute.anchor, directory_flags)
    except OSError:
        raise AssetManifestError("无法读取外部资产清单") from None
    try:
        for part in absolute.parts[1:-1]:
            try:
                child_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
            except OSError:
                raise AssetManifestError("无法读取外部资产清单") from None
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
            try:
                if not stat.S_ISDIR(os.fstat(directory_descriptor).st_mode):
                    raise AssetManifestError("无法读取外部资产清单")
            except OSError:
                raise AssetManifestError("无法读取外部资产清单") from None

        file_flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW"):
            file_flags |= getattr(os, name, 0)
        try:
            descriptor = os.open(
                absolute.name,
                file_flags,
                dir_fd=directory_descriptor,
            )
        except OSError:
            raise AssetManifestError("无法读取外部资产清单") from None
    finally:
        os.close(directory_descriptor)

    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= max_bytes
        ):
            raise AssetManifestError("外部资产清单文件无效")
        with os.fdopen(descriptor, "rb", closefd=True) as file:
            descriptor = -1
            payload = file.read(max_bytes + 1)
            after = os.fstat(file.fileno())
        if (
            len(payload) != before.st_size
            or len(payload) > max_bytes
            or _file_snapshot_identity(after) != _file_snapshot_identity(before)
        ):
            raise AssetManifestError("外部资产清单读取不稳定")
        return payload
    except AssetManifestError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise AssetManifestError("无法读取外部资产清单") from None


def _file_snapshot_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """投影用于检测 manifest 读取期间原地修改的 inode 元数据。

    输入参数：
        metadata：同一 held descriptor 读取前或读取后的 ``fstat`` 结果。
    输出返回值：
        设备、inode、大小、链接数、mtime/ctime 纳秒值组成的稳定元组。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def load_asset_manifest_bytes(payload: bytes) -> AssetManifest:
    """从一个不可变原始字节快照严格解析 download-only 资产清单。

    输入参数：
        payload：调用方已经冻结的 UTF-8 JSON bytes；不接受字符串、
            bytearray 或预解析的可变 object。
    输出返回值：
        字段类型、固定 revision、相对路径、大小、摘要与 MIME 均验证后的
        不可变 ``AssetManifest``。
    异常：
        AssetManifestError：输入类型、大小、UTF-8、JSON 或契约字段无效。
    """

    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > _MAX_MANIFEST_BYTES
    ):
        raise AssetManifestError("外部资产清单字节无效")
    try:
        raw = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_non_standard_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise AssetManifestError("无法解析外部资产清单") from error
    return _parse_asset_manifest(raw)


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    """构造字段唯一的 JSON object。

    输入参数：
        pairs：JSON decoder 保留原顺序提供的字段名和值。
    输出返回值：
        所有字段名唯一时返回新字典。
    异常：
        AssetManifestError：任一 object 含重复字段名。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssetManifestError("资产清单包含重复 JSON 字段")
        result[key] = value
    return result


def _reject_non_standard_json_constant(value: str) -> None:
    """拒绝 JSON 标准之外的 NaN 与 Infinity token。

    输入参数：
        value：decoder 遇到的非标准常量文本。
    输出返回值：
        不返回；恒抛 ``AssetManifestError``。
    """

    del value
    raise AssetManifestError("资产清单含非标准 JSON 常量")


def _parse_asset_manifest(raw: object) -> AssetManifest:
    """把已解码 JSON 值完整投影为不可变资产合同。

    输入参数：
        raw：仅由原始 bytes loader 产生、尚未信任的 JSON 顶层值。
    输出返回值：
        通过所有顶层、来源、条目与跨条目不变量的 ``AssetManifest``。
    异常：
        AssetManifestError：任一字段、路径、来源或闭集不合法。
    """

    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise AssetManifestError("资产清单 schema_version 必须为 1")
    _reject_unknown_fields(raw, allowed=_MANIFEST_FIELDS, field_name="manifest")
    asset_set_id = raw.get("asset_set_id")
    source_raw = raw.get("source")
    distribution_policy = raw.get("distribution_policy")
    files_raw = raw.get("files")
    if (
        not isinstance(asset_set_id, str)
        or _ASSET_SET_ID_PATTERN.fullmatch(asset_set_id) is None
    ):
        raise AssetManifestError("asset_set_id 必须是安全的单层目录名")
    if distribution_policy != "download_only":
        raise AssetManifestError("外部资产必须声明 download_only")
    if not isinstance(source_raw, dict):
        raise AssetManifestError("资产清单缺少 source object")
    source = _parse_source(source_raw)
    if not isinstance(files_raw, list) or not files_raw:
        raise AssetManifestError("资产清单 files 必须是非空列表")
    files = tuple(_parse_file(item) for item in files_raw)
    file_paths = [item.path for item in files]
    if len(file_paths) != len(set(file_paths)):
        raise AssetManifestError("资产清单包含重复文件路径")
    return AssetManifest(
        asset_set_id=asset_set_id,
        source=source,
        distribution_policy=distribution_policy,
        files=files,
    )


def verify_asset_directory(
    manifest: AssetManifest,
    root: Path,
) -> AssetVerification:
    """验证本地目录恰好包含清单声明且摘要一致的文件。

    输入参数：
        manifest：已通过 ``load_asset_manifest`` 校验的资产契约。
        root：本次 task 独占或只读挂载的资产目录。
    输出返回值：
        分别列出缺失、大小不符、摘要不符和多余相对路径的验证结果；
        结果不会包含文件正文。
    """

    declared = {item.path: item for item in manifest.files}
    present = _present_relative_files(root)
    missing: list[str] = []
    size_mismatch: list[str] = []
    hash_mismatch: list[str] = []

    for relative_path, asset in declared.items():
        candidate = root / relative_path
        if relative_path not in present or candidate.is_symlink():
            missing.append(relative_path)
            continue
        if candidate.stat().st_size != asset.size:
            size_mismatch.append(relative_path)
            continue
        if _sha256_file(candidate) != asset.sha256:
            hash_mismatch.append(relative_path)

    return AssetVerification(
        missing=tuple(sorted(missing)),
        size_mismatch=tuple(sorted(size_mismatch)),
        hash_mismatch=tuple(sorted(hash_mismatch)),
        unexpected=tuple(sorted(present - set(declared))),
    )


def fetch_asset_manifest(
    manifest: AssetManifest,
    root: Path,
    *,
    opener: Callable[[str], BinaryIO] | None = None,
) -> AssetVerification:
    """下载缺失或损坏资产，并在校验通过后原子提交到私有缓存。

    输入参数：
        manifest：固定 revision、大小和 SHA-256 的 download-only 清单。
        root：本次资产集合的独占缓存根目录。
        opener：可替换的只读 URL 打开函数；默认使用标准库 ``urlopen``，
            测试可注入无网络实现。
    输出返回值：
        下载完成后的闭集验证结果；成功结果的 ``ok`` 为 ``True``。
    异常：
        AssetFetchError：目录含多余文件、符号链接，或下载内容不符合清单。
    """

    _ensure_private_directory(root, root)
    before = verify_asset_directory(manifest, root)
    if before.unexpected:
        raise AssetFetchError("资产缓存含清单外文件，拒绝自动修改")
    open_url = opener if opener is not None else urlopen

    for asset in manifest.files:
        candidate = root / asset.path
        if (
            asset.path not in before.missing
            and asset.path not in before.size_mismatch
            and asset.path not in before.hash_mismatch
        ):
            continue
        if candidate.is_symlink():
            raise AssetFetchError("资产目标不得是符号链接")
        _ensure_private_directory(root, candidate.parent)
        _download_one_asset(
            manifest,
            asset,
            candidate,
            opener=open_url,
        )

    after = verify_asset_directory(manifest, root)
    if not after.ok:
        raise AssetFetchError("资产下载后仍未通过完整性校验")
    return after


def _parse_source(raw: dict[str, Any]) -> AssetSource:
    """校验固定 revision 的 Hugging Face dataset 来源字段。

    输入参数：
        raw：manifest 的 ``source`` object。
    输出返回值：
        不可变 ``AssetSource``。
    异常：
        AssetManifestError：provider、仓库、revision、路径或许可状态无效。
    """

    _reject_unknown_fields(raw, allowed=_SOURCE_FIELDS, field_name="source")
    provider = raw.get("provider")
    repository = raw.get("repository")
    revision = raw.get("revision")
    base_path = raw.get("base_path")
    license_status = raw.get("license_status")
    if provider != "huggingface_dataset":
        raise AssetManifestError("当前只支持 huggingface_dataset 来源")
    if (
        not isinstance(repository, str)
        or repository.count("/") != 1
        or ".." in repository
    ):
        raise AssetManifestError("Hugging Face repository 格式无效")
    if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
        raise AssetManifestError("资产 revision 必须是固定的 40 位 commit")
    _validate_relative_path(base_path, field_name="source.base_path")
    if not isinstance(license_status, str) or not license_status:
        raise AssetManifestError("资产来源必须显式声明 license_status")
    return AssetSource(
        provider=provider,
        repository=repository,
        revision=revision,
        base_path=base_path,
        license_status=license_status,
    )


def _parse_file(raw: Any) -> AssetFile:
    """校验一个资产文件条目。

    输入参数：
        raw：manifest ``files`` 列表中的一个元素。
    输出返回值：
        路径、大小和 SHA-256 均合法的 ``AssetFile``。
    异常：
        AssetManifestError：条目类型或字段无效。
    """

    if not isinstance(raw, dict):
        raise AssetManifestError("资产文件条目必须是 object")
    _reject_unknown_fields(raw, allowed=_FILE_FIELDS, field_name="files[]")
    relative_path = raw.get("path")
    size = raw.get("size")
    sha256 = raw.get("sha256")
    media_type = raw.get("media_type")
    _validate_relative_path(relative_path, field_name="files.path")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise AssetManifestError("资产文件 size 必须是非负整数")
    if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
        raise AssetManifestError("资产文件 sha256 格式无效")
    if media_type is not None and (
        not isinstance(media_type, str)
        or _MEDIA_TYPE_PATTERN.fullmatch(media_type) is None
    ):
        raise AssetManifestError("资产文件 media_type 格式无效")
    return AssetFile(
        path=relative_path,
        size=size,
        sha256=sha256,
        media_type=media_type,
    )


def _reject_unknown_fields(
    raw: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    field_name: str,
) -> None:
    """拒绝 manifest object 中 schema 未声明的字段。

    输入参数：
        raw：已确认为 object 的 manifest、source 或 file mapping。
        allowed：该 object 层级允许出现的字段名闭集。
        field_name：用于错误定位的 object 名称。
    输出返回值：
        无；字段完全落在闭集内时正常返回。
    异常：
        AssetManifestError：存在任意未知字段。
    """

    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise AssetManifestError(f"{field_name} 包含未知字段: {', '.join(unknown)}")


def _validate_relative_path(value: Any, *, field_name: str) -> None:
    """验证 manifest 路径是安全的 POSIX 相对路径。

    输入参数：
        value：待验证路径字段。
        field_name：仅用于不含路径值的错误定位。
    输出返回值：
        无；合法时正常返回。
    异常：
        AssetManifestError：路径为空、绝对、含父目录或反斜杠。
    """

    if not isinstance(value, str) or not value or "\\" in value:
        raise AssetManifestError(f"{field_name} 必须是 POSIX 相对路径")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise AssetManifestError(f"{field_name} 不得穿越资产根目录")


def _present_relative_files(root: Path) -> set[str]:
    """枚举资产目录中的全部文件和符号链接相对路径。

    输入参数：
        root：待检查目录。
    输出返回值：
        使用 POSIX 分隔符的相对路径集合；目录不存在时返回空集合。
    """

    if not root.is_dir():
        return set()
    return {
        candidate.relative_to(root).as_posix()
        for candidate in root.rglob("*")
        if candidate.is_file() or candidate.is_symlink()
    }


def _ensure_private_directory(root: Path, target: Path) -> None:
    """创建 root 到 target 的目录链并拒绝沿途符号链接。

    输入参数：
        root：资产集合缓存根目录。
        target：需要存在的 root 自身或其内部目录。
    输出返回值：
        无；所有目录存在并设置为 ``0700``。
    异常：
        AssetFetchError：target 不在 root 内，或路径任一部分是符号链接。
    """

    root_absolute = Path(os.path.abspath(os.fspath(root)))
    target_absolute = Path(os.path.abspath(os.fspath(target)))
    try:
        target_absolute.relative_to(root_absolute)
    except ValueError as error:
        raise AssetFetchError("资产目录不得越过缓存根目录") from error

    _ensure_directory_chain_without_symlinks(
        target_absolute,
        private_root=root_absolute,
    )


def _ensure_directory_chain_without_symlinks(
    target: Path,
    *,
    private_root: Path,
) -> None:
    """通过逐级 directory descriptor 建立目录链且不跟随 symlink。

    输入参数：
        target：已转换为绝对路径的最终目录。
        private_root：从该层开始必须设为 ``0700`` 的资产根。
    输出返回值：
        无；目标链完整存在且受控层级均为私有目录。
    异常：
        AssetFetchError：任一已存在层级是 symlink/非目录，或无法
            以 no-follow 方式打开。
    """

    if not target.is_absolute() or not private_root.is_absolute():
        raise AssetFetchError("资产缓存目录必须解析为绝对路径")
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    descriptor = os.open(target.anchor, directory_flags)
    private_depth = len(private_root.parts) - 1
    try:
        for depth, part in enumerate(target.parts[1:], start=1):
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            try:
                child_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=descriptor,
                )
            except OSError as error:
                raise AssetFetchError(
                    "资产缓存目录链不得包含符号链接或非目录"
                ) from error
            os.close(descriptor)
            descriptor = child_descriptor
            directory_status = os.fstat(descriptor)
            if not stat.S_ISDIR(directory_status.st_mode):
                raise AssetFetchError("资产缓存目录链含非目录")
            if depth >= private_depth:
                os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def _download_one_asset(
    manifest: AssetManifest,
    asset: AssetFile,
    target: Path,
    *,
    opener: Callable[[str], BinaryIO],
) -> None:
    """下载并校验一个资产，再以 ``0600`` 权限原子提交。

    输入参数：
        manifest：提供固定 repository、revision 与 base path 的清单。
        asset：提供相对路径、字节数和 SHA-256 的文件条目。
        target：已确认位于私有缓存根目录内的最终路径。
        opener：接收公开固定版本 URL 并返回二进制流的函数。
    输出返回值：
        无；成功时 target 是摘要匹配的完整普通文件。
    异常：
        AssetFetchError：下载字节数或 SHA-256 不匹配。
    """

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".partial",
        dir=target.parent,
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    byte_count = 0
    try:
        os.chmod(temporary_path, 0o600)
        url = _asset_url(manifest.source, asset.path)
        with os.fdopen(descriptor, "wb") as output, opener(url) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if byte_count != asset.size or digest.hexdigest() != asset.sha256:
            raise AssetFetchError("下载资产未通过大小或 SHA-256 校验")
        if target.is_symlink():
            raise AssetFetchError("资产目标在提交前变为符号链接")
        os.replace(temporary_path, target)
        os.chmod(target, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _asset_url(source: AssetSource, relative_path: str) -> str:
    """构造不含 token 且固定到 commit 的 Hugging Face 下载 URL。

    输入参数：
        source：已验证的 dataset repository、revision 和 base path。
        relative_path：已验证的资产文件 POSIX 相对路径。
    输出返回值：
        使用 HTTPS ``resolve/<revision>`` 的公开下载 URL。
    """

    repository = quote(source.repository, safe="/")
    full_path = str(PurePosixPath(source.base_path) / relative_path)
    encoded_path = quote(full_path, safe="/")
    return (
        "https://huggingface.co/datasets/"
        f"{repository}/resolve/{source.revision}/{encoded_path}"
    )


def _sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256，避免把大资产整体读入内存。

    输入参数：
        path：已确认存在且非符号链接的普通文件。
    输出返回值：
        小写十六进制 SHA-256 字符串。
    """

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
