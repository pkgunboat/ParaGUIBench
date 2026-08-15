"""OnlyOffice 共享文档集成边界。

本包只提供任务分流常量、share service 与客户端。它不拥有 evaluator，
也不改写 canonical task JSON。导入本包不会创建运行状态目录。
"""

from paraguibench.integrations.onlyoffice.contracts import (
    ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS,
    OSWORLD_SEARCH_AND_WRITE_TASK_IDS,
    SEARCH_AND_WRITE_TASK_IDS,
    build_attempt_document_id,
    uses_onlyoffice_shared_document,
)

__all__ = [
    "ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS",
    "OSWORLD_SEARCH_AND_WRITE_TASK_IDS",
    "SEARCH_AND_WRITE_TASK_IDS",
    "build_attempt_document_id",
    "uses_onlyoffice_shared_document",
]
