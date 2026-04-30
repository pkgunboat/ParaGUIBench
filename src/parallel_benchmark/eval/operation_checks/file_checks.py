"""
文件存在性及结构检查原语。

每个函数接收文件路径和参数字典，返回标准化结果：
    {"pass": bool, "score": float 0.0~1.0, "reason": str}

依赖: 无额外依赖（仅使用标准库 os, glob）
"""

import glob
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("eval.operation_checks.file")


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------

def _ok(reason: str = "通过") -> Dict[str, Any]:
    return {"pass": True, "score": 1.0, "reason": reason}


def _fail(reason: str) -> Dict[str, Any]:
    return {"pass": False, "score": 0.0, "reason": reason}


def _partial(score: float, reason: str) -> Dict[str, Any]:
    # 严格阈值：仅当 score 等于 1.0 才算 pass
    return {"pass": score >= 1.0 - 1e-9, "score": round(score, 4), "reason": reason}


def _config_error(reason: str) -> Dict[str, Any]:
    """评价器配置错误（缺参数等）：score=-1 哨兵，由上层冒泡为 evaluator_error。"""
    return {"pass": False, "score": -1.0, "status": "evaluator_error", "reason": reason}


# ------------------------------------------------------------------
# 检查函数
# ------------------------------------------------------------------

