"""OSWorld Chrome Bookmarks 的受控基线重置与证据采集。

本模块只读取动态 guest home 下固定的 Google Chrome
``Default/Bookmarks``。任务前在 Chrome 完整退出后写入已知空基线，
评价前再次等待 Chrome 落盘，然后以 4 MiB 硬上限读取。
URL 和文件夹名只进入 ``repr=False`` 的不可变 contract，
不进入异常消息或日志。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
from typing import Any

from paraguibench.integrations.osworld.bookmark_contracts import (
    CHROME_BOOKMARKS_PROTOCOL_ID,
    OSWORLD_BOOKMARK_TASK_BINDINGS,
    OSWORLD_BOOKMARK_TASK_IDS,
    ChromeBookmarkRecord,
    ChromeBookmarksObservation,
)


BOOKMARKS_MAX_FILE_BYTES = 4 * 1024 * 1024
BOOKMARKS_MAX_RESPONSE_BYTES = 6 * 1024 * 1024
_MAX_ROOTS = 32
_MAX_TOTAL_NODES = 8192
_MAX_BOOKMARK_RECORDS = 4096
_MAX_URL_BYTES = 8192
_MAX_FOLDER_DEPTH = 32
_MAX_FOLDER_COMPONENT_BYTES = 1024
_MAX_BOOKMARK_NAME_BYTES = 4096
_CHROME_EXIT_TIMEOUT_SECONDS = 15.0
_CHROME_CDP_PORT = 1337
_CHROME_CDP_TIMEOUT_SECONDS = 15.0
_BOOKMARKS_READ_TIMEOUT_SECONDS = 15.0
_BOOKMARKS_RELATIVE_PATH = PurePosixPath(".config/google-chrome/Default/Bookmarks")

_RESET_BOOKMARKS_GUEST_PROGRAM = r'''
import json
import os
import sys


def open_directory_without_symlinks(path):
    """
    功能：从根目录逐层以 O_NOFOLLOW 打开目标目录。
    输入参数：path 为 host 已验证的 POSIX 绝对目录。
    输出返回值：返回由调用方关闭的目录文件描述符。
    """

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_fd = os.open("/", flags)
    try:
        for component in path.split("/")[1:]:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def remove_if_present(directory_fd, name):
    """
    功能：在已打开目录内幂等删除固定文件名。
    输入参数：directory_fd 为目录描述符，name 为单层文件名。
    输出返回值：无；文件不存在时直接返回。
    """

    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass


def write_all(file_fd, payload):
    """
    功能：将已知有界 bytes 完整写入文件描述符。
    输入参数：file_fd 为目标描述符，payload 为空书签 JSON。
    输出返回值：无；短写会循环直到完成。
    """

    offset = 0
    while offset < len(payload):
        written = os.write(file_fd, payload[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def main():
    """
    功能：原子替换 Chrome Default profile 的书签为固定空基线。
    输入参数：sys.argv[1] 为 host 动态推导的 Bookmarks 路径。
    输出返回值：无；成功时不向 stdout/stderr 输出路径或内容。
    """

    bookmarks_path = sys.argv[1]
    directory_path, file_name = os.path.split(bookmarks_path)
    if file_name != "Bookmarks" or not directory_path.startswith("/"):
        raise ValueError("invalid fixed bookmark path")
    directory_fd = open_directory_without_symlinks(directory_path)
    temporary_name = ".paraguibench-bookmarks-reset"
    try:
        remove_if_present(directory_fd, temporary_name)
        remove_if_present(directory_fd, file_name + ".bak")
        payload = json.dumps(
            {
                "roots": {
                    "bookmark_bar": {
                        "children": [],
                        "date_added": "0",
                        "date_last_used": "0",
                        "date_modified": "0",
                        "guid": "00000000-0000-4000-a000-000000000002",
                        "id": "1",
                        "name": "Bookmarks bar",
                        "type": "folder",
                    },
                    "other": {
                        "children": [],
                        "date_added": "0",
                        "date_last_used": "0",
                        "date_modified": "0",
                        "guid": "00000000-0000-4000-a000-000000000003",
                        "id": "2",
                        "name": "Other bookmarks",
                        "type": "folder",
                    },
                    "synced": {
                        "children": [],
                        "date_added": "0",
                        "date_last_used": "0",
                        "date_modified": "0",
                        "guid": "00000000-0000-4000-a000-000000000004",
                        "id": "3",
                        "name": "Mobile bookmarks",
                        "type": "folder",
                    },
                },
                "version": 1,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", "strict")
        file_fd = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            write_all(file_fd, payload)
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.replace(
            temporary_name,
            file_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        remove_if_present(directory_fd, temporary_name)
        os.close(directory_fd)


main()
'''.strip()


class OSWorldBookmarkEvidenceError(RuntimeError):
    """表示书签身份、guest I/O 或 JSON 闭集无法可靠确定。"""


@dataclass(slots=True)
class _ParseState:
    """保存一次严格书签树遍历的有界内部状态。

    输入参数：
        records：已发现的受控 URL 投影。
        seen：用于忠实复现旧协议的 ``(URL, folder_path)`` 去重集。
        node_count：已遍历的 folder/URL 总节点数。
    输出返回值：
        可变的内部计数容器，不越过 parser 边界。
    """

    records: list[ChromeBookmarkRecord] = field(default_factory=list)
    seen: set[tuple[str, tuple[str, ...]]] = field(default_factory=set)
    node_count: int = 0


class OSWorldChromeBookmarkEvidenceSource:
    """为固定 11 任务提供 Chrome 书签基线与单 VM 快照。"""

    @property
    def reset_program(self) -> str:
        """返回测试可审计的固定 guest 重置程序。

        输入参数：
            无。
        输出返回值：
            不包含任务或 Agent 数据的常量 Python 程序。
        """

        return _RESET_BOOKMARKS_GUEST_PROGRAM

    def prepare(self, task: Mapping[str, Any], controller: Any) -> None:
        """在支持的 bookmark 任务开始前创建已知空基线。

        输入参数：
            task：可信 canonical task mapping；非 11 任务不做任何动作。
            controller：具有结构化 execute/launch/等待能力的单 VM controller。
        输出返回值：
            无；成功时 Chrome 以空书签状态重启并位于前台。
        异常：
            OSWorldBookmarkEvidenceError：任务身份漂移、动态 home
                或任一固定 guest 动作失败。
        """

        if not isinstance(task, Mapping):
            raise OSWorldBookmarkEvidenceError("bookmark task 身份无效")
        task_id = task.get("task_id")
        if task_id not in OSWORLD_BOOKMARK_TASK_IDS:
            return
        _validate_task_binding(task)
        _validate_prepare_controller(controller)
        bookmarks_path = _bookmarks_path_from_controller(controller)
        try:
            _stop_chrome(controller)
            reset_result = controller.execute(
                [
                    "python3",
                    "-I",
                    "-c",
                    _RESET_BOOKMARKS_GUEST_PROGRAM,
                    bookmarks_path,
                ]
            )
            if not _returncode_is(reset_result, {0}):
                raise OSWorldBookmarkEvidenceError("Chrome Bookmarks 基线重置失败")
            controller.launch(["google-chrome", "--remote-debugging-port=1337"])
            controller.wait_for_chrome_cdp(
                port=_CHROME_CDP_PORT,
                timeout=_CHROME_CDP_TIMEOUT_SECONDS,
            )
            controller.activate_window("Google Chrome")
        except OSWorldBookmarkEvidenceError:
            raise
        except Exception:
            raise OSWorldBookmarkEvidenceError(
                "Chrome Bookmarks 基线重置失败"
            ) from None

    def capture(
        self,
        protocol_id: str,
        controller: Any,
    ) -> tuple[ChromeBookmarksObservation, ...]:
        """同步 Chrome 落盘后读取一台 VM 的完整书签快照。

        输入参数：
            protocol_id：必须精确等于版本化 Chrome Bookmarks 协议。
            controller：当前仍存活的单 VM controller。
        输出返回值：
            仅含当前 VM 一个不可变 observation 的 tuple。
        异常：
            OSWorldBookmarkEvidenceError：协议、controller、落盘、有界读取
                或 JSON 闭集任一无法可靠确定。
        """

        if protocol_id != CHROME_BOOKMARKS_PROTOCOL_ID:
            raise OSWorldBookmarkEvidenceError("Chrome Bookmarks protocol 不受支持")
        _validate_capture_controller(controller)
        bookmarks_path = _bookmarks_path_from_controller(controller)
        try:
            _stop_chrome(controller)
            raw_payload = controller.collect_file_bytes(
                bookmarks_path,
                max_bytes=BOOKMARKS_MAX_FILE_BYTES,
                max_response_bytes=BOOKMARKS_MAX_RESPONSE_BYTES,
                timeout_seconds=_BOOKMARKS_READ_TIMEOUT_SECONDS,
            )
        except OSWorldBookmarkEvidenceError:
            raise
        except Exception:
            raise OSWorldBookmarkEvidenceError(
                "Chrome Bookmarks 无法可靠读取"
            ) from None
        return (parse_chrome_bookmarks_json(raw_payload),)


def parse_chrome_bookmarks_json(raw_payload: bytes) -> ChromeBookmarksObservation:
    """严格解析有界 Chrome Bookmarks JSON 并投影 URL 节点。

    输入参数：
        raw_payload：从固定 profile 以 4 MiB 上限读得的原始 bytes。
    输出返回值：
        保留 root key 与完整 folder path 的不可变完整快照；
        与旧最终协议一样，仅对完全相同的 URL+路径去重。
    异常：
        OSWorldBookmarkEvidenceError：类型、UTF-8、JSON、schema 或资源上限失效。
    """

    if not isinstance(raw_payload, bytes) or not (
        0 < len(raw_payload) <= BOOKMARKS_MAX_FILE_BYTES
    ):
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks 文件大小无效")
    try:
        text = raw_payload.decode("utf-8", "strict")
        payload = json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks JSON 无效") from None
    if not isinstance(payload, dict):
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks schema 无效")
    roots = payload.get("roots")
    if not isinstance(roots, dict) or not 1 <= len(roots) <= _MAX_ROOTS:
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks roots schema 无效")
    version = payload.get("version")
    if version is not None and (
        not isinstance(version, int) or isinstance(version, bool) or version < 0
    ):
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks version schema 无效")
    checksum = payload.get("checksum")
    if checksum is not None:
        _validate_bounded_string(
            checksum,
            max_bytes=1024,
            allow_empty=True,
            label="checksum",
        )

    state = _ParseState()
    for root_key, root_node in roots.items():
        _validate_bounded_string(
            root_key,
            max_bytes=_MAX_FOLDER_COMPONENT_BYTES,
            allow_empty=False,
            label="root",
        )
        if not isinstance(root_node, dict):
            raise OSWorldBookmarkEvidenceError("Chrome Bookmarks root node schema 无效")
        if root_node.get("type") != "folder":
            raise OSWorldBookmarkEvidenceError("Chrome Bookmarks root node schema 无效")
        _walk_bookmark_node(
            root_node,
            folder_path=(root_key,),
            root_node=True,
            state=state,
        )
    return ChromeBookmarksObservation(
        records=tuple(state.records),
        complete=True,
    )


def _walk_bookmark_node(
    node: dict[str, Any],
    *,
    folder_path: tuple[str, ...],
    root_node: bool,
    state: _ParseState,
) -> None:
    """按 Chrome ``roots`` 树结构严格遍历一个 folder/URL 节点。

    输入参数：
        node：当前已解析 JSON object。
        folder_path：从 root key 开始的当前父文件夹路径。
        root_node：当前节点是否为 ``roots`` 的直接值。
        state：全局节点计数、去重集与记录容器。
    输出返回值：
        无；合法 URL 投影追加到 ``state.records``。
    异常：
        OSWorldBookmarkEvidenceError：节点 schema、字符串或资源上限无效。
    """

    state.node_count += 1
    if state.node_count > _MAX_TOTAL_NODES:
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks 节点资源上限无效")
    if not 1 <= len(folder_path) <= _MAX_FOLDER_DEPTH:
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks 文件夹深度无效")
    node_type = node.get("type")
    name = node.get("name", "")
    _validate_bounded_string(
        name,
        max_bytes=(
            _MAX_FOLDER_COMPONENT_BYTES
            if node_type == "folder"
            else _MAX_BOOKMARK_NAME_BYTES
        ),
        allow_empty=root_node or node_type == "url",
        label="node name",
    )
    if node_type == "url":
        if "children" in node:
            raise OSWorldBookmarkEvidenceError("Chrome Bookmarks URL node schema 无效")
        url = node.get("url")
        _validate_bounded_string(
            url,
            max_bytes=_MAX_URL_BYTES,
            allow_empty=False,
            label="URL",
        )
        key = (url, folder_path)
        if key in state.seen:
            return
        if len(state.records) >= _MAX_BOOKMARK_RECORDS:
            raise OSWorldBookmarkEvidenceError("Chrome Bookmarks record 资源上限无效")
        state.seen.add(key)
        state.records.append(ChromeBookmarkRecord(url=url, folder_path=folder_path))
        return
    if node_type != "folder" or "url" in node:
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks node type 无效")
    children = node.get("children")
    if not isinstance(children, list):
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks folder node schema 无效")
    next_path = folder_path
    if not root_node:
        next_path = (*folder_path, name)
        if len(next_path) > _MAX_FOLDER_DEPTH:
            raise OSWorldBookmarkEvidenceError("Chrome Bookmarks 文件夹深度无效")
    for child in children:
        if not isinstance(child, dict):
            raise OSWorldBookmarkEvidenceError(
                "Chrome Bookmarks child node schema 无效"
            )
        _walk_bookmark_node(
            child,
            folder_path=next_path,
            root_node=False,
            state=state,
        )


def _strict_object_pairs(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    """将 JSON object pair 转为 dict 并拒绝重复键。

    输入参数：
        pairs：``json.loads`` 按原顺序传入的键值序列。
    输出返回值：
        保留输入顺序的唯一键字典。
    异常：
        ValueError：任一键重复，防止 parser 差异导致证据歧义。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    """拒绝 JSON 标准之外的 NaN/Infinity 常量。

    输入参数：
        value：Python JSON decoder 观察到的非标准常量文本。
    输出返回值：
        无；本函数始终抛出 ``ValueError``。
    """

    del value
    raise ValueError("non-standard JSON constant")


