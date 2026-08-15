"""解析 OSWorld VM 与容器不可变来源清单。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from paraguibench.integrations.osworld.qcow2_materializer import (
    MATERIALIZATION_PROTOCOL,
    OSWorldQcow2MaterializationError,
    OSWorldQcow2MaterializationSpec,
    validate_osworld_qcow2_materialization_spec,
)

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
_HUGGINGFACE_REPOSITORY_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*"
)
_MATERIALIZATION_STATUS_PENDING = "must_verify_before_live_run"
_MATERIALIZATION_STATUS_VERIFIED = "verified_reproducible_materialization"
_MAX_IMAGE_MANIFEST_BYTES = 1_048_576
_V2_PUBLICATION_METHOD = "o_tmpfile_linkat_noreplace_with_procfd_fallback"


class OSWorldImageManifestError(ValueError):
    """表示 OSWorld image manifest 的版本、路径或 digest 无效。"""


@dataclass(frozen=True)
class OSWorldImageManifest:
    """保存 live runtime 所需的非敏感不可变镜像身份。"""

    protocol_ids: tuple[str, ...]
    environment_id: str
    extracted_path: str
    extracted_sha256: str | None
    materialization_status: str
    container_image: str
    schema_version: int = 1
    extracted_size: int | None = None
    manifest_sha256: str | None = None
    materialization_spec: OSWorldQcow2MaterializationSpec | None = None
    _semantic_identity_sha256: str | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def materialization_recipe_ready(self) -> bool:
        """判断 schema v2 是否携带同源、完整的可执行 recipe。

        输入参数：
            无。
        输出返回值：
            仅 schema v2、typed recipe、外层输出字段与 loader
            语义身份全等时返回 ``True``。pending/verified
            均可携带 recipe，本属性不代表 live 授权。
        """

        spec = self.materialization_spec
        return (
            self.schema_version == 2
            and type(self.extracted_size) is int
            and self.extracted_size > 0
            and isinstance(self.extracted_sha256, str)
            and _SHA256_PATTERN.fullmatch(self.extracted_sha256) is not None
            and type(spec) is OSWorldQcow2MaterializationSpec
            and spec.output_path == self.extracted_path
            and spec.output_size == self.extracted_size
            and spec.output_sha256 == self.extracted_sha256
            and self._semantic_identity_sha256
            == _calculate_manifest_semantic_identity(self)
        )

    @property
    def live_run_ready(self) -> bool:
        """判断解压后 qcow2 是否已有真实物化回执可供 live run。

        输入参数：无。
        输出返回值：完整同源 recipe 且状态为
            ``verified_reproducible_materialization`` 时返回 ``True``；
            v2 pending 只可物化/审计，始终返回 ``False``。
        """

        return (
            self.materialization_recipe_ready
            and self.materialization_status == _MATERIALIZATION_STATUS_VERIFIED
        )


def load_osworld_image_manifest(path: Path) -> OSWorldImageManifest:
    """读取并验证 OSWorld image manifest 的 live runtime 字段。

    输入参数：
        path：仓库内 ``environments/osworld/image-manifest.json``。
    输出返回值：
        容器 digest 与可空 extracted qcow2 digest 的不可变 manifest。
    异常：
        OSWorldImageManifestError：JSON、schema、路径或 image digest 无效。
    """

    manifest, _manifest_sha256 = load_osworld_image_manifest_with_sha256(path)
    return manifest


def load_osworld_image_manifest_with_sha256(
    path: Path,
) -> tuple[OSWorldImageManifest, str]:
    """从同一次 nofollow 稳定读取解析镜像清单并返回摘要。

    输入参数：path 为待读取的 OSWorld image manifest。
    输出返回值：严格镜像身份与产生该对象的同一份原始字节
        SHA-256。
    异常：OSWorldImageManifestError：文件、竞态、JSON 或字段无效。
    """

    raw, manifest_sha256 = _read_image_manifest_json_with_sha256(path)
    return _build_osworld_image_manifest(raw, manifest_sha256)


def load_osworld_image_manifest_bytes_with_sha256(
    payload: bytes,
) -> tuple[OSWorldImageManifest, str]:
    """从已由上层 same-FD 安全读取的字节重建严格镜像快照。

    输入参数：payload 为正式仓库读取器产生的完整 manifest
        原始字节；函数不接受已构造 DTO。
    输出返回值：严格 v1-pending 或 v2-pending/verified 快照及同一份
        原始字节 SHA-256。
    """

    if (
        not isinstance(payload, bytes)
        or not 0 < len(payload) <= _MAX_IMAGE_MANIFEST_BYTES
    ):
        raise OSWorldImageManifestError("OSWorld image manifest 字节无效")
    raw = _decode_image_manifest_json(payload)
    manifest_sha256 = hashlib.sha256(payload).hexdigest()
    return _build_osworld_image_manifest(raw, manifest_sha256)


def _build_osworld_image_manifest(
    raw: dict[str, Any],
    manifest_sha256: str,
) -> tuple[OSWorldImageManifest, str]:
    """从严格 JSON 对象构造闭集镜像身份。

    输入参数：raw 为已拒绝重复键/非有限值的 JSON 对象；
        manifest_sha256 为产生该对象的同源原始字节摘要。
    输出返回值：只能由严格字节解析产生的 DTO 与同源摘要。
    """

    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise OSWorldImageManifestError("OSWorld image manifest schema 无效")
    protocol_ids_raw = raw.get("protocol_ids")
    supported_protocols = {
        "osworld.desktop.v1",
        "osworld.chrome.v1",
    }
    if (
        not isinstance(protocol_ids_raw, list)
        or not protocol_ids_raw
        or any(
            not isinstance(item, str) or item not in supported_protocols
            for item in protocol_ids_raw
        )
        or len(protocol_ids_raw) != len(set(protocol_ids_raw))
    ):
        raise OSWorldImageManifestError("OSWorld protocol_ids 无效")
    protocol_ids = tuple(protocol_ids_raw)
    environment_id = raw.get("environment_id")
    extracted = raw.get("extracted_image")
    container = raw.get("container")
    if not isinstance(environment_id, str) or not environment_id:
        raise OSWorldImageManifestError("environment_id 必须是非空字符串")
    if not isinstance(extracted, dict) or not isinstance(container, dict):
        raise OSWorldImageManifestError("manifest 缺少 extracted_image/container")
    extracted_path = extracted.get("path")
    extracted_sha256 = extracted.get("sha256")
    materialization_status = extracted.get("status")
    _validate_safe_relative_path(extracted_path, "extracted image")
    if schema_version == 1:
        if not (
            extracted_sha256 is None
            and materialization_status == _MATERIALIZATION_STATUS_PENDING
        ):
            raise OSWorldImageManifestError(
                "OSWorld schema v1 仅允许 pending status，verified 缺少 recipe"
            )
        extracted_size: int | None = None
        materialization_spec: OSWorldQcow2MaterializationSpec | None = None
    else:
        if (
            not isinstance(extracted_sha256, str)
            or _SHA256_PATTERN.fullmatch(extracted_sha256) is None
            or materialization_status
            not in {
                _MATERIALIZATION_STATUS_PENDING,
                _MATERIALIZATION_STATUS_VERIFIED,
            }
            or type(extracted.get("size")) is not int
            or extracted["size"] <= 0
        ):
            raise OSWorldImageManifestError(
                "OSWorld schema v2 extracted status 或身份无效"
            )
        extracted_size = extracted["size"]
        materialization_spec = None
    _validate_vm_archive(raw.get("vm_archive"))
    if schema_version == 1:
        _validate_v1_shape(raw, extracted, container)
    else:
        _validate_v2_shape(raw, extracted, container)
        materialization_spec = _parse_v2_materialization_spec(
            raw["materialization"],
            archive=raw["vm_archive"],
            extracted=extracted,
        )
    container_image = container.get("image")
    _validate_container_image(container_image)
    manifest_fields = {
        "protocol_ids": protocol_ids,
        "environment_id": environment_id,
        "extracted_path": extracted_path,
        "extracted_sha256": extracted_sha256,
        "materialization_status": materialization_status,
        "container_image": container_image,
        "schema_version": schema_version,
        "extracted_size": extracted_size,
        "manifest_sha256": manifest_sha256,
        "materialization_spec": materialization_spec,
    }
    semantic_identity = _calculate_manifest_semantic_identity_from_fields(
        **manifest_fields
    )
    manifest = OSWorldImageManifest(
        **manifest_fields,
        _semantic_identity_sha256=semantic_identity,
    )
    return manifest, manifest_sha256


def _calculate_manifest_semantic_identity(
    manifest: OSWorldImageManifest,
) -> str:
    """重算 DTO 的领域分离语义投影摘要。

    输入参数：manifest 为待检查的可读镜像身份值对象。
    输出返回值：绑定 raw manifest SHA 与所有投影字段的 SHA-256。
    注意：该摘要只用于检测 DTO 的意外 ``replace`` 漂移；生产
        执行/receipt 边界仍必须对正式字节重跑严格 loader。
    """

    return _calculate_manifest_semantic_identity_from_fields(
        protocol_ids=manifest.protocol_ids,
        environment_id=manifest.environment_id,
        extracted_path=manifest.extracted_path,
        extracted_sha256=manifest.extracted_sha256,
        materialization_status=manifest.materialization_status,
        container_image=manifest.container_image,
        schema_version=manifest.schema_version,
        extracted_size=manifest.extracted_size,
        manifest_sha256=manifest.manifest_sha256,
        materialization_spec=manifest.materialization_spec,
    )


def _calculate_manifest_semantic_identity_from_fields(
    *,
    protocol_ids: tuple[str, ...],
    environment_id: str,
    extracted_path: str,
    extracted_sha256: str | None,
    materialization_status: str,
    container_image: str,
    schema_version: int,
    extracted_size: int | None,
    manifest_sha256: str | None,
    materialization_spec: OSWorldQcow2MaterializationSpec | None,
) -> str:
    """对 manifest DTO 字段构造稳定语义投影摘要。

    输入参数：参数是 DTO 的全部公开字段，其中 typed
        recipe 被完整投影为字典。
    输出返回值：domain-separated canonical JSON SHA-256。
    """

    projection = {
        "schema_version": schema_version,
        "protocol_ids": list(protocol_ids),
        "environment_id": environment_id,
        "extracted_path": extracted_path,
        "extracted_size": extracted_size,
        "extracted_sha256": extracted_sha256,
        "materialization_status": materialization_status,
        "container_image": container_image,
        "manifest_sha256": manifest_sha256,
        "materialization_spec": (
            asdict(materialization_spec)
            if type(materialization_spec) is OSWorldQcow2MaterializationSpec
            else None
        ),
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(
        b"paraguibench-osworld-image-manifest-semantic-v2\0" + encoded
    ).hexdigest()


def _validate_safe_relative_path(value: Any, label: str) -> None:
    """验证 manifest 中的安全 POSIX 相对路径。

    输入参数：value 为候选路径；label 为固定错误类别。
    输出返回值：无；非空、非绝对、不含 ``..`` 或反斜线时返回。
    """

    if not isinstance(value, str) or not value:
        raise OSWorldImageManifestError(f"{label} path 无效")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or "\\" in value:
        raise OSWorldImageManifestError(f"{label} path 必须是安全相对路径")


def _validate_v1_shape(
    raw: dict[str, Any],
    extracted: dict[str, Any],
    container: dict[str, Any],
) -> None:
    """验证 schema v1 只读 pending 兼容形状。

    输入参数：raw/extracted/container 为已完成核心值检查的 JSON 对象。
    输出返回值：无；顶层和 extracted 严格闭集，container
        仅允许旧精简形状或完整分发元数据形状。
    """

    expected_top = {
        "schema_version",
        "protocol_ids",
        "environment_id",
        "vm_archive",
        "extracted_image",
        "container",
    }
    valid_container_fields = (
        {"image"},
        {"image", "distribution_policy", "build_recipe_status"},
    )
    if (
        set(raw) != expected_top
        or set(extracted) != {"path", "sha256", "status"}
        or set(container) not in valid_container_fields
    ):
        raise OSWorldImageManifestError("OSWorld schema v1 字段闭集无效")
    if set(container) != {"image"} and (
        container["distribution_policy"] != "pull_only"
        or container["build_recipe_status"] != "pending_upstream_audit"
    ):
        raise OSWorldImageManifestError("OSWorld container 字段无效")


def _validate_v2_shape(
    raw: dict[str, Any],
    extracted: dict[str, Any],
    container: dict[str, Any],
) -> None:
    """验证 schema v2 顶层、输出与容器的字段闭集。

    输入参数：raw/extracted/container 为待交叉绑定的 JSON 对象。
    输出返回值：无；任一未知、缺失或分发状态漂移均失败关闭。
    """

    expected_top = {
        "schema_version",
        "protocol_ids",
        "environment_id",
        "vm_archive",
        "extracted_image",
        "materialization",
        "container",
    }
    if (
        set(raw) != expected_top
        or set(extracted) != {"path", "size", "sha256", "status"}
        or set(container) != {"image", "distribution_policy", "build_recipe_status"}
        or container.get("distribution_policy") != "pull_only"
        or container.get("build_recipe_status") != "pending_upstream_audit"
    ):
        raise OSWorldImageManifestError("OSWorld schema v2 字段闭集无效")


def _parse_v2_materialization_spec(
    value: Any,
    *,
    archive: dict[str, Any],
    extracted: dict[str, Any],
) -> OSWorldQcow2MaterializationSpec:
    """将 schema v2 recipe 闭集解析为唯一可执行 typed spec。

    输入参数：value 为 materialization 对象；archive/extracted
        是外层不可变身份，用于逐字段交叉校验。
    输出返回值：已通过低层生产规格验证的精确 typed spec。
    """

    expected_fields = {
        "protocol_id",
        "protocol_version",
        "platform",
        "publication_method",
        "archive_path",
        "archive_size",
        "archive_sha256",
        "member_path",
        "member_compression_method",
        "member_flags",
        "member_creator_system",
        "member_external_attributes",
        "member_local_extra_hex",
        "member_central_extra_hex",
        "member_compressed_size",
        "member_uncompressed_size",
        "member_crc32",
        "output_path",
        "output_size",
        "output_sha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_fields
        or value.get("platform") != "linux"
        or value.get("publication_method") != _V2_PUBLICATION_METHOD
    ):
        raise OSWorldImageManifestError("OSWorld materialization recipe 字段闭集无效")
    try:
        spec = OSWorldQcow2MaterializationSpec(
            protocol=value["protocol_id"],
            protocol_version=value["protocol_version"],
            archive_path=value["archive_path"],
            archive_size=value["archive_size"],
            archive_sha256=value["archive_sha256"],
            member_path=value["member_path"],
            member_compression_method=value["member_compression_method"],
            member_flags=value["member_flags"],
            member_creator_system=value["member_creator_system"],
            member_external_attributes=value["member_external_attributes"],
            member_local_extra_hex=value["member_local_extra_hex"],
            member_central_extra_hex=value["member_central_extra_hex"],
            member_compressed_size=value["member_compressed_size"],
            member_uncompressed_size=value["member_uncompressed_size"],
            member_crc32=value["member_crc32"],
            output_path=value["output_path"],
            output_size=value["output_size"],
            output_sha256=value["output_sha256"],
        )
        validate_osworld_qcow2_materialization_spec(spec)
    except (KeyError, TypeError, OSWorldQcow2MaterializationError):
        raise OSWorldImageManifestError(
            "OSWorld materialization recipe 不可执行"
        ) from None
    if (
        spec.protocol != MATERIALIZATION_PROTOCOL
        or spec.archive_path != archive["path"]
        or spec.archive_size != archive["size"]
        or spec.archive_sha256 != archive["sha256"]
        or spec.output_path != extracted["path"]
        or spec.output_size != extracted["size"]
        or spec.output_sha256 != extracted["sha256"]
    ):
        raise OSWorldImageManifestError(
            "OSWorld materialization recipe 与外层身份不一致"
        )
    return spec


def _read_image_manifest_json_with_sha256(
    path: Path,
) -> tuple[dict[str, Any], str]:
    """通过单个 nofollow descriptor 读取有界 JSON 与同源摘要。

    输入参数：path 为 manifest 候选普通文件。
    输出返回值：JSON object 及完整原始字节 SHA-256。
    异常：OSWorldImageManifestError：类型、大小、读取竞态、编码或
        JSON 无效。
    """

    if not isinstance(path, Path):
        raise OSWorldImageManifestError("OSWorld image manifest 路径无效")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0:
        raise OSWorldImageManifestError("OSWorld image manifest nofollow 不可用")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow | cloexec)
    except OSError:
        raise OSWorldImageManifestError("OSWorld image manifest 无法读取") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= _MAX_IMAGE_MANIFEST_BYTES
        ):
            raise OSError
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                raise OSError
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise OSError
    except OSError:
        raise OSWorldImageManifestError("OSWorld image manifest 稳定读取失败") from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    payload = b"".join(chunks)
    raw = _decode_image_manifest_json(payload)
    return raw, hashlib.sha256(payload).hexdigest()


def _decode_image_manifest_json(payload: bytes) -> dict[str, Any]:
    """用统一严格 JSON 语义解析 manifest 原始字节。

    输入参数：payload 为已通过上限/稳定读取检查的完整字节。
    输出返回值：拒绝重复键、NaN/Infinity 和非对象顶层的字典。
    """

    try:
        raw = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise OSWorldImageManifestError("OSWorld image manifest JSON 无效") from None
    if not isinstance(raw, dict):
        raise OSWorldImageManifestError("OSWorld image manifest JSON 对象无效")
    return raw


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """将 JSON object 键值序列投影为拒绝重复键的字典。

    输入参数：pairs 为 ``json.loads`` 保留原始顺序的键值列表。
    输出返回值：键唯一时返回等价字典；重复键抛出不含输入
        值的 ``ValueError``，由上层折叠为固定 manifest 错误。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_nonfinite_json_constant(_value: str) -> Any:
    """拒绝 Python JSON decoder 默认容许的 NaN/Infinity 扩展。

    输入参数：_value 为 decoder 识别到的非有限常量文本；不回显。
    输出返回值：无正常返回；始终抛出 ``ValueError``。
    """

    del _value
    raise ValueError