def check_files_exist(result_dir: str, params: dict) -> dict:
    """
    检查指定路径下是否存在特定文件名。

    输入:
        result_dir: 搜索的根目录
        params:
            expected_files (list[str]): 期望存在的文件名列表
            search_subdirs (bool): 是否搜索子目录，默认 True
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    expected_files = params.get("expected_files", [])
    search_subdirs = params.get("search_subdirs", True)

    if not expected_files:
        return _config_error("参数缺少 expected_files")

    found_files = []
    missing_files = []

    for filename in expected_files:
        if search_subdirs:
            pattern = os.path.join(result_dir, "**", filename)
            matches = glob.glob(pattern, recursive=True)
        else:
            pattern = os.path.join(result_dir, filename)
            matches = glob.glob(pattern)

        if matches:
            found_files.append(filename)
        else:
            missing_files.append(filename)

    total = len(expected_files)
    if total == 0:
        return _ok("未指定需要检查的文件名")

    ratio = len(found_files) / total
    if len(missing_files) == 0:
        return _ok(f"全部 {total} 个文件都存在")

    return _partial(
        ratio,
        f"找到 {len(found_files)}/{total} 个文件，缺失: {', '.join(missing_files[:5])}"
    )


def check_files_in_same_folder(result_dir: str, params: dict) -> dict:
    """
    检查指定文件是否在同一个子文件夹中。

    输入:
        result_dir: 搜索的根目录
        params:
            file_groups (list[list[str]]): 文件分组列表，每组内的文件应在同一文件夹
                例如: [["file1.xlsx", "file2.xlsx"], ["file3.pptx", "file4.pptx"]]
    输出:
        {"pass": bool, "score": float, "reason": str}

    说明:
        - 组内文件数 < 2 时跳过该组（单文件组无法评估"同一文件夹"语义，
          否则 len(unique_dirs)==1 恒 true 会误判通过）
        - 组内任一文件缺失 → 该组计为 fail（不再仅对已找到的文件比较目录）
        - 若所有组都被跳过（全部为空组或单文件组）→ 返回 _fail
    """
    file_groups = params.get("file_groups", [])

    if not file_groups:
        return _config_error("参数缺少 file_groups")

    effective_groups = 0
    passed_groups = 0
    details: List[str] = []
    skipped_details: List[str] = []

    for i, group in enumerate(file_groups):
        if not group:
            skipped_details.append(f"组{i+1}: 空组，已跳过")
            continue
        if len(group) < 2:
            skipped_details.append(
                f"组{i+1}: 单文件组({group[0]})，无法评估同一文件夹语义，已跳过"
            )
            continue

        file_dirs: List[str] = []
        missing: List[str] = []
        for filename in group:
            pattern = os.path.join(result_dir, "**", filename)
            matches = glob.glob(pattern, recursive=True)
            if matches:
                file_dirs.append(os.path.dirname(matches[0]))
            else:
                missing.append(filename)

        effective_groups += 1
        if missing:
            details.append(f"组{i+1}: 缺失文件 {', '.join(missing[:3])}")
            continue

        unique_dirs = set(file_dirs)
        if len(unique_dirs) == 1:
            passed_groups += 1
            folder_name = os.path.basename(list(unique_dirs)[0]) or list(unique_dirs)[0]
            details.append(f"组{i+1}: {len(group)} 个文件全部在 {folder_name}")
        else:
            folder_names = [os.path.basename(d) or d for d in unique_dirs]
            details.append(
                f"组{i+1}: 文件分散在 {len(unique_dirs)} 个文件夹: "
                f"{', '.join(folder_names[:3])}"
            )

    if effective_groups == 0:
        reason = "无有效分组可评估"
        if skipped_details:
            reason += "：" + "; ".join(skipped_details[:5])
        return _fail(reason)

    ratio = passed_groups / effective_groups
    combined = details + skipped_details
    if passed_groups == effective_groups:
        ok_reason = f"{effective_groups} 组文件全部在同一文件夹"
        if skipped_details:
            ok_reason += f"（跳过 {len(skipped_details)} 组: {'; '.join(skipped_details[:3])}）"
        return _ok(ok_reason)

    return _partial(
        ratio,
        f"{passed_groups}/{effective_groups} 组在同一文件夹: {'; '.join(combined[:5])}",
    )


def check_html_files_for_xlsx(result_dir: str, params: dict) -> dict:
    """
    检查是否存在与 xlsx 文件名相同的 html 文件。

    输入:
        result_dir: 搜索的根目录
        params:
            search_subdirs (bool): 是否搜索子目录，默认 True
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    search_subdirs = params.get("search_subdirs", True)

    # 查找所有 xlsx 文件
    if search_subdirs:
        xlsx_pattern = os.path.join(result_dir, "**", "*.xlsx")
        xlsx_files = glob.glob(xlsx_pattern, recursive=True)
    else:
        xlsx_pattern = os.path.join(result_dir, "*.xlsx")
        xlsx_files = glob.glob(xlsx_pattern)

    if not xlsx_files:
        return _fail("未找到 xlsx 文件")

    total = len(xlsx_files)
    matched = 0
    details = []

    for xlsx_path in xlsx_files:
        xlsx_name = os.path.basename(xlsx_path)
        xlsx_base = os.path.splitext(xlsx_name)[0]
        html_name = xlsx_base + ".html"

        if search_subdirs:
            html_pattern = os.path.join(result_dir, "**", html_name)
            html_matches = glob.glob(html_pattern, recursive=True)
        else:
            html_pattern = os.path.join(result_dir, html_name)
            html_matches = glob.glob(html_pattern)

        if html_matches:
            matched += 1
            details.append(f"{xlsx_name} -> {html_name}")
        else:
            details.append(f"{xlsx_name} -> 缺失")

    ratio = matched / total
    if matched == total:
        return _ok(f"全部 {total} 个 xlsx 都有对应的 html 文件")

    return _partial(ratio, f"{matched}/{total} 个 xlsx 有对应 html: {', '.join(details[:5])}")


def check_named_files_exist(result_dir: str, params: dict) -> dict:
    """
    检查指定名称的文件是否存在。

    输入:
        result_dir: 搜索的根目录
        params:
            filenames (list[str]): 需要检查的文件名列表
            search_subdirs (bool): 是否搜索子目录，默认 True
    输出:
        {"pass": bool, "score": float, "reason": str}
    """
    filenames = params.get("filenames", [])
    search_subdirs = params.get("search_subdirs", True)

    if not filenames:
        return _config_error("参数缺少 filenames")

    found = []
    missing = []

    for name in filenames:
        if search_subdirs:
            pattern = os.path.join(result_dir, "**", name)
            matches = glob.glob(pattern, recursive=True)
        else:
            pattern = os.path.join(result_dir, name)
            matches = glob.glob(pattern)

        if matches:
            found.append(name)
        else:
            missing.append(name)

    total = len(filenames)
    ratio = len(found) / total

    if len(missing) == 0:
        return _ok(f"全部 {total} 个文件都存在")

    return _partial(ratio, f"找到 {len(found)}/{total}: 缺失 {', '.join(missing[:5])}")
