"""13 个 legacy OSWorld artifact family 的有界实际值取证入口。

本模块只负责 finalize 之后的 getter 与少量可本地闭环的解析/评分，不负责
启动应用、下载输入资产或解析外部 gold。原始文件字节和目录成员仅驻留在
evaluator 可信内存，并从 ``repr`` 中隐藏；共享 runtime 接线应只持久化最终
``ArtifactSlotObservation``，不得持久化 ``ArtifactFamilyCapture``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import posixpath
import re
import stat
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

from paraguibench.evaluation.osworld.artifact_metrics import (
    ArtifactMetricEvaluationError,
    evaluate_artifact_metric,
)
from paraguibench.integrations.osworld.artifact_contracts import (
    ArtifactMetricObservation,
    ArtifactSlotObservation,
)
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
    ArtifactEvidenceSpec,
    ArtifactEvidenceSpecError,
    ArtifactSlotEvidenceSpec,
    canonical_artifact_evidence_spec_json,
    project_inline_artifact_metric_inputs,
    validate_artifact_evidence_spec,
)
from paraguibench.integrations.osworld.controller import (
    OSWorldGuestPathMissingError,
)


LEGACY_OSWORLD_ARTIFACT_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperation-003",
        "Operation-FileOperate-CombinationDocs-009",
        "Operation-FileOperate-CombinationDocs-010",
        "Operation-FileOperate-CombinationDocs-011",
        "Operation-FileOperate-CombinationDocs-012",
        "Operation-FileOperate-CombinationDocs-013",
        "Operation-FileOperate-CombinationDocs-014",
        "Operation-FileOperate-SearchAndWrite-001",
        "Operation-FileOperate-SearchAndWrite-003",
        "Operation-FileOperate-SearchAndWrite-005",
        "Operation-FileOperate-SearchAndWrite-009",
        "Operation-FileOperate-Settings-001",
        "Operation-WebOperate-SearchAndWrite-001",
    }
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_POSIX_NAME_BYTES = 255
_BASE64_HTTP_ENVELOPE_OVERHEAD_BYTES = 4_096
_MAX_SINGLE_FILE_RESPONSE_BYTES = 16_777_216
ARTIFACT_FAMILY_SINGLE_FILE_MAX_BYTES = 12_579_840
_ALLOWED_CAPTURE_STATUSES = frozenset(
    {"available", "missing", "read_error", "parse_error", "schema_error"}
)


class OSWorldArtifactFamilyEvidenceError(RuntimeError):
    """表示 task/spec 身份或 getter 输入无法可靠绑定。"""


def artifact_family_single_file_byte_limit(
    spec: ArtifactEvidenceSpec,
) -> int:
    """返回 family actual 与 verified gold 共用的单文件字节上限。

    输入参数：
        spec：已通过 canonical 身份与 schema 校验的 artifact 取证规格。
    输出返回值：
        spec 单项上限、总量上限与受控 HTTP 原始载荷上限的最小值。
    异常：
        OSWorldArtifactFamilyEvidenceError：spec 或资源上限无效。
    """

    if not isinstance(spec, ArtifactEvidenceSpec):
        raise OSWorldArtifactFamilyEvidenceError("artifact family byte-limit spec 无效")
    candidates = (
        spec.limits.max_single_item_bytes,
        spec.limits.max_total_bytes,
        ARTIFACT_FAMILY_SINGLE_FILE_MAX_BYTES,
    )
    if any(type(value) is not int or value <= 0 for value in candidates):
        raise OSWorldArtifactFamilyEvidenceError("artifact family byte-limit 无效")
    return min(candidates)


class _PPTXBackgroundMissing(Exception):
    """表示有效 PPTX 的目标页没有可评价的直接内嵌背景图。"""


@dataclass(frozen=True, slots=True, repr=False)
class ArtifactFamilyCapture:
    """保存一个 artifact 槽位的 evaluator-only 临时实际值。

    输入参数：
        slot_id：冻结 spec 中不含路径的逻辑槽位身份。
        status：available/missing/read_error/parse_error/schema_error。
        payload_kind：file、file-bundle、directory-listing 或 image。
        _file_items：仅 evaluator 可信内存可读取的原始字节 tuple。
        _directory_members：仅 evaluator 可信内存可读取的成员名 tuple。
        _member_names：archive family 中与文件字节同序的私有成员名。
    输出返回值：
        不可变临时对象；自定义 ``repr`` 不含文件、目录或内容原值。
    """

    slot_id: str
    status: str
    payload_kind: str
    _file_items: tuple[bytes, ...] = field(default=(), repr=False)
    _directory_members: tuple[str, ...] = field(default=(), repr=False)
    _member_names: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        """验证临时 capture 的公开状态与私有载荷闭包。

        输入参数：
            无；读取当前 dataclass 字段。
        输出返回值：
            无；状态、类型和 available/empty 关系有效时正常返回。
        异常：
            ValueError：调用方构造了可能被错误评分的矛盾对象。
        """

        if (
            not isinstance(self.slot_id, str)
            or not self.slot_id
            or self.status not in _ALLOWED_CAPTURE_STATUSES
            or self.payload_kind
            not in {"file", "file-bundle", "directory-listing", "image"}
            or not isinstance(self._file_items, tuple)
            or not all(isinstance(item, bytes) for item in self._file_items)
            or not isinstance(self._directory_members, tuple)
            or not all(isinstance(member, str) for member in self._directory_members)
            or not isinstance(self._member_names, tuple)
            or not all(_is_safe_member_name(member) for member in self._member_names)
        ):
            raise ValueError("artifact family capture schema 无效")
        if self.status != "available" and (
            self._file_items or self._directory_members or self._member_names
        ):
            raise ValueError("失败 capture 不得保留 artifact 原值")
        if self.payload_kind == "directory-listing" and self._file_items:
            raise ValueError("目录 capture 不得携带文件字节")
        if self.payload_kind != "directory-listing" and self._directory_members:
            raise ValueError("文件 capture 不得携带目录成员")
        if self._member_names and (
            self.payload_kind != "file-bundle"
            or len(self._member_names) != len(self._file_items)
        ):
            raise ValueError("archive 成员名与文件字节不一致")

    def __repr__(self) -> str:
        """生成不含 artifact 原值、路径或大小的安全调试文本。

        输入参数：
            无；读取逻辑槽位、状态和 family 身份。
        输出返回值：
            仅包含安全 metadata 的字符串。
        """

        return (
            "ArtifactFamilyCapture("
            f"slot_id={self.slot_id!r}, status={self.status!r}, "
            f"payload_kind={self.payload_kind!r}, item_count={self.item_count})"
        )

    @property
    def item_count(self) -> int:
        """返回不暴露内容的临时实际值项数。

        输入参数：
            无。
        输出返回值：
            文件项数或目录成员数；缺失/错误 capture 为零。
        """

        if self.payload_kind == "directory-listing":
            return len(self._directory_members)
        return len(self._file_items)

    def file_items(self) -> tuple[bytes, ...]:
        """向 evaluator-only consumer 交付不可变文件字节 tuple。

        输入参数：
            无。
        输出返回值：
            available 文件 family 的原始字节；其他状态或 family 返回空。

        注意：
            返回值不得写入 RunStore、日志、异常或 Agent 可见上下文。
        """

        if self.status != "available" or self.payload_kind == "directory-listing":
            return ()
        return tuple(self._file_items)

    def directory_members(self) -> tuple[str, ...]:
        """向 evaluator-only consumer 交付目录成员闭集。

        输入参数：
            无。
        输出返回值：
            available directory-listing 的成员 tuple；其他情况返回空。

        注意：
            返回值不得写入 RunStore、日志、异常或 Agent 可见上下文。
        """

        if self.status != "available" or self.payload_kind != "directory-listing":
            return ()
        return tuple(self._directory_members)

    def member_names(self) -> tuple[str, ...]:
        """向 evaluator-only consumer 交付 archive 成员名闭集。

        输入参数：
            无。
        输出返回值：
            available archive family 中与 ``file_items`` 同序的名称；普通
            file-bundle、失败状态和非 bundle family 返回空。

        注意：
            返回值不得写入 RunStore、日志、异常或 Agent 可见上下文。
        """

        if self.status != "available" or self.payload_kind != "file-bundle":
            return ()
        return tuple(self._member_names)


class OSWorldArtifactFamilyEvidenceSource:
    """按冻结 artifact spec 捕获 13 个 legacy task 的临时实际值。"""

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> tuple[ArtifactFamilyCapture, ...]:
        """在 finalize 之后捕获一个 task 的全部逻辑槽位。

        输入参数：
            task_id：13 项 allowlist 中的 canonical task ID。
            controller：当前单 VM 的受控 getter 边界。
            guest_shared_dir：prepare 阶段冻结的 ``.../shared`` 绝对路径。
        输出返回值：
            按 spec 槽位顺序返回临时 capture tuple；原值不进入 repr。
        异常：
            OSWorldArtifactFamilyEvidenceError：task/spec/guest 绑定无效。
        """

        if task_id not in LEGACY_OSWORLD_ARTIFACT_TASK_IDS:
            raise OSWorldArtifactFamilyEvidenceError(
                "artifact family task 不在固定 allowlist"
            )
        spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS.get(task_id)
        if spec is None:
            raise OSWorldArtifactFamilyEvidenceError("artifact family spec 未注册")
        _verify_spec_digest(spec)
        guest_home = _resolve_guest_home_from_shared_binding(guest_shared_dir)
        return tuple(
            _capture_slot(spec, slot, guest_home, controller)
            for slot in spec.artifact_slots
        )


def evaluate_inline_directory_membership(
    task_id: str,
    capture: ArtifactFamilyCapture,
) -> ArtifactSlotObservation:
    """评价已冻结内联规则的单层目录成员切片。

    输入参数：
        task_id：必须是拥有目标槽位的 canonical task。
        capture：同一 spec 产生的 directory-listing 临时实际值。
    输出返回值：
        不含成员名的安全 ``ArtifactSlotObservation``。
    异常：
        OSWorldArtifactFamilyEvidenceError：task、slot 或 metric 绑定漂移。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS.get(task_id)
    if task_id not in LEGACY_OSWORLD_ARTIFACT_TASK_IDS or spec is None:
        raise OSWorldArtifactFamilyEvidenceError("artifact directory task 未注册")
    _verify_spec_digest(spec)
    slot = next(
        (
            candidate
            for candidate in spec.artifact_slots
            if candidate.slot_id == capture.slot_id
        ),
        None,
    )
    if (
        slot is None
        or slot.getter_kind != "directory-listing"
        or capture.payload_kind != "directory-listing"
        or len(slot.metrics) != 1
    ):
        raise OSWorldArtifactFamilyEvidenceError("artifact directory slot 绑定无效")
    if capture.status != "available":
        return ArtifactSlotObservation(
            slot_id=slot.slot_id,
            status=capture.status,
            metric_scores=(),
        )
    metric = slot.metrics[0]
    try:
        gold, options = project_inline_artifact_metric_inputs(metric)
        evaluation = evaluate_artifact_metric(
            metric.contract_id,
            actual=capture.directory_members(),
            gold=gold,
            options=options,
        )
    except (ArtifactEvidenceSpecError, ArtifactMetricEvaluationError):
        return ArtifactSlotObservation(
            slot_id=slot.slot_id,
            status="schema_error",
            metric_scores=(),
        )
    if (
        evaluation.metric_id != metric.metric_id
        or evaluation.contract_id != metric.contract_id
    ):
        return ArtifactSlotObservation(
            slot_id=slot.slot_id,
            status="schema_error",
            metric_scores=(),
        )
    return ArtifactSlotObservation(
        slot_id=slot.slot_id,
        status="available",
        metric_scores=(
            ArtifactMetricObservation(
                metric_id=evaluation.metric_id,
                score=evaluation.score,
            ),
        ),
    )


