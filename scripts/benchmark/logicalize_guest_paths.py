#!/usr/bin/env python3
"""把 guest 镜像绝对路径迁移为可部署的目录绑定。

当前 release-v1 仅 Settings-003 需要该迁移。脚本从
``agent_start_context.guest_path`` 推导来源目录，不在源码或输出中保存原始
guest 用户名；默认 dry-run，显式传入 ``--write`` 后才原子写入。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any


TARGET_TASK_ID = "Operation-WebOperate-Settings-003"
BINDING_NAME = "GUEST_SHARED_DIR"
BINDING_PLACEHOLDER = "${GUEST_SHARED_DIR}"


class GuestPathLogicalizationError(RuntimeError):
    """表示 guest path 无法被安全、确定地迁移。"""


def _load_json(path: Path) -> Any:
    """读取 UTF-8 JSON 文件。

    输入参数：
        path：待读取的 task 或 manifest 路径。
    输出返回值：
        解析后的 JSON 兼容对象。
    """

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _json_bytes(value: Any) -> bytes:
    """生成稳定、可读且以换行结尾的 JSON 字节。

    输入参数：
        value：待序列化的 JSON 兼容对象。
    输出返回值：
        两空格缩进并保留 Unicode 的 UTF-8 字节串。
    """

    return (
        json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    """在同目录创建临时文件并原子替换目标。

    输入参数：
        path：最终公开数据文件路径。
        content：要完整写入的新内容。
    输出返回值：
        无；成功时目标完整更新，异常时不会留下半写目标。
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


def _logicalize_task(task: dict[str, Any]) -> dict[str, Any]:
    """生成使用 ``GUEST_SHARED_DIR`` 的 Settings task 副本。

    输入参数：
        task：Settings-003 canonical task。
    输出返回值：
        instruction、guest_path 和 required bindings 一致更新的新字典。
    异常：
        GuestPathLogicalizationError：路径、题面引用或已有绑定不满足契约。
    """

    instruction = task.get("instruction")
    context = task.get("agent_start_context")
    if not isinstance(instruction, str) or not isinstance(context, dict):
        raise GuestPathLogicalizationError(
            "目标 task 缺少 instruction 或 agent_start_context"
        )
    guest_path = context.get("guest_path")
    if not isinstance(guest_path, str):
        raise GuestPathLogicalizationError("目标 task 缺少 guest_path")

    if guest_path.startswith(f"{BINDING_PLACEHOLDER}/"):
        logical_guest_path = guest_path
        logical_instruction = instruction
    else:
        parsed_path = PurePosixPath(guest_path)
        if not parsed_path.is_absolute() or ".." in parsed_path.parts:
            raise GuestPathLogicalizationError(
                "guest_path 不是安全的 POSIX 绝对路径"
            )
        source_directory = str(parsed_path.parent)
        if instruction.count(guest_path) != 1:
            raise GuestPathLogicalizationError(
                "instruction 未唯一引用 agent_start_context.guest_path"
            )
        logical_guest_path = guest_path.replace(
            source_directory,
            BINDING_PLACEHOLDER,
            1,
        )
        logical_instruction = instruction.replace(
            guest_path,
            logical_guest_path,
            1,
        )

    existing_bindings = task.get("required_environment_bindings", [])
    if not isinstance(existing_bindings, list) or not all(
        isinstance(name, str) for name in existing_bindings
    ):
        raise GuestPathLogicalizationError(
            "required_environment_bindings 类型异常"
        )
    bindings = list(existing_bindings)
    if BINDING_NAME not in bindings:
        bindings.append(BINDING_NAME)

    migrated = dict(task)
    migrated["required_environment_bindings"] = bindings
    migrated["instruction"] = logical_instruction
    migrated_context = dict(context)
    migrated_context["guest_path"] = logical_guest_path
    migrated["agent_start_context"] = migrated_context
    return migrated


def logicalize_repository(repo_root: Path, *, write: bool) -> bool:
    """验证并按需迁移 Settings-003 及其 release manifest 摘要。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        write：是否实际原子写入；``False`` 时只执行 dry-run。
    输出返回值：
        task 内容相对磁盘发生变化时返回 ``True``，否则返回 ``False``。
    """

    task_path = (
        repo_root / "benchmark" / "tasks" / f"{TARGET_TASK_ID}.json"
    )
    manifest_path = repo_root / "benchmark" / "manifests" / "release-v1.json"
    task = _load_json(task_path)
    if not isinstance(task, dict):
        raise GuestPathLogicalizationError("目标 task 根节点不是 object")
    migrated_task = _logicalize_task(task)
    task_content = _json_bytes(migrated_task)
    changed = task_content != task_path.read_bytes()

    manifest = _load_json(manifest_path)
    entries = manifest.get("tasks")
    if not isinstance(entries, list):
        raise GuestPathLogicalizationError("release manifest 缺少 tasks 列表")
    matching_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("task_id") == TARGET_TASK_ID
    ]
    if len(matching_entries) != 1:
        raise GuestPathLogicalizationError(
            "release manifest 未唯一引用目标 task"
        )
    matching_entries[0]["sha256"] = hashlib.sha256(task_content).hexdigest()
    manifest_content = _json_bytes(manifest)

    if write:
        if changed:
            _atomic_write(task_path, task_content)
        if manifest_content != manifest_path.read_bytes():
            _atomic_write(manifest_path, manifest_content)
    return changed


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。

    输入参数：
        无；参数来自当前进程命令行。
    输出返回值：
        包含仓库根目录和写入开关的参数对象。
    """

    parser = argparse.ArgumentParser(
        description="迁移 canonical task 中的 guest 绝对路径"
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
        help="通过全部校验后原子写入；默认仅 dry-run",
    )
    return parser.parse_args()


def main() -> int:
    """执行 guest path logicalization 并输出不含路径的摘要。

    输入参数：
        无；读取命令行参数。
    输出返回值：
        成功返回 ``0``，异常由进程转换为非零退出码。
    """

    args = _parse_args()
    changed = logicalize_repository(
        args.repo_root.resolve(),
        write=args.write,
    )
    mode = "written" if args.write else "dry-run"
    print(
        f"guest path logicalization {mode}: "
        f"task={TARGET_TASK_ID}, changed={int(changed)}; path not displayed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
