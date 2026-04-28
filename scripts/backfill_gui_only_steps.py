#!/usr/bin/env python3
"""
Backfill GUI-only step metrics in historical result files.

The old GUI-only pipeline wrote detailed steps to per-task execution_record.json
but left gui_rounds_total/gui_steps_sequential as 0 in *_results.json. This
script reads the execution records, patches those result files, and optionally
regenerates condition/root reports plus ablation_summary.json.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINES_DIR = os.path.join(REPO_ROOT, "src", "pipelines")
if PIPELINES_DIR not in sys.path:
    sys.path.insert(0, PIPELINES_DIR)

from report_generator import (  # noqa: E402
    compute_results_summary,
    enrich_results_with_gui_step_metrics,
    generate_report,
)


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: Any, backup_suffix: str | None) -> None:
    if backup_suffix:
        backup_path = path + backup_suffix
        if not os.path.exists(backup_path):
            shutil.copy2(path, backup_path)

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def _iter_result_files(root: str) -> Iterable[str]:
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith("_results.json"):
                yield os.path.join(dirpath, filename)


def _result_step_pair(result: Dict[str, Any]) -> Tuple[int, int]:
    try:
        total = int(result.get("gui_rounds_total") or 0)
    except (TypeError, ValueError):
        total = 0
    try:
        seq = int(result.get("gui_steps_sequential") or 0)
    except (TypeError, ValueError):
        seq = 0
    return total, seq


def backfill_result_file(
    path: str,
    dry_run: bool,
    backup_suffix: str | None,
) -> Tuple[int, int]:
    data = _load_json(path)
    if not isinstance(data, dict):
        return 0, 0

    before = {
        key: _result_step_pair(value)
        for key, value in data.items()
        if isinstance(value, dict)
    }
    enriched = enrich_results_with_gui_step_metrics(data, os.path.dirname(path))

    changed_tasks = 0
    for key, value in enriched.items():
        if not isinstance(value, dict):
            continue
        if before.get(key) != _result_step_pair(value):
            changed_tasks += 1

    if changed_tasks and not dry_run:
        _write_json(path, enriched, backup_suffix)

    return changed_tasks, len(data)


def _condition_dirs(result_files: List[str]) -> List[str]:
    return sorted({os.path.dirname(path) for path in result_files})


def _ablation_roots(condition_dirs: List[str]) -> List[str]:
    return sorted({os.path.dirname(path) for path in condition_dirs})


def _load_condition_results(condition_dir: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    condition = os.path.basename(condition_dir)
    for filename in os.listdir(condition_dir):
        if not filename.endswith("_results.json"):
            continue
        path = os.path.join(condition_dir, filename)
        try:
            loaded = _load_json(path)
        except Exception:
            continue
        if not isinstance(loaded, dict):
            continue
        for key, value in loaded.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item.setdefault("condition", condition)
            out[f"{filename}:{key}"] = item
    return out


def regenerate_reports(condition_dirs: List[str], dry_run: bool) -> int:
    regenerated = 0
    for condition_dir in condition_dirs:
        results = _load_condition_results(condition_dir)
        if not results:
            continue
        regenerated += 1
        if not dry_run:
            generate_report(results, condition_dir)

    for root in _ablation_roots(condition_dirs):
        results: Dict[str, Dict[str, Any]] = {}
        for condition in sorted(os.listdir(root)):
            condition_dir = os.path.join(root, condition)
            if not os.path.isdir(condition_dir):
                continue
            for key, value in _load_condition_results(condition_dir).items():
                results[f"{condition}:{key}"] = value
        if not results:
            continue
        regenerated += 1
        if not dry_run:
            generate_report(results, root)

    return regenerated


def update_ablation_summaries(
    condition_dirs: List[str],
    dry_run: bool,
    backup_suffix: str | None,
) -> int:
    updated = 0
    for root in _ablation_roots(condition_dirs):
        summary_path = os.path.join(root, "ablation_summary.json")
        if not os.path.isfile(summary_path):
            continue
        try:
            summary = _load_json(summary_path)
        except Exception:
            continue
        if not isinstance(summary, list):
            continue

        changed = False
        by_condition = {
            os.path.basename(condition_dir): condition_dir
            for condition_dir in condition_dirs
            if os.path.dirname(condition_dir) == root
        }
        for condition_report in summary:
            if not isinstance(condition_report, dict):
                continue
            condition = condition_report.get("condition")
            condition_dir = by_condition.get(condition)
            if not condition_dir:
                continue
            pipeline_results = condition_report.get("pipeline_results")
            if not isinstance(pipeline_results, dict):
                continue

            for filename in os.listdir(condition_dir):
                if not filename.endswith("_results.json"):
                    continue
                pipeline = filename.removesuffix("_results.json")
                path = os.path.join(condition_dir, filename)
                try:
                    results = _load_json(path)
                except Exception:
                    continue
                if not isinstance(results, dict):
                    continue
                metrics = compute_results_summary(results, output_dir=condition_dir)
                existing = pipeline_results.setdefault(pipeline, {})
                if not isinstance(existing, dict):
                    existing = {}
                    pipeline_results[pipeline] = existing
                for key, value in metrics.items():
                    if existing.get(key) != value:
                        existing[key] = value
                        changed = True

        if changed:
            updated += 1
            if not dry_run:
                _write_json(summary_path, summary, backup_suffix)

    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill GUI-only step metrics from execution_record.json."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=os.path.join(REPO_ROOT, "logs"),
        help="Directory to scan. Defaults to ./logs.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply changes. Without this flag, only prints a dry-run summary.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak files before writing.",
    )
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="Do not regenerate report/summary.md and report/summary.xlsx.",
    )
    parser.add_argument(
        "--no-ablation-summary",
        action="store_true",
        help="Do not update ablation_summary.json pipeline metrics.",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    dry_run = not args.write
    backup_suffix = None
    if args.write and not args.no_backup:
        backup_suffix = ".bak_gui_steps_" + datetime.now().strftime("%Y%m%d_%H%M%S")

    result_files = sorted(_iter_result_files(root))
    patched_files = 0
    patched_tasks = 0
    total_tasks = 0
    for path in result_files:
        changed_tasks, file_tasks = backfill_result_file(
            path, dry_run=dry_run, backup_suffix=backup_suffix)
        total_tasks += file_tasks
        if changed_tasks:
            patched_files += 1
            patched_tasks += changed_tasks
            rel = os.path.relpath(path, REPO_ROOT)
            print(f"{'would patch' if dry_run else 'patched'} {rel}: {changed_tasks}")

    condition_dirs = _condition_dirs(result_files)
    report_count = 0
    if not args.no_reports:
        report_count = regenerate_reports(condition_dirs, dry_run=dry_run)

    summary_count = 0
    if not args.no_ablation_summary:
        summary_count = update_ablation_summaries(
            condition_dirs, dry_run=dry_run, backup_suffix=backup_suffix)

    mode = "dry-run" if dry_run else "write"
    print(
        f"{mode}: scanned {len(result_files)} result files / {total_tasks} tasks; "
        f"{patched_files} files, {patched_tasks} tasks need backfill."
    )
    if not args.no_reports:
        print(f"{mode}: reports {'would be ' if dry_run else ''}regenerated: {report_count}")
    if not args.no_ablation_summary:
        print(
            f"{mode}: ablation_summary files "
            f"{'would be ' if dry_run else ''}updated: {summary_count}"
        )
    if backup_suffix:
        print(f"backup suffix: {backup_suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
