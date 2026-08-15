"""Settings-001 evaluator-only derived gold 的严格运行时合同测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

import pytest

from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    GoldAssetResolver,
    GoldFetchError,
    GoldManifestError,
    fetch_gold_assets,
    load_gold_asset_manifest,
)
from paraguibench.runtime import derived_gold
from paraguibench.runtime.derived_gold import (
    DerivedGoldMaterializationError,
    materialize_derived_gold,
)
from paraguibench.runtime.osworld_gold import (
    OSWorldGoldBindingError,
    bind_osworld_task_gold,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "Operation-FileOperate-Settings-001"
MANIFEST_PATH = REPO_ROOT / "benchmark" / "gold" / "manifests" / f"{TASK_ID}.json"
SCHEMA_PATH = REPO_ROOT / "benchmark" / "schemas" / "gold-asset-manifest-v2.schema.json"
_INPUT_FIXTURE_ROOT_ENV = "PARAGUIBENCH_SETTINGS001_INPUT_FIXTURE_ROOT"


def _prepare_private_real_source(tmp_path: Path) -> tuple[Path, Path]:
    """在隔离根复制并固定 Settings-001 真实输入。

    输入参数：tmp_path 为 pytest 提供的隔离目录。
    输出返回值：返回 0700 asset cache 根与尚未存在的 gold cache 根。
    """

    fixture_root_value = os.environ.get(_INPUT_FIXTURE_ROOT_ENV)
    if fixture_root_value is None:
        pytest.skip("Settings-001 私有真实 fixture 未配置")
    fixture_root = Path(fixture_root_value)
    if not fixture_root.is_dir():
        pytest.skip("Settings-001 私有真实 fixture 未预置")
    asset_cache_root = tmp_path / "asset-cache"
    task_asset_root = asset_cache_root / TASK_ID
    task_asset_root.mkdir(parents=True, mode=0o700)
    os.chmod(asset_cache_root, 0o700)
    os.chmod(task_asset_root, 0o700)
    source = fixture_root / "landscape.mp4"
    source_status = source.stat(follow_symlinks=False)
    assert source_status.st_size == 9_362_831
    assert source_status.st_nlink == 1
    assert source.is_file() and not source.is_symlink()
    fixture_bytes = source.read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == (
        "d39162e1d519e978261ad4ae824d4446f511936c80d5ce2e085cf617eae04c35"
    )
    target_source = task_asset_root / "landscape.mp4"
    target_source.write_bytes(fixture_bytes)
    os.chmod(target_source, 0o600)
    return asset_cache_root, tmp_path / "gold-cache"


def test_settings001_manifest_loads_as_strict_derived_v2_variant() -> None:
    """验证正式 manifest 精确绑定输入视频、派生帧和输出字节身份。

    输入参数：无；通过公开 loader 读取仓库内 Settings-001 gold manifest。
    输出返回值：无；v2 类型、输入 manifest、帧 PTS、工具链和输出身份
        全部与经真实 fixture 复核的固定证据一致。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)

    assert isinstance(manifest, DerivedGoldAssetManifest)
    assert manifest.schema_version == 2
    assert manifest.manifest_id == f"{TASK_ID}-gold-v2"
    assert manifest.distribution_policy == "private_materialization_only"
    assert manifest.license.status == "verified"
    assert manifest.license.spdx_expression == "Apache-2.0"
    assert manifest.license.evidence_ref == (
        "https://huggingface.co/datasets/xlangai/ubuntu_osworld_file_cache"
    )
    assert manifest.license.basis == "derived_from_source_input"
    assert manifest.license.distribution == "private_materialization_only"
    assert manifest.asset_set_id == TASK_ID
    assert manifest.asset_manifest_sha256 == (
        "8de1a8fa801bc0aa26cca86033a6f8370f1efe011369229ad821f8240922f6cf"
    )
    assert manifest.source_input.path == "landscape.mp4"
    assert manifest.source_input.size == 9_362_831
    assert manifest.source_input.sha256 == (
        "d39162e1d519e978261ad4ae824d4446f511936c80d5ce2e085cf617eae04c35"
    )
    assert manifest.derivation.protocol_id == (
        "paraguibench.gold.first-video-frame-pts-gte.v1"
    )
    assert manifest.derivation.stream_selector == "v:0"
    assert manifest.derivation.timestamp_field == "best_effort_timestamp_time"
    assert manifest.derivation.frame_order == "ffprobe_emitted_display_order"
    assert manifest.derivation.index_origin == 0
    assert manifest.derivation.timestamp_decimal_places == 6
    assert manifest.derivation.requested_pts == "8.000000"
    assert manifest.derivation.selected_frame_index == 240
    assert manifest.derivation.selected_pts == "8.008000"
    assert manifest.derivation.previous_pts == "7.974633"
    assert manifest.derivation.source_frame_count == 419
    assert manifest.derivation.ffmpeg_version == "8.1.1"
    assert manifest.derivation.ffprobe_version == "8.1.1"
    assert not hasattr(manifest.derivation, "ffmpeg_version_output_sha256")
    assert not hasattr(manifest.derivation, "ffprobe_version_output_sha256")
    assert manifest.derivation.protocol_whitelist == "pipe"
    assert manifest.derivation.threads == 1
    assert manifest.derivation.software_only is True
    assert len(manifest.entries) == 1
    output = manifest.entries[0]
    assert output.logical_key == (
        "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v2"
    )
    assert output.runtime_locator.value == "landscape.png"
    assert output.size == 2_216_858
    assert output.sha256 == (
        "b383ffccf666a2dfe83100b392e1d4e2dbb744e1034b2e200be72621cbe52fc3"
    )
    assert output.decoded_rgb_sha256 == (
        "70138e557d112dfb79e890c42311a5037ee99b014b2facd13b4e2a78a631cd7c"
    )
    assert (output.width, output.height, output.media_type) == (
        1920,
        1080,
        "image/png",
    )


