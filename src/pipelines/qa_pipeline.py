"""
QA Pipeline：信息检索任务（WebSearch / FileSearch / VisualSearch）。

功能:
    - 从统一任务目录扫描 QA 类型任务
    - Stage Init: 重建容器 + 下载任务文件
    - Stage Execute: Plan Agent 分解 -> 多 GUI Agent 并行 / gui_only 单 Agent
    - Stage Evaluate: 答案模糊匹配
"""

import os
import sys

# pipeline_base 已统一设置 sys.path（SRC_DIR / STAGES_DIR / INFRA_DIR / AGENTS_DIR）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pipeline_base import BasePipeline, TaskItem, UNIFIED_TASKS_DIR
from task_scanner import scan_unified_tasks
from parallel_benchmark.eval.qa_run_contracts import build_skipped_task_result

# 导入 QA 特有的业务函数
from run_QA_pipeline import (
    stage3_evaluate,
)
from run_QA_pipeline_parallel import (
    stage1_initialize_parallel,
    stage2_execute_agent_parallel,
    stage2_execute_gui_only,
)


class QAPipeline(BasePipeline):
    """
    QA 任务 Pipeline。

    功能:
        扫描 task_type=QA 的任务，通过 Plan Agent 或 gui_only 模式执行，
        使用模糊匹配评估答案。

    说明:
        开源版本单服务器部署，QA 与其它 pipeline 共用 deploy.yaml 的
        server.* 配置。如需跨机调度，可通过 CLI --vm-ip / --shared-base-dir /
        --qcow2-path 在命令行覆盖。
    """

    @property
    def pipeline_name(self):
        return "qa"

    @property
    def default_subset_file(self):
        return os.path.join(UNIFIED_TASKS_DIR, "subsets", "qa_subset.txt")

    def scan_tasks(self):
        """
        扫描 QA 任务。

        从统一任务目录中扫描 task_type=="QA" 且不含 OnlineShopping 的任务。
        ablation 模式下通过 subset 文件过滤（subset 文件内容为 task_id 格式）。

        输出:
            TaskItem 列表
        """
        allowed_ids = None
        if self.args.mode == "ablation":
            subset_path = self.default_subset_file
            if os.path.isfile(subset_path):
                with open(subset_path) as f:
                    allowed_ids = {line.strip() for line in f
                                   if line.strip() and not line.startswith("#")}

        raw = scan_unified_tasks(
            UNIFIED_TASKS_DIR, pipeline="qa", allowed_ids=allowed_ids
        )
        return [TaskItem(
            task_id=task_id,
            task_uid=config.get("task_uid", task_id),
            task_path=path, task_config=config,
        ) for task_id, path, config in raw]

    def stage_init(self, task, config, log):
        """
        调用 QA parallel 的 stage1_initialize_parallel。

        输入:
            task: TaskItem
            config: ContainerSetConfig
            log: logger
        输出:
            bool
        """
        return stage1_initialize_parallel(task.task_config, config, log)

    def build_pre_execution_result(self, task):
        """为 ``skip_eval`` QA 任务在调度前构造 SKIP 结果。

        功能：覆写 BasePipeline 钩子；隔离任务直接返回三态结果，从而不获取
        容器组、不执行 stage_init/stage_execute；普通任务返回 None。
        输入参数：task 为待调度 QA TaskItem。
        输出返回值：SKIP 结果字典，或普通任务的 None。
        """
        if task.task_config.get("skip_eval") is not True:
            return None
        result = build_skipped_task_result(
            task.task_uid,
            task.task_path,
            task.task_config,
        )
        result.update({
            "task_id": task.task_id,
            "pipeline": self.pipeline_name,
            "agent_mode": self.args.agent_mode,
            "gui_agent": self.args.gui_agent,
            "status": "skip",
            "score": None,
            "pass": None,
            "plan_rounds": 0,
            "gui_rounds_total": 0,
            "gui_steps_sequential": 0,
            "token_plan": 0,
            "token_gui": 0,
            "token_total": 0,
            "cost_usd": 0.0,
            "elapsed_time_sec": 0.0,
        })
        return result

    def stage_execute(self, task, config, log):
        """
        根据 agent_mode 调用 plan 或 gui_only 执行。

        输入:
            task: TaskItem
            config: ContainerSetConfig
            log: logger
        输出:
            (result_dict, controller_vm1)
        """
        if self.args.agent_mode == "gui_only":
            return stage2_execute_gui_only(
                task.task_config, task.task_uid, config, log,
                gui_agent=self.args.gui_agent,
                max_rounds=self.args.gui_max_rounds,
                gui_timeout=self.args.gui_timeout,
                output_dir=self.get_output_dir(),
            )
        return stage2_execute_agent_parallel(
            task.task_config, task.task_uid, config, log,
        )

    def stage_evaluate(self, task, agent_result, config, log):
        """
        调用 QA 评估（模糊匹配）。

        输入:
            task: TaskItem
            agent_result: stage_execute 返回的结果字典
            config: ContainerSetConfig
            log: logger
        输出:
            评估结果字典
        """
        return stage3_evaluate(task.task_config, agent_result, task.task_path)


if __name__ == "__main__":
    QAPipeline().main()
