"""OSWorld Chrome 书签证据与纯评价层共享的不可变 contract。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


CHROME_BOOKMARKS_PROTOCOL_ID = "paraguibench.osworld.chrome-bookmarks.v1"


@dataclass(frozen=True, slots=True)
class BookmarkTaskBinding:
    """保存一个 Chrome Bookmarks task 的完整 runtime 身份闭集。

    输入参数：
        task_id/task_uid：release 中稳定的 canonical 身份。
        task_source/task_type/task_tag：保留各任务当前正式分类，不假定
            所有 bookmark 任务都标为 OSWorld。
        evaluator_path：旧最终 evaluator 的固定来源路径。
    输出返回值：
        不可变 binding，供 evidence 与 runtime registry 共用。
    """

    task_id: str
    task_uid: str
    task_source: str
    task_type: str
    task_tag: str
    evaluator_path: str = "eval/webnavigate_bookmark_evaluator.py"


def _bookmark_binding(
    task_id: str,
    task_uid: str,
    *,
    task_source: str = "",
    task_type: str = "self",
) -> BookmarkTaskBinding:
    """构造共享固定 tag/evaluator 的书签任务绑定。

    输入参数：
        task_id/task_uid：正式 release 身份。
        task_source/task_type：当前 canonical task 的精确分类值。
    输出返回值：
        ``task_tag=WebOperate`` 且 evaluator 路径固定的不可变 binding。
    """

    return BookmarkTaskBinding(
        task_id=task_id,
        task_uid=task_uid,
        task_source=task_source,
        task_type=task_type,
        task_tag="WebOperate",
    )


_BOOKMARK_TASK_BINDINGS = (
    _bookmark_binding(
        "Operation-WebOperate-Settings-002",
        "ef47625b-cd1b-46ca-a16c-b0ac0c99c2cc",
    ),
    _bookmark_binding(
        "Operation-WebOperate-Settings-003",
        "bc69ee94-cf90-4cc4-a6ed-4266daa71706",
        task_source="OSWorld",
        task_type="OSWorld脚本",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-001",
        "49be33a6-666a-4f17-8f96-54ecf6fca25e",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-002",
        "9bc31d45-a51c-45c9-95de-b30d8bc67f79",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-003",
        "22e76d4d-0b1f-4c51-ab58-8ae41cbee9b7",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-004",
        "a1d0e68a-6dd0-402b-8d6a-713c152c19dc",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-005",
        "0f931391-7dd0-46ea-a492-13f064056d99",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-007",
        "1c100df8-4a3e-4680-be7e-3f5e2e26b22f",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-008",
        "eb1ad6e6-b3cc-49e6-a633-a012ae38f56e",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-010",
        "a93c6823-7716-40a2-91e1-17dabbaf7d0c",
        task_source="OSWorld",
        task_type="OSWorld脚本改造",
    ),
    _bookmark_binding(
        "Operation-WebOperate-WebNavigate-011",
        "38b185ab-d01d-4c97-a58e-d8d5ab4bec7b",
        task_source="OSWorld",
        task_type="OSWorld脚本改造",
    ),
)
OSWORLD_BOOKMARK_TASK_BINDINGS: Mapping[str, BookmarkTaskBinding] = MappingProxyType(
    {binding.task_id: binding for binding in _BOOKMARK_TASK_BINDINGS}
)
OSWORLD_BOOKMARK_TASK_IDS = frozenset(OSWORLD_BOOKMARK_TASK_BINDINGS)


@dataclass(frozen=True, slots=True)
class ChromeBookmarkRecord:
    """保存 Chrome Bookmarks 中一条 URL 节点的受控投影。

    输入参数：
        url：书签的完整 URL；只在 evaluator 可信内存中使用。
        folder_path：从 Chrome ``roots`` 键开始的完整文件夹路径。
    输出返回值：
        不可变记录；``repr`` 不包含 URL 或文件夹名。
    """

    url: str = field(repr=False)
    folder_path: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ChromeBookmarksObservation:
    """保存单台 VM 的 Chrome 书签完整快照。

    输入参数：
        records：从固定 Bookmarks 文件闭集解析的 URL 记录。
        complete：是否已无歧义地读取、解析和枚举整个文件。
    输出返回值：
        不可变 observation；对象本身不应持久化到 RunStore。
    """

    records: tuple[ChromeBookmarkRecord, ...] = field(repr=False)
    complete: bool = True


__all__ = [
    "CHROME_BOOKMARKS_PROTOCOL_ID",
    "OSWORLD_BOOKMARK_TASK_BINDINGS",
    "OSWORLD_BOOKMARK_TASK_IDS",
    "BookmarkTaskBinding",
    "ChromeBookmarkRecord",
    "ChromeBookmarksObservation",
]