def test_derived_gold_v2_schema_closes_every_object() -> None:
    """验证 v2 schema 是独立、全层闭合的 derived-only variant。

    输入参数：无；读取公开 v2 JSON Schema。
    输出返回值：无；顶层与全部嵌套对象 required/properties 完全相等，
        且 schema 只允许 Settings-001 固定 derived identity。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["schema_version"] == {"const": 2}
    assert schema["properties"]["derivation_kind"] == {"const": "derived_from_input"}
    assert schema["properties"]["distribution_policy"] == {
        "const": "private_materialization_only"
    }
    for name in (
        "derived_from_input",
        "source_input",
        "derivation",
        "toolchain",
        "derived_license",
        "entry",
        "runtime_locator",
        "provenance",
    ):
        definition = schema["$defs"][name]
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])


@pytest.mark.parametrize("top_level", ([], None, "manifest"))
def test_gold_loader_rejects_non_object_top_level_with_fixed_error(
    tmp_path: Path,
    top_level: object,
) -> None:
    """验证 v1/v2 dispatch 不会让非 object 逸出 ``AttributeError``。

    输入参数：tmp_path 为隔离文件根；top_level 为合法 JSON 非 object。
    输出返回值：无；公开 loader 一律抛固定 ``GoldManifestError``。
    """

    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(top_level), encoding="utf-8")

    with pytest.raises(Exception) as captured:
        load_gold_asset_manifest(candidate)

    assert type(captured.value).__name__ == "GoldManifestError"
    assert str(captured.value) == "GOLD_MANIFEST_INVALID"


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("derivation", "toolchain", "threads"), True),
        (("derivation", "toolchain", "software_only"), 1),
        (("entries", 0, "provenance", "expected_index"), False),
        (("entries", 0, "size"), 2_216_858.0),
        (("license", "basis"), True),
    ),
)
def test_derived_loader_rejects_bool_int_and_float_confusion(
    tmp_path: Path,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    """验证 Python 数值相等关系不能绕过 v2 JSON 强类型合同。

    输入参数：tmp_path 为隔离根；path/value 定向把 bool/int/float 混淆
        注入一项本应固定类型的 derived manifest 字段。
    输出返回值：无；全部变体都在 materializer/cache 前固定失败。
    """

    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    current: object = raw
    for component in path[:-1]:
        current = current[component]  # type: ignore[index]
    current[path[-1]] = value  # type: ignore[index]
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(Exception) as captured:
        load_gold_asset_manifest(candidate)

    assert type(captured.value).__name__ == "GoldManifestError"
    assert str(captured.value) == "GOLD_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "mutator",
    (
        lambda text: text.replace(
            '  "schema_version": 2,',
            '  "schema_version": 2,\n  "schema_version": 2,',
            1,
        ),
        lambda text: text.replace(
            '"selected_frame_index": 240',
            '"selected_frame_index": NaN',
            1,
        ),
        lambda text: text.replace(
            '"derivation_kind": "derived_from_input"',
            '"source_locator": {}',
            1,
        ),
    ),
)
def test_derived_loader_rejects_duplicate_nonstandard_and_v1_field_confusion(
    tmp_path: Path,
    mutator: object,
) -> None:
    """验证 v2 不接受重复键、NaN 或 v1 下载型字段混入。

    输入参数：tmp_path 为隔离根；mutator 只变异一个协议边界。
    输出返回值：无；全部候选以固定 manifest 错误失败关闭。
    """

    candidate = tmp_path / "candidate.json"
    candidate.write_text(
        mutator(MANIFEST_PATH.read_text(encoding="utf-8")),  # type: ignore[operator]
        encoding="utf-8",
    )

    with pytest.raises(GoldManifestError):
        load_gold_asset_manifest(candidate)


def test_download_fetch_permanently_rejects_derived_manifest(tmp_path: Path) -> None:
    """验证 download-only fetch 不能联网处理 derived-from-input gold。

    输入参数：tmp_path 为不应创建的 evaluator cache 根。
    输出返回值：无；公开 fetch 在 opener/cache 之前抛固定错误。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    assert isinstance(manifest, DerivedGoldAssetManifest)
    calls: list[str] = []

    def unreachable_opener(*_args: object, **_kwargs: object) -> object:
        """记录任何错误联网调用；本测试中必须不可达。"""

        calls.append("network")
        raise AssertionError("derived gold 不得联网 fetch")

    with pytest.raises(GoldFetchError):
        fetch_gold_assets(  # type: ignore[arg-type]
            manifest,
            tmp_path / "gold-cache",
            opener=unreachable_opener,
        )

    assert calls == []
    assert not (tmp_path / "gold-cache").exists()


