"""Evaluator-only pinned gold 显式预置流程的安全行为测试。"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
from typing import Any

import pytest

from paraguibench.runtime.gold_assets import (
    GoldAssetResolver,
    GoldFetchError,
    GoldIntegrityError,
    fetch_gold_assets,
    load_gold_asset_manifest,
)


LOGICAL_KEY = "osworld-gold:df67aebb-fb3a-44fd-b75b-51b6012df509:expected:0:v1"


class _FakeHTTPResponse:
    """模拟 ``urlopen`` 返回的有限二进制 HTTP response。"""

    def __init__(self, content: bytes, *, status: int = 200) -> None:
        """保存响应正文和公开状态码。

        输入参数：
            content：供 fetcher 流式读取的完整响应字节。
            status：``getcode`` 返回的 HTTP 状态码。
        输出返回值：
            无；内部使用 ``BytesIO`` 保存独立读取游标。
        """

        self._stream = io.BytesIO(content)
        self._status = status

    def read(self, size: int = -1) -> bytes:
        """按调用方请求的上限读取响应字节。

        输入参数：
            size：本次允许读取的最大字节数。
        输出返回值：
            当前游标后的至多 ``size`` 字节。
        """

        return self._stream.read(size)

    def getcode(self) -> int:
        """返回固定 HTTP 状态码。

        输入参数：
            无。
        输出返回值：
            构造时保存的整数状态码。
        """

        return self._status

    def __enter__(self) -> _FakeHTTPResponse:
        """进入 response 上下文并返回自身。

        输入参数：
            无。
        输出返回值：
            当前 response。
        """

        return self

    def __exit__(self, *_: object) -> None:
        """退出 response 上下文并关闭内存流。

        输入参数：
            _：context manager 传入的异常信息，本 fake 无需读取。
        输出返回值：
            无。
        """

        self._stream.close()


def _write_synthetic_manifest(
    tmp_path: Path,
    content: bytes,
    *,
    source_path: str = (
        "multi_apps/df67aebb-fb3a-44fd-b75b-51b6012df509/references.bib"
    ),
) -> Path:
    """写入与真实 015 provenance 一致的最小 pinned gold manifest。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
        content：用于固定 size 与 SHA-256 的预期下载字节。
        source_path：Hugging Face commit 下的安全相对源路径。
    输出返回值：
        可交给严格 loader 的 manifest 路径。
    """

    manifest = {
        "schema_version": 1,
        "manifest_id": "Operation-FileOperate-CombinationDocs-015-gold-v1",
        "distribution_policy": "download_only",
        "entries": [
            {
                "logical_key": LOGICAL_KEY,
                "source_locator": {
                    "provider": "huggingface_dataset",
                    "repository": "xlangai/ubuntu_osworld_file_cache",
                    "revision": "711e0811642364e7aa8f10a8918367d0b626d578",
                    "path": source_path,
                },
                "runtime_locator": {
                    "kind": "cache-relative-path",
                    "value": "blobs/0000",
                },
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "media_type": "application/x-bibtex",
                "license": {
                    "status": "verified",
                    "spdx_expression": "Apache-2.0",
                    "evidence_ref": (
                        "https://huggingface.co/datasets/"
                        "xlangai/ubuntu_osworld_file_cache"
                    ),
                    "distribution": "download_only",
                },
                "provenance": {
                    "source_benchmark": "OSWorld",
                    "source_task_id": "df67aebb-fb3a-44fd-b75b-51b6012df509",
                    "source_evaluator_id": "9f55fdb6-a749-4170-91a2-bebddd3492d7",
                    "expected_index": 0,
                    "source_contract_sha256": "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c",
                    "evidence_ref": (
                        "osworld:evaluator:"
                        "9f55fdb6-a749-4170-91a2-bebddd3492d7:expected:0"
                    ),
                },
            }
        ],
    }
    path = tmp_path / "gold-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_fetch_uses_immutable_hf_commit_url_and_installs_private_cache(
    tmp_path: Path,
) -> None:
    """验证显式 fetch 只访问固定 commit，并原子安装私有缓存。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 与缓存父目录。
    输出返回值：
        无；断言 URL、timeout、返回投影、权限和离线 resolver 全部闭合。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = load_gold_asset_manifest(
        _write_synthetic_manifest(
            tmp_path,
            content,
            source_path=(
                "multi_apps/df67aebb-fb3a-44fd-b75b-51b6012df509/reference file.bib"
            ),
        )
    )
    requests: list[tuple[str, float]] = []

    def opener(request: Any, *, timeout: float) -> _FakeHTTPResponse:
        """记录 fetcher 生成的固定 request 并返回可信字节。

        输入参数：
            request：应为无认证信息的 urllib ``Request``。
            timeout：显式有限网络超时。
        输出返回值：
            包含预期 gold 的 context-managed fake response。
        """

        requests.append((request.full_url, timeout))
        assert request.get_header("Authorization") is None
        return _FakeHTTPResponse(content)

    cache_root = tmp_path / "gold-cache"
    result = fetch_gold_assets(
        manifest,
        cache_root,
        opener=opener,
    )

    assert result.status.value == "AVAILABLE"
    assert result.requested_count == 1
    assert requests == [
        (
            "https://huggingface.co/datasets/"
            "xlangai/ubuntu_osworld_file_cache/resolve/"
            "711e0811642364e7aa8f10a8918367d0b626d578/"
            "multi_apps/df67aebb-fb3a-44fd-b75b-51b6012df509/"
            "reference%20file.bib",
            30.0,
        )
    ]
    target = cache_root / manifest.manifest_id / "blobs" / "0000"
    assert target.read_bytes() == content
    assert os.stat(cache_root).st_mode & 0o777 == 0o700
    assert os.stat(target.parent.parent).st_mode & 0o777 == 0o700
    assert os.stat(target.parent).st_mode & 0o777 == 0o700
    assert os.stat(target).st_mode & 0o777 == 0o600
    availability = GoldAssetResolver(
        manifest=manifest,
        cache_root=cache_root,
    ).verify_required((LOGICAL_KEY,))
    assert availability.requested_count == 1


