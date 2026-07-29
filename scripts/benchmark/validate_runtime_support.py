#!/usr/bin/env python3
"""独立校验 runtime-support-v1 与 canonical release 的一致性。"""

from __future__ import annotations

import argparse
from pathlib import Path

from runtime_support_manifest import (
    DEFAULT_OUTPUT_PATH,
    validate_runtime_support_manifest,
)


def _parse_arguments() -> argparse.Namespace:
    """解析独立 validator 的命令行参数。

    输入参数：
        无；参数从当前进程命令行读取。
    输出返回值：
        包含仓库根目录和可选清单路径的参数对象。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="ParaGUIBench 仓库根目录",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="待校验清单；相对路径按仓库根目录解析",
    )
    return parser.parse_args()


def main() -> int:
    """运行只读 validator 并输出无敏感值的汇总。

    输入参数：
        无；使用 ``_parse_arguments`` 返回的命令行参数。
    输出返回值：
        校验通过返回 0，发现任一错误返回 1。
    """

    arguments = _parse_arguments()
    repo_root = arguments.repo_root.resolve()
    manifest_path = arguments.manifest
    if manifest_path is None:
        manifest_path = repo_root / DEFAULT_OUTPUT_PATH
    elif not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path

    result = validate_runtime_support_manifest(repo_root, manifest_path)
    if result.ok:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in result.status_counts.items()
        )
        print(
            f"runtime-support-v1 valid: tasks={result.task_count}; {counts}"
        )
        return 0

    for error in result.errors:
        print(f"ERROR: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
