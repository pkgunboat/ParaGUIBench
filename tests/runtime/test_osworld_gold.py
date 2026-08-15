"""OSWorld task、artifact spec 与 evaluator-only gold 的闭集绑定测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from paraguibench.runtime.gold_assets import load_gold_asset_manifest
from paraguibench.runtime.osworld_gold import (
    OSWorldGoldBindingError,
    ResolvedOSWorldTaskGold,
    TaskGoldMode,
    bind_osworld_task_gold,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPO_ROOT
    / "benchmark"
    / "gold"
    / "manifests"
    / "Operation-FileOperate-CombinationDocs-015.json"
)
BIBTEX_TASK_ID = "Operation-FileOperate-CombinationDocs-015"
BIBTEX_TASK_UID = "9f55fdb6-a749-4170-91a2-bebddd3492d7"
BIBTEX_EVALUATOR_PATH = "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
BIBTEX_GOLD_KEY = "osworld-gold:df67aebb-fb3a-44fd-b75b-51b6012df509:expected:0:v1"
BATCH_TASK_ID = "Operation-FileOperate-BatchOperation-003"
BATCH_TASK_UID = "c919165f-cdfb-413a-8e00-424a0a133620"
BATCH_SOURCE_ID = "5df7b33a-9f77-4101-823e-02f863e1c1ae"
BATCH_EVALUATOR_PATH = f"eval/osworld_scripts/{BATCH_SOURCE_ID}.json"
BATCH_GOLD_KEY = f"osworld-gold:{BATCH_SOURCE_ID}:expected:0:v1"
BATCH_MANIFEST_PATH = (
    REPO_ROOT / "benchmark" / "gold" / "manifests" / f"{BATCH_TASK_ID}.json"
)
SETTINGS_TASK_ID = "Operation-FileOperate-Settings-001"
SETTINGS_TASK_UID = "9b5220d5-f1f0-4db9-902d-ad41aae4d775"
SETTINGS_EVALUATOR_PATH = f"eval/osworld_scripts/{SETTINGS_TASK_UID}.json"
SETTINGS_GOLD_KEY = "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:expected:0:v2"
SETTINGS_ASSET_MANIFEST_REFERENCE = (
    "benchmark/assets/manifests/Operation-FileOperate-Settings-001.json"
)
SETTINGS_MANIFEST_PATH = (
    REPO_ROOT / "benchmark" / "gold" / "manifests" / f"{SETTINGS_TASK_ID}.json"
)


@pytest.mark.parametrize(
    ("mode", "manifest_path", "logical_key"),
    (
        (
            TaskGoldMode.PINNED_DOWNLOAD_MANIFEST,
            SETTINGS_MANIFEST_PATH,
            SETTINGS_GOLD_KEY,
        ),
        (
            TaskGoldMode.PRIVATE_DERIVED_MANIFEST,
            MANIFEST_PATH,
            BIBTEX_GOLD_KEY,
        ),
    ),
)
def test_resolver_rejects_cross_paired_mode_and_manifest_type(
    tmp_path: Path,
    mode: TaskGoldMode,
    manifest_path: Path,
    logical_key: str,
) -> None:
    """验证下载与私有派生模式不能交叉绑定 manifest 类型。

    输入参数：tmp_path 为不会被访问的私有 cache；mode、
        manifest_path 与 logical_key 构成两组参数化交叉错配。
    输出返回：无；两组错配均在 resolver 构造前抛固定绑定错误。
    """

    resolved = ResolvedOSWorldTaskGold(
        mode=mode,
        manifest=load_gold_asset_manifest(manifest_path),
        logical_keys=(logical_key,),
    )

    with pytest.raises(OSWorldGoldBindingError):
        resolved.build_resolver(tmp_path / "must-not-exist")

    assert not (tmp_path / "must-not-exist").exists()


def test_resolver_rejects_pinned_mode_with_v2_logical_key(tmp_path: Path) -> None:
    """验证手工构造的 PINNED 绑定不能携带 v2 logical key。

    输入参数：tmp_path 为不应被访问的私有 cache；manifest
        是 v1 精确类型，但条目与 resolved key 都伪造为 Settings v2。
    输出返回：无；在 resolver 构造前抛固定绑定错误，不创建
        cache 目录。
    """

    downloaded = load_gold_asset_manifest(MANIFEST_PATH)
    derived = load_gold_asset_manifest(SETTINGS_MANIFEST_PATH)
    handcrafted = replace(
        downloaded,
        manifest_id=f"{SETTINGS_TASK_ID}-gold-v1",
        entries=(
            replace(
                downloaded.entries[0],
                logical_key=SETTINGS_GOLD_KEY,
                media_type="image/png",
                provenance=derived.entries[0].provenance,
            ),
        ),
    )
    resolved = ResolvedOSWorldTaskGold(
        mode=TaskGoldMode.PINNED_DOWNLOAD_MANIFEST,
        manifest=handcrafted,
        logical_keys=(SETTINGS_GOLD_KEY,),
    )
    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(OSWorldGoldBindingError):
        resolved.build_resolver(cache_root)

    assert not cache_root.exists()


def test_resolver_rejects_private_derived_mode_with_v1_logical_key(
    tmp_path: Path,
) -> None:
    """验证 PRIVATE_DERIVED 绑定的 key 必须使用 v2 身份。

    输入参数：tmp_path 为不应被访问的私有 cache；
        manifest 是 canonical Settings v2，但手工 resolved key 使用 v1。
    输出返回：无；在 derived resolver/cache I/O 前抛固定
        绑定错误。
    """

    resolved = ResolvedOSWorldTaskGold(
        mode=TaskGoldMode.PRIVATE_DERIVED_MANIFEST,
        manifest=load_gold_asset_manifest(SETTINGS_MANIFEST_PATH),
        logical_keys=(BIBTEX_GOLD_KEY,),
    )
    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(OSWorldGoldBindingError):
        resolved.build_resolver(cache_root)

    assert not cache_root.exists()


def test_resolver_rejects_non_tuple_logical_key_container(tmp_path: Path) -> None:
    """验证手工绑定的 logical key 容器必须是精确 tuple。

    输入参数：tmp_path 为不应被访问的私有 cache；
        resolved 携带内容正确但类型为 list 的 key 容器。
    输出返回：无；公开 resolver 边界固定失败，不创建 cache。
    """

    resolved = ResolvedOSWorldTaskGold(
        mode=TaskGoldMode.PINNED_DOWNLOAD_MANIFEST,
        manifest=load_gold_asset_manifest(MANIFEST_PATH),
        logical_keys=[BIBTEX_GOLD_KEY],  # type: ignore[arg-type]
    )
    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(OSWorldGoldBindingError):
        resolved.build_resolver(cache_root)

    assert not cache_root.exists()


def test_resolver_rejects_duplicate_logical_keys(tmp_path: Path) -> None:
    """验证手工绑定不能重复声明同一 logical key。

    输入参数：tmp_path 为不应被访问的私有 cache；
        resolved 在精确 tuple 中重复两次合法 v1 key。
    输出返回：无；公开 resolver 边界固定失败，不创建 cache。
    """

    resolved = ResolvedOSWorldTaskGold(
        mode=TaskGoldMode.PINNED_DOWNLOAD_MANIFEST,
        manifest=load_gold_asset_manifest(MANIFEST_PATH),
        logical_keys=(BIBTEX_GOLD_KEY, BIBTEX_GOLD_KEY),
    )
    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(OSWorldGoldBindingError):
        resolved.build_resolver(cache_root)

    assert not cache_root.exists()


def test_resolver_maps_non_string_logical_key_to_binding_error(
    tmp_path: Path,
) -> None:
    """验证手工绑定的非字符串 key 只产生固定领域错误。

    输入参数：tmp_path 为不应被访问的私有 cache；
        resolved 的精确 tuple 携带整数而非 exact str。
    输出返回：无；公开 resolver 边界抛脱敏绑定错误，
        不泄漏底层 regex ``TypeError`` 且不创建 cache。
    """

    resolved = ResolvedOSWorldTaskGold(
        mode=TaskGoldMode.PINNED_DOWNLOAD_MANIFEST,
        manifest=load_gold_asset_manifest(MANIFEST_PATH),
        logical_keys=(1,),  # type: ignore[arg-type]
    )
    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(OSWorldGoldBindingError) as caught:
        resolved.build_resolver(cache_root)

    assert str(caught.value) == "OSWORLD_GOLD_BINDING_INVALID"
    assert not cache_root.exists()


def test_inline_gold_task_binds_to_none_without_touching_cache(
    tmp_path: Path,
) -> None:
    """验证内联 gold task 不伪造外部 manifest 或缓存目录。

    输入参数：
        tmp_path：pytest 提供的不存在缓存父路径。
    输出返回值：
        无；mode 为 NONE、required key 为空，验证通过且不创建目录。
    """

    resolved = bind_osworld_task_gold(
        "Operation-FileOperate-BatchOperation-001",
        manifest=None,
    )
    cache_root = tmp_path / "must-not-exist"

    availability = resolved.verify(cache_root)

    assert resolved.mode is TaskGoldMode.NONE
    assert resolved.manifest is None
    assert resolved.logical_keys == ()
    assert availability.status.value == "AVAILABLE"
    assert availability.requested_count == 0
    assert resolved.build_resolver(cache_root) is None
    assert not cache_root.exists()


def test_resolver_rejects_none_mode_with_non_tuple_empty_keys(
    tmp_path: Path,
) -> None:
    """验证 NONE 模式也要求精确的空 tuple key 容器。

    输入参数：tmp_path 为不应被访问的 cache；手工
        resolved 使用空 list，其真值与合法空 tuple 相同但类型不同。
    输出返回：无；固定失败且不创建 cache；canonical NONE
        的 ``(NONE, None, ())`` 行为由相邻回归继续保证。
    """

    resolved = ResolvedOSWorldTaskGold(
        mode=TaskGoldMode.NONE,
        manifest=None,
        logical_keys=[],  # type: ignore[arg-type]
    )
    cache_root = tmp_path / "must-not-exist"

    with pytest.raises(OSWorldGoldBindingError):
        resolved.build_resolver(cache_root)

    assert not cache_root.exists()


def test_external_gold_task_requires_exact_manifest() -> None:
    """验证 015 缺失 gold manifest 时在 VM/Agent I/O 前失败关闭。

    输入参数：
        无；按 canonical task ID 请求外部 gold，但不提供 manifest。
    输出返回值：
        无；只抛固定绑定错误，不允许 source 在运行时临时寻找 gold。
    """

    with pytest.raises(OSWorldGoldBindingError) as caught:
        bind_osworld_task_gold(
            BIBTEX_TASK_ID,
            manifest=None,
            task_uid=BIBTEX_TASK_UID,
            evaluator_path=BIBTEX_EVALUATOR_PATH,
        )

    assert str(caught.value) == "OSWORLD_GOLD_BINDING_INVALID"


def test_combinationdocs_manifest_binds_spec_and_builds_offline_resolver(
    tmp_path: Path,
) -> None:
    """验证 015 manifest 精确闭合 task/spec/provenance 与离线缓存。

    输入参数：
        tmp_path：pytest 提供的 evaluator 私有缓存目录。
    输出返回值：
        无；绑定结果只含固定 key，resolver 能完整校验真实 manifest 字节。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    resolved = bind_osworld_task_gold(
        BIBTEX_TASK_ID,
        manifest=manifest,
        task_uid=BIBTEX_TASK_UID,
        evaluator_path=BIBTEX_EVALUATOR_PATH,
    )
    cache_root = tmp_path / "gold-cache"

    # 使用 manifest 固定的真实 gold 字节不可从仓库分发，因此测试仅验证
    # resolver 装配身份；字节级读取由 test_gold_assets.py 独立覆盖。
    resolver = resolved.build_resolver(cache_root)

    assert resolved.mode is TaskGoldMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is manifest
    assert resolved.logical_keys == (BIBTEX_GOLD_KEY,)
    assert resolver is not None
    assert not cache_root.exists()


