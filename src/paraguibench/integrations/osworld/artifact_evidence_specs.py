"""15 个 OSWorld artifact-state 任务的版本化取证规格目录。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping


ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID = "paraguibench.osworld.artifact-evidence-spec.v1"
_ALLOWED_FINALIZE_ACTION_IDS = frozenset(
    {
        "none",
        "archive-pdf-directory",
        "save-active-libreoffice-document",
        "export-calc-first-sheet-csv",
    }
)
_ALLOWED_GETTER_KINDS = frozenset(
    {
        "directory-listing",
        "file",
        "file-bundle",
        "image-directory-hash-manifest",
        "pptx-slide-background-image",
    }
)
_ALLOWED_SOURCE_PATH_ADAPTATION_IDS = frozenset(
    {
        "paraguibench.osworld.source-home-to-shared.v1",
        "paraguibench.osworld.source-path-identity.v1",
    }
)
_ALLOWED_METRIC_INPUT_PROJECTION_IDS = frozenset(
    {
        "gold-assets.with-evaluator-options.v1",
        "inline-rule.as-gold.no-options.v1",
        "inline-rule.expected-as-gold.flags-as-options.v1",
    }
)
_OSWORLD_HOME_COLLECTIONS = frozenset(
    {"Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music"}
)
# controller 的完整 single-file HTTP envelope 上限为 16 MiB；预留 4 KiB
# JSON/envelope 开销后，base64 公式允许的最大原文为 12,579,840 bytes。
_MAX_BASE64_TEXT_BYTES = 12_579_840
_LIMIT_CEILINGS = {
    "max_items": 4_096,
    "max_single_item_bytes": 536_870_912,
    "max_total_bytes": 1_073_741_824,
    "max_text_bytes": _MAX_BASE64_TEXT_BYTES,
    "max_container_entries": 4_096,
    "max_container_expanded_bytes": 2_147_483_648,
    "getter_timeout_seconds": 300.0,
    "finalize_timeout_seconds": 300.0,
}
_MIN_EVIDENCE_TIMEOUT_SECONDS = 0.001


class ArtifactEvidenceSpecError(ValueError):
    """表示取证规格违反定位、资源或 allowlist 边界。"""


@dataclass(frozen=True, slots=True)
class ArtifactEvidenceLimits:
    """定义单个任务取证过程不可突破的资源上限。

    输入参数：
        max_items：最多允许收集的文件或目录项数。
        max_single_item_bytes：单项最大字节数。
        max_total_bytes：一次取证所有项的总字节上限。
        max_text_bytes：文本投影或目录列表的最大字节数。
        max_container_entries/max_container_expanded_bytes：容器文档或
            压缩包的成员数和解包后总字节上限。
        getter_timeout_seconds/finalize_timeout_seconds：取证和收尾
            动作的硬超时。
    输出返回值：
        不可变的资源上限集合。
    """

    max_items: int
    max_single_item_bytes: int
    max_total_bytes: int
    max_text_bytes: int
    max_container_entries: int
    max_container_expanded_bytes: int
    getter_timeout_seconds: float
    finalize_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ArtifactMetricEvidenceSpec:
    """固定一次 metric 调用的身份、参数与 gold 类型。

    输入参数：
        metric_id/contract_id：源 metric 名称与版本化语义身份。
        score_threshold：通过阈值。
        options_json：源 evaluator ``options`` 的 canonical JSON。
        expected_kind：``inline-rule`` 或 ``gold-assets``。
        expected_options_json：已确认内联规则的 canonical
            JSON；外部 gold 时为 ``null``。
        gold_keys：稳定逻辑 gold 身份；不包含 URL、文件
            大小或未确认摘要。
        metric_input_projection_id：将源 expected/options 投影为
            固定 metric 入参的版本化规则。
    输出返回值：
        不可变的 metric 取证契约。
    """

    metric_id: str
    contract_id: str
    score_threshold: float
    options_json: str
    expected_kind: str
    expected_options_json: str | None
    gold_keys: tuple[str, ...]
    metric_input_projection_id: str


@dataclass(frozen=True, slots=True)
class ArtifactSlotEvidenceSpec:
    """定义一个逻辑 artifact 槽位如何从客户机取证。

    输入参数：
        slot_id/artifact_kind：纯评价规则中的槽位和媒体家族。
        locator_root_id：固定根目录身份，当前仅允许
            ``guest-home``。
        source_locator_relative_paths：源 OSWorld evaluator 中相对
            ``/home/user`` 的路径闭集。
        source_path_adaptation_id：源路径到 canonical task 路径的
            固定版本化映射。
        locator_relative_paths：相对当前动态 guest home 的实际
            取证路径闭集。
        getter_kind/getter_options_json：受限 getter 身份和参数。
        metrics：该槽位必须完整执行的 metric 闭集。
    输出返回值：
        不可变的槽位取证规格。
    """

    slot_id: str
    artifact_kind: str
    locator_root_id: str
    source_locator_relative_paths: tuple[str, ...]
    source_path_adaptation_id: str
    locator_relative_paths: tuple[str, ...]
    getter_kind: str
    getter_options_json: str
    metrics: tuple[ArtifactMetricEvidenceSpec, ...]


@dataclass(frozen=True, slots=True)
class ArtifactEvidenceSpec:
    """保存一个 canonical task 的完整、可鉴别取证规格。

    输入参数：
        schema_id/task_id/rule_id：规格 schema、canonical 任务和
            纯评价规则身份。
        source_evaluator_id/source_task_id/source_contract_sha256：最终
            OSWorld 源 evaluator 的三重身份。
        finalize_action_id/finalize_options_json：固定 allowlist 中的
            收尾动作和参数。
        limits/artifact_slots：严格资源上限与槽位闭集。
        evidence_spec_sha256：不含摘要字段的 canonical JSON
            SHA-256。
    输出返回值：
        可供 collector 与纯评价层共同绑定的不可变规格。
    """

    schema_id: str
    task_id: str
    rule_id: str
    source_evaluator_id: str
    source_task_id: str
    source_contract_sha256: str
    finalize_action_id: str
    finalize_options_json: str
    limits: ArtifactEvidenceLimits
    artifact_slots: tuple[ArtifactSlotEvidenceSpec, ...]
    evidence_spec_sha256: str


def _canonical_json(value: Any) -> str:
    """将仅由 JSON 原语组成的值编码为唯一字节序列。

    输入参数：
        value：要规范化的 JSON 值。
    输出返回值：
        UTF-8 友好、键排序、无多余空白的 JSON 字符串。
    """

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _spec_payload(spec: ArtifactEvidenceSpec) -> dict[str, Any]:
    """构造不含自身摘要的 canonical spec JSON 对象。

    输入参数：
        spec：待投影的不可变取证规格。
    输出返回值：
        只含 JSON 原语的新字典，不含
        ``evidence_spec_sha256``。
    """

    return {
        "schema_id": spec.schema_id,
        "task_id": spec.task_id,
        "rule_id": spec.rule_id,
        "source_evaluator_id": spec.source_evaluator_id,
        "source_task_id": spec.source_task_id,
        "source_contract_sha256": spec.source_contract_sha256,
        "finalize_action_id": spec.finalize_action_id,
        "finalize_options": json.loads(spec.finalize_options_json),
        "limits": {
            "max_items": spec.limits.max_items,
            "max_single_item_bytes": spec.limits.max_single_item_bytes,
            "max_total_bytes": spec.limits.max_total_bytes,
            "max_text_bytes": spec.limits.max_text_bytes,
            "max_container_entries": spec.limits.max_container_entries,
            "max_container_expanded_bytes": (spec.limits.max_container_expanded_bytes),
            "getter_timeout_seconds": spec.limits.getter_timeout_seconds,
            "finalize_timeout_seconds": spec.limits.finalize_timeout_seconds,
        },
        "artifact_slots": [
            {
                "slot_id": slot.slot_id,
                "artifact_kind": slot.artifact_kind,
                "locator_root_id": slot.locator_root_id,
                "source_locator_relative_paths": list(
                    slot.source_locator_relative_paths
                ),
                "source_path_adaptation_id": slot.source_path_adaptation_id,
                "locator_relative_paths": list(slot.locator_relative_paths),
                "getter_kind": slot.getter_kind,
                "getter_options": json.loads(slot.getter_options_json),
                "metrics": [
                    {
                        "metric_id": metric.metric_id,
                        "contract_id": metric.contract_id,
                        "score_threshold": metric.score_threshold,
                        "options": json.loads(metric.options_json),
                        "expected_kind": metric.expected_kind,
                        "expected_options": (
                            None
                            if metric.expected_options_json is None
                            else json.loads(metric.expected_options_json)
                        ),
                        "gold_keys": list(metric.gold_keys),
                        "metric_input_projection_id": (
                            metric.metric_input_projection_id
                        ),
                    }
                    for metric in slot.metrics
                ],
            }
            for slot in spec.artifact_slots
        ],
    }


def canonical_artifact_evidence_spec_json(spec: ArtifactEvidenceSpec) -> str:
    """生成用于版本绑定的 canonical JSON。

    输入参数：
        spec：已构建的 artifact evidence spec。
    输出返回值：
        不包含自身摘要字段的 canonical JSON 字符串。
    """

    validate_artifact_evidence_spec(spec)
    return _canonical_json(_spec_payload(spec))


def project_inline_artifact_metric_inputs(
    metric: ArtifactMetricEvidenceSpec,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """将已固定的内联 source rules 投影为 metric ``gold/options``。

    输入参数：
        metric：``expected_kind=inline-rule`` 的 metric evidence spec。
    输出返回值：
        两个新建 JSON 对象：可信 gold 映射与 metric
        options；不共享 catalog 内部可变对象。
    异常：
        ArtifactEvidenceSpecError：metric 不是支持的内联投影，
        或 source rules schema 与固定投影不一致。
    """

    if not isinstance(metric, ArtifactMetricEvidenceSpec):
        raise ArtifactEvidenceSpecError("artifact metric spec 类型无效")
    if metric.expected_kind != "inline-rule" or metric.expected_options_json is None:
        raise ArtifactEvidenceSpecError("artifact metric 不是内联规则")
    try:
        payload = json.loads(metric.expected_options_json)
    except (TypeError, json.JSONDecodeError):
        raise ArtifactEvidenceSpecError("artifact inline rule JSON 无效") from None
    if not isinstance(payload, dict):
        raise ArtifactEvidenceSpecError("artifact inline rule schema 无效")

    projection_id = metric.metric_input_projection_id
    if projection_id == "inline-rule.expected-as-gold.flags-as-options.v1":
        if set(payload) != {
            "expected",
            "expect_in_result",
            "result_not_list",
        } or not isinstance(payload.get("expected"), dict):
            raise ArtifactEvidenceSpecError("artifact inline rule schema 无效")
        if (
            type(payload["expect_in_result"]) is not bool
            or type(payload["result_not_list"]) is not bool
        ):
            raise ArtifactEvidenceSpecError("artifact inline rule flags 无效")
        return dict(payload["expected"]), {
            "expect_in_result": payload["expect_in_result"],
            "result_not_list": payload["result_not_list"],
        }
    if projection_id == "inline-rule.as-gold.no-options.v1":
        return dict(payload), None
    raise ArtifactEvidenceSpecError("artifact inline rule projection 未注册")


def validate_artifact_evidence_spec(spec: ArtifactEvidenceSpec) -> None:
    """验证 artifact evidence spec 的安全边界。

    输入参数：
        spec：待验证的取证规格。
    输出返回值：
        无；验证通过时正常返回。
    异常：
        ArtifactEvidenceSpecError：类型或定位器不可信。
        异常文本仅使用固定原因，不回显任务路径。
    """

    if not isinstance(spec, ArtifactEvidenceSpec):
        raise ArtifactEvidenceSpecError("artifact evidence spec 类型无效")
    if spec.finalize_action_id not in _ALLOWED_FINALIZE_ACTION_IDS:
        raise ArtifactEvidenceSpecError("artifact finalize action 未在固定 allowlist")
    _validate_finalize_action_options(
        spec.finalize_action_id,
        spec.finalize_options_json,
    )
    if not isinstance(spec.limits, ArtifactEvidenceLimits):
        raise ArtifactEvidenceSpecError("artifact evidence limits 类型无效")
    timeout_fields = {"getter_timeout_seconds", "finalize_timeout_seconds"}
    for field_name, ceiling in _LIMIT_CEILINGS.items():
        value = getattr(spec.limits, field_name)
        if not _is_valid_evidence_limit(
            value,
            minimum=(
                _MIN_EVIDENCE_TIMEOUT_SECONDS if field_name in timeout_fields else 1
            ),
            ceiling=ceiling,
            integer_only=field_name not in timeout_fields,
        ):
            raise ArtifactEvidenceSpecError("artifact evidence limit 无效")
    if spec.limits.max_single_item_bytes > spec.limits.max_total_bytes:
        raise ArtifactEvidenceSpecError("artifact evidence byte limits 关系无效")
    if spec.limits.max_total_bytes > spec.limits.max_container_expanded_bytes:
        raise ArtifactEvidenceSpecError("artifact evidence container limits 关系无效")
    if not isinstance(spec.artifact_slots, tuple) or not spec.artifact_slots:
        raise ArtifactEvidenceSpecError("artifact 槽位闭集无效")
    for slot in spec.artifact_slots:
        if not isinstance(slot, ArtifactSlotEvidenceSpec):
            raise ArtifactEvidenceSpecError("artifact 槽位类型无效")
        if slot.getter_kind not in _ALLOWED_GETTER_KINDS:
            raise ArtifactEvidenceSpecError("artifact getter 未在固定 allowlist")
        if slot.source_path_adaptation_id not in _ALLOWED_SOURCE_PATH_ADAPTATION_IDS:
            raise ArtifactEvidenceSpecError("artifact source path adaptation 无效")
        if slot.locator_root_id != "guest-home":
            raise ArtifactEvidenceSpecError("artifact locator 根目录无效")
        _validate_relative_path_tuple(slot.source_locator_relative_paths)
        _validate_relative_path_tuple(slot.locator_relative_paths)
        if slot.source_path_adaptation_id.endswith("source-path-identity.v1"):
            expected_runtime_paths = slot.source_locator_relative_paths
        else:
            expected_runtime_paths = tuple(
                _adapt_source_home_path_to_shared(path)
                for path in slot.source_locator_relative_paths
            )
        if slot.locator_relative_paths != expected_runtime_paths:
            raise ArtifactEvidenceSpecError("artifact source path adaptation 结果无效")
        if not isinstance(slot.metrics, tuple):
            raise ArtifactEvidenceSpecError("artifact metric 闭集无效")
        for metric in slot.metrics:
            if not isinstance(metric, ArtifactMetricEvidenceSpec):
                raise ArtifactEvidenceSpecError("artifact metric spec 类型无效")
            if (
                metric.metric_input_projection_id
                not in _ALLOWED_METRIC_INPUT_PROJECTION_IDS
            ):
                raise ArtifactEvidenceSpecError(
                    "artifact metric projection 未在 allowlist"
                )
            if metric.expected_kind == "inline-rule":
                if metric.expected_options_json is None or metric.gold_keys:
                    raise ArtifactEvidenceSpecError(
                        "artifact inline metric expected 无效"
                    )
            elif metric.expected_kind == "gold-assets":
                if metric.expected_options_json is not None or not metric.gold_keys:
                    raise ArtifactEvidenceSpecError(
                        "artifact gold metric expected 无效"
                    )
            else:
                raise ArtifactEvidenceSpecError("artifact metric expected 类型无效")


def _is_valid_evidence_limit(
    value: object,
    *,
    minimum: int | float,
    ceiling: int | float,
    integer_only: bool,
) -> bool:
    """在不做有溢出风险的浮点转换时校验资源上限。

    输入参数：
        value：来自不可变 evidence spec 的候选数值。
        minimum：计时器跨平台安全下界或整数资源的最小值。
        ceiling：该字段在版本化协议中的固定最大值。
        integer_only：计数或字节字段为 ``True``；timeout 字段为
            ``False``，允许有限浮点数。
    输出返回值：
        类型正确、有限且处于闭区间 ``[minimum, ceiling]`` 时返回 ``True``；
        bool、分数型计数、NaN/Infinity 和任意超大整数均返回 ``False``。
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if integer_only and not isinstance(value, int):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return minimum <= value <= ceiling