def _capture_slot(
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
) -> ArtifactFamilyCapture:
    """按单个 getter kind 捕获槽位，未知 family 失败关闭。

    输入参数：
        spec/slot：已完成摘要验证的任务与槽位规格。
        guest_home：由同一 Attempt 冻结 shared binding 还原的 home。
        controller：受控 guest getter 边界。
    输出返回值：
        当前 family 的临时 capture；尚未接入的 family 为 read_error。
    """

    if slot.getter_kind == "directory-listing":
        return _capture_directory_listing(spec, slot, guest_home, controller)
    if slot.getter_kind == "file" and slot.artifact_kind == "zip-pdf-bundle":
        return _capture_pdf_archive(spec, slot, guest_home, controller)
    if slot.getter_kind == "file":
        return _capture_single_file(spec, slot, guest_home, controller)
    if slot.getter_kind == "file-bundle":
        return _capture_file_bundle(spec, slot, guest_home, controller)
    if slot.getter_kind == "pptx-slide-background-image":
        return _capture_pptx_background(spec, slot, guest_home, controller)
    return ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="read_error",
        payload_kind=_payload_kind_for_slot(slot),
    )


def _capture_directory_listing(
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
) -> ArtifactFamilyCapture:
    """通过 controller 有界枚举一层目录并复核返回 schema。

    输入参数：
        spec/slot：已验证的目录 evidence spec。
        guest_home：冻结 guest home。
        controller：实现 ``list_directory`` 的边界。
    输出返回值：
        available/missing/read_error/schema_error capture；成员仅存私有字段。
    """

    if len(slot.locator_relative_paths) != 1:
        return _capture_error(slot, "schema_error")
    try:
        getter_options = _load_strict_json_object(slot.getter_options_json)
    except (TypeError, ValueError):
        return _capture_error(slot, "schema_error")
    if getter_options:
        return _capture_error(slot, "schema_error")
    getter = getattr(controller, "list_directory", None)
    if not callable(getter):
        return _capture_error(slot, "read_error")
    guest_path = str(guest_home / PurePosixPath(slot.locator_relative_paths[0]))
    try:
        entries = getter(
            guest_path,
            max_entries=spec.limits.max_items,
            max_name_bytes=_MAX_POSIX_NAME_BYTES,
            max_response_bytes=spec.limits.max_text_bytes,
        )
    except OSWorldGuestPathMissingError:
        return _capture_error(slot, "missing")
    except Exception:
        return _capture_error(slot, "read_error")
    if not isinstance(entries, tuple) or len(entries) > spec.limits.max_items:
        return _capture_error(slot, "schema_error")
    if not all(_is_safe_member_name(entry) for entry in entries):
        return _capture_error(slot, "schema_error")
    if tuple(
        sorted(entries, key=lambda value: value.encode("utf-8"))
    ) != entries or len(set(entries)) != len(entries):
        return _capture_error(slot, "schema_error")
    return ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="directory-listing",
        _directory_members=entries,
    )