def test_batch_manifest_binds_canonical_uid_and_exact_zip_gold() -> None:
    """验证 BatchOperation-003 的 evaluator-only ZIP gold 正式绑定。

    输入参数：
        无；加载仓库内固定 gold manifest，并使用 canonical task UID 与
        source evaluator 路径调用 production binder。
    输出返回值：
        无；绑定必须接受 canonical UID 与 source evaluator UUID 不同的
        合法身份，并固定唯一 ZIP logical key、size 与 SHA。
    """

    manifest = load_gold_asset_manifest(BATCH_MANIFEST_PATH)
    resolved = bind_osworld_task_gold(
        BATCH_TASK_ID,
        manifest=manifest,
        task_uid=BATCH_TASK_UID,
        evaluator_path=BATCH_EVALUATOR_PATH,
    )

    assert resolved.mode is TaskGoldMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.logical_keys == (BATCH_GOLD_KEY,)
    assert manifest.entries[0].size == 2_935_633
    assert manifest.entries[0].sha256 == (
        "5d028f5cb57e8f04fd8e5a65370959da91e7c873601bc1fcff9dc8ff5b72005f"
    )
    assert manifest.entries[0].media_type == "application/zip"


def test_settings_manifest_binds_private_derived_gold_without_download_mode() -> None:
    """验证 Settings-001 只绑定已物化的 host-only derived gold。

    输入参数：无；加载正式 v2 manifest，并使用 canonical task UID
        与 evaluator path 调用 production binder。
    输出返回值：无；绑定模式必须是私有派生而非下载，且唯一
        logical key 使用 v2 身份，不会把 9.042s 旧 v1 gold 重新接线。
    """

    manifest = load_gold_asset_manifest(SETTINGS_MANIFEST_PATH)

    resolved = bind_osworld_task_gold(
        SETTINGS_TASK_ID,
        manifest=manifest,
        task_uid=SETTINGS_TASK_UID,
        evaluator_path=SETTINGS_EVALUATOR_PATH,
        asset_manifest_reference=SETTINGS_ASSET_MANIFEST_REFERENCE,
    )

    assert resolved.mode is TaskGoldMode.PRIVATE_DERIVED_MANIFEST
    assert resolved.logical_keys == (SETTINGS_GOLD_KEY,)
    assert resolved.manifest is manifest


