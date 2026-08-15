"""4-task pipeline-implicit runtime adapter 与脱敏投影测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from paraguibench.agents import AgentRunResult
from paraguibench.benchmark import PreparedTask
from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    HIDE_NA_ROWS_PROTOCOL_ID,
    HIDE_NA_ROWS_TASK_ID,
    IMAGE_CLASSIFICATION_PROTOCOL_ID,
    IMAGE_CLASSIFICATION_TASK_ID,
    PINNED_CLASSIFIED_IMAGE_SHA256,
    PINNED_PRESENTATION_SHA256,
    PINNED_UNCLASSIFIED_IMAGE_SHA256,
    SEARCHWRITE_XLSX_PROTOCOL_ID,
    SEARCHWRITE_XLSX_TASK_ID,
    CategorizedImage,
    CrossDocumentObservation,
    HideNARowsObservation,
    ImageClassificationEvaluationError,
    ImageClassificationObservation,
    NarrativeFacts,
    PresentationArtifact,
    PresentationFacts,
    SearchWriteCell,
    SearchWriteObservation,
    SearchWriteWorkbook,
    WorkbookHiddenRows,
)
from paraguibench.integrations.pipeline_implicit import (
    PipelineImplicitArtifactEvidenceError,
)
from paraguibench.runtime.evaluators import (
    PipelineImplicitTaskEvaluator,
    build_task_evaluator,
)
from paraguibench.runstore import EvaluationOutcome, RunStore
from paraguibench.runtime.attempt_runner import AttemptRunner
from tests.runstore._audit import (
    synthetic_run_version_vector,
    synthetic_task_audit,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]


class _ObservationEnvironment:
    """返回已冻结 pipeline-implicit typed observation 的窄 seam。

    输入参数：
        observation：当前合成 Attempt 的 evaluator 专属观测。
    输出返回值：
        无；对象记录 adapter 请求的任务与协议身份。
    """

    def __init__(self, observation: object) -> None:
        self.observation = observation
        self.calls: list[tuple[str, str]] = []

    def pipeline_implicit_observation(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """返回同一 typed observation 并记录冻结身份。

        输入参数：
            task_id/protocol_id：runtime registry 在 adapter 构造时固定的值。
        输出返回值：
            构造时注入的不可变 observation。
        """

        self.calls.append((task_id, protocol_id))
        return self.observation

    def start(self) -> None:
        """启动合成 Attempt 环境。

        输入参数：无。
        输出返回值：无；本 fake 不占用外部资源。
        """

    def prepare(self, task: dict[str, Any]) -> None:
        """验证 AttemptRunner 下发的 trusted task 身份。

        输入参数：
            task：当前 pipeline-implicit canonical task。
        输出返回值：无；只检查 task_id 命中四任务闭集。
        """

        assert isinstance(task.get("task_id"), str)

    def close(self) -> None:
        """关闭合成 Attempt 环境。

        输入参数：无。
        输出返回值：无；本 fake 无需外部清理。
        """


class _SensitiveFinalAgent:
    """返回不得被 pipeline-implicit 评价或持久化的文本。"""

    def run(
        self,
        task_view: dict[str, Any],
        environment: object,
    ) -> AgentRunResult:
        """返回一步结束的合成 Agent 结果。

        输入参数：
            task_view：不含 evaluator 证据的 Agent 投影。
            environment：已准备的环境；本 fake 不读取。
        输出返回值：
            带敏感哨兵文本的合法 ``AgentRunResult``。
        """

        del environment
        assert task_view["task_id"] == IMAGE_CLASSIFICATION_TASK_ID
        return AgentRunResult(
            final_output="PRIVATE PIPELINE FINAL SENTINEL",
            step_count=1,
            termination="finished",
        )


class _CrossDocumentSensitiveFinalAgent:
    """为 CombinationDocs-002 返回不得作为证据的敏感 final text。"""

    def run(
        self,
        task_view: dict[str, Any],
        environment: object,
    ) -> AgentRunResult:
        """返回合法 Agent 终态但不读取 evaluator-only observation。

        输入参数：
            task_view：只含 Agent 可见任务投影。
            environment：已准备环境，本替身不读取。
        输出返回值：
            带私密哨兵文本的一步完成结果。
        """

        del environment
        assert task_view["task_id"] == CROSS_DOCUMENT_TASK_ID
        return AgentRunResult(
            final_output="PRIVATE CROSS DOCUMENT FINAL 47109",
            step_count=1,
            termination="finished",
        )


class _InternalFailureObservationEnvironment(_ObservationEnvironment):
    """在 typed observation seam 注入固定 bridge 内部错误。"""

    def pipeline_implicit_observation(
        self,
        task_id: str,
        protocol_id: str,
    ) -> object:
        """模拟 bridge 未产生 typed observation 的内部故障。

        输入参数：
            task_id/protocol_id：runtime evaluator 固定的任务和协议。
        输出返回值：不返回，抛 production bridge 的脱敏固定错误。
        """

        assert task_id == CROSS_DOCUMENT_TASK_ID
        assert protocol_id == CROSS_DOCUMENT_PROTOCOL_ID
        raise PipelineImplicitArtifactEvidenceError("TYPED_OBSERVATION_INVALID")


def _load_task(task_id: str) -> dict[str, Any]:
    """从 canonical task 目录读取一份任务 object。

    输入参数：
        task_id：必须对应 ``benchmark/tasks`` 中的文件。
    输出返回值：
        仅供当前测试使用的可变 JSON 字典。
    """

    return json.loads(
        (_REPO_ROOT / "benchmark/tasks" / f"{task_id}.json").read_text(encoding="utf-8")
    )


def _passing_image_observation() -> ImageClassificationObservation:
    """构造 PPT-003 的 copy 语义满分观测。

    输入参数：无。
    输出返回值：
        四类、十二张已分类图、保留十六张源图与四个未改 PPT。
    """

    return ImageClassificationObservation(
        complete=True,
        category_names=tuple(PINNED_CLASSIFIED_IMAGE_SHA256),
        categorized_images=tuple(
            CategorizedImage(category, digest)
            for category, digests in PINNED_CLASSIFIED_IMAGE_SHA256.items()
            for digest in digests
        ),
        source_image_sha256=tuple(
            digest
            for digests in PINNED_CLASSIFIED_IMAGE_SHA256.values()
            for digest in digests
        )
        + PINNED_UNCLASSIFIED_IMAGE_SHA256,
        presentations=tuple(
            PresentationArtifact(document_id, digest)
            for document_id, digest in PINNED_PRESENTATION_SHA256.items()
        ),
        unexpected_regular_file_count=0,
    )


def test_ppt_runtime_tracer_ignores_final_text_and_projects_only_counts() -> None:
    """验证 PPT-003 从 registry、environment seam 到脱敏 runtime 结果闭环。

    输入参数：
        无；传入满分 typed observation 与含私密占位符的 final text。
    输出返回值：
        无；结果只含固定协议、reason code 和计数，不依赖
        Agent 声明，不保存类别、摘要或文件信息。
    """

    task = _load_task(IMAGE_CLASSIFICATION_TASK_ID)
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=IMAGE_CLASSIFICATION_PROTOCOL_ID,
    )
    environment = _ObservationEnvironment(_passing_image_observation())

    result = evaluator.evaluate(
        task,
        "PRIVATE FINAL CLAIM basketball "
        + next(iter(PINNED_PRESENTATION_SHA256.values())),
        environment,
    )

    assert isinstance(evaluator, PipelineImplicitTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert environment.calls == [
        (IMAGE_CLASSIFICATION_TASK_ID, IMAGE_CLASSIFICATION_PROTOCOL_ID)
    ]
    assert result.details == {
        "protocol_id": IMAGE_CLASSIFICATION_PROTOCOL_ID,
        "reason_codes": (),
        "expected_category_count": 4,
        "matched_category_count": 4,
        "unexpected_category_count": 0,
        "expected_classification_count": 12,
        "matched_classification_count": 12,
        "missing_classification_count": 0,
        "misclassified_image_count": 0,
        "duplicate_classification_count": 0,
        "unexpected_image_count": 0,
        "missing_unclassified_image_count": 0,
        "duplicate_source_image_count": 0,
        "changed_presentation_count": 0,
        "unexpected_regular_file_count": 0,
    }
    assert "PRIVATE" not in repr(result.details)
    assert "basketball" not in repr(result.details)


def test_excel_runtime_tracer_projects_hidden_row_integrity_counts() -> None:
    """验证 Excel-008 通过固定 adapter 仅持久化行和文档计数。

    输入参数：
        无；传入五个工作簿的满分 typed observation。
    输出返回值：
        无；完整 runtime 结果不含文件名、单元格、路径或文本。
    """

    task = _load_task(HIDE_NA_ROWS_TASK_ID)
    observation = HideNARowsObservation(
        complete=True,
        workbooks=(
            WorkbookHiddenRows("KFC_Monthly_Data.xlsx", (8, 10), True),
            WorkbookHiddenRows("McDonalds_Monthly_Data.xlsx", (8, 14), True),
            WorkbookHiddenRows("Mixue_Monthly_Data.xlsx", (), True),
            WorkbookHiddenRows("PizzaHut_Monthly_Data.xlsx", (9,), True),
            WorkbookHiddenRows("Subway_Monthly_Data.xlsx", (4, 5, 8), True),
        ),
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=HIDE_NA_ROWS_PROTOCOL_ID,
    )

    result = evaluator.evaluate(
        task,
        "PRIVATE FINAL CLAIM KFC_Monthly_Data.xlsx",
        _ObservationEnvironment(observation),
    )

    assert isinstance(evaluator, PipelineImplicitTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "protocol_id": HIDE_NA_ROWS_PROTOCOL_ID,
        "reason_codes": (),
        "expected_document_count": 5,
        "evaluated_document_count": 5,
        "unexpected_document_count": 0,
        "expected_hidden_row_count": 8,
        "matched_hidden_row_count": 8,
        "missing_hidden_row_count": 0,
        "unexpected_hidden_row_count": 0,
        "mutated_document_count": 0,
    }
    assert "KFC" not in repr(result.details)
    assert "PRIVATE" not in repr(result.details)


def test_searchwrite_runtime_tracer_keeps_nine_cell_fixed_denominator() -> None:
    """验证 SearchAndWrite-008 在 runtime 仍以两文件九格为固定分母。

    输入参数：
        无；传入两个完整工作簿 typed observation。
    输出返回值：
        无；RuntimeEvaluation 仅保留文档、单元格与基线完整性计数。
    """

    task = _load_task(SEARCHWRITE_XLSX_TASK_ID)
    observation = SearchWriteObservation(
        complete=True,
        workbooks=(
            SearchWriteWorkbook(
                document_id="group-1",
                cells=(
                    SearchWriteCell("C6", 2),
                    SearchWriteCell("D6", "London"),
                    SearchWriteCell("B7", 1826),
                    SearchWriteCell("D8", "Edinburgh"),
                ),
                baseline_unchanged=True,
            ),
            SearchWriteWorkbook(
                document_id="group-2",
                cells=(
                    SearchWriteCell("D4", "Manchester"),
                    SearchWriteCell("B5", 1829),
                    SearchWriteCell("C6", 45),
                    SearchWriteCell("B8", 1965),
                    SearchWriteCell("D8", "Coventry"),
                ),
                baseline_unchanged=True,
            ),
        ),
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=SEARCHWRITE_XLSX_PROTOCOL_ID,
    )

    result = evaluator.evaluate(
        task,
        "PRIVATE FINAL CLAIM D8=Coventry",
        _ObservationEnvironment(observation),
    )

    assert isinstance(evaluator, PipelineImplicitTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "protocol_id": SEARCHWRITE_XLSX_PROTOCOL_ID,
        "reason_codes": (),
        "expected_document_count": 2,
        "evaluated_document_count": 2,
        "unexpected_document_count": 0,
        "expected_cell_count": 9,
        "matched_cell_count": 9,
        "missing_cell_count": 0,
        "mismatched_cell_count": 0,
        "unexpected_cell_count": 0,
        "mutated_document_count": 0,
    }
    assert "Coventry" not in repr(result.details)
    assert "D8" not in repr(result.details)


def test_cross_document_runtime_uses_typed_facts_not_agent_claims() -> None:
    """验证 CombinationDocs-002 只读取 XLSX 派生的 typed 事实。

    输入参数：
        无；提供 January 利润、客户数与利润顺序的满分观测。
    输出返回值：
        无；runtime 详情不保存月份、数值、文档文本或 Agent 声明。
    """

    task = _load_task(CROSS_DOCUMENT_TASK_ID)
    observation = CrossDocumentObservation(
        complete=True,
        reference_spreadsheet_unchanged=True,
        narrative=NarrativeFacts(
            january_profit=47_109,
            strongest_profit_order=("july", "december", "january"),
            other_facts_match_reference=True,
        ),
        presentation=PresentationFacts(
            january_customers=1_895,
            other_facts_match_reference=True,
        ),
        unexpected_document_count=0,
    )
    evaluator = build_task_evaluator(
        task,
        evaluation_protocol=CROSS_DOCUMENT_PROTOCOL_ID,
    )

    result = evaluator.evaluate(
        task,
        "PRIVATE FINAL CLAIM January 999999",
        _ObservationEnvironment(observation),
    )

    assert isinstance(evaluator, PipelineImplicitTaskEvaluator)
    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "protocol_id": CROSS_DOCUMENT_PROTOCOL_ID,
        "reason_codes": (),
        "required_fact_count": 3,
        "matched_fact_count": 3,
        "failed_fact_count": 0,
        "missing_document_count": 0,
        "unexpected_document_count": 0,
        "semantic_integrity_failure_count": 0,
        "reference_spreadsheet_changed": False,
    }
    assert "January" not in repr(result.details)
    assert "47109" not in repr(result.details)
    assert "999999" not in repr(result.details)


def test_incomplete_pipeline_observation_persists_error_and_null_score(
    tmp_path: Path,
) -> None:
    """验证不完整 bundle/manifest 在 RunStore 只落 ERROR/null。

    输入参数：
        tmp_path：pytest 提供的隔离 RunStore 根。
    输出返回值：
        无；AttemptRunner 保留成功执行终态，评价为 ERROR、
        score 为 null，且不持久化 Agent 最终文本。
    """

    task = _load_task(IMAGE_CLASSIFICATION_TASK_ID)
    prepared = PreparedTask(
        trusted_task=task,
        agent_task={"task_id": IMAGE_CLASSIFICATION_TASK_ID},
        audit_metadata=synthetic_task_audit(
            IMAGE_CLASSIFICATION_TASK_ID,
            task_uid=task["task_uid"],
            task_type=task["task_type"],
            task_source=task["task_source"],
            task_tag=task["task_tag"],
        ),
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-pipeline-incomplete",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-pipeline-incomplete",
        task_id=IMAGE_CLASSIFICATION_TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared.audit_metadata,
    )
    observation = ImageClassificationObservation(
        complete=False,
        category_names=(),
        categorized_images=(),
        source_image_sha256=(),
        presentations=(),
        unexpected_regular_file_count=0,
    )

    with pytest.raises(ImageClassificationEvaluationError):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=prepared,
            environment=_ObservationEnvironment(observation),
            agent=_SensitiveFinalAgent(),
            evaluator=build_task_evaluator(
                task,
                evaluation_protocol=IMAGE_CLASSIFICATION_PROTOCOL_ID,
            ),
        )

    summary_text = (attempt.path / "summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert summary["execution"]["outcome"] == "SUCCEEDED"
    assert summary["evaluation"]["outcome"] == EvaluationOutcome.ERROR.value
    assert summary["evaluation"]["score"] is None
    assert "PRIVATE PIPELINE FINAL SENTINEL" not in summary_text


def test_cross_document_internal_bridge_error_persists_error_and_null_score(
    tmp_path: Path,
) -> None:
    """验证 CombinationDocs-002 内部 parser 故障进入 ERROR/null 通道。

    输入参数：
        tmp_path：pytest 隔离 RunStore 根。
    输出返回值：
        无；Agent 执行成功，评价为 ERROR 且 score 为 null，final text
        和文档事实均不持久化。
    """

    task = _load_task(CROSS_DOCUMENT_TASK_ID)
    prepared = PreparedTask(
        trusted_task=task,
        agent_task={"task_id": CROSS_DOCUMENT_TASK_ID},
        audit_metadata=synthetic_task_audit(
            CROSS_DOCUMENT_TASK_ID,
            task_uid=task["task_uid"],
            task_type=task["task_type"],
            task_source=task["task_source"],
            task_tag=task["task_tag"],
        ),
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-cross-document-internal-error",
        run_record={"environment_id": "synthetic-osworld"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-cross-document-internal-error",
        task_id=CROSS_DOCUMENT_TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared.audit_metadata,
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError):
        AttemptRunner(store).run(
            attempt=attempt,
            prepared_task=prepared,
            environment=_InternalFailureObservationEnvironment(None),
            agent=_CrossDocumentSensitiveFinalAgent(),
            evaluator=build_task_evaluator(
                task,
                evaluation_protocol=CROSS_DOCUMENT_PROTOCOL_ID,
            ),
        )

    summary_text = (attempt.path / "summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)
    assert summary["execution"]["outcome"] == "SUCCEEDED"
    assert summary["evaluation"]["outcome"] == EvaluationOutcome.ERROR.value
    assert summary["evaluation"]["score"] is None
    assert "PRIVATE CROSS DOCUMENT FINAL" not in summary_text
    assert "47109" not in summary_text
