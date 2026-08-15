"""Pinned evaluator gold manifest 与离线 resolver 的行为测试。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

import pytest

from paraguibench.runtime.gold_assets import (
    GoldAssetResolver,
    GoldAssetManifest,
    GoldAvailabilityStatus,
    GoldIntegrityError,
    GoldReadError,
    GoldUnavailableError,
    GoldManifestError,
    fetch_gold_assets,
    load_gold_asset_manifest,
    load_gold_asset_manifest_bytes,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPO_ROOT
    / "benchmark"
    / "gold"
    / "manifests"
    / "Operation-FileOperate-CombinationDocs-015.json"
)
SCHEMA_PATH = REPO_ROOT / "benchmark" / "schemas" / "gold-asset-manifest-v1.schema.json"
LOGICAL_KEY = "osworld-gold:df67aebb-fb3a-44fd-b75b-51b6012df509:expected:0:v1"
SETTINGS_MANIFEST_PATH = (
    REPO_ROOT
    / "benchmark"
    / "gold"
    / "manifests"
    / "Operation-FileOperate-Settings-001.json"
)
SETTINGS_GOLD_KEY = "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v2"


def test_gold_manifest_bytes_loader_folds_recursive_json_to_fixed_error() -> None:
    """验证深度恶意 JSON 不会逸出 decoder ``RecursionError``。

    输入参数：无；构造大幅低于 1 MiB 但超过 Python JSON
        递归深度的 bytes payload。
    输出返回：公开 bytes loader 只抛固定脱敏
        ``GoldManifestError``，不回显 decoder 文本或 payload。
    """

    payload = b"[" * 1_200 + b"0" + b"]" * 1_200

    with pytest.raises(GoldManifestError) as caught:
        load_gold_asset_manifest_bytes(payload)

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


def _canonical_manifest_object() -> dict[str, object]:
    """返回仓库 gold manifest 的可变测试副本。

    输入参数：
        无；读取正式 CombinationDocs-015 manifest。
    输出返回值：
        只含 JSON 原语的新建字典，供单个行为测试定向变异。
    """

    loaded = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write_manifest(tmp_path: Path, value: object) -> Path:
    """在 pytest 隔离目录写入一份候选 manifest。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
        value：待 JSON 编码的候选 manifest 值。
    输出返回值：
        写入后的 manifest 绝对路径。
    """

    path = tmp_path / "gold-manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _replace_nested_value(
    root: dict[str, object],
    path: tuple[str | int, ...],
    value: object,
) -> None:
    """按 JSON 字段/索引路径替换一个测试值。

    输入参数：
        root：待变异的 manifest 字典。
        path：从顶层到目标的字段名或列表索引。
        value：写入目标位置的候选值。
    输出返回值：
        无；仅修改该次测试指定的一个字段。
    """

    current: object = root
    for component in path[:-1]:
        if isinstance(component, int):
            assert isinstance(current, list)
            current = current[component]
        else:
            assert isinstance(current, dict)
            current = current[component]
    final = path[-1]
    if isinstance(final, int):
        assert isinstance(current, list)
        current[final] = value
    else:
        assert isinstance(current, dict)
        current[final] = value


def _synthetic_manifest_for_content(
    tmp_path: Path,
    content: bytes,
) -> GoldAssetManifest:
    """生成一份字节身份与测试内容精确一致的 manifest。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
        content：将放入离线缓存的合成 gold 字节。
    输出返回值：
        经过公开 loader 完整验证的 ``GoldAssetManifest``。
    """

    raw = _canonical_manifest_object()
    _replace_nested_value(raw, ("entries", 0, "size"), len(content))
    _replace_nested_value(
        raw,
        ("entries", 0, "sha256"),
        hashlib.sha256(content).hexdigest(),
    )
    return load_gold_asset_manifest(_write_manifest(tmp_path, raw))


def _handcrafted_v1_manifest_with_settings_v2_key() -> GoldAssetManifest:
    """构造一份 loader 绝不会产生的 Settings v1/v2 混淆实例。

    输入参数：无；从两份正式清单取得 v1 容器与 Settings
        v2 key/provenance，再通过 ``dataclasses.replace`` 手工混合。
    输出返回：精确 ``GoldAssetManifest`` 类型、但违反 v1
        logical-key 语义的不可信 dataclass 实例。
    """

    downloaded = load_gold_asset_manifest(MANIFEST_PATH)
    derived = load_gold_asset_manifest(SETTINGS_MANIFEST_PATH)
    assert type(downloaded) is GoldAssetManifest
    return replace(
        downloaded,
        manifest_id="Operation-FileOperate-Settings-001-gold-v1",
        entries=(
            replace(
                downloaded.entries[0],
                logical_key=SETTINGS_GOLD_KEY,
                media_type="image/png",
                provenance=derived.entries[0].provenance,
            ),
        ),
    )


def _install_private_cache_blob(
    cache_root: Path,
    manifest: GoldAssetManifest,
    content: bytes,
) -> Path:
    """按 manifest runtime locator 建立私有离线缓存文件。

    输入参数：
        cache_root：resolver 使用的离线缓存根目录。
        manifest：包含单个合成 entry 的已校验 manifest。
        content：要写入的 gold 字节。
    输出返回值：
        仅供安全性测试继续变异的缓存文件路径。
    """

    entry = manifest.entries[0]
    target = cache_root / manifest.manifest_id / entry.runtime_locator.value
    target.parent.mkdir(parents=True, mode=0o700)
    current = cache_root
    os.chmod(current, 0o700)
    for part in (manifest.manifest_id, *Path(entry.runtime_locator.value).parts[:-1]):
        current = current / part
        os.chmod(current, 0o700)
    target.write_bytes(content)
    os.chmod(target, 0o600)
    return target


def test_combination_docs_015_gold_manifest_pins_verified_source() -> None:
    """验证 CombinationDocs-015 gold 绑定到不可变字节与来源。

    输入参数：
        无；读取仓库内独立 gold manifest。
    输出返回值：
        无；逻辑键、固定 revision、大小、摘要、媒体类型、
        来源 evaluator 与许可证据全部匹配时通过。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)

    assert manifest.manifest_id == ("Operation-FileOperate-CombinationDocs-015-gold-v1")
    assert manifest.distribution_policy == "download_only"
    assert len(manifest.entries) == 1
    entry = manifest.entries[0]
    assert entry.logical_key == LOGICAL_KEY
    assert entry.source_locator.revision == ("711e0811642364e7aa8f10a8918367d0b626d578")
    assert entry.size == 9_081
    assert entry.sha256 == (
        "056bde761437cad00b0207133f32ccbe6186decdbd621c6890c7b2c9ae373580"
    )
    assert entry.media_type == "application/x-bibtex"
    assert entry.provenance.source_task_id == ("df67aebb-fb3a-44fd-b75b-51b6012df509")
    assert entry.provenance.source_evaluator_id == (
        "9f55fdb6-a749-4170-91a2-bebddd3492d7"
    )
    assert entry.provenance.source_contract_sha256 == (
        "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c"
    )
    assert entry.license.spdx_expression == "Apache-2.0"
    assert entry.license.evidence_ref == (
        "https://huggingface.co/datasets/xlangai/ubuntu_osworld_file_cache"
    )