def _capture_single_file(
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
) -> ArtifactFamilyCapture:
    """通过 controller 的 nofollow helper 有界读取一个普通文件。

    输入参数：
        spec/slot：已验证的单文件 evidence spec。
        guest_home：冻结 guest home。
        controller：实现 ``collect_file_bytes`` 的边界。
    输出返回值：
        available/missing/read_error/schema_error capture；字节仅存私有字段。
    """

    if len(slot.locator_relative_paths) != 1:
        return _capture_error(slot, "schema_error")
    try:
        getter_options = _load_strict_json_object(slot.getter_options_json)
    except (TypeError, ValueError):
        return _capture_error(slot, "schema_error")
    if getter_options:
        return _capture_error(slot, "schema_error")
    getter = getattr(controller, "collect_file_bytes", None)
    if not callable(getter):
        return _capture_error(slot, "read_error")
    max_bytes = artifact_family_single_file_byte_limit(spec)
    max_response_bytes = _BASE64_HTTP_ENVELOPE_OVERHEAD_BYTES + 4 * (
        (max_bytes + 2) // 3
    )
    if max_response_bytes > _MAX_SINGLE_FILE_RESPONSE_BYTES:
        return _capture_error(slot, "schema_error")
    guest_path = str(guest_home / PurePosixPath(slot.locator_relative_paths[0]))
    try:
        content = getter(
            guest_path,
            max_bytes=max_bytes,
            max_response_bytes=max_response_bytes,
            timeout_seconds=spec.limits.getter_timeout_seconds,
        )
    except OSWorldGuestPathMissingError:
        return _capture_error(slot, "missing")
    except Exception:
        return _capture_error(slot, "read_error")
    if not isinstance(content, bytes) or len(content) > max_bytes:
        return _capture_error(slot, "schema_error")
    return ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(content,),
    )


