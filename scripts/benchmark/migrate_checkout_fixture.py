#!/usr/bin/env python3
"""将 WebMall checkout 任务迁移为版本化合成 fixture 引用。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


FIXTURE_ID = "webmall.checkout-profile.synthetic-public.v1"
FIXTURE_PATH = "benchmark/fixtures/webmall/checkout-profile-v1.json"
TASK_PATTERNS = (
    "Operation-OnlineShopping-Checkout-*.json",
    "Operation-OnlineShopping-EndToEnd-*.json",
)
PROFILE_BLOCK = re.compile(
    r"Pay via credit card using the following information: .*?\."
    r"\n\n(?=After completing)",
    flags=re.DOTALL,
)
PROFILE_TEMPLATE = (
    "Pay via credit card using the following checkout information: "
    "{{checkout_profile}}.\n\n"
)


def _load_json(path: Path) -> dict[str, Any]:
    """读取 UTF-8 JSON object。

    输入参数：
        path：待读取的 JSON 文件路径。
    输出返回值：
        解析后的 JSON object。
    """

    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path} 的 JSON 根节点必须是 object")
    return value


def _dump_json(path: Path, value: dict[str, Any]) -> None:
    """以确定性格式写入 UTF-8 JSON object。

    输入参数：
        path：目标 JSON 文件路径。
        value：待序列化的 JSON object。
    输出返回值：
        无；文件使用两空格缩进并以换行结尾。
    """

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    """计算文件的 SHA-256 摘要。

    输入参数：
        path：待计算摘要的文件路径。
    输出返回值：
        小写十六进制 SHA-256 字符串。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_checkout_tasks(repo_root: Path) -> list[Path]:
    """发现 checkout 与 end-to-end canonical task。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
    输出返回值：
        按文件名排序且去重后的 16 个任务路径。
    """

    task_root = repo_root / "benchmark" / "tasks"
    task_paths = {
        task_path
        for pattern in TASK_PATTERNS
        for task_path in task_root.glob(pattern)
    }
    return sorted(task_paths)


def migrate_task(task: dict[str, Any]) -> dict[str, Any]:
    """把单个旧 checkout task 转换为模板与 fixture 引用。

    输入参数：
        task：canonical task JSON object。
    输出返回值：
        新的 task object；输入对象不会被原地修改。
    """

    if "instruction_template" in task:
        migrated = dict(task)
        if migrated.get("fixture_ref") != {
            "binding": "checkout_profile",
            "fixture_id": FIXTURE_ID,
        }:
            raise ValueError(f"{task.get('task_id')} 使用了未知 checkout fixture")
        if "{{checkout_profile}}" not in str(migrated["instruction_template"]):
            raise ValueError(f"{task.get('task_id')} 的模板缺少 checkout_profile")
        migrated.pop("user_details", None)
        migrated.pop("payment_info", None)
        migrated.pop("instruction", None)
        return migrated

    instruction = task.get("instruction")
    if not isinstance(instruction, str):
        raise ValueError(f"{task.get('task_id')} 缺少 instruction")
    instruction_template, replacement_count = PROFILE_BLOCK.subn(
        PROFILE_TEMPLATE,
        instruction,
        count=1,
    )
    if replacement_count != 1:
        raise ValueError(f"{task.get('task_id')} 的 checkout profile 块不唯一")

    migrated: dict[str, Any] = {}
    for key, value in task.items():
        if key == "instruction":
            migrated["instruction_template"] = instruction_template
            migrated["fixture_ref"] = {
                "binding": "checkout_profile",
                "fixture_id": FIXTURE_ID,
            }
        elif key not in {"user_details", "payment_info"}:
            migrated[key] = value
    return migrated


def migrate_repository(repo_root: Path, *, check: bool = False) -> list[str]:
    """迁移仓库内 16 个任务并同步 release-v1 文件摘要。

    输入参数：
        repo_root：ParaGUIBench 仓库根目录。
        check：为 ``True`` 时只检查是否需要改写，不修改文件。
    输出返回值：
        会发生内容变化的仓库相对路径列表。
    """

    task_paths = discover_checkout_tasks(repo_root)
    if len(task_paths) != 16:
        raise ValueError(f"预期 16 个 checkout 任务，实际发现 {len(task_paths)} 个")

    changed_paths: list[str] = []
    migrated_by_path: dict[Path, dict[str, Any]] = {}
    for task_path in task_paths:
        original = _load_json(task_path)
        migrated = migrate_task(original)
        migrated_by_path[task_path] = migrated
        expected_text = json.dumps(migrated, ensure_ascii=False, indent=2) + "\n"
        if task_path.read_text(encoding="utf-8") != expected_text:
            changed_paths.append(str(task_path.relative_to(repo_root)))
            if not check:
                _dump_json(task_path, migrated)

    manifest_path = repo_root / "benchmark" / "manifests" / "release-v1.json"
    manifest = _load_json(manifest_path)
    task_entries = {
        entry["task_id"]: entry
        for entry in manifest.get("tasks", [])
        if isinstance(entry, dict) and isinstance(entry.get("task_id"), str)
    }
    for task_path, migrated in migrated_by_path.items():
        task_id = migrated["task_id"]
        entry = task_entries.get(task_id)
        if entry is None:
            raise ValueError(f"release-v1 缺少任务 {task_id}")
        digest = (
            hashlib.sha256(
                (json.dumps(migrated, ensure_ascii=False, indent=2) + "\n").encode(
                    "utf-8"
                )
            ).hexdigest()
            if check
            else _sha256(task_path)
        )
        if entry.get("sha256") != digest:
            entry["sha256"] = digest

    fixture_path = repo_root / FIXTURE_PATH
    fixture_digest = _sha256(fixture_path)
    fixture_entries = [
        entry
        for entry in manifest.get("fixtures", [])
        if isinstance(entry, dict) and entry.get("fixture_id") == FIXTURE_ID
    ]
    if len(fixture_entries) != 1:
        raise ValueError("release-v1 必须且只能声明一个 checkout fixture")
    if fixture_entries[0].get("sha256") != fixture_digest:
        fixture_entries[0]["sha256"] = fixture_digest

    expected_manifest_text = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    if manifest_path.read_text(encoding="utf-8") != expected_manifest_text:
        changed_paths.append(str(manifest_path.relative_to(repo_root)))
        if not check:
            _dump_json(manifest_path, manifest)
    return changed_paths


def _parse_args() -> argparse.Namespace:
    """解析迁移脚本命令行参数。

    输入参数：
        无；参数来自当前进程命令行。
    输出返回值：
        包含仓库根目录和只检查开关的参数对象。
    """

    parser = argparse.ArgumentParser(
        description="迁移 WebMall checkout 任务为版本化 fixture 引用"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="ParaGUIBench 仓库根目录",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查迁移是否已完成，不修改文件",
    )
    return parser.parse_args()


def main() -> int:
    """执行迁移或幂等性检查。

    输入参数：
        无；通过命令行参数确定仓库与模式。
    输出返回值：
        已满足目标状态返回 ``0``；check 模式发现差异返回 ``1``。
    """

    args = _parse_args()
    changed_paths = migrate_repository(args.repo_root.resolve(), check=args.check)
    if args.check and changed_paths:
        print(f"checkout fixture migration pending: {len(changed_paths)} file(s)")
        return 1
    if changed_paths:
        print(f"checkout fixture migration updated: {len(changed_paths)} file(s)")
    else:
        print("checkout fixture migration already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
