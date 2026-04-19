"""
WebNavigate Pipeline：网页导航任务（收藏夹操作、浏览器设置）。

特殊逻辑:
    - stage_init: 重建 VM + 清空收藏夹
    - stage_evaluate: 书签 URL 匹配（不依赖 agent_result）
"""

import os
import sys
from typing import List

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

from run_webnavigate_pipeline_parallel import (
    reinitialize_vms,
    clear_bookmarks_parallel,
    stage2_execute_plan,
    stage2_execute_gui_only,
    stage3_evaluate,
)

# 默认任务 ID 列表（非 full 模式且无 ablation subset 时使用）
DEFAULT_TASK_IDS = [
    "Operation-WebOperate-WebNavigate-001",
    "Operation-WebOperate-WebNavigate-002",
    "Operation-WebOperate-WebNavigate-003",
    "Operation-WebOperate-WebNavigate-004",
    "Operation-WebOperate-WebNavigate-005",
    "Operation-WebOperate-WebNavigate-006",
    "Operation-WebOperate-WebNavigate-007",
    "Operation-WebOperate-WebNavigate-008",
    "Operation-WebOperate-WebNavigate-009",
    "Operation-WebOperate-WebNavigate-010",
    "Operation-WebOperate-WebNavigate-011",
    "Operation-WebOperate-Settings-001",
    "Operation-WebOperate-Settings-002",
    "Operation-WebOperate-Settings-003",
]


class WebNavigatePipeline(BasePipeline):
    """
    WebNavigate 任务 Pipeline。

    功能:
        扫描 WebNavigate/Settings 任务，通过 Plan Agent 或 gui_only 模式执行，
        评估浏览器书签是否匹配目标 URL。
    """

    @property
    def pipeline_name(self):
        return "webnavigate"

    @property
    def default_subset_file(self):
        return os.path.join(UNIFIED_TASKS_DIR, "subsets", "webnavigate_subset.txt")

    def scan_tasks(self):
        """
        扫描 WebNavigate 任务。

        从统一任务目录中扫描包含 WebOperate 且不含 SearchAndWrite 的任务。
        ablation 模式下通过 subset 文件过滤；非 full 模式且无 subset 时使用 DEFAULT_TASK_IDS。

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

        if allowed_ids is None and self.args.mode != "full":
            allowed_ids = set(DEFAULT_TASK_IDS)

        raw = scan_unified_tasks(
            UNIFIED_TASKS_DIR, pipeline="webnavigate", allowed_ids=allowed_ids
        )
        return [TaskItem(
            task_id=task_id,
            task_uid=config.get("task_uid", task_id),
            task_path=path,
            task_config=config,
        ) for task_id, path, config in raw]

    def stage_init(self, task, config, log):
        """
        重建 VM + 清空收藏夹。

        输入:
            task: TaskItem
            config: ContainerSetConfig
            log: logger
        输出:
            bool
        """
        prepare_url = task.task_config.get("prepare_script_path", "")
        if not reinitialize_vms(config, log, mode=self.args.reset_mode,
                                prepare_url=prepare_url):
            return False
        vm_ports = config.get_server_ports()
        clear_bookmarks_parallel(config.vm_ip, vm_ports, log)
        return True

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
                task.task_config, task.task_id, config, log,
                gui_agent=self.args.gui_agent,
                max_rounds=self.args.gui_max_rounds,
                gui_timeout=self.args.gui_timeout,
                output_dir=output_dir,
            )
        return stage2_execute_plan(
            task.task_config, task.task_id, config, log,
            output_dir=output_dir,
        )

    def stage_evaluate(self, task, agent_result, config, log):
        """
        书签评估（直接从 VM 读取书签，不依赖 agent_result）。

        输入:
            task: TaskItem
            agent_result: stage_execute 返回的结果字典（本函数未使用）
            config: ContainerSetConfig
            log: logger
        输出:
            评估结果字典
        """
        return stage3_evaluate(task.task_config, config, log)


if __name__ == "__main__":
    WebNavigatePipeline().main()
