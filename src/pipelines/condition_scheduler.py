"""
ConditionScheduler：condition 级全局任务调度器（跨 pipeline 混跑）。

背景:
    run_ablation 旧行为是按 pipeline 串行（一个 pipeline 的所有任务跑完
    才开始下一个），每个 pipeline 收尾时并行度逐渐掉到 0，资源利用率
    呈"山峰形"。本调度器把一个 condition 内所有 pipeline 的任务摊平到
    同一个全局 worker pool 混跑，任一 pipeline 的空闲槽位立即被其他
    pipeline 的任务填上，整个 condition 只在最末尾有一次 drain。

依赖:
    - pipeline 实例需先完成 prepare_run()（健康检查/资源注入/pre_run_hook），
      并注入同一个 ResourcePool（全局 group 队列保证容器命名不冲突）。
    - 任务执行复用 BasePipeline._run_single_task_wrapper（其内部自带
      group/内存/端口的申请与释放），结果记录复用 record_task_result。

按 pipeline 限流（可选）:
    pipeline_caps 指定某 pipeline 同时在跑的任务数上限（如外部服务有
    并发连接许可限制时）。实现为"提交侧过滤"而非信号量阻塞——被限流的
    任务留在待选队列，worker 槽位让给其他 pipeline 的任务，不浪费并发。
"""

import logging
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Dict, List, Optional, Tuple

from pipeline_base import BasePipeline, ResourcePool, TaskItem, dashboard_session