def _validate_finalize_action_options(action_id: str, options_json: str) -> None:
    """验证 allowlist finalize action 的参数也属于窄契约。

    输入参数：
        action_id：已通过 allowlist 的收尾动作身份。
        options_json：该动作的 canonical JSON 参数。
    输出返回值：
        无；参数键集、类型、相对路径和固定值均可信时返回。
    异常：
        ArtifactEvidenceSpecError：参数可执行面超出窄契约；不回显值。
    """

    try:
        options = json.loads(options_json)
    except (TypeError, json.JSONDecodeError):
        raise ArtifactEvidenceSpecError("artifact finalize options JSON 无效") from None
    if not isinstance(options, dict) or _canonical_json(options) != options_json:
        raise ArtifactEvidenceSpecError(
            "artifact finalize options 必须使用 canonical JSON"
        )
    if action_id == "none":
        if options:
            raise ArtifactEvidenceSpecError("artifact none finalize options 无效")
        return
    if action_id == "archive-pdf-directory":
        if (
            set(options)
            != {
                "input_directory_relative_path",
                "member_suffix",
                "output_relative_path",
            }
            or options.get("member_suffix") != ".pdf"
        ):
            raise ArtifactEvidenceSpecError("artifact archive finalize options 无效")
        input_path = options.get("input_directory_relative_path")
        output_path = options.get("output_relative_path")
        _validate_relative_path_tuple((input_path, output_path))
        if not output_path.startswith(f"{input_path}/") or not output_path.endswith(
            ".zip"
        ):
            raise ArtifactEvidenceSpecError("artifact archive output 路径无效")
        return
    if action_id == "save-active-libreoffice-document":
        if set(options) != {
            "activation_settle_seconds",
            "application",
            "post_save_settle_seconds",
            "strict_window_title",
        } or options.get("application") not in {"calc", "impress", "writer"}:
            raise ArtifactEvidenceSpecError("artifact save finalize options 无效")
        activation_settle_seconds = options.get("activation_settle_seconds")
        if (
            isinstance(activation_settle_seconds, bool)
            or not isinstance(activation_settle_seconds, (int, float))
            or not math.isfinite(activation_settle_seconds)
            or float(activation_settle_seconds) not in {0.5, 5.0}
        ):
            raise ArtifactEvidenceSpecError("artifact save settle 无效")
        post_save_settle_seconds = options.get("post_save_settle_seconds")
        if (
            isinstance(post_save_settle_seconds, bool)
            or not isinstance(post_save_settle_seconds, (int, float))
            or not math.isfinite(post_save_settle_seconds)
            or float(post_save_settle_seconds) not in {0.5, 1.0}
        ):
            raise ArtifactEvidenceSpecError("artifact save post-settle 无效")
        window_title = options.get("strict_window_title")
        if (
            not isinstance(window_title, str)
            or not window_title
            or len(window_title) > 256
            or any(ord(character) < 32 for character in window_title)
        ):
            raise ArtifactEvidenceSpecError("artifact save window title 无效")
        return
    if action_id == "export-calc-first-sheet-csv":
        if set(options) != {
            "input_relative_path",
            "output_directory_relative_path",
        }:
            raise ArtifactEvidenceSpecError("artifact CSV finalize options 无效")
        input_path = options.get("input_relative_path")
        output_directory = options.get("output_directory_relative_path")
        _validate_relative_path_tuple((input_path, output_directory))
        input_parent = input_path.rpartition("/")[0]
        if not input_path.endswith(".xlsx") or input_parent != output_directory:
            raise ArtifactEvidenceSpecError("artifact CSV finalize path 关系无效")
        return
    raise ArtifactEvidenceSpecError("artifact finalize action 未接入参数验证")


