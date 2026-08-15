"""RunStore 任务 Attempt 级 artifact 持久化行为测试。"""

from __future__ import annotations

import hashlib
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from paraguibench.runstore import RunStore, RunStoreConflictError
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


def test_json_artifact_is_sanitized_and_described_by_manifest(
    tmp_path: Path,
) -> None:
    """验证结构化 artifact 被脱敏归档并生成可校验的 manifest。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；通过公开 RunStore 接口与持久化结果断言正文路径、脱敏状态
        和 manifest 摘要一致。
    """

    sentinel = "artifact-secret-must-not-reach-disk"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )
    payload = {
        "answer": "The task completed.",
        "provider": {"api_key": sentinel},
    }

    artifact = store.write_artifact(
        attempt=attempt,
        logical_name="evaluator-result",
        relative_path="evaluation/result.json",
        content=payload,
        media_type="application/json",
    )

    expected_path = attempt.path / "artifacts" / "evaluation" / "result.json"
    assert artifact.path == expected_path
    persisted_bytes = expected_path.read_bytes()
    assert json.loads(persisted_bytes) == {
        "answer": "The task completed.",
        "provider": {"api_key": "[REDACTED]"},
    }
    assert payload["provider"]["api_key"] == sentinel

    manifest = json.loads(
        (attempt.path / "artifacts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifacts"] == [
        {
            "logical_name": "evaluator-result",
            "media_type": "application/json",
            "relative_path": "evaluation/result.json",
            "sha256": hashlib.sha256(persisted_bytes).hexdigest(),
            "byte_count": len(persisted_bytes),
        }
    ]


def test_multiple_artifacts_are_preserved_in_one_attempt_manifest(
    tmp_path: Path,
) -> None:
    """验证同一 Attempt 的多个不同 artifact 均被保留在 manifest 中。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；连续写入两个不同 artifact 后，两个正文文件和两条
        manifest 记录必须同时存在。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-002",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-002",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )

    store.write_artifact(
        attempt=attempt,
        logical_name="agent-answer",
        relative_path="agent/answer.json",
        content={"answer": "A concise answer."},
        media_type="application/json",
    )
    store.write_artifact(
        attempt=attempt,
        logical_name="evaluator-result",
        relative_path="evaluation/result.json",
        content={"score": 1.0},
        media_type="application/json",
    )

    assert (attempt.path / "artifacts" / "agent" / "answer.json").is_file()
    assert (attempt.path / "artifacts" / "evaluation" / "result.json").is_file()
    manifest = json.loads(
        (attempt.path / "artifacts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert [entry["logical_name"] for entry in manifest["artifacts"]] == [
        "agent-answer",
        "evaluator-result",
    ]


def test_artifact_relative_path_cannot_traverse_outside_artifact_directory(
    tmp_path: Path,
) -> None:
    """验证包含父目录跳转的 artifact 路径在任何写入前被拒绝。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；路径验证失败时抛出 ``ValueError``，Attempt 根目录和
        artifacts 目录均不得出现正文或 manifest 副作用。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-path-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-path-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )

    with pytest.raises(ValueError, match="relative_path|traversal"):
        store.write_artifact(
            attempt=attempt,
            logical_name="escaped-result",
            relative_path="../escaped.json",
            content={"result": "must not be written"},
            media_type="application/json",
        )

    assert not (attempt.path / "escaped.json").exists()
    assert not (attempt.path / "artifacts" / "manifest.json").exists()