def test_settings_binding_rejects_handcrafted_v1_download_manifest() -> None:
    """验证 Settings-001 不能绑定手工伪造的 v1 下载 manifest。

    输入参数：无；将合法 v1 容器的条目替换为 Settings 精确
        v2 logical key、媒体类型与 provenance，但不提供 input
        manifest 引用。
    输出返回：无；binder 必须在 resolver/cache I/O 前固定失败，
        不允许降级为 ``PINNED_DOWNLOAD_MANIFEST``。
    """

    downloaded = load_gold_asset_manifest(MANIFEST_PATH)
    derived = load_gold_asset_manifest(SETTINGS_MANIFEST_PATH)
    handcrafted = replace(
        downloaded,
        manifest_id=f"{SETTINGS_TASK_ID}-gold-v1",
        entries=(
            replace(
                downloaded.entries[0],
                logical_key=SETTINGS_GOLD_KEY,
                media_type="image/png",
                provenance=derived.entries[0].provenance,
            ),
        ),
    )

    with pytest.raises(OSWorldGoldBindingError):
        bind_osworld_task_gold(
            SETTINGS_TASK_ID,
            manifest=handcrafted,
            task_uid=SETTINGS_TASK_UID,
            evaluator_path=SETTINGS_EVALUATOR_PATH,
        )