def _validate_relative_path_tuple(relative_paths: tuple[str, ...]) -> None:
    """验证一组定位器是非空、规范化的 POSIX 相对路径。

    输入参数：
        relative_paths：待验证的路径 tuple。
    输出返回值：
        无；全部可信时正常返回。
    异常：
        ArtifactEvidenceSpecError：闭集或任一路径无效；不回显路径。
    """

    if not isinstance(relative_paths, tuple) or not relative_paths:
        raise ArtifactEvidenceSpecError("artifact locator 闭集无效")
    for relative_path in relative_paths:
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path.startswith("/")
            or "\\" in relative_path
            or "\x00" in relative_path
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
        ):
            raise ArtifactEvidenceSpecError("artifact locator 必须是可信相对路径")


def _adapt_source_home_path_to_shared(relative_path: str) -> str:
    """将已验证的 OSWorld home 相对路径映射到 canonical shared。

    输入参数：
        relative_path：相对 ``/home/user`` 的源路径。
    输出返回值：
        相对动态 guest home 的 ``shared`` 路径；Desktop、
        Documents 等标准 collection 只去掉首层，保留所有子目录。
    """

    parts = relative_path.split("/")
    tail = parts[1:] if parts[0] in _OSWORLD_HOME_COLLECTIONS else parts
    return "/".join(("shared", *tail)) if tail else "shared"