def _capture_file_bundle(
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
) -> ArtifactFamilyCapture:
    """按 spec locator 顺序原子读取一个固定文件 bundle。

    输入参数：
        spec/slot：已验证的多文件 evidence spec。
        guest_home：冻结 guest home。
        controller：实现 ``collect_file_bytes`` 的边界。
    输出返回值：
        全部文件可靠读取才 available；任一失败即丢弃已读原值。
    """

    if len(slot.locator_relative_paths) < 2:
        return _capture_error(slot, "schema_error")
    try:
        getter_options = _load_strict_json_object(slot.getter_options_json)
    except (TypeError, ValueError):
        return _capture_error(slot, "schema_error")
    if getter_options:
        return _capture_error(slot, "schema_error")
    getter = getattr(controller, "collect_file_bytes", None)
    if not callable(getter):
        return _capture_error(slot, "read_error")

    contents: list[bytes] = []
    total_bytes = 0
    for relative_path in slot.locator_relative_paths:
        remaining_total = spec.limits.max_total_bytes - total_bytes
        max_bytes = min(
            artifact_family_single_file_byte_limit(spec),
            remaining_total,
        )
        if max_bytes <= 0:
            return _capture_error(slot, "schema_error")
        max_response_bytes = _BASE64_HTTP_ENVELOPE_OVERHEAD_BYTES + 4 * (
            (max_bytes + 2) // 3
        )
        if max_response_bytes > _MAX_SINGLE_FILE_RESPONSE_BYTES:
            return _capture_error(slot, "schema_error")
        guest_path = str(guest_home / PurePosixPath(relative_path))
        try:
            content = getter(
                guest_path,
                max_bytes=max_bytes,
                max_response_bytes=max_response_bytes,
                timeout_seconds=spec.limits.getter_timeout_seconds,
            )
        except OSWorldGuestPathMissingError:
            return _capture_error(slot, "missing")
        except Exception:
            return _capture_error(slot, "read_error")
        if not isinstance(content, bytes) or len(content) > max_bytes:
            return _capture_error(slot, "schema_error")
        contents.append(content)
        total_bytes += len(content)
    return ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file-bundle",
        _file_items=tuple(contents),
    )


