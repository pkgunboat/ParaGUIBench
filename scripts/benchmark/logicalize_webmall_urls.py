#!/usr/bin/env python3
"""把 WebMall canonical task 中的部署 URL 一次性迁移为 logical URL。

脚本不会输出发现的原始 host/origin。默认只执行验证和 dry-run；只有显式
传入 ``--write`` 才会原子改写 91 个 WebMall task，并同步 release manifest
中的 SHA-256。
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit


EXPECTED_WEBMALL_TASK_COUNT = 91
EXPECTED_STORE_COUNT = 4
MIGRATED_FIELDS = ("instruction", "answer", "expected_urls")
HTTP_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
LOGICAL_ORIGIN_PATTERN = re.compile(
    r"webmall://(?P<store_id>[A-Za-z0-9][A-Za-z0-9-]*)"
)


class LogicalizationError(RuntimeError):
    """表示 WebMall task 无法在不泄露部署地址的前提下安全迁移。"""


def _load_json(path: Path) -> Any:
    """读取 UTF-8 JSON 文件。

    输入参数：
        path：待读取文件路径。
    输出返回值：
        解析后的 JSON 兼容对象。
    """

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _json_bytes(value: Any) -> bytes:
    """把 JSON 对象序列化为稳定、可读的 UTF-8 字节。

    输入参数：
        value：待序列化的 JSON 兼容对象。
    输出返回值：
        使用两空格缩进、保留非 ASCII 字符并以换行结尾的字节串。
    """

    return (
        json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    """在目标目录内通过临时文件原子替换公开数据文件。

    输入参数：
        path：最终文件路径。
        content：完整的新文件内容。
    输出返回值：
        无；成功时目标路径已完整替换，失败时不会留下半写文件。
    """

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _candidate_origin(candidate: str) -> str | None:
    """从一个 HTTP URL 候选中提取不含路径的 origin。

    输入参数：
        candidate：正则从 instruction 中提取的 URL 候选。
    输出返回值：
        协议与 authority 组成的 origin；候选不合法时返回 ``None``。
    """

    parts = urlsplit(candidate.rstrip(".,;:)]}"))
    if parts.scheme not in {"http", "https"} or parts.hostname is None:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _ordered_origins(text: str) -> tuple[str, ...]:
    """按首次出现顺序收集 instruction 中互异的 HTTP(S) origin。

    输入参数：
        text：一个 WebMall task 的 instruction。
    输出返回值：
        去重且保序的 origin 元组；函数不会打印或记录这些值。
    """

    origins: list[str] = []
    seen: set[str] = set()
    for match in HTTP_URL_PATTERN.finditer(text):
        origin = _candidate_origin(match.group(0))
        if origin is not None and origin not in seen:
            origins.append(origin)
            seen.add(origin)
    return tuple(origins)


def _is_private_origin(origin: str) -> bool:
    """判断一个 origin 是否直接使用私有 IP。

    输入参数：
        origin：已去除路径的 HTTP(S) origin。
    输出返回值：
        主机是私有 IP 时返回 ``True``；域名或无效地址返回 ``False``。
    """

    hostname = urlsplit(origin).hostname
    if hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False


def _replace_origins(value: Any, origin_map: dict[str, str]) -> Any:
    """递归复制 JSON 值并替换其中已确认的 WebMall origin。

    输入参数：
        value：instruction、answer 或 expected_urls 的 JSON 值。
        origin_map：runtime origin 到 ``webmall://store-N`` 的内存映射。
    输出返回值：
        替换后的深层副本；非字符串标量保持原值。
    """

    if isinstance(value, str):
        result = value
        for origin in sorted(origin_map, key=len, reverse=True):
            result = result.replace(origin, origin_map[origin])
        return result
    if isinstance(value, list):
        return [_replace_origins(item, origin_map) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_origins(nested, origin_map)
            for key, nested in value.items()
        }
    return value


def _sha256_bytes(content: bytes) -> str:
    """计算内存字节串的 SHA-256。

    输入参数：
        content：待摘要的完整文件内容。
    输出返回值：
        小写十六进制 SHA-256 字符串。
    """

    return hashlib.sha256(content).hexdigest()


def _validate_gold_contract(task: dict[str, Any]) -> None:
    """验证 WebMall answer 与 expected_urls 的精确列表契约未被破坏。

    输入参数：
        task：已经 logicalize 的 WebMall task。
    输出返回值：
        无；契约满足时正常返回。
    异常：
        LogicalizationError：字段类型异常或两个 gold 表示不一致。
    """

    answer = task.get("answer")
    expected_urls = task.get("expected_urls")
    if (
        not isinstance(answer, str)
        or not isinstance(expected_urls, list)
        or answer.split("###") != expected_urls
    ):
        raise LogicalizationError(
            "WebMall answer 与 expected_urls 契约不一致"
        )
    expected_ids = {
        match.group("store_id")
        for url in expected_urls
        if isinstance(url, str)
        for match in LOGICAL_ORIGIN_PATTERN.finditer(url)
    }
    if not expected_ids or not expected_ids <= {
        f"store-{index}" for index in range(1, EXPECTED_STORE_COUNT + 1)
    }:
        raise LogicalizationError("WebMall gold 引用了未知 logical store")


def logicalize_repository(
    repo_root: Path,
    *,
    write: bool,
) -> tuple[int, int]:
    """验证并按需迁移仓库内全部 WebMall canonical task。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        write：``True`` 时原子写入 task 和 manifest；否则仅 dry-run。
    输出返回值：
        ``(WebMall task 数, 实际发生内容变化的 task 数)``。
    异常：
        LogicalizationError：任务数量、四店顺序、私有地址或 gold 契约异常。
    """

    task_root = repo_root / "benchmark" / "tasks"
    manifest_path = repo_root / "benchmark" / "manifests" / "release-v1.json"
    records: list[tuple[Path, dict[str, Any]]] = []
    for task_path in sorted(task_root.glob("*.json")):
        task = _load_json(task_path)
        if isinstance(task, dict) and task.get("task_source") == "WebMall":
            records.append((task_path, task))
    if len(records) != EXPECTED_WEBMALL_TASK_COUNT:
        raise LogicalizationError(
            "WebMall canonical task 数量不符合 release-v1 契约"
        )

    origin_orders = [
        _ordered_origins(str(task.get("instruction", "")))
        for _, task in records
    ]
    source_mode = all(origin_orders)
    logical_mode = all(
        not order
        and LOGICAL_ORIGIN_PATTERN.search(str(task.get("instruction", "")))
        for order, (_, task) in zip(origin_orders, records, strict=True)
    )
    if not source_mode and not logical_mode:
        raise LogicalizationError(
            "WebMall task 同时存在 runtime URL 与 logical URL，拒绝部分迁移"
        )

    if source_mode:
        reference_order = origin_orders[0]
        if len(reference_order) != EXPECTED_STORE_COUNT:
            raise LogicalizationError(
                "WebMall instruction 未形成稳定的四店 origin 顺序"
            )
        if any(order != reference_order for order in origin_orders[1:]):
            raise LogicalizationError(
                "91 个 WebMall task 的四店 origin 顺序不一致"
            )
        if not all(_is_private_origin(origin) for origin in reference_order):
            raise LogicalizationError(
                "来源 task 的 WebMall origin 不满足预期的私有部署边界"
            )
        origin_map = {
            origin: f"webmall://store-{index}"
            for index, origin in enumerate(reference_order, start=1)
        }
    else:
        origin_map = {}

    new_content_by_path: dict[Path, bytes] = {}
    changed_count = 0
    for task_path, task in records:
        migrated = dict(task)
        for field in MIGRATED_FIELDS:
            migrated[field] = _replace_origins(task.get(field), origin_map)
        _validate_gold_contract(migrated)
        content = _json_bytes(migrated)
        new_content_by_path[task_path] = content
        if content != task_path.read_bytes():
            changed_count += 1

    manifest = _load_json(manifest_path)
    entries = manifest.get("tasks")
    if not isinstance(entries, list):
        raise LogicalizationError("release manifest 缺少 tasks 列表")
    for entry in entries:
        if not isinstance(entry, dict):
            raise LogicalizationError("release manifest task 条目类型异常")
        relative_path = entry.get("path")
        if not isinstance(relative_path, str):
            raise LogicalizationError("release manifest task path 类型异常")
        task_path = (repo_root / relative_path).resolve()
        content = new_content_by_path.get(task_path)
        if content is not None:
            entry["sha256"] = _sha256_bytes(content)
    manifest_content = _json_bytes(manifest)

    if write:
        for task_path, content in new_content_by_path.items():
            if content != task_path.read_bytes():
                _atomic_write(task_path, content)
        if manifest_content != manifest_path.read_bytes():
            _atomic_write(manifest_path, manifest_content)

    return len(records), changed_count


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。

    输入参数：
        无；参数来自当前进程命令行。
    输出返回值：
        包含仓库根目录与 ``--write`` 开关的参数对象。
    """

    parser = argparse.ArgumentParser(
        description="安全迁移 WebMall canonical task 的部署 URL"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="ParaGUIBench 仓库根目录",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="通过全部断言后原子写入；默认仅 dry-run",
    )
    return parser.parse_args()


def main() -> int:
    """执行 WebMall URL logicalization 并输出不含地址的计数摘要。

    输入参数：
        无；读取命令行参数。
    输出返回值：
        成功返回 ``0``；契约不满足时抛出异常并由进程返回非零。
    """

    args = _parse_args()
    task_count, changed_count = logicalize_repository(
        args.repo_root.resolve(),
        write=args.write,
    )
    mode = "written" if args.write else "dry-run"
    print(
        f"WebMall logicalization {mode}: "
        f"tasks={task_count}, changed={changed_count}; origins not displayed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