def _with_digest(spec: ArtifactEvidenceSpec) -> ArtifactEvidenceSpec:
    """计算 spec canonical JSON 摘要并返回新对象。

    输入参数：
        spec：``evidence_spec_sha256`` 暂为空的规格。
    输出返回值：
        携带 canonical SHA-256 的不可变副本。
    """

    digest = hashlib.sha256(
        canonical_artifact_evidence_spec_json(spec).encode("utf-8")
    ).hexdigest()
    return replace(spec, evidence_spec_sha256=digest)


_DEFAULT_LIMITS = ArtifactEvidenceLimits(
    max_items=64,
    max_single_item_bytes=134_217_728,
    max_total_bytes=268_435_456,
    max_text_bytes=1_048_576,
    max_container_entries=512,
    max_container_expanded_bytes=536_870_912,
    getter_timeout_seconds=30.0,
    finalize_timeout_seconds=30.0,
)


def _metric_spec(
    metric_id: str,
    contract_id: str,
    *,
    score_threshold: float = 1.0,
    options: Mapping[str, Any] | None = None,
    inline_expected_options: Mapping[str, Any] | None = None,
    gold_keys: tuple[str, ...] = (),
    metric_input_projection_id: str | None = None,
) -> ArtifactMetricEvidenceSpec:
    """构造一条不可变的 metric evidence contract。

    输入参数：
        metric_id/contract_id：源 metric 和版本化语义身份。
        score_threshold/options：通过阈值与源 evaluator options。
        inline_expected_options：可选、已确认的内联规则。
        gold_keys：可选外部 gold 逻辑身份闭集。
        metric_input_projection_id：可选显式投影身份；外部
            gold 默认使用 evaluator options 直接投影。
    输出返回值：
        JSON 字段已 canonicalize 的 metric spec。
    """

    if inline_expected_options is not None and gold_keys:
        raise ValueError("metric expected 类型冲突")
    projection_id = metric_input_projection_id
    if projection_id is None:
        projection_id = "gold-assets.with-evaluator-options.v1"
    return ArtifactMetricEvidenceSpec(
        metric_id=metric_id,
        contract_id=contract_id,
        score_threshold=score_threshold,
        options_json=_canonical_json(dict(options or {})),
        expected_kind=(
            "inline-rule" if inline_expected_options is not None else "gold-assets"
        ),
        expected_options_json=(
            None
            if inline_expected_options is None
            else _canonical_json(dict(inline_expected_options))
        ),
        gold_keys=tuple(gold_keys),
        metric_input_projection_id=projection_id,
    )


