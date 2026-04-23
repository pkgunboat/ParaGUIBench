"""
SearchWrite Pipeline：搜索 + 文档编辑任务（OnlyOffice 在线协作）。

特殊逻辑:
    - add_pipeline_args: --onlyoffice-url, --onlyoffice-host-ip
    - pre_run_hook: Stage0 OnlyOffice 文档准备（串行）
    - stage_execute: 注入共享链接 + Stage2.5 触发保存
    - stage_evaluate: xlsx 评估
"""

import os
import sys

# pipeline_base 已统一设置 sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pipeline_base import BasePipeline, TaskItem, UNIFIED_TASKS_DIR, SRC_DIR
from task_scanner import scan_unified_tasks

from self_operation_pipeline.run_searchwrite_pipeline_parallel import (
    resolve_document_sharing_url,
    stage0_prepare_documents,
    stage1_initialize as sw_stage1_initialize,
    _build_instruction_with_share_urls,
    stage2_execute_gui_only as sw_stage2_gui_only,
    stage2_5_trigger_save,
    stage3_evaluate,
)
from self_operation_pipeline.run_self_operation_pipeline_parallel import (
    stage1_initialize_with_flatten as op_stage1_initialize_with_flatten,
)
from run_QA_pipeline_parallel import (
    stage2_execute_agent_parallel,
)
from parallel_benchmark.eval.operation_evaluator import evaluate as operation_evaluate