def test_resolver_rejects_handcrafted_v1_manifest_with_v2_logical_key(
    tmp_path: Path,
) -> None:
    """验证底层 resolver 不信任手工构造的 v1 dataclass。

    输入参数：tmp_path 为不应被创建的私有 cache；
        manifest 是 exact v1 类型，但携带 Settings v2 logical key。
    输出返回：无；构造边界抛固定 manifest 错误，不建立
        resolver 或 cache。
    """

    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(GoldManifestError) as caught:
        GoldAssetResolver(
            manifest=_handcrafted_v1_manifest_with_settings_v2_key(),
            cache_root=cache_root,
        )

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"
    assert not cache_root.exists()


def test_resolver_manifest_identity_rejects_bool_int_type_confusion(
    tmp_path: Path,
) -> None:
    """验证 resolver 身份比较不使用 Python 宽松 bool/int 等值。

    输入参数：tmp_path 为 resolver 仅保存但不访问的 cache；
        候选 manifest 仅把 provenance ``expected_index=0`` 替换为
        Python 中宽松等值的 ``False``。
    输出返回：无；完整精确类型身份校验必须返回
        ``False``，不创建 cache。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    assert type(manifest) is GoldAssetManifest
    drifted = replace(
        manifest,
        entries=(
            replace(
                manifest.entries[0],
                provenance=replace(
                    manifest.entries[0].provenance,
                    expected_index=False,
                ),
            ),
        ),
    )
    cache_root = tmp_path / "must-not-exist"
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)

    assert resolver.is_bound_to_manifest(drifted) is False
    assert not cache_root.exists()


def test_fetch_rejects_handcrafted_v1_manifest_before_network(
    tmp_path: Path,
) -> None:
    """验证显式 fetch 边界也重新校验手工 v1 dataclass。

    输入参数：tmp_path 为不应被创建的私有 cache；
        opener 是一旦被调用就使测试失败的网络边界。
    输出返回：无；v1/v2 logical-key 混淆在任何网络或
        cache I/O 前抛固定 manifest 错误。
    """

    opener_called = False

    def forbidden_opener(*_: object, **__: object) -> object:
        """标记并拒绝本测试中的任何网络访问。

        输入参数：_ / __ 接收 production opener 的位置与关键字参数。
        输出返回：无；本函数总是抛出断言错误。
        """

        nonlocal opener_called
        opener_called = True
        raise AssertionError("network boundary must not be reached")

    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(GoldManifestError) as caught:
        fetch_gold_assets(
            _handcrafted_v1_manifest_with_settings_v2_key(),
            cache_root,
            opener=forbidden_opener,
        )

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"
    assert opener_called is False
    assert not cache_root.exists()


def test_gold_manifest_schema_declares_closed_objects() -> None:
    """验证公开 JSON Schema 与 loader 一样对每层 object 失败关闭。

    输入参数：
        无；读取仓库内 gold manifest v1 schema。
    输出返回值：
        无；顶层和四个嵌套 contract 都声明完整 required 闭集
        与 ``additionalProperties=false``。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema_version",
        "manifest_id",
        "distribution_policy",
        "entries",
    }
    definitions = schema["$defs"]
    for name in ("entry", "source_locator", "runtime_locator", "license", "provenance"):
        assert definitions[name]["additionalProperties"] is False
        assert set(definitions[name]["required"]) == set(
            definitions[name]["properties"]
        )


