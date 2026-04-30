"""
Operation 任务规则化评估器。

基于规则的直接属性检查：从任务 JSON 的 eval_rules 字段读取检查规则，
在 Agent 产出目录中查找匹配文件，逐条执行检查并加权汇总得分。

典型用法:
    from eval.operation_evaluator import evaluate

    result = evaluate(
        result_dir="/path/to/agent/output",
        task_config={"task_id": "...", "eval_rules": [...]},
    )
    # result = {"score": 0.85, "pass": True, "reason": "...", "rule_results": [...]}

eval_rules 最简格式（只需 check + description）:
    [
        {
            "check": "check_batchexcel001_annual_sum",
            "description": "检查B16=1137977，C16=93.75"
        }
    ]

完整格式（均为可选，有默认值）:
    [
        {
            "check": "check_font_name",          # 必填：检查函数名
            "description": "全文字体为 TNR",       # 可选：可读描述
            "file_pattern": "*.docx",             # 可选：默认从注册表推断
            "params": {"font_name": "TNR"},       # 可选：默认 {}
            "weight": 1.0                         # 可选：默认 1.0
        }
    ]
"""

import glob
import logging
import os
from typing import Any, Callable, Dict, List, Tuple, Union

# 检查原语导入
from eval.operation_checks.docx_checks import (
    check_max_consecutive_blank_lines,
    check_font_name,
    check_line_spacing,
    check_heading_hierarchy,
    check_has_toc,
    check_first_line_indent,
    check_batchword002_tab_indent,
    # 扩展函数
    check_heading_style_exists,
    check_has_table,
    check_vowels_colored_red,
    check_uppercase_words_have_parentheses,
    check_highlighted_words_capitalized,
    check_misspelled_words_highlighted,
    check_heading_colors_different,
    check_image_name_matches_doc,
    check_docx_word_count,
    check_docx_has_hyperlink,
)
from eval.operation_checks.xlsx_checks import (
    check_cell_value,
    check_header_bold,
    check_column_alignment,
    check_sort_order,
    check_has_sum_row,
    check_batchexcel001_annual_sum,
    check_batchexcel002_header_bold,
    check_batchexcel002_range_right_align,
    # 扩展函数
    check_cell_contains_string,
    check_values_are_decimals,
    check_negative_values_colored,
    check_sorted_columns_exist,
    check_no_na_values,
    check_sequential_numbers,
    check_multi_cell_values,
    check_cells_filled,
)
from eval.operation_checks.pptx_checks import (
    check_slide_transition,
    check_text_not_overflow,
    check_batchppt002_bounds_overlap,
    # 扩展函数
    check_ppt_has_images_or_tables,
    check_ppt_slide_has_number,
)
from eval.operation_checks.file_checks import (
    check_files_exist,
    check_files_in_same_folder,
    check_html_files_for_xlsx,
    check_named_files_exist,
)

logger = logging.getLogger("eval.operation_evaluator")

# 注册表条目类型：
#   2 元组 (检查函数, 默认文件匹配模式)         — 文件级 check，逐文件调用
#   3 元组 (检查函数, 默认文件匹配模式, True)   — 目录级 check，直接接收 result_dir
# 目录级 check 自身已包含目录遍历逻辑（如 glob("**/*.xlsx")），若再被
# _execute_single_rule 按 file_pattern 匹配后逐路径循环调用，会把单个
# 文件/目录路径当作 result_dir 传入导致 glob 落空。标注 directory_level=True
# 的条目会跳过文件匹配循环，由函数自行处理目录遍历。
_RegistryEntry = Union[
    Tuple[Callable[[str, dict], dict], str],
    Tuple[Callable[[str, dict], dict], str, bool],
]

# ==================================================================
# 检查函数注册表
# ==================================================================
# 每个条目为 (函数, 默认 file_pattern)。
# eval_rules 中省略 file_pattern 时自动使用此默认值。

