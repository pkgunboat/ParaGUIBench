"""RunStore 任务级 Attempt 与默认脱敏行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from paraguibench.runstore import RunStore, RunStoreConflictError
from tests.runstore._audit import synthetic_task_audit


def test_task_attempt_is_scoped_and_secrets_never_reach_disk(tmp_path: Path) -> None:
    """验证任务 Attempt 独立落盘，并递归移除配置中的敏感值。

    输入参数：
        tmp_path：pytest 提供的临时目录，作为本次测试的 RunStore 根目录。
    输出返回值：
        无；通过公开 RunStore interface 与最终磁盘内容断言行为。
    """

    sentinel = "pb-secret-sentinel-never-persist"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-001",
        run_record={
            "test": True,
            "provider": {
                "model": "example-model",
                "api_key": sentinel,
                "headers": {"Authorization": f"Bearer {sentinel}"},
            },
        },
    )

    attempt = store.start_attempt(
        run_id="run-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001",
            task_type="InformationRetrieval",
        ),
    )

    expected_path = (
        tmp_path
        / "run-001"
        / "tasks"
        / "InformationRetrieval-FileSearch-Readonly-001"
        / "attempts"
        / "attempt-001"
    )
    assert attempt.path == expected_path
    assert (expected_path / "attempt.json").is_file()
    assert (expected_path.parent.parent / "task.json").is_file()

    persisted_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert sentinel not in persisted_text
    assert "[REDACTED]" in persisted_text


def test_task_attempt_redacts_provider_keys_and_url_credentials(
    tmp_path: Path,
) -> None:
    """验证 provider 前缀密钥和 URL 凭据不会进入任务快照。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；扫描所有持久化文本，确认 sentinel 消失且非敏感 URL 参数保留。
    """

    sentinel = "pb-url-secret-sentinel"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-002",
        run_record={
            "test": True,
            "OPENAI_API_KEY": sentinel,
            "client-secret": sentinel,
            "endpoint": (
                f"https://reader:{sentinel}@example.com/search"
                f"?api_key={sentinel}&view=compact"
            ),
        },
    )

    store.start_attempt(
        run_id="run-002",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )

    persisted_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    assert sentinel not in persisted_text
    assert "view=compact" in persisted_text
    assert "reader@" not in persisted_text


def test_task_snapshot_cannot_be_overwritten_by_later_attempt(
    tmp_path: Path,
) -> None:
    """验证同一 Run 中的任务定义不可被后续 Attempt 改写。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；第二个不同任务快照必须抛出 ``RunStoreConflictError``。
    """

    store = RunStore(tmp_path)
    store.start_run(run_id="run-003", run_record={"test": True})
    common_identity = {
        "run_id": "run-003",
        "task_id": "InformationRetrieval-FileSearch-Readonly-001",
    }
    store.start_attempt(
        **common_identity,
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001",
            task_tag="first-immutable-snapshot",
        ),
    )

    with pytest.raises(RunStoreConflictError):
        store.start_attempt(
            **common_identity,
            attempt_id="attempt-002",
            task_record=synthetic_task_audit(
                "InformationRetrieval-FileSearch-Readonly-001",
                task_tag="conflicting-snapshot",
            ),
        )


def test_attempt_identity_can_only_be_started_once(tmp_path: Path) -> None:
    """验证相同 attempt_id 不会被重复启动或覆盖。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；第二次启动相同身份必须抛出 ``RunStoreConflictError``。
    """

    store = RunStore(tmp_path)
    store.start_run(run_id="run-004", run_record={"test": True})
    start_arguments = {
        "run_id": "run-004",
        "task_id": "InformationRetrieval-FileSearch-Readonly-001",
        "attempt_id": "attempt-001",
        "task_record": synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    }

    store.start_attempt(**start_arguments)

    with pytest.raises(RunStoreConflictError):
        store.start_attempt(**start_arguments)
