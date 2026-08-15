"""固定版本外部资产清单与本地缓存验证测试。"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from paraguibench.runtime.assets import (
    AssetFile,
    AssetFetchError,
    AssetManifest,
    AssetManifestError,
    AssetSource,
    ResolvedTaskAssets,
    TaskAssetMode,
    fetch_asset_manifest,
    load_asset_manifest,
    load_asset_manifest_bytes,
    resolve_task_assets,
    verify_asset_directory,
)


def test_asset_manifest_bytes_loader_parses_one_immutable_snapshot() -> None:
    """验证调用方可从同一不可变 bytes 快照完成全部资产契约解析。

    输入参数：
        无；构造一份最小但完整的固定 revision manifest 原始字节。
    输出返回值：
        无；返回的 ``AssetManifest`` 必须保留来源与文件全部字段，不再要求
        第二次按路径读取，也不接受预解析可变 object 作为信任边界。
    """

    payload = json.dumps(
        {
            "schema_version": 1,
            "asset_set_id": "snapshot-assets",
            "source": {
                "provider": "huggingface_dataset",
                "repository": "example/snapshot-assets",
                "revision": "a" * 40,
                "base_path": "dataset/snapshot-assets",
                "license_status": "unverified",
            },
            "distribution_policy": "download_only",
            "files": [
                {
                    "path": "input.bin",
                    "size": 7,
                    "sha256": "b" * 64,
                    "media_type": "application/octet-stream",
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")

    manifest = load_asset_manifest_bytes(payload)

    assert manifest.asset_set_id == "snapshot-assets"
    assert manifest.source.revision == "a" * 40
    assert manifest.files == (
        AssetFile(
            path="input.bin",
            size=7,
            sha256="b" * 64,
            media_type="application/octet-stream",
        ),
    )


def test_asset_manifest_bytes_loader_rejects_duplicate_json_fields() -> None:
    """验证同一 bytes 快照中的重复字段不能被 JSON 后值静默覆盖。

    输入参数：
        无；构造顶层 ``schema_version`` 重复但其余字段合法的 JSON bytes。
    输出返回值：
        无；loader 必须失败关闭，避免两套解析器对同一 manifest 产生不同
        机器身份解释。
    """

    payload = (
        b'{"schema_version":1,"schema_version":1,'
        b'"asset_set_id":"duplicate-fields",'
        b'"source":{"provider":"huggingface_dataset",'
        b'"repository":"example/assets",'
        b'"revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"base_path":"dataset/assets",'
        b'"license_status":"unverified"},'
        b'"distribution_policy":"download_only",'
        b'"files":[{"path":"input.bin","size":1,'
        b'"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]}'
    )

    with pytest.raises(AssetManifestError):
        load_asset_manifest_bytes(payload)


def test_asset_manifest_path_loader_rejects_symlinked_manifest(
    tmp_path: Path,
) -> None:
    """验证路径入口不会跟随可替换 symlink 后再次读取另一份 manifest。

    输入参数：
        tmp_path：pytest 提供的隔离目录，用于真实文件与符号链接别名。
    输出返回值：
        无；最终路径是 symlink 时必须在 JSON 解析前失败关闭。
    """

    real_manifest = tmp_path / "real.json"
    real_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "real-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/assets",
                    "revision": "a" * 40,
                    "base_path": "dataset/assets",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "input.bin",
                        "size": 1,
                        "sha256": "b" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    alias = tmp_path / "alias.json"
    alias.symlink_to(real_manifest)

    with pytest.raises(AssetManifestError):
        load_asset_manifest(alias)


def test_asset_manifest_path_loader_rejects_oversized_file_before_parse(
    tmp_path: Path,
) -> None:
    """验证超过 4 MiB 的 sparse manifest 不会先整体读入内存。

    输入参数：
        tmp_path：pytest 隔离目录，用于创建 4 MiB+1 字节的稀疏普通文件。
    输出返回值：
        无；路径 loader 依据 held descriptor 的 ``fstat`` 大小立即失败关闭。
    """

    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as file:
        file.truncate(4 * 1024 * 1024 + 1)

    with pytest.raises(AssetManifestError):
        load_asset_manifest(oversized)


def test_zero_asset_tasks_accept_missing_or_empty_legacy_declaration(
    tmp_path: Path,
) -> None:
    """验证无资产任务不需要伪造空 manifest。

    输入参数：
        tmp_path：pytest 提供的仓库根占位目录。
    输出返回值：
        无；缺失 ``prepare_script_path`` 与显式空字符串都解析成同一个
        ``NONE`` 资产模式，且不产生 manifest。
    """

    missing = resolve_task_assets(tmp_path, {"task_id": "task-missing"})
    explicitly_empty = resolve_task_assets(
        tmp_path,
        {
            "task_id": "task-empty",
            "prepare_script_path": "",
        },
    )

    assert missing.mode is TaskAssetMode.NONE
    assert missing.manifest is None
    assert explicitly_empty == missing


@pytest.mark.parametrize(
    "task",
    [
        {"task_id": "task-empty-manifest", "asset_manifest": ""},
        {"task_id": "task-invalid-manifest", "asset_manifest": 7},
        {
            "task_id": "task-conflicting-assets",
            "asset_manifest": "benchmark/assets/task.json",
            "prepare_script_path": "legacy/script.py",
        },
        {"task_id": "task-invalid-legacy", "prepare_script_path": 7},
    ],
)
def test_task_asset_resolver_rejects_malformed_explicit_declarations(
    tmp_path: Path,
    task: dict[str, object],
) -> None:
    """验证损坏的显式资产字段不能静默降级为零资产模式。

    输入参数：
        tmp_path：pytest 提供的仓库根占位目录。
        task：空 manifest、错误类型或新旧字段冲突的 canonical task。
    输出返回值：
        无；统一解析器失败关闭，不创建缓存目录或伪造 ``NONE``。
    """

    with pytest.raises(AssetManifestError, match="资产|asset"):
        resolve_task_assets(tmp_path, task)

    assert not (tmp_path / "cache").exists()


def test_resolved_task_assets_enforces_mode_manifest_invariants() -> None:
    """验证公开资产解析结果不能被伪造成相互矛盾的 mode/manifest 组合。

    输入参数：
        无；构造一个不执行 I/O 的最小合法 AssetManifest。
    输出返回值：
        无；字符串 mode、NONE 携带 manifest、pinned 缺 manifest 均在数据类型
        构造阶段失败，所有 doctor/environment 消费者共享同一不变量。
    """

    manifest = AssetManifest(
        asset_set_id="synthetic-assets",
        source=AssetSource(
            provider="huggingface_dataset",
            repository="example/assets",
            revision="a" * 40,
            base_path="dataset/task",
            license_status="unverified",
        ),
        distribution_policy="download_only",
        files=(AssetFile(path="paper.pdf", size=1, sha256="b" * 64),),
    )

    with pytest.raises(TypeError, match="mode"):
        ResolvedTaskAssets(mode="none", manifest=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="manifest"):
        ResolvedTaskAssets(mode=TaskAssetMode.NONE, manifest=manifest)
    with pytest.raises(ValueError, match="manifest"):
        ResolvedTaskAssets(
            mode=TaskAssetMode.PINNED_DOWNLOAD_MANIFEST,
            manifest=None,
        )


def test_task_asset_resolver_loads_repository_pinned_manifest(
    tmp_path: Path,
) -> None:
    """验证统一解析器保留固定下载 manifest 的既有语义。

    输入参数：
        tmp_path：pytest 提供的合成仓库根目录。
    输出返回值：
        无；仓库内普通 JSON 文件被严格加载为 pinned 模式，且来源 revision
        与文件摘要没有被零资产分支吞掉。
    """

    manifest_path = tmp_path / "benchmark" / "assets" / "task.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "task-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/benchmark-assets",
                    "revision": "c" * 40,
                    "base_path": "dataset/task-assets",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "paper.pdf",
                        "size": 1,
                        "sha256": "d" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_task_assets(
        tmp_path,
        {
            "task_id": "task-pinned",
            "asset_manifest": "benchmark/assets/task.json",
        },
    )

    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == "task-assets"
    assert resolved.manifest.source.revision == "c" * 40


@pytest.mark.parametrize("unknown_scope", ["manifest", "source", "file"])
def test_asset_manifest_rejects_unknown_fields_at_every_object_level(
    tmp_path: Path,
    unknown_scope: str,
) -> None:
    """验证固定资产 manifest 在每个 object 层级都失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 目录。
        unknown_scope：注入额外字段的顶层、source 或 file 层级。
    输出返回值：
        无；loader 对任何未声明字段均抛出 ``AssetManifestError``。
    """

    manifest: dict[str, object] = {
        "schema_version": 1,
        "asset_set_id": "strict-assets",
        "source": {
            "provider": "huggingface_dataset",
            "repository": "example/benchmark-assets",
            "revision": "a" * 40,
            "base_path": "dataset/task",
            "license_status": "unverified",
        },
        "distribution_policy": "download_only",
        "files": [
            {
                "path": "paper.pdf",
                "size": 1,
                "sha256": "b" * 64,
                "media_type": "application/pdf",
            }
        ],
    }
    if unknown_scope == "manifest":
        manifest["unexpected"] = True
    elif unknown_scope == "source":
        source = manifest["source"]
        assert isinstance(source, dict)
        source["unexpected"] = True
    else:
        files = manifest["files"]
        assert isinstance(files, list)
        first_file = files[0]
        assert isinstance(first_file, dict)
        first_file["unexpected"] = True
    manifest_path = tmp_path / "strict-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AssetManifestError, match="未知字段"):
        load_asset_manifest(manifest_path)


@pytest.mark.parametrize(
    "asset_set_id",
    ["../escape", "/absolute", "nested/name", r"nested\name", ".", ".."],
)
def test_asset_manifest_rejects_cache_escaping_asset_set_id(
    tmp_path: Path,
    asset_set_id: str,
) -> None:
    """验证资产集合 ID 只能作为缓存根内的单层安全目录名。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 目录。
        asset_set_id：绝对、父目录、嵌套或特殊目录形式的恶意集合 ID。
    输出返回值：
        无；manifest loader 在任何下载或缓存写入前失败关闭。
    """

    manifest_path = tmp_path / "unsafe-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": asset_set_id,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/benchmark-assets",
                    "revision": "a" * 40,
                    "base_path": "dataset/task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [{"path": "paper.pdf", "size": 1, "sha256": "b" * 64}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AssetManifestError, match="asset_set_id"):
        load_asset_manifest(manifest_path)


def test_asset_directory_verification_checks_size_hash_and_exact_file_set(
    tmp_path: Path,
) -> None:
    """验证缓存目录必须逐文件满足大小、哈希与闭集契约。

    输入参数：
        tmp_path：pytest 提供的隔离临时目录。
    输出返回值：
        无；正确目录通过，篡改或多余文件均产生结构化失败项。
    """

    content = b"fixed asset content"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "synthetic-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/benchmark-assets",
                    "revision": "a" * 40,
                    "base_path": "benchmark_dataset/synthetic",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "paper.txt",
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / "paper.txt").write_bytes(content)
    manifest = load_asset_manifest(manifest_path)

    valid = verify_asset_directory(manifest, cache_root)
    assert valid.ok is True

    (cache_root / "paper.txt").write_bytes(b"tampered")
    (cache_root / "unexpected.txt").write_text("extra", encoding="utf-8")
    invalid = verify_asset_directory(manifest, cache_root)
    assert invalid.ok is False
    assert invalid.size_mismatch == ("paper.txt",)
    assert invalid.unexpected == ("unexpected.txt",)


def test_asset_fetch_uses_pinned_revision_and_atomic_private_file(
    tmp_path: Path,
) -> None:
    """验证下载 URL 固定 revision，且缓存以私有权限原子提交。

    输入参数：
        tmp_path：pytest 提供的隔离临时目录。
    输出返回值：
        无；fake opener 捕获 URL，最终缓存通过哈希验证且不存在临时残留。
    """

    content = b"downloaded immutable asset"
    revision = "b" * 40
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "synthetic-download",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/benchmark-assets",
                    "revision": revision,
                    "base_path": "dataset/task-one",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "paper.pdf",
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    requested_urls: list[str] = []

    def fake_open(url: str) -> io.BytesIO:
        """返回合成文件流并记录不含凭据的请求 URL。

        输入参数：
            url：下载器构造的固定版本公开 URL。
        输出返回值：
            支持上下文管理器的内存字节流。
        """

        requested_urls.append(url)
        return io.BytesIO(content)

    manifest = load_asset_manifest(manifest_path)
    cache_root = tmp_path / "cache"
    result = fetch_asset_manifest(
        manifest,
        cache_root,
        opener=fake_open,
    )

    assert result.ok is True
    assert requested_urls == [
        (
            "https://huggingface.co/datasets/example/benchmark-assets/"
            f"resolve/{revision}/dataset/task-one/paper.pdf"
        )
    ]
    assert (cache_root / "paper.pdf").read_bytes() == content
    assert os.stat(cache_root).st_mode & 0o777 == 0o700
    assert os.stat(cache_root / "paper.pdf").st_mode & 0o777 == 0o600
    assert not list(cache_root.rglob("*.partial"))


def test_asset_fetch_rejects_symlink_in_cache_root_ancestor(
    tmp_path: Path,
) -> None:
    """验证缓存根的祖先符号链接不能把下载重定向到根外。

    输入参数：
        tmp_path：pytest 提供的隔离缓存和外部诱饵目录。
    输出返回值：
        无；fetch 在打开下载源之前失败关闭，外部目录保持为空。
    """

    content = b"immutable"
    manifest = AssetManifest(
        asset_set_id="synthetic-assets",
        source=AssetSource(
            provider="huggingface_dataset",
            repository="example/assets",
            revision="a" * 40,
            base_path="dataset/task",
            license_status="unverified",
        ),
        distribution_policy="download_only",
        files=(
            AssetFile(
                path="paper.pdf",
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            ),
        ),
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(outside, target_is_directory=True)
    opened = False

    def fake_open(_url: str) -> io.BytesIO:
        """记录是否在路径门禁前错误打开了网络源。

        输入参数：
            _url：本测试不使用的固定版本 URL。
        输出返回值：
            内存字节流；安全实现不应调用本函数。
        """

        nonlocal opened
        opened = True
        return io.BytesIO(content)

    with pytest.raises(AssetFetchError, match="符号链接"):
        fetch_asset_manifest(
            manifest,
            cache_link / manifest.asset_set_id,
            opener=fake_open,
        )

    assert opened is False
    assert list(outside.iterdir()) == []