def _validate_bounded_string(
    value: Any,
    *,
    max_bytes: int,
    allow_empty: bool,
    label: str,
) -> None:
    """验证一个 JSON 字符串的类型、规范形式与 UTF-8 上限。

    输入参数：
        value：待验证的不可信值。
        max_bytes：严格 UTF-8 最大字节数。
        allow_empty：是否允许空字符串。
        label：只用于固定 schema 错误类别，不含数据。
    输出返回值：
        无；全部条件成立即返回。
    异常：
        OSWorldBookmarkEvidenceError：类型、空白、控制字符或字节上限无效。
    """

    if not isinstance(value, str):
        raise OSWorldBookmarkEvidenceError(f"Chrome Bookmarks {label} schema 无效")
    try:
        encoded_size = len(value.encode("utf-8", "strict"))
    except UnicodeError:
        raise OSWorldBookmarkEvidenceError(
            f"Chrome Bookmarks {label} schema 无效"
        ) from None
    if (
        (not allow_empty and not value)
        or value != value.strip()
        or encoded_size > max_bytes
        or any(not character.isprintable() for character in value)
    ):
        raise OSWorldBookmarkEvidenceError(f"Chrome Bookmarks {label} schema 无效")


def _validate_task_binding(task: Mapping[str, Any]) -> None:
    """验证 bookmark 任务的完整正式 runtime 身份绑定。

    输入参数：
        task：已按 task_id 命中 11 任务目录的 mapping。
    输出返回值：
        无；ID、UID、来源、类型、标签与 evaluator 精确匹配时返回。
    异常：
        OSWorldBookmarkEvidenceError：任一身份字段缺失或漂移。
    """

    task_id = task.get("task_id")
    binding = (
        OSWORLD_BOOKMARK_TASK_BINDINGS.get(task_id)
        if isinstance(task_id, str)
        else None
    )
    if binding is None or any(
        task.get(field) != getattr(binding, field)
        for field in (
            "task_id",
            "task_uid",
            "task_source",
            "task_type",
            "task_tag",
            "evaluator_path",
        )
    ):
        raise OSWorldBookmarkEvidenceError("bookmark task 身份绑定无效")


