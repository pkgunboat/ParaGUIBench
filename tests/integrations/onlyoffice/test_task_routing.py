"""精确断言 SearchAndWrite 的 OnlyOffice / OSWorld 5/5 分流。"""

from __future__ import annotations

from paraguibench.integrations.onlyoffice.contracts import (
    ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS,
    OSWORLD_SEARCH_AND_WRITE_TASK_IDS,
    SEARCH_AND_WRITE_TASK_IDS,
    uses_onlyoffice_shared_document,
)


def test_onlyoffice_and_osworld_searchwrite_sets_are_exact_and_disjoint() -> None:
    """功能：锁定仅 5 项走 OnlyOffice、其余 5 项走 OSWorld，且不得按 xlsx 推断。

    输入参数：
        无；读取任务集合常量。
    输出返回值：
        无；集合大小、成员、互斥与并集必须与规范逐字一致。
    """

    assert ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS == frozenset(
        {
            "Operation-FileOperate-SearchAndWrite-002",
            "Operation-FileOperate-SearchAndWrite-004",
            "Operation-FileOperate-SearchAndWrite-006",
            "Operation-FileOperate-SearchAndWrite-007",
            "Operation-FileOperate-SearchAndWrite-008",
        }
    )
    assert OSWORLD_SEARCH_AND_WRITE_TASK_IDS == frozenset(
        {
            "Operation-FileOperate-SearchAndWrite-001",
            "Operation-FileOperate-SearchAndWrite-003",
            "Operation-FileOperate-SearchAndWrite-005",
            "Operation-FileOperate-SearchAndWrite-009",
            "Operation-WebOperate-SearchAndWrite-001",
        }
    )
    assert ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS.isdisjoint(
        OSWORLD_SEARCH_AND_WRITE_TASK_IDS
    )
    assert SEARCH_AND_WRITE_TASK_IDS == (
        ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS | OSWORLD_SEARCH_AND_WRITE_TASK_IDS
    )
    assert len(SEARCH_AND_WRITE_TASK_IDS) == 10


def test_searchwrite_007_routes_to_onlyoffice_shared_documents() -> None:
    """功能：SearchAndWrite-007 无 evaluator_path，必须判定为 OnlyOffice 任务。

    输入参数：
        无。
    输出返回值：
        无；007 必须进入 OnlyOffice 集合，不得回退 OSWorld 集合
        （否则 searchwrite Stage 0 会跳过模板上传并报 evaluator_error）。
    """

    assert (
        uses_onlyoffice_shared_document("Operation-FileOperate-SearchAndWrite-007")
        is True
    )
    assert "Operation-FileOperate-SearchAndWrite-007" in (
        ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS
    )
    for task_id in ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS:
        assert uses_onlyoffice_shared_document(task_id) is True
    for task_id in OSWORLD_SEARCH_AND_WRITE_TASK_IDS:
        assert uses_onlyoffice_shared_document(task_id) is False