def _slot_without_metrics(
    slot_id: str,
    artifact_kind: str,
    getter_kind: str,
    *relative_paths: str,
    getter_options: Mapping[str, Any] | None = None,
    metrics: tuple[ArtifactMetricEvidenceSpec, ...] = (),
    runtime_relative_paths: tuple[str, ...] | None = None,
    source_path_adaptation_id: str = ("paraguibench.osworld.source-path-identity.v1"),
) -> ArtifactSlotEvidenceSpec:
    """构造已固定定位与 getter 的槽位。

    输入参数：
        slot_id/artifact_kind/getter_kind：槽位、媒体家族和
            受限 getter 身份。
        relative_paths：``guest-home`` 下的相对路径闭集。
        getter_options：可选的 JSON getter 参数。
        metrics：该槽位需要完整执行的 metric 闭集。
        runtime_relative_paths/source_path_adaptation_id：可选的 canonical
            runtime 路径与版本化源路径映射；默认为 identity。
    输出返回值：
        已规范化 getter options 的槽位规格。
    """

    return ArtifactSlotEvidenceSpec(
        slot_id=slot_id,
        artifact_kind=artifact_kind,
        locator_root_id="guest-home",
        source_locator_relative_paths=tuple(relative_paths),
        source_path_adaptation_id=source_path_adaptation_id,
        locator_relative_paths=(
            tuple(relative_paths)
            if runtime_relative_paths is None
            else tuple(runtime_relative_paths)
        ),
        getter_kind=getter_kind,
        getter_options_json=_canonical_json(dict(getter_options or {})),
        metrics=tuple(metrics),
    )


def _task_spec(
    task_id: str,
    source_evaluator_id: str,
    source_task_id: str,
    source_contract_sha256: str,
    *artifact_slots: ArtifactSlotEvidenceSpec,
    finalize_action_id: str = "none",
    finalize_options: Mapping[str, Any] | None = None,
) -> ArtifactEvidenceSpec:
    """构造并摘要化一条 canonical task 取证规格。

    输入参数：
        task_id：ParaGUIBench canonical task ID。
        source_evaluator_id/source_task_id/source_contract_sha256：最终
            源 evaluator 身份。
        artifact_slots：同一 VM 上必须完整取证的槽位。
        finalize_action_id/finalize_options：收尾动作身份与
            静态参数。
    输出返回值：
        已填充 ``evidence_spec_sha256`` 的不可变规格。
    """

    return _with_digest(
        ArtifactEvidenceSpec(
            schema_id=ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID,
            task_id=task_id,
            rule_id=f"paraguibench.osworld.artifact-rule.{task_id}.v1",
            source_evaluator_id=source_evaluator_id,
            source_task_id=source_task_id,
            source_contract_sha256=source_contract_sha256,
            finalize_action_id=finalize_action_id,
            finalize_options_json=_canonical_json(dict(finalize_options or {})),
            limits=_DEFAULT_LIMITS,
            artifact_slots=tuple(artifact_slots),
            evidence_spec_sha256="",
        )
    )