@pytest.mark.parametrize(
    "asset_manifest_reference",
    (
        None,
        "benchmark/assets/manifests/Operation-FileOperate-BatchOperation-003.json",
    ),
)
def test_settings_binding_rejects_missing_or_cross_task_input_manifest_reference(
    asset_manifest_reference: str | None,
) -> None:
    """验证 Settings 派生 gold 必须绑定 canonical input manifest 引用。

    输入参数：asset_manifest_reference 为缺失或跨任务输入引用。
    输出返回：无；两类漂移均在 cache/resolver I/O 前失败。
    """

    manifest = load_gold_asset_manifest(SETTINGS_MANIFEST_PATH)

    with pytest.raises(OSWorldGoldBindingError):
        bind_osworld_task_gold(
            SETTINGS_TASK_ID,
            manifest=manifest,
            task_uid=SETTINGS_TASK_UID,
            evaluator_path=SETTINGS_EVALUATOR_PATH,
            asset_manifest_reference=asset_manifest_reference,
        )


@pytest.mark.parametrize(
    "mutator",
    (
        lambda manifest: replace(manifest, schema_version=1),
        lambda manifest: replace(
            manifest,
            asset_set_id="Operation-FileOperate-BatchOperation-003",
        ),
        lambda manifest: replace(
            manifest,
            license=replace(
                manifest.license,
                basis="caller_asserted",
            ),
        ),
    ),
)
def test_settings_binding_rejects_v2_identity_or_license_semantic_drift(
    mutator: object,
) -> None:
    """验证已解析 v2 dataclass 被替换后也不能绕过绑定。

    输入参数：mutator 分别漂移 schema、asset-set 或私有派生
        license basis，不改变其他条目字段。
    输出返回：任一 v2 身份/许可语义漂移都在 resolver 前
        抛固定 ``OSWorldGoldBindingError``。
    """

    manifest = load_gold_asset_manifest(SETTINGS_MANIFEST_PATH)
    mutated = mutator(manifest)  # type: ignore[operator]

    with pytest.raises(OSWorldGoldBindingError):
        bind_osworld_task_gold(
            SETTINGS_TASK_ID,
            manifest=mutated,
            task_uid=SETTINGS_TASK_UID,
            evaluator_path=SETTINGS_EVALUATOR_PATH,
            asset_manifest_reference=SETTINGS_ASSET_MANIFEST_REFERENCE,
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda manifest: replace(manifest, manifest_id="wrong-gold-v1"),
        lambda manifest: replace(
            manifest,
            entries=(
                replace(
                    manifest.entries[0],
                    provenance=replace(
                        manifest.entries[0].provenance,
                        source_evaluator_id=("00000000-0000-4000-8000-000000000000"),
                    ),
                ),
            ),
        ),
        lambda manifest: replace(
            manifest,
            entries=(
                replace(
                    manifest.entries[0],
                    media_type="text/csv",
                ),
            ),
        ),
        lambda manifest: replace(
            manifest,
            entries=(
                replace(
                    manifest.entries[0],
                    provenance=replace(
                        manifest.entries[0].provenance,
                        source_contract_sha256="0" * 64,
                    ),
                ),
            ),
        ),
    ],
)
def test_binding_rejects_manifest_identity_or_provenance_drift(
    mutator: object,
) -> None:
    """验证已解析 dataclass 被替换后也不能绕过跨文件绑定。

    输入参数：
        mutator：pytest 提供的 manifest 身份或 provenance 漂移函数。
    输出返回值：
        无；绑定层统一抛固定错误，不信任手工构造的 dataclass。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
    mutated = mutator(manifest)  # type: ignore[operator]

    with pytest.raises(OSWorldGoldBindingError):
        bind_osworld_task_gold(
            BIBTEX_TASK_ID,
            manifest=mutated,
            task_uid=BIBTEX_TASK_UID,
            evaluator_path=BIBTEX_EVALUATOR_PATH,
        )


def test_binding_rejects_v1_bool_int_type_confusion() -> None:
    """验证 binder 不使用 Python 宽松 bool/int 等值语义。

    输入参数：无；手工把正式 v1 provenance 的整数
        ``expected_index=0`` 替换为宽松等值的 ``False``。
    输出返回：无；binder 在返回 Resolved 前抛固定绑定
        错误，不把手工 dataclass 当作 loader 产物。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)
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

    with pytest.raises(OSWorldGoldBindingError):
        bind_osworld_task_gold(
            BIBTEX_TASK_ID,
            manifest=drifted,
            task_uid=BIBTEX_TASK_UID,
            evaluator_path=BIBTEX_EVALUATOR_PATH,
        )


