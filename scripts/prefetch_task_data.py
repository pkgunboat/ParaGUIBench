#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Dict, Iterable, Tuple

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_dir = os.path.join(repo_root, "src")

if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from stages.task_data_cache import ensure_task_data_cached, get_task_cache_dir  # noqa: E402

TASKS_DIR = os.path.join(src_dir, "parallel_benchmark", "tasks")


def iter_tasks() -> Iterable[Tuple[str, str, Dict]]:
    for root, _, files in os.walk(TASKS_DIR):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8") as file_obj:
                    config = json.load(file_obj)
            except Exception:
                continue
            yield path, config.get("task_uid", ""), config


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="预下载任务依赖文件到本地 HF 缓存")
    parser.add_argument("--task-type", default="", help="按 task_type 过滤，例如 QA")
    parser.add_argument("--task-tag", default="", help="按 task_tag 过滤，例如 FileOperate")
    parser.add_argument("--task-id-contains", default="", help="按 task_id 子串过滤")
    parser.add_argument("--exclude-task-id-contains", default="", help="排除 task_id 子串")
    parser.add_argument("--task-uid", default="", help="仅预下载指定 task_uid")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个任务")
    parser.add_argument("--force", action="store_true", help="忽略现有缓存并重新下载")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("prefetch_task_data")

    total = 0
    success = 0
    failed = 0

    for _, task_uid, config in iter_tasks():
        task_id = config.get("task_id", "")
        prepare_url = config.get("prepare_script_path", "")
        if not task_uid or not prepare_url:
            continue
        if args.task_uid and task_uid != args.task_uid:
            continue
        if args.task_type and config.get("task_type", "") != args.task_type:
            continue
        if args.task_tag and config.get("task_tag", "") != args.task_tag:
            continue
        if args.task_id_contains and args.task_id_contains not in task_id:
            continue
        if args.exclude_task_id_contains and args.exclude_task_id_contains in task_id:
            continue

        total += 1
        log.info("预下载 %s (%s)", task_id, task_uid)
        ok = ensure_task_data_cached(
            task_uid,
            prepare_url,
            log=log,
            force=args.force,
        )
        if ok:
            success += 1
            log.info("  ✓ 缓存目录: %s", get_task_cache_dir(task_uid))
        else:
            failed += 1
            log.info("  ✗ 下载失败")

        if args.limit and total >= args.limit:
            break

    log.info("")
    log.info("完成: total=%d success=%d failed=%d", total, success, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