def test_gold_manifest_loader_rejects_symlinked_manifest(tmp_path: Path) -> None:
    """验证 manifest loader 本身也不跟随被替换的符号链接。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；即使 symlink 指向完全合法的 manifest，loader 也在
        JSON 解码前以固定 manifest 错误失败。
    """

    linked = tmp_path / "linked-manifest.json"
    linked.symlink_to(MANIFEST_PATH)

    with pytest.raises(GoldManifestError) as caught:
        load_gold_asset_manifest(linked)

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


def test_gold_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    """验证 manifest 任一层的未知字段都不会被静默忽略。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；在条目中添加未定义字段后，loader 以固定
        ``GOLD_MANIFEST_INVALID`` 代码失败。
    """

    raw = _canonical_manifest_object()
    entries = raw["entries"]
    assert isinstance(entries, list) and isinstance(entries[0], dict)
    entries[0]["future_field"] = "must-not-be-ignored"

    with pytest.raises(GoldManifestError) as caught:
        load_gold_asset_manifest(_write_manifest(tmp_path, raw))

    assert caught.value.code == "GOLD_MANIFEST_INVALID"
    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


def test_gold_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    """验证重复 JSON key 不会通过后值覆盖前值改写契约。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；顶层出现重复 ``manifest_id`` 时以固定错误失败。
    """

    duplicate = MANIFEST_PATH.read_text(encoding="utf-8").replace(
        '  "manifest_id": "Operation-FileOperate-CombinationDocs-015-gold-v1",',
        (
            '  "manifest_id": '
            '"Operation-FileOperate-CombinationDocs-015-gold-v1",\n'
            '  "manifest_id": "shadowed",'
        ),
        1,
    )
    candidate = tmp_path / "duplicate.json"
    candidate.write_text(duplicate, encoding="utf-8")

    with pytest.raises(GoldManifestError) as caught:
        load_gold_asset_manifest(candidate)

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", "main"),
        ("revision", "A" * 40),
        ("path", "../references.bib"),
        ("path", "/absolute/references.bib"),
        ("path", r"multi_apps\references.bib"),
        ("path", "multi_apps/%2e%2e/references.bib"),
        ("path", "multi_apps/references.bib?token=secret"),
        ("path", "multi_apps/references.bib\x00hidden"),
    ],
)
def test_gold_manifest_rejects_mutable_or_injected_source_locator(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    """验证 source locator 只能使用固定 commit 和安全 POSIX 路径。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
        field：本例变异的 source locator 字段。
        value：可变 revision，或包含穿越、URL/编码注入的路径。
    输出返回值：
        无；全部候选在访问缓存或网络前以固定错误失败。
    """

    raw = _canonical_manifest_object()
    entries = raw["entries"]
    assert isinstance(entries, list) and isinstance(entries[0], dict)
    source = entries[0]["source_locator"]
    assert isinstance(source, dict)
    source[field] = value

    with pytest.raises(GoldManifestError) as caught:
        load_gold_asset_manifest(_write_manifest(tmp_path, raw))

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "section_name",
    ["source_locator", "runtime_locator", "license", "provenance"],
)
def test_gold_manifest_rejects_unknown_nested_fields(
    tmp_path: Path,
    section_name: str,
) -> None:
    """验证所有嵌套 contract 均使用严格字段闭集。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
        section_name：待注入未知字段的嵌套 contract 名称。
    输出返回值：
        无；每个嵌套对象的未知字段均映射为同一公开错误。
    """

    raw = _canonical_manifest_object()
    entries = raw["entries"]
    assert isinstance(entries, list) and isinstance(entries[0], dict)
    section = entries[0][section_name]
    assert isinstance(section, dict)
    section["unknown"] = "private-value"

    with pytest.raises(GoldManifestError) as caught:
        load_gold_asset_manifest(_write_manifest(tmp_path, raw))

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    [
        (("schema_version",), 2),
        (("schema_version",), True),
        (("manifest_id",), "../gold"),
        (("distribution_policy",), "redistribute"),
        (("entries", 0, "logical_key"), "gold:mutable"),
        (("entries", 0, "runtime_locator", "kind"), "absolute-path"),
        (("entries", 0, "runtime_locator", "value"), "../escape"),
        (("entries", 0, "runtime_locator", "value"), "blobs/#fragment"),
        (("entries", 0, "size"), True),
        (("entries", 0, "size"), 0),
        (("entries", 0, "size"), 536_870_913),
        (("entries", 0, "sha256"), "A" * 64),
        (("entries", 0, "media_type"), "text/html"),
        (("entries", 0, "license", "status"), "assumed"),
        (("entries", 0, "license", "evidence_ref"), "http://example.test"),
        (("entries", 0, "provenance", "expected_index"), 1),
        (("entries", 0, "provenance", "source_task_id"), "not-a-uuid"),
        (("entries", 0, "provenance", "source_contract_sha256"), "short"),
    ],
)
def test_gold_manifest_rejects_invalid_identity_and_metadata(
    tmp_path: Path,
    field_path: tuple[str | int, ...],
    invalid_value: object,
) -> None:
    """验证 gold 身份、资源上限、媒体和追溯字段必须强类型匹配。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
        field_path：本例定向变异的 JSON 字段路径。
        invalid_value：错误类型、越界值或与 logical key 矛盾的值。
    输出返回值：
        无；任一契约违反都以固定 manifest 错误失败。
    """

    raw = _canonical_manifest_object()
    _replace_nested_value(raw, field_path, invalid_value)

    with pytest.raises(GoldManifestError) as caught:
        load_gold_asset_manifest(_write_manifest(tmp_path, raw))

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