@pytest.mark.parametrize(
    "mutator",
    (
        lambda manifest: replace(manifest, schema_version=True),
        lambda manifest: replace(manifest, distribution_policy="download_only"),
        lambda manifest: replace(
            manifest,
            asset_manifest=(
                "benchmark/assets/manifests/"
                "Operation-FileOperate-BatchOperation-003.json"
            ),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(
                manifest.derivation,
                protocol_whitelist="file,pipe",
            ),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, threads=True),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, software_only=1),
        ),
        lambda manifest: replace(
            manifest,
            entries=(replace(manifest.entries[0], size=2_216_858.0),),
        ),
        lambda manifest: replace(
            manifest,
            entries=(replace(manifest.entries[0], sha256="0" * 64),),
        ),
    ),
)
def test_materializer_rejects_noncanonical_manifest_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutator: object,
) -> None:
    """验证公开物化边界不接受手工组装的非 canonical 清单。

    输入参数：tmp_path 提供不应被创建的私有路径；monkeypatch
        将 cache resolver 和子进程边界设为不可达哨兵；mutator
        漂移一个固定值或注入 bool/int/float 类型混淆。
    输出返回值：无；所有变体必须在任何 cache 路径创建、
        Popen 或源 manifest 读取前抛固定 manifest 错误。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    assert isinstance(manifest, DerivedGoldAssetManifest)
    candidate = mutator(manifest)  # type: ignore[operator]
    calls: list[str] = []

    class ForbiddenResolver:
        """记录任何错误越过 canonical manifest 门的 cache 访问。"""

        def __init__(self, **_kwargs: object) -> None:
            """记录构造并立即失败；本测试中必须不可达。"""

            calls.append("cache")
            raise AssertionError("非 canonical manifest 不得访问 cache")

    def forbidden_process(*_args: object, **_kwargs: object) -> object:
        """记录任何错误启动的外部子进程。"""

        calls.append("process")
        raise AssertionError("非 canonical manifest 不得启动工具")

    def forbidden_source_open(*_args: object, **_kwargs: object) -> object:
        """记录任何错误打开的源 input manifest。"""

        calls.append("source")
        raise AssertionError("非 canonical manifest 不得读取源路径")

    monkeypatch.setattr(derived_gold, "GoldAssetResolver", ForbiddenResolver)
    monkeypatch.setattr(derived_gold.subprocess, "Popen", forbidden_process)
    monkeypatch.setattr(
        derived_gold,
        "_open_regular_nofollow",
        forbidden_source_open,
    )
    asset_cache_root = tmp_path / "asset-cache"
    asset_cache_root.mkdir(mode=0o700)
    gold_cache_root = tmp_path / "gold-cache"

    with pytest.raises(GoldManifestError) as captured:
        materialize_derived_gold(
            manifest=candidate,
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    assert str(captured.value) == "GOLD_MANIFEST_INVALID"
    assert calls == []
    assert not gold_cache_root.exists()


@pytest.mark.parametrize(
    "mutator",
    (
        lambda manifest: replace(
            manifest,
            source_input=replace(manifest.source_input, path="other.mp4"),
        ),
        lambda manifest: replace(
            manifest,
            source_input=replace(manifest.source_input, size=9_362_832),
        ),
        lambda manifest: replace(
            manifest,
            source_input=replace(manifest.source_input, sha256="0" * 64),
        ),
        lambda manifest: replace(manifest, asset_manifest_sha256="0" * 64),
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, protocol_id="other"),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(
                manifest.derivation,
                protocol_whitelist="file,pipe",
            ),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, threads=True),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, software_only=1),
        ),
        lambda manifest: replace(
            manifest,
            entries=(
                replace(
                    manifest.entries[0],
                    runtime_locator=replace(
                        manifest.entries[0].runtime_locator,
                        value="../../secret",
                    ),
                ),
            ),
        ),
        lambda manifest: replace(
            manifest,
            entries=(replace(manifest.entries[0], size=2_216_859),),
        ),
        lambda manifest: replace(
            manifest,
            entries=(replace(manifest.entries[0], sha256="0" * 64),),
        ),
        lambda manifest: replace(
            manifest,
            entries=(replace(manifest.entries[0], decoded_rgb_sha256="0" * 64),),
        ),
    ),
)
def test_settings_binder_rejects_every_noncanonical_derived_field(
    mutator: object,
) -> None:
    """验证 Settings binder 不信任手工变更的 derived dataclass。

    输入参数：mutator 分别漂移 source input、派生协议、
        runtime locator 或输出字节/RGB 身份。
    输出返回值：无；公开 binder 一律在 resolver/cache 前固定
        失败，不把局部字段对上误当为完整 canonical 合同。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    assert isinstance(manifest, DerivedGoldAssetManifest)
    candidate = mutator(manifest)  # type: ignore[operator]

    with pytest.raises(OSWorldGoldBindingError) as captured:
        bind_osworld_task_gold(
            TASK_ID,
            manifest=candidate,
            task_uid="9b5220d5-f1f0-4db9-902d-ad41aae4d775",
            evaluator_path=(
                "eval/osworld_scripts/9b5220d5-f1f0-4db9-902d-ad41aae4d775.json"
            ),
            asset_manifest_reference=(
                "benchmark/assets/manifests/Operation-FileOperate-Settings-001.json"
            ),
        )

    assert str(captured.value) == "OSWORLD_GOLD_BINDING_INVALID"


