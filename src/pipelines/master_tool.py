"""
Master Table CLI 工具。

子命令:
    rebuild-reports     重新渲染 xlsx/md 子表报告
    export-pending      导出待重跑任务 ID → txt
    mark <task_id>      单任务标记
    mark-batch <file>   从 txt 批量标记
    show <task_id>      显示任务所有匹配行
    import-run <dir>    从历史 ablation 目录导入
    remove <task_id>    删除记录

用法参见 docs/superpowers/specs/2026-04-13-master-table-design.md §6。
"""

import argparse
import json
import os
import sys
from typing import List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import master_table as mt


def _add_scope_args(p: argparse.ArgumentParser) -> None:
    """--mode / --condition / --pipeline 过滤参数。"""
    p.add_argument("--mode", type=str, default=None)
    p.add_argument("--condition", type=str, default=None)
    p.add_argument("--pipeline", type=str, default=None)


def _cmd_rebuild_reports(_args) -> int:
    """重新生成子表报告。"""
    try:
        import master_report
    except ImportError:
        print("master_report 模块尚未实现（见 Plan Task 7/8）", file=sys.stderr)
        return 2
    out_dir = master_report.rebuild_reports()
    print(f"报告已生成: {out_dir}")
    return 0


def _cmd_export_pending(args) -> int:
    """导出待重跑 task_id。"""
    task_ids = mt.export_pending(
        mode=args.mode,
        condition=args.condition,
        pipelines=args.pipelines,
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for tid in task_ids:
                f.write(tid + "\n")
        print(f"共 {len(task_ids)} 个任务 → {args.output}")
    else:
        for tid in task_ids:
            print(tid)
    return 0


def _cmd_mark(args) -> int:
    kwargs = _mark_kwargs_from_args(args)
    n = mt.mark(
        task_id=args.task_id,
        mode=args.mode, condition=args.condition, pipeline=args.pipeline,
        **kwargs,
    )
    print(f"Updated {n} row(s)")
    return 0 if n > 0 else 1


def _cmd_mark_batch(args) -> int:
    with open(args.file, "r", encoding="utf-8") as f:
        ids = [line.strip() for line in f if line.strip()]
    kwargs = _mark_kwargs_from_args(args)
    n = mt.mark_batch(
        task_ids=ids,
        mode=args.mode, condition=args.condition, pipeline=args.pipeline,
        **kwargs,
    )
    print(f"Updated {n} row(s) across {len(ids)} task IDs")
    return 0 if n > 0 else 1


def _mark_kwargs_from_args(args) -> dict:
    """把 argparse 的 --needs-rerun 等开关翻译成 mt.mark 的 kwargs。"""
    kw = {}
    for field in ("needs_rerun", "lock", "error", "empty"):
        val = getattr(args, field, None)
        if val:
            kw[field] = True
    for field in ("clear_error", "clear_needs_rerun",
                  "clear_empty", "clear_note"):
        if getattr(args, field, False):
            kw[field] = True
    if getattr(args, "note", None) is not None:
        kw["note"] = args.note
    return kw


def _cmd_show(args) -> int:
    rows = mt.show(task_id=args.task_id, mode=args.mode,
                   condition=args.condition, pipeline=args.pipeline)
    if not rows:
        print("(no matching row)")
        return 1
    for r in rows:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def _cmd_remove(args) -> int:
    n = mt.remove(task_id=args.task_id, mode=args.mode,
                  condition=args.condition, pipeline=args.pipeline)
    print(f"Removed {n} row(s)")
    return 0 if n > 0 else 1


def _cmd_import_run(args) -> int:
    try:
        import master_report
    except ImportError:
        print("master_report 模块尚未实现（见 Plan Task 11）", file=sys.stderr)
        return 2
    n = master_report.import_run(args.dir)
    print(f"Imported {n} row(s) from {args.dir}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Master Table CLI",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_rebuild = sub.add_parser("rebuild-reports")
    p_rebuild.set_defaults(func=_cmd_rebuild_reports)

    p_export = sub.add_parser("export-pending")
    _add_scope_args(p_export)
    p_export.add_argument("--pipelines", nargs="+", default=None)
    p_export.add_argument("-o", "--output", type=str, default=None)
    p_export.set_defaults(func=_cmd_export_pending)

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("task_id")
    _add_scope_args(p_mark)
    _add_mark_flags(p_mark)
    p_mark.set_defaults(func=_cmd_mark)

    p_batch = sub.add_parser("mark-batch")
    p_batch.add_argument("file")
    _add_scope_args(p_batch)
    _add_mark_flags(p_batch)
    p_batch.set_defaults(func=_cmd_mark_batch)

    p_show = sub.add_parser("show")
    p_show.add_argument("task_id")
    _add_scope_args(p_show)
    p_show.set_defaults(func=_cmd_show)

    p_remove = sub.add_parser("remove")
    p_remove.add_argument("task_id")
    _add_scope_args(p_remove)
    p_remove.set_defaults(func=_cmd_remove)

    p_import = sub.add_parser("import-run")
    p_import.add_argument("dir")
    p_import.set_defaults(func=_cmd_import_run)

    return p


def _add_mark_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--needs-rerun", dest="needs_rerun", action="store_true")
    p.add_argument("--lock", action="store_true")
    p.add_argument("--error", action="store_true")
    p.add_argument("--empty", action="store_true")
    p.add_argument("--clear-error", dest="clear_error", action="store_true")
    p.add_argument("--clear-needs-rerun", dest="clear_needs_rerun",
                   action="store_true")
    p.add_argument("--clear-empty", dest="clear_empty", action="store_true")
    p.add_argument("--clear-note", dest="clear_note", action="store_true")
    p.add_argument("--note", type=str, default=None)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
