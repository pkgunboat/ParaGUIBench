"""RunStore 不可变 Run manifest 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from paraguibench.runstore import RunStore
from tests.runstore._audit import synthetic_task_audit


def test_run_manifest_is_immutable_and_sanitized(tmp_path: Path) -> None:
    """验证 Run 身份与复现元数据默认脱敏并稳定落盘。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；通过公开 ``start_run`` interface 检查目录、manifest 和 sentinel
        隐私行为。
    """

    sentinel = "pb-run-config-secret"
    store = RunStore(tmp_path)

    run = store.start_run(
        run_id="run-manifest-001",
        run_record={
            "git_revision": "abc123",
            "benchmark_manifest_sha256": "0" * 64,
            "agent_system": "paragui",
            "configuration": {
                "model": "example-model",
                "provider_api_key": sentinel,
            },
        },
    )

    assert run.path == tmp_path / "run-manifest-001"
    manifest_text = (run.path / "run.json").read_text(encoding="utf-8")
    assert sentinel not in manifest_text
    assert "[REDACTED]" in manifest_text
    assert "abc123" in manifest_text


def test_process_environment_uses_allowlist_and_presence_markers(
    tmp_path: Path,
) -> None:
    """验证完整进程环境不会被序列化到 Run manifest。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；安全 locale 值保留，凭据只记录存在状态，PATH 与真实 secret
        值均不得落盘。
    """

    sentinel = "pb-environment-secret"
    store = RunStore(tmp_path)

    run = store.start_run(
        run_id="run-manifest-002",
        run_record={
            "process_environment": {
                "LANG": "en_US.UTF-8",
                "PATH": "/private/user-specific/bin",
                "OPENAI_API_KEY": sentinel,
            }
        },
    )

    manifest_text = (run.path / "run.json").read_text(encoding="utf-8")
    assert "en_US.UTF-8" in manifest_text
    assert "OPENAI_API_KEY" in manifest_text
    assert "[PRESENT]" in manifest_text
    assert sentinel not in manifest_text
    assert "/private/user-specific/bin" not in manifest_text
    assert '"PATH"' not in manifest_text


def test_attempt_requires_an_existing_run_manifest(tmp_path: Path) -> None:
    """验证调用方不能绕过 Run manifest 直接创建任务 Attempt。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；缺少 ``run.json`` 时公开 interface 必须抛出 ``ValueError``。
    """

    store = RunStore(tmp_path)

    with pytest.raises(ValueError, match="run manifest"):
        store.start_attempt(
            run_id="run-without-manifest",
            task_id="InformationRetrieval-FileSearch-Readonly-001",
            attempt_id="attempt-001",
            task_record=synthetic_task_audit(
                "InformationRetrieval-FileSearch-Readonly-001"
            ),
        )