def _capture_pdf_archive(
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
) -> ArtifactFamilyCapture:
    """有界读取并安全展开 BatchOperation-003 的直接 PDF 成员。

    输入参数：
        spec/slot：已验证的 zip-pdf-bundle evidence spec。
        guest_home：冻结 guest home。
        controller：实现 ``collect_file_bytes`` 的边界。
    输出返回值：
        名称排序后的 file-bundle capture；成员名与字节只驻留私有字段。
    """

    raw_capture = _capture_single_file(spec, slot, guest_home, controller)
    if raw_capture.status != "available":
        return ArtifactFamilyCapture(
            slot_id=slot.slot_id,
            status=raw_capture.status,
            payload_kind="file-bundle",
        )
    raw_items = raw_capture.file_items()
    if len(raw_items) != 1:
        return ArtifactFamilyCapture(
            slot_id=slot.slot_id,
            status="schema_error",
            payload_kind="file-bundle",
        )
    try:
        with zipfile.ZipFile(BytesIO(raw_items[0]), mode="r") as archive:
            members = _validate_zip_members(
                archive,
                max_entries=spec.limits.max_container_entries,
                max_single_member_bytes=spec.limits.max_single_item_bytes,
                max_expanded_bytes=spec.limits.max_container_expanded_bytes,
            )
            infos = sorted(
                members.values(),
                key=lambda member: member.filename,
            )
            if len(infos) > spec.limits.max_items or any(
                member.is_dir()
                or "/" in member.filename
                or not member.filename.endswith(".pdf")
                or not _is_safe_member_name(member.filename)
                for member in infos
            ):
                raise ValueError("PDF archive 成员闭集无效")
            contents = tuple(
                _read_zip_member(
                    archive,
                    member,
                    max_bytes=spec.limits.max_single_item_bytes,
                )
                for member in infos
            )
    except Exception:
        return ArtifactFamilyCapture(
            slot_id=slot.slot_id,
            status="parse_error",
            payload_kind="file-bundle",
        )
    return ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file-bundle",
        _file_items=contents,
        _member_names=tuple(member.filename for member in infos),
    )


