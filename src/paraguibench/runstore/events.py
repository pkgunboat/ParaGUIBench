"""RunStore 中单 producer 独占的结构化事件流。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .contracts import TaskAttempt
from .identifiers import validate_identifier
from .persistence import (
    append_private_json_line,
    ensure_private_subdirectory,
)
from .privacy import sanitize_record

_SCHEMA_VERSION = "1.0"
_PRODUCER_DIRECTORIES = {
    "environment": "environment",
    "evaluator": "evaluator",
    "planner": "planner",
    "runtime": "runtime",
    "worker": "workers",
}


class EventStream:
    """为单个 producer 顺序写入默认脱敏的 JSONL 事件。

    输入参数：
        attempt：事件所属的任务 Attempt。
        producer_kind：planner、worker、environment、evaluator 或 runtime。
        producer_id：本 Attempt 内稳定且安全的 producer 标识。
    输出返回值：
        实例通过 ``append`` 返回所写事件的全局唯一 ``event_id``。
    """

    def __init__(
        self,
        *,
        attempt: TaskAttempt,
        producer_kind: str,
        producer_id: str,
    ) -> None:
        """初始化独占事件文件与进程内序号。

        输入参数：
            attempt：已经由 RunStore 建立的任务 Attempt。
            producer_kind：受支持的 producer 类别。
            producer_id：本 Attempt 内的 producer 标识。
        输出返回值：
            无；对应私有目录被建立，首个事件序号从 1 开始。
        """

        if producer_kind not in _PRODUCER_DIRECTORIES:
            supported = ", ".join(sorted(_PRODUCER_DIRECTORIES))
            raise ValueError(
                f"producer_kind must be one of: {supported}"
            )

        self._attempt = attempt
        self._producer_kind = producer_kind
        self._producer_id = validate_identifier("producer_id", producer_id)
        self._sequence = 0
        self._lock = Lock()
        producer_directory = ensure_private_subdirectory(
            attempt.path,
            _PRODUCER_DIRECTORIES[producer_kind],
            self._producer_id,
        )
        self._path = producer_directory / "events-00001.jsonl"

    @property
    def path(self) -> Path:
        """返回该 producer 当前事件文件路径。

        输入参数：
            无。
        输出返回值：
            JSONL 文件的 ``Path``；调用方不得绕过 EventStream 直接写入。
        """

        return self._path

    def append(
        self,
        *,
        event_type: str,
        data: Mapping[str, Any],
        level: str = "INFO",
    ) -> str:
        """追加一条具有因果身份和 producer 顺序的脱敏事件。

        输入参数：
            event_type：稳定的点分事件类型，例如 ``worker.step``。
            data：需要记录的结构化事件数据，写入前统一递归脱敏。
            level：日志级别，默认 ``INFO``。
        输出返回值：
            新事件的全局唯一 ``event_id``。
        """

        normalized_event_type = validate_identifier("event_type", event_type)
        normalized_level = level.upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("unsupported event level")

        with self._lock:
            self._sequence += 1
            event_id = uuid4().hex
            append_private_json_line(
                self._path,
                sanitize_record(
                    {
                        "schema_version": _SCHEMA_VERSION,
                        "event_id": event_id,
                        "run_id": self._attempt.run_id,
                        "task_id": self._attempt.task_id,
                        "attempt_id": self._attempt.attempt_id,
                        "producer_kind": self._producer_kind,
                        "producer_id": self._producer_id,
                        "producer_sequence": self._sequence,
                        "timestamp_utc": datetime.now(UTC).isoformat(),
                        "level": normalized_level,
                        "event_type": normalized_event_type,
                        "data": dict(data),
                    }
                ),
            )
        return event_id
