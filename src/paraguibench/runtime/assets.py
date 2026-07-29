"""固定版本外部任务资产的清单加载与本地闭集验证。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from collections.abc import Callable
from typing import BinaryIO
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class AssetManifestError(ValueError):
    """表示外部资产清单结构无效或包含不安全路径。"""


class AssetFetchError(RuntimeError):
    """表示外部资产无法通过固定大小与摘要门禁。"""


@dataclass(frozen=True)
class AssetFile:
    """描述一个固定大小与 SHA-256 的外部文件。"""

    path: str
    size: int
    sha256: str


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
            self.missing
            or self.size_mismatch
            or self.hash_mismatch
            or self.unexpected
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

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AssetManifestError("无法读取外部资产清单") from error
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise AssetManifestError("资产清单 schema_version 必须为 1")
    asset_set_id = raw.get("asset_set_id")
    source_raw = raw.get("source")
    distribution_policy = raw.get("distribution_policy")
    files_raw = raw.get("files")
    if not isinstance(asset_set_id, str) or not asset_set_id:
        raise AssetManifestError("asset_set_id 必须是非空字符串")
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
    relative_path = raw.get("path")
    size = raw.get("size")
    sha256 = raw.get("sha256")
    _validate_relative_path(relative_path, field_name="files.path")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise AssetManifestError("资产文件 size 必须是非负整数")
    if not isinstance(sha256, str) or not _SHA256_PATTERN.fullmatch(sha256):
        raise AssetManifestError("资产文件 sha256 格式无效")
    return AssetFile(path=relative_path, size=size, sha256=sha256)


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

    root_absolute = root.absolute()
    target_absolute = target.absolute()
    try:
        relative = target_absolute.relative_to(root_absolute)
    except ValueError as error:
        raise AssetFetchError("资产目录不得越过缓存根目录") from error

    if root_absolute.is_symlink():
        raise AssetFetchError("资产缓存根目录不得是符号链接")
    root_absolute.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root_absolute, 0o700)
    current = root_absolute
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise AssetFetchError("资产缓存目录链不得包含符号链接")
        current.mkdir(exist_ok=True, mode=0o700)
        os.chmod(current, 0o700)


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