def test_offline_resolver_opens_verified_bounded_gold(tmp_path: Path) -> None:
    """验证 resolver 只从私有缓存返回已校验的可 seek 字节流。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；返回流与 manifest 固定字节完全相同，且可供
        evaluator 重复 seek；全过程无任何网络依赖。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = _synthetic_manifest_for_content(tmp_path, content)
    cache_root = tmp_path / "gold-cache"
    _install_private_cache_blob(cache_root, manifest, content)
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)

    with resolver.open_verified(
        LOGICAL_KEY,
        max_bytes=len(content),
        expected_media_types=frozenset({"application/x-bibtex"}),
    ) as stream:
        assert stream.read() == content
        stream.seek(0)
        assert stream.read(8) == content[:8]


@pytest.mark.parametrize("failure_operation", ["create", "write", "seek"])
def test_offline_resolver_maps_snapshot_io_failures_to_fixed_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_operation: str,
) -> None:
    """验证临时快照创建、写入或回卷失败均使用统一读取错误。

    输入参数：
        tmp_path：pytest 提供的合法私有 gold 缓存。
        monkeypatch：替换 ``SpooledTemporaryFile`` 的合成 I/O 边界。
        failure_operation：本例注入失败的 create、write 或 seek 操作。
    输出返回值：
        无；底层 ``OSError`` 被固定映射为 ``GOLD_READ_ERROR``。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = _synthetic_manifest_for_content(tmp_path, content)
    cache_root = tmp_path / "gold-cache"
    _install_private_cache_blob(cache_root, manifest, content)

    class FailingSnapshot:
        """只在指定快照操作抛出合成 ``OSError`` 的最小替身。"""

        def write(self, value: bytes) -> int:
            """写入快照或注入写入失败。

            输入参数：
                value：resolver 已读取并完成计数的 gold 分块。
            输出返回值：
                未注入写入失败时返回完整写入长度。
            """

            if failure_operation == "write":
                raise OSError("synthetic snapshot write failure")
            return len(value)

        def seek(self, offset: int) -> int:
            """回卷快照或注入 seek 失败。

            输入参数：
                offset：resolver 在校验完成后请求的固定零偏移。
            输出返回值：
                未注入 seek 失败时返回该偏移。
            """

            if failure_operation == "seek":
                raise OSError("synthetic snapshot seek failure")
            return offset

        def close(self) -> None:
            """关闭合成快照。

            输入参数：无。
            输出返回值：无。
            """

    def snapshot_factory(**_: object) -> FailingSnapshot:
        """建立指定行为的临时快照替身。

        输入参数：
            _：生产代码传入但测试无需读取的 max_size 与 mode。
        输出返回值：
            write/seek 可定向失败的合成快照。
        """

        if failure_operation == "create":
            raise OSError("synthetic snapshot create failure")
        return FailingSnapshot()

    monkeypatch.setattr(
        "paraguibench.runtime.gold_assets.tempfile.SpooledTemporaryFile",
        snapshot_factory,
    )
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)

    with pytest.raises(GoldReadError) as caught:
        with resolver.open_verified(
            LOGICAL_KEY,
            max_bytes=len(content),
            expected_media_types=frozenset({"application/x-bibtex"}),
        ):
            pass

    assert str(caught.value) == "GOLD_READ_ERROR"