@pytest.mark.parametrize(
    ("task_uid", "evaluator_path"),
    [
        (
            "00000000-0000-4000-8000-000000000000",
            BIBTEX_EVALUATOR_PATH,
        ),
        (
            BIBTEX_TASK_UID,
            "eval/osworld_scripts/00000000-0000-4000-8000-000000000000.json",
        ),
    ],
)
def test_binding_rejects_canonical_task_evaluator_identity_drift(
    task_uid: str,
    evaluator_path: str,
) -> None:
    """验证 task_uid/evaluator_path 必须与 artifact spec 的 source 一致。

    输入参数：
        task_uid：参数化 canonical task evaluator UUID。
        evaluator_path：参数化 source evaluator JSON 相对路径。
    输出返回值：
        无；任一身份漂移都在 cache/resolver 构造前固定失败。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)

    with pytest.raises(OSWorldGoldBindingError):
        bind_osworld_task_gold(
            BIBTEX_TASK_ID,
            manifest=manifest,
            task_uid=task_uid,
            evaluator_path=evaluator_path,
        )


def test_unrelated_task_cannot_attach_combinationdocs_gold() -> None:
    """验证无 external-gold spec 的任务不能附加任意 gold manifest。

    输入参数：
        无；把 015 manifest 绑定到普通 OSWorld task。
    输出返回值：
        无；绑定在 resolver 构造和缓存访问前失败。
    """

    manifest = load_gold_asset_manifest(MANIFEST_PATH)

    with pytest.raises(OSWorldGoldBindingError):
        bind_osworld_task_gold(
            "Operation-FileOperate-BatchOperation-001",
            manifest=manifest,
        )
