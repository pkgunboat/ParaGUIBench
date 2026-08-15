"""Word-012 task-specific typed 语义合同 runtime 纵向测试。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
from typing import Any

import pytest

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.operation import (
    OPERATION_PROTOCOL_ID,
    OperationEvaluationError,
    WordAbbreviationBaseline,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OperationArtifactSnapshot,
)
from paraguibench.runstore import (
    EvaluationOutcome,
    ExecutionOutcome,
    RunStore,
)
from paraguibench.runtime.attempt_runner import AttemptRunner
from paraguibench.runtime.evaluators import (
    OperationTaskEvaluator,
    build_task_evaluator,
)
from paraguibench.runtime.osworld_environment import (
    OSWorldEnvironmentError,
    OSWorldTaskEnvironment,
)
import paraguibench.runtime.osworld_environment as osworld_environment_module
from tests.evaluation.test_operation_word012_abbreviation_semantics import (
    _EXPECTED_TEXT,
    _SOURCE_TEXT,
    _baseline,
    _write_documents,
)
from tests.runtime.test_osworld_environment import _Controller, _DockerSession
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_ID = "Operation-FileOperate-BatchOperationWord-012"


def _task() -> dict[str, object]:
    """读取题面未改的 canonical Word-012 任务。

    输入参数：无。
    输出返回值：含完整 eval-rules 的 trusted task 映射。
    """

    path = _REPO_ROOT / "benchmark/tasks" / f"{_TASK_ID}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class _MissingBaselineSpyEnvironment:
    """在顺序错误时记录 post snapshot 读取的对抗环境。"""

    def __init__(self) -> None:
        """初始化 post 快照调用计数。

        输入参数：无。
        输出返回值：无。
        """

        self.snapshot_calls = 0

    def operation_artifact_snapshot(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """记录本不应发生的 post 快照读取。

        输入参数：
            task_id/protocol_id：runtime evaluator 固定身份。
        输出返回值：
            无有效返回；若被调用立即暴露顺序漏洞。
        """

        assert task_id == _TASK_ID
        assert protocol_id == OPERATION_PROTOCOL_ID
        self.snapshot_calls += 1
        raise AssertionError("baseline 失效前不得读取 post snapshot")


class _TypedSnapshotEnvironment:
    """返回 prepare 前 DTO 与 Agent 后冻结快照的最小环境。"""

    def __init__(
        self,
        baseline: WordAbbreviationBaseline,
        snapshot: OperationArtifactSnapshot,
    ) -> None:
        """绑定两个独立时点的 typed 证据。

        输入参数：
            baseline：prepare 前的不可变 DTO；snapshot：post owned 快照。
        输出返回值：无；并初始化两类读取计数。
        """

        self.baseline = baseline
        self.snapshot = snapshot
        self.baseline_calls = 0
        self.snapshot_calls = 0

    def start(self) -> None:
        """启动无外部资源的合成环境。

        输入参数：无。
        输出返回值：无。
        """

    def prepare(self, task: dict[str, Any]) -> None:
        """验证 AttemptRunner 传入的 trusted task 身份。

        输入参数：
            task：完整 canonical Word-012 任务。
        输出返回值：无；身份漂移由断言暴露。
        """

        assert task["task_id"] == _TASK_ID

    def close(self) -> None:
        """清理当前 Attempt 拥有的 post 快照。

        输入参数：无。
        输出返回值：无；快照自身提供幂等清理。
        """

        self.snapshot.close()

    def operation_word_abbreviation_baseline(
        self,
        task_id: str,
        protocol_id: str,
    ) -> WordAbbreviationBaseline:
        """返回同一 pre DTO 并记录调用。

        输入参数：
            task_id/protocol_id：evaluator 固定的 Word-012 与 Operation 身份。
        输出返回值：构造阶段注入的同一 baseline。
        """

        assert task_id == _TASK_ID
        assert protocol_id == OPERATION_PROTOCOL_ID
        self.baseline_calls += 1
        return self.baseline

    def operation_artifact_snapshot(
        self,
        task_id: str,
        protocol_id: str,
    ) -> OperationArtifactSnapshot:
        """返回同一 post owned 快照并记录调用。

        输入参数：
            task_id/protocol_id：evaluator 固定身份。
        输出返回值：构造阶段注入的 snapshot。
        """

        assert task_id == _TASK_ID
        assert protocol_id == OPERATION_PROTOCOL_ID
        self.snapshot_calls += 1
        return self.snapshot


class _ClaimingAgent:
    """返回不得参与评价或持久化的 Agent 终端文本。"""

    def __init__(self, final_output: str) -> None:
        """保存对抗 final text。

        输入参数：
            final_output：可包含伪完成声明或敏感哨兵的文本。
        输出返回值：无。
        """

        self.final_output = final_output

    def run(
        self,
        task_view: dict[str, Any],
        environment: object,
    ) -> AgentRunResult:
        """返回一步结束的合法 Agent 结果。

        输入参数：
            task_view：不含 evaluator 私有字段的 Agent 投影；
            environment：当前存活环境，本 Agent 不读取。
        输出返回值：
            包含哨兵 final text 的 ``AgentRunResult``。
        """

        del environment
        assert task_view["task_id"] == _TASK_ID
        assert "eval_rules" not in task_view
        return AgentRunResult(
            final_output=self.final_output,
            step_count=1,
            termination="finished",
        )


def _prepared_task() -> PreparedTask:
    """构造 Word-012 trusted/Agent/audit 三投影任务。

    输入参数：无。
    输出返回值：
        evaluator 可见完整规则、Agent 仅见题面的 ``PreparedTask``。
    """

    task = _task()
    return PreparedTask(
        trusted_task=task,
        agent_task={
            "task_id": task["task_id"],
            "instruction": task["instruction"],
        },
        audit_metadata=synthetic_task_audit(
            str(task["task_id"]),
            task_uid=str(task["task_uid"]),
            task_type=str(task["task_type"]),
            task_source=str(task["task_source"]),
            task_tag=str(task["task_tag"]),
        ),
    )


def _start_attempt(store: RunStore, prepared: PreparedTask):
    """在合成 RunStore 中建立 Word-012 Attempt。

    输入参数：
        store：空测试 RunStore；prepared：Word-012 三投影任务。
    输出返回值：
        已登记且可交给 AttemptRunner 的 ``TaskAttempt``。
    """

    store.start_run(
        run_id="run-word012-semantics",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    return store.start_attempt(
        run_id="run-word012-semantics",
        task_id=_TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared.audit_metadata,
    )


def _snapshot(texts: dict[str, str]) -> OperationArtifactSnapshot:
    """构造四路径闭集完整的 Word-012 post 快照。

    输入参数：
        texts：固定文件名到单段正文的映射。
    输出返回值：
        文件数为 4 的 owned ``OperationArtifactSnapshot``。
    """

    temporary = tempfile.TemporaryDirectory(prefix="paraguibench-word012-attempt-")
    _write_documents(Path(temporary.name), texts)
    return OperationArtifactSnapshot(
        task_id=_TASK_ID,
        protocol_id=OPERATION_PROTOCOL_ID,
        file_count=4,
        temporary_directory=temporary,
    )


def test_word012_missing_baseline_stops_before_post_snapshot() -> None:
    """验证缺 typed seam 在任何 post 捕获前映射为 ERROR。

    输入参数：无；使用只实现 post 捕获的对抗环境。
    输出返回值：
        无；必须抛出脱敏类型错误，且 post snapshot 调用数为零。
    """

    environment = _MissingBaselineSpyEnvironment()
    evaluator = OperationTaskEvaluator(
        task_id=_TASK_ID,
        evaluation_protocol=OPERATION_PROTOCOL_ID,
    )

    with pytest.raises(TypeError, match="abbreviation typed baseline"):
        evaluator.evaluate(
            _task(),
            "AGENT FINAL TEXT MUST NOT BE EVIDENCE",
            environment,
        )

    assert environment.snapshot_calls == 0


def test_word012_runtime_uses_typed_artifacts_and_ignores_final_text(
    tmp_path: Path,
) -> None:
    """验证 runtime 端到端使用 pre/post typed 证据而不读 final text。

    输入参数：
        tmp_path：构造四份 pre 正文的隔离根。
    输出返回值：
        无；相同 artifact 在两个相反 Agent 声明下结果全等且满分。
    """

    source_root = tmp_path / "pre"
    source_root.mkdir()
    _write_documents(source_root, _SOURCE_TEXT)
    temporary = tempfile.TemporaryDirectory(prefix="paraguibench-word012-post-")
    result_root = Path(temporary.name).resolve(strict=True)
    _write_documents(result_root, _EXPECTED_TEXT)
    snapshot = OperationArtifactSnapshot(
        task_id=_TASK_ID,
        protocol_id=OPERATION_PROTOCOL_ID,
        file_count=4,
        temporary_directory=temporary,
    )
    environment = _TypedSnapshotEnvironment(_baseline(source_root), snapshot)
    evaluator = OperationTaskEvaluator(
        task_id=_TASK_ID,
        evaluation_protocol=OPERATION_PROTOCOL_ID,
    )
    try:
        first = evaluator.evaluate(_task(), "I FAILED", environment)
        second = evaluator.evaluate(
            _task(),
            "I PASSED AND SECRET MAPPING IS WHATEVER",
            environment,
        )
    finally:
        snapshot.close()

    assert first == second
    assert first.passed is True
    assert first.score == 1.0
    assert "SECRET" not in repr(first.details)
    assert environment.baseline_calls == 2
    assert environment.snapshot_calls == 2


def test_word012_forged_baseline_is_rejected_before_post_snapshot(
    tmp_path: Path,
) -> None:
    """验证错 task 的伪 DTO 在 post I/O 前映射为固定 ERROR。

    输入参数：
        tmp_path：构造有效 baseline 后仅漂移其 task 身份。
    输出返回值：
        无；snapshot 读取计数必须为零，异常不回显 DTO 细节。
    """

    source_root = tmp_path / "pre"
    source_root.mkdir()
    _write_documents(source_root, _SOURCE_TEXT)
    temporary = tempfile.TemporaryDirectory(prefix="paraguibench-word012-unused-")
    snapshot = OperationArtifactSnapshot(
        task_id=_TASK_ID,
        protocol_id=OPERATION_PROTOCOL_ID,
        file_count=0,
        temporary_directory=temporary,
    )
    environment = _TypedSnapshotEnvironment(
        replace(_baseline(source_root), task_id="forged-task"),
        snapshot,
    )
    evaluator = OperationTaskEvaluator(
        task_id=_TASK_ID,
        evaluation_protocol=OPERATION_PROTOCOL_ID,
    )
    try:
        with pytest.raises(
            OperationEvaluationError,
            match="WORD_ABBREVIATION_SEMANTICS_INVALID",
        ) as captured:
            evaluator.evaluate(_task(), "IGNORED", environment)
    finally:
        snapshot.close()

    assert "forged" not in str(captured.value)
    assert environment.baseline_calls == 1
    assert environment.snapshot_calls == 0


def _word012_prepare_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, object]]:
    """写入可由 OSWorld prepare 解析的四文档合成资产。

    输入参数：
        tmp_path：pytest 隔离根。
    输出返回值：
        ``(repo_root, cache_root, cache_directory, task)``，manifest 与
        host cache 的路径、size 与 SHA 完全一致。
    """

    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark/assets/manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    cache_directory = cache_root / _TASK_ID
    cache_directory.mkdir(parents=True)
    _write_documents(cache_directory, _SOURCE_TEXT)
    entries = []
    for name in _SOURCE_TEXT:
        payload = (cache_directory / name).read_bytes()
        entries.append(
            {
                "path": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "media_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
            }
        )
    manifest_reference = f"benchmark/assets/manifests/{_TASK_ID}.json"
    manifest_path = repo_root / manifest_reference
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": _TASK_ID,
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/word012-assets",
                    "revision": "d" * 40,
                    "base_path": "dataset/task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": entries,
            }
        ),
        encoding="utf-8",
    )
    return (
        repo_root,
        cache_root,
        cache_directory,
        {
            "task_id": _TASK_ID,
            "task_tag": "FileOperate",
            "asset_manifest": manifest_reference,
        },
    )


def test_word012_prepare_captures_baseline_before_first_guest_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证固定 input 身份与语义 baseline 在首次 guest I/O 前完成。

    输入参数：
        tmp_path：合成 repo/cache；monkeypatch：仅替换正式合同身份
        为同构清单，保留 production prepare 时序。
    输出返回值：
        无；capture 时 desktop 与 upload 均未访问，prepare 后 getter
        返回同一 typed DTO。
    """

    repo_root, cache_root, cache_directory, task = _word012_prepare_fixture(tmp_path)
    manifest_reference = str(task["asset_manifest"])
    manifest_payload = (repo_root / manifest_reference).read_bytes()
    manifest = json.loads(manifest_payload)
    contract = SimpleNamespace(
        manifest_reference=manifest_reference,
        manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
        files=tuple(
            SimpleNamespace(
                path=entry["path"],
                size=entry["size"],
                sha256=entry["sha256"],
            )
            for entry in manifest["files"]
        ),
    )
    monkeypatch.setattr(
        osworld_environment_module,
        "operation_word_abbreviation_input_contract",
        lambda task_id: contract,
        raising=False,
    )
    calls: list[str] = []
    controller = _Controller(calls)
    expected_baseline = _baseline(cache_directory.resolve(strict=True))

    def _capture(**kwargs: Any) -> WordAbbreviationBaseline:
        """记录 production capture 时点并返回合法 typed DTO。

        输入参数：
            kwargs：environment 传入的 task/protocol/manifest/source 构造参数。
        输出返回值：
            已在测试 pre 根构造的同一 ``WordAbbreviationBaseline``。
        """

        assert kwargs["task_id"] == _TASK_ID
        assert controller.desktop_path_calls == 0
        assert controller.files == {}
        calls.append("word-abbreviation.capture")
        return expected_baseline

    monkeypatch.setattr(
        osworld_environment_module,
        "capture_word_abbreviation_baseline",
        _capture,
        raising=False,
    )
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
    )

    environment.start()
    environment.prepare(task)
    baseline = environment.operation_word_abbreviation_baseline(
        _TASK_ID,
        OPERATION_PROTOCOL_ID,
    )

    assert baseline is expected_baseline
    assert calls.index("word-abbreviation.capture") < calls.index("controller.upload")
    environment.close()