def test_offline_resolver_rejects_symlinked_cache_file(tmp_path: Path) -> None:
    """验证摘要正确的 symlink 也不能绕过离线缓存边界。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；即使 symlink 指向字节完全匹配的文件，resolver 仍以
        固定完整性错误失败，且不返回流。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = _synthetic_manifest_for_content(tmp_path, content)
    cache_root = tmp_path / "gold-cache"
    target = _install_private_cache_blob(cache_root, manifest, content)
    outside = tmp_path / "outside-gold.bib"
    outside.write_bytes(content)
    os.chmod(outside, 0o600)
    target.unlink()
    target.symlink_to(outside)
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)

    with pytest.raises(GoldIntegrityError) as caught:
        with resolver.open_verified(
            LOGICAL_KEY,
            max_bytes=len(content),
            expected_media_types=frozenset({"application/x-bibtex"}),
        ):
            pass

    assert str(caught.value) == "GOLD_CACHE_INTEGRITY_ERROR"


def test_offline_resolver_rejects_symlinked_cache_ancestor(
    tmp_path: Path,
) -> None:
    """验证 runtime locator 路径链中任一 symlink 目录都失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；中间 ``blobs`` 目录被替换为指向匹配内容的
        symlink 后，resolver 仍返回固定完整性错误。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = _synthetic_manifest_for_content(tmp_path, content)
    cache_root = tmp_path / "gold-cache"
    target = _install_private_cache_blob(cache_root, manifest, content)
    target.unlink()
    target.parent.rmdir()
    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir(mode=0o700)
    outside = outside_directory / "0000"
    outside.write_bytes(content)
    os.chmod(outside, 0o600)
    target.parent.symlink_to(outside_directory, target_is_directory=True)
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)

    with pytest.raises(GoldIntegrityError):
        with resolver.open_verified(
            LOGICAL_KEY,
            max_bytes=len(content),
            expected_media_types=frozenset({"application/x-bibtex"}),
        ):
            pass