def _validate_vm_archive(value: Any) -> None:
    """验证 OSWorld VM 归档的不可变来源与字段闭集。

    输入参数：
        value：manifest 中的 ``vm_archive`` 候选 object。
    输出返回值：
        无；固定 Hugging Face dataset revision、安全相对路径、
        正整数大小、完整 SHA-256 与 download-only 策略时返回。
    异常：
        OSWorldImageManifestError：字段缺失、额外、可变或越界时
            以固定 archive 错误失败关闭。
    """

    expected_fields = {
        "provider",
        "repository",
        "revision",
        "path",
        "size",
        "sha256",
        "distribution_policy",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise OSWorldImageManifestError("OSWorld vm_archive 字段闭集无效")
    archive_path = value["path"]
    if not isinstance(archive_path, str) or not archive_path:
        raise OSWorldImageManifestError("OSWorld vm_archive path 无效")
    parsed_path = PurePosixPath(archive_path)
    safe_path = (
        not parsed_path.is_absolute()
        and ".." not in parsed_path.parts
        and "\\" not in archive_path
    )
    archive_size = value["size"]
    if (
        value["provider"] != "huggingface_dataset"
        or not isinstance(value["repository"], str)
        or _HUGGINGFACE_REPOSITORY_PATTERN.fullmatch(value["repository"]) is None
        or not isinstance(value["revision"], str)
        or _REVISION_PATTERN.fullmatch(value["revision"]) is None
        or not safe_path
        or not isinstance(archive_size, int)
        or isinstance(archive_size, bool)
        or archive_size <= 0
        or not isinstance(value["sha256"], str)
        or _SHA256_PATTERN.fullmatch(value["sha256"]) is None
        or value["distribution_policy"] != "download_only"
    ):
        raise OSWorldImageManifestError("OSWorld vm_archive 不可变身份无效")


def _validate_container_image(value: Any) -> None:
    """验证容器引用固定到 sha256 digest。

    输入参数：
        value：manifest 中的 container.image。
    输出返回值：
        无；合法 digest 引用正常返回。
    异常：
        OSWorldImageManifestError：引用可变或 digest 格式无效。
    """

    if not isinstance(value, str) or value.count("@sha256:") != 1:
        raise OSWorldImageManifestError("container image 必须固定 sha256 digest")
    repository, digest = value.split("@sha256:", 1)
    if (
        not repository
        or any(character.isspace() for character in repository)
        or _SHA256_PATTERN.fullmatch(digest) is None
    ):
        raise OSWorldImageManifestError("container image digest 格式无效")