def _capture_pptx_background(
    spec: ArtifactEvidenceSpec,
    slot: ArtifactSlotEvidenceSpec,
    guest_home: PurePosixPath,
    controller: Any,
) -> ArtifactFamilyCapture:
    """有界读取 PPTX 并提取冻结页码的直接内嵌背景图。

    输入参数：
        spec/slot：已验证的 PPTX 背景 evidence spec。
        guest_home：冻结 guest home。
        controller：实现 ``collect_file_bytes`` 的边界。
    输出返回值：
        available/missing/read_error/parse_error/schema_error capture。
    """

    if len(slot.locator_relative_paths) != 1:
        return _capture_error(slot, "schema_error")
    try:
        getter_options = _load_strict_json_object(slot.getter_options_json)
    except (TypeError, ValueError):
        return _capture_error(slot, "schema_error")
    slide_index = getter_options.get("slide_index")
    if (
        set(getter_options) != {"slide_index"}
        or not isinstance(slide_index, int)
        or isinstance(slide_index, bool)
        or slide_index < 0
    ):
        return _capture_error(slot, "schema_error")
    getter = getattr(controller, "collect_file_bytes", None)
    if not callable(getter):
        return _capture_error(slot, "read_error")
    max_bytes = artifact_family_single_file_byte_limit(spec)
    max_response_bytes = _BASE64_HTTP_ENVELOPE_OVERHEAD_BYTES + 4 * (
        (max_bytes + 2) // 3
    )
    if max_response_bytes > _MAX_SINGLE_FILE_RESPONSE_BYTES:
        return _capture_error(slot, "schema_error")
    guest_path = str(guest_home / PurePosixPath(slot.locator_relative_paths[0]))
    try:
        content = getter(
            guest_path,
            max_bytes=max_bytes,
            max_response_bytes=max_response_bytes,
            timeout_seconds=spec.limits.getter_timeout_seconds,
        )
    except OSWorldGuestPathMissingError:
        return _capture_error(slot, "missing")
    except Exception:
        return _capture_error(slot, "read_error")
    if not isinstance(content, bytes) or len(content) > max_bytes:
        return _capture_error(slot, "schema_error")
    try:
        image = _extract_pptx_background_image(
            content,
            slide_index=slide_index,
            max_entries=spec.limits.max_container_entries,
            max_single_member_bytes=spec.limits.max_single_item_bytes,
            max_expanded_bytes=spec.limits.max_container_expanded_bytes,
            max_xml_bytes=spec.limits.max_text_bytes,
        )
    except _PPTXBackgroundMissing:
        return _capture_error(slot, "missing")
    except Exception:
        return _capture_error(slot, "parse_error")
    return ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="image",
        _file_items=(image,),
    )


def _extract_pptx_background_image(
    content: bytes,
    *,
    slide_index: int,
    max_entries: int,
    max_single_member_bytes: int,
    max_expanded_bytes: int,
    max_xml_bytes: int,
) -> bytes:
    """从 PPTX ZIP 中安全解析指定页的直接背景图片关系。

    输入参数：
        content：已由 nofollow getter 有界读取的 PPTX bytes。
        slide_index：与旧 getter 一致的零基页码。
        max_entries/max_single_member_bytes/max_expanded_bytes/max_xml_bytes：
            spec 冻结的容器、单成员、总展开和 XML 资源上限。
    输出返回值：
        关系指向的内部图片 bytes；不返回成员路径。
    异常：
        _PPTXBackgroundMissing：目标页或直接背景关系不存在。
        ValueError/zipfile.BadZipFile：容器、XML、关系或资源边界无效。
    """

    with zipfile.ZipFile(BytesIO(content), mode="r") as archive:
        members = _validate_zip_members(
            archive,
            max_entries=max_entries,
            max_single_member_bytes=max_single_member_bytes,
            max_expanded_bytes=max_expanded_bytes,
        )
        slide_number = slide_index + 1
        slide_name = f"ppt/slides/slide{slide_number}.xml"
        rels_name = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
        slide_member = members.get(slide_name)
        rels_member = members.get(rels_name)
        if slide_member is None or rels_member is None:
            raise _PPTXBackgroundMissing()
        slide_xml = _read_zip_member(
            archive,
            slide_member,
            max_bytes=max_xml_bytes,
        )
        rels_xml = _read_zip_member(
            archive,
            rels_member,
            max_bytes=max_xml_bytes,
        )
        slide_root = _parse_safe_xml(slide_xml)
        rels_root = _parse_safe_xml(rels_xml)

        bg_tag = "{http://schemas.openxmlformats.org/presentationml/2006/main}bgPr"
        blip_tag = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
        embed_attr = (
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
        )
        relationship_ids = tuple(
            element.attrib.get(embed_attr)
            for background in slide_root.iter(bg_tag)
            for element in background.iter(blip_tag)
            if isinstance(element.attrib.get(embed_attr), str)
            and element.attrib.get(embed_attr)
        )
        if not relationship_ids:
            raise _PPTXBackgroundMissing()
        if len(relationship_ids) != 1:
            raise ValueError("PPTX 背景关系不唯一")

        relationship_tag = (
            "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
        )
        observed_ids: set[str] = set()
        targets: list[str] = []
        for relationship in rels_root.iter(relationship_tag):
            relationship_id = relationship.attrib.get("Id")
            relationship_type = relationship.attrib.get("Type")
            target = relationship.attrib.get("Target")
            target_mode = relationship.attrib.get("TargetMode", "Internal")
            if (
                not isinstance(relationship_id, str)
                or not relationship_id
                or relationship_id in observed_ids
            ):
                raise ValueError("PPTX relationship Id 无效")
            observed_ids.add(relationship_id)
            if relationship_id != relationship_ids[0]:
                continue
            if (
                not isinstance(relationship_type, str)
                or not relationship_type.endswith("/image")
                or not isinstance(target, str)
                or not target
                or target_mode != "Internal"
            ):
                raise ValueError("PPTX 背景 relationship 无效")
            targets.append(target)
        if not targets:
            raise _PPTXBackgroundMissing()
        if len(targets) != 1:
            raise ValueError("PPTX 背景 target 不唯一")
        resolved_target = posixpath.normpath(posixpath.join("ppt/slides", targets[0]))
        if (
            resolved_target.startswith("../")
            or resolved_target.startswith("/")
            or not resolved_target.startswith("ppt/media/")
            or "\\" in resolved_target
        ):
            raise ValueError("PPTX 背景 target 越界")
        image_member = members.get(resolved_target)
        if image_member is None:
            raise _PPTXBackgroundMissing()
        image = _read_zip_member(
            archive,
            image_member,
            max_bytes=max_single_member_bytes,
        )
        if not image:
            raise ValueError("PPTX 背景图为空")
        return image


