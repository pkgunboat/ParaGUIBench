"""精确断言 SearchAndWrite 的 OnlyOffice / OSWorld 4/6 分流。"""

from __future__ import annotations

from paraguibench.integrations.onlyoffice.contracts import (
    ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS,
    OSWORLD_SEARCH_AND_WRITE_TASK_IDS,
    SEARCH_AND_WRITE_TASK_IDS,
    uses_onlyoffice_shared_document,
)


def test_onlyoffice_and_osworld_searchwrite_sets_are_exact_and_disjoint() -> None:
    """功能：锁定仅 4 项走 OnlyOffice、其余 6 项走 OSWorld，且不得按 xlsx 推断。

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
            "Operation-FileOperate-SearchAndWrite-008",
        }
    )
    assert OSWORLD_SEARCH_AND_WRITE_TASK_IDS == frozenset(
        {
            "Operation-FileOperate-SearchAndWrite-001",
            "Operation-FileOperate-SearchAndWrite-003",
            "Operation-FileOperate-SearchAndWrite-005",
            "Operation-FileOperate-SearchAndWrite-007",
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


def test_xlsx_presence_does_not_infer_onlyoffice_routing() -> None:
    """功能：SearchAndWrite-007 虽有 xlsx 资产，仍必须判定为 OSWorld 任务。

    输入参数：
        无。
    输出返回值：
        无；007 不得进入 OnlyOffice 集合。
    """

    assert (
        uses_onlyoffice_shared_document("Operation-FileOperate-SearchAndWrite-007")
        is False
    )
    assert "Operation-FileOperate-SearchAndWrite-007" in (
        OSWORLD_SEARCH_AND_WRITE_TASK_IDS
    )
    for task_id in ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS:
        assert uses_onlyoffice_shared_document(task_id) is True
    for task_id in OSWORLD_SEARCH_AND_WRITE_TASK_IDS:
        assert uses_onlyoffice_shared_document(task_id) is False