class ConditionScheduler:
    """
    condition 内全局任务调度器。

    功能:
        把多个 (pipeline, tasks) 摊平为全局任务流，提交到一个共享的
        ThreadPoolExecutor（max_workers = ResourcePool.max_parallel_tasks）
        混跑；完成时调用对应 pipeline 的 record_task_result 归档；
        统计 per-pipeline 的 span（首任务开始→末任务结束）等指标。

    输入:
        resource_pool: 共享资源池（仅用其 max_parallel_tasks 作并发上限；
            group/内存等资源由各任务 wrapper 内部向 pool 申请）
        log: logger
        progress_state: 共享 ProgressState（可选，用于任务总数与完成计数）
        dashboard: DashboardRenderer（可选，调度期间由本类统一启停）
        pipeline_caps: {pipeline_name: 最大并发任务数}（可选，>=1）
    """

    def __init__(self, resource_pool: ResourcePool, log: logging.Logger,
                 progress_state=None, dashboard=None,
                 pipeline_caps: Optional[Dict[str, int]] = None):
        self.pool = resource_pool
        self.log = log
        self.progress_state = progress_state
        self.dashboard = dashboard
        self.pipeline_caps = dict(pipeline_caps or {})
        for name, cap in self.pipeline_caps.items():
            if cap < 1:
                raise ValueError(
                    f"pipeline_caps[{name}]={cap} 非法：上限必须 >= 1")

    @staticmethod
    def _interleave(prepared: List[Tuple[BasePipeline, List[TaskItem]]]
                    ) -> "deque[Tuple[BasePipeline, TaskItem]]":
        """
        把各 pipeline 的任务按轮转（round-robin）交错排成全局待选队列。

        交错的目的:
            1. 各 pipeline 尽早都有任务在跑（共享服务负载更平滑）；
            2. 配合 per-pipeline 限流时减少队首被同一 pipeline 占满的情况。

        输入:
            prepared: [(pipeline_instance, tasks), ...]
        输出:
            deque[(pipeline_instance, task)]
        """
        queues = [deque((inst, t) for t in tasks)
                  for inst, tasks in prepared if tasks]
        pending: "deque[Tuple[BasePipeline, TaskItem]]" = deque()
        while any(queues):
            for q in queues:
                if q:
                    pending.append(q.popleft())
        return pending

    def run(self, prepared: List[Tuple[BasePipeline, List[TaskItem]]]
            ) -> Dict[str, Dict[str, Any]]:
        """
        执行混跑调度（阻塞直到所有任务完成）。

        输入:
            prepared: [(pipeline_instance, tasks), ...]，各实例已完成
                prepare_run 且共享同一 ResourcePool
        输出:
            per-pipeline 统计 {pipeline_name: {
                "span_seconds": 首任务开始→末任务结束的墙钟跨度,
                "task_time_sum_sec": 各任务 elapsed 之和,
                "errors": 异常/中断任务数,
                "tasks": 任务总数,
            }}
        """
        pending = self._interleave(prepared)
        total = len(pending)
        if self.progress_state:
            self.progress_state.set_task_total(total)
            # 混跑模式：注册 per-pipeline 任务总数（dashboard 逐 pipeline 行）
            if hasattr(self.progress_state, "set_pipeline_task_totals"):
                self.progress_state.set_pipeline_task_totals(
                    {inst.pipeline_name: len(tasks) for inst, tasks in prepared})

        stats: Dict[str, Dict[str, Any]] = {}
        in_flight: Dict[str, int] = {}
        for inst, tasks in prepared:
            stats[inst.pipeline_name] = {
                "first_start": None, "last_end": None,
                "task_time_sum_sec": 0.0, "errors": 0, "tasks": len(tasks),
            }
            in_flight[inst.pipeline_name] = 0

        max_workers = self.pool.max_parallel_tasks
        futures: Dict[Any, Tuple[BasePipeline, TaskItem]] = {}

        self.log.info(
            "[Scheduler] 混跑开始: %d 个 pipeline 共 %d 个任务, 全局并发=%d%s",
            len(prepared), total, max_workers,
            f", 限流={self.pipeline_caps}" if self.pipeline_caps else "",
        )

        def _cap_ok(name: str) -> bool:
            """判断该 pipeline 是否未达并发上限。"""
            cap = self.pipeline_caps.get(name)
            return cap is None or in_flight[name] < cap

        with dashboard_session(self.dashboard):
            with ThreadPoolExecutor(max_workers=max_workers) as executor:

                def _submit_eligible():
                    """
                    从待选队列挑出未被限流的任务填满空闲槽位。
                    被限流的任务按原顺序放回队首，等待该 pipeline 让出额度。
                    """
                    blocked: "deque[Tuple[BasePipeline, TaskItem]]" = deque()
                    while pending and len(futures) < max_workers:
                        inst, task = pending.popleft()
                        name = inst.pipeline_name
                        if not _cap_ok(name):
                            blocked.append((inst, task))
                            continue
                        in_flight[name] += 1
                        if stats[name]["first_start"] is None:
                            stats[name]["first_start"] = time.time()
                        fut = executor.submit(inst._run_single_task_wrapper, task)
                        futures[fut] = (inst, task)
                    # extendleft 会反序，先 reverse 以保持原相对顺序
                    pending.extendleft(reversed(blocked))

                _submit_eligible()
                # 退出条件含 pending：不变量上 futures 空 ⇒ in_flight 全 0
                # ⇒ 任何 cap>=1 的任务都可提交，pending 不可能滞留；
                # 此处作防御性兜底，万一不变量被破坏则报错而非静默丢任务
                while futures or pending:
                    if not futures:
                        self.log.error(
                            "[Scheduler] 调度不变量被破坏: %d 个任务滞留在"
                            "待选队列但无在途任务，放弃这些任务",
                            len(pending))
                        break
                    done, _ = wait(list(futures), return_when=FIRST_COMPLETED)
                    for fut in done:
                        inst, task = futures.pop(fut)
                        name = inst.pipeline_name
                        in_flight[name] -= 1
                        stats[name]["last_end"] = time.time()
                        try:
                            result = fut.result()
                            stats[name]["task_time_sum_sec"] += float(
                                result.get("elapsed_time_sec", 0) or 0)
                            if result.get("interrupted"):
                                stats[name]["errors"] += 1
                            inst.record_task_result(
                                task, result, dashboard=self.dashboard)
                        except Exception as exc:
                            stats[name]["errors"] += 1
                            self.log.error("[%s/%s] 任务异常: %s",
                                           name, task.task_id, exc)
                    _submit_eligible()

        out: Dict[str, Dict[str, Any]] = {}
        for name, st in stats.items():
            if st["first_start"] is not None and st["last_end"] is not None:
                span = st["last_end"] - st["first_start"]
            else:
                span = 0.0
            out[name] = {
                "span_seconds": round(span, 1),
                "task_time_sum_sec": round(st["task_time_sum_sec"], 1),
                "errors": st["errors"],
                "tasks": st["tasks"],
            }
        self.log.info("[Scheduler] 混跑结束: %s", out)
        return out