def _validate_zip_members(
    archive: zipfile.ZipFile,
    *,
    max_entries: int,
    max_single_member_bytes: int,
    max_expanded_bytes: int,
) -> dict[str, zipfile.ZipInfo]:
    """验证 OOXML central directory 的路径、类型与资源闭包。

    输入参数：
        archive：已打开但尚未信任的 ZIP。
        max_entries/max_single_member_bytes/max_expanded_bytes：固定上限。
    输出返回值：
        成员名到唯一 ``ZipInfo`` 的新字典。
    异常：
        ValueError：重复、加密、symlink、路径穿越或大小超限。
    """

    infos = archive.infolist()
    if len(infos) > max_entries:
        raise ValueError("OOXML 成员数超限")
    members: dict[str, zipfile.ZipInfo] = {}
    normalized_names: set[str] = set()
    expanded_bytes = 0
    for info in infos:
        name = info.filename
        normalized = name[:-1] if name.endswith("/") else name
        parts = normalized.split("/")
        mode = (info.external_attr >> 16) & 0o170000
        if (
            not normalized
            or name.startswith("/")
            or "\\" in name
            or "\x00" in name
            or any(part in {"", ".", ".."} for part in parts)
            or any(not character.isprintable() for character in name)
            or name in members
            or normalized in normalized_names
            or info.flag_bits & 0x1
            or info.file_size < 0
            or info.compress_size < 0
            or info.file_size > max_single_member_bytes
            or (mode and mode not in {stat.S_IFREG, stat.S_IFDIR})
        ):
            raise ValueError("OOXML 成员 metadata 无效")
        expanded_bytes += info.file_size
        if expanded_bytes > max_expanded_bytes:
            raise ValueError("OOXML 展开大小超限")
        normalized_names.add(normalized)
        members[name] = info
    return members


def _read_zip_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    max_bytes: int,
) -> bytes:
    """以声明大小与实际流双重上限读取单个 ZIP 成员。

    输入参数：
        archive/member：已完成 central-directory 校验的容器与成员。
        max_bytes：本次解析允许的原始展开字节上限。
    输出返回值：
        完整成员 bytes。
    异常：
        ValueError：声明大小、实际大小或尾部不符合上限。
    """

    if member.file_size > max_bytes:
        raise ValueError("OOXML 成员读取超限")
    with archive.open(member, mode="r") as stream:
        content = stream.read(max_bytes + 1)
        tail = stream.read(1)
    if (
        not isinstance(content, bytes)
        or not isinstance(tail, bytes)
        or len(content) > max_bytes
        or len(content) != member.file_size
        or tail != b""
    ):
        raise ValueError("OOXML 成员读取不完整")
    return content


def _parse_safe_xml(content: bytes) -> ET.Element:
    """拒绝 DTD/entity 后解析有界 OOXML XML。

    输入参数：
        content：已由成员读取上限约束的 XML bytes。
    输出返回值：
        标准库 ``Element`` 根节点。
    异常：
        ValueError/ET.ParseError：声明了主动实体或 XML 无效。
    """

    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("OOXML XML 含主动实体声明")
    return ET.fromstring(content)


def _capture_error(
    slot: ArtifactSlotEvidenceSpec,
    status: str,
) -> ArtifactFamilyCapture:
    """构造不携带任何原值的固定失败 capture。

    输入参数：
        slot：可信 spec 槽位。
        status：missing/read_error/parse_error/schema_error。
    输出返回值：
        私有载荷为空的 ``ArtifactFamilyCapture``。
    """

    return ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status=status,
        payload_kind=_payload_kind_for_slot(slot),
    )


