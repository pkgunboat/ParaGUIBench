"""QA 批量调度、SKIP 结果与汇总统计的纯函数契约。"""

from __future__ import annotations

import json
import os
import shlex
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


TaskItem = Tuple[str, str, Dict[str, Any]]


def build_explicit_conda_activation(
    vm_user: str,
    bench_conda_activate: Any = "",
    required_conda_env: Any = "",
) -> str:
    """只根据显式配置构造宿主机 conda 激活命令。

    功能：优先采用非空 ``BENCH_CONDA_ACTIVATE``；其次仅在调用方显式提供
    ``REQUIRED_CONDA_ENV`` 时构造安全转义的 conda 激活命令；两者均未配置
    时返回 shell no-op ``:``，不得猜测旧环境名或当前工作树环境名。
    输入参数：vm_user 为远端宿主机用户名；bench_conda_activate 为显式完整
    激活命令；required_conda_env 为显式 conda 环境名。
    输出返回值：可置于 ``&&`` 前的激活命令或 ``:``。
    """
    explicit_command = str(bench_conda_activate or "").strip()
    if explicit_command:
        return explicit_command
    environment_name = str(required_conda_env or "").strip()
    if not environment_name:
        return ":"
    conda_script = f"/home/{str(vm_user).strip()}/miniconda3/etc/profile.d/conda.sh"
    return (
        f"source {shlex.quote(conda_script)} "
        f"&& conda activate {shlex.quote(environment_name)}"
    )


def scan_qa_pipeline_tasks(tasks_dir: str) -> List[TaskItem]:
    """Scan the authoritative task directory for every QA-pipeline task.

    功能：递归读取真实 ``parallel_benchmark/tasks`` 目录，只保留
    ``task_type=QA``、具有 task_uid 且不属于 OnlineShopping 的任务，与统一
    ``task_scanner.scan_unified_tasks(..., pipeline="qa")`` 保持相同语义。
    这会同时包含信息检索任务和明确标为 QA 的 FileOperate 任务。
    输入参数：tasks_dir 为权威任务 JSON 目录。
    输出返回值：按 task_id、task_uid 稳定排序的 ``(uid, path, config)``
    三元组列表。
    """
    if not os.path.isdir(tasks_dir):
        raise FileNotFoundError(f"未找到任务目录: {tasks_dir}")

    qa_tasks: List[TaskItem] = []
    for root, _, files in os.walk(tasks_dir):
        for filename in files:
            if not filename.endswith(".json"):
                continue
            task_path = os.path.join(root, filename)
            try:
                with open(task_path, "r", encoding="utf-8") as file_obj:
                    task_config = json.load(file_obj)
            except (OSError, ValueError, TypeError):
                continue
            if task_config.get("task_type") != "QA":
                continue
            task_id = str(task_config.get("task_id") or "")
            task_uid = str(task_config.get("task_uid") or "")
            if not task_uid or "OnlineShopping" in task_id:
                continue
            qa_tasks.append((task_uid, task_path, task_config))

    qa_tasks.sort(
        key=lambda item: (str(item[2].get("task_id") or ""), item[0])
    )
    return qa_tasks


def scan_information_retrieval_qa_tasks(tasks_dir: str) -> List[TaskItem]:
    """兼容旧调用名，返回完整 QA pipeline 任务集合。

    功能：保留本轮修复早期使用的函数名，避免外部导入中断；实际委托给
    ``scan_qa_pipeline_tasks``，不再按 InformationRetrieval 前缀静默漏任务。
    输入参数：tasks_dir 为权威任务 JSON 目录。
    输出返回值：完整 QA pipeline 任务三元组列表。
    """
    return scan_qa_pipeline_tasks(tasks_dir)


def get_skip_eval_reason(task_config: Mapping[str, Any]) -> str:
    """Return the configured skip reason with a stable fallback.

    功能：读取任务的 ``skip_eval_reason``，并在缺失或仅含空白时返回稳定
    的默认说明，保证调度器与评价器使用同一口径。
    输入参数：task_config 为任务配置映射。
    输出返回值：非空跳过原因字符串。
    """
    reason = str(task_config.get("skip_eval_reason") or "").strip()
    return reason or "Task is marked skip_eval=true and requires reannotation."


