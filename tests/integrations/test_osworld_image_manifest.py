"""OSWorld 固定镜像 manifest 的解析与可重现物化门禁测试。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifestError,
    load_osworld_image_manifest,
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.integrations.osworld.qcow2_materializer import (
    OSWorldQcow2MaterializationSpec,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_MANIFEST_PATH = (
    _REPOSITORY_ROOT / "environments/osworld/image-manifest.json"
)


def test_repository_image_manifest_records_verified_reproducible_materialization() -> (
    None
):
    """验证正式环境身份已纳入受控 Linux 可重现物化结论。

    输入参数：
        无；读取仓库正式 OSWorld image manifest。
    输出返回值：
        无；6bf recipe 的 archive/member/output/extra 字段全部匹配，
        且正式证据纳入后镜像物化门禁必须 ready；这不代表任何任务
        已完成版本化 live validation。
    """

    manifest = load_osworld_image_manifest(_REPOSITORY_MANIFEST_PATH)

    assert manifest.schema_version == 2
    assert manifest.extracted_sha256 == (
        "6bf667a852b3c307f61d9f09c42559351f45e0607e428b4997becf534cf4d313"
    )
    assert manifest.materialization_status == "verified_reproducible_materialization"
    assert manifest.live_run_ready is True
    assert isinstance(manifest.materialization_spec, OSWorldQcow2MaterializationSpec)
    assert manifest.materialization_spec.member_crc32 == 3184026217
    assert manifest.materialization_spec.member_local_extra_hex.startswith("55540900")
    assert manifest.materialization_spec.member_central_extra_hex.startswith("55540500")


def _write_verified_repository_manifest_copy(tmp_path: Path) -> Path:
    """写入仅将正式预登记 recipe 状态切为 verified 的隔离副本。

    输入参数：tmp_path 为 pytest 隔离目录。
    输出返回值：返回严格 schema v2 副本路径，不改动
        正式清单、任务题面或 input fixture。
    """

    raw = json.loads(_REPOSITORY_MANIFEST_PATH.read_text(encoding="utf-8"))
    raw["extracted_image"]["status"] = "verified_reproducible_materialization"
    candidate = tmp_path / "verified-image-manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")
    return candidate


def test_image_manifest_loader_binds_parse_and_sha_to_same_bytes() -> None:
    """验证 browser image 解析对象与被 WebMall 引用的 SHA 同源。

    输入参数：无；读取正式 OSWorld image manifest。
    输出返回值：无；同次稳定读取的 SHA 等于原始字节摘要。
    """

    manifest, manifest_sha256 = load_osworld_image_manifest_with_sha256(
        _REPOSITORY_MANIFEST_PATH
    )

    assert manifest == load_osworld_image_manifest(_REPOSITORY_MANIFEST_PATH)
    assert (
        manifest_sha256
        == hashlib.sha256(_REPOSITORY_MANIFEST_PATH.read_bytes()).hexdigest()
    )
    assert manifest.manifest_sha256 == manifest_sha256


@pytest.mark.parametrize("malformed", ("duplicate_key", "nan"))
def test_image_manifest_rejects_duplicate_keys_and_nonfinite_values(
    tmp_path: Path,
    malformed: str,
) -> None:
    """held image manifest 在 candidate 和 receipt loader 间共用严格 JSON 语义。

    输入参数：tmp_path 提供隔离 manifest；malformed 选择顶层
        重复 ``schema_version`` 或未使用字段内的 ``NaN``。
    输出返回值：两类 JSON 均在原始字节解析阶段失败关闭，
        不得产生 candidate 可用但正式 loader 不可用的 image 对象。
    """

    raw = json.loads(_REPOSITORY_MANIFEST_PATH.read_text(encoding="utf-8"))
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    if malformed == "duplicate_key":
        payload = '{"schema_version":1,' + canonical[1:]
    else:
        payload = canonical[:-1] + ',"unused_nonfinite":NaN}'
    candidate = tmp_path / "image-manifest.json"
    candidate.write_text(payload, encoding="utf-8")

    with pytest.raises(OSWorldImageManifestError, match="JSON"):
        load_osworld_image_manifest(candidate)


def test_image_manifest_preserves_unverified_state_until_digest_is_fixed(
    tmp_path: Path,
) -> None:
    """验证 archive 摘要不被误当成解压后 qcow2 摘要。

    输入参数：
        tmp_path：pytest 提供的合成 manifest 目录。
    输出返回值：
        无；extracted digest 为 null 时 manifest 可审计但不可 live run。
    """

    path = tmp_path / "image-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol_ids": [
                    "osworld.desktop.v1",
                    "osworld.chrome.v1",
                ],
                "environment_id": "osworld-ubuntu-x86_64",
                "vm_archive": {
                    "provider": "huggingface_dataset",
                    "repository": "example/osworld",
                    "revision": "c" * 40,
                    "path": "Ubuntu.qcow2.zip",
                    "size": 1024,
                    "sha256": "a" * 64,
                    "distribution_policy": "download_only",
                },
                "extracted_image": {
                    "path": "Ubuntu.qcow2",
                    "sha256": None,
                    "status": "must_verify_before_live_run",
                },
                "container": {
                    "image": "example/osworld@sha256:" + "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    manifest = load_osworld_image_manifest(path)

    assert manifest.extracted_sha256 is None
    assert manifest.live_run_ready is False
    assert manifest.container_image.endswith("b" * 64)
    assert manifest.protocol_ids == (
        "osworld.desktop.v1",
        "osworld.chrome.v1",
    )


def test_schema_v2_verified_manifest_derives_the_only_executable_spec(
    tmp_path: Path,
) -> None:
    """schema v2 verified manifest 必须交叉绑定唯一 typed recipe。

    输入参数：tmp_path 提供从正式 manifest 复制的隔离 JSON。
    输出返回值：无；loader 返回的 spec 包含协议版本、
        archive、local/central member 和 output 的完整字段。
    """

    raw = json.loads(_REPOSITORY_MANIFEST_PATH.read_text(encoding="utf-8"))
    raw["extracted_image"]["status"] = "verified_reproducible_materialization"
    candidate = tmp_path / "image-manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    manifest = load_osworld_image_manifest(candidate)
    spec = manifest.materialization_spec

    assert spec is not None
    assert spec.protocol_version == 1
    assert spec.archive_path == raw["vm_archive"]["path"]
    assert spec.archive_sha256 == raw["vm_archive"]["sha256"]
    assert spec.member_compression_method == 8
    assert spec.member_flags == 0
    assert spec.member_creator_system == 3
    assert spec.member_external_attributes == 0x81A40000
    assert spec.output_size == raw["extracted_image"]["size"]
    assert spec.output_sha256 == raw["extracted_image"]["sha256"]


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("vm_archive", "size"),
        ("vm_archive", "sha256"),
        ("extracted_image", "path"),
        ("extracted_image", "size"),
        ("extracted_image", "sha256"),
    ],
)
def test_v2_manifest_rejects_recipe_cross_field_drift(
    tmp_path: Path,
    section: str,
    field: str,
) -> None:
    """archive/extracted 任一重复字段与 recipe 漂移都必须拒绝。

    输入参数：tmp_path 为隔离 JSON；section/field 选择被定向
        修改的一个外层重复身份字段。
    输出返回值：无；typed recipe 不得与外层身份分裂。
    """

    raw = json.loads(_REPOSITORY_MANIFEST_PATH.read_text(encoding="utf-8"))
    original = raw[section][field]
    if field == "size":
        raw[section][field] = original + 1
    elif field == "path":
        raw[section][field] = "Different.qcow2"
    else:
        raw[section][field] = "0" * 64
    candidate = tmp_path / "image-manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(OSWorldImageManifestError, match="recipe"):
        load_osworld_image_manifest(candidate)


def test_image_manifest_requires_fixed_osworld_protocol_ids(
    tmp_path: Path,
) -> None:
    """验证镜像 manifest 不能省略或伪造实际支持的环境协议集合。

    输入参数：
        tmp_path：pytest 提供的合成 manifest 目录。
    输出返回值：
        无；缺少 ``protocol_ids`` 的旧形状在 runtime loader 中失败关闭。
    """

    path = tmp_path / "image-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment_id": "osworld-ubuntu-x86_64",
                "extracted_image": {
                    "path": "Ubuntu.qcow2",
                    "sha256": "a" * 64,
                },
                "container": {
                    "image": "example/osworld@sha256:" + "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OSWorldImageManifestError, match="protocol"):
        load_osworld_image_manifest(path)


@pytest.mark.parametrize(
    ("sha256", "status"),
    [
        ("a" * 64, "must_verify_before_live_run"),
        (None, "verified_reproducible_materialization"),
        ("a" * 64, "verified_on_reference_deployment"),
    ],
)
def test_image_manifest_rejects_digest_status_confusion(
    tmp_path: Path,
    sha256: str | None,
    status: str,
) -> None:
    """验证摘要与物化状态不能被独立伪造为 live-ready。

    输入参数：
        tmp_path：pytest 提供的合成 manifest 目录。
        sha256/status：故意不匹配或不再可接受的物化声明。
    输出返回值：
        无；只有「空摘要+待核验」或「完整摘要+可重现物化
        已核验」可通过，其余组合全部失败关闭。
    """

    path = tmp_path / "image-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol_ids": [
                    "osworld.desktop.v1",
                    "osworld.chrome.v1",
                ],
                "environment_id": "synthetic-osworld",
                "extracted_image": {
                    "path": "Ubuntu.qcow2",
                    "sha256": sha256,
                    "status": status,
                },
                "container": {
                    "image": "example/osworld@sha256:" + "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OSWorldImageManifestError, match="status"):
        load_osworld_image_manifest(path)


def test_schema_v1_verified_shape_cannot_bypass_missing_recipe(
    tmp_path: Path,
) -> None:
    """验证 schema v1 的「verified + SHA」旧形状不得绕过 recipe。

    输入参数：
        tmp_path：pytest 提供的合成 manifest 目录。
    输出返回值：
        无；没有 materialization 闭集的 schema v1 必须失败关闭。
    """

    path = tmp_path / "image-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol_ids": ["osworld.desktop.v1"],
                "environment_id": "synthetic-osworld-reproducible",
                "vm_archive": {
                    "provider": "huggingface_dataset",
                    "repository": "example/osworld",
                    "revision": "c" * 40,
                    "path": "Ubuntu.qcow2.zip",
                    "size": 1024,
                    "sha256": "d" * 64,
                    "distribution_policy": "download_only",
                },
                "extracted_image": {
                    "path": "Ubuntu.qcow2",
                    "sha256": "a" * 64,
                    "status": "verified_reproducible_materialization",
                },
                "container": {
                    "image": "example/osworld@sha256:" + "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OSWorldImageManifestError, match="schema|recipe"):
        load_osworld_image_manifest(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "unknown_provider"),
        ("repository", "missing-owner"),
        ("revision", "main"),
        ("path", "../Ubuntu.qcow2.zip"),
        ("size", 0),
        ("sha256", "f" * 63),
        ("distribution_policy", "redistribute"),
    ],
)
def test_image_manifest_rejects_unpinned_archive_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    """验证 live 身份不能跳过上游 VM 归档的不可变来源。

    输入参数：
        tmp_path：pytest 提供的合成 manifest 目录。
        field/value：定向破坏 archive provider、revision、路径、
            大小、摘要或分发策略的字段与候选值。
    输出返回值：
        无；任一 archive 身份漂移都必须在 doctor/CLI 之前失败。
    """

    raw = json.loads(_REPOSITORY_MANIFEST_PATH.read_text(encoding="utf-8"))
    raw["vm_archive"][field] = value
    candidate = tmp_path / "image-manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(OSWorldImageManifestError, match="archive"):
        load_osworld_image_manifest(candidate)


def test_image_manifest_rejects_unknown_archive_fields(tmp_path: Path) -> None:
    """验证 archive 字段闭集不允许未审计来源元数据混入。

    输入参数：
        tmp_path：pytest 提供的合成 manifest 目录。
    输出返回值：
        无；额外 archive 字段必须被严格 loader 拒绝。
    """

    raw = json.loads(_REPOSITORY_MANIFEST_PATH.read_text(encoding="utf-8"))
    raw["vm_archive"]["unreviewed"] = True
    candidate = tmp_path / "image-manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(OSWorldImageManifestError, match="archive"):
        load_osworld_image_manifest(candidate)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("top", "unreviewed"),
        ("extracted_image", "unreviewed"),
        ("materialization", "unreviewed"),
        ("container", "unreviewed"),
    ],
)
def test_schema_v2_rejects_unknown_fields_at_every_object_boundary(
    tmp_path: Path,
    section: str,
    field: str,
) -> None:
    """schema v2 各对象边界都必须拒绝未知字段。

    输入参数：tmp_path 提供隔离 JSON；section/field
        选择顶层、extracted、recipe 或 container 的额外键。
    输出返回值：无；任一未纳入版本化协议的键都失败关闭。
    """

    raw = json.loads(_REPOSITORY_MANIFEST_PATH.read_text(encoding="utf-8"))
    if section == "top":
        raw[field] = True
    else:
        raw[section][field] = True
    candidate = tmp_path / "image-manifest.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(OSWorldImageManifestError, match="闭集"):
        load_osworld_image_manifest(candidate)


def test_manual_verified_dto_cannot_forge_loader_attestation(tmp_path: Path) -> None:
    """手工构造 verified DTO 不得伪造 loader 信任边界。

    输入参数：无；从正式 v2 manifest 取得有效 typed spec，
        再手工构造字段完全相同的 DTO。
    输出返回值：无；只有严格 loader 生成的对象可 ready。
    """

    loaded = load_osworld_image_manifest(
        _write_verified_repository_manifest_copy(tmp_path)
    )
    forged = type(loaded)(
        protocol_ids=loaded.protocol_ids,
        environment_id=loaded.environment_id,
        extracted_path=loaded.extracted_path,
        extracted_sha256=loaded.extracted_sha256,
        materialization_status=loaded.materialization_status,
        container_image=loaded.container_image,
        schema_version=loaded.schema_version,
        extracted_size=loaded.extracted_size,
        materialization_spec=loaded.materialization_spec,
    )

    assert loaded.live_run_ready is True
    assert forged.live_run_ready is False


def test_dataclass_replace_cannot_preserve_ready_state_after_identity_drift(
    tmp_path: Path,
) -> None:
    """``dataclasses.replace`` 不得继承可用的 ready 身份。

    输入参数：无；对正式 loader 产生的 v2 对象
        替换 environment_id，其他字段和私有字段由 dataclass 继承。
    输出返回值：无；替换后的 DTO 不得被视为 ready。
    """

    loaded = load_osworld_image_manifest(
        _write_verified_repository_manifest_copy(tmp_path)
    )
    forged = replace(loaded, environment_id="forged-environment")

    assert loaded.live_run_ready is True
    assert forged.live_run_ready is False
