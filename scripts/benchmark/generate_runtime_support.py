#!/usr/bin/env python3
"""独立生成确定性的 runtime-support-v1 preview 清单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_support_manifest import (
    DEFAULT_OUTPUT_PATH,
    build_runtime_support_manifest,
)


def _parse_arguments() -> argparse.Namespace:
    """解析独立生成器的命令行参数。

    输入参数：
        无；参数从当前进程命令行读取。
    输出返回值：
        包含仓库根目录和可选输出路径的参数对象。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="ParaGUIBench 仓库根目录",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="输出路径；相对路径按仓库根目录解析",
    )
    return parser.parse_args()


def main() -> int:
    """生成清单并只输出任务总数，不回显任何任务正文。

    输入参数：
        无；使用 ``_parse_arguments`` 返回的命令行参数。
    输出返回值：
        成功写出清单返回 0；读取或写入失败时由异常终止。
    """

    arguments = _parse_arguments()
    repo_root = arguments.repo_root.resolve()
    output_path = arguments.output
    if output_path is None:
        output_path = repo_root / DEFAULT_OUTPUT_PATH
    elif not output_path.is_absolute():
        output_path = repo_root / output_path

    manifest = build_runtime_support_manifest(repo_root)
    output_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "runtime-support-v1 generated: "
        f"tasks={manifest['canonical_task_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
