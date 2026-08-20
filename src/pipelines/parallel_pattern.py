# src/pipelines/parallel_pattern.py
"""
并行模式（parallel_class）查找工具。

数据源: 仓库根 task_parallel_pattern_v2.csv，列含
    task_id, pipeline, sub_category, task_source, parallel_class, n_subtasks, reasoning
按 task_id 查该任务的 parallel_class 与 n_subtasks，供统计沿"并行模式"维度横向聚合。
"""
import csv
import os

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
_DEFAULT_CSV = os.path.join(_REPO_ROOT, "task_parallel_pattern_v2.csv")


def _int_or_none(value):
    """把 CSV 文本转 int；空串/非法值返回 None。"""
    try:
        text = (value or "").strip()
        if text == "":
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


class ParallelPatternLookup:
    """
    并行模式查找表。

    构造时一次性加载 CSV 到内存字典；get() O(1) 查询。
    CSV 不存在时退化为空表（所有查询返回 None 对），不报错。
    """

    def __init__(self, csv_path: str = None):
        """
        输入: csv_path（可选），默认仓库根 task_parallel_pattern_v2.csv
        """
        self._map = {}
        path = csv_path or _DEFAULT_CSV
        if not os.path.isfile(path):
            return
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tid = (row.get("task_id") or "").strip()
                if not tid:
                    continue
                cls = (row.get("parallel_class") or "").strip() or None
                self._map[tid] = {
                    "parallel_class": cls,
                    "n_subtasks": _int_or_none(row.get("n_subtasks")),
                }

    def get(self, task_id: str) -> dict:
        """
        输入: task_id
        输出: {"parallel_class": str|None, "n_subtasks": int|None}；
              未知 task 返回 {None, None}。
        """
        return self._map.get(
            task_id, {"parallel_class": None, "n_subtasks": None})
