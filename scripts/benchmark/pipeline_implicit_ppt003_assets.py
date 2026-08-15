#!/usr/bin/env python3
"""生成或检查 PPT-003 正式 pipeline-implicit input/gold 清单。"""

from __future__ import annotations

import argparse
from pathlib import Path

from paraguibench.integrations.pipeline_implicit.verified_assets import (
    check_ppt003_asset_manifest_files,
    write_ppt003_asset_manifest_files,
)


def _parse_arguments() -> argparse.Namespace:
    """解析 generate/check 子命令与仓库根。

    输入参数：
        无；读取当前进程命令行。
    输出返回值：
        包含 ``command`` 与 ``repo_root`` 的 argparse namespace。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser.parse_args()


def main() -> int:
    """执行两份正式 manifest 的生成或逐字节检查。

    输入参数：
        无；使用 ``_parse_arguments`` 返回的命令行参数。
    输出返回值：
        生成成功或检查一致返回 0；检查漂移返回 1。
    """

    arguments = _parse_arguments()
    repo_root = arguments.repo_root.resolve()
    if arguments.command == "generate":
        write_ppt003_asset_manifest_files(repo_root)
        print("PPT-003 asset manifests generated: input=20; gold=32")
        return 0
    if check_ppt003_asset_manifest_files(repo_root):
        print("PPT-003 asset manifests valid: input=20; gold=32")
        return 0
    print("PPT-003 asset manifests drifted")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
