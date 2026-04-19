"""
Operation Pipeline：文件批量操作任务（Word/Excel/PPT/编程/组合文档）。

特殊逻辑:
    - add_pipeline_args: --gt-cache-dir
    - stage_init: GT 文件下载 + flatten
    - stage_evaluate: 3 策略评估（自定义 .py / OSWorld / 文件比对）
"""

import os
import sys

# 路径设置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(SRC_DIR)
EXAMPLES_DIR = SRC_DIR
UBUNTU_ENV_DIR = REPO_ROOT
STAGES_DIR = os.path.join(SRC_DIR, "stages")
for _p in [SRC_DIR, SCRIPT_DIR, STAGES_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline_base import BasePipeline, TaskItem, UNIFIED_TASKS_DIR
from task_scanner import scan_unified_tasks

from self_operation_pipeline.run_self_operation_pipeline_parallel import (
    stage1_initialize_with_flatten,
    stage2_execute_gui_only as op_stage2_gui_only,
    stage3_evaluate_operation,
)
from run_QA_pipeline_parallel import stage2_execute_agent_parallel


class OperationPipeline(BasePipeline):
    """
    Operation 任务 Pipeline。

    功能:
        扫描 FileOperate 类型任务，通过 Plan Agent 或 gui_only 模式执行，
        评估结果文件与 Ground Truth 的匹配度。
    """

    @property
    def pipeline_name(self):
        return "operation"

    @property
    def default_subset_file(self):
        return os.path.join(UNIFIED_TASKS_DIR, "subsets", "operation_subset.txt")

    def add_pipeline_args(self, parser):
        """
        添加 Operation 特有参数。

        输入:
            parser: ArgumentParser 实例
        """
        parser.add_argument(
            "--gt-cache-dir", type=str,
            default=os.path.join(STAGES_DIR, "self_operation_pipeline", "gt_cache"),
            help="Ground Truth 文件缓存目录（由 download_resources.py 填充）",
        )

    def scan_tasks(self):
        """
        扫描 Operation 任务。

        从统一任务目录中扫描以 Operation-FileOperate- 开头且不含 SearchAndWrite 的任务。
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
            UNIFIED_TASKS_DIR, pipeline="operation", allowed_ids=allowed_ids
        )
        return [TaskItem(
            task_id=task_id,
            task_uid=config.get("task_uid", task_id),
            task_path=path, task_config=config,
        ) for task_id, path, config in raw]

    def stage_init(self, task, config, log):
        """
        环境初始化（含 flatten 下载文件到共享目录根层级）。

        输入:
            task: TaskItem
            config: ContainerSetConfig
            log: logger
        输出:
            bool
        """
        return stage1_initialize_with_flatten(task.task_config, config, log)

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
            return op_stage2_gui_only(
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
        Operation 任务评估（3 策略：自定义 .py / OSWorld / 文件比对）。

        输入:
            task: TaskItem
            agent_result: stage_execute 返回的结果字典
            config: ContainerSetConfig
            log: logger
        输出:
            评估结果字典
        """
        return stage3_evaluate_operation(
            task.task_config, agent_result, task.task_path,
            config, self.args.gt_cache_dir, log,
            save_result_dir=self.args.save_result_dir,
        )


if __name__ == "__main__":
    OperationPipeline().main()
