"""
统一任务扫描模块

从统一任务目录扫描任务 JSON 文件，支持按 pipeline 类型和任务 ID 过滤。
支持的 pipeline 类型：qa, webmall, operation, webnavigate, searchwrite。
"""

import json
import glob
import os
from typing import Any, Dict, List, Optional, Set, Tuple

# 支持的 pipeline 名称集合
VALID_PIPELINES = {"qa", "webmall", "operation", "webnavigate", "searchwrite"}


def _match_pipeline(task_id: str, config: Dict[str, Any], pipeline: str) -> bool:
    """
    判断一个任务是否属于指定的 pipeline。

    过滤规则：
      - qa:          task_type == "QA" 且 task_id 中不含 "OnlineShopping"
      - webmall:     task_id 中包含 "OnlineShopping"
      - operation:   task_id 以 "Operation-FileOperate-" 开头，且不含 "SearchAndWrite"
      - webnavigate: task_id 中包含 "WebOperate" 且不含 "SearchAndWrite"
      - searchwrite: task_id 中包含 "SearchAndWrite"

    输入参数:
        task_id:  任务 ID 字符串
        config:   任务 JSON 配置字典
        pipeline: pipeline 名称（qa/webmall/operation/webnavigate/searchwrite）

    输出返回值:
        bool — 该任务是否匹配指定 pipeline
    """
    if pipeline == "qa":
        return config.get("task_type") == "QA" and "OnlineShopping" not in task_id

    if pipeline == "webmall":
        return "OnlineShopping" in task_id

    if pipeline == "operation":
        return task_id.startswith("Operation-FileOperate-") and "SearchAndWrite" not in task_id

    if pipeline == "webnavigate":
        return "WebOperate" in task_id and "SearchAndWrite" not in task_id

    if pipeline == "searchwrite":
        return "SearchAndWrite" in task_id

    return False


def scan_unified_tasks(
    tasks_dir: str,
    pipeline: Optional[str] = None,
    allowed_ids: Optional[Set[str]] = None,
    allowed_uids: Optional[Set[str]] = None,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    从统一任务目录扫描任务 JSON 文件，支持按 pipeline 和 ID 过滤。

    扫描 tasks_dir 下所有 *.json 文件（跳过 id_mapping.json 等非任务文件），
    解析每个 JSON 并根据过滤条件筛选，返回排序后的任务列表。

    输入参数:
        tasks_dir:    任务 JSON 文件所在目录的路径
        pipeline:     可选，pipeline 名称（qa/webmall/operation/webnavigate/searchwrite）。
                      为 None 时不按 pipeline 过滤，返回全部任务。
        allowed_ids:  可选，允许的 task_id 集合。为 None 时不按 task_id 过滤。
        allowed_uids: 可选，允许的 task_uid 集合。为 None 时不按 task_uid 过滤。

    输出返回值:
        List[Tuple[str, str, Dict[str, Any]]] — 按 task_id 字母序排序的三元组列表：
            (task_id, task_json_文件绝对路径, 任务配置字典)

    异常:
        ValueError — pipeline 参数不在合法值集合中时抛出
    """
    if pipeline is not None and pipeline not in VALID_PIPELINES:
        raise ValueError(
            f"未知的 pipeline: '{pipeline}'，合法值为 {sorted(VALID_PIPELINES)}"
        )

    # 跳过的非任务文件名（不含扩展名）
    skip_basenames = {"id_mapping"}

    results: List[Tuple[str, str, Dict[str, Any]]] = []

    json_pattern = os.path.join(tasks_dir, "*.json")
    for filepath in glob.glob(json_pattern):
        basename = os.path.splitext(os.path.basename(filepath))[0]
        if basename in skip_basenames:
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            # 跳过无法解析的文件
            continue

        task_id = config.get("task_id", "")
        task_uid = config.get("task_uid", "")

        # --- 过滤逻辑 ---

        # 按 pipeline 过滤
        if pipeline is not None and not _match_pipeline(task_id, config, pipeline):
            continue

        # 按 allowed_ids 过滤
        if allowed_ids is not None and task_id not in allowed_ids:
            continue

        # 按 allowed_uids 过滤
        if allowed_uids is not None and task_uid not in allowed_uids:
            continue

        results.append((task_id, os.path.abspath(filepath), config))

    # 按 task_id 字母序排序
    results.sort(key=lambda x: x[0])
    return results
