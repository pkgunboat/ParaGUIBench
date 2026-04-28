"""
Master Table 核心数据模块。

提供：
    - Schema 常量（列名、默认值、类型）
    - load_master / save_master（原子写 + filelock）
    - upsert_results 三态写入（ok / error / empty）
    - mark / mark_batch / remove 人工标记
    - export_pending 导出待重跑 task_id 列表
    - import_run 从历史 ablation 目录导入

设计文档: docs/superpowers/specs/2026-04-13-master-table-design.md
"""

import csv
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

# ── 路径设置 ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.dirname(SCRIPT_DIR)
UBUNTU_ENV_DIR = os.path.dirname(EXAMPLES_DIR)
if UBUNTU_ENV_DIR not in sys.path:
    sys.path.insert(0, UBUNTU_ENV_DIR)

# ── 存储路径 ──
MASTER_DIR = os.path.join(UBUNTU_ENV_DIR, "logs", "master_table")
MASTER_CSV = os.path.join(MASTER_DIR, "master.csv")
MASTER_LOCK = os.path.join(MASTER_DIR, "master.csv.lock")

# ── Schema ──
IDENTITY_COLUMNS = ["mode", "condition", "pipeline", "task_id", "task_subtype"]
DIMENSION_COLUMNS = [
    "plan_model", "gui_agent", "agent_mode",
    "vms_per_task", "oracle_plan_injected",
]
METRIC_COLUMNS = [
    "score", "pass", "interrupted",
    "plan_rounds", "gui_rounds_total", "gui_steps_sequential",
    "token_plan", "token_gui", "token_total",
    "cost_usd", "elapsed_time_sec",
]
PROVENANCE_COLUMNS = ["run_timestamp", "run_dir", "log_path", "result_dir_path"]
FLAG_COLUMNS = ["lock", "empty", "error", "needs_rerun", "note"]

COLUMNS: List[str] = (
    IDENTITY_COLUMNS + DIMENSION_COLUMNS + METRIC_COLUMNS
    + PROVENANCE_COLUMNS + FLAG_COLUMNS
)

PRIMARY_KEY: Tuple[str, ...] = ("mode", "condition", "pipeline", "task_id")

# 每列默认值
DEFAULTS: Dict[str, Any] = {
    # 标识列无默认，必须提供
    "mode": "", "condition": "", "pipeline": "", "task_id": "", "task_subtype": "",
    # 维度
    "plan_model": "", "gui_agent": "", "agent_mode": "",
    "vms_per_task": 0, "oracle_plan_injected": False,
    # 指标
    "score": "", "pass": "", "interrupted": "",
    "plan_rounds": "", "gui_rounds_total": "", "gui_steps_sequential": "",
    "token_plan": "", "token_gui": "", "token_total": "",
    "cost_usd": "", "elapsed_time_sec": "",
    # 溯源
    "run_timestamp": "", "run_dir": "", "log_path": "", "result_dir_path": "",
    # 标记
    "lock": False, "empty": False, "error": False,
    "needs_rerun": False, "note": "",
}

# bool 列（CSV 存 "true"/"false" 字符串，解析时转 bool）
BOOL_COLUMNS: Set[str] = {
    "oracle_plan_injected", "pass", "interrupted",
    "lock", "empty", "error", "needs_rerun",
}

# int 列
INT_COLUMNS: Set[str] = {
    "vms_per_task", "plan_rounds", "gui_rounds_total", "gui_steps_sequential",
    "token_plan", "token_gui", "token_total",
}

# float 列
FLOAT_COLUMNS: Set[str] = {"score", "cost_usd", "elapsed_time_sec"}


# ============================================================
# 解析工具
# ============================================================

_TASK_SUBTYPE_RE = re.compile(r"-\d+$")


def parse_task_subtype(task_id: str) -> str:
    """
    从 task_id 解析出 subtype（去掉末尾 `-NNN`）。

    输入:
        task_id: 任务 ID，如 "Operation-FileOperate-BatchoperationWord-001"

    输出:
        subtype 字符串，如 "Operation-FileOperate-BatchoperationWord"；
        不匹配末尾编号模式时返回原 task_id。
    """
    stripped = _TASK_SUBTYPE_RE.sub("", task_id)
    return stripped if stripped != task_id else task_id


