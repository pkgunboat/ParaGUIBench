"""RunStore producer 事件流的隔离与脱敏行为测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paraguibench.runstore import RunStore
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


def test_each_worker_owns_a_sanitized_event_stream(tmp_path: Path) -> None:
    """验证不同 worker 的事件不串流，且事件数据默认脱敏。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；通过公开 event stream interface 和最终 JSONL 断言隔离、身份、
        序号及隐私行为。
    """

    sentinel = "pb-worker-event-secret"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-events-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-events-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )

    first_worker = store.open_event_stream(
        attempt=attempt,
        producer_kind="worker",
        producer_id="worker-01",
    )
    second_worker = store.open_event_stream(
        attempt=attempt,
        producer_kind="worker",
        producer_id="worker-02",
    )
    first_worker.append(
        event_type="worker.step",
        data={"step": 1, "api_key": sentinel},
    )
    second_worker.append(
        event_type="worker.step",
        data={"step": 1, "message": "second worker"},
    )

    event_files = sorted(attempt.path.glob("workers/*/events-00001.jsonl"))
    assert len(event_files) == 2

    first_event = json.loads(event_files[0].read_text(encoding="utf-8"))
    second_event = json.loads(event_files[1].read_text(encoding="utf-8"))
    assert first_event["producer_id"] == "worker-01"
    assert second_event["producer_id"] == "worker-02"
    assert first_event["producer_sequence"] == 1
    assert second_event["producer_sequence"] == 1
    assert sentinel not in event_files[0].read_text(encoding="utf-8")
    assert first_event["data"]["api_key"] == "[REDACTED]"


def test_event_stream_makes_every_intermediate_directory_private(
    tmp_path: Path,
) -> None:
    """验证 producer 分类目录不会继承宽松 umask 权限。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；environment、evaluator、runtime 与 workers 分类目录及 producer
        目录都必须为 ``0700``。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-events-private",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-events-private",
        task_id="synthetic-private-directories",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "synthetic-private-directories",
            task_type="synthetic",
        ),
    )

    for producer_kind, producer_id in (
        ("environment", "task-environment"),
        ("evaluator", "task-evaluator"),
        ("runtime", "attempt-runner"),
        ("worker", "agent-system"),
    ):
        stream = store.open_event_stream(
            attempt=attempt,
            producer_kind=producer_kind,
            producer_id=producer_id,
        )
        assert stream.path.parent.stat().st_mode & 0o777 == 0o700
        assert stream.path.parent.parent.stat().st_mode & 0o777 == 0o700


def test_event_append_rejects_symlink_file_without_touching_target(
    tmp_path: Path,
) -> None:
    """验证事件 JSONL 追加不会跟随预置的符号链接。

    输入参数：
        tmp_path：pytest 提供的 RunStore 与外部诱饵文件根目录。
    输出返回值：
        无；追加必须失败关闭，外部文件字节保持不变。
    """

    outside = tmp_path / "outside.jsonl"
    outside.write_text("sentinel\n", encoding="utf-8")
    store = RunStore(tmp_path / "runs")
    store.start_run(
        run_id="run-event-link",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-event-link",
        task_id="synthetic-task",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit("synthetic-task"),
    )
    stream = store.open_event_stream(
        attempt=attempt,
        producer_kind="runtime",
        producer_id="attempt-runner",
    )
    stream.path.symlink_to(outside)

    with pytest.raises((OSError, ValueError), match="symlink|regular"):
        stream.append(event_type="attempt.started", data={})

    assert outside.read_text(encoding="utf-8") == "sentinel\n"
