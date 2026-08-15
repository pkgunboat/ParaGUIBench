"""OSWorld artifact spec 与 evaluator-only pinned gold 的运行时绑定。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re

from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
    ArtifactEvidenceSpec,
)
from paraguibench.integrations.osworld.artifact_gold_media import (
    OSWorldArtifactGoldMediaContractError,
    artifact_gold_media_types,
)
from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
)
from paraguibench.runtime.gold_assets import (
    DerivedGoldAssetManifest,
    GoldAssetManifest,
    GoldAssetResolver,
    GoldAvailability,
    GoldAvailabilityStatus,
    GoldManifestError,
    validate_derived_gold_asset_manifest,
    validate_gold_asset_manifest,
)


_EXPECTED_INDEX_PATTERN = re.compile(r":expected:(0|[1-9][0-9]*):v(1|2)$")
_SETTINGS001_TASK_ID = "Operation-FileOperate-Settings-001"
_SETTINGS001_ASSET_MANIFEST_REFERENCE = (
    "benchmark/assets/manifests/Operation-FileOperate-Settings-001.json"
)
_EXTERNAL_GOLD_CANONICAL_TASK_UIDS = {
    task_id: prepare_spec.task_uid
    for task_id, prepare_spec in ARTIFACT_FAMILY_TASK_PREPARE_SPECS.items()
} | {
    "Operation-FileOperate-CombinationDocs-015": (
        "9f55fdb6-a749-4170-91a2-bebddd3492d7"
    ),
}


class OSWorldGoldBindingError(RuntimeError):
    """表示 task、evidence spec 与 gold manifest 无法形成精确闭集。"""

    def __init__(self) -> None:
        """构造不携带 task、locator、摘要或路径的固定公开错误。

        输入参数：
            无。
        输出返回值：
            无；异常文本固定为稳定领域代码。
        """

        super().__init__("OSWORLD_GOLD_BINDING_INVALID")


class TaskGoldMode(StrEnum):
    """表示一个任务是否依赖 evaluator-only 外部 gold。"""

    NONE = "none"
    PINNED_DOWNLOAD_MANIFEST = "pinned_download_manifest"
    PRIVATE_DERIVED_MANIFEST = "private_derived_manifest"


@dataclass(frozen=True, slots=True)
class ResolvedOSWorldTaskGold:
    """保存已与 canonical artifact spec 闭合的 gold 依赖。"""

    mode: TaskGoldMode
    manifest: GoldAssetManifest | DerivedGoldAssetManifest | None
    logical_keys: tuple[str, ...]

    def build_resolver(self, cache_root: Path) -> GoldAssetResolver | None:
        """构造纯离线 resolver，零外部 gold 任务不触碰缓存。

        输入参数：
            cache_root：显式 evaluator-only 私有缓存根。
        输出返回值：
            外部 gold 任务返回绑定 manifest 的 ``GoldAssetResolver``；
            ``NONE`` 模式返回 ``None``。
        异常：
            TypeError：cache_root 不是 ``Path``。
            OSWorldGoldBindingError：内部 mode/manifest 组合被非法构造。
        """

        if not isinstance(cache_root, Path):
            raise TypeError("cache_root 必须是 Path")
        if self.mode is TaskGoldMode.NONE:
            if (
                self.manifest is not None
                or type(self.logical_keys) is not tuple
                or self.logical_keys
            ):
                raise OSWorldGoldBindingError
            return None
        exact_manifest_pair = (
            self.mode is TaskGoldMode.PINNED_DOWNLOAD_MANIFEST
            and type(self.manifest) is GoldAssetManifest
        ) or (
            self.mode is TaskGoldMode.PRIVATE_DERIVED_MANIFEST
            and type(self.manifest) is DerivedGoldAssetManifest
        )
        if (
            not exact_manifest_pair
            or type(self.logical_keys) is not tuple
            or not self.logical_keys
            or any(type(item) is not str for item in self.logical_keys)
            or len(self.logical_keys) != len(set(self.logical_keys))
        ):
            raise OSWorldGoldBindingError
        expected_key_version = (
            "1" if self.mode is TaskGoldMode.PINNED_DOWNLOAD_MANIFEST else "2"
        )
        if any(
            (match := _EXPECTED_INDEX_PATTERN.search(logical_key)) is None
            or match.group(2) != expected_key_version
            for logical_key in self.logical_keys
        ):
            raise OSWorldGoldBindingError
        return GoldAssetResolver(
            manifest=self.manifest,
            cache_root=cache_root,
        )

    def verify(self, cache_root: Path) -> GoldAvailability:
        """在运行前离线验证当前 task 的全部 evaluator gold。

        输入参数：
            cache_root：显式 evaluator-only 私有缓存根。
        输出返回值：
            零依赖任务返回 ``AVAILABLE, 0``；外部 gold 闭集完整时返回
            resolver 的 ``AVAILABLE`` 与固定条目数。
        异常：
            GoldAssetError：缓存缺失、权限/字节身份错误或无法读取。
            OSWorldGoldBindingError：绑定对象内部组合不合法。
        """

        resolver = self.build_resolver(cache_root)
        if resolver is None:
            return GoldAvailability(
                status=GoldAvailabilityStatus.AVAILABLE,
                requested_count=0,
            )
        return resolver.verify_required(self.logical_keys)


def bind_osworld_task_gold(
    task_id: str,
    manifest: GoldAssetManifest | DerivedGoldAssetManifest | None,
    *,
    task_uid: str | None = None,
    evaluator_path: str | None = None,
    asset_manifest_reference: str | None = None,
) -> ResolvedOSWorldTaskGold:
    """把 task 的 external-gold metric 与 manifest 精确双向绑定。

    输入参数：
        task_id：canonical ParaGUIBench task ID。
        manifest：任务声明并经严格 loader 解析的 gold manifest；无声明时
            为 ``None``。
        task_uid：canonical ParaGUIBench task UID；只有 external gold 任务
            必须提供，且不假设它等于 source evaluator UUID。
        evaluator_path：canonical task 的 evaluator JSON 仓库相对路径；
            只有 external gold 任务必须提供。
        asset_manifest_reference：canonical task 的 input manifest 相对
            引用；仅 Settings-001 私有派生 gold 必须提供。
    输出返回值：
        ``NONE`` 或 ``PINNED_DOWNLOAD_MANIFEST`` 的不可变绑定；后者固定
        所有 metric logical key，且逐条核对 OSWorld task/evaluator/contract。
    异常：
        OSWorldGoldBindingError：任务与 spec 身份无效、缺失/多余 manifest，
            logical key 闭集或 provenance 任一字段不一致。
    """

    if not isinstance(task_id, str) or not task_id:
        raise OSWorldGoldBindingError
    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS.get(task_id)
    if spec is None:
        if manifest is not None or asset_manifest_reference is not None:
            raise OSWorldGoldBindingError
        return _no_external_gold()

    logical_keys = _external_gold_keys(spec)
    if not logical_keys:
        if manifest is not None or asset_manifest_reference is not None:
            raise OSWorldGoldBindingError
        return _no_external_gold()
    if not isinstance(manifest, (GoldAssetManifest, DerivedGoldAssetManifest)):
        raise OSWorldGoldBindingError
    if isinstance(manifest, DerivedGoldAssetManifest):
        try:
            manifest = validate_derived_gold_asset_manifest(manifest)
        except GoldManifestError:
            raise OSWorldGoldBindingError from None
    elif isinstance(manifest, GoldAssetManifest):
        try:
            manifest = validate_gold_asset_manifest(manifest)
        except GoldManifestError:
            raise OSWorldGoldBindingError from None
    expected_evaluator_path = f"eval/osworld_scripts/{spec.source_evaluator_id}.json"
    expected_task_uid = _EXTERNAL_GOLD_CANONICAL_TASK_UIDS.get(task_id)
    if (
        not isinstance(expected_task_uid, str)
        or task_uid != expected_task_uid
        or evaluator_path != expected_evaluator_path
    ):
        raise OSWorldGoldBindingError
    is_settings = task_id == _SETTINGS001_TASK_ID
    derived_settings = (
        is_settings
        and type(manifest) is DerivedGoldAssetManifest
        and manifest.schema_version == 2
        and manifest.manifest_id == f"{task_id}-gold-v2"
        and manifest.distribution_policy == "private_materialization_only"
        and asset_manifest_reference == _SETTINGS001_ASSET_MANIFEST_REFERENCE
        and manifest.asset_manifest == asset_manifest_reference
        and manifest.asset_set_id == task_id
        and manifest.license.status == "verified"
        and manifest.license.spdx_expression == "Apache-2.0"
        and manifest.license.evidence_ref
        == "https://huggingface.co/datasets/xlangai/ubuntu_osworld_file_cache"
        and manifest.license.basis == "derived_from_source_input"
        and manifest.license.distribution == manifest.distribution_policy
    )
    downloaded_gold = (
        not is_settings
        and type(manifest) is GoldAssetManifest
        and manifest.manifest_id == f"{task_id}-gold-v1"
        and asset_manifest_reference is None
    )
    if not (derived_settings or downloaded_gold):
        raise OSWorldGoldBindingError

    if (
        not isinstance(manifest.entries, tuple)
        or not manifest.entries
        or any(not isinstance(entry.logical_key, str) for entry in manifest.entries)
    ):
        raise OSWorldGoldBindingError
    entries_by_key = {entry.logical_key: entry for entry in manifest.entries}
    if len(entries_by_key) != len(manifest.entries) or tuple(
        sorted(entries_by_key)
    ) != tuple(sorted(logical_keys)):
        raise OSWorldGoldBindingError
    expected_media_types = _external_gold_media_types(spec)
    if set(expected_media_types) != set(logical_keys):
        raise OSWorldGoldBindingError
    for logical_key in logical_keys:
        entry = entries_by_key.get(logical_key)
        if entry is None:
            raise OSWorldGoldBindingError
        provenance = entry.provenance
        index_match = _EXPECTED_INDEX_PATTERN.search(logical_key)
        if (
            index_match is None
            or provenance.expected_index != int(index_match.group(1))
            or entry.media_type not in expected_media_types[logical_key]
            or provenance.source_benchmark != "OSWorld"
            or provenance.source_task_id != spec.source_task_id
            or provenance.source_evaluator_id != spec.source_evaluator_id
            or provenance.source_contract_sha256 != spec.source_contract_sha256
        ):
            raise OSWorldGoldBindingError

    return ResolvedOSWorldTaskGold(
        mode=(
            TaskGoldMode.PRIVATE_DERIVED_MANIFEST
            if derived_settings
            else TaskGoldMode.PINNED_DOWNLOAD_MANIFEST
        ),
        manifest=manifest,
        logical_keys=logical_keys,
    )


def _external_gold_keys(spec: ArtifactEvidenceSpec) -> tuple[str, ...]:
    """从可信 artifact spec 收集去重且顺序稳定的 external gold key。

    输入参数：
        spec：仓库内版本化 OSWorld artifact evidence spec。
    输出返回值：
        按槽位与 metric 声明顺序排列的唯一 logical key tuple。
    异常：
        OSWorldGoldBindingError：external-gold metric 未声明 key，或非
            external metric 携带 key，说明 spec 与运行时协议不一致。
    """

    ordered: list[str] = []
    for slot in spec.artifact_slots:
        for metric in slot.metrics:
            if metric.expected_kind == "gold-assets":
                if (
                    not isinstance(metric.gold_keys, tuple)
                    or not metric.gold_keys
                    or any(
                        not isinstance(key, str) or not key for key in metric.gold_keys
                    )
                ):
                    raise OSWorldGoldBindingError
                for key in metric.gold_keys:
                    if key not in ordered:
                        ordered.append(key)
            elif metric.gold_keys:
                raise OSWorldGoldBindingError
    return tuple(ordered)


def _external_gold_media_types(
    spec: ArtifactEvidenceSpec,
) -> dict[str, frozenset[str]]:
    """从固定 metric contract 推导逐 logical key 的媒体闭集。

    输入参数：
        spec：仓库内版本化 OSWorld artifact evidence spec。
    输出返回值：
        logical gold key 到受信不可变媒体 allowlist 的映射。
    异常：
        OSWorldGoldBindingError：contract 未注册、key 数量不一致或同一 key
            被矛盾媒体家族重复声明。
    """

    expected: dict[str, frozenset[str]] = {}
    for slot in spec.artifact_slots:
        for metric in slot.metrics:
            if metric.expected_kind != "gold-assets":
                continue
            try:
                media_types = artifact_gold_media_types(metric.contract_id)
            except OSWorldArtifactGoldMediaContractError:
                raise OSWorldGoldBindingError from None
            if len(media_types) != len(metric.gold_keys):
                raise OSWorldGoldBindingError
            for logical_key, allowed_media_types in zip(
                metric.gold_keys,
                media_types,
                strict=True,
            ):
                prior = expected.get(logical_key)
                if prior is not None and prior != allowed_media_types:
                    raise OSWorldGoldBindingError
                expected[logical_key] = allowed_media_types
    return expected


def _no_external_gold() -> ResolvedOSWorldTaskGold:
    """构造不触碰缓存的零外部 gold 绑定。

    输入参数：
        无。
    输出返回值：
        manifest 为 ``None``、logical key 为空的不可变绑定。
    """

    return ResolvedOSWorldTaskGold(
        mode=TaskGoldMode.NONE,
        manifest=None,
        logical_keys=(),
    )


__all__ = [
    "OSWorldGoldBindingError",
    "ResolvedOSWorldTaskGold",
    "TaskGoldMode",
    "bind_osworld_task_gold",
]