# ============================================================
# CSV Load / Save
# ============================================================

def _coerce_value(col: str, raw: str) -> Any:
    """
    CSV 单元格字符串 → Python 值（bool / int / float / str）。

    输入:
        col: 列名
        raw: CSV 原始字符串

    输出:
        转换后的 Python 值；空字符串统一保留为空字符串（除 bool 列为 False）。
    """
    if col in BOOL_COLUMNS:
        if raw in ("", "false", "False", "0"):
            return False
        return True
    if col in INT_COLUMNS:
        if raw == "":
            return ""
        try:
            return int(raw)
        except ValueError:
            return raw
    if col in FLOAT_COLUMNS:
        if raw == "":
            return ""
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _serialize_value(col: str, val: Any) -> str:
    """
    Python 值 → CSV 单元格字符串。

    输入:
        col: 列名
        val: Python 值

    输出:
        字符串表示；bool 转 "true"/"false"。
    """
    if col in BOOL_COLUMNS:
        return "true" if val else "false"
    if val is None:
        return ""
    return str(val)


def _ensure_dir() -> None:
    """确保 MASTER_DIR 目录存在。"""
    os.makedirs(MASTER_DIR, exist_ok=True)


def load_master() -> List[Dict[str, Any]]:
    """
    从 master.csv 读取所有行。

    输入:
        无

    输出:
        行字典列表；每个字典含所有 COLUMNS，值已做类型转换。
        CSV 不存在时返回空列表。
    """
    if not os.path.exists(MASTER_CSV):
        return []

    rows: List[Dict[str, Any]] = []
    with open(MASTER_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            typed = {col: _coerce_value(col, raw_row.get(col, ""))
                     for col in COLUMNS}
            rows.append(typed)
    return rows


def save_master(rows: List[Dict[str, Any]]) -> None:
    """
    原子地把 rows 写入 master.csv（临时文件 + rename）。
    调用者需自行持有 filelock；本函数不内嵌锁，便于复用。

    输入:
        rows: 行字典列表

    输出:
        None
    """
    _ensure_dir()
    tmp_path = MASTER_CSV + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            serialized = {col: _serialize_value(col, row.get(col, DEFAULTS[col]))
                          for col in COLUMNS}
            writer.writerow(serialized)
    os.replace(tmp_path, MASTER_CSV)


def _acquire_lock(timeout: float = 30.0):
    """
    获取 master.csv 的跨进程锁。

    输入:
        timeout: 等待秒数

    输出:
        filelock.FileLock 上下文管理器对象
    """
    from filelock import FileLock
    _ensure_dir()
    return FileLock(MASTER_LOCK, timeout=timeout)


# ============================================================
# Upsert 三态规则
# ============================================================

def _row_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """根据主键构造元组 key。"""
    return (row["mode"], row["condition"], row["pipeline"], row["task_id"])


def _build_new_row_template(
    pipeline: str, task_id: str, context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    构造一个新行，所有列填默认值，然后覆盖标识/维度/溯源列。

    输入:
        pipeline: pipeline 名
        task_id: 任务 ID
        context: upsert 上下文，见 upsert_results 文档

    输出:
        完整 30 列的 dict
    """
    row = {col: DEFAULTS[col] for col in COLUMNS}
    row["mode"] = context["mode"]
    row["condition"] = context["condition"]
    row["pipeline"] = pipeline
    row["task_id"] = task_id
    row["task_subtype"] = parse_task_subtype(task_id)
    row["plan_model"] = context.get("plan_model", "")
    row["gui_agent"] = context.get("gui_agent", "")
    row["agent_mode"] = context.get("agent_mode", "")
    row["vms_per_task"] = context.get("vms_per_task", 0)
    row["oracle_plan_injected"] = bool(context.get("oracle_plan_injected", False))
    row["run_timestamp"] = context.get("run_timestamp", "")
    row["run_dir"] = context.get("run_dir", "")
    return row


def _safe_int(value: Any) -> int:
    """宽松转换统计字段，空值/非法值按 0 处理。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_gui_only_step_count_from_record(record: Dict[str, Any]) -> int:
    """从 gui_only execution_record 中提取单 Agent step 数。"""
    if not isinstance(record, dict):
        return 0

    summary = record.get("summary", {})
    if not isinstance(summary, dict) or summary.get("mode") != "gui_only":
        return 0

    steps = record.get("steps")
    if isinstance(steps, list) and steps:
        return len(steps)

    rounds_timing = record.get("rounds_timing")
    if isinstance(rounds_timing, list) and rounds_timing:
        return len(rounds_timing)

    return _safe_int(summary.get("total_rounds"))


def _execution_record_candidates(
    row: Dict[str, Any],
    result: Dict[str, Any],
    context: Optional[Dict[str, Any]],
) -> List[str]:
    """按可信度列出可能的 execution_record.json 路径。"""
    task_id = row.get("task_id") or result.get("task_id")
    if not task_id:
        return []

    candidates: List[str] = []
    for key in ("result_dir", "result_dir_path"):
        result_dir = result.get(key)
        if result_dir:
            candidates.append(os.path.join(result_dir, "execution_record.json"))

    if context:
        run_dir = context.get("run_dir", "")
        if run_dir:
            base = run_dir if os.path.isabs(run_dir) else os.path.join(
                UBUNTU_ENV_DIR, "logs", run_dir)
            candidates.append(os.path.join(base, task_id, "execution_record.json"))

    seen = set()
    out = []
    for path in candidates:
        norm = os.path.abspath(path)
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(norm):
            out.append(norm)
    return out


def _backfill_gui_only_steps_for_master(
    row: Dict[str, Any],
    result: Dict[str, Any],
    context: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """历史结果 GUI step 为 0 时，从 execution_record.json 回填。"""
    agent_mode = result.get("agent_mode") or (context or {}).get("agent_mode")
    if agent_mode != "gui_only":
        return result
    if _safe_int(result.get("gui_rounds_total")) > 0 \
            or _safe_int(result.get("gui_steps_sequential")) > 0:
        return result

    for path in _execution_record_candidates(row, result, context):
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except Exception:
            continue
        step_count = _extract_gui_only_step_count_from_record(record)
        if step_count <= 0:
            continue
        enriched = dict(result)
        enriched["gui_rounds_total"] = step_count
        enriched["gui_steps_sequential"] = step_count
        enriched.setdefault("result_dir", os.path.dirname(path))
        return enriched

    return result


def _fill_metric_columns(
    row: Dict[str, Any],
    result: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """从 pipeline result dict 中抽取指标字段写入 row。缺失字段填默认值。"""
    result = _backfill_gui_only_steps_for_master(row, result, context)
    for col in METRIC_COLUMNS:
        if col in result:
            row[col] = result[col]
        else:
            row[col] = DEFAULTS[col]


def _fill_provenance_from_result(
    row: Dict[str, Any], result: Dict[str, Any], context: Dict[str, Any],
) -> None:
    """
    基于 result 和 context 填充 log_path / result_dir_path。

    约定：
        log_path = <run_dir>/<task_id>/task.log
        result_dir_path = result.get("result_dir", "") 或 <run_dir>/<task_id>/
    """
    run_dir = context.get("run_dir", "")
    task_id = row["task_id"]
    row["log_path"] = os.path.join(run_dir, task_id, "task.log") if run_dir else ""
    explicit = result.get("result_dir", "")
    if explicit:
        row["result_dir_path"] = explicit
    elif run_dir:
        row["result_dir_path"] = os.path.join(run_dir, task_id) + os.sep
    else:
        row["result_dir_path"] = ""


def _classify(task_id: str, results: Dict[str, Dict[str, Any]]) -> str:
    """
    判定 result_kind。

    输入:
        task_id: 任务 ID
        results: pipeline 返回的任务结果字典（key 已归一为 task_id）

    输出:
        "ok" / "error" / "missing"
    """
    if task_id not in results:
        return "missing"
    r = results[task_id]
    if r.get("interrupted", False) or r.get("_exception"):
        return "error"
    return "ok"


def _normalize_results_keys(
    results: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    pipeline_base 以 task_uid 或 task_id 作 key；统一归一为以 result["task_id"] 作 key。
    缺失 task_id 字段的记录跳过。
    """
    out = {}
    for _k, v in results.items():
        tid = v.get("task_id")
        if tid:
            out[tid] = v
    return out


def upsert_results(
    results: Dict[str, Dict[str, Any]],
    expected_task_ids: List[str],
    pipeline: str,
    context: Dict[str, Any],
) -> None:
    """
    把一次 pipeline 运行结果合并进 master.csv。

    输入:
        results: pipeline 返回的 {task_key: task_result_dict}
                 task_key 可能是 task_uid 或 task_id；内部归一。
        expected_task_ids: 本次经过 scanner 过滤后应跑的任务 ID 列表
        pipeline: pipeline 名（qa / webmall / ...）
        context: 上下文字典，需包含 keys:
            mode, condition, run_timestamp, run_dir,
            plan_model, gui_agent, agent_mode, vms_per_task, oracle_plan_injected

    输出:
        None（直接写 master.csv）

    规则（参见 spec §5.2）:
        对每个任务按 result_kind ∈ {ok, error, missing} 分派：
        - ok: 覆盖指标 / 维度 / 溯源；清零 empty/error/needs_rerun；保留 lock/note
        - error: 同 ok 但置 error=True
        - missing: 置 empty=True；指标列填默认
        - 若已有行且 lock=True：整行跳过
    """
    results_by_id = _normalize_results_keys(results)
    # 合集：expected ∪ results（processed 但不在 expected 的也保留）
    all_ids = set(expected_task_ids) | set(results_by_id.keys())

    with _acquire_lock():
        rows = load_master()
        index: Dict[Tuple[str, str, str, str], int] = {
            _row_key(r): i for i, r in enumerate(rows)
        }

        for task_id in sorted(all_ids):
            kind = _classify(task_id, results_by_id)
            pk = (context["mode"], context["condition"], pipeline, task_id)

            # 已有且 lock → 跳过
            if pk in index and rows[index[pk]].get("lock", False):
                continue

            # 构造基础 row（新行默认 / 旧行拷贝）
            if pk in index:
                row = dict(rows[index[pk]])
                preserved_note = row.get("note", "")
                preserved_lock = row.get("lock", False)
            else:
                row = _build_new_row_template(pipeline, task_id, context)
                preserved_note = ""
                preserved_lock = False

            # 标识/维度/溯源刷新
            base = _build_new_row_template(pipeline, task_id, context)
            for col in IDENTITY_COLUMNS + DIMENSION_COLUMNS:
                row[col] = base[col]
            row["run_timestamp"] = base["run_timestamp"]
            row["run_dir"] = base["run_dir"]

            # 按类型写指标与标记
            if kind == "ok":
                _fill_metric_columns(row, results_by_id[task_id], context)
                _fill_provenance_from_result(row, results_by_id[task_id], context)
                row["empty"] = False
                row["error"] = False
                row["needs_rerun"] = False
            elif kind == "error":
                _fill_metric_columns(row, results_by_id[task_id], context)
                _fill_provenance_from_result(row, results_by_id[task_id], context)
                row["error"] = True
                # empty / needs_rerun 保持现状
            else:  # missing
                for col in METRIC_COLUMNS:
                    row[col] = DEFAULTS[col]
                row["log_path"] = ""
                row["result_dir_path"] = ""
                row["empty"] = True

            # lock / note 始终保留
            row["lock"] = preserved_lock
            row["note"] = preserved_note

            if pk in index:
                rows[index[pk]] = row
            else:
                rows.append(row)
                index[pk] = len(rows) - 1

        save_master(rows)


# ============================================================
# 人工标记 / 查询 / 导出
# ============================================================

_MARK_BOOL_FIELDS = ("lock", "empty", "error", "needs_rerun")


def _match_row(
    row: Dict[str, Any],
    task_id: str,
    mode: Optional[str] = None,
    condition: Optional[str] = None,
    pipeline: Optional[str] = None,
) -> bool:
    """判断一行是否匹配给定过滤条件。None 表示不限。"""
    if row.get("task_id") != task_id:
        return False
    if mode is not None and row.get("mode") != mode:
        return False
    if condition is not None and row.get("condition") != condition:
        return False
    if pipeline is not None and row.get("pipeline") != pipeline:
        return False
    return True


def _apply_mark(row: Dict[str, Any], **kwargs: Any) -> None:
    """
    把 kwargs 中非 None 的字段写入 row。
    支持：lock / empty / error / needs_rerun（bool）、note（str）、
          clear_error / clear_needs_rerun / clear_empty / clear_note（flag）。
    """
    for field in _MARK_BOOL_FIELDS:
        val = kwargs.get(field)
        if val is not None:
            row[field] = bool(val)
    note = kwargs.get("note")
    if note is not None:
        row["note"] = note
    if kwargs.get("clear_error"):
        row["error"] = False
    if kwargs.get("clear_needs_rerun"):
        row["needs_rerun"] = False
    if kwargs.get("clear_empty"):
        row["empty"] = False
    if kwargs.get("clear_note"):
        row["note"] = ""


def mark(
    task_id: str,
    mode: Optional[str] = None,
    condition: Optional[str] = None,
    pipeline: Optional[str] = None,
    **mark_fields: Any,
) -> int:
    """
    给匹配主键的行打标记。

    输入:
        task_id, mode, condition, pipeline: 定位条件（后三者 None 表示不过滤）
        mark_fields: lock / empty / error / needs_rerun / note /
                     clear_error / clear_needs_rerun / clear_empty / clear_note

    输出:
        int — 被修改的行数。若为 0 表示未找到匹配记录。
    """
    with _acquire_lock():
        rows = load_master()
        n = 0
        for row in rows:
            if _match_row(row, task_id, mode, condition, pipeline):
                _apply_mark(row, **mark_fields)
                n += 1
        if n > 0:
            save_master(rows)
    return n


def mark_batch(
    task_ids: List[str],
    mode: Optional[str] = None,
    condition: Optional[str] = None,
    pipeline: Optional[str] = None,
    **mark_fields: Any,
) -> int:
    """
    批量标记。语义同 mark()，对 task_ids 中每个 id 循环。

    输出:
        int — 总共修改的行数。
    """
    with _acquire_lock():
        rows = load_master()
        target = set(task_ids)
        n = 0
        for row in rows:
            if row.get("task_id") in target \
                    and (mode is None or row.get("mode") == mode) \
                    and (condition is None or row.get("condition") == condition) \
                    and (pipeline is None or row.get("pipeline") == pipeline):
                _apply_mark(row, **mark_fields)
                n += 1
        if n > 0:
            save_master(rows)
    return n


def remove(
    task_id: str,
    mode: Optional[str] = None,
    condition: Optional[str] = None,
    pipeline: Optional[str] = None,
) -> int:
    """
    删除匹配的行。

    输出:
        int — 被删除的行数。
    """
    with _acquire_lock():
        rows = load_master()
        keep = [r for r in rows
                if not _match_row(r, task_id, mode, condition, pipeline)]
        removed = len(rows) - len(keep)
        if removed > 0:
            save_master(keep)
    return removed


def export_pending(
    mode: Optional[str] = None,
    condition: Optional[str] = None,
    pipelines: Optional[List[str]] = None,
) -> List[str]:
    """
    导出待重跑任务 ID（去重、按字母序）。

    规则:
        (empty OR error OR needs_rerun) AND NOT lock
        可按 mode / condition / pipelines 过滤。

    输出:
        List[str] — task_id 列表
    """
    rows = load_master()
    result: Set[str] = set()
    for r in rows:
        if mode is not None and r.get("mode") != mode:
            continue
        if condition is not None and r.get("condition") != condition:
            continue
        if pipelines is not None and r.get("pipeline") not in pipelines:
            continue
        if r.get("lock", False):
            continue
        if r.get("empty", False) or r.get("error", False) or r.get("needs_rerun", False):
            result.add(r["task_id"])
    return sorted(result)


def show(
    task_id: str,
    mode: Optional[str] = None,
    condition: Optional[str] = None,
    pipeline: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    查询匹配行（不改数据）。

    输出:
        匹配的行字典列表。
    """
    rows = load_master()
    return [r for r in rows if _match_row(r, task_id, mode, condition, pipeline)]