@pytest.mark.parametrize(
    "mutator",
    (
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, threads=True),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, software_only=1),
        ),
        lambda manifest: replace(
            manifest,
            entries=(replace(manifest.entries[0], size=2_216_858.0),),
        ),
        lambda manifest: replace(
            manifest,
            entries=(
                replace(
                    manifest.entries[0],
                    runtime_locator=replace(
                        manifest.entries[0].runtime_locator,
                        value="../../secret",
                    ),
                ),
            ),
        ),
    ),
)
def test_resolver_rejects_noncanonical_derived_manifest_at_construction(
    tmp_path: Path,
    mutator: object,
) -> None:
    """验证直接构造 resolver 也不能绕过 strict v2 loader。

    输入参数：tmp_path 提供不应被访问的 cache 路径；
        mutator 注入 Python 宽松相等可隐藏的类型混淆或逃逸路径。
    输出返回值：无；构造器在保存清单或访问 cache 前
        抛固定 ``GoldManifestError``。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    assert isinstance(manifest, DerivedGoldAssetManifest)
    candidate = mutator(manifest)  # type: ignore[operator]
    cache_root = tmp_path / "gold-cache"

    with pytest.raises(GoldManifestError) as captured:
        GoldAssetResolver(manifest=candidate, cache_root=cache_root)

    assert str(captured.value) == "GOLD_MANIFEST_INVALID"
    assert not cache_root.exists()


@pytest.mark.parametrize(
    "mutator",
    (
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, threads=True),
        ),
        lambda manifest: replace(
            manifest,
            derivation=replace(manifest.derivation, software_only=1),
        ),
        lambda manifest: replace(
            manifest,
            entries=(replace(manifest.entries[0], size=2_216_858.0),),
        ),
    ),
)
def test_resolver_never_reports_loose_equal_manifest_as_bound(
    tmp_path: Path,
    mutator: object,
) -> None:
    """验证 resolver 的绑定检查不使用 Python dataclass 宽松等值。

    输入参数：tmp_path 提供不会读取的 cache 路径；mutator
        注入三类与 canonical 数值宽松相等的 bool/int/float 混淆。
    输出返回值：无；用 canonical 清单构造的 resolver 对所有
        混淆候选都返回 ``False``，不暴露字段值。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    assert isinstance(manifest, DerivedGoldAssetManifest)
    resolver = GoldAssetResolver(
        manifest=manifest,
        cache_root=tmp_path / "gold-cache",
    )
    candidate = mutator(manifest)  # type: ignore[operator]

    assert resolver.is_bound_to_manifest(candidate) is False
    assert not (tmp_path / "gold-cache").exists()


def test_production_resolver_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    """验证正式 resolver 在类型校验前打开 FIFO 也不会永久阻塞。

    输入参数：tmp_path 提供 0700 v2 cache，唯一 target 为 0600
        named pipe；使用隔离子进程为旧实现设置硬超时。
    输出返回值：无；子进程必须在 2 秒内返回固定
        ``GoldIntegrityError``，不得等待 FIFO writer。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    cache_root = tmp_path / "gold-cache"
    output_parent = cache_root / manifest.manifest_id
    output_parent.mkdir(parents=True, mode=0o700)
    os.chmod(cache_root, 0o700)
    os.chmod(output_parent, 0o700)
    fifo = output_parent / manifest.entries[0].runtime_locator.value
    os.mkfifo(fifo, mode=0o600)
    code = """
from pathlib import Path
from paraguibench.runtime.gold_assets import GoldAssetResolver, load_gold_asset_manifest
manifest = load_gold_asset_manifest(Path(__import__('sys').argv[1]))
try:
    GoldAssetResolver(manifest=manifest, cache_root=Path(__import__('sys').argv[2])).verify_required((manifest.entries[0].logical_key,))
except Exception as error:
    print(type(error).__name__)
"""

    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            code,
            os.fspath(MANIFEST_PATH),
            os.fspath(cache_root),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2.0,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == b"GoldIntegrityError\n"
    assert result.stderr == b""


def test_materializer_reproduces_settings001_png_from_real_fixture(
    tmp_path: Path,
) -> None:
    """验证正式工具链从真实固定 MP4 重现唯一 host-only PNG。

    输入参数：tmp_path 提供 repo 外、物理分离的 input/gold 私有缓存根。
    输出返回值：无；公开 materializer 复核全部419帧 PTS 后生成固定
        b383 PNG，production resolver 再从0600缓存复核编码与RGB身份。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    repository_pngs_before = {
        path.relative_to(REPO_ROOT): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in REPO_ROOT.rglob("*.png")
        if ".venv-dev" not in path.parts
    }

    availability = materialize_derived_gold(
        manifest=load_gold_asset_manifest(MANIFEST_PATH),
        repo_root=REPO_ROOT,
        asset_cache_root=asset_cache_root,
        gold_cache_root=gold_cache_root,
        ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
        ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
        timeout_seconds=30.0,
    )

    output = gold_cache_root / f"{TASK_ID}-gold-v2" / "landscape.png"
    assert availability.status.value == "AVAILABLE"
    assert availability.requested_count == 1
    assert output.stat().st_size == 2_216_858
    assert output.stat().st_mode & 0o777 == 0o600
    assert output.stat().st_nlink == 1
    assert output.resolve().is_relative_to(tmp_path.resolve())
    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    resolver_availability = GoldAssetResolver(
        manifest=manifest,
        cache_root=gold_cache_root,
    ).verify_required((manifest.entries[0].logical_key,))
    assert resolver_availability.status.value == "AVAILABLE"
    assert resolver_availability.requested_count == 1
    repository_pngs_after = {
        path.relative_to(REPO_ROOT): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in REPO_ROOT.rglob("*.png")
        if ".venv-dev" not in path.parts
    }
    assert repository_pngs_after == repository_pngs_before