def test_offline_resolver_verifies_required_key_set(tmp_path: Path) -> None:
    """验证 preflight 只返回状态与计数，不暴露 gold 元数据。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；指定 logical key 通过离线完整性校验后，返回
        ``AVAILABLE`` 和数量，且公开对象不含 URL/路径/摘要。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = _synthetic_manifest_for_content(tmp_path, content)
    cache_root = tmp_path / "gold-cache"
    _install_private_cache_blob(cache_root, manifest, content)
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)

    result = resolver.verify_required((LOGICAL_KEY,))

    assert result.status is GoldAvailabilityStatus.AVAILABLE
    assert result.requested_count == 1
    assert set(result.__dataclass_fields__) == {"status", "requested_count"}


def test_offline_resolver_maps_invalid_unhashable_key_to_fixed_error(
    tmp_path: Path,
) -> None:
    """验证不可哈希的恶意 key 不会泄漏底层 Python 异常。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；传入 list 而非字符串时，公开错误仍仅为
        ``GOLD_MANIFEST_INVALID``，不出现 ``unhashable`` 详情。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    resolver = GoldAssetResolver(
        manifest=manifest,
        cache_root=tmp_path / "gold-cache",
    )

    with pytest.raises(GoldManifestError) as caught:
        with resolver.open_verified(
            ["private-key"],  # type: ignore[arg-type]
            max_bytes=10_000,
            expected_media_types=frozenset({"application/x-bibtex"}),
        ):
            pass

    assert str(caught.value) == "GOLD_MANIFEST_INVALID"


def test_resolver_error_does_not_disclose_gold_metadata(tmp_path: Path) -> None:
    """验证 resolver 的公开异常不携带 URL、路径、摘要、内容或 token。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；缓存缺失时，``str`` 与 ``repr`` 仅暴露固定代码，
        不包含 manifest 元数据或注入的私密标记。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    private_cache = tmp_path / "private-token-cache"
    resolver = GoldAssetResolver(manifest=manifest, cache_root=private_cache)

    with pytest.raises(GoldUnavailableError) as caught:
        with resolver.open_verified(
            LOGICAL_KEY,
            max_bytes=10_000,
            expected_media_types=frozenset({"application/x-bibtex"}),
        ):
            pass

    rendered = f"{caught.value!s}|{caught.value!r}"
    entry = manifest.entries[0]
    forbidden = (
        str(private_cache),
        entry.source_locator.path,
        entry.source_locator.repository,
        entry.sha256,
        entry.license.evidence_ref,
        "private-token",
        "references.bib",
    )
    assert str(caught.value) == "GOLD_NOT_PROVISIONED"
    assert all(value not in rendered for value in forbidden)


def test_offline_resolver_rejects_hard_linked_cache_file(tmp_path: Path) -> None:
    """验证多链接普通文件不会被当作 evaluator 私有 gold。

    输入参数：
        tmp_path：pytest 提供的隔离目录。
    输出返回值：
        无；缓存文件存在第二个 hard link 时，即使内容和
        摘要匹配，仍返回固定完整性错误。
    """

    content = b"@article{trusted, title={Pinned Gold}}\n"
    manifest = _synthetic_manifest_for_content(tmp_path, content)
    cache_root = tmp_path / "gold-cache"
    target = _install_private_cache_blob(cache_root, manifest, content)
    os.link(target, tmp_path / "second-link")
    resolver = GoldAssetResolver(manifest=manifest, cache_root=cache_root)

    with pytest.raises(GoldIntegrityError):
        with resolver.open_verified(
            LOGICAL_KEY,
            max_bytes=len(content),
            expected_media_types=frozenset({"application/x-bibtex"}),
        ):
            pass
