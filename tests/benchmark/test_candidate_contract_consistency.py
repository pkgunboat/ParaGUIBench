"""candidate 闭集与 runtime-support / 评价协议的跨清单一致性。"""

from __future__ import annotations

import json
from pathlib import Path

from paraguibench.evaluation.pipeline_implicit import SEARCHWRITE_XLSX_TASK_ID
from paraguibench.integrations.pipeline_implicit.artifact_evidence import (
    PIPELINE_IMPLICIT_TASK_PROTOCOLS,
)
from paraguibench.runtime.osworld_artifact_component_contracts import (
    OSWORLD_ARTIFACT_COMPONENT_TASK_IDS,
    osworld_artifact_environment_protocol,
)
from paraguibench.runtime.pipeline_implicit_component_receipts import (
    PIPELINE_IMPLICIT_COMPONENT_TASK_IDS,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_SUPPORT_PATH = REPO_ROOT / "benchmark/manifests/runtime-support-v1.json"
_PIPELINE_RECEIPT_SCHEMA_PATH = (
    REPO_ROOT / "benchmark/schemas/pipeline-implicit-component-receipt-v1.schema.json"
)
_ARTIFACT_RECEIPT_SCHEMA_PATH = (
    REPO_ROOT / "benchmark/schemas/osworld-artifact-component-receipt-v1.schema.json"
)
_IMPLEMENTED_PIPELINE_CANDIDATE_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperationExcel-008",
        "Operation-FileOperate-BatchOperationPPT-003",
        "Operation-FileOperate-CombinationDocs-002",
    }
)


def _runtime_support_tasks() -> dict[str, dict[str, object]]:
    """读取正式 runtime-support 的 task 条目。

    输入参数：无；读取仓库内 ``runtime-support-v1.json``。
    输出返回值：``task_id -> 条目`` 的字典。
    """

    payload = json.loads(_RUNTIME_SUPPORT_PATH.read_text(encoding="utf-8"))
    entries = payload["tasks"]
    assert isinstance(entries, list)
    return {str(entry["task_id"]): entry for entry in entries}


def test_artifact_candidate_environment_matches_runtime_support() -> None:
    """确认 12-task artifact candidate 要求的环境协议等于正式清单。

    输入参数：无；对照 runtime-support 与 candidate 合同函数。
    输出返回值：WebOperate-SearchAndWrite-001 为 chrome.v1，
        其余 11 项为 desktop.v1，且与 runtime-support 逐项一致。
    """

    entries = _runtime_support_tasks()
    web_task_id = "Operation-WebOperate-SearchAndWrite-001"
    assert web_task_id in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS
    assert osworld_artifact_environment_protocol(web_task_id) == "osworld.chrome.v1"
    for task_id in OSWORLD_ARTIFACT_COMPONENT_TASK_IDS:
        official = entries[task_id]["environment_protocol"]
        required = osworld_artifact_environment_protocol(task_id)
        assert official == required
        if task_id == web_task_id:
            assert official == "osworld.chrome.v1"
        else:
            assert official == "osworld.desktop.v1"


def test_artifact_receipt_schema_allows_official_environment_protocols() -> None:
    """确认 artifact receipt schema 覆盖 candidate 实际使用的环境协议。

    输入参数：无；读取版本化 receipt schema。
    输出返回值：``environment_protocol`` 允许 desktop 与 chrome，
        不再把全部 12 项锁死为 desktop.v1。
    """

    schema = json.loads(_ARTIFACT_RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    allowed = schema["$defs"]["receipt"]["properties"]["environment_protocol"]
    assert allowed == {
        "enum": ["osworld.chrome.v1", "osworld.desktop.v1"],
    }


def test_pipeline_component_candidate_is_implemented_subset() -> None:
    """确认无 Agent candidate 只公开已实现的 3 项，评价闭集仍为 4 项。

    输入参数：无；对照评价协议表、candidate 闭集、receipt schema
        与 runtime-support。
    输出返回值：SearchAndWrite-008 仍是正式评价任务，但不在
        candidate/receipt 闭集中。
    """

    evaluation_ids = set(PIPELINE_IMPLICIT_TASK_PROTOCOLS)
    assert SEARCHWRITE_XLSX_TASK_ID in evaluation_ids
    assert SEARCHWRITE_XLSX_TASK_ID not in PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
    assert PIPELINE_IMPLICIT_COMPONENT_TASK_IDS == (
        _IMPLEMENTED_PIPELINE_CANDIDATE_TASK_IDS
    )
    assert PIPELINE_IMPLICIT_COMPONENT_TASK_IDS < evaluation_ids

    schema = json.loads(_PIPELINE_RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    receipt = schema["$defs"]["receipt"]
    assert set(receipt["properties"]["task_id"]["enum"]) == (
        PIPELINE_IMPLICIT_COMPONENT_TASK_IDS
    )
    assert SEARCHWRITE_XLSX_TASK_ID not in receipt["properties"]["task_id"]["enum"]
    assert (
        PIPELINE_IMPLICIT_TASK_PROTOCOLS[SEARCHWRITE_XLSX_TASK_ID]
        not in receipt["properties"]["task_evaluation_protocol"]["enum"]
    )

    entries = _runtime_support_tasks()
    search = entries[SEARCHWRITE_XLSX_TASK_ID]
    assert search["evaluation_protocol"] == (
        PIPELINE_IMPLICIT_TASK_PROTOCOLS[SEARCHWRITE_XLSX_TASK_ID]
    )
    assert search["local_readiness_status"] == "local_ready"
    assert "pipeline_implicit_live_validation_not_completed" in search["blocker_codes"]
