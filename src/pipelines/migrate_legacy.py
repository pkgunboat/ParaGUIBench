# src/pipelines/migrate_legacy.py
"""
旧日志结构 → 新任务中心结构 的迁移核心。

旧结构: logs/<host>/ablation_<ts>/<condition>/<task_id>/{task.log,rounds.json,execution_record.json}
        外加 <condition>/<pipeline>_results.json（含每个 task 的 result dict）
新结构: logs/by_task/<condition>/<prefix>/<task_id>/runs/<host>__<ts>/{...,meta.json}
        旧路径原位降级为指向新 run_dir 的目录级软链接。
迁移三步: 物理备份 → 搬真实文件进 by_task + 写 meta → 原位软链接 → (可选) 重建 stats。
"""
import json
import os
import re
import shutil

import log_layout
import parallel_pattern
import stats_updater

_HOST_DIR_RE = re.compile(r"^ablation_(\d{8}_\d{6})$")
_PER_TASK_FILES = ("task.log", "rounds.json", "execution_record.json")


def _iter_legacy_task_dirs(logs_dir: str):
    """
    遍历旧结构，yield (host, ts, condition, task_id, task_dir, cond_dir, kind)。

    仅认 logs/<host>/ablation_<ts>/<condition>/<task_id>/ 形态；
    跳过 by_task/stats/_legacy_* 等新结构或备份目录。

    kind:
        "migrate" —— tdir 是含 per-task 文件的实体目录，需完整迁移。
        "repair"  —— tdir 是不含 per-task 文件的实体目录，但对应 by_task
                     run_dir/meta.json 已存在（典型为"半迁移崩溃"残留的空壳）；
                     真实数据已在 by_task，只需删空壳 + 补建原位软链接。
    跳过: 已是软链接的 tdir（迁移完成）；以及既无 per-task 文件、对应 by_task
          run_dir 也不存在的实体目录（report/agent_results 等非任务产物）。
    """
    skip_top = {"by_task", "stats"}
    for host in sorted(os.listdir(logs_dir)):
        if host in skip_top or host.startswith("_legacy"):
            continue
        host_path = os.path.join(logs_dir, host)
        if not os.path.isdir(host_path):
            continue
        for abl in sorted(os.listdir(host_path)):
            m = _HOST_DIR_RE.match(abl)
            if not m:
                continue
            ts = m.group(1)
            abl_path = os.path.join(host_path, abl)
            for cond in sorted(os.listdir(abl_path)):
                cond_dir = os.path.join(abl_path, cond)
                if not os.path.isdir(cond_dir):
                    continue
                for task_id in sorted(os.listdir(cond_dir)):
                    tdir = os.path.join(cond_dir, task_id)
                    # 已迁移过的 task 现在是软链接，跳过（保证幂等，避免对
                    # 同一真实文件重复 os.replace）
                    if os.path.islink(tdir):
                        continue
                    if not os.path.isdir(tdir):
                        continue
                    if any(os.path.isfile(os.path.join(tdir, fn))
                           for fn in _PER_TASK_FILES):
                        yield host, ts, cond, task_id, tdir, cond_dir, "migrate"
                        continue
                    # 无 per-task 文件: 若 by_task 已有该 run 的 meta.json，
                    # 视为半迁移崩溃残留(空壳)，补软链自愈；否则是非任务目录，跳过。
                    rd = log_layout.run_dir(logs_dir, cond, task_id, host, ts)
                    if os.path.isfile(os.path.join(rd, "meta.json")):
                        yield host, ts, cond, task_id, tdir, cond_dir, "repair"