CHECK_REGISTRY: Dict[str, _RegistryEntry] = {
    # ---- docx 通用 ----
    "check_max_consecutive_blank_lines": (check_max_consecutive_blank_lines, "*.docx"),
    "check_font_name":                   (check_font_name, "*.docx"),
    "check_line_spacing":                (check_line_spacing, "*.docx"),
    "check_heading_hierarchy":           (check_heading_hierarchy, "*.docx"),
    "check_has_toc":                     (check_has_toc, "*.docx"),
    "check_first_line_indent":           (check_first_line_indent, "*.docx"),
    # ---- docx 任务专用 ----
    "check_batchword002_tab_indent":     (check_batchword002_tab_indent, "*.docx"),
    # ---- docx 扩展 ----
    "check_heading_style_exists":        (check_heading_style_exists, "*.docx"),
    "check_has_table":                   (check_has_table, "*.docx"),
    "check_vowels_colored_red":          (check_vowels_colored_red, "*.docx"),
    "check_uppercase_words_have_parentheses": (check_uppercase_words_have_parentheses, "*.docx"),
    "check_highlighted_words_capitalized": (check_highlighted_words_capitalized, "*.docx"),
    "check_misspelled_words_highlighted": (check_misspelled_words_highlighted, "*.docx"),
    "check_heading_colors_different":    (check_heading_colors_different, "*.docx"),
    "check_image_name_matches_doc":      (check_image_name_matches_doc, "*.docx"),
    "check_docx_word_count":            (check_docx_word_count, "*.docx"),
    "check_docx_has_hyperlink":         (check_docx_has_hyperlink, "*.docx"),
    # ---- xlsx 通用 ----
    "check_cell_value":                  (check_cell_value, "*.xlsx"),
    "check_header_bold":                 (check_header_bold, "*.xlsx"),
    "check_column_alignment":            (check_column_alignment, "*.xlsx"),
    "check_sort_order":                  (check_sort_order, "*.xlsx"),
    "check_has_sum_row":                 (check_has_sum_row, "*.xlsx"),
    # ---- xlsx 任务专用 ----
    "check_batchexcel001_annual_sum":    (check_batchexcel001_annual_sum, "*.xlsx"),
    "check_batchexcel002_header_bold":   (check_batchexcel002_header_bold, "*.xlsx"),
    "check_batchexcel002_range_right_align": (check_batchexcel002_range_right_align, "*.xlsx"),
    # ---- xlsx 扩展 ----
    "check_cell_contains_string":         (check_cell_contains_string, "*.xlsx"),
    "check_values_are_decimals":         (check_values_are_decimals, "*.xlsx"),
    "check_negative_values_colored":     (check_negative_values_colored, "*.xlsx"),
    "check_sorted_columns_exist":        (check_sorted_columns_exist, "*.xlsx"),
    "check_no_na_values":                (check_no_na_values, "*.xlsx"),
    "check_sequential_numbers":          (check_sequential_numbers, "*.xlsx"),
    "check_multi_cell_values":           (check_multi_cell_values, "*.xlsx"),
    "check_cells_filled":                (check_cells_filled, "*.xlsx"),
    # ---- pptx 通用 ----
    "check_slide_transition":            (check_slide_transition, "*.pptx"),
    "check_text_not_overflow":           (check_text_not_overflow, "*.pptx"),
    # ---- pptx 任务专用 ----
    "check_batchppt002_bounds_overlap":  (check_batchppt002_bounds_overlap, "*.pptx"),
    # ---- pptx 扩展 ----
    "check_ppt_has_images_or_tables":    (check_ppt_has_images_or_tables, "*.pptx"),
    "check_ppt_slide_has_number":        (check_ppt_slide_has_number, "*.pptx"),
    # ---- file 通用（目录级：跳过文件匹配循环，直接传 result_dir）----
    "check_files_exist":                 (check_files_exist, "*", True),
    "check_files_in_same_folder":        (check_files_in_same_folder, "*", True),
    "check_html_files_for_xlsx":         (check_html_files_for_xlsx, "*.xlsx", True),
    "check_named_files_exist":           (check_named_files_exist, "*", True),
}


# ==================================================================
# 文件匹配
# ==================================================================

def _find_matching_files(result_dir: str, file_pattern: str) -> List[str]:
    """
    在结果目录中查找匹配 glob 模式的文件。

    输入:
        result_dir: Agent 产出文件所在目录
        file_pattern: glob 模式（如 "*.docx", "report_*.xlsx"）
    输出:
        匹配文件路径列表（绝对路径），按文件名排序
    """
    pattern = os.path.join(result_dir, file_pattern)
    files = glob.glob(pattern)
    # 也搜索子目录
    recursive_pattern = os.path.join(result_dir, "**", file_pattern)
    files.extend(glob.glob(recursive_pattern, recursive=True))
    # 去重并排序
    unique = sorted(set(files))
    return unique


