"""RunStore 不可变 Run manifest 测试。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from paraguibench.runstore import RunStore, RunVersionVector
from tests.runstore._audit import synthetic_task_audit
from tests.runstore._audit import synthetic_run_version_vector


def test_run_manifest_persists_complete_version_vector(
    tmp_path: Path,
) -> None:
    """验证新 Run 在顶层保存完整且独立于自由配置的版本向量。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；通过公开 ``start_run`` interface 断言源码、Agent、评价器、
        评价协议、环境协议与环境 manifest revision 均不可缺失。
    """

    version_vector = RunVersionVector(
        source_revision="tree-sha256:" + "a" * 64,
        agent_code_revision="tree-sha256:" + "b" * 64,
        evaluator_revision="tree-sha256:" + "c" * 64,
        evaluation_protocol="paraguibench.answer.exact.v1",
        environment_protocol="osworld.chrome.v1",
        environment_revision="manifest-sha256:" + "d" * 64,
    )

    run = RunStore(tmp_path).start_run(
        run_id="run-versioned-001",
        run_record={"agent_system": "synthetic"},
        version_vector=version_vector,
    )

    manifest = json.loads((run.path / "run.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "2.0"
    assert manifest["version_vector"] == {
        "source_revision": "tree-sha256:" + "a" * 64,
        "agent_code_revision": "tree-sha256:" + "b" * 64,
        "evaluator_revision": "tree-sha256:" + "c" * 64,
        "evaluation_protocol": "paraguibench.answer.exact.v1",
        "environment_protocol": "osworld.chrome.v1",
        "environment_revision": "manifest-sha256:" + "d" * 64,
    }


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("source_revision", "HEAD"),
        ("agent_code_revision", "latest"),
        ("evaluator_revision", "unknown"),
        ("evaluation_protocol", "unknown"),
        ("evaluation_protocol", "paraguibench.answer.latest"),
        ("evaluation_protocol", "paraguibench.answer.unknown"),
        ("evaluation_protocol", "paraguibench.answer.v0"),
        ("evaluation_protocol", "paraguibench.answer.V1"),
        ("environment_protocol", ""),
        ("environment_protocol", "osworld.desktop.latest"),
        ("environment_revision", "manifest-sha256:1234"),
    ],
)
def test_run_version_vector_rejects_floating_or_incomplete_identity(
    tmp_path: Path,
    field_name: str,
    invalid_value: str,
) -> None:
    """验证占位、浮动或不完整版本在创建 Run 目录前失败。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
        field_name：本用例替换的版本向量字段。
        invalid_value：不得进入持久化层的占位或不完整值。
    输出返回值：
        无；公开 ``start_run`` 抛出不回显原值的 ``ValueError``，且目标
        ``run_id`` 目录不存在。
    """

    vector = replace(
        synthetic_run_version_vector(),
        **{field_name: invalid_value},
    )
    store = RunStore(tmp_path)

    with pytest.raises(ValueError, match="version_vector"):
        store.start_run(
            run_id="run-invalid-version",
            run_record={"test": True},
            version_vector=vector,
        )

    assert not (tmp_path / "run-invalid-version").exists()


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
        version_vector=synthetic_run_version_vector(),
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
        version_vector=synthetic_run_version_vector(),
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


@pytest.mark.parametrize(
    "invalid_manifest",
    [
        {},
        {
            "schema_version": "1.0",
            "run_id": "run-invalid-parent",
        },
        {
            "schema_version": "2.0",
            "run_id": "another-run",
            "version_vector": {},
        },
        {
            "schema_version": "2.0",
            "run_id": "run-invalid-parent",
            "version_vector": {
                "source_revision": "tree-sha256:" + "a" * 64,
            },
        },
    ],
)
def test_attempt_rejects_unversioned_or_mismatched_parent_run(
    tmp_path: Path,
    invalid_manifest: dict[str, object],
) -> None:
    """验证新 Attempt 只能挂到身份一致且版本向量完整的 schema 2.0 Run。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
        invalid_manifest：缺少版本、身份错配或版本向量残缺的父 Run 记录。
    输出返回值：
        无；公开 ``start_attempt`` 必须在创建 task/attempt 目录前失败。
    """

    store = RunStore(tmp_path)
    run_path = tmp_path / "run-invalid-parent"
    run_path.mkdir()
    (run_path / "run.json").write_text(
        json.dumps(invalid_manifest),
        encoding="utf-8",
    )

    with pytest.raises((TypeError, ValueError), match="run manifest"):
        store.start_attempt(
            run_id="run-invalid-parent",
            task_id="synthetic-task",
            attempt_id="attempt-001",
            task_record=synthetic_task_audit("synthetic-task"),
        )

    assert not (run_path / "tasks").exists()