def test_fetch_percent_encodes_literal_hash_in_pinned_source_filename(
    tmp_path: Path,
) -> None:
    """验证 source 文件名的字面 ``#`` 不会被解释为 URL fragment。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 与缓存目录。
    输出返回值：
        无；严格 loader 接受真实文件名，fetcher 只向包含
        ``%23`` 且不含 fragment 的固定 commit URL 发起请求。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = load_gold_asset_manifest(
        _write_synthetic_manifest(
            tmp_path,
            content,
            source_path=(
                "multi_apps/df67aebb-fb3a-44fd-b75b-51b6012df509/Invoice # 243729.pdf"
            ),
        )
    )
    observed_urls: list[str] = []

    def opener(request: Any, *, timeout: float) -> _FakeHTTPResponse:
        """记录经过逐段编码的匿名 HTTP 请求。

        输入参数：
            request：fetcher 构造的 urllib ``Request``。
            timeout：固定有限超时。
        输出返回值：
            包含预期字节的 fake response。
        """

        assert timeout == 30.0
        observed_urls.append(request.full_url)
        return _FakeHTTPResponse(content)

    fetch_gold_assets(manifest, tmp_path / "gold-cache", opener=opener)

    assert len(observed_urls) == 1
    assert observed_urls[0].endswith("/Invoice%20%23%20243729.pdf")
    assert "#" not in observed_urls[0]


def test_fetch_reuses_verified_cache_without_network(tmp_path: Path) -> None:
    """验证重复 fetch 对已经完整的条目执行离线幂等复用。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 与私有缓存目录。
    输出返回值：
        无；首次下载一次，第二次在网络不可用时仍直接通过离线校验。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = load_gold_asset_manifest(_write_synthetic_manifest(tmp_path, content))
    calls = 0

    def opener(_: Any, *, timeout: float) -> _FakeHTTPResponse:
        """只允许首次 provisioning 访问合成网络边界。

        输入参数：
            _：未读取的固定 commit request。
            timeout：fetcher 传入的有限超时。
        输出返回值：
            首次返回固定 gold；再次调用即使测试失败。
        """

        nonlocal calls
        calls += 1
        assert timeout == 30.0
        if calls > 1:
            raise RuntimeError("network must not be used for verified cache")
        return _FakeHTTPResponse(content)

    cache_root = tmp_path / "gold-cache"
    fetch_gold_assets(manifest, cache_root, opener=opener)
    result = fetch_gold_assets(manifest, cache_root, opener=opener)

    assert result.status.value == "AVAILABLE"
    assert result.requested_count == 1
    assert calls == 1