def test_word012_nonformal_manifest_is_rejected_before_guest_access(
    tmp_path: Path,
) -> None:
    """验证 schema 有效但非正式 Word-012 manifest 不能进入 guest。

    输入参数：
        tmp_path：合成 repo/cache 根；其 manifest 与 cache 内部一致，
        但不命中 production 整文件 SHA/size 身份。
    输出返回值：
        无：prepare 只抛固定环境错误，desktop/upload/execute 全部为零。
    """

    repo_root, cache_root, _cache_directory, task = _word012_prepare_fixture(tmp_path)
    calls: list[str] = []
    controller = _Controller(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
    )
    environment.start()

    with pytest.raises(
        OSWorldEnvironmentError,
        match="Operation Word abbreviation typed baseline 构造失败",
    ) as captured:
        environment.prepare(task)

    assert "Clinical Procedure" not in str(captured.value)
    assert controller.desktop_path_calls == 0
    assert controller.files == {}
    assert "controller.upload" not in calls
    environment.close()


def test_word012_semantic_failure_persists_only_fixed_counts(
    tmp_path: Path,
) -> None:
    """验证可比较的错释义以固定 FAIL/0 脱敏落盘。

    输入参数：
        tmp_path：RunStore 与 pre source 的隔离根。
    输出返回值：
        无；RunStore 仅保留协议、rule ID、固定 reason 和计数，
        不含 final text、文件名、正文、释义或 baseline repr。
    """

    source_root = tmp_path / "pre"
    source_root.mkdir()
    _write_documents(source_root, _SOURCE_TEXT)
    wrong = dict(_EXPECTED_TEXT)
    private_wrong_expansion = "PRIVATE WRONG EXPANSION SENTINEL"
    wrong["Clinical Procedure.docx"] = wrong["Clinical Procedure.docx"].replace(
        "MAC (Macintosh)\u00a0tablet",
        f"MAC ({private_wrong_expansion})\u00a0tablet",
    )
    snapshot = _snapshot(wrong)
    environment = _TypedSnapshotEnvironment(_baseline(source_root), snapshot)
    prepared = _prepared_task()
    store = RunStore(tmp_path / "runstore")
    attempt = _start_attempt(store, prepared)
    evaluator = build_task_evaluator(
        prepared.trusted_task,
        evaluation_protocol=OPERATION_PROTOCOL_ID,
    )
    final_sentinel = "PRIVATE WORD012 AGENT FINAL SENTINEL"

    result = AttemptRunner(store).run(
        attempt=attempt,
        prepared_task=prepared,
        environment=environment,
        agent=_ClaimingAgent(final_sentinel),
        evaluator=evaluator,
    )

    assert result.evaluation_outcome is EvaluationOutcome.FAILED
    assert result.score == 0.0
    persisted = b"\n".join(
        path.read_bytes()
        for path in (tmp_path / "runstore").rglob("*")
        if path.is_file()
    )
    assert b"ABBREVIATION_SEMANTICS_MISMATCH" in persisted
    for forbidden in (
        final_sentinel.encode("utf-8"),
        private_wrong_expansion.encode("utf-8"),
        b"Clinical Procedure.docx",
        b"Minimum Alveolar Concentration",
        b"Media Access Control",
        b"Message Authentication Code",
        b"WordAbbreviationBaseline",
    ):
        assert forbidden not in persisted


