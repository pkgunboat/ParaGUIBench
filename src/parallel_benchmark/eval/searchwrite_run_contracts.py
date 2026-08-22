"""SearchWrite 评价状态、错误归因与有效分母的纯函数契约。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping


def uses_osworld_evaluator(task_config: Mapping[str, Any]) -> bool:
    """判断 SearchWrite 任务是否必须走 OSWorld 执行与评价链。

    功能：同时识别 ``task_type=OSWorld脚本`` 与 JSON
    ``evaluator_path``，供统一入口和 legacy 入口共用，避免只在
    评价阶段分流而遗漏任务准备。
    输入参数：task_config 为任务 JSON 配置映射。
    输出返回值：需要 OSWorld 执行链时返回 True，否则返回 False。
    """
    evaluator_path = str(task_config.get("evaluator_path") or "")
    return (
        task_config.get("task_type") == "OSWorld脚本"
        or evaluator_path.endswith(".json")
    )


def missing_expected_searchwrite_files(
    expected_names: Iterable[str],
    available_names: Iterable[str],
) -> list[str]:
    """找出 SearchWrite 预期文档中未进入当前评价集的文件。

    功能：以可信模板文件名为基准检查共享链接、下载结果或
    评价文件对是否完整，防止多文件任务仅凭余下文件满分通过。
    输入参数：expected_names 为预期文件名；available_names 为实际
    可用文件名。
    输出返回值：按文件名排序的缺失列表。
    """
    expected = {str(name) for name in expected_names if str(name)}
    available = {str(name) for name in available_names if str(name)}
    return sorted(expected - available)


def build_searchwrite_evaluator_error(reason: str) -> Dict[str, Any]:
    """构造 SearchWrite 评价基础设施故障的统一三态结果。

    功能：把下载、回写、GT/模板或评价器自身故障与 Agent 普通失败
    分离，供所有入口排除出有效评分分母。
    输入参数：reason 为故障诊断说明。
    输出返回值：``score=-1``、``pass=None``、状态为
    ``evaluator_error`` 的结果字典。
    """
    normalized_reason = str(reason or "unknown SearchWrite evaluator error")
    return {
        "score": -1.0,
        "pass": None,
        "status": "evaluator_error",
        "reason": normalized_reason,
    }


def classify_searchwrite_result(task_result: Mapping[str, Any]) -> str:
    """将 SearchWrite 任务结果映射为互斥运行状态。

    功能：按 EVALUATOR_ERROR、INTERRUPTED、PASS、FAIL、UNKNOWN 的
    优先级处理三态评价结果，防止 ``pass=None`` 被压成普通失败。
    输入参数：task_result 为单项 pipeline 结果映射。
    输出返回值：上述五种大写状态之一。
    """
    evaluator_output = task_result.get("evaluator_output")
    evaluator_status = (
        str(evaluator_output.get("status") or "").lower()
        if isinstance(evaluator_output, Mapping)
        else ""
    )
    outer_status = str(
        task_result.get("run_status") or task_result.get("status") or ""
    ).lower()
    if evaluator_status == "evaluator_error" or outer_status == "evaluator_error":
        return "EVALUATOR_ERROR"
    if task_result.get("interrupted"):
        return "INTERRUPTED"
    if isinstance(evaluator_output, Mapping):
        outcome = evaluator_output.get("pass")
        if outcome is True:
            return "PASS"
        if outcome is False:
            return "FAIL"
        return "UNKNOWN"
    return "UNKNOWN"


def summarize_searchwrite_results(
    results: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """汇总 SearchWrite 互斥状态并计算有效通过率。

    功能：只以真实 PASS 和 FAIL 为有效评分分母，单列评价器故障、
    Agent/基础设施中断及缺少自动 gold 的 UNKNOWN。
    输入参数：results 为任务结果映射的可迭代对象。
    输出返回值：各状态计数、有效分母和通过率字典。
    """
    materialized = list(results)
    counts = {
        "PASS": 0,
        "FAIL": 0,
        "EVALUATOR_ERROR": 0,
        "INTERRUPTED": 0,
        "UNKNOWN": 0,
    }
    for result in materialized:
        counts[classify_searchwrite_result(result)] += 1
    valid_evaluations = counts["PASS"] + counts["FAIL"]
    return {
        "total": len(materialized),
        "passed": counts["PASS"],
        "failed": counts["FAIL"],
        "evaluator_errors": counts["EVALUATOR_ERROR"],
        "interrupted": counts["INTERRUPTED"],
        "unknown": counts["UNKNOWN"],
        "valid_evaluations": valid_evaluations,
        "pass_rate": (
            counts["PASS"] / valid_evaluations
            if valid_evaluations
            else None
        ),
    }