def test_materializer_verifies_published_output_before_reporting_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 materializer 自身在返回 AVAILABLE 前调用正式 resolver。

    输入参数：tmp_path 提供隔离私有根；monkeypatch 包装正式
        ``GoldAssetResolver`` 公开边界并记录请求的 logical-key 闭集。
    输出返回值：无；物化先校验可复用输出，发布后再用同一唯一
        logical key 复核一次，阻断“发布完即盲目报成功”。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    original_resolver = GoldAssetResolver
    calls: list[tuple[str, ...]] = []

    class RecordingResolver:
        """代理正式 resolver 并仅记录公开 verify 请求。"""

        def __init__(self, *, manifest: object, cache_root: Path) -> None:
            """保留原 resolver 的真实 manifest/cache 语义。"""

            self._delegate = original_resolver(
                manifest=manifest,  # type: ignore[arg-type]
                cache_root=cache_root,
            )

        def verify_required(self, logical_keys: tuple[str, ...]) -> object:
            """记录 logical keys 后委托正式字节验证。"""

            calls.append(logical_keys)
            return self._delegate.verify_required(logical_keys)

    monkeypatch.setattr(
        derived_gold,
        "GoldAssetResolver",
        RecordingResolver,
        raising=False,
    )

    availability = materialize_derived_gold(
        manifest=load_gold_asset_manifest(MANIFEST_PATH),
        repo_root=REPO_ROOT,
        asset_cache_root=asset_cache_root,
        gold_cache_root=gold_cache_root,
        ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
        ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
        timeout_seconds=30.0,
    )

    assert availability.status.value == "AVAILABLE"
    logical_key = load_gold_asset_manifest(MANIFEST_PATH).entries[0].logical_key
    assert calls == [(logical_key,), (logical_key,)]


def test_materializer_reuses_an_already_verified_output_without_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证同一私有 cache 上的重复物化是验证型幂等操作。

    输入参数：tmp_path 提供隔离 input/gold 根；monkeypatch 在首次
        成功后将所有子进程启动设为不可达哨兵。
    输出返回值：无；第二次直接用正式 resolver 复核已有字节并返回
        AVAILABLE，不启动工具、不覆盖文件，inode 和摘要保持不变。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    first = materialize_derived_gold(
        manifest=manifest,
        repo_root=REPO_ROOT,
        asset_cache_root=asset_cache_root,
        gold_cache_root=gold_cache_root,
        ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
        ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
        timeout_seconds=30.0,
    )
    output = gold_cache_root / manifest.manifest_id / "landscape.png"
    before = output.stat(follow_symlinks=False)
    before_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()

    def forbidden_process(*_args: object, **_kwargs: object) -> object:
        """拒绝重复物化误启任何媒体或版本子进程。"""

        raise AssertionError("已验证输出不得重跑媒体工具")

    monkeypatch.setattr(derived_gold.subprocess, "Popen", forbidden_process)

    second = materialize_derived_gold(
        manifest=manifest,
        repo_root=REPO_ROOT,
        asset_cache_root=asset_cache_root,
        gold_cache_root=gold_cache_root,
        ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
        ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
        timeout_seconds=30.0,
    )

    after = output.stat(follow_symlinks=False)
    assert first == second
    assert (after.st_dev, after.st_ino, after.st_nlink) == (
        before.st_dev,
        before.st_ino,
        before.st_nlink,
    )
    assert hashlib.sha256(output.read_bytes()).hexdigest() == before_sha256