def test_word012_baseline_error_is_null_and_persists_only_exception_type(
    tmp_path: Path,
) -> None:
    """验证伪 baseline 经 AttemptRunner 只落盘 ERROR/null 异常类型。

    输入参数：
        tmp_path：RunStore、pre 和未使用 post 快照的隔离根。
    输出返回值：
        无：score 必须为 null，post snapshot 读取为零，落盘不含
        固定错误码、DTO repr、语义映射或 Agent final text。
    """

    source_root = tmp_path / "pre"
    source_root.mkdir()
    _write_documents(source_root, _SOURCE_TEXT)
    forged = replace(_baseline(source_root), manifest_sha256="f" * 64)
    snapshot = _snapshot(_EXPECTED_TEXT)
    environment = _TypedSnapshotEnvironment(forged, snapshot)
    prepared = _prepared_task()
    store = RunStore(tmp_path / "runstore")
    attempt = _start_attempt(store, prepared)
    evaluator = build_task_evaluator(
        prepared.trusted_task,
        evaluation_protocol=OPERATION_PROTOCOL_ID,
    )
    final_sentinel = "PRIVATE WORD012 ERROR FINAL"

    with pytest.raises(OperationEvaluationError):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=prepared,
            environment=environment,
            agent=_ClaimingAgent(final_sentinel),
            evaluator=evaluator,
        )

    assert environment.snapshot_calls == 0
    inspection = store.inspect_attempt(
        run_id="run-word012-semantics",
        task_id=_TASK_ID,
        attempt_id="attempt-001",
    )
    assert inspection.execution_outcome is ExecutionOutcome.SUCCEEDED
    assert inspection.evaluation_outcome is EvaluationOutcome.ERROR
    assert inspection.score is None
    persisted = b"\n".join(
        path.read_bytes()
        for path in (tmp_path / "runstore").rglob("*")
        if path.is_file()
    )
    assert b"OperationEvaluationError" in persisted
    for forbidden in (
        b"WORD_ABBREVIATION_SEMANTICS_INVALID",
        b"WordAbbreviationBaseline",
        final_sentinel.encode("utf-8"),
        b"Clinical Procedure.docx",
        b"Media Access Control",
    ):
        assert forbidden not in persisted