def _bookmarks_path_from_controller(controller: Any) -> str:
    """从 controller 动态 Desktop 结果推导固定 Chrome profile 路径。

    输入参数：
        controller：提供 ``get_desktop_path`` 的单 VM controller。
    输出返回值：
        ``<dynamic-home>/.config/google-chrome/Default/Bookmarks`` 规范绝对路径。
    异常：
        OSWorldBookmarkEvidenceError：Desktop 类型、名称、规范性或 home 层级无效。
    """

    try:
        desktop_value = controller.get_desktop_path()
    except Exception:
        raise OSWorldBookmarkEvidenceError("guest home 无法可靠推导") from None
    if (
        not isinstance(desktop_value, str)
        or not desktop_value
        or "\x00" in desktop_value
        or desktop_value.endswith("/")
    ):
        raise OSWorldBookmarkEvidenceError("guest home 路径无效")
    desktop_path = PurePosixPath(desktop_value)
    if (
        not desktop_path.is_absolute()
        or ".." in desktop_path.parts
        or desktop_path.name != "Desktop"
        or desktop_path.parent == PurePosixPath("/")
        or str(desktop_path) != desktop_value
    ):
        raise OSWorldBookmarkEvidenceError("guest home 路径无效")
    return str(desktop_path.parent / _BOOKMARKS_RELATIVE_PATH)


