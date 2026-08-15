"""pipeline-implicit 任务的有界、no-follow 原子 artifact 闭集。

本模块只在 evaluator 可信边界内保留文件名、摘要和原始字节。
``PipelineImplicitArtifactObservation`` 不得直接持久化到 RunStore；
共享 runtime 后续只应持久化纯 evaluator 返回的脱敏计数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import PurePosixPath
import re
import time
from types import MappingProxyType
from typing import Any, Iterator
import unicodedata

from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_PROTOCOL_ID,
    HIDE_NA_ROWS_TASK_ID,
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    SEARCHWRITE_XLSX_PROTOCOL_ID,
    SEARCHWRITE_XLSX_TASK_ID,
    CrossDocumentObservation,
    ImageClassificationObservation,
    HideNARowsObservation,
    SearchWriteObservation,
)


_MAX_FILES = 64
_MAX_NODES = 128
_MAX_DEPTH = 4
_MAX_NAME_BYTES = 255
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_MANIFEST_RESPONSE_BYTES = 1024 * 1024
_MAX_FILE_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_FILE_BYTES = 12 * 1024 * 1024
_CAPTURE_TIMEOUT_SECONDS = 30.0
_CAPTURE_TOTAL_TIMEOUT_SECONDS = 180.0
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")

PIPELINE_IMPLICIT_TASK_PROTOCOLS = MappingProxyType(
    {
        HIDE_NA_ROWS_TASK_ID: HIDE_NA_ROWS_PROTOCOL_ID,
        IMAGE_CLASSIFICATION_TASK_ID: IMAGE_CLASSIFICATION_PROTOCOL_ID,
        CROSS_DOCUMENT_TASK_ID: CROSS_DOCUMENT_PROTOCOL_ID,
        SEARCHWRITE_XLSX_TASK_ID: SEARCHWRITE_XLSX_PROTOCOL_ID,
    }
)

_ERROR_CODES = frozenset(
    {
        "TASK_NOT_REGISTERED",
        "GUEST_SHARED_DIR_INVALID",
        "GETTER_NOT_AVAILABLE",
        "MANIFEST_CAPTURE_FAILED",
        "MANIFEST_INVALID",
        "ARTIFACT_PATH_INVALID",
        "ARTIFACT_LIMIT_EXCEEDED",
        "FILE_CAPTURE_FAILED",
        "FILE_INTEGRITY_INVALID",
        "BUNDLE_CHANGED",
        "CAPTURE_DEADLINE_EXCEEDED",
        "EVIDENCE_PATH_INVALID",
        "TYPED_OBSERVATION_INVALID",
    }
)


class PipelineImplicitArtifactEvidenceError(RuntimeError):
    """表示 artifact 闭集无法形成可信观测。

    输入参数：
        code：不含 guest/host 路径、文件名或内容的固定错误码。
    输出返回值：
        可由 runtime 稳定分类的脱敏异常。
    """

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("pipeline implicit evidence error code 未注册")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class PipelineImplicitArtifactFile:
    """保存一个 evaluator-only 的已校验文件观测。

    输入参数：
        relative_path：相对冻结 shared 根的安全 POSIX 路径。
        size_bytes/sha256：guest manifest 和下载字节同时确认的身份。
        _payload：仅供后续受控解析器短期读取的原始字节。
    输出返回值：
        不可变 typed file observation；自定义 ``repr`` 不暴露身份。
    """

    relative_path: str = field(repr=False)
    size_bytes: int
    sha256: str = field(repr=False)
    _payload: bytes = field(repr=False)

    def read_for_evaluator(self) -> bytes:
        """返回已完整性校验的文件字节。

        输入参数：无。
        输出返回值：
            仅供同进程受信 evaluator 使用的不可变 ``bytes``。
        """

        return self._payload

    def __repr__(self) -> str:
        """返回仅含字节计数的脱敏调试表示。

        输入参数：无。
        输出返回值：
            不含相对路径、摘要和 payload 的字符串。
        """

        return f"PipelineImplicitArtifactFile(size_bytes={self.size_bytes!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PipelineImplicitArtifactObservation:
    """保存单个 Attempt 的原子文件闭集。

    输入参数：
        task_id/protocol_id：捕获时绑定的 canonical 任务和协议。
        complete：始终为真，表示前后 manifest 完全一致。
        _files：按 UTF-8 字节序稳定排列的所有常规文件。
    输出返回值：
        可交给专属 parser/evaluator 的短生命周期 typed observation。
    """

    task_id: str
    protocol_id: str
    complete: bool
    _files: tuple[PipelineImplicitArtifactFile, ...] = field(repr=False)

    @property
    def file_count(self) -> int:
        """返回原子闭集的常规文件数。

        输入参数：无。
        输出返回值：非负文件数。
        """

        return len(self._files)

    @property
    def total_bytes(self) -> int:
        """返回闭集原始字节总量。

        输入参数：无。
        输出返回值：已校验文件大小之和。
        """

        return sum(item.size_bytes for item in self._files)

    def iter_files_for_evaluator(self) -> Iterator[PipelineImplicitArtifactFile]:
        """以 manifest 稳定顺序迭代 evaluator-only 文件。

        输入参数：无。
        输出返回值：
            不产生路径副本的 typed file iterator。
        """

        return iter(self._files)

    def read_file_for_evaluator(self, relative_path: str) -> bytes:
        """按安全相对路径读取一个已冻结文件。

        输入参数：
            relative_path：受信 parser 从任务规格派生的 POSIX 路径。
        输出返回值：
            匹配文件的不可变原始字节。
        异常：
            PipelineImplicitArtifactEvidenceError：路径非安全相对路径
                或不存在于已冻结闭集。
        """

        candidate = _validate_relative_path(relative_path)
        normalized = candidate.as_posix()
        for item in self._files:
            if item.relative_path == normalized:
                return item.read_for_evaluator()
        raise PipelineImplicitArtifactEvidenceError("EVIDENCE_PATH_INVALID")

    def __repr__(self) -> str:
        """返回不含文件身份、摘要或内容的表示。

        输入参数：无。
        输出返回值：
            仅含 canonical 任务/协议与资源计数的字符串。
        """

        return (
            "PipelineImplicitArtifactObservation("
            f"task_id={self.task_id!r}, protocol_id={self.protocol_id!r}, "
            f"complete={self.complete!r}, file_count={self.file_count!r}, "
            f"total_bytes={self.total_bytes!r})"
        )


class PipelineImplicitArtifactEvidenceSource:
    """通过 OSWorld 固定 helper 捕获 pipeline-implicit 文件闭集。"""

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> (
        PipelineImplicitArtifactObservation
        | CrossDocumentObservation
        | ImageClassificationObservation
        | HideNARowsObservation
        | SearchWriteObservation
    ):
        """执行 manifest—nofollow 文件—manifest 原子捕获。

        输入参数：
            task_id：必须命中四任务专属规格闭集。
            controller：必须提供完整树 manifest 与单文件 no-follow getter。
            guest_shared_dir：prepare 阶段冻结的规范 POSIX 绝对路径。
        输出返回值：
            前后闭集一致、且每个文件 SHA/长度匹配的
            observation；四个任务均进一步返回各自的正式 typed
            observation，generic bytes 不离开 evaluator 可信边界。
        异常：
            PipelineImplicitArtifactEvidenceError：任何身份、资源、路径、
                getter、完整性或原子性失败；异常文本仅含固定码。
        """

        protocol_id = PIPELINE_IMPLICIT_TASK_PROTOCOLS.get(task_id)
        if protocol_id is None:
            raise PipelineImplicitArtifactEvidenceError("TASK_NOT_REGISTERED")
        guest_root = _validate_guest_shared_dir(guest_shared_dir)
        manifest_getter = getattr(controller, "collect_artifact_tree_manifest", None)
        file_getter = getattr(controller, "collect_file_bytes", None)
        if not callable(manifest_getter) or not callable(file_getter):
            raise PipelineImplicitArtifactEvidenceError("GETTER_NOT_AVAILABLE")
        deadline = time.monotonic() + _CAPTURE_TOTAL_TIMEOUT_SECONDS
        limits = {
            "max_files": _MAX_FILES,
            "max_nodes": _MAX_NODES,
            "max_depth": _MAX_DEPTH,
            "max_name_bytes": _MAX_NAME_BYTES,
            "max_file_bytes": _MAX_FILE_BYTES,
            "max_total_bytes": _MAX_TOTAL_BYTES,
            "max_response_bytes": _MAX_MANIFEST_RESPONSE_BYTES,
        }
        try:
            raw_manifest = manifest_getter(
                guest_root.as_posix(),
                timeout_seconds=_remaining_timeout(deadline),
                **limits,
            )
        except PipelineImplicitArtifactEvidenceError:
            raise
        except Exception:
            raise PipelineImplicitArtifactEvidenceError(
                "MANIFEST_CAPTURE_FAILED"
            ) from None
        manifest = _validate_manifest(raw_manifest)
        files: list[PipelineImplicitArtifactFile] = []
        for relative_path, size_bytes, expected_sha256 in manifest:
            try:
                payload = file_getter(
                    guest_root.joinpath(*relative_path.parts).as_posix(),
                    max_bytes=max(1, size_bytes),
                    max_response_bytes=_MAX_FILE_RESPONSE_BYTES,
                    timeout_seconds=_remaining_timeout(deadline),
                )
            except PipelineImplicitArtifactEvidenceError:
                raise
            except Exception:
                raise PipelineImplicitArtifactEvidenceError(
                    "FILE_CAPTURE_FAILED"
                ) from None
            if (
                not isinstance(payload, bytes)
                or len(payload) != size_bytes
                or hashlib.sha256(payload).hexdigest() != expected_sha256
            ):
                raise PipelineImplicitArtifactEvidenceError("FILE_INTEGRITY_INVALID")
            files.append(
                PipelineImplicitArtifactFile(
                    relative_path=relative_path.as_posix(),
                    size_bytes=size_bytes,
                    sha256=expected_sha256,
                    _payload=payload,
                )
            )
        try:
            final_raw_manifest = manifest_getter(
                guest_root.as_posix(),
                timeout_seconds=_remaining_timeout(deadline),
                **limits,
            )
        except PipelineImplicitArtifactEvidenceError:
            raise
        except Exception:
            raise PipelineImplicitArtifactEvidenceError(
                "MANIFEST_CAPTURE_FAILED"
            ) from None
        if _validate_manifest(final_raw_manifest) != manifest:
            raise PipelineImplicitArtifactEvidenceError("BUNDLE_CHANGED")
        artifact_observation = PipelineImplicitArtifactObservation(
            task_id=task_id,
            protocol_id=protocol_id,
            complete=True,
            _files=tuple(files),
        )
        if task_id == IMAGE_CLASSIFICATION_TASK_ID:
            from .image_classification_bridge import (
                build_image_classification_observation,
            )

            return build_image_classification_observation(artifact_observation)
        if task_id == HIDE_NA_ROWS_TASK_ID:
            from .hide_na_rows_bridge import build_hide_na_rows_observation

            return build_hide_na_rows_observation(artifact_observation)
        if task_id == SEARCHWRITE_XLSX_TASK_ID:
            from .searchwrite_bridge import build_searchwrite_observation

            return build_searchwrite_observation(artifact_observation)
        if task_id == CROSS_DOCUMENT_TASK_ID:
            from .cross_document_bridge import build_cross_document_observation

            return build_cross_document_observation(artifact_observation)
        return artifact_observation


def _remaining_timeout(deadline: float) -> float:
    """返回当前 getter 可用的有界剩余截止时间。

    输入参数：
        deadline：整个 bundle capture 的 ``monotonic`` 绝对截止点。
    输出返回值：
        不超过单 getter 上限且不超过总剩余时间的秒数。
    """

    remaining = deadline - time.monotonic()
    if remaining < 0.001:
        raise PipelineImplicitArtifactEvidenceError("CAPTURE_DEADLINE_EXCEEDED")
    return min(_CAPTURE_TIMEOUT_SECONDS, remaining)


def _validate_guest_shared_dir(value: str | None) -> PurePosixPath:
    """验证 prepare 冻结的 guest shared 目录。

    输入参数：
        value：候选 POSIX 绝对目录字符串。
    输出返回值：
        不含空分量、点段、控制字符和尾斜线的 ``PurePosixPath``。
    """

    if (
        not isinstance(value, str)
        or not value
        or value == "/"
        or not value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or _CONTROL_CHARACTER_PATTERN.search(value) is not None
    ):
        raise PipelineImplicitArtifactEvidenceError("GUEST_SHARED_DIR_INVALID")
    parts = value.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        raise PipelineImplicitArtifactEvidenceError("GUEST_SHARED_DIR_INVALID")
    path = PurePosixPath(value)
    if path.as_posix() != value:
        raise PipelineImplicitArtifactEvidenceError("GUEST_SHARED_DIR_INVALID")
    return path


def _validate_relative_path(value: object) -> PurePosixPath:
    """验证单个 manifest 或 evaluator 相对路径。

    输入参数：
        value：候选 POSIX 相对文件路径。
    输出返回值：
        深度、名称字节长度和 Unicode 均受控的 ``PurePosixPath``。
    """

    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or _CONTROL_CHARACTER_PATTERN.search(value) is not None
    ):
        raise PipelineImplicitArtifactEvidenceError("ARTIFACT_PATH_INVALID")
    raw_parts = value.split("/")
    if len(raw_parts) > _MAX_DEPTH or any(
        part in {"", ".", ".."} for part in raw_parts
    ):
        raise PipelineImplicitArtifactEvidenceError("ARTIFACT_PATH_INVALID")
    try:
        if any(
            not unicodedata.is_normalized("NFC", part)
            or len(part.encode("utf-8", errors="strict")) > _MAX_NAME_BYTES
            for part in raw_parts
        ):
            raise PipelineImplicitArtifactEvidenceError("ARTIFACT_PATH_INVALID")
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise PipelineImplicitArtifactEvidenceError("ARTIFACT_PATH_INVALID") from None
    path = PurePosixPath(value)
    if path.as_posix() != value:
        raise PipelineImplicitArtifactEvidenceError("ARTIFACT_PATH_INVALID")
    return path


def _portable_path_key(path: PurePosixPath) -> tuple[str, ...]:
    """生成防止 host 大小写/Unicode 折叠的节点键。

    输入参数：
        path：已通过单分量 NFC 验证的相对路径。
    输出返回值：
        每个分量经 NFC 和 ``casefold`` 的不可变 tuple。
    """

    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def _validate_manifest(
    value: object,
) -> tuple[tuple[PurePosixPath, int, str], ...]:
    """验证 guest manifest 是唯一、有序、有界的常规文件闭集。

    输入参数：
        value：guest helper 返回的三元组 tuple。
    输出返回值：
        路径已规范化、资源已聚合校验的 manifest tuple。
    """

    if not isinstance(value, tuple):
        raise PipelineImplicitArtifactEvidenceError("MANIFEST_INVALID")
    if len(value) > _MAX_FILES:
        raise PipelineImplicitArtifactEvidenceError("ARTIFACT_LIMIT_EXCEEDED")
    validated: list[tuple[PurePosixPath, int, str]] = []
    total_bytes = 0
    prior_sort_key: bytes | None = None
    portable_nodes: dict[tuple[str, ...], tuple[str, ...]] = {}
    file_paths: set[str] = set()
    for entry in value:
        if not isinstance(entry, tuple) or len(entry) != 3:
            raise PipelineImplicitArtifactEvidenceError("MANIFEST_INVALID")
        raw_path, size_bytes, sha256 = entry
        path = _validate_relative_path(raw_path)
        path_text = path.as_posix()
        sort_key = path_text.encode("utf-8", errors="strict")
        if prior_sort_key is not None and sort_key <= prior_sort_key:
            raise PipelineImplicitArtifactEvidenceError("MANIFEST_INVALID")
        prior_sort_key = sort_key
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 0 <= size_bytes <= _MAX_FILE_BYTES
            or not isinstance(sha256, str)
            or _SHA256_PATTERN.fullmatch(sha256) is None
        ):
            raise PipelineImplicitArtifactEvidenceError("MANIFEST_INVALID")
        total_bytes += size_bytes
        if total_bytes > _MAX_TOTAL_BYTES:
            raise PipelineImplicitArtifactEvidenceError("ARTIFACT_LIMIT_EXCEEDED")
        for depth in range(1, len(path.parts) + 1):
            original_node = path.parts[:depth]
            portable_node = _portable_path_key(PurePosixPath(*original_node))
            prior_node = portable_nodes.get(portable_node)
            if prior_node is not None and prior_node != original_node:
                raise PipelineImplicitArtifactEvidenceError("ARTIFACT_PATH_INVALID")
            portable_nodes[portable_node] = original_node
            if len(portable_nodes) > _MAX_NODES:
                raise PipelineImplicitArtifactEvidenceError("ARTIFACT_LIMIT_EXCEEDED")
        for parent in path.parents:
            parent_text = parent.as_posix()
            if parent_text != "." and parent_text in file_paths:
                raise PipelineImplicitArtifactEvidenceError("ARTIFACT_PATH_INVALID")
        file_paths.add(path_text)
        validated.append((path, size_bytes, sha256))
    return tuple(validated)
