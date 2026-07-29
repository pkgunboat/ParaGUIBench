"""解析 OSWorld VM 与容器不可变来源清单。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class OSWorldImageManifestError(ValueError):
    """表示 OSWorld image manifest 的版本、路径或 digest 无效。"""


@dataclass(frozen=True)
class OSWorldImageManifest:
    """保存 live runtime 所需的非敏感不可变镜像身份。"""

    environment_id: str
    extracted_path: str
    extracted_sha256: str | None
    container_image: str

    @property
    def live_run_ready(self) -> bool:
        """判断解压后的 qcow2 是否已有完整 SHA-256。

        输入参数：
            无。
        输出返回值：
            digest 为 64 位小写十六进制时返回 ``True``，否则返回 ``False``。
        """

        return (
            isinstance(self.extracted_sha256, str)
            and _SHA256_PATTERN.fullmatch(self.extracted_sha256) is not None
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

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OSWorldImageManifestError(
            f"无法读取 OSWorld image manifest：{type(error).__name__}"
        ) from None
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise OSWorldImageManifestError("OSWorld image manifest schema 无效")
    environment_id = raw.get("environment_id")
    extracted = raw.get("extracted_image")
    container = raw.get("container")
    if not isinstance(environment_id, str) or not environment_id:
        raise OSWorldImageManifestError("environment_id 必须是非空字符串")
    if not isinstance(extracted, dict) or not isinstance(container, dict):
        raise OSWorldImageManifestError("manifest 缺少 extracted_image/container")
    extracted_path = extracted.get("path")
    extracted_sha256 = extracted.get("sha256")
    if not isinstance(extracted_path, str) or not extracted_path:
        raise OSWorldImageManifestError("extracted image path 无效")
    parsed_path = PurePosixPath(extracted_path)
    if (
        parsed_path.is_absolute()
        or ".." in parsed_path.parts
        or "\\" in extracted_path
    ):
        raise OSWorldImageManifestError("extracted image path 必须是安全相对路径")
    if extracted_sha256 is not None and (
        not isinstance(extracted_sha256, str)
        or _SHA256_PATTERN.fullmatch(extracted_sha256) is None
    ):
        raise OSWorldImageManifestError("extracted image SHA-256 格式无效")
    if extracted_sha256 is None and (
        extracted.get("status") != "must_verify_before_live_run"
    ):
        raise OSWorldImageManifestError("缺失 qcow2 摘要时必须显式阻断 live run")
    container_image = container.get("image")
    _validate_container_image(container_image)
    return OSWorldImageManifest(
        environment_id=environment_id,
        extracted_path=extracted_path,
        extracted_sha256=extracted_sha256,
        container_image=container_image,
    )


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