def _payload_kind_for_slot(slot: ArtifactSlotEvidenceSpec) -> str:
    """把冻结 getter kind 映射为稳定的临时 payload family。

    输入参数：
        slot：可信 evidence spec 槽位。
    输出返回值：
        file、file-bundle、directory-listing 或 image。
    """

    if slot.artifact_kind == "zip-pdf-bundle":
        return "file-bundle"
    return {
        "file": "file",
        "file-bundle": "file-bundle",
        "directory-listing": "directory-listing",
        "pptx-slide-background-image": "image",
    }.get(slot.getter_kind, "file")


def _verify_spec_digest(spec: ArtifactEvidenceSpec) -> None:
    """验证 spec schema 与 canonical SHA-256 自身份。

    输入参数：
        spec：默认只读 catalog 中的候选规格。
    输出返回值：
        无；schema 与摘要完全匹配时返回。
    异常：
        OSWorldArtifactFamilyEvidenceError：规格失配，且不回显原值。
    """

    try:
        validate_artifact_evidence_spec(spec)
        canonical = canonical_artifact_evidence_spec_json(spec)
    except (ArtifactEvidenceSpecError, TypeError, ValueError):
        raise OSWorldArtifactFamilyEvidenceError("artifact family spec 无效") from None
    if (
        _SHA256_PATTERN.fullmatch(spec.evidence_spec_sha256) is None
        or hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        != spec.evidence_spec_sha256
    ):
        raise OSWorldArtifactFamilyEvidenceError("artifact family spec 摘要不匹配")


def _resolve_guest_home_from_shared_binding(
    guest_shared_dir: str | None,
) -> PurePosixPath:
    """从 prepare 阶段冻结的 shared locator 还原 guest home。

    输入参数：
        guest_shared_dir：规范 POSIX 绝对路径，末段必须严格为 shared。
    输出返回值：
        非根目录、无 ``..`` 的动态 guest home。
    异常：
        OSWorldArtifactFamilyEvidenceError：绑定缺失或不安全。
    """

    if not isinstance(guest_shared_dir, str) or not guest_shared_dir:
        raise OSWorldArtifactFamilyEvidenceError("artifact guest 路径绑定缺失")
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
        raise OSWorldArtifactFamilyEvidenceError("artifact guest 路径绑定无效")
    return guest_home


def _is_safe_member_name(value: object) -> bool:
    """独立复核目录成员是单一可打印 POSIX 名称。

    输入参数：
        value：controller 返回的未信任成员名。
    输出返回值：
        非空、无分隔符/NUL/控制字符且不超过 255 UTF-8 bytes 时为真。
    """

    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or any(not character.isprintable() for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", "strict")) <= _MAX_POSIX_NAME_BYTES
    except UnicodeEncodeError:
        return False


def _load_strict_json_object(serialized: str) -> dict[str, object]:
    """解析 canonical JSON object 并拒绝重复键和非标准常量。

    输入参数：
        serialized：spec 中固定的 getter options JSON。
    输出返回值：
        新建的顶层 dict。
    异常：
        TypeError/ValueError：文本、键唯一性或顶层类型无效。
    """

    if not isinstance(serialized, str):
        raise TypeError("artifact getter options 必须是 JSON 字符串")

    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        """把 JSON 键值对转成 dict，并拒绝重复键。

        输入参数：
            pairs：decoder 保留顺序的键值对。
        输出返回值：
            键唯一的新字典。
        """

        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("artifact getter options 含重复键")
            result[key] = value
        return result

    def reject_constant(_value: str) -> object:
        """拒绝 NaN/Infinity 等非标准 JSON 常量。

        输入参数：
            _value：decoder 识别的常量文本；故意不回显。
        输出返回值：
            不返回；始终抛出 ``ValueError``。
        """

        raise ValueError("artifact getter options 含非标准常量")

    payload = json.loads(
        serialized,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("artifact getter options 顶层必须是对象")
    return payload


__all__ = [
    "ARTIFACT_FAMILY_SINGLE_FILE_MAX_BYTES",
    "LEGACY_OSWORLD_ARTIFACT_TASK_IDS",
    "ArtifactFamilyCapture",
    "OSWorldArtifactFamilyEvidenceError",
    "OSWorldArtifactFamilyEvidenceSource",
    "artifact_family_single_file_byte_limit",
    "evaluate_inline_directory_membership",
]
