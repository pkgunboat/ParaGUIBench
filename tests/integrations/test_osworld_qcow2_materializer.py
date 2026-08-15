"""OSWorld ZIP→qcow2 安全物化公共接口测试。"""

from __future__ import annotations

import gc
import hashlib
from dataclasses import replace
import inspect
import json
import os
from pathlib import Path
import secrets
import stat
import struct
import subprocess
import textwrap
import weakref
import zipfile

import pytest

from paraguibench.integrations.osworld import qcow2_materializer as materializer_module
from paraguibench.integrations.osworld.qcow2_materializer import (
    OSWorldQcow2MaterializationError,
    OSWorldQcow2MaterializationSpec,
)


# 下列两个别名只服务低层安全边界单元测试；正式入口仅允许
# ``materialize_osworld_qcow2_from_repo_root`` 固定仓库 manifest。
materialize_osworld_qcow2 = materializer_module._materialize_osworld_qcow2_from_spec
materialize_osworld_qcow2_from_manifest = (
    materializer_module._materialize_osworld_qcow2_from_manifest
)


class _TestAnonymousOutputBoundary:
    """仅供 macOS 单元测试的可观测文件系统边界。

    输入参数：无；所有路径都通过生产代码已验证的
        held output-parent dirfd 操作。
    输出返回值：模拟同 inode 只读重开与 no-replace 发布。
    注意：该类不是生产安全实现；正式调用不传 ``_system_boundary``，
        必须走 Linux O_TMPFILE + FD-linkat。
    """

    prepublish_nlink = 1

    def __init__(self) -> None:
        self._entries: dict[tuple[int, int], tuple[int, str]] = {}

    def open_anonymous(self, parent_descriptor: int) -> int:
        """在受控测试目录中 O_EXCL 创建一个随机文件。

        输入参数：parent_descriptor 为 held owner-only 测试目录。
        输出返回值：0600 O_RDWR FD。
        """

        for _attempt in range(64):
            name = ".test-anonymous-" + secrets.token_hex(12)
            try:
                descriptor = os.open(
                    name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            status = os.fstat(descriptor)
            self._entries[(status.st_dev, status.st_ino)] = (
                parent_descriptor,
                name,
            )
            return descriptor
        raise OSError("synthetic anonymous output collision")

    def reopen_readonly(self, descriptor: int) -> int:
        """通过测试边界内部名称重开同 inode 的 O_RDONLY FD。

        输入参数：descriptor 为 ``open_anonymous`` 返回的 writer。
        输出返回值：同 dev/inode 的 O_RDONLY FD。
        """

        status = os.fstat(descriptor)
        parent_descriptor, name = self._entries[(status.st_dev, status.st_ino)]
        return os.open(name, os.O_RDONLY, dir_fd=parent_descriptor)

    def publish_noreplace(
        self,
        source_descriptor: int,
        parent_descriptor: int,
        output_name: str,
    ) -> None:
        """在单线程测试边界中发布同 inode，目标存在即失败。

        输入参数：source_descriptor/parent_descriptor/output_name
            与生产边界形状相同。
        输出返回值：无。
        """

        status = os.fstat(source_descriptor)
        source_parent, name = self._entries[(status.st_dev, status.st_ino)]
        assert source_parent == parent_descriptor
        os.link(
            name,
            output_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        os.unlink(name, dir_fd=parent_descriptor)
        del self._entries[(status.st_dev, status.st_ino)]

    def discard_unpublished(
        self,
        source_descriptor: int,
        parent_descriptor: int,
    ) -> None:
        """清理只存在于受控测试边界的未发布文件。

        输入参数：source_descriptor 识别 inode；parent_descriptor
            必须与创建时 held dirfd 一致。
        输出返回值：无。
        """

        status = os.fstat(source_descriptor)
        entry = self._entries.pop((status.st_dev, status.st_ino), None)
        if entry is not None:
            source_parent, name = entry
            assert source_parent == parent_descriptor
            os.unlink(name, dir_fd=parent_descriptor)


@pytest.fixture(autouse=True)
def _install_controlled_anonymous_output_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """将非 Linux 测试进程的私有 factory 替换为单线程测试边界。

    输入参数：monkeypatch 为 pytest 自动回滚的边界替换器。
    输出返回值：无；公共 API 签名仍封闭，仅私有 factory
        在本测试进程中返回新的受控对象。
    """

    monkeypatch.setattr(
        materializer_module,
        "_create_system_boundary",
        _TestAnonymousOutputBoundary,
    )


def _create_synthetic_archive(
    root: Path,
) -> tuple[Path, bytes, OSWorldQcow2MaterializationSpec]:
    """创建小型、严格单 regular member 的合成 ZIP 与匹配规格。

    输入参数：root 为 pytest 隔离目录。
    输出返回值：ZIP 路径、原始 qcow2 字节和完整匹配规格。
    """

    payload = b"".join(
        hashlib.sha256(str(index).encode()).digest() for index in range(4096)
    )
    archive_path = root / "Ubuntu.qcow2.zip"
    info = zipfile.ZipInfo("Ubuntu.qcow2")
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr(info, payload, compresslevel=9)
    with zipfile.ZipFile(archive_path, mode="r") as archive:
        member = archive.infolist()[0]
    spec = OSWorldQcow2MaterializationSpec(
        protocol="paraguibench.osworld.qcow2-zip-materializer.v1",
        protocol_version=1,
        archive_path="Ubuntu.qcow2.zip",
        archive_size=archive_path.stat().st_size,
        archive_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        member_path="Ubuntu.qcow2",
        member_compression_method=member.compress_type,
        member_flags=member.flag_bits,
        member_creator_system=member.create_system,
        member_external_attributes=member.external_attr,
        member_local_extra_hex="",
        member_central_extra_hex=member.extra.hex(),
        member_compressed_size=member.compress_size,
        member_uncompressed_size=len(payload),
        member_crc32=member.CRC,
        output_path="Ubuntu.qcow2",
        output_size=len(payload),
        output_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return archive_path, payload, spec


def test_zip64_size_parser_accepts_the_attested_local_extra_record_order() -> None:
    """验证真实 OSWorld ZIP local extra 的三记录闭集可解析。

    输入参数：无；合成与 2026-08-12 受控主机只读实证
        一致的 ``0x5455 -> 0x7875 -> 0x0001`` 记录顺序。
    输出返回值：无；解析结果必须是已登记的真实
        compressed/uncompressed 尺寸，不得把非 ZIP64 先行记录误报为未知。
    """

    extra = bytes.fromhex(
        "555409000325f2916825f29168"
        "75780b000104000000000400000000"
        "010010000000f1b105000000edca94db02000000"
    )

    compressed, uncompressed = materializer_module._resolve_local_zip_sizes(
        compressed_size_32=0xFFFFFFFF,
        uncompressed_size_32=0xFFFFFFFF,
        extra=extra,
    )

    assert compressed == 12_273_896_173
    assert uncompressed == 24_460_197_888


def _create_attested_extra_layout_archive(
    root: Path,
) -> tuple[Path, bytes, OSWorldQcow2MaterializationSpec]:
    """创建小型但 local/central extra 布局与真实归档同形的 ZIP64。

    输入参数：root 为 pytest 隔离目录。
    输出返回值：返回重写后的 ZIP、原始输出字节与
        绑定 local 48B/central 44B extra 原始字节的 typed spec。
    """

    archive_path, payload, original_spec = _create_synthetic_archive(root)
    raw = bytearray(archive_path.read_bytes())
    local_signature = b"PK\x03\x04"
    central_signature = b"PK\x01\x02"
    eocd_signature = b"PK\x05\x06"
    assert raw[:4] == local_signature
    original_central_offset = raw.index(central_signature)
    original_eocd_offset = raw.index(eocd_signature, original_central_offset)
    filename_length = struct.unpack_from("<H", raw, 26)[0]
    assert filename_length == len(b"Ubuntu.qcow2")
    assert struct.unpack_from("<H", raw, 28)[0] == 0
    central_filename_length = struct.unpack_from(
        "<H", raw, original_central_offset + 28
    )[0]
    assert central_filename_length == filename_length
    assert struct.unpack_from("<H", raw, original_central_offset + 30)[0] == 0

    timestamp_local = struct.pack("<HH", 0x5455, 9) + bytes.fromhex(
        "0325f2916825f29168"
    )
    timestamp_central = struct.pack("<HH", 0x5455, 5) + bytes.fromhex("0325f29168")
    unix_owner = struct.pack("<HH", 0x7875, 11) + bytes.fromhex(
        "0104000000000400000000"
    )
    zip64 = struct.pack(
        "<HHQQ", 0x0001, 16, len(payload), original_spec.member_compressed_size
    )
    local_extra = timestamp_local + unix_owner + zip64
    central_extra = timestamp_central + unix_owner + zip64
    assert len(local_extra) == 48
    assert len(central_extra) == 44

    struct.pack_into("<H", raw, 4, 45)
    struct.pack_into("<II", raw, 18, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<H", raw, 28, len(local_extra))
    local_extra_offset = 30 + filename_length
    raw[local_extra_offset:local_extra_offset] = local_extra

    central_offset = original_central_offset + len(local_extra)
    assert raw[central_offset : central_offset + 4] == central_signature
    struct.pack_into("<H", raw, central_offset + 6, 45)
    struct.pack_into("<II", raw, central_offset + 20, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<H", raw, central_offset + 30, len(central_extra))
    central_extra_offset = central_offset + 46 + central_filename_length
    raw[central_extra_offset:central_extra_offset] = central_extra

    eocd_offset = original_eocd_offset + len(local_extra) + len(central_extra)
    assert raw[eocd_offset : eocd_offset + 4] == eocd_signature
    struct.pack_into("<I", raw, eocd_offset + 12, eocd_offset - central_offset)
    struct.pack_into("<I", raw, eocd_offset + 16, central_offset)
    archive_path.write_bytes(raw)

    with zipfile.ZipFile(archive_path, mode="r") as archive:
        member = archive.infolist()[0]
        assert archive.read(member) == payload
    spec = replace(
        original_spec,
        archive_size=len(raw),
        archive_sha256=hashlib.sha256(raw).hexdigest(),
        member_local_extra_hex=local_extra.hex(),
        member_central_extra_hex=central_extra.hex(),
    )
    return archive_path, payload, spec


def test_manifest_driven_materializer_accepts_attested_zip64_extra_layout(
    tmp_path: Path,
) -> None:
    """验证正式入口能执行与真实 OSWorld ZIP extra 同形的 recipe。

    输入参数：tmp_path 提供小型 ZIP64、清单和 owner-only
        输出目录，不使用 12 GB 真实归档。
    输出返回值：无；manifest-derived spec 必须精确绑定
        local/central raw extra，并成功发布 0400 单链接输出。
    """

    archive_path, payload, spec = _create_attested_extra_layout_archive(tmp_path)
    manifest_path = tmp_path / "image-manifest.json"
    _write_verified_v2_manifest(
        manifest_path,
        spec,
        status="must_verify_before_live_run",
    )
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    result = materialize_osworld_qcow2_from_manifest(
        manifest_path=manifest_path,
        archive_path=archive_path,
        output_parent=output_parent,
    )

    assert result.image_path.read_bytes() == payload
    assert result.sha256 == spec.output_sha256
    assert stat.S_IMODE(result.image_path.stat().st_mode) == 0o400


@pytest.mark.parametrize(
    ("field_name", "mutation"),
    (
        ("member_local_extra_hex", "unknown"),
        ("member_local_extra_hex", "reordered"),
        ("member_local_extra_hex", "duplicate"),
        ("member_local_extra_hex", "truncated"),
        ("member_central_extra_hex", "unknown"),
        ("member_central_extra_hex", "reordered"),
        ("member_central_extra_hex", "duplicate"),
        ("member_central_extra_hex", "truncated"),
    ),
)
def test_materializer_rejects_unregistered_or_malformed_extra_record_layout(
    tmp_path: Path,
    field_name: str,
    mutation: str,
) -> None:
    """验证 local/central extra 的未知、重排、重复与截断均失败。

    输入参数：tmp_path 提供同形 ZIP64；field_name 选择
        local 或 central raw hex；mutation 选择一种协议漂移。
    输出返回值：无；任一漂移必须在打开归档前被 typed
        spec 闭集拒绝，不得发布最终输出。
    """

    archive_path, _payload, spec = _create_attested_extra_layout_archive(tmp_path)
    original = bytes.fromhex(getattr(spec, field_name))
    records = list(materializer_module._parse_zip_extra_records(original))
    if mutation == "unknown":
        records[0] = (0x9999, records[0][1])
        mutated = b"".join(
            struct.pack("<HH", identifier, len(payload)) + payload
            for identifier, payload in records
        )
    elif mutation == "reordered":
        records[0], records[1] = records[1], records[0]
        mutated = b"".join(
            struct.pack("<HH", identifier, len(payload)) + payload
            for identifier, payload in records
        )
    elif mutation == "duplicate":
        identifier, payload = records[0]
        mutated = original + struct.pack("<HH", identifier, len(payload)) + payload
    else:
        mutated = original[:-1]
    candidate_spec = replace(spec, **{field_name: mutated.hex()})
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    with pytest.raises(OSWorldQcow2MaterializationError, match="规格"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=candidate_spec,
        )

    assert not (output_parent / "Ubuntu.qcow2").exists()


def _write_verified_v2_manifest(
    path: Path,
    spec: OSWorldQcow2MaterializationSpec,
    *,
    status: str = "verified_reproducible_materialization",
) -> None:
    """为合成 ZIP 写入与生产 schema v2 同形的严格 manifest。

    输入参数：path 为隔离输出路径；spec 为合成归档的
        archive/member/output 完整身份。
    输出返回值：无；写入只含版本化安全相对路径的 manifest。
    """

    raw = {
        "schema_version": 2,
        "protocol_ids": ["osworld.desktop.v1", "osworld.chrome.v1"],
        "environment_id": "synthetic-osworld",
        "vm_archive": {
            "provider": "huggingface_dataset",
            "repository": "example/osworld",
            "revision": "c" * 40,
            "path": spec.archive_path,
            "size": spec.archive_size,
            "sha256": spec.archive_sha256,
            "distribution_policy": "download_only",
        },
        "extracted_image": {
            "path": spec.output_path,
            "size": spec.output_size,
            "sha256": spec.output_sha256,
            "status": status,
        },
        "materialization": {
            "protocol_id": spec.protocol,
            "protocol_version": spec.protocol_version,
            "platform": "linux",
            "publication_method": "o_tmpfile_linkat_noreplace_with_procfd_fallback",
            "archive_path": spec.archive_path,
            "archive_size": spec.archive_size,
            "archive_sha256": spec.archive_sha256,
            "member_path": spec.member_path,
            "member_compression_method": spec.member_compression_method,
            "member_flags": spec.member_flags,
            "member_creator_system": spec.member_creator_system,
            "member_external_attributes": spec.member_external_attributes,
            "member_local_extra_hex": spec.member_local_extra_hex,
            "member_central_extra_hex": spec.member_central_extra_hex,
            "member_compressed_size": spec.member_compressed_size,
            "member_uncompressed_size": spec.member_uncompressed_size,
            "member_crc32": spec.member_crc32,
            "output_path": spec.output_path,
            "output_size": spec.output_size,
            "output_sha256": spec.output_sha256,
        },
        "container": {
            "image": "example/osworld@sha256:" + "b" * 64,
            "distribution_policy": "pull_only",
            "build_recipe_status": "pending_upstream_audit",
        },
    }
    path.write_text(json.dumps(raw), encoding="utf-8")


def _run_materializer_module_subprocess(
    *,
    tmp_path: Path,
    module_name: str,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    """通过项目解释器真实执行 materializer 的 ``python -m`` 入口。

    输入参数：tmp_path 提供隔离的启动钩子目录；module_name 是待验证的
        模块入口；arguments 是完整 CLI 参数，不含解释器与 ``-m``。
    输出返回值：捕获文本 stdout/stderr 的已完成子进程。macOS 测试使用
        启动时安装的受控匿名输出边界；Linux 生产代码和公开 API 不接受
        该注入。钩子同时强制 canonical implementation 来自当前 checkout。
    """

    repo_root = Path(__file__).resolve().parents[2]
    python = repo_root / ".venv-dev/bin/python"
    assert python.is_file()
    hook_root = tmp_path / f"subprocess-hook-{len(tuple(tmp_path.iterdir()))}"
    hook_root.mkdir(mode=0o700)
    canonical_path = (
        repo_root / "src/paraguibench/integrations/osworld/qcow2_materializer.py"
    ).resolve()
    (hook_root / "sitecustomize.py").write_text(
        textwrap.dedent(
            """
            import os
            from pathlib import Path
            import secrets

            from paraguibench.integrations.osworld import qcow2_materializer
            from paraguibench.integrations.osworld import image_manifest

            expected = Path(os.environ["PARAGUIBENCH_TEST_CANONICAL_MODULE"]).resolve()
            if Path(qcow2_materializer.__file__).resolve() != expected:
                raise RuntimeError("materializer import did not come from checkout")
            if (
                image_manifest.OSWorldQcow2MaterializationSpec
                is not qcow2_materializer.OSWorldQcow2MaterializationSpec
            ):
                raise RuntimeError("canonical materializer type identity split")

            class _SubprocessAnonymousOutputBoundary:
                prepublish_nlink = 1

                def __init__(self):
                    self._entries = {}

                def open_anonymous(self, parent_descriptor):
                    name = ".subprocess-" + secrets.token_hex(12)
                    descriptor = os.open(
                        name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=parent_descriptor,
                    )
                    status = os.fstat(descriptor)
                    self._entries[(status.st_dev, status.st_ino)] = (
                        parent_descriptor,
                        name,
                    )
                    return descriptor

                def reopen_readonly(self, descriptor):
                    status = os.fstat(descriptor)
                    parent_descriptor, name = self._entries[
                        (status.st_dev, status.st_ino)
                    ]
                    return os.open(name, os.O_RDONLY, dir_fd=parent_descriptor)

                def publish_noreplace(
                    self,
                    source_descriptor,
                    parent_descriptor,
                    output_name,
                ):
                    status = os.fstat(source_descriptor)
                    source_parent, name = self._entries.pop(
                        (status.st_dev, status.st_ino)
                    )
                    if source_parent != parent_descriptor:
                        raise RuntimeError("synthetic parent identity mismatch")
                    os.link(
                        name,
                        output_name,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                    os.unlink(name, dir_fd=parent_descriptor)

                def discard_unpublished(
                    self,
                    source_descriptor,
                    parent_descriptor,
                ):
                    status = os.fstat(source_descriptor)
                    entry = self._entries.pop(
                        (status.st_dev, status.st_ino),
                        None,
                    )
                    if entry is not None:
                        source_parent, name = entry
                        if source_parent != parent_descriptor:
                            raise RuntimeError("synthetic parent identity mismatch")
                        os.unlink(name, dir_fd=parent_descriptor)

            qcow2_materializer._create_system_boundary = (
                _SubprocessAnonymousOutputBoundary
            )
            """
        ),
        encoding="utf-8",
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join((str(hook_root), str(repo_root / "src"))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONWARNINGS": "ignore::RuntimeWarning",
        "PARAGUIBENCH_TEST_CANONICAL_MODULE": str(canonical_path),
    }
    return subprocess.run(
        [str(python), "-m", module_name, *arguments],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_formal_python_module_entry_avoids_implementation_type_double_load(
    tmp_path: Path,
) -> None:
    """正式薄入口必须让 v2 recipe 与 implementation 保持同一类型身份。

    输入参数：tmp_path 提供合成仓库、严格 ZIP、私有输出目录和启动钩子。
    输出返回值：新正式入口的 ``--help`` 与完整 synthetic 物化都通过真实项目解释器，
        stdout JSON 只含名称、摘要和大小。
    """

    archive_root = tmp_path / "archive"
    archive_root.mkdir(mode=0o700)
    archive_path, _payload, spec = _create_synthetic_archive(archive_root)
    repo_root = tmp_path / "repo"
    fixed_manifest = repo_root / "environments/osworld/image-manifest.json"
    fixed_manifest.parent.mkdir(parents=True)
    _write_verified_v2_manifest(
        fixed_manifest,
        spec,
        status="must_verify_before_live_run",
    )
    formal_output_parent = tmp_path / "formal-owned"
    formal_output_parent.mkdir(mode=0o700)
    common_arguments = [
        "--repo-root",
        str(repo_root),
        "--archive",
        str(archive_path),
    ]
    formal_module = "paraguibench.cli.osworld_qcow2_materializer"

    formal_help = _run_materializer_module_subprocess(
        tmp_path=tmp_path,
        module_name=formal_module,
        arguments=["--help"],
    )
    formal_execution = _run_materializer_module_subprocess(
        tmp_path=tmp_path,
        module_name=formal_module,
        arguments=[
            *common_arguments,
            "--output-parent",
            str(formal_output_parent),
        ],
    )

    assert formal_help.returncode == 0, formal_help.stderr
    assert formal_help.stderr == ""
    assert (
        "usage: python -m paraguibench.cli.osworld_qcow2_materializer"
        in formal_help.stdout
    )
    assert (
        "paraguibench.integrations.osworld.qcow2_materializer" not in formal_help.stdout
    )
    assert formal_execution.returncode == 0, formal_execution.stderr
    assert formal_execution.stderr == ""
    assert json.loads(formal_execution.stdout) == {
        "output_name": "Ubuntu.qcow2",
        "sha256": spec.output_sha256,
        "size": spec.output_size,
    }
    assert str(formal_output_parent) not in formal_execution.stdout
    assert (formal_output_parent / "Ubuntu.qcow2").read_bytes() == _payload


def test_implementation_python_module_entry_is_a_fixed_migration_failure(
    tmp_path: Path,
) -> None:
    """旧 implementation ``-m`` 必须先于参数解析和文件 I/O 失败关闭。

    输入参数：tmp_path 仅提供子进程启动钩子；完整参数刻意携带不存在的
        repo/archive/output 哨兵路径，任何一项都不应被打开或创建。
    输出返回值：``--help`` 与完整参数均返回同一固定迁移提示和退出码 1；
        哨兵值不会出现在输出，且旧路径不再伪装成可运行 CLI。
    """

    implementation_module = "paraguibench.integrations.osworld.qcow2_materializer"
    sentinel = "SYNTHETIC_OLD_ENTRY_PATH_CANARY"
    help_result = _run_materializer_module_subprocess(
        tmp_path=tmp_path,
        module_name=implementation_module,
        arguments=["--help"],
    )
    full_result = _run_materializer_module_subprocess(
        tmp_path=tmp_path,
        module_name=implementation_module,
        arguments=[
            "--repo-root",
            str(tmp_path / f"repo-{sentinel}"),
            "--archive",
            str(tmp_path / f"archive-{sentinel}"),
            "--output-parent",
            str(tmp_path / f"output-{sentinel}"),
        ],
    )
    expected = (
        "OSWORLD_QCOW2_IMPLEMENTATION_MODULE_NOT_CLI; "
        "use python -m paraguibench.cli.osworld_qcow2_materializer\n"
    )

    for result in (help_result, full_result):
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == expected
        assert sentinel not in result.stderr
    assert not (tmp_path / f"output-{sentinel}").exists()


@pytest.mark.parametrize(
    ("unknown_option", "sensitive_value"),
    (
        ("--api-key", "SYNTHETIC_SENTINEL_NOT_A_SECRET"),
        ("--manifest", "/sensitive/operator-selected/manifest.json"),
    ),
)
def test_formal_python_module_entry_redacts_unknown_argument_values(
    tmp_path: Path,
    unknown_option: str,
    sensitive_value: str,
) -> None:
    """正式薄入口的参数错误不得复述未知选项所携带的敏感值。

    输入参数：tmp_path 提供隔离路径；unknown_option/sensitive_value
        覆盖伪凭据与任意 manifest 路径两种未知参数。
    输出返回值：真实子进程在任何 manifest、archive 或 output I/O 前
        以固定错误和 rc=2 失败；stdout/stderr 均不含输入值，也不产物。
    """

    formal_module = "paraguibench.cli.osworld_qcow2_materializer"
    output_parent = tmp_path / "must-not-be-created"
    result = _run_materializer_module_subprocess(
        tmp_path=tmp_path,
        module_name=formal_module,
        arguments=[
            "--repo-root",
            str(tmp_path / "nonexistent-repo"),
            "--archive",
            str(tmp_path / "nonexistent-archive.zip"),
            "--output-parent",
            str(output_parent),
            unknown_option,
            sensitive_value,
        ],
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "OSWORLD_QCOW2_ARGUMENT_ERROR\n"
    assert sensitive_value not in result.stdout
    assert sensitive_value not in result.stderr
    assert not output_parent.exists()


def test_manifest_driven_materializer_rejects_schema_v1_pending_without_recipe(
    tmp_path: Path,
) -> None:
    """验证可审计但无 recipe 的 schema v1 pending 不能启动物化。

    输入参数：tmp_path 提供合成归档、v1 pending manifest 与
        owner-only 输出目录。
    输出返回值：无；正式入口必须在读 ZIP 或创建输出前
        因缺少 schema v2 typed recipe 失败关闭。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    manifest_path = tmp_path / "image-manifest-v1.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol_ids": ["osworld.desktop.v1"],
                "environment_id": "synthetic-osworld-v1",
                "vm_archive": {
                    "provider": "huggingface_dataset",
                    "repository": "example/osworld",
                    "revision": "c" * 40,
                    "path": spec.archive_path,
                    "size": spec.archive_size,
                    "sha256": spec.archive_sha256,
                    "distribution_policy": "download_only",
                },
                "extracted_image": {
                    "path": spec.output_path,
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
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    with pytest.raises(OSWorldQcow2MaterializationError, match="typed recipe"):
        materialize_osworld_qcow2_from_manifest(
            manifest_path=manifest_path,
            archive_path=archive_path,
            output_parent=output_parent,
        )

    assert not (output_parent / "Ubuntu.qcow2").exists()


def test_materialize_valid_single_member_archive_to_private_readonly_image(
    tmp_path: Path,
) -> None:
    """固定单 member ZIP 只能物化为新建的独立只读 qcow2。

    输入参数：tmp_path 提供不含真实 12 GB 归档的隔离文件系统。
    输出返回值：无；公共物化接口必须核对归档和 member
        完整身份，保留源归档，并返回 0400、单链接、新 inode 输出。
    """

    archive_path, payload, spec = _create_synthetic_archive(tmp_path)
    archive_before = os.stat(archive_path, follow_symlinks=False)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    result = materialize_osworld_qcow2(
        archive_path=archive_path,
        output_parent=output_parent,
        spec=spec,
    )

    archive_after = os.stat(archive_path, follow_symlinks=False)
    output_stat = os.stat(result.image_path, follow_symlinks=False)
    assert result.sha256 == spec.output_sha256
    assert result.size == len(payload)
    assert result.image_path.name == "Ubuntu.qcow2"
    assert result.image_path.read_bytes() == payload
    assert stat.S_IMODE(output_stat.st_mode) == 0o400
    assert output_stat.st_nlink == 1
    assert output_stat.st_ino != archive_after.st_ino
    assert (
        archive_before.st_dev,
        archive_before.st_ino,
        archive_before.st_size,
        archive_before.st_mtime_ns,
        archive_before.st_ctime_ns,
    ) == (
        archive_after.st_dev,
        archive_after.st_ino,
        archive_after.st_size,
        archive_after.st_mtime_ns,
        archive_after.st_ctime_ns,
    )


def test_materializer_rejects_non_spec_without_leaking_attribute_error(
    tmp_path: Path,
) -> None:
    """非规格对象必须在任何字段解引用前失败关闭。

    输入参数：tmp_path 提供不会被读取的候选路径。
    输出返回值：无；公共边界应返回固定物化异常，
        不得暴露 ``AttributeError`` 或候选对象内容。
    """

    with pytest.raises(OSWorldQcow2MaterializationError, match="规格"):
        materialize_osworld_qcow2(
            archive_path=tmp_path / "missing.zip",
            output_parent=tmp_path,
            spec=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("relative_argument", ("archive", "output_parent"))
def test_materializer_rejects_relative_security_boundary_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_argument: str,
) -> None:
    """归档与输出父目录必须是可逐级 nofollow 的绝对路径。

    输入参数：tmp_path 提供合成归档；monkeypatch 仅更改
        测试工作目录；relative_argument 选择被降级的边界。
    输出返回值：无；即使相对路径当前可解析也必须拒绝。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    monkeypatch.chdir(tmp_path)
    candidate_archive = (
        Path(archive_path.name) if relative_argument == "archive" else archive_path
    )
    candidate_output = (
        Path(output_parent.name)
        if relative_argument == "output_parent"
        else output_parent
    )

    with pytest.raises(OSWorldQcow2MaterializationError, match="绝对"):
        materialize_osworld_qcow2(
            archive_path=candidate_archive,
            output_parent=candidate_output,
            spec=spec,
        )


@pytest.mark.parametrize("symlink_boundary", ("archive", "output_parent"))
def test_materializer_rejects_symlink_in_any_security_boundary_ancestor(
    tmp_path: Path,
    symlink_boundary: str,
) -> None:
    """任一归档或输出祖先分量是 symlink 时都必须拒绝。

    输入参数：tmp_path 提供真实目录与中间 symlink；
        symlink_boundary 选择归档或输出边界。
    输出返回值：无；不允许仅对 leaf 执行 ``O_NOFOLLOW``。
    """

    archive_root = tmp_path / "archive-real"
    archive_root.mkdir(mode=0o700)
    archive_path, _payload, spec = _create_synthetic_archive(archive_root)
    archive_alias = tmp_path / "archive-link"
    archive_alias.symlink_to(archive_root, target_is_directory=True)
    output_root = tmp_path / "output-real"
    output_root.mkdir(mode=0o700)
    output_alias = tmp_path / "output-link"
    output_alias.symlink_to(output_root, target_is_directory=True)

    candidate_archive = (
        archive_alias / archive_path.name
        if symlink_boundary == "archive"
        else archive_path
    )
    candidate_output = (
        output_alias if symlink_boundary == "output_parent" else output_root
    )

    with pytest.raises(OSWorldQcow2MaterializationError, match="nofollow"):
        materialize_osworld_qcow2(
            archive_path=candidate_archive,
            output_parent=candidate_output,
            spec=spec,
        )


def test_failed_materialization_closes_unpublished_output_without_touching_sentinel(
    tmp_path: Path,
) -> None:
    """输出摘要不匹配时必须在发布前失败。

    输入参数：tmp_path 提供合成 ZIP 与 owner-only 输出父目录。
    输出返回值：无；生产匿名 inode 只需 close，原归档、
        固定最终名与父目录 sentinel 都不受影响。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    sentinel = output_parent / "keep.txt"
    sentinel.write_text("not-owned-by-materializer", encoding="utf-8")

    with pytest.raises(OSWorldQcow2MaterializationError, match="输出"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=replace(spec, output_sha256="0" * 64),
        )

    assert archive_path.is_file()
    assert sentinel.read_text(encoding="utf-8") == "not-owned-by-materializer"
    assert sorted(path.name for path in output_parent.iterdir()) == ["keep.txt"]


def test_public_materializer_cannot_inject_or_name_a_temporary_output() -> None:
    """公共物化接口不得暴露系统边界注入或 named-temp 清理。

    输入参数：无；检查公共函数签名与 Linux 生产边界源。
    输出返回值：无；操作者只能提供 archive/output/spec，
        生产边界不包含 unlink/rename 的 pathname cleanup。
    """

    parameters = tuple(inspect.signature(materialize_osworld_qcow2).parameters)
    production_source = inspect.getsource(
        materializer_module._LinuxAnonymousOutputBoundary
    )

    assert parameters == ("archive_path", "output_parent", "spec")
    assert "os.unlink" not in production_source
    assert "os.rename" not in production_source


def test_materializer_rejects_local_header_method_that_disagrees_with_central_directory(
    tmp_path: Path,
) -> None:
    """ZIP local header 与 central directory 的压缩法必须一致。

    输入参数：tmp_path 提供合成 ZIP；测试只把 local
        header method 从 deflate 改成 store，并同步 archive SHA 以绕过外层摘要。
    输出返回值：无；即使 central recipe 仍完整匹配，
        local/central 分裂也必须在任何输出发布前失败。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    raw = bytearray(archive_path.read_bytes())
    assert raw[:4] == b"PK\x03\x04"
    raw[8:10] = (zipfile.ZIP_STORED).to_bytes(2, "little")
    archive_path.write_bytes(raw)
    mutated_spec = replace(
        spec,
        archive_sha256=hashlib.sha256(raw).hexdigest(),
    )
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    with pytest.raises(OSWorldQcow2MaterializationError, match="local header"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=mutated_spec,
        )

    assert not (output_parent / "Ubuntu.qcow2").exists()


def test_materializer_never_overwrites_existing_final_path(tmp_path: Path) -> None:
    """最终普通文件已存在时必须 no-replace 失败并保留原字节。

    输入参数：tmp_path 提供合成 ZIP 和预存 ``Ubuntu.qcow2``。
    输出返回值：无；不论旧文件内容是否符合 recipe，
        物化器都不得覆盖、删除或复用它。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    existing = output_parent / "Ubuntu.qcow2"
    existing.write_bytes(b"pre-existing-not-owned")
    before = os.stat(existing, follow_symlinks=False)

    with pytest.raises(OSWorldQcow2MaterializationError, match="物化失败"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=spec,
        )

    after = os.stat(existing, follow_symlinks=False)
    assert existing.read_bytes() == b"pre-existing-not-owned"
    assert (before.st_dev, before.st_ino, before.st_size) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
    )


def test_archive_path_replacement_between_hashes_fails_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次 ZIP 摘要后替换归档 path entry 必须在发布前失败。

    输入参数：tmp_path 提供合成归档；monkeypatch 让受控
        测试边界在 writer 完成后、归档第二次摘要前替换名称。
    输出返回值：无；同一 held FD 的字节仍正确也不够，
        逐级 path continuity 必须拒绝替换且最终名不得出现。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    class _ReplacingArchiveBoundary(_TestAnonymousOutputBoundary):
        """在输出只读重开时仅替换一次 archive path entry。"""

        def reopen_readonly(self, descriptor: int) -> int:
            """返回只读 FD 前替换归档名称。

            输入参数：descriptor 为测试 writer。
            输出返回值：父类返回的同 inode O_RDONLY FD。
            """

            readonly = super().reopen_readonly(descriptor)
            original = archive_path.with_suffix(".held-original")
            archive_path.rename(original)
            archive_path.write_bytes(b"replacement-archive-path")
            return readonly

    monkeypatch.setattr(
        materializer_module,
        "_create_system_boundary",
        _ReplacingArchiveBoundary,
    )

    with pytest.raises(OSWorldQcow2MaterializationError, match="漂移|连续性"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=spec,
        )

    assert not (output_parent / "Ubuntu.qcow2").exists()


def test_final_image_name_is_not_visible_until_full_output_is_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解压写入期间不得暴露最终 ``Ubuntu.qcow2`` 名称。

    输入参数：tmp_path 提供合成 ZIP；monkeypatch 在首次
        输出 write 的文件系统边界观测随机目录。
    输出返回值：无；只有在 fsync、0400 和完整重读摘要后
        才允许 no-replace 发布最终名。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    original_write = os.write
    observation = {"checked": False}

    def _observe_first_output_write(descriptor: int, data: bytes) -> int:
        """在首次写入前确认最终名尚未可见。

        输入参数：descriptor/data 透传给真实 ``os.write``。
        输出返回值：真实写入的字节数。
        """

        if not observation["checked"]:
            temporary = [
                path
                for path in output_parent.iterdir()
                if path.name.startswith(".test-anonymous-")
            ]
            assert len(temporary) == 1
            assert not (output_parent / "Ubuntu.qcow2").exists()
            observation["checked"] = True
        return original_write(descriptor, data)

    monkeypatch.setattr(os, "write", _observe_first_output_write)

    result = materialize_osworld_qcow2(
        archive_path=archive_path,
        output_parent=output_parent,
        spec=spec,
    )

    assert observation["checked"] is True
    assert result.image_path.name == "Ubuntu.qcow2"


def test_final_inode_mutation_after_hash_is_rejected_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最终 FD 完整求摘要后的同 inode 改写必须被拒绝。

    输入参数：tmp_path 提供合成归档；monkeypatch 在首次
        final-name continuity 检查前修改已发布 inode 的字节，
        然后恢复 0400 模式。
    输出返回值：无；返回前必须将路径与求摘要结束时的
        full-stat 快照精确绑定，不得用篡改后的 fresh stat 作预期值。
    """

    archive_path, payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    final_path = output_parent / "Ubuntu.qcow2"
    original_verify = materializer_module._verify_final_name_continuity
    observation = {"mutated": False}

    def _mutate_then_verify(
        parent_descriptor: int,
        output_name: str,
        expected_identity: object,
    ) -> None:
        """在首次最终路径核验前将同 inode 篡改。"""

        if not observation["mutated"]:
            os.chmod(final_path, 0o600)
            with final_path.open("r+b") as stream:
                stream.seek(0)
                stream.write(b"X" if payload[:1] != b"X" else b"Y")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(final_path, 0o400)
            observation["mutated"] = True
        original_verify(parent_descriptor, output_name, expected_identity)

    monkeypatch.setattr(
        materializer_module,
        "_verify_final_name_continuity",
        _mutate_then_verify,
    )

    with pytest.raises(OSWorldQcow2MaterializationError, match="漂移"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=spec,
        )

    assert observation["mutated"] is True


def test_final_readonly_open_requires_nonblocking_before_fifo_fstat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最终名打开必须先 O_NONBLOCK，再对 FIFO 失败关闭。

    输入参数：monkeypatch 替换 ``os.open`` 以在不产生
        真实阻塞的情况下审计 flags。
    输出返回值：无；若未携带 O_NONBLOCK，测试立即 RED，
        避免生产中在 fstat 前被 FIFO 无限阻塞。
    """

    nonblock = getattr(os, "O_NONBLOCK", 0)
    assert nonblock

    def _reject_synthetic_fifo(
        path: str,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        """断言最终打开的非阻塞属性并模拟 FIFO 拒绝。"""

        del path, args, kwargs
        assert flags & nonblock
        raise OSError("synthetic fifo")

    monkeypatch.setattr(os, "open", _reject_synthetic_fifo)
    with pytest.raises(OSWorldQcow2MaterializationError, match="nofollow"):
        materializer_module._open_final_readonly(123, "Ubuntu.qcow2")


def test_output_parent_permission_widening_is_rejected_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已 held 输出父目录在发布前被放宽权限必须拒绝。

    输入参数：tmp_path 提供合成归档；monkeypatch 使
        受控边界在 publish 入口将 owner-only 目录改为 0770。
    输出返回值：无；物化器必须在发布前重验 held
        parent 的目录类型、uid 和 owner-only 模式。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    class _WideningParentBoundary(_TestAnonymousOutputBoundary):
        """在真正 publish 前放宽输出父目录权限。"""

        def publish_noreplace(
            self,
            source_descriptor: int,
            parent_descriptor: int,
            output_name: str,
        ) -> None:
            """改为 0770 后转发测试边界的 publish。"""

            os.fchmod(parent_descriptor, 0o770)
            super().publish_noreplace(
                source_descriptor,
                parent_descriptor,
                output_name,
            )

    monkeypatch.setattr(
        materializer_module,
        "_create_system_boundary",
        _WideningParentBoundary,
    )

    try:
        with pytest.raises(OSWorldQcow2MaterializationError, match="owner-only"):
            materialize_osworld_qcow2(
                archive_path=archive_path,
                output_parent=output_parent,
                spec=spec,
            )
    finally:
        output_parent.chmod(0o700)


@pytest.mark.parametrize(
    "field_name",
    (
        "protocol_version",
        "archive_size",
        "member_compression_method",
        "member_flags",
        "member_creator_system",
        "member_external_attributes",
        "member_compressed_size",
        "member_uncompressed_size",
        "member_crc32",
        "output_size",
    ),
)
def test_materialization_spec_rejects_bool_for_every_integer_field(
    tmp_path: Path,
    field_name: str,
) -> None:
    """recipe 中任一整数字段都不得接受 bool。

    输入参数：tmp_path 提供合成 recipe；field_name
        选择将被替换为 ``True`` 的整数字段。
    输出返回值：无；严格 ``type is int`` 必须在 I/O 前拒绝。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    with pytest.raises(OSWorldQcow2MaterializationError, match="规格"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=replace(spec, **{field_name: True}),
        )


@pytest.mark.parametrize("operation", ("open_anonymous", "reopen_readonly"))
def test_linux_boundary_closes_new_descriptor_when_post_open_fstat_fails(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Linux 边界在 open 成功后的 fstat 异常不得泄漏 FD。

    输入参数：monkeypatch 提供系统调用故障注入；
        operation 选择匿名创建或只读重开。
    输出返回值：无；新取得所有权的 FD 必须被关闭一次。
    """

    opened = 91
    closed: list[int] = []
    monkeypatch.setattr(materializer_module.sys, "platform", "linux")
    monkeypatch.setattr(materializer_module.os, "O_TMPFILE", 0x400000, raising=False)
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: opened)
    monkeypatch.setattr(
        os, "fstat", lambda _descriptor: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(os, "close", closed.append)
    boundary = materializer_module._LinuxAnonymousOutputBoundary()

    with pytest.raises(OSWorldQcow2MaterializationError):
        if operation == "open_anonymous":
            boundary.open_anonymous(10)
        else:
            boundary.reopen_readonly(10)

    assert closed == [opened]


@pytest.mark.parametrize("operation", ("archive", "output_parent"))
def test_held_path_is_closed_when_post_open_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """held 路径句柄在首个打开后校验失败时必须关闭。

    输入参数：tmp_path 提供规格；monkeypatch 注入
        held-handle 与 fstat 故障；operation 选择归档或输出父目录。
    输出返回值：无；异常路径必须调用 handle.close。
    """

    _archive_path, _payload, spec = _create_synthetic_archive(tmp_path)

    class _SyntheticHandle:
        """记录 close 的最小 held-handle 替身。"""

        leaf_descriptor = 73

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            """记录生产异常分支已释放句柄。"""

            self.closed = True

    handle = _SyntheticHandle()
    monkeypatch.setattr(
        materializer_module,
        "_open_absolute_nofollow",
        lambda *args, **kwargs: handle,
    )
    if operation == "archive":
        monkeypatch.setattr(
            os,
            "fstat",
            lambda _descriptor: (_ for _ in ()).throw(OSError()),
        )
        invoke = lambda: materializer_module._open_attested_archive(  # noqa: E731
            tmp_path / "Ubuntu.qcow2.zip",
            spec,
        )
    else:
        monkeypatch.setattr(
            materializer_module,
            "_verify_private_output_parent",
            lambda _descriptor: (_ for _ in ()).throw(
                OSWorldQcow2MaterializationError("synthetic")
            ),
        )
        invoke = lambda: materializer_module._open_private_output_parent(  # noqa: E731
            tmp_path
        )

    with pytest.raises(OSWorldQcow2MaterializationError):
        invoke()
    assert handle.closed is True


def test_archive_path_mutation_after_final_output_hash_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """final output 求摘要结束后的归档 path 替换必须拒绝。

    输入参数：tmp_path 提供合成归档；monkeypatch 在
        第二次 output 完整求摘要后替换 archive path entry。
    输出返回值：无；返回前必须用第二次 archive SHA
        结束时的 full-stat 快照重验 held FD 和路径链。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    original_verify = materializer_module._verify_readonly_image
    calls = {"count": 0}

    def _mutate_archive_after_final_hash(
        descriptor: int,
        candidate_spec: OSWorldQcow2MaterializationSpec,
        *,
        expected_nlink: int,
    ) -> tuple[int, ...]:
        """转发 output 核验，并在第二次返回前替换归档名。"""

        identity = original_verify(
            descriptor,
            candidate_spec,
            expected_nlink=expected_nlink,
        )
        calls["count"] += 1
        if calls["count"] == 2:
            archive_path.rename(archive_path.with_suffix(".verified-held"))
            archive_path.write_bytes(b"replacement-after-final-output-hash")
        return identity

    monkeypatch.setattr(
        materializer_module,
        "_verify_readonly_image",
        _mutate_archive_after_final_hash,
    )

    with pytest.raises(OSWorldQcow2MaterializationError, match="ZIP.*漂移|连续性"):
        materialize_osworld_qcow2(
            archive_path=archive_path,
            output_parent=output_parent,
            spec=spec,
        )

    assert calls["count"] == 2


def test_duplicate_descriptor_is_closed_when_fdopen_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dup 成功但 fdopen 失败时必须关闭 raw FD。

    输入参数：monkeypatch 注入 dup/fdopen 部分失败并记录 close。
    输出返回值：无；ZIP central 与解压两条路径共用的
        helper 必须精确释放已取得的 duplicate descriptor。
    """

    closed: list[int] = []
    monkeypatch.setattr(os, "dup", lambda _descriptor: 87)
    monkeypatch.setattr(
        os,
        "fdopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic")),
    )
    monkeypatch.setattr(os, "close", closed.append)

    with pytest.raises(OSError, match="synthetic"):
        materializer_module._open_duplicate_binary_stream(11)

    assert closed == [87]


def test_manifest_driven_entry_derives_spec_without_operator_digest_fields(
    tmp_path: Path,
) -> None:
    """正式入口必须仅从严格 manifest 派生 typed recipe。

    输入参数：tmp_path 提供合成 ZIP、v2 manifest 与 0700 输出目录。
    输出返回值：无；入口只接受三个路径，成功物化与
        manifest output SHA/size 完全一致的固定名文件。
    """

    archive_path, payload, spec = _create_synthetic_archive(tmp_path)
    manifest_path = tmp_path / "image-manifest.json"
    _write_verified_v2_manifest(manifest_path, spec)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    result = materialize_osworld_qcow2_from_manifest(
        manifest_path=manifest_path,
        archive_path=archive_path,
        output_parent=output_parent,
    )

    assert result.image_path.read_bytes() == payload
    assert result.sha256 == spec.output_sha256


def test_manifest_driven_entry_rejects_symlink_manifest_ancestor(
    tmp_path: Path,
) -> None:
    """正式 manifest 路径的任一 symlink 祖先都必须失败关闭。

    输入参数：tmp_path 提供真实目录与其别名 symlink。
    输出返回值：无；严格 bytes loader 前的 held 路径边界必须拒绝。
    """

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    archive_path, _payload, spec = _create_synthetic_archive(real)
    manifest_path = real / "image-manifest.json"
    _write_verified_v2_manifest(manifest_path, spec)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    with pytest.raises(OSWorldQcow2MaterializationError, match="nofollow"):
        materialize_osworld_qcow2_from_manifest(
            manifest_path=alias / manifest_path.name,
            archive_path=archive_path,
            output_parent=output_parent,
        )


def test_manifest_driven_entry_rejects_manifest_replacement_before_core_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """物化期间 manifest path entry 被替换必须在 core 返回前失败。

    输入参数：tmp_path 提供合成闭集；monkeypatch 在最终
        output SHA 返回后把 manifest 名称替换为相同字节的新 inode。
    输出返回值：无；始终 held 的 manifest FD/path continuity
        callback 必须失败，且 wrapper 不得在 core 后留下未保护 I/O。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    manifest_path = tmp_path / "image-manifest.json"
    _write_verified_v2_manifest(manifest_path, spec)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    original_verify = materializer_module._verify_readonly_image
    calls = {"count": 0}

    def _replace_after_final_hash(
        descriptor: int,
        candidate_spec: OSWorldQcow2MaterializationSpec,
        *,
        expected_nlink: int,
    ) -> tuple[int, ...]:
        """在第二次 output SHA 后以同字节新 inode 替换 manifest。"""

        identity = original_verify(
            descriptor,
            candidate_spec,
            expected_nlink=expected_nlink,
        )
        calls["count"] += 1
        if calls["count"] == 2:
            payload = manifest_path.read_bytes()
            manifest_path.rename(manifest_path.with_suffix(".held-original"))
            manifest_path.write_bytes(payload)
        return identity

    monkeypatch.setattr(
        materializer_module,
        "_verify_readonly_image",
        _replace_after_final_hash,
    )

    with pytest.raises(OSWorldQcow2MaterializationError, match="manifest.*漂移|连续性"):
        materialize_osworld_qcow2_from_manifest(
            manifest_path=manifest_path,
            archive_path=archive_path,
            output_parent=output_parent,
        )


def test_returned_capability_detects_replacement_in_python_finally_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """返回对象必须持有权威 FD，并识别最后 continuity 后的名称替换。

    输入参数：tmp_path 提供合成归档与私有输出目录；monkeypatch
        在核心最后一次路径 continuity 完成后、Python ``finally``
        首次关闭 FD 时，以不同内容的新 inode 替换最终目录项。
    输出返回值：无；返回对象的 held-FD 权威内容不得随 pathname
        替换而变化，``verify_current`` 必须报告 path drift；恢复原目录项
        后，测试从对象私有权威 FD 读取到的摘要仍必须是原始 payload。
    """

    archive_path, payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    final_path = output_parent / "Ubuntu.qcow2"
    held_original = output_parent / "Ubuntu.qcow2.held-original"
    replacement = b"replacement-after-final-continuity"
    original_continuity = materializer_module._verify_final_name_continuity
    original_close = os.close
    observation = {"continuity_calls": 0, "armed": False, "replaced": False}

    def _arm_after_last_continuity(
        parent_descriptor: int,
        output_name: str,
        expected_identity: tuple[int, ...],
    ) -> None:
        """转发 continuity，并在第三次成功校验后打开攻击窗口。"""

        original_continuity(parent_descriptor, output_name, expected_identity)
        observation["continuity_calls"] += 1
        if observation["continuity_calls"] == 3:
            observation["armed"] = True

    def _replace_before_first_finally_close(descriptor: int) -> None:
        """在 ``return`` 求值后的首个 close 前替换固定最终名。"""

        if observation["armed"] and not observation["replaced"]:
            final_path.rename(held_original)
            final_path.write_bytes(replacement)
            final_path.chmod(0o400)
            observation["replaced"] = True
        original_close(descriptor)

    monkeypatch.setattr(
        materializer_module,
        "_verify_final_name_continuity",
        _arm_after_last_continuity,
    )
    monkeypatch.setattr(os, "close", _replace_before_first_finally_close)

    result = materialize_osworld_qcow2(
        archive_path=archive_path,
        output_parent=output_parent,
        spec=spec,
    )
    monkeypatch.setattr(os, "close", original_close)

    assert observation == {
        "continuity_calls": 3,
        "armed": True,
        "replaced": True,
    }
    assert final_path.read_bytes() == replacement
    with pytest.raises(OSWorldQcow2MaterializationError, match="路径.*漂移|连续性"):
        result.verify_current()

    authority_payload = os.pread(result._final_descriptor, len(payload), 0)
    assert hashlib.sha256(authority_payload).hexdigest() == spec.output_sha256
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    result.close()


def test_returned_capability_detects_archive_replacement_after_last_core_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """返回 capability 必须延续 archive FD 与 pathname 的权威性。

    输入参数：tmp_path 提供合成归档/输出目录；monkeypatch 在核心
        第二次 archive return check 后、``finally`` 首次 close 时替换
        archive 目录项。
    输出返回值：无；旧实现会返回且无法发现 drift（RED）；修复后
        ``verify_current`` 必须拒绝替换，同时 held archive FD 的完整
        SHA 仍等于 recipe 固定摘要。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    held_archive = archive_path.with_suffix(".held-original")
    original_verify = materializer_module._verify_archive_return_identity
    original_close = os.close
    observation = {"checks": 0, "armed": False, "replaced": False}

    def _arm_after_last_archive_check(
        handle: object,
        expected_identity: tuple[int, ...],
    ) -> None:
        """转发 archive check，并在第二次成功后打开攻击窗口。"""

        original_verify(handle, expected_identity)
        observation["checks"] += 1
        if observation["checks"] == 2:
            observation["armed"] = True

    def _replace_before_first_finally_close(descriptor: int) -> None:
        """在最后 archive check 后的首个 close 前替换目录项。"""

        if observation["armed"] and not observation["replaced"]:
            archive_path.rename(held_archive)
            archive_path.write_bytes(b"replacement-archive-after-last-check")
            observation["replaced"] = True
        original_close(descriptor)

    monkeypatch.setattr(
        materializer_module,
        "_verify_archive_return_identity",
        _arm_after_last_archive_check,
    )
    monkeypatch.setattr(os, "close", _replace_before_first_finally_close)

    result = materialize_osworld_qcow2(
        archive_path=archive_path,
        output_parent=output_parent,
        spec=spec,
    )
    monkeypatch.setattr(os, "close", original_close)

    assert observation == {"checks": 2, "armed": True, "replaced": True}
    with pytest.raises(OSWorldQcow2MaterializationError, match="ZIP.*漂移|连续性"):
        result.verify_current()
    archive_payload = os.pread(
        result._archive_handle.leaf_descriptor,
        spec.archive_size,
        0,
    )
    assert hashlib.sha256(archive_payload).hexdigest() == spec.archive_sha256
    result.close()


def test_formal_capability_detects_manifest_replacement_in_return_finally_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正式 repo-root capability 必须延续固定 manifest 的 held 身份。

    输入参数：tmp_path 提供合成仓库/归档/输出目录；monkeypatch 在核心
        最后 output continuity 后、返回清理首个 close 时替换固定 manifest。
    输出返回值：无；返回对象必须持有原 manifest FD，轻量验证拒绝路径
        drift，原 held FD 字节摘要仍等于首次严格解析的 manifest。
    """

    archive_root = tmp_path / "archive"
    archive_root.mkdir(mode=0o700)
    archive_path, _payload, spec = _create_synthetic_archive(archive_root)
    repo_root = tmp_path / "repo"
    manifest_path = repo_root / "environments/osworld/image-manifest.json"
    manifest_path.parent.mkdir(parents=True)
    _write_verified_v2_manifest(manifest_path, spec)
    original_manifest = manifest_path.read_bytes()
    held_manifest = manifest_path.with_suffix(".held-original")
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    original_continuity = materializer_module._verify_final_name_continuity
    original_close = os.close
    observation = {"checks": 0, "armed": False, "replaced": False}

    def _arm_after_last_output_check(
        parent_descriptor: int,
        output_name: str,
        expected_identity: tuple[int, ...],
    ) -> None:
        """转发 output continuity，并在第三次成功后打开攻击窗口。"""

        original_continuity(parent_descriptor, output_name, expected_identity)
        observation["checks"] += 1
        if observation["checks"] == 3:
            observation["armed"] = True

    def _replace_before_first_finally_close(descriptor: int) -> None:
        """在正式 wrapper 返回清理窗口替换固定 manifest 名称。"""

        if observation["armed"] and not observation["replaced"]:
            manifest_path.rename(held_manifest)
            manifest_path.write_text("{}", encoding="utf-8")
            observation["replaced"] = True
        original_close(descriptor)

    monkeypatch.setattr(
        materializer_module,
        "_verify_final_name_continuity",
        _arm_after_last_output_check,
    )
    monkeypatch.setattr(os, "close", _replace_before_first_finally_close)

    result = materializer_module.materialize_osworld_qcow2_from_repo_root(
        repo_root=repo_root,
        archive_path=archive_path,
        output_parent=output_parent,
    )
    monkeypatch.setattr(os, "close", original_close)

    assert observation == {"checks": 3, "armed": True, "replaced": True}
    with pytest.raises(
        OSWorldQcow2MaterializationError,
        match="manifest.*漂移|连续性",
    ):
        result.verify_current()
    manifest_payload = os.pread(
        result._manifest_handle.leaf_descriptor,
        len(original_manifest),
        0,
    )
    assert (
        hashlib.sha256(manifest_payload).hexdigest()
        == hashlib.sha256(original_manifest).hexdigest()
    )
    owned_descriptors = (
        result._final_descriptor,
        *result._output_handle.descriptors,
        *result._archive_handle.descriptors,
        *result._manifest_handle.descriptors,
    )
    result.close()
    for descriptor in owned_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_materialized_capability_context_closes_every_owned_fd_and_rejects_reuse(
    tmp_path: Path,
) -> None:
    """上下文退出必须释放全部 owned FD，关闭后的 API 必须失败关闭。

    输入参数：tmp_path 提供合成归档与 owner-only 输出目录。
    输出返回值：无；上下文内轻量/完整验证均成功，退出后最终只读 FD
        与 held-path 链全部为 EBADF，重复 close 安全，所有身份/路径 API
        都拒绝把已释放的 capability 当作证据继续使用。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    result = materialize_osworld_qcow2(
        archive_path=archive_path,
        output_parent=output_parent,
        spec=spec,
    )
    final_descriptor = result._final_descriptor
    path_descriptors = tuple(result._output_handle.descriptors)
    archive_descriptors = tuple(result._archive_handle.descriptors)
    assert repr(result) == "MaterializedOSWorldQcow2()"
    with result as capability:
        capability.verify_current()
        capability.verify_full()
        assert capability.output_name == "Ubuntu.qcow2"

    assert result.closed is True
    for descriptor in (
        final_descriptor,
        *path_descriptors,
        *archive_descriptors,
    ):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    result.close()

    closed_calls = (
        lambda: result.image_path,
        lambda: result.output_name,
        lambda: result.sha256,
        lambda: result.size,
        result.verify_current,
        result.verify_full,
        result.__enter__,
    )
    for invoke in closed_calls:
        with pytest.raises(OSWorldQcow2MaterializationError, match="已关闭"):
            invoke()


def test_materialized_capability_finalizer_has_no_strong_self_cycle(
    tmp_path: Path,
) -> None:
    """泄漏兜底 finalizer 不得因强引用 result 而永远无法触发。

    输入参数：tmp_path 提供合成归档和私有输出目录。
    输出返回值：无；删除最后一个 result 强引用并执行 GC 后对象必须消失，
        final/output/archive 的全部 owned FD 都必须已关闭。该测试只验证
        泄漏防御，正式正确性仍依赖显式 ``with``/``close``。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)
    result = materialize_osworld_qcow2(
        archive_path=archive_path,
        output_parent=output_parent,
        spec=spec,
    )
    owned_descriptors = (
        result._final_descriptor,
        *result._output_handle.descriptors,
        *result._archive_handle.descriptors,
    )
    result_reference = weakref.ref(result)

    del result
    gc.collect()

    assert result_reference() is None
    for descriptor in owned_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_formal_cli_uses_only_repo_root_fixed_image_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """正式 CLI 只能从 repo-root 下固定相对位置取得 image manifest。

    输入参数：tmp_path 提供合成仓库、ZIP 与私有输出目录；capsys
        捕获正式发证 JSON。
    输出返回值：无；固定 ``environments/osworld/image-manifest.json``
        可驱动物化，JSON 只含固定名称、SHA 和大小，不泄漏宿主路径。
    """

    archive_root = tmp_path / "archive"
    archive_root.mkdir(mode=0o700)
    archive_path, _payload, spec = _create_synthetic_archive(archive_root)
    repo_root = tmp_path / "repo"
    fixed_manifest = repo_root / "environments/osworld/image-manifest.json"
    fixed_manifest.parent.mkdir(parents=True)
    _write_verified_v2_manifest(fixed_manifest, spec)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    exit_code = materializer_module.main(
        [
            "--repo-root",
            str(repo_root),
            "--archive",
            str(archive_path),
            "--output-parent",
            str(output_parent),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "output_name": "Ubuntu.qcow2",
        "sha256": spec.output_sha256,
        "size": spec.output_size,
    }
    assert str(output_parent) not in captured.out


def test_public_export_surface_contains_only_formal_repo_root_entry() -> None:
    """模块星号导出不得暴露任意 manifest 或操作者 spec 入口。

    输入参数：无；读取模块声明的公开导出闭集。
    输出返回值：无；只有固定 repo-root trust anchor 可以成为正式入口，
        低层 spec/from-manifest seam 即使保留测试用途也必须保持私有。
    """

    assert materializer_module.__all__ == ["materialize_osworld_qcow2_from_repo_root"]


def test_formal_cli_rejects_arbitrary_manifest_option(
    tmp_path: Path,
) -> None:
    """任意外部 manifest 参数不得进入正式 CLI trust anchor。

    输入参数：tmp_path 提供一个语义有效但不位于固定 repo 相对路径的
        manifest、合成 ZIP 与私有输出目录。
    输出返回值：无；argparse 必须拒绝 ``--manifest``，且不得发布 qcow2。
    """

    archive_path, _payload, spec = _create_synthetic_archive(tmp_path)
    arbitrary_manifest = tmp_path / "operator-selected.json"
    _write_verified_v2_manifest(arbitrary_manifest, spec)
    output_parent = tmp_path / "owned"
    output_parent.mkdir(mode=0o700)

    with pytest.raises(SystemExit) as error:
        materializer_module.main(
            [
                "--manifest",
                str(arbitrary_manifest),
                "--archive",
                str(archive_path),
                "--output-parent",
                str(output_parent),
            ]
        )

    assert error.value.code == 2
    assert not (output_parent / "Ubuntu.qcow2").exists()