def test_materializer_rejects_an_invalid_existing_output_without_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证已有 target 身份错误时固定失败且不覆盖或重算。

    输入参数：tmp_path 提供隔离 input/gold 根；monkeypatch 将所有
        媒体子进程设为不可达哨兵。
    输出返回值：无；错误的单链接 0600 target 导致公开派生错误，
        原字节/inode 保留，不启动工具，也不泄漏 resolver 内部错误类。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    output_parent = gold_cache_root / manifest.manifest_id
    output_parent.mkdir(parents=True, mode=0o700)
    os.chmod(gold_cache_root, 0o700)
    os.chmod(output_parent, 0o700)
    output = output_parent / "landscape.png"
    output.write_bytes(b"invalid")
    os.chmod(output, 0o600)
    before = output.stat(follow_symlinks=False)

    def forbidden_process(*_args: object, **_kwargs: object) -> object:
        """拒绝已有错误 target 触发任何子进程。"""

        raise AssertionError("错误旧 target 不得被工具重算覆盖")

    monkeypatch.setattr(derived_gold.subprocess, "Popen", forbidden_process)

    with pytest.raises(DerivedGoldMaterializationError) as captured:
        materialize_derived_gold(
            manifest=manifest,
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    assert str(captured.value) == "DERIVED_GOLD_MATERIALIZATION_FAILED"
    after = output.stat(follow_symlinks=False)
    assert output.read_bytes() == b"invalid"
    assert (after.st_dev, after.st_ino, after.st_nlink) == (
        before.st_dev,
        before.st_ino,
        before.st_nlink,
    )


@pytest.mark.parametrize("failure_kind", ("selector-init", "keyboard-interrupt"))
def test_bounded_process_always_reaps_its_child_on_monitor_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    """验证子进程监控初始化失败或中断都会回收 owned child。

    输入参数：monkeypatch 在 selector 构造或 select 阶段注入异常；
        failure_kind 选择普通 OSError 或 KeyboardInterrupt。
    输出返回值：无；公式边界保留原异常类，同时已启动进程
        一定终止/wait，stdout/stderr 管道关闭，不留后台 ffmpeg。
    """

    original_popen = derived_gold.subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        """启动时间足够长的 owned 子进程并记录句柄。"""

        del args
        process = original_popen(
            [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
            **kwargs,
        )
        children.append(process)
        return process

    monkeypatch.setattr(derived_gold.subprocess, "Popen", recording_popen)
    if failure_kind == "selector-init":
        monkeypatch.setattr(
            derived_gold.selectors,
            "DefaultSelector",
            lambda: (_ for _ in ()).throw(OSError("selector init failed")),
        )
        expected_error: type[BaseException] = OSError
    else:
        original_selector = derived_gold.selectors.DefaultSelector

        class InterruptingSelector:
            """代理真 selector，仅在 select 时模拟用户中断。"""

            def __init__(self) -> None:
                """创建可正常 register/close 的真 selector。"""

                self._delegate = original_selector()

            def register(self, *args: object, **kwargs: object) -> object:
                """委托注册文件对象。"""

                return self._delegate.register(*args, **kwargs)

            def get_map(self) -> object:
                """委托返回已注册对象。"""

                return self._delegate.get_map()

            def select(self, *_args: object, **_kwargs: object) -> object:
                """模拟运行中收到 KeyboardInterrupt。"""

                raise KeyboardInterrupt

            def close(self) -> None:
                """关闭真 selector。"""

                self._delegate.close()

        monkeypatch.setattr(
            derived_gold.selectors,
            "DefaultSelector",
            InterruptingSelector,
        )
        expected_error = KeyboardInterrupt

    with pytest.raises(expected_error):
        derived_gold._run_bounded_process(  # noqa: SLF001
            argv=("ignored",),
            stdin_descriptor=None,
            timeout_seconds=1.0,
            maximum_stdout=1024,
            maximum_stderr=1024,
        )

    assert len(children) == 1
    child = children[0]
    assert child.poll() is not None
    assert child.stdout is not None and child.stdout.closed
    assert child.stderr is not None and child.stderr.closed


def test_verified_tool_rejects_same_inode_same_size_in_place_mutation(
    tmp_path: Path,
) -> None:
    """验证工具版本校验后的同 inode 等长原地改写也会失败。

    输入参数：tmp_path 提供一个私有可执行脚本，其 ``-version``
        stdout 先被当作临时固定合同，随后仅改字节不改 inode/大小。
    输出返回值：无；冻结 tool 的 continuity 校验必须根据纳秒 mtime/
        ctime 拒绝改写，防止只比 dev/inode/size 的 TOCTOU 窗口。
    """

    tool_path = tmp_path / "synthetic-tool"
    original = b"#!/bin/sh\nprintf 'ffmpeg version 8.1.1 synthetic-a\\n'\n"
    replacement = b"#!/bin/sh\nprintf 'ffmpeg version 8.1.1 synthetic-b\\n'\n"
    assert len(original) == len(replacement)
    tool_path.write_bytes(original)
    os.chmod(tool_path, 0o700)
    subprocess.run(
        [os.fspath(tool_path), "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    tool = derived_gold._verify_tool_identity(  # noqa: SLF001
        tool_path,
        expected_program="ffmpeg",
        expected_version="8.1.1",
        timeout_seconds=2.0,
    )
    before = tool_path.stat(follow_symlinks=False)
    with tool_path.open("r+b", buffering=0) as stream:
        stream.seek(0)
        stream.write(replacement)
        os.fsync(stream.fileno())
    after = tool_path.stat(follow_symlinks=False)
    assert (before.st_dev, before.st_ino, before.st_size) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
    )

    with pytest.raises(DerivedGoldMaterializationError):
        tool.verify_continuity()


def test_materializer_leaves_no_file_when_atomic_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证原子 no-clobber 发布失败时不留输出或临时文件。

    输入参数：tmp_path 提供隔离私有根；monkeypatch 仅在文件系统
        边界注入一次 hard-link 发布故障。
    输出返回值：无；公开 materializer 固定失败，私有根下没有
        任何普通文件，也没有可被后续运行误用的半成品。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)

    def fail_atomic_link(*_args: object, **_kwargs: object) -> None:
        """模拟目标目录在 no-clobber link 边界拒绝发布。"""

        raise OSError("synthetic atomic publish failure")

    monkeypatch.setattr(derived_gold.os, "link", fail_atomic_link)

    with pytest.raises(DerivedGoldMaterializationError):
        materialize_derived_gold(
            manifest=load_gold_asset_manifest(MANIFEST_PATH),
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    regular_files = tuple(
        path
        for path in gold_cache_root.rglob("*")
        if path.is_file() or path.is_symlink()
    )
    assert regular_files == ()


def test_materializer_rolls_back_its_output_when_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证临时名称已移除后的持久化失败仍回滚本次输出。

    输入参数：tmp_path 提供隔离私有根；monkeypatch 仅使目标目录
        fsync 在临时文件持久化成功后失败。
    输出返回值：无；公开 materializer 不返回 AVAILABLE，且私有
        cache 下不保留未持久化完成的 target 或临时文件。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    original_fsync = derived_gold.os.fsync
    fsync_count = 0

    def fail_directory_fsync(descriptor: int) -> None:
        """允许临时文件 fsync，仅在父目录 fsync 时失败。"""

        nonlocal fsync_count
        fsync_count += 1
        if fsync_count == 2:
            raise OSError("synthetic directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(derived_gold.os, "fsync", fail_directory_fsync)

    with pytest.raises(DerivedGoldMaterializationError):
        materialize_derived_gold(
            manifest=load_gold_asset_manifest(MANIFEST_PATH),
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    assert fsync_count == 2
    assert (
        tuple(
            path
            for path in gold_cache_root.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        == ()
    )


def test_materializer_preserves_unknown_replacement_but_cleans_its_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证发布名 ABA 时不删未知对象，仍完成其他清理。

    输入参数：tmp_path 提供隔离私有根；monkeypatch 在 hard-link
        成功后把 target 换成另一个单链接私有文件。
    输出返回值：无；公开边界失败，未知 target 原样保留，而本次
        O_EXCL 临时文件必须清理，避免一个 cleanup 错误跳过其余收尾。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    original_link = derived_gold.os.link

    def replace_linked_target(*args: object, **kwargs: object) -> None:
        """先执行真实 no-clobber link，再用未知私有 inode 替换目标名。"""

        original_link(*args, **kwargs)
        target_name = args[1]
        parent_fd = kwargs["dst_dir_fd"]
        assert isinstance(target_name, str) and isinstance(parent_fd, int)
        os.unlink(target_name, dir_fd=parent_fd)
        descriptor = os.open(
            target_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            os.write(descriptor, b"unknown")
        finally:
            os.close(descriptor)

    monkeypatch.setattr(derived_gold.os, "link", replace_linked_target)

    with pytest.raises(DerivedGoldMaterializationError):
        materialize_derived_gold(
            manifest=load_gold_asset_manifest(MANIFEST_PATH),
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    output_parent = gold_cache_root / f"{TASK_ID}-gold-v2"
    output = output_parent / "landscape.png"
    assert output.read_bytes() == b"unknown"
    assert tuple(output_parent.glob(".gold-download-*.tmp")) == ()


@pytest.mark.parametrize(
    "relationship",
    (
        "same-as-repo",
        "inside-repo",
        "contains-repo",
        "same-as-asset-cache",
        "inside-asset-cache",
        "contains-asset-cache",
    ),
)
def test_materializer_rejects_gold_root_overlap_before_any_media_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relationship: str,
) -> None:
    """验证 host-only gold 根与仓库或 input cache 重叠时前置失败。

    输入参数：tmp_path 提供真实 input 副本；monkeypatch 记录外部媒体
        工具启动；relation 覆盖相同与双向祖先关系。
    输出返回值：无；全部非隔离布局都在任何 ffmpeg/ffprobe 启动前
        固定失败，防止 evaluator-only gold 进入源码或 guest input 树。
    """

    asset_cache_root, _unused_gold_root = _prepare_private_real_source(tmp_path)
    gold_roots = {
        "same-as-repo": REPO_ROOT,
        "inside-repo": REPO_ROOT / ".synthetic-derived-gold",
        "contains-repo": REPO_ROOT.parent,
        "same-as-asset-cache": asset_cache_root,
        "inside-asset-cache": asset_cache_root / "private-gold",
        "contains-asset-cache": tmp_path,
    }
    calls: list[tuple[object, ...]] = []

    def forbidden_process(*args: object, **_kwargs: object) -> object:
        """记录任何越过根隔离门的子进程启动。"""

        calls.append(args)
        raise AssertionError("重叠根不得启动媒体工具")

    monkeypatch.setattr(derived_gold.subprocess, "Popen", forbidden_process)

    with pytest.raises(DerivedGoldMaterializationError):
        materialize_derived_gold(
            manifest=load_gold_asset_manifest(MANIFEST_PATH),
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_roots[relationship],
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    assert calls == []


@pytest.mark.parametrize("unsafe_layout", ("symlink-ancestor", "open-task-dir"))
def test_materializer_rejects_unsafe_input_ancestor_before_media_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_layout: str,
) -> None:
    """验证 input cache 的 symlink 祖先或非私有目录在工具前失败。

    输入参数：tmp_path 提供真实 input 副本；monkeypatch 记录媒体
        进程；unsafe_layout 选择祖先 symlink 或 0755 任务目录。
    输出返回值：无；物化不跟随祖先 symlink，也不从 group/other
        可遍历目录读取媒体，且两者都不启动子进程。
    """

    real_asset_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    asset_cache_root = real_asset_root
    if unsafe_layout == "symlink-ancestor":
        asset_cache_root = tmp_path / "asset-cache-alias"
        asset_cache_root.symlink_to(real_asset_root, target_is_directory=True)
    else:
        os.chmod(real_asset_root / TASK_ID, 0o755)
    calls: list[tuple[object, ...]] = []

    def forbidden_process(*args: object, **_kwargs: object) -> object:
        """记录任何越过 input 祖先门的子进程启动。"""

        calls.append(args)
        raise AssertionError("不安全 input 祖先不得启动媒体工具")

    monkeypatch.setattr(derived_gold.subprocess, "Popen", forbidden_process)

    with pytest.raises(DerivedGoldMaterializationError):
        materialize_derived_gold(
            manifest=load_gold_asset_manifest(MANIFEST_PATH),
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    assert calls == []


def test_materializer_rejects_wrong_source_size_before_full_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 manifest 大小不同的源在全量读取与工具前失败。

    输入参数：tmp_path 提供真实私有源；monkeypatch 把内部全量哈希
        边界设为不可达哨兵，以证明 fstat 大小门不会读取无界字节。
    输出返回值：无；追加一字节的源固定失败，哈希与子进程均未执行。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    source = asset_cache_root / TASK_ID / "landscape.mp4"
    with source.open("ab") as stream:
        stream.write(b"x")
    calls: list[str] = []

    def forbidden_hash(*_args: object, **_kwargs: object) -> tuple[str, int]:
        """记录任何越过 fstat 大小门的全文哈希。"""

        calls.append("hash")
        raise AssertionError("错误大小源不得全文哈希")

    monkeypatch.setattr(derived_gold, "_hash_held_file", forbidden_hash)

    with pytest.raises(DerivedGoldMaterializationError):
        materialize_derived_gold(
            manifest=load_gold_asset_manifest(MANIFEST_PATH),
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    assert calls == []


def test_materializer_rejects_task_directory_aba_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证已持有源 FD 不能隐藏 task 目录名的 ABA 替换。

    输入参数：tmp_path 提供真实私有源；monkeypatch 在首个工具
        身份校验后移走原 task 目录，再用相同字节重建同名目录。
    输出返回值：无；即使 held leaf 字节未变，逐级名称到目录 FD
        的连续性失效也必须在发布前失败，gold cache 不产生文件。
    """

    asset_cache_root, gold_cache_root = _prepare_private_real_source(tmp_path)
    task_root = asset_cache_root / TASK_ID
    source_bytes = (task_root / "landscape.mp4").read_bytes()
    original_verify = derived_gold._verify_tool_identity
    replaced = False

    def replace_task_after_tool_check(*args: object, **kwargs: object) -> Path:
        """保留真实工具校验，并仅一次替换 task 目录名。"""

        nonlocal replaced
        result = original_verify(*args, **kwargs)
        if not replaced:
            replaced = True
            moved = asset_cache_root / f"{TASK_ID}-moved"
            task_root.rename(moved)
            task_root.mkdir(mode=0o700)
            replacement = task_root / "landscape.mp4"
            replacement.write_bytes(source_bytes)
            os.chmod(replacement, 0o600)
        return result

    monkeypatch.setattr(
        derived_gold,
        "_verify_tool_identity",
        replace_task_after_tool_check,
    )

    with pytest.raises(DerivedGoldMaterializationError):
        materialize_derived_gold(
            manifest=load_gold_asset_manifest(MANIFEST_PATH),
            repo_root=REPO_ROOT,
            asset_cache_root=asset_cache_root,
            gold_cache_root=gold_cache_root,
            ffmpeg_path=Path("/opt/homebrew/bin/ffmpeg"),
            ffprobe_path=Path("/opt/homebrew/bin/ffprobe"),
            timeout_seconds=30.0,
        )

    assert replaced is True
    assert (
        tuple(
            path
            for path in gold_cache_root.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        == ()
    )


def test_probe_contract_rejects_extra_csv_column_after_first_frame() -> None:
    """验证 ffprobe CSV 仅容许首帧固定 side-data 空列。

    输入参数：无；从公式帧时间步长构造 419 行合同，但在第
        2 帧追加额外 CSV 空列。
    输出返回值：无；内部 probe 合同固定拒绝额外列，避免将
        ffprobe 字段漂移误当成时间身份。
    """

    values = [f"{index * 1001 / 30000:.6f}" for index in range(419)]
    values[0] += ","
    values[1] += ","
    payload = ("\n".join(values) + "\n").encode("ascii")

    with pytest.raises(DerivedGoldMaterializationError):
        derived_gold._verify_probe_contract(  # noqa: SLF001
            payload,
            load_gold_asset_manifest(MANIFEST_PATH),
        )


def test_probe_contract_requires_the_pinned_first_frame_csv_shape() -> None:
    """验证首帧固定 side-data 空列不能静默消失。

    输入参数：无；构造时间值全正确但首帧没有唯一逗号的
        419 行 CSV。
    输出返回值：无；固定 FFprobe 8.1.1 的精确输出形状漂移时
        固定失败，而不只校验数值巧合。
    """

    values = [f"{index * 1001 / 30000:.6f}" for index in range(419)]
    payload = ("\n".join(values) + "\n").encode("ascii")

    with pytest.raises(DerivedGoldMaterializationError):
        derived_gold._verify_probe_contract(  # noqa: SLF001
            payload,
            load_gold_asset_manifest(MANIFEST_PATH),
        )