_TASK_SPECS = (
    _with_digest(
        ArtifactEvidenceSpec(
            schema_id=ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID,
            task_id="Operation-FileOperate-BatchOperation-001",
            rule_id=(
                "paraguibench.osworld.artifact-rule."
                "Operation-FileOperate-BatchOperation-001.v1"
            ),
            source_evaluator_id="ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
            source_task_id="ce2b64a2-ddc1-4f91-8c7d-a88be7121aac",
            source_contract_sha256=(
                "28fdb8cb9b84390cfd642e1670d15aa4a5179a6931fa8986495fdd8bece2501c"
            ),
            finalize_action_id="none",
            finalize_options_json="{}",
            limits=_DEFAULT_LIMITS,
            artifact_slots=(
                ArtifactSlotEvidenceSpec(
                    slot_id="renamed_picture_set",
                    artifact_kind="directory-json-state",
                    locator_root_id="guest-home",
                    source_locator_relative_paths=("Pictures",),
                    source_path_adaptation_id=(
                        "paraguibench.osworld.source-home-to-shared.v1"
                    ),
                    locator_relative_paths=("shared",),
                    getter_kind="image-directory-hash-manifest",
                    getter_options_json=_canonical_json(
                        {
                            "content_detection": ("pillow-open-no-suffix-filter"),
                            "digest_algorithm": "sha256",
                            "hash_projection": "pillow-image-tobytes",
                            "duplicate_digest_policy": ("last-observed-entry-wins"),
                            "member_selection": "all-direct-members",
                            "symlink_policy": "nofollow-fail-closed",
                        }
                    ),
                    metrics=(
                        _metric_spec(
                            "check_direct_json_object",
                            "mountain-file-hash-name-map.v1",
                            inline_expected_options={
                                "expected": {
                                    (
                                        "6ed4239ecc2be3ec15ad65a78c5c823b9"
                                        "004d640b8cc83a6a7af5930f354de91"
                                    ): [
                                        "Everest",
                                        "everest",
                                        "Everest.jpg",
                                        "everest.jpg",
                                        "Mount Everest",
                                        "mount everest",
                                        "Mount Everest.jpg",
                                        "mount everest.jpg",
                                        "Everest Mountain",
                                        "everest mountain",
                                        "Everest Mountain.jpg",
                                        "everest mountain.jpg",
                                        "Sagarmatha",
                                        "sagarmatha",
                                        "Sagarmatha.jpg",
                                        "sagarmatha.jpg",
                                        "Sagarmatha Mountain",
                                        "sagarmatha mountain",
                                        "Sagarmatha Mountain.jpg",
                                        "sagarmatha mountain.jpg",
                                        "Chomolungma",
                                        "chomolungma",
                                        "Chomolungma.jpg",
                                        "chomolungma.jpg",
                                        "Qomolangma",
                                        "qomolangma",
                                        "Qomolangma.jpg",
                                        "qomolangma.jpg",
                                        "Himalayas",
                                        "himalayas",
                                        "Himalayas.jpg",
                                        "himalayas.jpg",
                                        "Himalayas Mountain",
                                        "himalayas mountain",
                                        "Himalayas Mountain.jpg",
                                        "himalayas mountain.jpg",
                                        "Himalaya",
                                        "himalaya",
                                        "Himalaya.jpg",
                                        "himalaya.jpg",
                                        "Himalaya Mountain",
                                        "himalaya mountain",
                                        "Himalaya Mountain.jpg",
                                        "himalaya mountain.jpg",
                                        "Ama Dablam",
                                        "ama dablam",
                                        "Ama Dablam.jpg",
                                        "ama dablam.jpg",
                                        "Mount Ama Dablam",
                                        "mount ama dablam",
                                        "Mount Ama Dablam.jpg",
                                        "mount ama dablam.jpg",
                                        "Ama Dablam Mountain",
                                        "ama dablam mountain",
                                        "Ama Dablam Mountain.jpg",
                                        "ama dablam mountain.jpg",
                                    ],
                                    (
                                        "79f45d40d8413d4e81f1b9734ea39e58"
                                        "622cafd79e12bab32959643fc245147c"
                                    ): [
                                        "Hua",
                                        "hua",
                                        "Hua.jpg",
                                        "hua.jpg",
                                        "Mount Hua",
                                        "mount hua",
                                        "Mount Hua.jpg",
                                        "mount hua.jpg",
                                        "Hua Mountain",
                                        "hua mountain",
                                        "Hua Mountain.jpg",
                                        "hua mountain.jpg",
                                        "Huashan",
                                        "huashan",
                                        "Huashan.jpg",
                                        "huashan.jpg",
                                        "Hua Shan",
                                        "hua shan",
                                        "Hua Shan.jpg",
                                        "hua shan.jpg",
                                        "Huashan Mountain",
                                        "huashan mountain",
                                        "Huashan Mountain.jpg",
                                        "huashan mountain.jpg",
                                        "Hua Shan Mountain",
                                        "hua shan mountain",
                                        "Hua Shan Mountain.jpg",
                                        "hua shan mountain.jpg",
                                    ],
                                    (
                                        "ec076282f61ba74642e94b5a6a1250c69"
                                        "88204d59d9b02936606b6b8ef1e4433"
                                    ): [
                                        "Kili",
                                        "kili",
                                        "Kili.jpg",
                                        "kili.jpg",
                                        "Kilimanjaro",
                                        "kilimanjaro",
                                        "Kilimanjaro.jpg",
                                        "kilimanjaro.jpg",
                                        "Mount Kilimanjaro",
                                        "mount kilimanjaro",
                                        "Mount Kilimanjaro.jpg",
                                        "mount kilimanjaro.jpg",
                                        "Kilimanjaro Mountain",
                                        "kilimanjaro mountain",
                                        "Kilimanjaro Mountain.jpg",
                                        "kilimanjaro mountain.jpg",
                                    ],
                                },
                                "expect_in_result": True,
                                "result_not_list": True,
                            },
                            metric_input_projection_id=(
                                "inline-rule.expected-as-gold.flags-as-options.v1"
                            ),
                        ),
                    ),
                ),
            ),
            evidence_spec_sha256="",
        )
    ),
    _task_spec(
        "Operation-FileOperate-BatchOperation-003",
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "5df7b33a-9f77-4101-823e-02f863e1c1ae",
        "0456405408bdb3d305b10dac904cba7fbc556f041417bef5530387a736cfd517",
        _slot_without_metrics(
            "chapter_pdf_archive",
            "zip-pdf-bundle",
            "file",
            "Desktop/book/book.zip",
            metrics=(
                _metric_spec(
                    "compare_archive",
                    "pdf-chapter-archive.v1",
                    options={"file_type": "pdf"},
                    gold_keys=(
                        "osworld-gold:5df7b33a-9f77-4101-823e-02f863e1c1ae:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="archive-pdf-directory",
        finalize_options={
            "input_directory_relative_path": "Desktop/book",
            "member_suffix": ".pdf",
            "output_relative_path": "Desktop/book/book.zip",
        },
    ),
    _task_spec(
        "Operation-FileOperate-CombinationDocs-009",
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "eb303e01-261e-4972-8c07-c9b4e7a4922a",
        "bc73a485042a3878b972e5fa14b9841cef85cfc79ef6c42274c5b68aaef1670b",
        _slot_without_metrics(
            "presentation_with_notes",
            "pptx",
            "file",
            "Desktop/lecture1-2021-with-ink.pptx",
            metrics=(
                _metric_spec(
                    "compare_pptx_files",
                    "speaker-notes.no-shape-no-bullets.v1",
                    options={
                        "examine_shape": False,
                        "examine_bullets": False,
                    },
                    gold_keys=(
                        "osworld-gold:eb303e01-261e-4972-8c07-c9b4e7a4922a:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="save-active-libreoffice-document",
        finalize_options={
            "activation_settle_seconds": 5.0,
            "application": "impress",
            "post_save_settle_seconds": 1.0,
            "strict_window_title": (
                "lecture1-2021-with-ink.pptx - LibreOffice Impress"
            ),
        },
    ),
    _task_spec(
        "Operation-FileOperate-CombinationDocs-010",
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "aceb0368-56b8-4073-b70e-3dc9aee184e0",
        "1e04563701fde1335a57c6540c5e9919472fd36b1d2ad1c0d5ae75fb5a1b1387",
        _slot_without_metrics(
            "graded_workbook",
            "xlsx",
            "file",
            "exam/grades.xlsx",
            metrics=(
                _metric_spec(
                    "compare_table",
                    "sheet-data.first-sheet.v1",
                    options={
                        "rules": [
                            {
                                "type": "sheet_data",
                                "sheet_idx0": 0,
                                "sheet_idx1": "EI0",
                            }
                        ]
                    },
                    gold_keys=(
                        "osworld-gold:aceb0368-56b8-4073-b70e-3dc9aee184e0:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
    ),
    _task_spec(
        "Operation-FileOperate-CombinationDocs-011",
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "337d318b-aa07-4f4f-b763-89d9a2dd013f",
        "846c0629ec2dde2f18a34807e9c0b899260fff5de22fd0cd710d5df3170e94f7",
        _slot_without_metrics(
            "problem_invoice",
            "pdf",
            "file",
            "Desktop/problematic/Invoice # 243729.pdf",
            metrics=(
                _metric_spec(
                    "compare_pdfs",
                    "problem-invoice-content.v1",
                    gold_keys=(
                        "osworld-gold:337d318b-aa07-4f4f-b763-89d9a2dd013f:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        _slot_without_metrics(
            "problematic_directory_membership",
            "directory-listing",
            "directory-listing",
            "Desktop/problematic",
            metrics=(
                _metric_spec(
                    "check_include_exclude",
                    "problematic-invoice-membership.v1",
                    inline_expected_options={
                        "include": ["Invoice # 243729.pdf"],
                        "exclude": [
                            "invoice TII-20220301-90.pdf",
                            "Invoice # GES-20220215-82.pdf",
                        ],
                    },
                    metric_input_projection_id=("inline-rule.as-gold.no-options.v1"),
                ),
            ),
        ),
    ),
    _task_spec(
        "Operation-FileOperate-CombinationDocs-012",
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e",
        "4780cfb96a299a1e8b30ab369fe767150164ee6140dd747ca7e017ecbe8bc948",
        _slot_without_metrics(
            "apa_references_document",
            "docx",
            "file",
            "Desktop/students work/case study.docx",
            metrics=(
                _metric_spec(
                    "compare_references",
                    "apa7-references.content-only.base-0_6.v1",
                    options={
                        "content_only": True,
                        "reference_base_result": 0.6,
                    },
                    gold_keys=(
                        "osworld-gold:2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="save-active-libreoffice-document",
        finalize_options={
            "activation_settle_seconds": 0.5,
            "application": "writer",
            "post_save_settle_seconds": 0.5,
            "strict_window_title": "case study.docx - LibreOffice Writer",
        },
    ),
    _task_spec(
        "Operation-FileOperate-CombinationDocs-013",
        "3d514057-efd2-44b9-98dd-4b092ac2828a",
        "7e287123-70ca-47b9-8521-47db09b69b14",
        "99468cac2c1677f2ddda08f8289b97890f01cf39a24417668c494b77e52c4ed3",
        _slot_without_metrics(
            "grf_workbook_bundle",
            "xlsx-csv-bundle",
            "file-bundle",
            "Desktop/GRF-p5y.xlsx",
            "Desktop/GRF-p5y-Sheet1.csv",
            metrics=(
                _metric_spec(
                    "compare_table",
                    "grf-sheet-print.sheet1.v1",
                    options={
                        "rules": [
                            {
                                "type": "sheet_print",
                                "sheet_idx0": "RNSheet1",
                                "sheet_idx1": "ENSheet1",
                            }
                        ]
                    },
                    gold_keys=(
                        "osworld-gold:7e287123-70ca-47b9-8521-47db09b69b14:"
                        "expected:0:v1",
                        "osworld-gold:7e287123-70ca-47b9-8521-47db09b69b14:"
                        "expected:1:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="export-calc-first-sheet-csv",
        finalize_options={
            "input_relative_path": "Desktop/GRF-p5y.xlsx",
            "output_directory_relative_path": "Desktop",
        },
    ),
    _task_spec(
        "Operation-FileOperate-CombinationDocs-014",
        "881deb30-9549-4583-a841-8270c65f2a17",
        "881deb30-9549-4583-a841-8270c65f2a17",
        "f8bb09a70d6733f65bbe1e03e6d8c7a7366671cff70bf688450bba34fbcd809d",
        _slot_without_metrics(
            "ecs_workbook_bundle",
            "xlsx-csv-bundle",
            "file-bundle",
            "Documents/Fundings/supported_rate.xlsx",
            "Documents/Fundings/supported_rate-Sheet1.csv",
            metrics=(
                _metric_spec(
                    "compare_table",
                    "supported-rate-sheet-print.sheet1.v1",
                    options={
                        "rules": [
                            {
                                "type": "sheet_print",
                                "sheet_idx0": "RNSheet1",
                                "sheet_idx1": "ENSheet1",
                            }
                        ]
                    },
                    gold_keys=(
                        "osworld-gold:881deb30-9549-4583-a841-8270c65f2a17:"
                        "expected:0:v1",
                        "osworld-gold:881deb30-9549-4583-a841-8270c65f2a17:"
                        "expected:1:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="export-calc-first-sheet-csv",
        finalize_options={
            "input_relative_path": "Documents/Fundings/supported_rate.xlsx",
            "output_directory_relative_path": "Documents/Fundings",
        },
    ),
    _task_spec(
        "Operation-FileOperate-CombinationDocs-015",
        "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "df67aebb-fb3a-44fd-b75b-51b6012df509",
        "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c",
        _slot_without_metrics(
            "bibtex_output",
            "bibtex-text",
            "file",
            "Desktop/references.bib",
            metrics=(
                _metric_spec(
                    "compare_text_file",
                    "bibtex.ignore-blanks.v1",
                    options={"ignore_blanks": True},
                    gold_keys=(
                        "osworld-gold:df67aebb-fb3a-44fd-b75b-51b6012df509:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
    ),
    _task_spec(
        "Operation-FileOperate-SearchAndWrite-001",
        "e9e7bcf6-92da-4ff0-aaea-821099370093",
        "c7c1e4c3-9e92-4eba-a4b8-689953975ea4",
        "8ff91da03ef3013c0abe4bac318a6c9ddaa5a6271cf6ed4652ffe7f8b6f73539",
        _slot_without_metrics(
            "professor_contact_workbook",
            "xlsx",
            "file",
            "Desktop/Professor_Contact.xlsx",
            metrics=(
                _metric_spec(
                    "compare_table",
                    "sheet-data.first-sheet.v1",
                    options={
                        "rules": [
                            {
                                "type": "sheet_data",
                                "sheet_idx0": 0,
                                "sheet_idx1": "EI0",
                            }
                        ]
                    },
                    gold_keys=(
                        "osworld-gold:c7c1e4c3-9e92-4eba-a4b8-689953975ea4:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="save-active-libreoffice-document",
        finalize_options={
            "activation_settle_seconds": 0.5,
            "application": "calc",
            "post_save_settle_seconds": 0.5,
            "strict_window_title": ("Professor_Contact.xlsx - LibreOffice Calc"),
        },
    ),
    _task_spec(
        "Operation-FileOperate-SearchAndWrite-003",
        "51d7a7fe-e659-4de0-8345-c2c04da90373",
        "da52d699-e8d2-4dc5-9191-a2199e0b6a9b",
        "8485b90d63965980bae26b44093cafa2c4dbd4b1971354f9b9b14dd12b7ed6a1",
        _slot_without_metrics(
            "book_result_document",
            "docx",
            "file",
            "Desktop/book_list_result.docx",
            metrics=(
                _metric_spec(
                    "compare_docx_files",
                    "docx-content.v1",
                    gold_keys=(
                        "osworld-gold:da52d699-e8d2-4dc5-9191-a2199e0b6a9b:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="save-active-libreoffice-document",
        finalize_options={
            "activation_settle_seconds": 0.5,
            "application": "writer",
            "post_save_settle_seconds": 0.5,
            "strict_window_title": ("book_list_result.docx - LibreOffice Writer"),
        },
    ),
    _task_spec(
        "Operation-FileOperate-SearchAndWrite-005",
        "dce61462-cf48-42d9-9466-5a0171aa5d12",
        "67890eb6-6ce5-4c00-9e3d-fb4972699b06",
        "f031f50bac3ab93f3dd1894b9cea737a2246c798ba2daf6a2b777c0239365855",
        _slot_without_metrics(
            "acl_awards_workbook",
            "xlsx",
            "file",
            "Desktop/best_awards_acl.xlsx",
            metrics=(
                _metric_spec(
                    "compare_table",
                    "sheet-data.first-sheet.v1",
                    options={
                        "rules": [
                            {
                                "type": "sheet_data",
                                "sheet_idx0": 0,
                                "sheet_idx1": "EI0",
                            }
                        ]
                    },
                    gold_keys=(
                        "osworld-gold:67890eb6-6ce5-4c00-9e3d-fb4972699b06:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="save-active-libreoffice-document",
        finalize_options={
            "activation_settle_seconds": 0.5,
            "application": "calc",
            "post_save_settle_seconds": 1.0,
            "strict_window_title": "best_awards_acl.xlsx - LibreOffice Calc",
        },
    ),
    _task_spec(
        "Operation-FileOperate-SearchAndWrite-009",
        "14b28a49-e101-4458-835e-2067823ddefb",
        "3e3fc409-bff3-4905-bf16-c968eee3f807",
        "8a440569b160bd2b7295ec4b006a83e002e2b578559c06a4f96d8265902189bf",
        _slot_without_metrics(
            "movies_workbook",
            "xlsx",
            "file",
            "Desktop/movies.xlsx",
            metrics=(
                _metric_spec(
                    "compare_table",
                    "sheet-data.named-unseen-movies.v1",
                    options={
                        "rules": [
                            {
                                "type": "sheet_data",
                                "sheet_idx0": "RNunseen_movies",
                                "sheet_idx1": "ENunseen_movies",
                            }
                        ]
                    },
                    gold_keys=(
                        "osworld-gold:3e3fc409-bff3-4905-bf16-c968eee3f807:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
    ),
    _task_spec(
        "Operation-FileOperate-Settings-001",
        "9b5220d5-f1f0-4db9-902d-ad41aae4d775",
        "47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5",
        "5f3ebcf626c74ac25b31c54c186166064c8a62edec23a87efbf1655a854ff66d",
        _slot_without_metrics(
            "slide_background_image",
            "pptx-background-image",
            "pptx-slide-background-image",
            "Desktop/Robotic_Workshop_Infographics.pptx",
            getter_options={"slide_index": 1},
            metrics=(
                _metric_spec(
                    "compare_images",
                    "slide-index-1.frame-00-08.v1",
                    score_threshold=0.90,
                    options={"score_threshold": 0.90},
                    gold_keys=(
                        "osworld-gold:47f7c0ce-a5fb-4100-a5e6-65cd0e7429e5:"
                        "expected:0:v2",
                    ),
                ),
            ),
        ),
        finalize_action_id="save-active-libreoffice-document",
        finalize_options={
            "activation_settle_seconds": 0.5,
            "application": "impress",
            "post_save_settle_seconds": 1.0,
            "strict_window_title": (
                "Robotic_Workshop_Infographics.pptx - LibreOffice Impress"
            ),
        },
    ),
    _task_spec(
        "Operation-WebOperate-SearchAndWrite-001",
        "d017201e-a098-46ab-86be-6c99d263ecff",
        "d1acdb87-bb67-4f30-84aa-990e56a09c92",
        "2262ca74a553975a89efff303a8731a9cafee598f9a0e2174562fa2f034e35c4",
        _slot_without_metrics(
            "restaurant_contact_workbook",
            "xlsx",
            "file",
            "Desktop/MUST_VISIT.xlsx",
            metrics=(
                _metric_spec(
                    "compare_table",
                    "sheet-fuzzy.restaurant-contacts.v1",
                    options={
                        "rules": [
                            {
                                "type": "sheet_fuzzy",
                                "sheet_idx0": "RNSheet1",
                                "sheet_idx1": "ENSheet1",
                                "rules": [
                                    {
                                        "range": ["A1:A6", "D1:D6"],
                                        "type": "exact_match",
                                    },
                                    {
                                        "range": ["B1:B6"],
                                        "type": "fuzzy_match",
                                        "threshold": 85,
                                        "normalization": [
                                            ["Rd", "Road"],
                                            ["St", "Street"],
                                        ],
                                        "ignore_case": True,
                                    },
                                    {
                                        "range": ["C1:C6"],
                                        "type": "includes",
                                        "trim_leadings": "+ ",
                                        "ignore_chars": " ()-",
                                    },
                                ],
                            }
                        ]
                    },
                    gold_keys=(
                        "osworld-gold:d1acdb87-bb67-4f30-84aa-990e56a09c92:"
                        "expected:0:v1",
                    ),
                ),
            ),
        ),
        finalize_action_id="save-active-libreoffice-document",
        finalize_options={
            "activation_settle_seconds": 0.5,
            "application": "calc",
            "post_save_settle_seconds": 1.0,
            "strict_window_title": "MUST_VISIT.xlsx - LibreOffice Calc",
        },
    ),
)

OSWORLD_ARTIFACT_EVIDENCE_SPECS: Mapping[str, ArtifactEvidenceSpec] = MappingProxyType(
    {spec.task_id: spec for spec in _TASK_SPECS}
)


__all__ = [
    "ARTIFACT_EVIDENCE_SPEC_SCHEMA_ID",
    "OSWORLD_ARTIFACT_EVIDENCE_SPECS",
    "ArtifactEvidenceLimits",
    "ArtifactEvidenceSpec",
    "ArtifactEvidenceSpecError",
    "ArtifactMetricEvidenceSpec",
    "ArtifactSlotEvidenceSpec",
    "canonical_artifact_evidence_spec_json",
    "project_inline_artifact_metric_inputs",
    "validate_artifact_evidence_spec",
]