def _stop_chrome(controller: Any) -> None:
    """请求 Chrome 正常退出并等待书签状态完整落盘。

    输入参数：
        controller：提供结构化 execute 与退出等待的单 VM controller。
    输出返回值：
        无；``pkill`` 返回 0（已通知）或 1（本已退出）均有效。
    异常：
        OSWorldBookmarkEvidenceError：返回码或退出等待不可靠。
    """

    try:
        stop_result = controller.execute(["pkill", "chrome"])
        if not _returncode_is(stop_result, {0, 1}):
            raise OSWorldBookmarkEvidenceError("Chrome 书签同步关闭失败")
        controller.wait_for_chrome_exit(timeout=_CHROME_EXIT_TIMEOUT_SECONDS)
    except OSWorldBookmarkEvidenceError:
        raise
    except Exception:
        raise OSWorldBookmarkEvidenceError("Chrome 书签同步关闭失败") from None


def _returncode_is(result: Any, accepted: set[int]) -> bool:
    """验证 controller 命令结果的 returncode 属于固定闭集。

    输入参数：
        result：不可信 controller 命令结果。
        accepted：当前固定动作允许的整数返回码集。
    输出返回值：
        returncode 为非 bool 整数且位于闭集时返回 ``True``。
    """

    returncode = getattr(result, "returncode", None)
    return (
        isinstance(returncode, int)
        and not isinstance(returncode, bool)
        and returncode in accepted
    )