def test_fetch_keeps_published_blob_when_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证目录持久化失败不会删除已经完成字节校验的发布文件。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 与缓存目录。
        monkeypatch：仅在目标目录 descriptor 上注入 ``fsync`` 失败。
    输出返回值：
        无；fetch 报固定持久化错误，但新发布文件仍可由离线 resolver 校验。
    """

    expected = b"@article{expected}\n"
    manifest = load_gold_asset_manifest(_write_synthetic_manifest(tmp_path, expected))
    cache_root = tmp_path / "gold-cache"
    target = cache_root / manifest.manifest_id / "blobs" / "0000"
    target.parent.mkdir(parents=True, mode=0o700)
    os.chmod(cache_root, 0o700)
    os.chmod(target.parent.parent, 0o700)
    os.chmod(target.parent, 0o700)
    target.write_bytes(b"@article{corrupt_}\n")
    os.chmod(target, 0o600)
    original_fsync = os.fsync

    def failing_directory_fsync(descriptor: int) -> None:
        """只让目录 ``fsync`` 失败，保留临时文件同步与原子替换。

        输入参数：
            descriptor：gold fetcher 当前同步的文件或目录 descriptor。
        输出返回值：
            普通文件正常同步；目录始终抛出合成 ``OSError``。
        """

        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("synthetic directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", failing_directory_fsync)

    with pytest.raises(GoldFetchError):
        fetch_gold_assets(
            manifest,
            cache_root,
            opener=lambda *_args, **_kwargs: _FakeHTTPResponse(expected),
        )

    assert target.read_bytes() == expected
    availability = GoldAssetResolver(
        manifest=manifest,
        cache_root=cache_root,
    ).verify_required((LOGICAL_KEY,))
    assert availability.requested_count == 1


@pytest.mark.parametrize(
    "downloaded",
    [
        b"@article{expected}\nEXTRA",
        b"@article{wrong___}\n",
    ],
)
def test_fetch_rejects_wrong_or_oversized_bytes_without_publishing(
    tmp_path: Path,
    downloaded: bytes,
) -> None:
    """验证 size/SHA 不一致时不会留下正式文件或临时文件。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
        downloaded：长度超限或同长摘要错误的恶意响应正文。
    输出返回值：
        无；只抛固定完整性错误，缓存中不存在可被 runtime 使用的 blob。
    """

    expected = b"@article{expected}\n"
    manifest = load_gold_asset_manifest(_write_synthetic_manifest(tmp_path, expected))

    def opener(_: Any, *, timeout: float) -> _FakeHTTPResponse:
        """返回未通过固定字节身份校验的响应。

        输入参数：
            _：未使用的固定 request。
            timeout：fetcher 传入的有限超时。
        输出返回值：
            包含参数化错误字节的 fake response。
        """

        assert timeout == 30.0
        return _FakeHTTPResponse(downloaded)

    cache_root = tmp_path / "gold-cache"
    with pytest.raises(GoldIntegrityError) as caught:
        fetch_gold_assets(manifest, cache_root, opener=opener)

    rendered = f"{caught.value!s}|{caught.value!r}"
    assert str(caught.value) == "GOLD_CACHE_INTEGRITY_ERROR"
    assert str(cache_root) not in rendered
    target = cache_root / manifest.manifest_id / "blobs" / "0000"
    assert not target.exists()
    if target.parent.exists():
        assert tuple(target.parent.iterdir()) == ()


def test_fetch_maps_network_failure_to_fixed_error_without_metadata(
    tmp_path: Path,
) -> None:
    """验证网络失败不会回显 URL、路径、token 或底层异常详情。

    输入参数：
        tmp_path：pytest 提供的隔离 manifest 与缓存目录。
    输出返回值：
        无；公开异常严格为 ``GOLD_FETCH_ERROR``，且不发布缓存文件。
    """

    content = b"private-gold"
    manifest = load_gold_asset_manifest(_write_synthetic_manifest(tmp_path, content))

    def opener(_: Any, *, timeout: float) -> _FakeHTTPResponse:
        """模拟携带秘密详情的底层网络异常。

        输入参数：
            _：未读取的 request。
            timeout：必须为有限默认值。
        输出返回值：
            永不返回；直接抛出合成异常。
        """

        assert timeout == 30.0
        raise RuntimeError("secret-token at private.example/references.bib")

    cache_root = tmp_path / "gold-cache"
    with pytest.raises(GoldFetchError) as caught:
        fetch_gold_assets(manifest, cache_root, opener=opener)

    rendered = f"{caught.value!s}|{caught.value!r}"
    assert str(caught.value) == "GOLD_FETCH_ERROR"
    assert "secret-token" not in rendered
    assert "private.example" not in rendered
    assert "references.bib" not in rendered


def test_fetch_rejects_symlinked_cache_root_before_network(
    tmp_path: Path,
) -> None:
    """验证私有缓存路径含 symlink 时在下载前失败关闭。

    输入参数：
        tmp_path：pytest 提供的真实目录和 symlink 父目录。
    输出返回值：
        无；不调用 opener，避免把已下载的 gold 写入非预期位置。
    """

    content = b"@article{trusted}\n"
    manifest = load_gold_asset_manifest(_write_synthetic_manifest(tmp_path, content))
    real_root = tmp_path / "real-cache"
    real_root.mkdir(mode=0o700)
    linked_root = tmp_path / "linked-cache"
    linked_root.symlink_to(real_root, target_is_directory=True)
    calls = 0

    def opener(_: Any, *, timeout: float) -> _FakeHTTPResponse:
        """记录任何越过本地路径门禁的网络访问。

        输入参数：
            _：未使用 request。
            timeout：未使用超时。
        输出返回值：
            理论上不返回；调用本身即表示测试失败。
        """

        nonlocal calls
        calls += 1
        return _FakeHTTPResponse(content)

    with pytest.raises(GoldIntegrityError):
        fetch_gold_assets(manifest, linked_root, opener=opener)

    assert calls == 0
    assert tuple(real_root.iterdir()) == ()