def test_artifact_logical_name_cannot_be_rebound_to_another_path(
    tmp_path: Path,
) -> None:
    """验证已登记的 artifact 逻辑名不能被另一正文路径复用。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；第二次使用相同逻辑名时抛出
        ``RunStoreConflictError``，且冲突 artifact 正文不得落盘。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-conflict-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-conflict-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )
    store.write_artifact(
        attempt=attempt,
        logical_name="agent-answer",
        relative_path="agent/answer.json",
        content={"answer": "First immutable answer."},
        media_type="application/json",
    )

    with pytest.raises(RunStoreConflictError):
        store.write_artifact(
            attempt=attempt,
            logical_name="agent-answer",
            relative_path="agent/rebound.json",
            content={"answer": "Conflicting answer."},
            media_type="application/json",
        )

    assert not (attempt.path / "artifacts" / "agent" / "rebound.json").exists()


def test_concurrent_artifact_logical_name_conflict_has_one_winner(
    tmp_path: Path,
) -> None:
    """验证并发争用同一 artifact 逻辑名时只提交一个获胜者。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；八个并发 writer 中仅一个成功，其余明确冲突，manifest 和
        正文文件均只对应唯一获胜者。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-race-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-race-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )
    writer_count = 8
    barrier = Barrier(writer_count)

    def write_candidate(candidate_id: int) -> str:
        """同步起跑并尝试提交一个共享逻辑名的候选 artifact。

        输入参数：
            candidate_id：候选 writer 的稳定整数编号，同时用于唯一
                正文路径。
        输出返回值：
            提交成功返回 ``success``；稳定身份冲突返回 ``conflict``。
        """

        barrier.wait()
        try:
            store.write_artifact(
                attempt=attempt,
                logical_name="shared-result",
                relative_path=f"workers/candidate-{candidate_id}.json",
                content={"candidate_id": candidate_id},
                media_type="application/json",
            )
        except RunStoreConflictError:
            return "conflict"
        return "success"

    with ThreadPoolExecutor(max_workers=writer_count) as executor:
        results = list(executor.map(write_candidate, range(writer_count)))

    assert results.count("success") == 1
    assert results.count("conflict") == writer_count - 1
    persisted_candidates = list(
        (attempt.path / "artifacts" / "workers").glob("candidate-*.json")
    )
    assert len(persisted_candidates) == 1
    manifest = json.loads(
        (attempt.path / "artifacts" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["artifacts"]) == 1
    assert (
        manifest["artifacts"][0]["relative_path"]
        == f"workers/{persisted_candidates[0].name}"
    )


def test_artifact_manifest_symlink_is_rejected_before_content_write(
    tmp_path: Path,
) -> None:
    """验证外部 manifest symlink 不会被跟随或替换。

    输入参数：
        tmp_path：pytest 提供的临时目录，同时承载 RunStore 和外部
            诱饵文件。
    输出返回值：
        无；manifest 路径为 symlink 时抛出 ``ValueError``，外部文件
        与预期 artifact 正文均保持不变。
    """

    root = tmp_path / "runs"
    outside_manifest = tmp_path / "outside-manifest.json"
    outside_payload = '{"schema_version":"1.0","artifacts":[]}\n'
    outside_manifest.write_text(outside_payload, encoding="utf-8")

    store = RunStore(root)
    store.start_run(
        run_id="run-artifact-symlink-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-symlink-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )
    artifact_root = attempt.path / "artifacts"
    artifact_root.mkdir(mode=0o700)
    (artifact_root / "manifest.json").symlink_to(outside_manifest)

    with pytest.raises(ValueError, match="symlink"):
        store.write_artifact(
            attempt=attempt,
            logical_name="agent-answer",
            relative_path="agent/answer.json",
            content={"answer": "Must not be written."},
            media_type="application/json",
        )

    assert outside_manifest.read_text(encoding="utf-8") == outside_payload
    assert not (artifact_root / "agent" / "answer.json").exists()
    assert (artifact_root / "manifest.json").is_symlink()


def test_artifact_cannot_claim_reserved_manifest_path(
    tmp_path: Path,
) -> None:
    """验证调用方正文不能占用 RunStore 的内部 manifest 路径。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；保留路径在任何正文写入前抛出 ``ValueError``，不会生成
        一个同时充当正文和 manifest 的歧义文件。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-reserved-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-reserved-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )

    with pytest.raises(ValueError, match="reserved"):
        store.write_artifact(
            attempt=attempt,
            logical_name="ambiguous-manifest",
            relative_path="manifest.json",
            content={"answer": "Must not replace metadata."},
            media_type="application/json",
        )

    assert not (attempt.path / "artifacts" / "manifest.json").exists()


def test_structured_artifact_rejects_non_json_media_type(
    tmp_path: Path,
) -> None:
    """验证结构化 JSON writer 不会在 manifest 中登记虚假媒体类型。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；非 ``application/json`` 类型在任何 artifact 写入前抛出
        ``ValueError``。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-media-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-media-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )

    with pytest.raises(ValueError, match="application/json"):
        store.write_artifact(
            attempt=attempt,
            logical_name="fake-screenshot",
            relative_path="screenshots/fake.png",
            content={"pixels": "not actually a PNG"},
            media_type="image/png",
        )

    assert not (attempt.path / "artifacts").exists()


def test_artifact_directories_and_files_use_private_permissions(
    tmp_path: Path,
) -> None:
    """验证 artifact 目录和所有持久化文件使用最小私有权限。

    输入参数：
        tmp_path：pytest 提供的临时 RunStore 根目录。
    输出返回值：
        无；artifact 根目录与正文父目录必须为 ``0700``，正文、manifest
        和锁文件必须为 ``0600``。
    """

    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-artifact-mode-001",
        run_record={"test": True},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-artifact-mode-001",
        task_id="InformationRetrieval-FileSearch-Readonly-001",
        attempt_id="attempt-001",
        task_record=synthetic_task_audit(
            "InformationRetrieval-FileSearch-Readonly-001"
        ),
    )
    artifact = store.write_artifact(
        attempt=attempt,
        logical_name="agent-answer",
        relative_path="agent/answer.json",
        content={"answer": "Permission-safe answer."},
        media_type="application/json",
    )
    artifact_root = attempt.path / "artifacts"

    assert stat.S_IMODE(artifact_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifact.path.parent.stat().st_mode) == 0o700
    for private_file in (
        artifact.path,
        artifact_root / "manifest.json",
        artifact_root / ".manifest.lock",
    ):
        assert stat.S_IMODE(private_file.stat().st_mode) == 0o600
