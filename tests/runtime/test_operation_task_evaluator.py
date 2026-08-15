"""32-task Operation runtime adapter 闭集与证据完整性测试。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any

import pytest

from paraguibench.evaluation.operation import (
    OPERATION_PROTOCOL_ID,
    OPERATION_TASK_RULES,
)
from paraguibench.integrations.osworld.operation_artifacts import (
    OperationArtifactCaptureError,
    OperationArtifactSnapshot,
)
from paraguibench.runtime.evaluators import (
    OperationTaskEvaluator,
    UnsupportedTaskEvaluatorError,
    build_task_evaluator,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK_ID = "Operation-FileOperate-CombinationDocs-005"


class _SnapshotEnvironment:
    """为 runtime adapter 返回已冻结 Operation 快照的最小环境。"""

    def __init__(self, snapshot: OperationArtifactSnapshot) -> None:
        """保存唯一快照及读取身份记录。

        输入参数：
            snapshot：当前合成 Attempt 拥有的 host 快照。
        输出返回值：
            无；不复制或解析文件。
        """

        self.snapshot = snapshot
        self.calls: list[tuple[str, str]] = []

    def operation_artifact_snapshot(
        self,
        task_id: str,
        protocol_id: str,
    ) -> OperationArtifactSnapshot:
        """返回同一快照并记录 task/protocol 身份。

        输入参数：
            task_id/protocol_id：runtime adapter 构造时固定的身份。
        输出返回值：
            构造阶段注入的同一 ``OperationArtifactSnapshot``。
        """

        self.calls.append((task_id, protocol_id))
        return self.snapshot


def _load_task(task_id: str) -> dict[str, Any]:
    """从仓库 canonical JSON 读取一个 Operation 任务。

    输入参数：
        task_id：必须对应 ``benchmark/tasks/<task_id>.json``。
    输出返回值：
        含完整 eval_rules 的可变测试字典。
    """

    return json.loads(
        (_REPO_ROOT / "benchmark/tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )


def _snapshot_with_named_pdfs(
    *,
    omit_first: bool = False,
) -> tuple[
    OperationArtifactSnapshot,
    tempfile.TemporaryDirectory[str],
]:
    """创建 CombinationDocs-005 五个预期 PDF 的合成快照。

    输入参数：
        omit_first：是否在完整 manifest 模型中明确缺少第一个预期 PDF。
    输出返回值：
        file_count 与实际写入文件数一致的快照，以及其
        owned 临时目录句柄。
    """

    task = _load_task(_TASK_ID)
    temporary_directory = tempfile.TemporaryDirectory(
        prefix="paraguibench-operation-evaluator-test-"
    )
    root = Path(temporary_directory.name)
    names = tuple(task["eval_rules"][0]["params"]["filenames"])
    selected_names = names[1:] if omit_first else names
    for name in selected_names:
        (root / name).write_bytes(b"%PDF-1.7\nsynthetic\n")
    return (
        OperationArtifactSnapshot(
            task_id=_TASK_ID,
            protocol_id=OPERATION_PROTOCOL_ID,
            file_count=len(selected_names),
            temporary_directory=temporary_directory,
        ),
        temporary_directory,
    )


def test_runtime_registry_binds_exactly_all_32_operation_tasks() -> None:
    """验证 runtime registry 只对固定 32 个 canonical 任务建立 adapter。

    输入参数：无；读取规则目录对应的 canonical task JSON。
    输出返回值：
        无；32 项全部必须构造 ``OperationTaskEvaluator``，伪造
        task ID 或非 FileOperate 标签必须拒绝。
    """

    built_ids: set[str] = set()
    for task_id in sorted(OPERATION_TASK_RULES):
        task = _load_task(task_id)
        evaluator = build_task_evaluator(
            task,
            evaluation_protocol=OPERATION_PROTOCOL_ID,
        )
        assert isinstance(evaluator, OperationTaskEvaluator)
        built_ids.add(task_id)
    assert built_ids == set(OPERATION_TASK_RULES)
    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            {"task_id": "Operation-FileOperate-Unknown", "task_tag": "FileOperate"},
            evaluation_protocol=OPERATION_PROTOCOL_ID,
        )
    drifted = _load_task(_TASK_ID)
    drifted["task_tag"] = "WebOperate"
    with pytest.raises(UnsupportedTaskEvaluatorError, match="protocol"):
        build_task_evaluator(
            drifted,
            evaluation_protocol=OPERATION_PROTOCOL_ID,
        )


def test_complete_missing_output_fails_without_using_final_text() -> None:
    """验证完整 manifest 内缺失必需输出时严格不通过。

    输入参数：无；快照明确宣告并包含四个 PDF。
    输出返回值：
        无；即使 Agent final text 声称完成，evaluator 仍只依据
        artifact 返回 failed；保留旧规则的完成比例分数，但详情
        不含 final text 或文件名。
    """

    snapshot, _temporary_directory = _snapshot_with_named_pdfs(
        omit_first=True,
    )
    try:
        environment = _SnapshotEnvironment(snapshot)
        evaluator = OperationTaskEvaluator(
            task_id=_TASK_ID,
            evaluation_protocol=OPERATION_PROTOCOL_ID,
        )

        result = evaluator.evaluate(
            _load_task(_TASK_ID),
            "CLAIMED COMPLETE PRIVATE FINAL TEXT",
            environment,
        )

        assert result.passed is False
        assert result.score == 0.8
        assert result.details["artifact_count"] == 4
        assert "CLAIMED" not in repr(result.details)
    finally:
        snapshot.close()


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_post_capture_missing_or_extra_evidence_is_an_error(
    mutation: str,
) -> None:
    """验证 manifest 冻结后出现缺失或额外文件时 fail closed。

    输入参数：
        mutation：``missing`` 删除已声明文件，``extra`` 注入未声明文件。
    输出返回值：
        无；evaluator 必须抛脱敏完整性错误，不得把部分或
        额外证据折算为一个正常分数。
    """

    snapshot, _temporary_directory = _snapshot_with_named_pdfs()
    try:
        root = snapshot.artifact_root()
        if mutation == "missing":
            next(root.iterdir()).unlink()
        else:
            (root / "private-extra-evidence.pdf").write_bytes(b"%PDF-1.7\nextra\n")
        evaluator = OperationTaskEvaluator(
            task_id=_TASK_ID,
            evaluation_protocol=OPERATION_PROTOCOL_ID,
        )

        with pytest.raises(
            OperationArtifactCaptureError,
            match="不完整",
        ) as captured:
            evaluator.evaluate(
                _load_task(_TASK_ID),
                "ignored-final-output",
                _SnapshotEnvironment(snapshot),
            )

        assert "private-extra" not in str(captured.value)
    finally:
        snapshot.close()
