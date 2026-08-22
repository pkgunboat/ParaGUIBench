"""
SearchWrite Pipeline：搜索 + 文档编辑任务（OnlyOffice 在线协作）。

特殊逻辑:
    - add_pipeline_args: --onlyoffice-url, --onlyoffice-host-ip
    - pre_run_hook: Stage0 OnlyOffice 文档准备（串行）
    - stage_execute: 注入共享链接 + Stage2.5 触发保存
    - stage_evaluate: xlsx 评估
"""

import os
import fnmatch
import shutil
import sys
import tempfile

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
    fetch_document_file_via_api,
    base_url_from_share_url,
)
from self_operation_pipeline.run_self_operation_pipeline_parallel import (
    stage1_initialize_with_flatten as op_stage1_initialize_with_flatten,
)
from run_QA_pipeline_parallel import (
    stage2_execute_agent_parallel,
)
from parallel_benchmark.eval.operation_evaluator import evaluate as operation_evaluate
from parallel_benchmark.eval.searchwrite_run_contracts import (
    build_searchwrite_evaluator_error,
    missing_expected_searchwrite_files,
    uses_osworld_evaluator,
)


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
        # 多实例：deploy.yaml 配置 services.onlyoffice.instances 时，
        # 默认 URL 为逗号分隔的多个实例地址（stage0 按任务轮转路由）
        _default_oo_url = os.environ.get(
            "ONLYOFFICE_URL",
            ",".join(
                f"http://{_default_oo_host}:{inst['flask_port']}"
                for inst in _deploy.onlyoffice_instances
            ),
        )
        parser.add_argument(
            "--onlyoffice-url", type=str,
            default=_default_oo_url,
            help="OnlyOffice 文档共享服务 URL，支持逗号分隔多个实例"
                 "（默认读 deploy.yaml services.onlyoffice.instances/"
                 "ONLYOFFICE_HOST_IP；也可用 ONLYOFFICE_URL 覆盖）",
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
        onlyoffice_tasks = [
            task for task in tasks
            if not uses_osworld_evaluator(task.task_config)
        ]
        if not onlyoffice_tasks:
            self.log.info("未检测到 OnlyOffice 类型 SearchWrite 任务，跳过 Stage0")
            for task in tasks:
                task.extra["share_urls"] = {}
            return

        # 解析实例 URL（支持逗号分隔的多实例，逐个解析可用性）
        raw_urls = [u.strip() for u in (self.args.onlyoffice_url or "").split(",")
                    if u.strip()]
        if not raw_urls:
            raw_urls = [""]
        resolved_urls = [
            resolve_document_sharing_url(u, self.args.onlyoffice_host_ip, log=self.log)
            for u in raw_urls
        ]
        self.args.onlyoffice_url = ",".join(resolved_urls)

        task_items_raw = [(t.task_uid, t.task_path, t.task_config) for t in onlyoffice_tasks]
        self._task_share_urls = stage0_prepare_documents(
            task_items_raw,
            resolved_urls,
            self.args.onlyoffice_host_ip,
            self.log,
        )
        # 将 share_urls 与该任务所属实例 URL 写入各 task 的 extra
        for task in tasks:
            task.extra["share_urls"] = self._task_share_urls.get(task.task_uid, {})
            task.extra["onlyoffice_url"] = self._task_onlyoffice_url(task)

    def _task_onlyoffice_url(self, task):
        """
        获取该任务文档所属 OnlyOffice 实例的 base URL。

        多实例路由：stage0 按任务轮转分配实例，share_url 自带实例端口，
        此处从 share_url 反推；无共享链接时退化为第一个配置实例。

        输入:
            task: TaskItem
        输出:
            base URL 字符串（如 http://<HOST>:5051）
        """
        cached = task.extra.get("onlyoffice_url", "")
        if cached:
            return cached
        for url in (task.extra.get("share_urls") or {}).values():
            base = base_url_from_share_url(url)
            if base:
                return base
        return (self.args.onlyoffice_url or "").split(",")[0].strip()

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
        if uses_osworld_evaluator(task.task_config):
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

        # Stage 2.5: 触发 OnlyOffice 保存（路由到该任务所属实例）
        if share_urls:
            save_verified = stage2_5_trigger_save(
                config, task.task_uid, share_urls,
                self._task_onlyoffice_url(task), log,
            )
            if isinstance(result, dict):
                result["onlyoffice_save_verified"] = save_verified

        return result, ctrl

    def _save_onlyoffice_results_for_eval_rules(self, task, task_result_dir, log):
        """下载供 eval_rules 使用的完整 OnlyOffice 结果集合。

        功能：将当前任务全部共享文档下载到本地评价目录；只有每个预期
        文档均下载成功时才返回成功，避免多文件任务用残缺集合评分。
        输入参数：task 为任务对象；task_result_dir 为本地结果目录；
        log 为日志记录器。
        输出返回值：全部预期文件均非空且成功落盘时为 True，否则 False。
        """
        share_urls = task.extra.get("share_urls", {})
        if not share_urls:
            return False

        patterns = self._eval_rule_file_patterns(
            task.task_config.get("eval_rules", [])
        )
        template_dir = os.path.join(
            SRC_DIR,
            "parallel_benchmark",
            "hf_data",
            "benchmark_dataset",
            task.task_uid,
        )
        expected_names = set()
        if os.path.isdir(template_dir):
            expected_names = {
                filename
                for filename in os.listdir(template_dir)
                if any(fnmatch.fnmatch(filename, pattern) for pattern in patterns)
            }
        missing_links = missing_expected_searchwrite_files(
            expected_names,
            share_urls.keys(),
        )
        if missing_links:
            log.warning("OnlyOffice 共享文档集不完整: %s", missing_links)
            return False

        os.makedirs(task_result_dir, exist_ok=True)
        uid_short = task.task_uid.split("-")[0]
        saved = 0
        task_oo_url = self._task_onlyoffice_url(task)

        for filename in share_urls:
            safe_name = os.path.basename(filename)
            stem = os.path.splitext(safe_name)[0]
            doc_id = f"{uid_short}_{stem}"
            try:
                content = fetch_document_file_via_api(task_oo_url, doc_id)
            except Exception as exc:
                log.warning("下载 OnlyOffice 结果失败 %s: %s", doc_id, exc)
                continue

            if not content:
                log.warning("OnlyOffice 结果为空: %s", doc_id)
                continue

            dst = os.path.join(task_result_dir, safe_name)
            with open(dst, "wb") as f:
                f.write(content)
            saved += 1
            log.info("OnlyOffice 结果已保存供 eval_rules 使用: %s", dst)

        expected_count = len(share_urls)
        if saved != expected_count:
            log.warning(
                "OnlyOffice 结果集合不完整: %d/%d",
                saved,
                expected_count,
            )
        return expected_count > 0 and saved == expected_count

    def _eval_rule_file_patterns(self, eval_rules):
        """
        Derive candidate output file patterns from eval_rules.

        operation_evaluator has its own default file patterns, but SearchWrite
        needs the same intent earlier when collecting local/shared VM outputs.
        Keep this intentionally small and conservative: explicit file_pattern
        wins; otherwise infer from the check name.
        """
        patterns = []
        for rule in eval_rules or []:
            pattern = rule.get("file_pattern")
            if pattern:
                patterns.append(pattern)
                continue

            check_name = str(rule.get("check", "")).lower()
            if "docx" in check_name:
                patterns.append("*.docx")
            elif "xlsx" in check_name or "cell" in check_name or "sheet" in check_name:
                patterns.append("*.xlsx")
            elif "pptx" in check_name:
                patterns.append("*.pptx")

        deduped = []
        for pattern in patterns:
            if pattern not in deduped:
                deduped.append(pattern)
        return deduped

    def _save_shared_results_for_eval_rules(self, task, config, task_result_dir, log):
        """收集供 eval_rules 使用的 VM shared 结果文件。

        功能：针对不走 OnlyOffice 的 SearchWrite/OSWorld 风格任务，按规则
        文件模式及模板文件名从 shared 目录复制 Agent 产物；模板集合已知
        时要求全部到齐，防止多文件任务以残缺集合得分。
        输入参数：task 为任务对象；config 为容器配置；task_result_dir
        为本地评价目录；log 为日志记录器。
        输出返回值：所需文件集合完整时为 True，否则 False。
        """
        patterns = self._eval_rule_file_patterns(task.task_config.get("eval_rules", []))
        if not patterns:
            log.debug("eval_rules 无可推断文件模式，跳过 shared 结果收集")
            return False

        shared_dir = getattr(config, "shared_host_dir", "")
        if not shared_dir or not os.path.isdir(shared_dir):
            log.warning("shared 结果目录不可用: %s", shared_dir)
            return False

        template_dir = os.path.join(
            SRC_DIR, "parallel_benchmark", "hf_data", "benchmark_dataset", task.task_uid
        )
        target_names = set()
        if os.path.isdir(template_dir):
            for filename in os.listdir(template_dir):
                if any(fnmatch.fnmatch(filename, pattern) for pattern in patterns):
                    target_names.add(filename)

        copied = 0
        copied_names = set()
        for root, _, files in os.walk(shared_dir):
            for filename in files:
                if filename.startswith(".") or filename.startswith("~$"):
                    continue
                if target_names and filename not in target_names:
                    continue
                if not any(fnmatch.fnmatch(filename, pattern) for pattern in patterns):
                    continue

                src = os.path.join(root, filename)
                rel = os.path.relpath(src, shared_dir)
                dst = os.path.join(task_result_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                    copied_names.add(filename)
                    log.info("shared 结果已保存供 eval_rules 使用: %s", dst)
                except Exception as exc:
                    log.warning("复制 shared 结果失败 %s: %s", src, exc)

        if copied == 0:
            if target_names:
                log.warning(
                    "shared 目录未找到目标结果文件: %s (patterns=%s)",
                    sorted(target_names), patterns,
                )
            else:
                log.warning("shared 目录未找到匹配 eval_rules 的结果文件: %s", patterns)
        return target_names.issubset(copied_names) if target_names else copied > 0

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
        if (
            task.extra.get("share_urls")
            and isinstance(agent_result, dict)
            and agent_result.get("onlyoffice_save_verified") is False
        ):
            return build_searchwrite_evaluator_error(
                "OnlyOffice 编辑结果未完成回写验证"
            )

        # 路径 0：如果任务配置中有 eval_rules，使用 operation_evaluator
        eval_rules = task.task_config.get("eval_rules", [])
        if eval_rules:
            log.info("检测到 eval_rules，使用 operation_evaluator 进行评估")
            result_dir = self.args.save_result_dir or os.path.join(
                tempfile.gettempdir(),
                "paraguibench_searchwrite_eval",
            )

            task_result_dir = os.path.join(result_dir, task.task_config.get("task_id", ""))
            if task.extra.get("share_urls"):
                # OnlyOffice 任务下载不完时必须保留基础设施
                # 错误语义，不能再从可能含旧文件的 shared 目录兜底。
                saved_for_eval = self._save_onlyoffice_results_for_eval_rules(
                    task, task_result_dir, log
                )
            else:
                # OSWorld/shared 任务没有 OnlyOffice 链接，按规则
                # 文件模式收集 VM 产物。
                saved_for_eval = self._save_shared_results_for_eval_rules(
                    task, config, task_result_dir, log
                )
            if not saved_for_eval or not os.path.isdir(task_result_dir):
                log.warning("没有可供 eval_rules 评价的结果文件: %s", task_result_dir)
                return build_searchwrite_evaluator_error(
                    f"结果文件收集失败: {task_result_dir}"
                )

            try:
                return operation_evaluate(task_result_dir, task.task_config)
            except Exception as exc:
                log.error("operation_evaluator 评估失败: %s", exc)
                return build_searchwrite_evaluator_error(f"评估异常: {exc}")

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
                    save_result_dir=os.path.join(
                        self.args.save_result_dir,
                        task.task_config.get("task_id", ""),
                    ) if self.args.save_result_dir else "",
                )
            except Exception as exc:
                log.error("OSWorld 评测执行失败: %s", exc, exc_info=True)
                return build_searchwrite_evaluator_error(
                    f"OSWorld 评测异常: {exc}"
                )

        # 路径 2：否则使用原有的 xlsx 评估逻辑（路由到该任务所属实例）
        share_urls = task.extra.get("share_urls", {})
        return stage3_evaluate(
            task.task_uid, task.task_config, share_urls,
            self._task_onlyoffice_url(task), log,
            save_result_dir=self.args.save_result_dir,
        )


if __name__ == "__main__":
    SearchWritePipeline().main()
