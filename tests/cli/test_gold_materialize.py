"""Settings-001 evaluator-only derived gold 显式物化 CLI 测试。"""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest

from paraguibench.benchmark import PreparedTask
from paraguibench.cli.main import _load_task_gold_context, build_parser, main
from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    GoldAssetManifest,
    GoldAvailability,
    GoldAvailabilityStatus,
    load_gold_asset_manifest,
)
from paraguibench.runtime.osworld_gold import (
    ResolvedOSWorldTaskGold,
    TaskGoldMode,
    bind_osworld_task_gold,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_TASK_ID = "Operation-FileOperate-Settings-001"
SETTINGS_MANIFEST_PATH = (
    REPO_ROOT / "benchmark" / "gold" / "manifests" / f"{SETTINGS_TASK_ID}.json"
)
SETTINGS_ASSET_MANIFEST_REFERENCE = (
    "benchmark/assets/manifests/Operation-FileOperate-Settings-001.json"
)


def _resolved_settings_gold() -> tuple[
    DerivedGoldAssetManifest,
    ResolvedOSWorldTaskGold,
]:
    """加载并语义绑定 Settings 正式 v2 gold。

    输入参数：无。
    输出返回：严格 derived manifest 与已闭合任务身份的
        ``ResolvedOSWorldTaskGold``。
    """

    manifest = load_gold_asset_manifest(SETTINGS_MANIFEST_PATH)
    assert isinstance(manifest, DerivedGoldAssetManifest)
    resolved = bind_osworld_task_gold(
        SETTINGS_TASK_ID,
        manifest,
        task_uid="9b5220d5-f1f0-4db9-902d-ad41aae4d775",
        evaluator_path=(
            "eval/osworld_scripts/9b5220d5-f1f0-4db9-902d-ad41aae4d775.json"
        ),
        asset_manifest_reference=SETTINGS_ASSET_MANIFEST_REFERENCE,
    )
    return manifest, resolved


def _resolved_pinned_gold() -> tuple[GoldAssetManifest, ResolvedOSWorldTaskGold]:
    """加载并语义绑定 CombinationDocs-015 正式 v1 gold。

    输入参数：无。
    输出返回：严格下载型 manifest 与已闭合任务身份的
        ``ResolvedOSWorldTaskGold``。
    """

    task_id = "Operation-FileOperate-CombinationDocs-015"
    manifest = load_gold_asset_manifest(
        REPO_ROOT / "benchmark" / "gold" / "manifests" / f"{task_id}.json"
    )
    assert isinstance(manifest, GoldAssetManifest)
    resolved = bind_osworld_task_gold(
        task_id,
        manifest,
        task_uid="9f55fdb6-a749-4170-91a2-bebddd3492d7",
        evaluator_path=(
            "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
        ),
    )
    return manifest, resolved


def test_gold_materialize_parser_requires_explicit_private_roots_and_tools() -> None:
    """验证私有物化命令显式接收全部本地边界。

    输入参数：无；解析一组不含 URL、凭据或 gold 内容的
        ``gold materialize`` 参数。
    输出返回：命令只暴露任务、两个私有根、两个工具
        路径与正超时，且绑定统一 gold handler。
    """

    arguments = build_parser().parse_args(
        [
            "gold",
            "materialize",
            "--repo-root",
            "/tmp/repo",
            "--task-id",
            "Operation-FileOperate-Settings-001",
            "--asset-cache-root",
            "/tmp/assets",
            "--gold-cache-root",
            "/tmp/gold",
            "--ffmpeg-path",
            "/opt/tools/ffmpeg",
            "--ffprobe-path",
            "/opt/tools/ffprobe",
            "--timeout-seconds",
            "45",
        ]
    )

    assert arguments.gold_command == "materialize"
    assert arguments.asset_cache_root == "/tmp/assets"
    assert arguments.gold_cache_root == "/tmp/gold"
    assert arguments.ffmpeg_path == "/opt/tools/ffmpeg"
    assert arguments.ffprobe_path == "/opt/tools/ffprobe"
    assert arguments.timeout_seconds == 45.0
    assert callable(arguments.handler)
    assert Path(arguments.repo_root).is_absolute()


@pytest.mark.parametrize("invalid_timeout", ("0", "-1", "nan", "inf", "secret-x"))
def test_gold_materialize_parser_rejects_non_positive_or_non_finite_timeout(
    invalid_timeout: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证物化子进程超时必须是正有限数。

    输入参数：invalid_timeout 覆盖零、负数、NaN、无穷和
        不可解析字符；capsys 捕获安全 parser 输出。
    输出返回：argparse 以 2 退出，且固定错误不回显原始值。
    """

    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(
            [
                "gold",
                "materialize",
                "--task-id",
                SETTINGS_TASK_ID,
                "--asset-cache-root",
                "/tmp/assets",
                "--gold-cache-root",
                "/tmp/gold",
                "--ffmpeg-path",
                "/opt/tools/ffmpeg",
                "--ffprobe-path",
                "/opt/tools/ffprobe",
                "--timeout-seconds",
                invalid_timeout,
            ]
        )

    captured = capsys.readouterr()
    assert caught.value.code == 2
    assert captured.err.endswith("error=ArgumentParseError\n")
    assert invalid_timeout not in captured.err


def test_task_gold_loader_passes_canonical_input_reference_to_settings_binding() -> (
    None
):
    """验证 canonical task 的 input manifest 引用进入 Settings 语义绑定。

    输入参数：无；构造仅含受信 task 字段的 ``PreparedTask``，
        并读取正式 v2 gold manifest。
    输出返回：loader 返回私有派生模式；若遗漏 task 的
        canonical asset-manifest 引用，binder 必须失败。
    """

    prepared = PreparedTask(
        trusted_task={
            "task_id": SETTINGS_TASK_ID,
            "task_uid": "9b5220d5-f1f0-4db9-902d-ad41aae4d775",
            "evaluator_path": (
                "eval/osworld_scripts/9b5220d5-f1f0-4db9-902d-ad41aae4d775.json"
            ),
            "asset_manifest": SETTINGS_ASSET_MANIFEST_REFERENCE,
            "gold_manifest": (f"benchmark/gold/manifests/{SETTINGS_TASK_ID}.json"),
        },
        agent_task={},
        audit_metadata={},
    )

    resolved = _load_task_gold_context(
        repo_root=REPO_ROOT,
        prepared_task=prepared,
    )

    assert resolved.mode is TaskGoldMode.PRIVATE_DERIVED_MANIFEST
    assert isinstance(resolved.manifest, DerivedGoldAssetManifest)


def test_gold_materialize_calls_lazy_derived_boundary_with_absolute_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 CLI 只在执行物化时懒加载 derived 边界。

    输入参数：tmp_path 提供不需预存在的相对私有路径；
        capsys 捕获脱敏输出；monkeypatch 在 module import 系统边界
        注入无媒体 I/O 的 materializer。
    输出返回：fake 只收到严格 v2 manifest、绝对路径与正超时；
        终端仅含 manifest ID、条目数和 PASS。
    """

    manifest, resolved = _resolved_settings_gold()
    monkeypatch.setattr(
        "paraguibench.cli.main._load_prepared_task_context",
        lambda _arguments: (REPO_ROOT, object()),
    )
    monkeypatch.setattr(
        "paraguibench.cli.main._load_task_gold_context",
        lambda **_kwargs: resolved,
    )
    received: dict[str, object] = {}

    def fake_materialize(**kwargs: object) -> GoldAvailability:
        """记录经 CLI 收紧后的物化边界。

        输入参数：kwargs 为 production materializer 的全部 keyword-only 参数。
        输出返回：脱敏 AVAILABLE/1 结果。
        """

        received.update(kwargs)
        return GoldAvailability(
            status=GoldAvailabilityStatus.AVAILABLE,
            requested_count=1,
        )

    fake_module = ModuleType("paraguibench.runtime.derived_gold")
    fake_module.materialize_derived_gold = fake_materialize  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)
    asset_root = tmp_path / "private-assets"
    gold_root = tmp_path / "private-gold"
    ffmpeg_path = tmp_path / "tools" / "ffmpeg"
    ffprobe_path = tmp_path / "tools" / "ffprobe"

    exit_code = main(
        [
            "gold",
            "materialize",
            "--repo-root",
            str(REPO_ROOT),
            "--task-id",
            SETTINGS_TASK_ID,
            "--asset-cache-root",
            str(asset_root),
            "--gold-cache-root",
            str(gold_root),
            "--ffmpeg-path",
            str(ffmpeg_path),
            "--ffprobe-path",
            str(ffprobe_path),
            "--timeout-seconds",
            "45",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert received == {
        "manifest": manifest,
        "repo_root": REPO_ROOT.absolute(),
        "asset_cache_root": asset_root.absolute(),
        "gold_cache_root": gold_root.absolute(),
        "ffmpeg_path": ffmpeg_path.absolute(),
        "ffprobe_path": ffprobe_path.absolute(),
        "timeout_seconds": 45.0,
    }
    assert captured.out.splitlines() == [
        f"gold_manifest={SETTINGS_TASK_ID}-gold-v2",
        "entries=1",
        "status=PASS",
    ]
    assert captured.err == ""
    serialized = captured.out + captured.err
    assert str(asset_root) not in serialized
    assert str(gold_root) not in serialized
    assert manifest.entries[0].sha256 not in serialized
    assert manifest.entries[0].logical_key not in serialized


def test_gold_fetch_rejects_derived_manifest_before_network_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证下载命令不会把 v2 derived manifest 交给网络 fetcher。

    输入参数：tmp_path 提供不存在的 gold cache；capsys 捕获
        公开错误；monkeypatch 把网络预置边界替换为不可达哨兵。
    输出返回：CLI 在 fetcher 前以脱敏 ``GoldFetchError`` 返回 2，
        不创建 cache，也不输出路径、摘要、logical key 或内容。
    """

    manifest, resolved = _resolved_settings_gold()
    monkeypatch.setattr(
        "paraguibench.cli.main._load_prepared_task_context",
        lambda _arguments: (REPO_ROOT, object()),
    )
    monkeypatch.setattr(
        "paraguibench.cli.main._load_task_gold_context",
        lambda **_kwargs: resolved,
    )
    fetch_calls = 0

    def forbidden_fetch(*_args: object, **_kwargs: object) -> object:
        """拒绝 derived manifest 越过 CLI 模式门禁。

        输入参数：_args/_kwargs 为误调用时的 fetcher 参数。
        输出返回：不返回；调用即使测试失败。
        """

        nonlocal fetch_calls
        fetch_calls += 1
        raise AssertionError("derived gold 不得进入网络 fetcher")

    monkeypatch.setattr("paraguibench.cli.main.fetch_gold_assets", forbidden_fetch)
    cache_root = tmp_path / "must-not-exist"

    exit_code = main(
        [
            "gold",
            "fetch",
            "--repo-root",
            str(REPO_ROOT),
            "--task-id",
            SETTINGS_TASK_ID,
            "--gold-cache-root",
            str(cache_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert fetch_calls == 0
    assert captured.out == ""
    assert captured.err == "error=GoldFetchError\n"
    assert not cache_root.exists()
    serialized = captured.out + captured.err
    assert str(cache_root) not in serialized
    assert manifest.entries[0].sha256 not in serialized
    assert manifest.entries[0].logical_key not in serialized


def test_gold_verify_accepts_v2_mode_as_strictly_offline_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 verify 对 v2 执行与 v1 相同的纯离线字节门禁。

    输入参数：tmp_path 提供缺失的私有 gold cache；capsys 捕获
        脱敏错误；monkeypatch 仅替换 canonical task 加载边界。
    输出返回：命令进入 v2 resolver 并返回 ``GoldUnavailableError``，
        不创建 cache、不访问网络、不回显私密身份。
    """

    manifest, resolved = _resolved_settings_gold()
    monkeypatch.setattr(
        "paraguibench.cli.main._load_prepared_task_context",
        lambda _arguments: (REPO_ROOT, object()),
    )
    monkeypatch.setattr(
        "paraguibench.cli.main._load_task_gold_context",
        lambda **_kwargs: resolved,
    )
    cache_root = tmp_path / "missing-v2-gold"

    exit_code = main(
        [
            "gold",
            "verify",
            "--repo-root",
            str(REPO_ROOT),
            "--task-id",
            SETTINGS_TASK_ID,
            "--gold-cache-root",
            str(cache_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err == "error=GoldUnavailableError\n"
    assert not cache_root.exists()
    serialized = captured.out + captured.err
    assert str(cache_root) not in serialized
    assert manifest.entries[0].sha256 not in serialized
    assert manifest.entries[0].logical_key not in serialized


def test_gold_materialize_rejects_v1_mode_before_materializer_call(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证下载型 v1 manifest 不能进入派生物化边界。

    输入参数：tmp_path 提供未使用本地路径；capsys 捕获错误；
        monkeypatch 注入不可达 materializer 与 pinned task context。
    输出返回：CLI 在媒体工具或 cache I/O 前返回 2，
        materializer 调用计数为零且输出无路径/摘要/内容。
    """

    manifest, resolved = _resolved_pinned_gold()
    monkeypatch.setattr(
        "paraguibench.cli.main._load_prepared_task_context",
        lambda _arguments: (REPO_ROOT, object()),
    )
    monkeypatch.setattr(
        "paraguibench.cli.main._load_task_gold_context",
        lambda **_kwargs: resolved,
    )
    calls = 0

    def forbidden_materialize(**_kwargs: object) -> object:
        """拒绝 v1 manifest 越过模式隔离。

        输入参数：_kwargs 为误调用的 materializer 参数。
        输出返回：不返回；调用即使测试失败。
        """

        nonlocal calls
        calls += 1
        raise AssertionError("v1 gold 不得物化")

    fake_module = ModuleType("paraguibench.runtime.derived_gold")
    fake_module.materialize_derived_gold = forbidden_materialize  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)
    gold_root = tmp_path / "must-not-create-gold"

    exit_code = main(
        [
            "gold",
            "materialize",
            "--repo-root",
            str(REPO_ROOT),
            "--task-id",
            "Operation-FileOperate-CombinationDocs-015",
            "--asset-cache-root",
            str(tmp_path / "assets"),
            "--gold-cache-root",
            str(gold_root),
            "--ffmpeg-path",
            str(tmp_path / "ffmpeg"),
            "--ffprobe-path",
            str(tmp_path / "ffprobe"),
            "--timeout-seconds",
            "30",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == 0
    assert captured.out == ""
    assert captured.err == "error=ValueError\n"
    assert not gold_root.exists()
    serialized = captured.out + captured.err
    assert str(gold_root) not in serialized
    assert manifest.entries[0].sha256 not in serialized
    assert manifest.entries[0].logical_key not in serialized