def _validate_prepare_controller(controller: Any) -> None:
    """在基线副作用前验证 prepare 所需全部 controller 窄接口。

    输入参数：
        controller：待用于书签基线重置的对象。
    输出返回值：
        无；全部方法可调时返回。
    异常：
        OSWorldBookmarkEvidenceError：任一窄能力缺失。
    """

    _validate_controller_methods(
        controller,
        (
            "get_desktop_path",
            "execute",
            "wait_for_chrome_exit",
            "launch",
            "wait_for_chrome_cdp",
            "activate_window",
        ),
    )


def _validate_capture_controller(controller: Any) -> None:
    """在评价副作用前验证 capture 所需全部 controller 窄接口。

    输入参数：
        controller：待用于书签落盘与有界读取的对象。
    输出返回值：
        无；全部方法可调时返回。
    异常：
        OSWorldBookmarkEvidenceError：任一窄能力缺失。
    """

    _validate_controller_methods(
        controller,
        (
            "get_desktop_path",
            "execute",
            "wait_for_chrome_exit",
            "collect_file_bytes",
        ),
    )


def _validate_controller_methods(
    controller: Any,
    method_names: Sequence[str],
) -> None:
    """验证 controller 上一组固定方法均可调。

    输入参数：
        controller：待验证的外部对象。
        method_names：该阶段允许使用的方法名闭集。
    输出返回值：
        无；每个属性均可调时返回。
    异常：
        OSWorldBookmarkEvidenceError：任一属性不可调。
    """

    if any(
        not callable(getattr(controller, method_name, None))
        for method_name in method_names
    ):
        raise OSWorldBookmarkEvidenceError("Chrome Bookmarks controller 窄接口无效")


__all__ = [
    "BOOKMARKS_MAX_FILE_BYTES",
    "BOOKMARKS_MAX_RESPONSE_BYTES",
    "OSWorldBookmarkEvidenceError",
    "OSWorldChromeBookmarkEvidenceSource",
    "parse_chrome_bookmarks_json",
]