def build_skip_evaluation(task_config: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the evaluator-shaped result for a quarantined task.

    功能：产生 ``pass=None`` 且 ``status=skip`` 的标准评价结果，同时原样透传
    ``skip_eval_reason``，避免跳过被误计为失败或评价器错误。
    输入参数：task_config 为任务配置映射。
    输出返回值：与 QA 评价器返回结构兼容的字典。
    """
    reason = get_skip_eval_reason(task_config)
    return {
        "pass": None,
        "score": None,
        "status": "skip",
        "reason": reason,
        "skip_eval_reason": reason,
        "match_type": "skip_eval",
        "ref_text": "",
        "pred_text": "",
    }


def build_skipped_task_result(
    task_uid: str,
    task_path: str,
    task_config: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build a persisted pipeline result without allocating an Agent or VM.

    功能：为调度前被剔除的 ``skip_eval`` 任务构造可直接写入结果 JSON
    的完整记录，显式标记 ``run_status=SKIP`` 和原因。
    输入参数：task_uid 为任务 UID；task_path 为配置路径；task_config 为
    已加载任务配置。
    输出返回值：可持久化的 pipeline 任务结果字典。
    """
    reason = get_skip_eval_reason(task_config)
    return {
        "task_uid": task_uid,
        "task_path": task_path,
        "instruction": str(task_config.get("instruction") or ""),
        "expected_answer": str(task_config.get("answer") or ""),
        "model_output_answer": "",
        "plan_agent_model": "",
        "gui_agent_model": "",
        "plan_agent_total_rounds": 0,
        "evaluator_output": build_skip_evaluation(task_config),
        "plan_agent_last_round_output": "",
        "plan_agent_last_round_messages": [],
        "interrupted": False,
        "interrupt_reason": "",
        "skipped": True,
        "skip_eval_reason": reason,
        "run_status": "SKIP",
        "token_usage": None,
    }


def partition_skipped_tasks(
    task_items: Sequence[TaskItem],
) -> Tuple[List[TaskItem], Dict[str, Dict[str, Any]]]:
    """Remove quarantined tasks before executor submission.

    功能：在创建 worker future 前把 ``skip_eval=true`` 任务与可执行任务分开，
    并为每个被跳过任务生成持久化结果，从调度结构上保证不启动
    Agent 或容器。
    输入参数：task_items 为 ``(uid, path, config)`` 三元组序列。
    输出返回值：二元组；第一项是可提交任务列表，第二项是按 UID
    索引的 SKIP 结果字典。
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


def format_task_status(task_result: Mapping[str, Any]) -> str:
    """Map a task result to the dashboard/log status label.

    功能：以 SKIP 优先级最高的方式生成实时状态，其次是
    EVALUATOR_ERROR、INTERRUPTED、PASS 和 FAIL，避免 ``pass=None`` 的
    跳过或评价器错误任务被显示为 FAIL。
    输入参数：task_result 为已收集的任务结果映射。
    输出返回值：``SKIP``、``EVALUATOR_ERROR``、``INTERRUPTED``、
    ``PASS`` 或 ``FAIL``。
    """
    evaluator_output = task_result.get("evaluator_output")
    evaluator_status = (
        str(evaluator_output.get("status") or "").lower()
        if isinstance(evaluator_output, Mapping)
        else ""
    )
    outer_status = str(task_result.get("status") or "").lower()
    if task_result.get("skipped") or evaluator_status == "skip" or outer_status == "skip":
        return "SKIP"
    if evaluator_status == "evaluator_error" or outer_status == "evaluator_error":
        return "EVALUATOR_ERROR"
    if task_result.get("interrupted"):
        return "INTERRUPTED"
    if isinstance(evaluator_output, Mapping):
        evaluator_pass = evaluator_output.get(
            "pass",
            evaluator_output.get("passed"),
        )
        if evaluator_pass is True:
            return "PASS"
        if evaluator_pass is False:
            return "FAIL"
    if task_result.get("pass") is True:
        return "PASS"
    return "FAIL"


def summarize_qa_results(
    results: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Aggregate QA outcomes while excluding SKIP from the valid denominator.

    功能：分别统计通过、失败、跳过、中断与评价器错误；有效评价分母
    只包含 ``pass`` 为布尔值且状态不是 skip/evaluator_error 的结果。
    输入参数：results 为任务结果映射的可迭代对象。
    输出返回值：包含 total、passed、failed、skipped、interrupted、
    evaluator_errors、valid_evaluations 和 pass_rate 的统计字典。
    """
    materialized = list(results)
    passed = 0
    failed = 0
    skipped = 0
    interrupted = 0
    evaluator_errors = 0

    for result in materialized:
        evaluator_output = result.get("evaluator_output")
        evaluator_status = (
            str(evaluator_output.get("status") or "").lower()
            if isinstance(evaluator_output, Mapping)
            else ""
        )
        outer_status = str(result.get("status") or "").lower()
        if result.get("skipped") or evaluator_status == "skip" or outer_status == "skip":
            skipped += 1
            continue
        if evaluator_status == "evaluator_error" or outer_status == "evaluator_error":
            evaluator_errors += 1
            continue
        if result.get("interrupted"):
            interrupted += 1
            continue
        if isinstance(evaluator_output, Mapping):
            outcome = evaluator_output.get(
                "pass",
                evaluator_output.get("passed"),
            )
        else:
            outcome = result.get("pass")
        if outcome is True:
            passed += 1
        elif outcome is False:
            failed += 1

    valid_evaluations = passed + failed
    return {
        "total": len(materialized),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "interrupted": interrupted,
        "evaluator_errors": evaluator_errors,
        "valid_evaluations": valid_evaluations,
        "pass_rate": passed / valid_evaluations if valid_evaluations else None,
    }