class SearchWritePipeline(BasePipeline):
    """
    SearchWrite 任务 Pipeline。

    功能:
        扫描 SearchWrite 任务，先串行准备 OnlyOffice 共享链接，
        然后通过 Plan Agent 或 gui_only 模式执行，
        评估 xlsx 编辑结果。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._task_share_urls = {}  # Stage0 产出：{task_uid: {filename: share_url}}

    @property
    def pipeline_name(self):
        return "searchwrite"

    @property
    def default_subset_file(self):
        return os.path.join(UNIFIED_TASKS_DIR, "subsets", "searchwrite_subset.txt")

    def add_pipeline_args(self, parser):
        """
        添加 SearchWrite 特有参数。

        输入:
            parser: ArgumentParser 实例
        """
        # 默认从 deploy.yaml 的 services.onlyoffice.host_ip 读取；若未配置则退化到
        # server.vm_host（单机场景下两者相同）。环境变量 ONLYOFFICE_HOST_IP 亦可覆盖。
        from config_loader import DeployConfig
        _deploy = DeployConfig()
        _default_oo_host = os.environ.get(
            "ONLYOFFICE_HOST_IP",
            _deploy.onlyoffice_host or _deploy.vm_host,
        )
        _default_oo_url = os.environ.get(
            "ONLYOFFICE_URL",
            f"http://{_default_oo_host}:{_deploy.onlyoffice_flask_port}",
        )
        parser.add_argument(
            "--onlyoffice-url", type=str,
            default=_default_oo_url,
            help="OnlyOffice 文档共享服务 URL（默认读 deploy.yaml/ONLYOFFICE_HOST_IP；也可用 ONLYOFFICE_URL 覆盖）",
        )
        parser.add_argument(
            "--onlyoffice-host-ip", type=str,
            default=_default_oo_host,
            help="OnlyOffice 宿主机 IP（默认读 configs/deploy.yaml 或 ONLYOFFICE_HOST_IP）",
        )

    def scan_tasks(self):
        """
        扫描 SearchWrite 任务。

        从统一任务目录中扫描包含 SearchAndWrite 的任务。
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
            UNIFIED_TASKS_DIR, pipeline="searchwrite", allowed_ids=allowed_ids
        )
        return [TaskItem(
            task_id=task_id,
            task_uid=config.get("task_uid", task_id),
            task_path=path, task_config=config,
        ) for task_id, path, config in raw]

    def pre_run_hook(self, tasks):
        """
        Stage0：串行准备 OnlyOffice 文档共享链接。
        必须在并行调度之前完成，避免并发写冲突。

        输入:
            tasks: 待执行的任务列表
        """
        def _is_osworld_task(task):
            cfg = task.task_config
            return (
                cfg.get("task_type") == "OSWorld脚本"
                or cfg.get("evaluator_path", "").endswith(".json")
            )

        onlyoffice_tasks = [t for t in tasks if not _is_osworld_task(t)]
        if not onlyoffice_tasks:
            self.log.info("未检测到 OnlyOffice 类型 SearchWrite 任务，跳过 Stage0")
            for task in tasks:
                task.extra["share_urls"] = {}
            return

        self.args.onlyoffice_url = resolve_document_sharing_url(
            self.args.onlyoffice_url,
            self.args.onlyoffice_host_ip,
            log=self.log,
        )
        task_items_raw = [(t.task_uid, t.task_path, t.task_config) for t in onlyoffice_tasks]
        self._task_share_urls = stage0_prepare_documents(
            task_items_raw,
            self.args.onlyoffice_url,
            self.args.onlyoffice_host_ip,
            self.log,
        )
        # 将 share_urls 写入各 task 的 extra
        for task in tasks:
            task.extra["share_urls"] = self._task_share_urls.get(task.task_uid, {})

    def stage_init(self, task, config, log):
        """
        SearchWrite 专用初始化。

        输入:
            task: TaskItem
            config: ContainerSetConfig
            log: logger
        输出:
            bool
        """
        evaluator_path = task.task_config.get("evaluator_path", "")
        if (
            task.task_config.get("task_type") == "OSWorld脚本"
            or evaluator_path.endswith(".json")
        ):
            return op_stage1_initialize_with_flatten(task.task_config, config, log)
        return sw_stage1_initialize(config, log)

    def stage_execute(self, task, config, log):
        """
        执行任务 + Stage 2.5 触发 OnlyOffice 保存。

        输入:
            task: TaskItem
            config: ContainerSetConfig
            log: logger
        输出:
            (result_dict, controller_vm1)
        """
        output_dir = self.get_output_dir()
        share_urls = task.extra.get("share_urls", {})
        instruction = task.task_config.get("instruction", "")

        if share_urls:
            augmented_instruction = _build_instruction_with_share_urls(
                instruction, share_urls,
            )
            modified_config = dict(task.task_config)
            modified_config["instruction"] = augmented_instruction
        else:
            modified_config = task.task_config

        if self.args.agent_mode == "gui_only":
            result, ctrl = sw_stage2_gui_only(
                modified_config, task.task_uid, config, log,
                gui_agent=self.args.gui_agent,
                max_rounds=self.args.gui_max_rounds,
                gui_timeout=self.args.gui_timeout,
                output_dir=output_dir,
            )
        else:
            result, ctrl = stage2_execute_agent_parallel(
                modified_config, task.task_uid, config, log,
            )

        # Stage 2.5: 触发 OnlyOffice 保存
        if share_urls:
            stage2_5_trigger_save(
                config, task.task_uid, share_urls,
                self.args.onlyoffice_url, log,
            )

        return result, ctrl

    def stage_evaluate(self, task, agent_result, config, log):
        """
        评估：优先使用 eval_rules（operation_evaluator），
        其次检查 evaluator_path（OSWorld JSON），
        最后使用 xlsx 评估。

        输入:
            task: TaskItem
            agent_result: stage_execute 返回的结果字典
            config: ContainerSetConfig
            log: logger
        输出:
            评估结果字典
        """
        # 路径 0：如果任务配置中有 eval_rules，使用 operation_evaluator
        eval_rules = task.task_config.get("eval_rules", [])
        if eval_rules:
            log.info("检测到 eval_rules，使用 operation_evaluator 进行评估")
            result_dir = self.args.save_result_dir
            if not result_dir:
                log.warning("save_result_dir 未设置，operation_evaluator 需要结果目录")
                return {"score": 0.0, "pass": False, "reason": "save_result_dir 未设置"}

            task_result_dir = os.path.join(result_dir, task.task_config.get("task_id", ""))
            if not os.path.isdir(task_result_dir):
                log.warning("结果目录不存在: %s", task_result_dir)
                return {"score": 0.0, "pass": False, "reason": f"结果目录不存在: {task_result_dir}"}

            try:
                return operation_evaluate(task_result_dir, task.task_config)
            except Exception as exc:
                log.error("operation_evaluator 评估失败: %s", exc)
                return {"score": 0.0, "pass": False, "reason": f"评估异常: {exc}"}

        # 路径 1：如果有 evaluator_path 且为 .json，使用 OSWorld 评价器
        evaluator_path = task.task_config.get("evaluator_path", "")
        if evaluator_path and evaluator_path.endswith(".json"):
            log.info("检测到 OSWorld JSON 评测配置: %s", evaluator_path)
            try:
                from parallel_benchmark.eval.osworld_evaluator import evaluate_osworld_task
                # OSWorld JSON 评测配置位于 src/parallel_benchmark/<evaluator_path>
                json_path = os.path.join(SRC_DIR, "parallel_benchmark", evaluator_path)
                vm_pairs = config.get_vm_pairs()
                vm_port = vm_pairs[0][0]  # 使用第一个 VM
                return evaluate_osworld_task(
                    evaluator_json_path=json_path,
                    vm_ip=config.vm_ip,
                    vm_port=vm_port,
                    shared_host_dir=config.shared_host_dir,
                    log=log,
                )
            except Exception as exc:
                log.error("OSWorld 评测执行失败: %s", exc, exc_info=True)
                return {"score": 0.0, "pass": False, "reason": f"OSWorld 评测异常: {exc}"}

        # 路径 2：否则使用原有的 xlsx 评估逻辑
        share_urls = task.extra.get("share_urls", {})
        return stage3_evaluate(
            task.task_uid, task.task_config, share_urls,
            self.args.onlyoffice_url, log,
            save_result_dir=self.args.save_result_dir,
        )


if __name__ == "__main__":
    SearchWritePipeline().main()
