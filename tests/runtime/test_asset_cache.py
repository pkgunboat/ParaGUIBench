"""固定版本外部资产清单与本地缓存验证测试。"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

from paraguibench.runtime.assets import (
    fetch_asset_manifest,
    load_asset_manifest,
    verify_asset_directory,
)


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
