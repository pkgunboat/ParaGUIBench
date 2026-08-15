"""OnlyOffice 共享文档任务集合、document_id 与单实例端口合同。"""

from __future__ import annotations

import hashlib
import re

ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS: frozenset[str] = frozenset(
    {
        "Operation-FileOperate-SearchAndWrite-002",
        "Operation-FileOperate-SearchAndWrite-004",
        "Operation-FileOperate-SearchAndWrite-006",
        "Operation-FileOperate-SearchAndWrite-008",
    }
)
"""必须走 OnlyOffice 共享文档服务的精确 4 项任务。"""

OSWORLD_SEARCH_AND_WRITE_TASK_IDS: frozenset[str] = frozenset(
    {
        "Operation-FileOperate-SearchAndWrite-001",
        "Operation-FileOperate-SearchAndWrite-003",
        "Operation-FileOperate-SearchAndWrite-005",
        "Operation-FileOperate-SearchAndWrite-007",
        "Operation-FileOperate-SearchAndWrite-009",
        "Operation-WebOperate-SearchAndWrite-001",
    }
)
"""必须继续走 OSWorld / LibreOffice 路径的精确 6 项 SearchAndWrite 任务。"""

SEARCH_AND_WRITE_TASK_IDS: frozenset[str] = (
    ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS | OSWORLD_SEARCH_AND_WRITE_TASK_IDS
)
"""全部 10 个 SearchAndWrite 任务的闭集。"""

ALLOWED_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "odt",
        "ods",
        "odp",
        "txt",
        "rtf",
        "pdf",
    }
)
"""share service 允许上传的扩展名闭集。"""

DEFAULT_DOCUMENT_SERVER_PORT = 8080
"""单实例 DocumentServer 的默认宿主端口。"""

DEFAULT_SHARE_PORT = 5050
"""单实例 share service 的默认宿主端口。"""

_DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,220}$")
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._=-]+")


def uses_onlyoffice_shared_document(task_id: str) -> bool:
    """按精确任务 ID 判断是否使用 OnlyOffice 共享文档服务。

    功能：
        只根据规范闭集分流，不得根据资产是否为 xlsx 推断。
        SearchAndWrite-007 含 xlsx 资产，但仍返回 False。
    输入参数：
        task_id：canonical 任务 ID。
    输出返回值：
        属于 4 项 OnlyOffice 任务时为 True，否则为 False。
    """

    return task_id in ONLYOFFICE_SHARED_DOCUMENT_TASK_IDS


def project_asset_digest(asset_digest: str) -> str:
    """把输入资产摘要投影为 document_id 使用的稳定短标识。

    功能：
        优先取十六进制摘要的前 16 位；否则对原字符串做 SHA-256 后再截断。
    输入参数：
        asset_digest：资产 SHA-256 或其他稳定摘要。
    输出返回值：
        16 位小写十六进制投影。
    异常语义：
        空字符串仍返回确定性哈希投影，不抛异常。
    """

    cleaned = "".join(
        character
        for character in asset_digest.lower()
        if character in "0123456789abcdef"
    )
    if len(cleaned) >= 16:
        return cleaned[:16]
    return hashlib.sha256(asset_digest.encode("utf-8")).hexdigest()[:16]


def sanitize_document_id_token(value: str) -> str:
    """把任意身份字段收成 document_id 可拼接的安全片段。

    功能：
        替换路径分隔符与非法字符，避免目录穿越。
    输入参数：
        value：task_id、run_id、attempt_id 或文档 stem。
    输出返回值：
        只含 ``[A-Za-z0-9._=-]`` 的非空片段；空输入回退为 ``id``。
    """

    cleaned = value.replace("/", "_").replace("\\", "_").replace("..", "_")
    cleaned = _SAFE_TOKEN_RE.sub("_", cleaned).strip("._-")
    return cleaned or "id"


def build_attempt_document_id(
    *,
    task_id: str,
    run_id: str,
    attempt_id: str,
    asset_digest: str,
    document_stem: str,
) -> str:
    """构造 Attempt 级唯一、同 Attempt 可复用的 document_id。

    功能：
        由 task/run/attempt/文档 stem/资产摘要投影拼接，保证同一 Attempt
        的全部 worker 得到相同 ID，不同 Attempt 不碰撞。
    输入参数：
        task_id：canonical 任务 ID。
        run_id：RunStore run 身份。
        attempt_id：RunStore attempt 身份。
        asset_digest：该输入资产的稳定摘要。
        document_stem：同一 Attempt 内区分多份文档的文件名主干，
            例如 SearchWrite-008 的两份 xlsx。
    输出返回值：
        可写入 share service 的 document_id。
    异常语义：
        结果不符合允许字符集时抛出 ValueError。
    """

    document_id = "__".join(
        (
            sanitize_document_id_token(task_id),
            sanitize_document_id_token(run_id),
            sanitize_document_id_token(attempt_id),
            sanitize_document_id_token(document_stem),
            project_asset_digest(asset_digest),
        )
    )
    if not _DOCUMENT_ID_RE.fullmatch(document_id):
        raise ValueError(f"生成的 document_id 非法: {document_id}")
    return document_id


def is_safe_document_id(document_id: str) -> bool:
    """检查调用方提供的 document_id 是否允许落盘。

    功能：
        拒绝空值、路径穿越和超出长度的标识。
    输入参数：
        document_id：待检查的文档 ID。
    输出返回值：
        合法时为 True，否则为 False。
    """

    return bool(_DOCUMENT_ID_RE.fullmatch(document_id))
