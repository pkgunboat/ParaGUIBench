"""
任务中心日志的元数据构造与五级增量统计。

职责:
    1. build_meta(): 把一次 run 的 pipeline result dict 规整为权威 meta.json。
    2. StatsUpdater: 每完成一次 run，增量刷新 task/prefix/condition/overall/
       parallel_pattern 五级统计 JSON（flock 锁 + 原子写）。
    3. rebuild_all_stats(): 扫全部 meta.json 全量重建 stats（与增量结果一致）。
"""
import fcntl
import json
import os

import log_layout


def _derive_status(result: dict) -> str:
    """
    推导本次 run 的状态。

    优先用 result 自带的 status（init_failed / init_only_success 等），
    否则按 interrupted 标志归为 interrupted / completed。
    """
    explicit = result.get("status")
    if explicit:
        return str(explicit)
    if result.get("interrupted"):
        return "interrupted"
    return "completed"


def build_meta(result: dict, *, condition: str, host: str, timestamp: str,
               parallel_lookup, started_at: str, ended_at: str,
               pipeline: str = None, config_snapshot: dict = None) -> dict:
    """
    从 pipeline result dict 构造 meta.json 内容。

    输入:
        result: pipeline_base._handle_one_task 返回的结果字典
        condition / host / timestamp: 运行上下文
        parallel_lookup: ParallelPatternLookup 实例（或同接口桩）
        started_at / ended_at: ISO 时间串
        pipeline: 可选，缺省取 result["pipeline"]
        config_snapshot: 可选，关键运行配置快照
    输出:
        meta dict（将被原样 json.dump 到 run_dir/meta.json）
    """
    task_id = result.get("task_id")
    pat = parallel_lookup.get(task_id)
    meta = {
        "task_id": task_id,
        "task_uid": result.get("task_uid"),
        "task_prefix": log_layout.task_prefix(task_id) if task_id else None,
        "condition": condition,
        "pipeline": pipeline or result.get("pipeline"),
        "host": host,
        "run_id": log_layout.run_name(host, timestamp),
        "timestamp": timestamp,
        "parallel_class": pat.get("parallel_class"),
        "n_subtasks": pat.get("n_subtasks"),
        "agent_mode": result.get("agent_mode"),
        "gui_agent": result.get("gui_agent"),
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_time_sec": result.get("elapsed_time_sec"),
        "status": _derive_status(result),
        "score": result.get("score"),
        "pass": bool(result.get("pass")),
        "interrupted": bool(result.get("interrupted")),
        "interrupt_reason": result.get("interrupt_reason") or None,
        "plan_rounds": result.get("plan_rounds"),
        "gui_rounds_total": result.get("gui_rounds_total"),
        "gui_steps_sequential": result.get("gui_steps_sequential"),
        "token_plan": result.get("token_plan"),
        "token_gui": result.get("token_gui"),
        "token_total": result.get("token_total"),
        "cost_usd": result.get("cost_usd"),
    }
    if config_snapshot is not None:
        meta["config_snapshot"] = config_snapshot
    return meta


