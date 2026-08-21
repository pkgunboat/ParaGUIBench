"""WebMall 调度前置隔离与评价结果统计契约。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


TaskItem = Tuple[str, str, Dict[str, Any]]


def build_task_provenance(task_config: Mapping[str, Any]) -> Dict[str, Any]:
    """提取 WebMall 运行结果必须携带的任务与动态 gold 版本信息。

    功能：为串行、并行和预执行 SKIP 结果提供同一组追溯字段；未显式修订
    的历史任务按 revision 1 记录，未使用动态 gold 的字段保留空字符串。
    输入参数：task_config 为已加载的 WebMall 任务配置。
    输出返回值：包含 task_id、task_revision、gold_snapshot_id/path 和
    gold_catalog_sha256 的结果元数据字典。
    """

    return {
        "task_id": str(task_config.get("task_id") or ""),
        "task_revision": task_config.get("task_revision", 1),
        "gold_snapshot_id": str(task_config.get("gold_snapshot_id") or ""),
        "gold_snapshot_path": str(task_config.get("gold_snapshot_path") or ""),
        "gold_catalog_sha256": str(task_config.get("gold_catalog_sha256") or ""),
    }


def get_skip_eval_reason(task_config: Mapping[str, Any]) -> str:
    """读取任务的人工隔离原因，并在缺失时给出稳定说明。

    功能：统一串行、并行 WebMall 入口对 ``skip_eval`` 原因的读取方式。
    输入参数：task_config 为已加载的任务配置映射。
    输出返回值：去除首尾空白后的非空原因字符串。
    """
    reason = str(task_config.get("skip_eval_reason") or "").strip()
    return reason or "Task is marked skip_eval=true and requires reannotation."


def build_skip_evaluation(task_config: Mapping[str, Any]) -> Dict[str, Any]:
    """构造不会被误判为失败的 WebMall SKIP 评价结果。

    功能：同时保留历史 ``passed`` 和通用 ``pass`` 字段，二者均设为
    ``None``，并显式写入 ``status=skip``。
    输入参数：task_config 为已加载的任务配置映射。
    输出返回值：可直接写入 ``evaluator_output`` 的字典。
    """
    reason = get_skip_eval_reason(task_config)
    return {
        "passed": None,
        "pass": None,
        "score": None,
        "max_score": None,
        "status": "skip",
        "reason": reason,
        "skip_eval_reason": reason,
    }


def build_skipped_task_result(
    task_uid: str,
    task_path: str,
    task_config: Mapping[str, Any],
) -> Dict[str, Any]:
    """在分配 Agent、内存或虚拟机前构造完整 SKIP 记录。

    功能：把被人工隔离的任务直接转换为可持久化运行结果，使两个
    WebMall 入口不必启动任何执行资源。
    输入参数：task_uid 为任务唯一标识；task_path 为任务 JSON 路径；
    task_config 为已加载的任务配置映射。
    输出返回值：与 WebMall pipeline 结果结构兼容的字典。
    """
    reason = get_skip_eval_reason(task_config)
    instruction = str(task_config.get("instruction") or "")
    return {
        **build_task_provenance(task_config),
        "task_uid": task_uid,
        "task_path": task_path,
        "task_tag": str(task_config.get("task_tag") or ""),
        "answer_type": str(task_config.get("answer_type") or ""),
        "instruction": instruction,
        "instruction_raw": instruction,
        "expected_answer": task_config.get("answer", ""),
        "expected_urls": task_config.get("expected_urls", []),
        "model_output_answer": "",
        "plan_agent_model": "",
        "gui_agent_model": "",
        "plan_agent_total_rounds": 0,
        "evaluator_output": build_skip_evaluation(task_config),
        "interrupted": False,
        "interrupt_reason": "",
        "skipped": True,
        "skip_eval_reason": reason,
        "run_status": "SKIP",
        "token_usage": None,
        "bookmark_reset": {},
    }


def build_evaluator_error(reason: str) -> Dict[str, Any]:
    """把评价器异常表示为无分数的独立状态。

    功能：避免评价器崩溃被压缩成 ``pass=False`` 或 Agent 中断。
    输入参数：reason 为异常原因或诊断文本。
    输出返回值：可直接写入 ``evaluator_output`` 的字典。
    """
    normalized_reason = str(reason or "unknown evaluator error").strip()
    return {
        "passed": None,
        "pass": None,
        "score": None,
        "max_score": None,
        "status": "evaluator_error",
        "reason": normalized_reason,
        "error": normalized_reason,
    }


def partition_skipped_tasks(
    task_items: Sequence[TaskItem],
) -> Tuple[List[TaskItem], Dict[str, Dict[str, Any]]]:
    """在执行器提交前拆分可运行任务和人工隔离任务。

    功能：保证 ``skip_eval=true`` 的任务不会申请内存、端口、容器或
    Agent，同时预先生成其持久化结果。
    输入参数：task_items 为 ``(uid, path, config)`` 三元组序列。
    输出返回值：可运行任务列表及按 UID 索引的 SKIP 结果字典。
    """
    runnable: List[TaskItem] = []
    skipped: Dict[str, Dict[str, Any]] = {}
    for task_uid, task_path, task_config in task_items:
        if task_config.get("skip_eval") is True:
            skipped[task_uid] = build_skipped_task_result(
                task_uid,
                task_path,
                task_config,
            )
        else:
            runnable.append((task_uid, task_path, task_config))
    return runnable, skipped


def classify_webmall_result(task_result: Mapping[str, Any]) -> str:
    """将单项结果映射为互斥的 WebMall 运行状态。

    功能：按 SKIP、EVALUATOR_ERROR、INTERRUPTED、PASS、FAIL 的优先级
    分类，兼容 ``passed``、``pass`` 和旧式分数字段。
    输入参数：task_result 为单个任务的持久化结果映射。
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
    if task_result.get("skipped") or evaluator_status == "skip" or outer_status == "skip":
        return "SKIP"
    if evaluator_status == "evaluator_error" or outer_status == "evaluator_error":
        return "EVALUATOR_ERROR"
    if task_result.get("interrupted"):
        return "INTERRUPTED"

    if isinstance(evaluator_output, Mapping):
        outcome = evaluator_output.get("passed", evaluator_output.get("pass"))
        if outcome is True:
            return "PASS"
        if outcome is False:
            return "FAIL"
        score = evaluator_output.get("score")
        max_score = evaluator_output.get("max_score")
        if isinstance(score, (int, float)) and isinstance(max_score, (int, float)):
            return "PASS" if max_score > 0 and score == max_score else "FAIL"
    return "FAIL"


def summarize_webmall_results(
    results: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """统计互斥运行状态，并只用 PASS/FAIL 计算有效通过率。

    功能：将隔离、评价器异常和 Agent/基础设施中断从有效评分分母中
    排除，防止数据质量问题或系统故障改变模型分数。
    输入参数：results 为单项任务结果映射的可迭代对象。
    输出返回值：总数、五类状态计数、有效分母和通过率字典。
    """
    materialized = list(results)
    counts = {
        "PASS": 0,
        "FAIL": 0,
        "SKIP": 0,
        "EVALUATOR_ERROR": 0,
        "INTERRUPTED": 0,
    }
    for result in materialized:
        counts[classify_webmall_result(result)] += 1

    valid_evaluations = counts["PASS"] + counts["FAIL"]
    return {
        "total": len(materialized),
        "passed": counts["PASS"],
        "failed": counts["FAIL"],
        "skipped": counts["SKIP"],
        "evaluator_errors": counts["EVALUATOR_ERROR"],
        "interrupted": counts["INTERRUPTED"],
        "valid_evaluations": valid_evaluations,
        "pass_rate": (
            counts["PASS"] / valid_evaluations
            if valid_evaluations
            else None
        ),
    }