# ==================================================================
# 单条规则执行
# ==================================================================

def _execute_single_rule(
    rule: dict,
    result_dir: str,
) -> Dict[str, Any]:
    """
    执行单条 eval_rule，对所有匹配文件运行检查并取平均分。

    输入:
        rule: eval_rules 中的一条规则
        result_dir: Agent 产出文件所在目录
    输出:
        {
            "check": str,          # 检查函数名
            "description": str,    # 规则描述
            "weight": float,       # 权重
            "score": float,        # 该规则得分（所有匹配文件的平均）
            "pass": bool,
            "file_results": [...], # 每个文件的检查结果
            "reason": str,
        }
    """
    check_name = rule.get("check", "")
    description = rule.get("description", check_name)
    params = rule.get("params", {})
    weight = rule.get("weight", 1.0)

    # 查找检查函数及默认文件模式
    entry = CHECK_REGISTRY.get(check_name)
    if entry is None:
        # 评价器自身故障：规则配置引用了未注册的 check 函数
        return {
            "check": check_name,
            "description": description,
            "weight": weight,
            "score": -1.0,
            "pass": False,
            "status": "evaluator_error",
            "file_results": [],
            "reason": f"未注册的检查函数: {check_name}",
        }

    # 兼容 2 元组 / 3 元组：第 3 个元素为 directory_level，默认 False
    if len(entry) == 3:
        check_fn, default_pattern, directory_level = entry  # type: ignore[misc]
    else:
        check_fn, default_pattern = entry  # type: ignore[misc]
        directory_level = False

    # 目录级 check：跳过文件匹配循环，直接用 result_dir 调用一次
    if directory_level:
        try:
            dir_result = check_fn(result_dir, params)
        except Exception as exc:
            logger.error("目录级检查函数 %s 执行异常 (%s): %s", check_name, result_dir, exc)
            dir_result = {"pass": False, "score": 0.0, "reason": f"异常: {exc}"}

        score_val = float(dir_result.get("score", 0.0))
        pass_val = bool(dir_result.get("pass", False))
        dir_reason = dir_result.get("reason", "")
        return {
            "check": check_name,
            "description": description,
            "weight": weight,
            "score": round(score_val, 4),
            "pass": pass_val,
            "file_results": [{
                "file": os.path.basename(os.path.normpath(result_dir)) or result_dir,
                "file_path": result_dir,
                "pass": pass_val,
                "score": score_val,
                "reason": dir_reason,
            }],
            "reason": f"{description}: {dir_reason}" if dir_reason else description,
        }

    file_pattern = rule.get("file_pattern", default_pattern)

    # 查找匹配文件
    matched_files = _find_matching_files(result_dir, file_pattern)
    if not matched_files:
        return {
            "check": check_name,
            "description": description,
            "weight": weight,
            "score": 0.0,
            "pass": False,
            "file_results": [],
            "reason": f"未找到匹配 '{file_pattern}' 的文件",
        }

    # 对每个文件执行检查
    file_results = []
    for fpath in matched_files:
        try:
            result = check_fn(fpath, params)
        except Exception as exc:
            logger.error("检查函数 %s 执行异常 (%s): %s", check_name, fpath, exc)
            result = {"pass": False, "score": 0.0, "reason": f"异常: {exc}"}

        file_results.append({
            "file": os.path.basename(fpath),
            "file_path": fpath,
            **result,
        })

    # 检测单文件评价器错误（如 check 原语返回"参数缺少 ..."），冒泡为规则级 evaluator_error
    file_eval_errors = [r for r in file_results if r.get("status") == "evaluator_error"]
    if file_eval_errors:
        first_err = file_eval_errors[0]
        return {
            "check": check_name,
            "description": description,
            "weight": weight,
            "score": -1.0,
            "pass": False,
            "status": "evaluator_error",
            "file_results": file_results,
            "reason": f"{description}: {first_err.get('reason','')}",
        }

    # 取所有文件得分的平均值
    avg_score = sum(r.get("score", 0.0) for r in file_results) / len(file_results)
    # 规则级 pass：所有文件均严格满分（1.0）才算 pass，与 _partial 阈值保持一致
    all_passed = avg_score >= 1.0 - 1e-9

    passed_count = sum(1 for r in file_results if r.get("pass", False))
    # 文案显式说明"严格满分（=1.0）"语义，避免与平均分产生矛盾的观感
    reason = (
        f"{description}: {passed_count}/{len(file_results)} 文件严格满分，"
        f"平均得分 {avg_score:.2f}/1.00"
    )

    return {
        "check": check_name,
        "description": description,
        "weight": weight,
        "score": round(avg_score, 4),
        "pass": all_passed,
        "file_results": file_results,
        "reason": reason,
    }


