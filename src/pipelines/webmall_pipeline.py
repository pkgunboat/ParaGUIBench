"""
WebMall Pipeline：电商网站操作任务（收藏/购物车/下单）。

特殊逻辑:
    - pre_run_hook: 检查 WebMall 商店可达性
    - stage_init: 重建容器
    - stage_execute: 改写 string 任务指令
    - stage_evaluate: 3 种评估方式（string/cart/checkout）
"""

import os
import sys

# 路径设置：开源版新布局
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(SRC_DIR)
EXAMPLES_DIR = SRC_DIR
UBUNTU_ENV_DIR = REPO_ROOT
for _p in [SRC_DIR, SCRIPT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline_base import BasePipeline, TaskItem, UNIFIED_TASKS_DIR
from task_scanner import scan_unified_tasks

# 导入 WebMall 特有函数
from run_webmall_pipeline import (
    check_webmall_shops,
)
from run_webmall_pipeline_parallel import (
    reinitialize_vms_parallel,
    stage2_execute_parallel,
    stage2_execute_gui_only,
    stage3_evaluate_parallel,
)


class WebMallPipeline(BasePipeline):
    """
    WebMall 任务 Pipeline。

    功能:
        扫描 WebMall 任务，通过 Plan Agent 或 gui_only 模式执行，
        评估购物车/收藏夹/下单结果。
    """

    @property
    def pipeline_name(self):
        return "webmall"

    @property
    def default_subset_file(self):
        return os.path.join(UNIFIED_TASKS_DIR, "subsets", "webmall_subset_20.txt")

    def scan_tasks(self):
        """
        扫描 WebMall 任务。

        从统一任务目录中扫描包含 OnlineShopping 的任务。
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
            UNIFIED_TASKS_DIR, pipeline="webmall", allowed_ids=allowed_ids
        )
        return [TaskItem(
            task_id=task_id,
            task_uid=config.get("task_uid", task_id),
            task_path=path, task_config=config,
        ) for task_id, path, config in raw]

    def pre_run_hook(self, tasks):
        """
        检查 WebMall 商店可达性。

        输入:
            tasks: 待执行的任务列表
        """
        check_webmall_shops()

    def stage_init(self, task, config, log):
        """
        重建 VM 容器。

        输入:
            task: TaskItem
            config: ContainerSetConfig
            log: logger
        输出:
            bool
        """
        return reinitialize_vms_parallel(config, log, mode=self.args.reset_mode)

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
        output_dir = self.get_output_dir()
        if self.args.agent_mode == "gui_only":
            return stage2_execute_gui_only(
                task.task_config, task.task_uid, config, log,
                gui_agent=self.args.gui_agent,
                max_rounds=self.args.gui_max_rounds,
                gui_timeout=self.args.gui_timeout,
                output_dir=output_dir,
            )
        return stage2_execute_parallel(
            task.task_config, task.task_uid, config, log,
            gui_agent=self.args.gui_agent,
            output_dir=output_dir,
        )

    def stage_evaluate(self, task, agent_result, config, log):
        """
        调用 WebMall 评估。

        输入:
            task: TaskItem
            agent_result: stage_execute 返回的结果字典
            config: ContainerSetConfig
            log: logger
        输出:
            评估结果字典
        """
        return stage3_evaluate_parallel(task.task_config, agent_result, config, log)


if __name__ == "__main__":
    WebMallPipeline().main()
