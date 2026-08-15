"""OSWorld artifact-state 的受控 finalize、getter 与 metric runtime 证据源。"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any

from paraguibench.evaluation.osworld.artifact_metrics import (
    ArtifactMetricEvaluationError,
    evaluate_artifact_metric,
)
from paraguibench.integrations.osworld.artifact_contracts import (
    ArtifactMetricObservation,
    ArtifactSlotObservation,
    ArtifactStateObservation,
)
from paraguibench.integrations.osworld.artifact_family_evidence import (
    LEGACY_OSWORLD_ARTIFACT_TASK_IDS,
    ArtifactFamilyCapture,
    OSWorldArtifactFamilyEvidenceError,
    OSWorldArtifactFamilyEvidenceSource,
    artifact_family_single_file_byte_limit,
    evaluate_inline_directory_membership,
)
from paraguibench.integrations.osworld.artifact_gold_media import (
    OSWorldArtifactGoldMediaContractError,
    artifact_gold_media_types,
)
from paraguibench.integrations.osworld.artifact_metric_projection import (
    OSWorldArtifactMetricProjectionError,
    project_verified_artifact_metric_values,
)
from paraguibench.integrations.osworld.controller import (
    OSWorldGuestPathMissingError,
)
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
    ArtifactEvidenceSpec,
    ArtifactEvidenceSpecError,
    ArtifactMetricEvidenceSpec,
    ArtifactSlotEvidenceSpec,
    canonical_artifact_evidence_spec_json,
    project_inline_artifact_metric_inputs,
    validate_artifact_evidence_spec,
)
from paraguibench.runtime.osworld_artifact_finalization import (
    OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS,
)
from paraguibench.runtime.gold_assets import GoldAssetManifest, GoldAssetResolver
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
    OSWorldArtifactComponentGoldProof,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_DIRECTORY_GETTER = "image-directory-hash-manifest"
_SINGLE_FILE_GETTER = "file"
_BIBTEX_TASK_ID = "Operation-FileOperate-CombinationDocs-015"
_SUPPORTED_TASK_IDS = (
    frozenset(
        {
            "Operation-FileOperate-BatchOperation-001",
            _BIBTEX_TASK_ID,
        }
    )
    | LEGACY_OSWORLD_ARTIFACT_TASK_IDS
)
_MAX_POSIX_NAME_BYTES = 255
_MAX_DECODED_BYTES_PER_PIXEL_BUDGET = 16
_BASE64_HTTP_ENVELOPE_OVERHEAD_BYTES = 4_096
_MAX_SINGLE_FILE_RESPONSE_BYTES = 16_777_216
_EXPECTED_IMAGE_GETTER_OPTIONS = {
    "content_detection": "pillow-open-no-suffix-filter",
    "digest_algorithm": "sha256",
    "duplicate_digest_policy": "last-observed-entry-wins",
    "hash_projection": "pillow-image-tobytes",
    "member_selection": "all-direct-members",
    "symlink_policy": "nofollow-fail-closed",
}


class OSWorldArtifactEvidenceError(RuntimeError):
    """表示 artifact spec、getter 或 metric 无法形成可信 observation。"""


class OSWorldArtifactEvidenceSource:
    """按版本化 spec 捕获单 VM artifact 的脱敏评价投影。

    当前 production 支持固定 15 项 artifact-state 任务；其中 10 项必须先由
    environment 的精确 runtime capability 完成版本化 finalize，13 项外部 gold
    任务还必须注入受控 resolver。任何未进入正式能力闭集的非 ``none``
    finalize 任务都会显式失败，不会用占位分数冒充已迁移。
    """

    def __init__(
        self,
        *,
        specs: Mapping[str, ArtifactEvidenceSpec] | None = None,
        gold_resolver: Any | None = None,
    ) -> None:
        """构造不访问 guest、文件或外部 gold 的证据源。

        输入参数：
            specs：可选的可信版本化 spec 映射；生产默认使用仓库只读
                catalog，测试可注入仍通过完整校验与摘要绑定的副本。
            gold_resolver：可选 evaluator-only 离线 gold resolver；只有外部
                gold 任务会使用，其 ``open_verified`` 不得暴露缓存路径。
        输出返回值：
            无；只复制映射并验证 task key、spec 身份和 canonical 摘要。
        异常：
            OSWorldArtifactEvidenceError：映射或任一 spec 无法可信绑定。
        """

        selected = OSWORLD_ARTIFACT_EVIDENCE_SPECS if specs is None else specs
        if not isinstance(selected, Mapping):
            raise OSWorldArtifactEvidenceError("artifact evidence spec catalog 无效")
        copied: dict[str, ArtifactEvidenceSpec] = {}
        for task_id, spec in selected.items():
            if (
                not isinstance(task_id, str)
                or not task_id
                or not isinstance(spec, ArtifactEvidenceSpec)
                or spec.task_id != task_id
            ):
                raise OSWorldArtifactEvidenceError(
                    "artifact evidence spec catalog 身份无效"
                )
            _verify_spec_digest(spec)
            copied[task_id] = spec
        self._specs: Mapping[str, ArtifactEvidenceSpec] = MappingProxyType(copied)
        self._gold_resolver = gold_resolver
        self._uses_production_catalog = specs is None
        self._component_gold_completed: set[str] = set()

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> ArtifactStateObservation:
        """捕获单个 canonical task 的单 VM artifact observation。

        输入参数：
            task_id：必须存在于构造时冻结的 spec catalog。
            controller：当前 VM 的窄 OSWorld controller；图片任务必须实现
                单次 nofollow ``collect_image_pixel_hashes`` getter。
            guest_shared_dir：environment 在 prepare 阶段冻结的 guest shared
                绝对路径；capture 不得再次读取 Desktop 或重新推导 home。
        输出返回值：
            只含 rule/spec 摘要、槽位状态与 metric 分数的不可变
            ``ArtifactStateObservation``；不携带路径、文件名、内容或 gold。
        异常：
            OSWorldArtifactEvidenceError：任务未注册、所需 runtime finalize/getter
                尚未迁移，或 spec 在捕获前发生身份漂移。
        """

        if not isinstance(task_id, str) or not task_id:
            raise OSWorldArtifactEvidenceError("artifact task_id 无效")
        spec = self._specs.get(task_id)
        if spec is None:
            raise OSWorldArtifactEvidenceError("artifact task spec 未注册")
        _verify_spec_digest(spec)
        if task_id not in _SUPPORTED_TASK_IDS:
            raise OSWorldArtifactEvidenceError("artifact task source 尚未迁移")
        if task_id in LEGACY_OSWORLD_ARTIFACT_TASK_IDS:
            return self._capture_artifact_family_task(
                spec=spec,
                task_id=task_id,
                controller=controller,
                guest_shared_dir=guest_shared_dir,
            )
        if (
            spec.finalize_action_id != "none"
            and task_id not in OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS
        ):
            raise OSWorldArtifactEvidenceError("artifact finalize 尚未迁移")
        guest_home = _resolve_guest_home_from_shared_binding(guest_shared_dir)
        if task_id == _BIBTEX_TASK_ID:
            return self._capture_bibtex_task(
                spec=spec,
                guest_home=guest_home,
                controller=controller,
            )
        if any(
            slot.getter_kind != _IMAGE_DIRECTORY_GETTER for slot in spec.artifact_slots
        ):
            raise OSWorldArtifactEvidenceError("artifact getter 尚未迁移")
        observations: list[ArtifactSlotObservation] = []
        for slot in spec.artifact_slots:
            observations.append(
                _capture_image_directory_slot(
                    spec=spec,
                    slot=slot,
                    guest_home=guest_home,
                    controller=controller,
                )
            )
        return ArtifactStateObservation(
            rule_id=spec.rule_id,
            source_contract_sha256=spec.source_contract_sha256,
            evidence_spec_sha256=spec.evidence_spec_sha256,
            artifact_slots=tuple(observations),
        )

    def _capture_artifact_family_task(
        self,
        *,
        spec: ArtifactEvidenceSpec,
        task_id: str,
        controller: Any,
        guest_shared_dir: str | None,
    ) -> ArtifactStateObservation:
        """聚合 legacy family capture、verified gold 投影与纯 metric。

        输入参数：
            spec/task_id：已完成 canonical 摘要校验的任务规格与身份。
            controller：当前 VM 的受控 getter 边界。
            guest_shared_dir：prepare 阶段冻结的 guest shared 绝对路径。
        输出返回值：
            只含槽位状态与 metric 分数的单 VM 脱敏 observation。
        异常：
            OSWorldArtifactEvidenceError：resolver 缺失、所需 runtime finalize
                尚未接入，或 raw family source 的槽位闭集无法绑定。
        """

        canonical_spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS.get(task_id)
        if canonical_spec is None or spec != canonical_spec:
            raise OSWorldArtifactEvidenceError("artifact family spec 身份不一致")
        resolver_open = getattr(self._gold_resolver, "open_verified", None)
        if not callable(resolver_open):
            raise OSWorldArtifactEvidenceError("artifact gold resolver 尚未装配")
        gold_by_slot, gold_error_status = _load_artifact_family_gold_values(
            spec=spec,
            resolver_open=resolver_open,
        )
        if gold_by_slot is None:
            return _artifact_state_with_uniform_slot_error(
                spec,
                gold_error_status,
            )
        if (
            spec.finalize_action_id != "none"
            and task_id not in OSWORLD_ARTIFACT_RUNTIME_FINALIZE_TASK_IDS
        ):
            raise OSWorldArtifactEvidenceError("artifact finalize 尚未迁移")
        try:
            captures = OSWorldArtifactFamilyEvidenceSource().capture(
                task_id,
                controller,
                guest_shared_dir=guest_shared_dir,
            )
        except OSWorldArtifactFamilyEvidenceError:
            raise OSWorldArtifactEvidenceError("artifact family capture 失败") from None
        expected_slot_ids = tuple(slot.slot_id for slot in spec.artifact_slots)
        if tuple(capture.slot_id for capture in captures) != expected_slot_ids:
            raise OSWorldArtifactEvidenceError("artifact family 槽位闭集无效")
        observations = tuple(
            _evaluate_artifact_family_capture(
                task_id=task_id,
                slot=slot,
                capture=capture,
                verified_gold_bytes=gold_by_slot[slot.slot_id],
            )
            for slot, capture in zip(spec.artifact_slots, captures, strict=True)
        )
        observation = ArtifactStateObservation(
            rule_id=spec.rule_id,
            source_contract_sha256=spec.source_contract_sha256,
            evidence_spec_sha256=spec.evidence_spec_sha256,
            artifact_slots=observations,
        )
        if _artifact_family_component_projection_is_complete(
            spec=spec,
            observation=observation,
        ):
            self._component_gold_completed.add(task_id)
        return observation

    def osworld_artifact_component_gold_proof(
        self,
        task_id: str,
        *,
        expected_manifest: GoldAssetManifest,
    ) -> OSWorldArtifactComponentGoldProof:
        """投影同次 capture 已完成真实 resolver/projection/metric 的事实。

        输入参数：task_id 必须属于正式 12-task 闭集；expected_manifest 为
            environment 从当前 canonical task 引用重新安全加载的 gold 清单。
        输出返回值：仅含任务身份和三项成功布尔值的脱敏 proof。
        异常：OSWorldArtifactEvidenceError：使用了可替换 spec catalog、非精确
            production resolver、manifest 漂移，或 capture 未完成所有槽位的
            actual/gold projection 与 metric 调用。
        """

        resolver = self._gold_resolver
        if (
            task_id not in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
            or not self._uses_production_catalog
            or type(resolver) is not GoldAssetResolver
            or not resolver.is_bound_to_manifest(expected_manifest)
            or task_id not in self._component_gold_completed
        ):
            raise OSWorldArtifactEvidenceError("artifact component gold 生命周期未闭合")
        return OSWorldArtifactComponentGoldProof(
            task_id=task_id,
            resolver_manifest_verified=True,
            metric_projection_completed=True,
            metric_evaluation_completed=True,
        )

    def _capture_bibtex_task(
        self,
        *,
        spec: ArtifactEvidenceSpec,
        guest_home: PurePosixPath,
        controller: Any,
    ) -> ArtifactStateObservation:
        """捕获 CombinationDocs-015 的 actual，并用离线 gold 评价。

        输入参数：
            spec：已通过 canonical digest 校验的任务取证规格。
            guest_home：由同一 Attempt 冻结 shared binding 还原的 guest home。
            controller：实现 ``collect_file_bytes`` 的窄 guest 边界。
        输出返回值：
            仅含槽位状态与 ``compare_text_file`` 分数的脱敏 observation。
        异常：
            OSWorldArtifactEvidenceError：production gold resolver 未装配，或
                spec getter family 尚未按该切片迁移；均发生在 guest I/O 前。
        """

        resolver_open = getattr(self._gold_resolver, "open_verified", None)
        if not callable(resolver_open):
            raise OSWorldArtifactEvidenceError("artifact gold resolver 尚未装配")
        if any(slot.getter_kind != _SINGLE_FILE_GETTER for slot in spec.artifact_slots):
            raise OSWorldArtifactEvidenceError("artifact getter 尚未迁移")

        gold_by_slot: list[dict[str, bytes]] = []
        for slot in spec.artifact_slots:
            gold_values, gold_error_status = _load_bibtex_gold_values(
                slot=slot,
                resolver_open=resolver_open,
                max_bytes=spec.limits.max_text_bytes,
            )
            if gold_values is None:
                return ArtifactStateObservation(
                    rule_id=spec.rule_id,
                    source_contract_sha256=spec.source_contract_sha256,
                    evidence_spec_sha256=spec.evidence_spec_sha256,
                    artifact_slots=tuple(
                        _slot_error(item.slot_id, gold_error_status)
                        for item in spec.artifact_slots
                    ),
                )
            gold_by_slot.append(gold_values)

        observations: list[ArtifactSlotObservation] = []
        for slot, gold_values in zip(
            spec.artifact_slots,
            gold_by_slot,
            strict=True,
        ):
            observations.append(
                _capture_bibtex_file_slot(
                    spec=spec,
                    slot=slot,
                    guest_home=guest_home,
                    controller=controller,
                    gold_values=gold_values,
                )
            )
        return ArtifactStateObservation(
            rule_id=spec.rule_id,
            source_contract_sha256=spec.source_contract_sha256,
            evidence_spec_sha256=spec.evidence_spec_sha256,
            artifact_slots=tuple(observations),
        )


def _load_artifact_family_gold_values(
    *,
    spec: ArtifactEvidenceSpec,
    resolver_open: Any,
) -> tuple[dict[str, dict[str, bytes]] | None, str]:
    """在 guest I/O 前读取 artifact family task 的全部 verified gold。

    输入参数：
        spec：已验证摘要的 13-task family evidence spec。
        resolver_open：可信 resolver 的 ``open_verified`` bound method。
    输出返回值：
        成功时返回逐槽位 gold bytes 与 ``available``；绑定损坏返回
        ``schema_error``，缓存、完整性或读取失败返回 ``read_error``。
    """

    if spec.task_id not in LEGACY_OSWORLD_ARTIFACT_TASK_IDS:
        raise OSWorldArtifactEvidenceError("artifact family gold 任务未注册")
    slot_bindings: dict[str, list[tuple[str, frozenset[str]]]] = {}
    unique_bindings: dict[str, frozenset[str]] = {}
    for slot in spec.artifact_slots:
        bindings: list[tuple[str, frozenset[str]]] = []
        for metric in slot.metrics:
            if metric.expected_kind == "inline-rule":
                if (
                    metric.expected_options_json is None
                    or metric.gold_keys
                    or metric.metric_input_projection_id
                    != "inline-rule.as-gold.no-options.v1"
                ):
                    return None, "schema_error"
                continue
            try:
                media_types = artifact_gold_media_types(metric.contract_id)
            except OSWorldArtifactGoldMediaContractError:
                return None, "schema_error"
            if (
                metric.expected_kind != "gold-assets"
                or metric.expected_options_json is not None
                or metric.metric_input_projection_id
                != "gold-assets.with-evaluator-options.v1"
                or len(media_types) != len(metric.gold_keys)
            ):
                return None, "schema_error"
            for logical_key, expected_media_types in zip(
                metric.gold_keys,
                media_types,
                strict=True,
            ):
                if not isinstance(logical_key, str) or not logical_key:
                    return None, "schema_error"
                prior = unique_bindings.get(logical_key)
                if prior is not None and prior != expected_media_types:
                    return None, "schema_error"
                unique_bindings[logical_key] = expected_media_types
                bindings.append((logical_key, expected_media_types))
        slot_bindings[slot.slot_id] = bindings

    loaded: dict[str, bytes] = {}
    total_bytes = 0
    for logical_key, expected_media_types in unique_bindings.items():
        remaining = spec.limits.max_total_bytes - total_bytes
        max_bytes = min(
            artifact_family_single_file_byte_limit(spec),
            remaining,
        )
        if max_bytes <= 0:
            return None, "read_error"
        try:
            with resolver_open(
                logical_key,
                max_bytes=max_bytes,
                expected_media_types=expected_media_types,
            ) as stream:
                read = getattr(stream, "read", None)
                if not callable(read):
                    return None, "read_error"
                content = read(max_bytes + 1)
                tail = read(1)
        except Exception:
            return None, "read_error"
        if (
            not isinstance(content, bytes)
            or not isinstance(tail, bytes)
            or len(content) > max_bytes
            or tail != b""
        ):
            return None, "read_error"
        loaded[logical_key] = content
        total_bytes += len(content)

    return (
        {
            slot_id: {
                logical_key: loaded[logical_key]
                for logical_key, _expected_media_types in bindings
            }
            for slot_id, bindings in slot_bindings.items()
        },
        "available",
    )


def _artifact_family_component_projection_is_complete(
    *,
    spec: ArtifactEvidenceSpec,
    observation: ArtifactStateObservation,
) -> bool:
    """检查一次 production capture 是否完整执行所有 gold metric。

    输入参数：spec 为当前冻结 evidence 规格；observation 为同次
        resolver、getter、projection 与 metric 调用生成的脱敏结果。
    输出返回值：每个槽位均 ``available``、身份与 metric 闭集精确一致，
        且所有分数有限并位于 ``[0, 1]`` 时返回 ``True``；missing/read/
        parse/schema 状态均返回 ``False``，不能据此伪造 gold 完成。
    """

    if (
        not isinstance(spec, ArtifactEvidenceSpec)
        or not isinstance(observation, ArtifactStateObservation)
        or len(observation.artifact_slots) != len(spec.artifact_slots)
    ):
        return False
    for slot_spec, slot_observation in zip(
        spec.artifact_slots,
        observation.artifact_slots,
        strict=True,
    ):
        if (
            slot_observation.slot_id != slot_spec.slot_id
            or slot_observation.status != "available"
            or tuple(metric.metric_id for metric in slot_observation.metric_scores)
            != tuple(metric.metric_id for metric in slot_spec.metrics)
        ):
            return False
        for metric in slot_observation.metric_scores:
            if (
                isinstance(metric.score, bool)
                or not isinstance(metric.score, (int, float))
                or not math.isfinite(float(metric.score))
                or not 0.0 <= float(metric.score) <= 1.0
            ):
                return False
    return True


def _evaluate_artifact_family_capture(
    *,
    task_id: str,
    slot: ArtifactSlotEvidenceSpec,
    capture: ArtifactFamilyCapture,
    verified_gold_bytes: Mapping[str, bytes],
) -> ArtifactSlotObservation:
    """把一个 raw family capture 投影、评分并立即丢弃私有值。

    输入参数：
        task_id/slot/capture：同一 canonical evidence spec 的任务、槽位与实际值。
        verified_gold_bytes：guest I/O 前完成身份校验的槽位 gold 闭集。
    输出返回值：
        只含状态和 metric 分数的脱敏槽位 observation。
    """

    if capture.status != "available":
        return _slot_error(slot.slot_id, capture.status)
    if slot.getter_kind == "directory-listing":
        try:
            return evaluate_inline_directory_membership(task_id, capture)
        except OSWorldArtifactFamilyEvidenceError:
            return _slot_error(slot.slot_id, "schema_error")
    try:
        projections = project_verified_artifact_metric_values(
            task_id,
            capture,
            verified_gold_bytes=verified_gold_bytes,
        )
        scores: list[ArtifactMetricObservation] = []
        for projection in projections:
            evaluation = evaluate_artifact_metric(
                projection.contract_id,
                actual=projection.actual_value(),
                gold=projection.gold_value(),
                options=projection.options(),
            )
            if (
                evaluation.metric_id != projection.metric_id
                or evaluation.contract_id != projection.contract_id
            ):
                return _slot_error(slot.slot_id, "schema_error")
            scores.append(
                ArtifactMetricObservation(
                    metric_id=evaluation.metric_id,
                    score=evaluation.score,
                )
            )
    except (
        ArtifactMetricEvaluationError,
        OSWorldArtifactMetricProjectionError,
        TypeError,
        ValueError,
    ):
        return _slot_error(slot.slot_id, "schema_error")
    if tuple(item.metric_id for item in scores) != tuple(
        metric.metric_id for metric in slot.metrics
    ):
        return _slot_error(slot.slot_id, "schema_error")
    return ArtifactSlotObservation(
        slot_id=slot.slot_id,
        status="available",
        metric_scores=tuple(scores),
    )


def _artifact_state_with_uniform_slot_error(
    spec: ArtifactEvidenceSpec,
    status: str,
) -> ArtifactStateObservation:
    """构造全部槽位同一失败状态的脱敏 observation。

    输入参数：
        spec：当前可信 evidence spec。
        status：固定 missing/read_error/parse_error/schema_error 状态。
    输出返回值：
        不含 raw、gold 或 locator 的任务 observation。
    """

    return ArtifactStateObservation(
        rule_id=spec.rule_id,
        source_contract_sha256=spec.source_contract_sha256,
        evidence_spec_sha256=spec.evidence_spec_sha256,
        artifact_slots=tuple(
            _slot_error(slot.slot_id, status) for slot in spec.artifact_slots
        ),
    )


def _verify_spec_digest(spec: ArtifactEvidenceSpec) -> None:
    """验证 spec schema 与 canonical SHA-256 自身份。

    输入参数：
        spec：待用于证据捕获的不可变版本化规格。
    输出返回值：
        无；schema 和摘要均精确匹配时正常返回。
    异常：
        OSWorldArtifactEvidenceError：规格无效或摘要不匹配；错误不回显
            task、locator、gold 或摘要值。
    """

    try:
        validate_artifact_evidence_spec(spec)
        canonical = canonical_artifact_evidence_spec_json(spec)
    except (ArtifactEvidenceSpecError, TypeError, ValueError):
        raise OSWorldArtifactEvidenceError("artifact evidence spec 无效") from None
    expected = spec.evidence_spec_sha256
    if (
        not isinstance(expected, str)
        or _SHA256_PATTERN.fullmatch(expected) is None
        or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != expected
    ):
        raise OSWorldArtifactEvidenceError("artifact evidence spec 摘要不匹配")


def _resolve_guest_home_from_shared_binding(
    guest_shared_dir: str | None,
) -> PurePosixPath:
    """验证 prepare 阶段冻结的 shared locator 并还原 guest home。

    输入参数：
        guest_shared_dir：由同一 environment 在资产上传前冻结的 POSIX
            shared 绝对路径；不接受 capture 阶段重新解析的路径。
    输出返回值：
        不为根目录、无 ``..`` 且末段严格为 ``shared`` 的 guest home。
    异常：
        OSWorldArtifactEvidenceError：绑定缺失、非规范或不安全；不回显路径。
    """

    if not isinstance(guest_shared_dir, str) or not guest_shared_dir:
        raise OSWorldArtifactEvidenceError("artifact guest 路径绑定缺失")
    shared = PurePosixPath(guest_shared_dir)
    guest_home = shared.parent
    if (
        not shared.is_absolute()
        or ".." in shared.parts
        or shared.name != "shared"
        or guest_home == PurePosixPath("/")
        or str(guest_home) in {"", "."}
        or str(shared) != guest_shared_dir
    ):
        raise OSWorldArtifactEvidenceError("artifact guest 路径绑定无效")
    return guest_home


def _capture_image_directory_slot(
    *,
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
) -> ArtifactSlotObservation:
    """通过单次 guest nofollow helper 捕获 Pillow 像素摘要映射。

    输入参数：
        spec/slot：已完成 canonical 摘要校验的任务与图片槽位规格。
        guest_home：从当前 VM 动态推导的 home。
        controller：实现单次有界 ``collect_image_pixel_hashes`` 的接口。
    输出返回值：
        ``available``、``missing``、``read_error`` 或 ``schema_error``
        槽位 observation；任何返回均不包含路径、成员名或摘要。
    """

    if len(slot.locator_relative_paths) != 1:
        return _slot_error(slot.slot_id, "schema_error")
    try:
        getter_options = _load_strict_json_object(slot.getter_options_json)
    except (TypeError, ValueError):
        return _slot_error(slot.slot_id, "schema_error")
    if getter_options != _EXPECTED_IMAGE_GETTER_OPTIONS:
        return _slot_error(slot.slot_id, "schema_error")

    getter = getattr(controller, "collect_image_pixel_hashes", None)
    if not callable(getter):
        return _slot_error(slot.slot_id, "read_error")
    relative = PurePosixPath(slot.locator_relative_paths[0])
    guest_directory = str(guest_home / relative)
    decoded_ceiling = spec.limits.max_container_expanded_bytes
    max_pixels = decoded_ceiling // _MAX_DECODED_BYTES_PER_PIXEL_BUDGET
    if max_pixels <= 0:
        return _slot_error(slot.slot_id, "schema_error")
    try:
        records = getter(
            guest_directory,
            max_entries=spec.limits.max_items,
            max_name_bytes=_MAX_POSIX_NAME_BYTES,
            max_compressed_item_bytes=spec.limits.max_single_item_bytes,
            max_total_compressed_bytes=spec.limits.max_total_bytes,
            max_pixels_per_image=max_pixels,
            max_decoded_item_bytes=decoded_ceiling,
            max_total_decoded_bytes=decoded_ceiling,
            max_response_bytes=spec.limits.max_text_bytes,
            timeout_seconds=spec.limits.getter_timeout_seconds,
        )
    except OSWorldGuestPathMissingError:
        return _slot_error(slot.slot_id, "missing")
    except Exception:
        return _slot_error(slot.slot_id, "read_error")
    if not isinstance(records, tuple):
        return _slot_error(slot.slot_id, "schema_error")
    if len(records) > spec.limits.max_items:
        return _slot_error(slot.slot_id, "schema_error")
    manifest: dict[str, str] = {}
    observed_names: set[str] = set()
    for record in records:
        if (
            not isinstance(record, tuple)
            or len(record) != 2
            or not isinstance(record[0], str)
            or _SHA256_PATTERN.fullmatch(record[0]) is None
            or not _is_safe_member_name(record[1])
            or record[1] in observed_names
        ):
            return _slot_error(slot.slot_id, "schema_error")
        digest, name = record
        observed_names.add(name)
        manifest[digest] = name

    metric_observations = _evaluate_inline_metrics(slot.metrics, manifest)
    if metric_observations is None:
        return _slot_error(slot.slot_id, "schema_error")
    return ArtifactSlotObservation(
        slot_id=slot.slot_id,
        status="available",
        metric_scores=metric_observations,
    )


def _load_bibtex_gold_values(
    *,
    slot: ArtifactSlotEvidenceSpec,
    resolver_open: Any,
    max_bytes: int,
) -> tuple[dict[str, bytes] | None, str]:
    """在任何 guest actual I/O 前解析当前槽位的全部 BibTeX gold。

    输入参数：
        slot：已完成 spec digest 校验的单文件槽位。
        resolver_open：trusted resolver 的 ``open_verified`` bound method。
        max_bytes：每个 gold 文本允许读取的字节上限。
    输出返回值：
        成功时返回 ``({logical_key: bytes}, "available")``；spec 投影损坏
        返回 ``(None, "schema_error")``，gold 未准备或读取异常返回
        ``(None, "read_error")``。任何分支都不包含 locator、摘要或正文。
    """

    logical_key_bindings: list[tuple[str, frozenset[str]]] = []
    for metric in slot.metrics:
        try:
            _load_strict_json_object(metric.options_json)
            media_types = artifact_gold_media_types(metric.contract_id)
        except (
            OSWorldArtifactGoldMediaContractError,
            TypeError,
            ValueError,
        ):
            return None, "schema_error"
        if (
            metric.expected_kind != "gold-assets"
            or metric.expected_options_json is not None
            or metric.metric_input_projection_id
            != "gold-assets.with-evaluator-options.v1"
            or not isinstance(metric.gold_keys, tuple)
            or len(metric.gold_keys) != 1
            or len(media_types) != 1
            or not isinstance(metric.gold_keys[0], str)
            or not metric.gold_keys[0]
        ):
            return None, "schema_error"
        logical_key_bindings.append((metric.gold_keys[0], media_types[0]))

    gold_values: dict[str, bytes] = {}
    unique_bindings = dict(logical_key_bindings)
    if len(unique_bindings) != len(logical_key_bindings):
        return None, "schema_error"
    for logical_key, expected_media_types in unique_bindings.items():
        try:
            with resolver_open(
                logical_key,
                max_bytes=max_bytes,
                expected_media_types=expected_media_types,
            ) as stream:
                read = getattr(stream, "read", None)
                if not callable(read):
                    return None, "read_error"
                content = read(max_bytes + 1)
                tail = read(1)
        except Exception:
            return None, "read_error"
        if (
            not isinstance(content, bytes)
            or not isinstance(tail, bytes)
            or len(content) > max_bytes
            or tail != b""
        ):
            return None, "read_error"
        gold_values[logical_key] = content
    return gold_values, "available"


def _capture_bibtex_file_slot(
    *,
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
    gold_values: Mapping[str, bytes],
) -> ArtifactSlotObservation:
    """有界读取 actual BibTeX 并执行冻结的外部 gold metric。

    输入参数：
        spec/slot：已完成 canonical digest 校验的任务与文件槽位规格。
        guest_home：由 prepare 阶段 frozen shared binding 推导的 guest home。
        controller：实现单次 nofollow ``collect_file_bytes`` 的窄接口。
        gold_values：已在 guest I/O 前完整验证的 logical-key→bytes 映射。
    输出返回值：
        ``available``、``missing``、``read_error`` 或 ``schema_error``
        槽位 observation；不携带 actual/gold 正文或路径。
    """

    if len(slot.locator_relative_paths) != 1:
        return _slot_error(slot.slot_id, "schema_error")
    try:
        getter_options = _load_strict_json_object(slot.getter_options_json)
    except (TypeError, ValueError):
        return _slot_error(slot.slot_id, "schema_error")
    if getter_options != {}:
        return _slot_error(slot.slot_id, "schema_error")
    getter = getattr(controller, "collect_file_bytes", None)
    if not callable(getter):
        return _slot_error(slot.slot_id, "read_error")

    guest_path = str(guest_home / PurePosixPath(slot.locator_relative_paths[0]))
    max_bytes = spec.limits.max_text_bytes
    max_response_bytes = _BASE64_HTTP_ENVELOPE_OVERHEAD_BYTES + 4 * (
        (max_bytes + 2) // 3
    )
    if max_response_bytes > _MAX_SINGLE_FILE_RESPONSE_BYTES:
        return _slot_error(slot.slot_id, "schema_error")
    try:
        actual = getter(
            guest_path,
            max_bytes=max_bytes,
            max_response_bytes=max_response_bytes,
            timeout_seconds=spec.limits.getter_timeout_seconds,
        )
    except OSWorldGuestPathMissingError:
        return _slot_error(slot.slot_id, "missing")
    except Exception:
        return _slot_error(slot.slot_id, "read_error")
    if not isinstance(actual, bytes) or len(actual) > max_bytes:
        return _slot_error(slot.slot_id, "schema_error")

    observations = _evaluate_external_gold_metrics(
        slot.metrics,
        actual=actual,
        gold_values=gold_values,
    )
    if observations is None:
        return _slot_error(slot.slot_id, "schema_error")
    return ArtifactSlotObservation(
        slot_id=slot.slot_id,
        status="available",
        metric_scores=observations,
    )


def _evaluate_external_gold_metrics(
    metrics: tuple[ArtifactMetricEvidenceSpec, ...],
    *,
    actual: bytes,
    gold_values: Mapping[str, bytes],
) -> tuple[ArtifactMetricObservation, ...] | None:
    """执行已固定 logical gold 的无 I/O artifact metric。

    输入参数：
        metrics：当前槽位的版本化外部 gold metric 闭集。
        actual：受限 guest getter 返回的原始字节。
        gold_values：resolver 已完成大小、摘要与媒体门禁的可信字节映射。
    输出返回值：
        全部 metric 成功执行时返回脱敏分数 tuple；spec、文本或 metric
        registry 不可解释时返回 ``None``。
    """

    observations: list[ArtifactMetricObservation] = []
    for metric in metrics:
        if len(metric.gold_keys) != 1:
            return None
        gold = gold_values.get(metric.gold_keys[0])
        if gold is None:
            return None
        try:
            options = _load_strict_json_object(metric.options_json)
            evaluation = evaluate_artifact_metric(
                metric.contract_id,
                actual=actual,
                gold=gold,
                options=options,
            )
        except (ArtifactMetricEvaluationError, TypeError, ValueError):
            return None
        if (
            evaluation.metric_id != metric.metric_id
            or evaluation.contract_id != metric.contract_id
        ):
            return None
        observations.append(
            ArtifactMetricObservation(
                metric_id=evaluation.metric_id,
                score=evaluation.score,
            )
        )
    return tuple(observations)


def _evaluate_inline_metrics(
    metrics: tuple[ArtifactMetricEvidenceSpec, ...],
    manifest: Mapping[str, str],
) -> tuple[ArtifactMetricObservation, ...] | None:
    """把固定内联 rules 投影并执行无 I/O metric registry。

    输入参数：
        metrics：当前槽位的版本化 metric 闭集。
        manifest：guest helper 返回顺序应用“后观测覆盖”后的内存映射。
    输出返回值：
        全部 metric 成功执行时返回脱敏分数 tuple；配置或 schema 无法
        可靠评价时返回 ``None``，由调用方标记 evaluator error。
    """

    observations: list[ArtifactMetricObservation] = []
    for metric in metrics:
        try:
            gold, options = project_inline_artifact_metric_inputs(metric)
            evaluation = evaluate_artifact_metric(
                metric.contract_id,
                actual=manifest,
                gold=gold,
                options=options,
            )
        except (ArtifactEvidenceSpecError, ArtifactMetricEvaluationError):
            return None
        if (
            evaluation.metric_id != metric.metric_id
            or evaluation.contract_id != metric.contract_id
        ):
            return None
        observations.append(
            ArtifactMetricObservation(
                metric_id=evaluation.metric_id,
                score=evaluation.score,
            )
        )
    return tuple(observations)


def _slot_error(slot_id: str, status: str) -> ArtifactSlotObservation:
    """构造不带 metric 原值的固定失败槽位。

    输入参数：
        slot_id：可信 spec 中的逻辑槽位身份。
        status：纯评价协议允许的 missing/read/schema 状态。
    输出返回值：
        metric 闭集为空的不可变槽位 observation。
    """

    return ArtifactSlotObservation(
        slot_id=slot_id,
        status=status,
        metric_scores=(),
    )


def _is_safe_member_name(value: object) -> bool:
    """独立复核 getter 返回值可作为单一 POSIX 路径分量。

    输入参数：
        value：未信任的 guest helper 成员名字段。
    输出返回值：
        非空、无分隔符/NUL/控制字符且不超过 255 UTF-8 bytes 时为真。
    """

    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\x00" in value
        or any(not character.isprintable() for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", "strict")) <= _MAX_POSIX_NAME_BYTES
    except UnicodeEncodeError:
        return False


def _load_strict_json_object(serialized: str) -> dict[str, object]:
    """解析 canonical options 并拒绝重复键和非标准常量。

    输入参数：
        serialized：spec 中不可变的 JSON 字符串。
    输出返回值：
        新建的顶层 dict。
    异常：
        TypeError/ValueError：文本、键唯一性或顶层类型无效。
    """

    if not isinstance(serialized, str):
        raise TypeError("artifact options 必须是 JSON 字符串")

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        """把 object pairs 转为 dict，并拒绝重复键。

        输入参数：
            pairs：JSON decoder 保留顺序的键值对。
        输出返回值：
            键唯一时的新字典。
        """

        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("artifact options 含重复键")
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        """拒绝 NaN/Infinity 等非标准 JSON 常量。

        输入参数：
            _value：decoder 识别出的常量文本；故意不回显。
        输出返回值：
            不返回；始终抛出 ``ValueError``。
        """

        raise ValueError("artifact options 含非标准常量")

    payload = json.loads(
        serialized,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("artifact options 顶层必须是对象")
    return payload


__all__ = [
    "OSWorldArtifactEvidenceError",
    "OSWorldArtifactEvidenceSource",
]