def _atomic_write_json(path: str, data: dict) -> None:
    """写 path.tmp 再 os.replace 覆盖 path，保证读者永不见半截文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str) -> dict:
    """读 JSON；不存在或损坏时返回 {}。"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class _FileLock:
    """对单个 stats JSON 文件取写锁（flock），锁文件为 <path>.lock。"""

    def __init__(self, target_path: str):
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        self._lock_path = target_path + ".lock"
        self._fh = None

    def __enter__(self):
        self._fh = open(self._lock_path, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


def _task_aggregate(runs: list) -> dict:
    """
    由该任务全部 run 条目算 task 级聚合。

    输入: runs（[{run_id,host,timestamp,score,pass,status,cost_usd,
                 token_total,elapsed_time_sec}, ...]）
    输出: aggregate dict（run_count/pass_count/pass_rate/best_score/
                          latest_score/mean_score/total_cost_usd/total_tokens/
                          mean_elapsed_sec/latest_run_id/latest_status）
    """
    n = len(runs)
    scores = [r.get("score") or 0.0 for r in runs]
    passes = [bool(r.get("pass")) for r in runs]
    # latest: timestamp 最大者（YYYYMMDD_HHMMSS 字典序即时间序），并列取 host 大者
    latest = max(runs, key=lambda r: (r.get("timestamp", ""),
                                      r.get("host", "")))
    return {
        "run_count": n,
        "pass_count": sum(1 for p in passes if p),
        "pass_rate": (sum(1 for p in passes if p) / n) if n else 0.0,
        "best_score": max(scores) if scores else 0.0,
        "latest_score": latest.get("score") or 0.0,
        "mean_score": (sum(scores) / n) if n else 0.0,
        "total_cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in runs), 4),
        "total_tokens": sum(r.get("token_total") or 0 for r in runs),
        "mean_elapsed_sec": (sum(r.get("elapsed_time_sec") or 0.0
                                 for r in runs) / n) if n else 0.0,
        "latest_run_id": latest.get("run_id"),
        "latest_status": latest.get("status"),
        "latest_pass": bool(latest.get("pass")),
    }


def _member_from_task_agg(task_agg: dict) -> dict:
    """
    由 task 级 aggregate 派生 roll-up 的成员摘要。

    输入: task_agg（_task_aggregate 产物）
    输出: roll-up members[task_id] 的值（latest/best 两口径 + 计数/成本/token）
    """
    return {
        "latest_pass": task_agg.get("latest_pass"),
        "best_pass": (task_agg.get("pass_count") or 0) > 0,
        "latest_score": task_agg.get("latest_score"),
        "best_score": task_agg.get("best_score"),
        "runs": task_agg.get("run_count"),
        "total_cost_usd": task_agg.get("total_cost_usd"),
        "total_tokens": task_agg.get("total_tokens"),
    }


class StatsUpdater:
    """
    五级增量统计维护器。

    每完成一次 run（一份 meta），刷新:
        stats/by_task/<cond>/<prefix>/<task_id>.json   （含全部 run 明细）
        stats/by_prefix/<cond>/<prefix>.json
        stats/by_condition/<cond>.json
        stats/overall.json
        stats/by_parallel_pattern/<class>.json          （class 为 None 时跳过）
    每个文件独立 flock 锁 + 原子写；增量结果与 rebuild_all_stats 一致。
    """

    def __init__(self, logs_dir: str):
        """输入: logs_dir（logs 绝对路径）。"""
        self.logs_dir = logs_dir
        self.stats_dir = os.path.join(logs_dir, "stats")

    # ---- 路径 ----
    def _task_path(self, m):
        return os.path.join(self.stats_dir, "by_task", m["condition"],
                            m["task_prefix"], m["task_id"] + ".json")

    def _prefix_path(self, m):
        return os.path.join(self.stats_dir, "by_prefix", m["condition"],
                            m["task_prefix"] + ".json")

    def _condition_path(self, m):
        return os.path.join(self.stats_dir, "by_condition",
                            m["condition"] + ".json")

    def _overall_path(self):
        return os.path.join(self.stats_dir, "overall.json")

    def _parallel_path(self, m):
        return os.path.join(self.stats_dir, "by_parallel_pattern",
                            str(m["parallel_class"]) + ".json")

    # ---- 入口 ----
    def on_run_finished(self, meta: dict) -> None:
        """
        输入: meta（build_meta 产物）
        副作用: 刷新五级统计文件。

        并发安全: task 文件在 task 锁内更新；各 roll-up 在自己的文件锁内重读
        权威 task 文件、由其 aggregate 现算 member（不接收外部快照）。因此同一
        task 多次 run 并发收尾时，roll-up 的最后写者必然反映 task 文件累计的
        全部 run，不会用过期快照覆盖（消除跨锁丢更新）。
        """
        self._update_task(meta)
        task_path = self._task_path(meta)
        self._roll_up(self._prefix_path(meta), "by_prefix",
                      [meta["condition"], meta["task_prefix"]],
                      meta["task_id"], task_path)
        self._roll_up(self._condition_path(meta), "by_condition",
                      [meta["condition"]], meta["task_id"], task_path)
        self._roll_up(self._overall_path(), "overall", [],
                      meta["task_id"], task_path)
        if meta.get("parallel_class"):
            self._roll_up(self._parallel_path(meta), "by_parallel_pattern",
                          [meta["parallel_class"]], meta["task_id"], task_path)

    # ---- task 级 ----
    def _update_task(self, m: dict) -> dict:
        """upsert 本次 run 到 task JSON 的 runs[]，重算并写回 aggregate。"""
        path = self._task_path(m)
        with _FileLock(path):
            data = _read_json(path)
            runs = data.get("runs", [])
            run_entry = {
                "run_id": m["run_id"], "host": m["host"],
                "timestamp": m["timestamp"], "score": m.get("score"),
                "pass": bool(m.get("pass")), "status": m.get("status"),
                "cost_usd": m.get("cost_usd"),
                "token_total": m.get("token_total"),
                "elapsed_time_sec": m.get("elapsed_time_sec"),
            }
            runs = [r for r in runs if r.get("run_id") != m["run_id"]]
            runs.append(run_entry)
            agg = _task_aggregate(runs)
            _atomic_write_json(path, {
                "task_id": m["task_id"], "condition": m["condition"],
                "prefix": m["task_prefix"],
                "parallel_class": m.get("parallel_class"),
                "aggregate": agg, "runs": runs,
            })
            return agg

    # ---- roll-up 级 ----
    def _roll_up(self, path, scope, key, task_id, task_path) -> None:
        """
        upsert task_id 的成员摘要到 scope JSON 的 members{}，重算 scope aggregate。

        关键: 在持有 scope 文件锁的临界区内重读权威 task 文件，由其 aggregate
        现算 member（而非接收外部传入的快照）——这样并发下最后写者总反映 task
        的最新累计状态，杜绝跨锁丢更新。
        输入: path（scope JSON 路径）、scope/key（元信息）、task_id、
              task_path（该 task 的 by_task stats JSON 路径）。
        """
        with _FileLock(path):
            data = _read_json(path)
            members = data.get("members", {})
            task_agg = _read_json(task_path).get("aggregate")
            if task_agg:
                members[task_id] = _member_from_task_agg(task_agg)
            _atomic_write_json(path, {
                "scope": scope, "key": key,
                "aggregate": _scope_aggregate(members),
                "members": members,
            })


def _scope_aggregate(members: dict) -> dict:
    """
    由 members 字典算范围级聚合（prefix/condition/overall/parallel 通用）。

    输入: members（{task_id: {latest_pass,best_pass,latest_score,best_score,
                              runs,total_cost_usd,total_tokens}}）
    输出: aggregate dict，含 latest / best 两套口径。
    """
    n = len(members)
    if n == 0:
        return {"task_count": 0, "total_runs": 0,
                "pass_rate_by_task_latest": 0.0,
                "pass_rate_by_task_best": 0.0,
                "mean_score_by_task_latest": 0.0,
                "total_cost_usd": 0.0, "total_tokens": 0}
    vals = list(members.values())
    return {
        "task_count": n,
        "total_runs": sum(v.get("runs", 0) for v in vals),
        "pass_rate_by_task_latest":
            sum(1 for v in vals if v.get("latest_pass")) / n,
        "pass_rate_by_task_best":
            sum(1 for v in vals if v.get("best_pass")) / n,
        "mean_score_by_task_latest":
            sum(v.get("latest_score") or 0.0 for v in vals) / n,
        "total_cost_usd":
            round(sum(v.get("total_cost_usd") or 0.0 for v in vals), 4),
        "total_tokens": sum(v.get("total_tokens") or 0 for v in vals),
    }


def rebuild_all_stats(logs_dir: str) -> int:
    """
    扫 logs/by_task/**/runs/*/meta.json 全量重建 logs/stats/。

    输入: logs_dir
    输出: 处理的 run 数
    副作用: 先删除 stats/ 再用 StatsUpdater 逐条重放（保证与增量一致）。
            重放顺序按 (task_id, timestamp) 排序，使 latest 判定稳定。
    """
    stats_dir = os.path.join(logs_dir, "stats")
    if os.path.isdir(stats_dir):
        import shutil
        shutil.rmtree(stats_dir)
    by_task_root = os.path.join(logs_dir, "by_task")
    metas = []
    for root, _dirs, files in os.walk(by_task_root):
        if os.path.basename(root) == "runs":
            for run_name in os.listdir(root):
                meta_path = os.path.join(root, run_name, "meta.json")
                data = _read_json(meta_path)
                if data.get("task_id"):
                    metas.append(data)
    metas.sort(key=lambda m: (m.get("task_id", ""), m.get("timestamp", "")))
    updater = StatsUpdater(logs_dir)
    for m in metas:
        updater.on_run_finished(m)
    return len(metas)