def _load_results_index(cond_dir: str) -> dict:
    """
    读 cond_dir 下所有 *_results.json，建 task_id -> result dict 索引。

    输入: cond_dir（旧结构某 condition 目录）
    输出: {task_id: result_dict}
    """
    index = {}
    for fn in os.listdir(cond_dir):
        if not fn.endswith("_results.json"):
            continue
        try:
            with open(os.path.join(cond_dir, fn), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for _key, value in data.items():
            tid = value.get("task_id")
            if tid:
                index[tid] = value
    return index


def _backup(logs_dir: str, backup_tag: str, mode: str) -> str:
    """
    物理备份旧 host 目录到 logs/<backup_tag>/。

    输入: logs_dir, backup_tag（如 _legacy_20260530）, mode（copy/hardlink）
    输出: 备份根目录
    幂等: 逐 host 判断——已备份的 host 跳过，未备份的（含后续增量加入的新
          host）才 copytree。避免"备份根已存在即整体跳过"导致增量迁移的新
          数据没有物理备份。
    """
    backup_root = os.path.join(logs_dir, backup_tag)
    os.makedirs(backup_root, exist_ok=True)
    for host in sorted(os.listdir(logs_dir)):
        if host in ("by_task", "stats") or host.startswith("_legacy"):
            continue
        if host == backup_tag:
            continue
        src = os.path.join(logs_dir, host)
        if not os.path.isdir(src):
            continue
        if not any(_HOST_DIR_RE.match(d) for d in os.listdir(src)):
            continue
        dst = os.path.join(backup_root, host)
        if os.path.exists(dst):
            continue
        if mode == "hardlink":
            shutil.copytree(src, dst, copy_function=os.link)
        else:
            shutil.copytree(src, dst)
    return backup_root


def migrate(logs_dir: str, *, backup_mode: str = "copy",
            backup_tag: str = "_legacy_migrated", do_rebuild: bool = True) -> int:
    """
    执行迁移。

    输入:
        logs_dir: logs 绝对路径
        backup_mode: "copy"（独立物理副本，默认）| "hardlink"（省空间）
        backup_tag: 备份目录名
        do_rebuild: 迁移后是否调 rebuild_all_stats
    输出: 迁移的 task-run 数
    步骤: 备份 → 逐 task 搬文件+写 meta → 原位软链接 → (可选)重建 stats。
    """
    _backup(logs_dir, backup_tag, backup_mode)
    lookup = parallel_pattern.ParallelPatternLookup()
    results_cache = {}
    count = 0
    for host, ts, cond, task_id, tdir, cond_dir, kind in \
            list(_iter_legacy_task_dirs(logs_dir)):
        if kind == "repair":
            # 半迁移崩溃残留: 真实数据已在 by_task, 仅删空壳 + 补建原位软链接。
            shutil.rmtree(tdir, ignore_errors=True)
            log_layout.create_legacy_symlink(logs_dir, host, ts, cond, task_id)
            count += 1
            continue
        if cond_dir not in results_cache:
            results_cache[cond_dir] = _load_results_index(cond_dir)
        # 复制一份，避免 setdefault 改动缓存里的共享 dict
        result = dict(results_cache[cond_dir].get(task_id) or {})
        result.setdefault("task_id", task_id)

        rd = log_layout.run_dir(logs_dir, cond, task_id, host, ts)
        os.makedirs(rd, exist_ok=True)
        # 搬整个任务目录的全部内容进 by_task（含 task.log/rounds.json/
        # execution_record.json 以及任务产物文件、嵌套子目录如 screenshots/），
        # 而非只搬 per-task 日志——避免 rmtree 丢弃产物。meta.json 随后写入，
        # 若 legacy 目录恰有同名 meta.json 则被我方权威 meta 覆盖。
        for entry in sorted(os.listdir(tdir)):
            os.replace(os.path.join(tdir, entry), os.path.join(rd, entry))
        # 构造并写 meta.json（缺失关键指标时标 legacy=True）
        meta = stats_updater.build_meta(
            result, condition=cond, host=host, timestamp=ts,
            parallel_lookup=lookup,
            started_at=None, ended_at=None,
            pipeline=result.get("pipeline"))
        if result.get("score") is None:
            meta["legacy"] = True
        with open(os.path.join(rd, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        # latest 软链接（同 task 多 run 时，rebuild 不依赖它，这里指最近写入）
        log_layout.update_latest_symlink(
            log_layout.by_task_dir(logs_dir, cond, task_id),
            log_layout.run_name(host, ts))
        # 删除已掏空的旧 task 目录，原位建目录级软链接
        shutil.rmtree(tdir, ignore_errors=True)
        log_layout.create_legacy_symlink(logs_dir, host, ts, cond, task_id)
        count += 1

    if do_rebuild:
        stats_updater.rebuild_all_stats(logs_dir)
    return count