# ==================================================================
# 主入口
# ==================================================================

def evaluate(result_dir: str, task_config: dict) -> dict:
    """
    Operation 任务规则化评估主入口。

    从 task_config["eval_rules"] 读取规则列表，在 result_dir 中查找匹配文件，
    逐条执行检查，加权汇总得分。

    输入:
        result_dir: Agent 产出文件所在目录（本地路径）
        task_config: 任务配置字典，必须包含 eval_rules 字段
    输出:
        {
            "score": float,            # 0.0~1.0 加权总分（允许部分得分）；
                                       # 评价器自身故障时为 -1.0（哨兵）
            "pass": bool,              # 严格通过：仅当 weighted_score == 1.0 时为 True
            "status": str,             # "ok" | "evaluator_error"
                                       # ok: 评价器正常完成（无论 pass/fail）
                                       # evaluator_error: 评价器自身故障（缺规则/目录不存在/check 未注册），
                                       #                  外层应排除出 fail 统计
            "reason": str,             # 汇总说明
            "rule_results": list,      # 每条规则的详细结果
            "task_id": str,
        }
    """
    task_id = task_config.get("task_id", "unknown")
    eval_rules = task_config.get("eval_rules", [])

    if not eval_rules:
        return {
            "score": -1.0,
            "pass": False,
            "status": "evaluator_error",
            "reason": f"任务 {task_id} 未定义 eval_rules",
            "rule_results": [],
            "task_id": task_id,
        }

    if not os.path.isdir(result_dir):
        return {
            "score": -1.0,
            "pass": False,
            "status": "evaluator_error",
            "reason": f"结果目录不存在: {result_dir}",
            "rule_results": [],
            "task_id": task_id,
        }

    logger.info("开始评估任务 %s（%d 条规则）", task_id, len(eval_rules))

    # 逐条执行规则
    rule_results = []
    for i, rule in enumerate(eval_rules):
        logger.info("执行规则 %d/%d: %s", i + 1, len(eval_rules), rule.get("check", ""))
        result = _execute_single_rule(rule, result_dir)
        rule_results.append(result)

    # 评价器自身故障冒泡：任一规则标记 evaluator_error 则整个任务视为评价器故障
    error_rules = [r for r in rule_results if r.get("status") == "evaluator_error"]
    if error_rules:
        return {
            "score": -1.0,
            "pass": False,
            "status": "evaluator_error",
            "reason": f"任务 {task_id}: 评价器配置错误（{error_rules[0].get('reason','')}）",
            "rule_results": rule_results,
            "task_id": task_id,
        }

    # 加权汇总
    total_weight = sum(r["weight"] for r in rule_results)
    if total_weight == 0:
        weighted_score = 0.0
    else:
        weighted_score = sum(
            r["score"] * r["weight"] for r in rule_results
        ) / total_weight

    weighted_score = round(weighted_score, 4)
    passed_rules = sum(1 for r in rule_results if r["pass"])

    reason = (
        f"任务 {task_id}: "
        f"加权得分 {weighted_score:.2f}/1.00（严格通过 "
        f"{passed_rules}/{len(rule_results)} 条规则）"
    )
    logger.info(reason)

    return {
        "score": weighted_score,
        # 严格通过：浮点容差仅用于消除 round 误差
        "pass": weighted_score >= 1.0 - 1e-9,
        "status": "ok",
        "reason": reason,
        "rule_results": rule_results,
        "task_id": task_id,
    }


# ==================================================================
# 命令行入口（便于独立调试）
# ==================================================================

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("用法: python operation_evaluator.py <result_dir> <task_json_path>")
        print("  result_dir: Agent 产出文件所在目录")
        print("  task_json_path: 任务 JSON 文件路径（须含 eval_rules 字段）")
        sys.exit(1)

    result_dir_arg = sys.argv[1]
    task_json_path = sys.argv[2]

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    with open(task_json_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    eval_result = evaluate(result_dir_arg, config)
    print(json.dumps(eval_result, ensure_ascii=False, indent=2))
