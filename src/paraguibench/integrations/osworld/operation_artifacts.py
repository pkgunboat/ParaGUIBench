"""Operation 任务的受控 guest artifact 完整快照。"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
from typing import Any
import unicodedata

from paraguibench.evaluation.operation import (
    OPERATION_PROTOCOL_ID,
    OPERATION_TASK_RULES,
)


_MAX_FILES = 512
_MAX_NODES = 1024
_MAX_DEPTH = 8
_MAX_NAME_BYTES = 255
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_MANIFEST_RESPONSE_BYTES = 1024 * 1024
_MAX_FILE_RESPONSE_BYTES = 16 * 1024 * 1024
_FILE_RESPONSE_ENVELOPE_RESERVE_BYTES = 4096
_MAX_FILE_BYTES = 3 * (
    (_MAX_FILE_RESPONSE_BYTES - _FILE_RESPONSE_ENVELOPE_RESERVE_BYTES) // 4
)
_CAPTURE_TIMEOUT_SECONDS = 30.0
_CAPTURE_TOTAL_TIMEOUT_SECONDS = 300.0
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OperationArtifactCaptureError(RuntimeError):
    """Operation artifact 树无法形成可信完整快照。"""


class OperationArtifactSnapshot:
    """保存单个 Attempt 临时 artifact 根及脱敏身份。

    输入参数：
        task_id/protocol_id：已由 capture source 验证的任务与协议。
        file_count：完整 guest manifest 中的常规文件数。
        temporary_directory：当前快照独占的临时目录句柄。
    输出返回值：
        可在 evaluator 内短暂读取、但不应写入 RunStore 的快照对象。
    """

    __slots__ = (
        "_closed",
        "_file_count",
        "_protocol_id",
        "_task_id",
        "_temporary_directory",
    )

    def __init__(
        self,
        *,
        task_id: str,
        protocol_id: str,
        file_count: int,
        temporary_directory: tempfile.TemporaryDirectory[str],
    ) -> None:
        """绑定已完成下载和校验的临时目录。

        输入参数：
            task_id/protocol_id/file_count：快照的脱敏身份与计数。
            temporary_directory：由 source 创建且尚未清理的目录。
        输出返回值：
            无；对象取得该目录的唯一清理责任。
        """

        self._task_id = task_id
        self._protocol_id = protocol_id
        self._file_count = file_count
        self._temporary_directory = temporary_directory
        self._closed = False

    @property
    def task_id(self) -> str:
        """返回固定 canonical task ID。

        输入参数：无。
        输出返回值：不含 artifact 信息的任务 ID。
        """

        return self._task_id

    @property
    def protocol_id(self) -> str:
        """返回固定 Operation 评价协议 ID。

        输入参数：无。
        输出返回值：版本化协议字符串。
        """

        return self._protocol_id

    @property
    def file_count(self) -> int:
        """返回 guest 完整 manifest 的文件数。

        输入参数：无。
        输出返回值：不含文件名的安全整数计数。
        """

        return self._file_count

    def artifact_root(self) -> Path:
        """仅向可信 pure evaluator 提供当前临时根。

        输入参数：无。
        输出返回值：未关闭快照的 host ``Path``。
        异常：
            OperationArtifactCaptureError：快照已关闭，拒绝返回失效路径。
        """

        if self._closed:
            raise OperationArtifactCaptureError("operation artifact 快照已关闭")
        return Path(self._temporary_directory.name)

    def close(self) -> None:
        """幂等删除当前 Attempt 拥有的临时 artifact 树。

        输入参数：无。
        输出返回值：无；重复调用不做任何事。
        """

        if self._closed:
            return
        try:
            self._temporary_directory.cleanup()
        except Exception:
            raise OperationArtifactCaptureError(
                "operation artifact 快照清理失败"
            ) from None
        self._closed = True

    def __repr__(self) -> str:
        """返回不包含 host 路径和文件名的调试表示。

        输入参数：无。
        输出返回值：仅含协议、任务、计数和关闭状态的字符串。
        """

        return (
            "OperationArtifactSnapshot("
            f"task_id={self._task_id!r}, protocol_id={self._protocol_id!r}, "
            f"file_count={self._file_count!r}, closed={self._closed!r})"
        )


class OSWorldOperationArtifactSource:
    """通过固定 guest helper contract 捕获 Operation 完整文件树。"""

    def capture(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> OperationArtifactSnapshot:
        """冻结 manifest，逐文件 nofollow 下载并校验后创建快照。

        输入参数：
            task_id：必须命中 32-task Operation 规则闭集。
            controller：必须提供受限完整 manifest 与单文件 getter。
            guest_shared_dir：prepare 阶段冻结的规范 POSIX 绝对目录。
        输出返回值：
            校验过大小与 SHA-256 的 host 临时快照。
        异常：
            OperationArtifactCaptureError：任何身份、接口、manifest、
                下载、完整性或 host I/O 失败；消息不回显路径和内容。
        """

        if not isinstance(task_id, str) or task_id not in OPERATION_TASK_RULES:
            raise OperationArtifactCaptureError("operation artifact 任务未注册")
        guest_root = _validate_guest_shared_dir(guest_shared_dir)
        manifest_getter = getattr(
            controller,
            "collect_artifact_tree_manifest",
            None,
        )
        file_getter = getattr(controller, "collect_file_bytes", None)
        if not callable(manifest_getter) or not callable(file_getter):
            raise OperationArtifactCaptureError("operation artifact getter 未装配")
        capture_deadline = time.monotonic() + _CAPTURE_TOTAL_TIMEOUT_SECONDS
        manifest_limits = {
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
                timeout_seconds=_remaining_capture_timeout(capture_deadline),
                **manifest_limits,
            )
        except Exception:
            raise OperationArtifactCaptureError(
                "operation artifact manifest 捕获失败"
            ) from None
        manifest = _validate_manifest(raw_manifest)
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="paraguibench-operation-"
        )
        root = Path(temporary_directory.name)
        try:
            for relative_path, expected_size, expected_sha256 in manifest:
                guest_path = guest_root.joinpath(*relative_path.parts)
                payload = file_getter(
                    guest_path.as_posix(),
                    max_bytes=max(1, expected_size),
                    max_response_bytes=_MAX_FILE_RESPONSE_BYTES,
                    timeout_seconds=_remaining_capture_timeout(capture_deadline),
                )
                if not isinstance(payload, bytes):
                    raise OperationArtifactCaptureError(
                        "operation artifact 文件响应类型无效"
                    )
                if (
                    len(payload) != expected_size
                    or hashlib.sha256(payload).hexdigest() != expected_sha256
                ):
                    raise OperationArtifactCaptureError(
                        "operation artifact 文件快照不稳定"
                    )
                destination = root.joinpath(*relative_path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as artifact_file:
                    artifact_file.write(payload)
            final_manifest = _validate_manifest(
                manifest_getter(
                    guest_root.as_posix(),
                    timeout_seconds=_remaining_capture_timeout(capture_deadline),
                    **manifest_limits,
                )
            )
            if final_manifest != manifest:
                raise OperationArtifactCaptureError(
                    "operation artifact 文件树捕获期间发生变化"
                )
        except Exception:
            try:
                temporary_directory.cleanup()
            except Exception:
                pass
            raise OperationArtifactCaptureError(
                "operation artifact 文件捕获失败"
            ) from None
        return OperationArtifactSnapshot(
            task_id=task_id,
            protocol_id=OPERATION_PROTOCOL_ID,
            file_count=len(manifest),
            temporary_directory=temporary_directory,
        )


def _remaining_capture_timeout(deadline: float) -> float:
    """返回当前 getter 可用的剩余截止时间。

    输入参数：
        deadline：整次 Operation capture 的 ``monotonic`` 绝对截止点。
    输出返回值：
        不超过单 getter 30 秒、且不超过总预算剩余量的秒数。
    异常：
        OperationArtifactCaptureError：剩余量小于 controller
            允许的 1 毫秒下限，不再发起新请求。
    """

    remaining = deadline - time.monotonic()
    if remaining < 0.001:
        raise OperationArtifactCaptureError("operation artifact 捕获超过总截止时间")
    return min(_CAPTURE_TIMEOUT_SECONDS, remaining)


def _validate_guest_shared_dir(value: str | None) -> PurePosixPath:
    """验证 prepare 阶段冻结的 guest shared 绝对路径。

    输入参数：
        value：environment 传入的 guest POSIX 目录字符串。
    输出返回值：
        不含空值、NUL、点段或多余分隔符的规范绝对路径。
    """

    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or not value.startswith("/")
        or value == "/"
        or "//" in value
        or value.endswith("/")
    ):
        raise OperationArtifactCaptureError("operation guest shared 路径无效")
    raw_parts = value.split("/")[1:]
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise OperationArtifactCaptureError("operation guest shared 路径无效")
    path = PurePosixPath(value)
    if path.as_posix() != value:
        raise OperationArtifactCaptureError("operation guest shared 路径无效")
    return path


def _validate_manifest(
    value: object,
) -> tuple[tuple[PurePosixPath, int, str], ...]:
    """将 guest manifest 验证为唯一、有序、有界的相对文件闭集。

    输入参数：
        value：guest helper 返回的 ``(relative, size, sha256)`` tuple。
    输出返回值：
        路径已转换为 ``PurePosixPath`` 的不可变 manifest。
    异常：
        OperationArtifactCaptureError：schema、路径、排序、大小或摘要无效。
    """

    if not isinstance(value, tuple) or len(value) > _MAX_FILES:
        raise OperationArtifactCaptureError("operation artifact manifest schema 无效")
    validated: list[tuple[PurePosixPath, int, str]] = []
    total_bytes = 0
    previous_name: str | None = None
    file_names: set[str] = set()
    portable_nodes: dict[tuple[str, ...], tuple[str, ...]] = {}
    for entry in value:
        if not isinstance(entry, tuple) or len(entry) != 3:
            raise OperationArtifactCaptureError(
                "operation artifact manifest schema 无效"
            )
        raw_name, size_bytes, sha256 = entry
        relative_path = _validate_relative_artifact_path(raw_name)
        normalized_name = relative_path.as_posix()
        if previous_name is not None and normalized_name <= previous_name:
            raise OperationArtifactCaptureError("operation artifact manifest 顺序无效")
        if (
            not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 0 <= size_bytes <= _MAX_FILE_BYTES
            or not isinstance(sha256, str)
            or _SHA256_PATTERN.fullmatch(sha256) is None
        ):
            raise OperationArtifactCaptureError("operation artifact manifest 条目无效")
        for parent in relative_path.parents:
            parent_name = parent.as_posix()
            if parent_name != "." and parent_name in file_names:
                raise OperationArtifactCaptureError(
                    "operation artifact manifest 路径冲突"
                )
        if any(existing.startswith(f"{normalized_name}/") for existing in file_names):
            raise OperationArtifactCaptureError("operation artifact manifest 路径冲突")
        parts = relative_path.parts
        for index in range(1, len(parts) + 1):
            actual_node = tuple(parts[:index])
            portable_node = tuple(
                unicodedata.normalize("NFC", component).casefold()
                for component in actual_node
            )
            previous_node = portable_nodes.get(portable_node)
            if previous_node is not None and previous_node != actual_node:
                raise OperationArtifactCaptureError(
                    "operation artifact manifest 路径冲突"
                )
            portable_nodes[portable_node] = actual_node
        total_bytes += size_bytes
        if total_bytes > _MAX_TOTAL_BYTES:
            raise OperationArtifactCaptureError(
                "operation artifact manifest 超过大小上限"
            )
        validated.append((relative_path, size_bytes, sha256))
        file_names.add(normalized_name)
        previous_name = normalized_name
    return tuple(validated)


def _validate_relative_artifact_path(value: object) -> PurePosixPath:
    """拒绝绝对、穿越、非规范、过深或过长的 artifact 路径。

    输入参数：
        value：guest manifest 中的相对 POSIX 路径字符串。
    输出返回值：
        可安全拼到 host 临时根的 ``PurePosixPath``。
    """

    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or len(value.encode("utf-8")) > 4096
        or any(not character.isprintable() for character in value)
    ):
        raise OperationArtifactCaptureError("operation artifact 相对路径无效")
    raw_parts = value.split("/")
    if (
        len(raw_parts) > _MAX_DEPTH
        or any(part in {"", ".", ".."} for part in raw_parts)
        or any(len(part.encode("utf-8")) > _MAX_NAME_BYTES for part in raw_parts)
    ):
        raise OperationArtifactCaptureError("operation artifact 相对路径无效")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise OperationArtifactCaptureError("operation artifact 相对路径无效")
    return path


__all__ = [
    "OSWorldOperationArtifactSource",
    "OperationArtifactCaptureError",
    "OperationArtifactSnapshot",
]
