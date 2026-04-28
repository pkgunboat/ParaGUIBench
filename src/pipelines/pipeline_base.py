"""
Pipeline 基类：封装公共的参数解析、资源管理、并行调度框架。

子类只需实现：
    - pipeline_name (属性)
    - add_pipeline_args(parser)
    - scan_tasks() -> List[TaskItem]
    - stage_init(task, config, log) -> bool
    - stage_execute(task, config, log) -> (result, controller)
    - stage_evaluate(task, agent_result, config, log) -> eval_dict
    - pre_run_hook() (可选)
    - post_run_hook() (可选)
"""

import argparse
import atexit
import json
import logging
import os
import queue
import sys
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

# ── 路径设置：开源版布局 ──
#   src/
#   ├── pipelines/              ← 当前文件所在
#   ├── stages/                 ← run_QA_pipeline{,_parallel}.py 等
#   ├── desktop_env/            ← OSWorld Docker provider
#   ├── mm_agents/              ← OSWorld-style Plan Agent
#   └── parallel_benchmark/
#        ├── eval/ prompts/ parallel_agents/
#        ├── utils/ logs/ tasks/
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(SRC_DIR)
STAGES_DIR = os.path.join(SRC_DIR, "stages")
# parallel_benchmark 原是 ubuntu_env 下的 package；该目录同时被当作 sys.path
# 入口使用（脚本里常见 `from parallel_agents.X` / `from eval.X` 的相对式 import）
PARALLEL_BENCHMARK_DIR = os.path.join(SRC_DIR, "parallel_benchmark")

for _p in [SRC_DIR, SCRIPT_DIR, STAGES_DIR, PARALLEL_BENCHMARK_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 保持旧变量别名。UBUNTU_ENV_DIR 指 REPO_ROOT（"logs/" 等运行期目录）。
EXAMPLES_DIR = SRC_DIR
UBUNTU_ENV_DIR = REPO_ROOT
LOGS_DIR = os.path.join(REPO_ROOT, "logs")

# ── 统一任务目录（保持 parallel_benchmark 命名空间） ──
UNIFIED_TASKS_DIR = os.path.join(SRC_DIR, "parallel_benchmark", "tasks")

# ── 部署配置（单服务器默认值的权威来源） ──
from config_loader import DeployConfig, get_ssh_password  # noqa: E402

# ── 多机同步：当前节点的 host_tag，作为 logs/ 下的命名空间目录名 ──
from _host_tag import get_host_tag  # noqa: E402

_DEPLOY = DeployConfig()

# ── 从现有代码导入公共基础设施 ──
from desktop_env.providers.docker.parallel_manager import (  # noqa: E402
    ContainerSetConfig,
    MemoryGuard,
    allocate_ports_for_group,
    scan_remote_docker_ports,
)

from run_QA_pipeline_parallel import (  # noqa: E402
    rebuild_containers_parallel,
    cleanup_group_containers,
    execute_on_vm_with_ip,
    wait_for_vm_ready_with_ip,
    get_ssh_credentials,
    disable_screensaver_parallel,
)
from run_QA_pipeline import ensure_conda_env  # noqa: E402

# ── 线程局部上下文：用于向 stage2_execute_agent_parallel 传递 per-task logger ──
_thread_context = threading.local()


# ============================================================
# 数据结构
# ============================================================

@dataclass
class TaskItem:
    """
    统一的任务数据结构。

    输入:
        task_id: 任务 ID（如 Operation-FileOperate-xxx-001）
        task_uid: 任务 UUID（QA/WebMall 用 uid，其余可与 task_id 相同）
        task_path: 任务 JSON 文件路径
        task_config: 任务配置字典
        extra: 子类可附加的额外数据（如 SearchWrite 的 share_urls）
    """
    task_id: str
    task_uid: str
    task_path: str
    task_config: Dict[str, Any]
    extra: Dict[str, Any] = field(default_factory=dict)


# ============================================================
# 防黑屏心跳（从 run_QA_pipeline_parallel.py 提取并参数化）
# ============================================================

class GlobalScreensaverHeartbeat:
    """
    全局防黑屏心跳守护线程。

    功能:
        每 interval 秒向所有活跃 VM 发送 dbus-send SetActive false + xset s reset。
        支持动态端口列表（通过 port_provider 回调实时获取）。

    输入:
        vm_ip: Docker 宿主机 IP
        port_provider: 回调函数，返回当前所有活跃 VM server 端口列表
        interval: 心跳间隔（秒，默认 180）
    """

    def __init__(self, vm_ip: str, port_provider: Callable[[], List[int]],
                 interval: int = 180):
        self.vm_ip = vm_ip
        self.port_provider = port_provider
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _heartbeat_loop(self) -> None:
        """
        心跳循环主体，在后台线程中运行。
        每隔 interval 秒向所有活跃 VM 发送屏保重置命令。
        """
        heartbeat_script = (
            "import subprocess, os\n"
            "env = os.environ.copy()\n"
            "env['DISPLAY'] = ':0'\n"
            "env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'\n"
            "try:\n"
            "    subprocess.run(['dbus-send', '--session',\n"
            "        '--dest=org.gnome.ScreenSaver', '--type=method_call',\n"
            "        '/org/gnome/ScreenSaver',\n"
            "        'org.gnome.ScreenSaver.SetActive', 'boolean:false'],\n"
            "        env=env, capture_output=True, timeout=5)\n"
            "    subprocess.run(['xset', 's', 'reset'],\n"
            "        env=env, capture_output=True, timeout=5)\n"
            "except Exception:\n"
            "    pass\n"
            "print('heartbeat_ok')\n"
        )

        log = logging.getLogger("pipeline.heartbeat")

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.interval):
                break

            ports = self.port_provider()
            if not ports:
                continue

            log.debug("心跳: 向 %d 个 VM 发送屏保重置", len(ports))
            for port in ports:
                try:
                    url = f"http://{self.vm_ip}:{port}/execute"
                    payload = json.dumps({
                        "command": ["python", "-c", heartbeat_script],
                        "shell": False,
                    })
                    requests.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        data=payload,
                        timeout=10,
                    )
                except Exception:
                    pass  # 静默忽略，不影响主流程

    def start(self) -> None:
        """启动心跳守护线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="global-screensaver-heartbeat",
            daemon=True,
        )
        self._thread.start()
        logging.getLogger("pipeline.heartbeat").info(
            "GlobalScreensaverHeartbeat 已启动（间隔 %ds）", self.interval
        )

    def stop(self) -> None:
        """停止心跳守护线程。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logging.getLogger("pipeline.heartbeat").info("GlobalScreensaverHeartbeat 已停止")


# ============================================================
# BasePipeline 基类
# ============================================================

class BasePipeline(ABC):
    """
    所有 pipeline 的公共框架。

    功能:
        - 统一参数解析（公共 + 子类特有）
        - 资源管理（MemoryGuard、容器组队列、防黑屏心跳）
        - 消融实验环境变量覆盖
        - 任务加载与过滤（task-list-file、skip-completed、full/ablation 模式）
        - ThreadPoolExecutor 并行调度
        - 实时 JSON 结果持久化
        - 统计汇总

    输入:
        args: 已解析的参数（可选，为 None 则从 CLI 解析）
        output_dir: 输出目录（可选，覆盖 --output-json-path）
    """

    def __init__(self, args=None, output_dir=None):
        self.args = args
        self.output_dir_override = output_dir
        self.last_results: Dict[str, Any] = {}
        self.last_expected_task_ids: List[str] = []
        self.log = None
        self._memory_guard = None
        self._available_groups = None
        self._heartbeat = None
        self._active_groups = {}
        self._active_groups_lock = threading.Lock()
        self._active_ports = {}
        self._active_ports_lock = threading.Lock()
        self._output_results = {}
        self._results_lock = threading.Lock()

    # ── 属性（子类必须定义） ──

    @property
    @abstractmethod
    def pipeline_name(self) -> str:
        """
        Pipeline 名称标识符。

        输出:
            如 "qa", "webmall", "webnavigate", "operation", "searchwrite"
        """
        ...

    @property
    def default_vm_ip(self) -> str:
        """
        默认 Docker 宿主机 IP。从 configs/deploy.yaml 的 server.vm_host 读取，
        单机部署默认 127.0.0.1。子类一般不需要覆盖。
        """
        return _DEPLOY.vm_host

    @property
    def default_shared_base_dir(self) -> str:
        """
        默认共享目录根路径（宿主机侧）。来源：deploy.yaml 的
        server.shared_base_dir，兜底 /home/benchmark/shared。
        """
        return _DEPLOY.shared_base_dir

    @property
    def default_qcow2_path(self) -> str:
        """
        默认 VM 磁盘镜像路径。来源：deploy.yaml 的 server.qcow2_path，
        兜底 ./resources/Ubuntu.qcow2（相对 repo 根）。
        """
        return _DEPLOY.qcow2_path

    @property
    def default_subset_file(self) -> str:
        """
        ablation 模式下的默认子集文件路径。子类应覆盖。

        输出:
            subset 文件路径字符串，空字符串表示无默认子集
        """
        return ""

    # ── 参数解析 ──

    def build_parser(self) -> argparse.ArgumentParser:
        """
        构建完整的 argparse 解析器（公共参数 + 子类参数）。

        输出:
            ArgumentParser 实例
        """
        parser = argparse.ArgumentParser(
            description=f"{self.pipeline_name} pipeline (v2)",
        )
        self._add_common_args(parser)
        self.add_pipeline_args(parser)
        return parser

    def _add_common_args(self, parser):
        """
        添加所有 pipeline 共享的公共参数。

        输入:
            parser: ArgumentParser 实例

        公共参数列表:
            -p, -n, --vm-ip, --shared-base-dir, --qcow2-path, --docker-image,
            --vm-memory, --vm-cpu-cores, --memory-limit-gb,
            --agent-mode, --gui-agent, --gui-max-rounds, --gui-timeout,
            --mode (full/ablation), --task-list-file, --task-ids,
            --skip-completed-dir, --save-result-dir, --reset-mode,
            --output-json-path
        """
        # 并行配置
        parser.add_argument("-p", "--max-parallel-tasks", type=int, default=3)
        parser.add_argument("-n", "--vms-per-task", type=int, default=5)

        # VM 配置
        parser.add_argument("--vm-ip", type=str, default=self.default_vm_ip)
        parser.add_argument("--shared-base-dir", type=str, default=self.default_shared_base_dir)
        parser.add_argument("--qcow2-path", type=str, default=self.default_qcow2_path)
        parser.add_argument("--docker-image", type=str, default="happysixd/osworld-docker-sshfs")
        parser.add_argument("--vm-memory", type=str, default="2G")
        parser.add_argument("--vm-cpu-cores", type=str, default="1")
        parser.add_argument("--memory-limit-gb", type=float, default=48.0)

        # Agent 配置
        parser.add_argument("--agent-mode", type=str, default="plan",
                            choices=["plan", "gui_only"])
        parser.add_argument("--gui-agent", type=str, default="seed18",
                            choices=["seed18", "claude", "kimi", "gpt", "gpt54", "qwen", "doubao"])
        parser.add_argument("--gui-max-rounds", type=int, default=200)
        parser.add_argument("--gui-timeout", type=int, default=3600)

        # 任务选择
        parser.add_argument("--mode", type=str, default="ablation",
                            choices=["full", "ablation"],
                            help="full=加载全部任务, ablation=使用子集文件")
        parser.add_argument("--task-list-file", type=str, default="")
        parser.add_argument("--task-ids", type=str, default="",
                            help="直接指定任务 ID（逗号分隔）")

        # 统一功能
        parser.add_argument("--skip-completed-dir", type=str, default="",
                            help="跳过已有结果的任务（支持逗号分隔多个目录）")
        parser.add_argument("--save-result-dir", type=str, default="",
                            help="Agent 结果文件持久化目录")
        parser.add_argument("--reset-mode", type=str, default="rebuild",
                            choices=["rebuild", "clean"])

        # 输出
        parser.add_argument("--output-json-path", type=str, default="")

        # Final 模式
        parser.add_argument("--final", type=str, default="",
                            help="Final 模式：指定固定输出目录，维护进度表，自动跳过已完成任务")

        # 测试与确认
        parser.add_argument("--test", action="store_true", default=False,
                            help="测试模式：每个 pipeline 仅执行 1 个任务，gui_max_rounds=2")
        parser.add_argument("--confirm", action="store_true", default=False,
                            help="执行前显示完整配置并等待用户确认")
        parser.add_argument("--no-dashboard", action="store_true",
                            help="禁用 Rich 仪表板，使用传统 logging 输出")

    def add_pipeline_args(self, parser):
        """
        子类添加特有参数。默认空实现。

        输入:
            parser: ArgumentParser 实例
        """
        pass

    # ── 消融覆盖 ──

    def apply_ablation_overrides(self):
        """
        读取 ABLATION_* 环境变量，覆盖 args 中的对应值。

        覆盖规则:
            ABLATION_AGENT_MODE → args.agent_mode
            ABLATION_GUI_AGENT → args.gui_agent
            ABLATION_PLAN_MODEL → 存入 args（供 stage_execute 使用）
            ABLATION_TEST_MODE → 限制轮次
            ABLATION_ORACLE_PLAN_DIR → 存入 args
            ABLATION_RECORD_DIR → 存入 args
        """
        args = self.args
        if os.environ.get("ABLATION_AGENT_MODE"):
            args.agent_mode = os.environ["ABLATION_AGENT_MODE"]
        if os.environ.get("ABLATION_GUI_AGENT"):
            args.gui_agent = os.environ["ABLATION_GUI_AGENT"]
        args.ablation_plan_model = os.environ.get("ABLATION_PLAN_MODEL", "")
        args.ablation_test_mode = os.environ.get("ABLATION_TEST_MODE", "") == "1"
        args.ablation_oracle_plan_dir = os.environ.get("ABLATION_ORACLE_PLAN_DIR", "")
        args.ablation_record_dir = os.environ.get("ABLATION_RECORD_DIR", "")

        # 反向同步：确保 CLI 参数 --gui-agent 也能被 setup_environment_parallel 读到
        # （该函数通过环境变量 ABLATION_GUI_AGENT 获取 GUI Agent 类型）
        if args.gui_agent and not os.environ.get("ABLATION_GUI_AGENT"):
            os.environ["ABLATION_GUI_AGENT"] = args.gui_agent

    # ── 资源管理 ──

    def setup_resources(self):
        """
        初始化公共资源：MemoryGuard、容器组队列、防黑屏心跳、atexit 清理。
        """
        args = self.args
        self._memory_guard = MemoryGuard(args.memory_limit_gb, args.vm_memory)
        self._available_groups = queue.Queue()
        for i in range(args.max_parallel_tasks):
            self._available_groups.put(i)
        self._heartbeat = GlobalScreensaverHeartbeat(
            vm_ip=args.vm_ip,
            port_provider=self.get_all_active_ports,
        )
        self._heartbeat.start()
        atexit.register(self._atexit_cleanup)

    def cleanup_resources(self):
        """
        停止心跳、清理活跃容器组。
        """
        if self._heartbeat:
            self._heartbeat.stop()

    def _atexit_cleanup(self):
        """
        程序异常退出时清理所有活跃容器。
        """
        with self._active_groups_lock:
            for gid, config in list(self._active_groups.items()):
                try:
                    cleanup_group_containers(config, logging.getLogger())
                except Exception:
                    pass

    # ── 端口管理 ──

    def register_group_ports(self, group_id: int, server_ports: List[int]):
        """
        注册容器组端口到活跃表。

        输入:
            group_id: 容器组编号
            server_ports: VM server 端口列表
        """
        with self._active_ports_lock:
            self._active_ports[group_id] = server_ports

    def unregister_group_ports(self, group_id: int):
        """
        从活跃表注销容器组端口。

        输入:
            group_id: 容器组编号
        """
        with self._active_ports_lock:
            self._active_ports.pop(group_id, None)

    def get_all_active_ports(self) -> List[int]:
        """
        获取所有活跃 VM server 端口（扁平列表）。

        输出:
            端口号列表
        """
        with self._active_ports_lock:
            return [p for ports in self._active_ports.values() for p in ports]

    # ── 任务加载 ──

    @abstractmethod
    def scan_tasks(self) -> List[TaskItem]:
        """
        扫描并返回任务列表。子类必须实现。

        输出:
            TaskItem 列表
        """
        ...

    def load_and_filter_tasks(self) -> List[TaskItem]:
        """
        加载任务并应用过滤器。

        流程:
            1. 调用 scan_tasks() 获取全量/子集任务
            2. 按 --task-ids 过滤（如指定）
            3. 按 --task-list-file 过滤（如指定）
            4. 按 --skip-completed-dir 跳过已完成

        输出:
            过滤后的 TaskItem 列表
        """
        args = self.args
        all_tasks = self.scan_tasks()

        # --task-ids 过滤
        if args.task_ids:
            id_set = set(args.task_ids.split(","))
            all_tasks = [t for t in all_tasks
                         if t.task_id in id_set or t.task_uid in id_set]

        # --task-list-file 过滤
        if args.task_list_file and os.path.isfile(args.task_list_file):
            with open(args.task_list_file, "r") as f:
                id_set = {line.strip() for line in f
                          if line.strip() and not line.startswith("#")}
            all_tasks = [t for t in all_tasks
                         if t.task_id in id_set or t.task_uid in id_set]

        # --final 模式：从 final_progress.json 自动跳过已完成
        if getattr(self.args, "final", "") and self.args.final:
            progress = self._load_final_progress()
            completed_ids = set(progress.get("tasks", {}).keys())
            if completed_ids:
                before = len(all_tasks)
                all_tasks = [t for t in all_tasks
                             if t.task_id not in completed_ids and t.task_uid not in completed_ids]
                skipped = before - len(all_tasks)
                if skipped > 0:
                    self.log.info("[FINAL] 跳过已完成任务: %d 个（来自 final_progress.json）", skipped)

        # --skip-completed-dir 过滤
        if args.skip_completed_dir:
            completed_ids = set()
            for one_dir in args.skip_completed_dir.split(","):
                one_dir = one_dir.strip()
                if os.path.isdir(one_dir):
                    for fname in os.listdir(one_dir):
                        if fname.endswith(".json"):
                            try:
                                with open(os.path.join(one_dir, fname)) as f:
                                    data = json.load(f)
                                if isinstance(data, dict):
                                    completed_ids.update(data.keys())
                            except Exception:
                                pass
                    for dname in os.listdir(one_dir):
                        if os.path.isdir(os.path.join(one_dir, dname)):
                            completed_ids.add(dname)

            before = len(all_tasks)
            all_tasks = [t for t in all_tasks
                         if t.task_id not in completed_ids
                         and t.task_uid not in completed_ids]
            skipped = before - len(all_tasks)
            if skipped > 0:
                self.log.info("跳过已完成任务: %d 个", skipped)

        return all_tasks

    # ── 并行调度 ──

    def run_all_tasks(self, tasks: List[TaskItem]) -> Dict[str, Any]:
        """
        并行调度所有任务。

        输入:
            tasks: 待执行的 TaskItem 列表

        输出:
            结果字典 {task_id/uid: result_dict}

        流程:
            1. ThreadPoolExecutor 提交各任务到 _run_single_task_wrapper
            2. as_completed 收集结果
            3. 每完成一个任务立即写入 JSON（中间保存）
            4. 统计 PASS/FAIL/INTERRUPTED
        """
        args = self.args
        output_json_path = self._resolve_output_json_path()

        # 设置任务总数
        if hasattr(self, '_progress_state'):
            self._progress_state.set_task_total(len(tasks))

        # 启动仪表板 + 管理 logging StreamHandler 冲突
        dashboard = getattr(self, '_dashboard', None)
        saved_handlers = []
        _stdout_proxy = None
        if dashboard and dashboard._enabled:
            root = logging.getLogger()
            for h in root.handlers[:]:
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    saved_handlers.append(h)
                    root.removeHandler(h)
            root.addHandler(logging.NullHandler())

            from progress_display import ThreadLocalStdout
            _stdout_proxy = ThreadLocalStdout(sys.stdout)
            sys.stdout = _stdout_proxy
            dashboard.start()

        try:
            with ThreadPoolExecutor(max_workers=args.max_parallel_tasks) as executor:
                futures = {}
                for task in tasks:
                    future = executor.submit(self._run_single_task_wrapper, task)
                    futures[future] = task

                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        result = future.result()
                        task_key = task.task_uid or task.task_id
                        with self._results_lock:
                            self._output_results[task_key] = result
                            with open(output_json_path, "w", encoding="utf-8") as f:
                                json.dump(self._output_results, f,
                                          ensure_ascii=False, indent=2)

                        # 更新 ProgressState
                        if hasattr(self, '_progress_state'):
                            score = result.get("score", 0.0)
                            interrupted = result.get("interrupted", False)
                            if interrupted:
                                status = "error"
                            elif score == 1.0:
                                status = "pass"
                            else:
                                status = "fail"
                            self._progress_state.complete_task(
                                task.task_id, status,
                                result.get("elapsed_time_sec", 0),
                                result.get("plan_rounds", 0),
                                result.get("cost_usd", 0.0),
                            )
                            if dashboard:
                                dashboard.update()

                        # --final 模式
                        if getattr(self.args, "final", "") and self.args.final:
                            self._update_final_progress_with_result(result)
                    except Exception as exc:
                        self.log.error("[%s] 任务异常: %s", task.task_id, exc)

            return self._output_results
        finally:
            # 停止仪表板 + 恢复 logging
            if dashboard and dashboard._enabled:
                dashboard.stop()
                if _stdout_proxy:
                    sys.stdout = _stdout_proxy._original

                root = logging.getLogger()
                for h in root.handlers[:]:
                    if isinstance(h, logging.NullHandler):
                        root.removeHandler(h)
                for h in saved_handlers:
                    root.addHandler(h)

    @staticmethod
    def compute_gui_step_metrics(agent_result: Dict[str, Any]) -> Dict[str, int]:
        """
        从 agent_result 中计算 GUI 步骤指标。

        输入:
            agent_result: stage_execute 返回的结果字典，应包含 rounds_detail 信息

        输出:
            {"gui_rounds_total": int, "gui_steps_sequential": int}

        计算逻辑:
            - gui_rounds_total = 所有 VM 的 GUI 轮次总和
            - gui_steps_sequential = 每轮 plan 调用中，各 VM 的 GUI 轮次取 max，再求和
              （即：如果不并行的话，串行需要多少步）
        """
        rounds_detail = agent_result.get("rounds_detail", [])
        if not rounds_detail:
            # fallback: 直接从已有字段读取
            total = agent_result.get("gui_rounds_total", 0)
            seq = agent_result.get("gui_steps_sequential", total)
            return {"gui_rounds_total": total, "gui_steps_sequential": seq}

        gui_total = 0
        gui_seq = 0
        for plan_round in rounds_detail:
            # plan_round 可能包含多个并行 GUI agent 的轮次
            vm_rounds = plan_round.get("gui_agent_rounds", [])
            if isinstance(vm_rounds, list) and vm_rounds:
                gui_total += sum(vm_rounds)
                gui_seq += max(vm_rounds)
            elif isinstance(vm_rounds, int):
                gui_total += vm_rounds
                gui_seq += vm_rounds

        return {"gui_rounds_total": gui_total, "gui_steps_sequential": gui_seq}

    @staticmethod
    def _extract_gui_metrics(agent_result: Dict[str, Any]) -> Dict[str, int]:
        """
        从 agent_result 中提取 GUI 步骤指标，兼容多种返回格式。

        适配 plan_agent_thought_action.execute_task() 的返回格式：
          - "history": List[Dict]，每轮包含 "results" 列表，
            每个 result 的 "result.steps" 记录该 GUI Agent 的执行步骤
          - gui_only 模式返回的 "execution_record"，其中 summary.mode == "gui_only"，
            轮次记录在 execution_record.steps / rounds_timing / summary.total_rounds

        计算逻辑:
          - gui_rounds_total: 所有 Plan 轮次中所有 GUI Agent 的步骤数总和
          - gui_steps_sequential: 每轮 Plan 中各 GUI Agent 步骤数取 max，再求和
            （串行等效步骤数，用于计算并行度 = total / sequential）
          - gui_only 只有单个 GUI Agent，total 与 sequential 相同

        输入:
            agent_result: stage_execute 返回的结果字典

        输出:
            {"gui_rounds_total": int, "gui_steps_sequential": int}
        """
        # 优先级 1: 已有扁平化字段（旧格式或已适配的结果）
        if agent_result.get("gui_rounds_total", 0) > 0:
            return {
                "gui_rounds_total": agent_result["gui_rounds_total"],
                "gui_steps_sequential": agent_result.get(
                    "gui_steps_sequential", agent_result["gui_rounds_total"]),
            }

        # 优先级 2: rounds_detail 格式（compute_gui_step_metrics 已处理）
        if agent_result.get("rounds_detail"):
            return BasePipeline.compute_gui_step_metrics(agent_result)

        # 优先级 3: 从 history 中提取（plan_agent_thought_action 的格式）
        # history 结构: [{round, tool_calls, results: [{result: {steps: [...]}}]}]
        history = agent_result.get("history", [])
        if history:
            gui_total = 0
            gui_seq = 0
            for plan_round in history:
                results = plan_round.get("results", [])
                if not results:
                    continue
                # 每个 result 对应一个并行 GUI Agent 的执行结果
                round_steps = []
                for r in results:
                    result_data = r.get("result", {})
                    if isinstance(result_data, dict):
                        steps = result_data.get("steps", [])
                        step_count = len(steps) if isinstance(steps, list) else 0
                    else:
                        step_count = 0
                    round_steps.append(step_count)

                if round_steps:
                    gui_total += sum(round_steps)
                    gui_seq += max(round_steps)

            return {"gui_rounds_total": gui_total, "gui_steps_sequential": gui_seq}

        # 优先级 4: gui_only 模式没有 Plan history，轮次在 execution_record 中。
        gui_only_record = agent_result.get("execution_record")
        if isinstance(gui_only_record, dict):
            summary = gui_only_record.get("summary", {})
            if isinstance(summary, dict) and summary.get("mode") == "gui_only":
                total = BasePipeline._extract_single_gui_round_count(gui_only_record)
                return {"gui_rounds_total": total, "gui_steps_sequential": total}

        # 优先级 5: 兼容直接传入 GUI Agent result / execution_record 的情况。
        if agent_result.get("steps") or agent_result.get("rounds_timing"):
            total = BasePipeline._extract_single_gui_round_count(agent_result)
            return {"gui_rounds_total": total, "gui_steps_sequential": total}

        return {"gui_rounds_total": 0, "gui_steps_sequential": 0}

    @staticmethod
    def _extract_single_gui_round_count(record: Dict[str, Any]) -> int:
        """
        从单 GUI Agent 的执行记录中提取轮次数。

        gui_only 当前写入三种冗余来源，按可信度优先使用：
          1. steps 列表长度
          2. rounds_timing 列表长度
          3. summary.total_rounds
          4. devices[*].agents[*].summary.total_rounds
        """
        steps = record.get("steps")
        if isinstance(steps, list) and steps:
            return len(steps)

        rounds_timing = record.get("rounds_timing")
        if isinstance(rounds_timing, list) and rounds_timing:
            return len(rounds_timing)

        summary = record.get("summary")
        if isinstance(summary, dict):
            total_rounds = summary.get("total_rounds")
            try:
                if total_rounds is not None and int(total_rounds) > 0:
                    return int(total_rounds)
            except (TypeError, ValueError):
                pass

        total = 0
        devices = record.get("devices", [])
        if isinstance(devices, list):
            for device in devices:
                if not isinstance(device, dict):
                    continue
                agents = device.get("agents", [])
                if not isinstance(agents, list):
                    continue
                for agent in agents:
                    if not isinstance(agent, dict):
                        continue
                    agent_summary = agent.get("summary", {})
                    if not isinstance(agent_summary, dict):
                        continue
                    try:
                        total += int(agent_summary.get("total_rounds") or 0)
                    except (TypeError, ValueError):
                        continue
        return total

    def _run_single_task_wrapper(self, task: TaskItem) -> Dict[str, Any]:
        """
        单任务执行包装器（模板方法）。

        流程:
            0. 从队列获取 group_id
            1. 申请内存额度
            2. 分配端口
            3. 调用 stage_init
            4. 调用 stage_execute
            5. 调用 stage_evaluate
            6. finally: 清理容器、释放内存、归还 group_id
        """
        group_id = self._available_groups.get()
        args = self.args
        uid_short = (task.task_uid or task.task_id)[:8]
        log = logging.getLogger(f"pipeline.G{group_id}.{uid_short}")
        config = None
        start_time = time.time()

        # --- per-task 文件日志（按任务 ID 分子目录）---
        task_logger_name = f"pipeline.task.{task.task_id}"
        task_logger = logging.getLogger(task_logger_name)
        task_logger.setLevel(logging.DEBUG)
        task_logger.propagate = False  # 不向 root logger 传播，避免污染终端

        log_dir = os.path.join(self.get_output_dir(), task.task_id)
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, "task.log")
        _file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        _file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        task_logger.addHandler(_file_handler)
        task_logger.info("Task started: %s (uid=%s)", task.task_id, task.task_uid)

        try:
            # 1. 申请内存
            self._memory_guard.acquire(args.vms_per_task)

            # 2. 分配端口
            creds = get_ssh_credentials(args.vm_ip)
            used_ports = scan_remote_docker_ports(
                ssh_password=creds["ssh_password"],
                ssh_opts=creds["ssh_opts"],
                ssh_host=creds["ssh_host"],
                conda_activate=creds["conda_activate"],
            )
            all_active = self.get_all_active_ports()
            containers = allocate_ports_for_group(
                num_vms=args.vms_per_task,
                group_id=group_id,
                extra_used_ports=used_ports | set(all_active),
            )
            config = ContainerSetConfig(
                group_id=group_id,
                num_vms=args.vms_per_task,
                vm_memory=args.vm_memory,
                vm_cpu_cores=args.vm_cpu_cores,
                containers=containers,
                shared_host_dir=os.path.join(args.shared_base_dir, f"group_{group_id}"),
                vm_ip=args.vm_ip,
                docker_image=args.docker_image,
                qcow2_path=args.qcow2_path,
            )
            with self._active_groups_lock:
                self._active_groups[group_id] = config
            server_ports = config.get_server_ports()
            self.register_group_ports(group_id, server_ports)

            # 3. Stage Init
            log.info("[Stage 1] 环境初始化...")
            task_logger.info("[Stage 1] 环境初始化 (group=%d, vms=%d)...",
                             group_id, args.vms_per_task)
            if not self.stage_init(task, config, log):
                task_logger.error("[Stage 1] 环境初始化失败 (group=%d)", group_id)
                return {"task_id": task.task_id, "task_uid": task.task_uid,
                        "status": "init_failed", "interrupted": True}

            # 设置线程局部上下文，供 stage2_execute_agent_parallel 读取
            _thread_context.task_logger = task_logger
            _thread_context.progress_state = getattr(self, '_progress_state', None)
            _thread_context.thread_name = threading.current_thread().name

            # 4. Stage Execute
            log.info("[Stage 2] Agent 执行...")
            agent_result, controller = self.stage_execute(task, config, log)

            # 5. Stage Evaluate
            log.info("[Stage 3] 评估...")
            eval_result = self.stage_evaluate(task, agent_result, config, log)

            # 写入逐轮推理记录 rounds.json（Plan Agent 轮次摘要）
            rounds_record = agent_result.get("rounds_record")
            if rounds_record:
                rounds_path = os.path.join(log_dir, "rounds.json")
                try:
                    with open(rounds_path, "w", encoding="utf-8") as f:
                        json.dump(rounds_record, f, ensure_ascii=False, indent=2)
                    task_logger.info("[ROUNDS] 逐轮记录已保存: %s", rounds_path)
                except Exception as e:
                    task_logger.warning("[ROUNDS] 保存失败: %s", e)

            # 写入 ExecutionRecorder 详细执行记录（含 GUI Agent 各轮截图、动作等）
            execution_record = agent_result.get("execution_record")
            if execution_record:
                exec_record_path = os.path.join(log_dir, "execution_record.json")
                try:
                    with open(exec_record_path, "w", encoding="utf-8") as f:
                        json.dump(execution_record, f, ensure_ascii=False, indent=2, default=str)
                    task_logger.info("[EXEC_RECORD] 详细执行记录已保存: %s", exec_record_path)
                except Exception as e:
                    task_logger.warning("[EXEC_RECORD] 保存失败: %s", e)

            # 记录最终结果到 task log
            task_logger.info("[RESULT] Score: %s | Pass: %s | Elapsed: %.1fs",
                           eval_result.get("score", 0.0),
                           eval_result.get("score", 0.0) == 1.0,
                           time.time() - start_time)

            # 6. 组装结果 — 提取标准化指标
            elapsed = round(time.time() - start_time, 2)

            # 从 agent_result 提取轮次和 token
            # 适配 plan_agent_thought_action.execute_task() 的返回格式：
            #   - "rounds": Plan Agent 轮次数
            #   - "history": List[Dict]，每轮包含 "results" 列表，
            #     每个 result 有 "result.steps" 记录 GUI Agent 执行步骤
            plan_rounds = (agent_result.get("plan_agent_total_rounds")
                           or agent_result.get("rounds", 0))

            gui_metrics = self._extract_gui_metrics(agent_result)
            gui_rounds_total = gui_metrics["gui_rounds_total"]
            gui_steps_sequential = gui_metrics["gui_steps_sequential"]

            token_usage = agent_result.get("token_usage", {})
            token_plan = token_usage.get("plan_agent", {}).get("total_tokens", 0)
            token_gui = token_usage.get("gui_agent", {}).get("total_tokens", 0)
            cost_usd = token_usage.get("total_cost_usd", 0.0)

            score = eval_result.get("score")
            if score is None:
                score = 0.0

            # 优先使用 evaluator 自身的 pass 判定（兼容 "pass" 和 "passed" 两种 key）
            # 某些 evaluator（如 webmall）会综合 precision/recall 判定 passed，
            # 比单纯的 score >= 1.0 更准确（避免假阳性）
            # 注意：skip_eval 任务 evaluator 返回 pass=None, score=None，此处需兜底
            _eval_pass = eval_result.get("pass", eval_result.get("passed", None))
            if _eval_pass is not None:
                task_pass = bool(_eval_pass)
            else:
                task_pass = score >= 1.0 - 1e-6

            result = {
                # 基本信息
                "task_id": task.task_id,
                "task_uid": task.task_uid,
                "pipeline": self.pipeline_name,
                "instruction": task.task_config.get("instruction", ""),
                "agent_mode": args.agent_mode,
                "gui_agent": args.gui_agent,

                # 1. 分数与成功
                "score": score,
                "pass": task_pass,

                # 2. 轮次
                "plan_rounds": plan_rounds,
                "gui_rounds_total": gui_rounds_total,

                # 3. GUI 步骤数（串行等效）
                "gui_steps_sequential": gui_steps_sequential,

                # 4. Token 消耗
                "token_plan": token_plan,
                "token_gui": token_gui,
                "token_total": token_plan + token_gui,
                "cost_usd": round(cost_usd, 4),

                # 5. 运行时间
                "elapsed_time_sec": elapsed,

                # 原始详情
                "evaluator_output": eval_result,
                "token_usage": token_usage,
                "interrupted": False,
                "interrupt_reason": "",
                "group_id": group_id,
                "result_dir": log_dir,
            }
            # ── 自动问题检测 ──
            try:
                from parallel_benchmark.logs.issue_detector import detect_issues as _detect_issues
                _exp_name = ""
                _out_dir = self.get_output_dir()
                if "ablation_" in _out_dir:
                    parts = _out_dir.replace("\\", "/").split("/")
                    for idx, p in enumerate(parts):
                        if p.startswith("ablation_"):
                            _exp_name = "/".join(parts[idx:idx+2])
                            break
                _detect_issues(
                    result, task.task_config, agent_result,
                    experiment=_exp_name,
                    expected_agents=getattr(args, "vms_per_task", 0),
                )
            except Exception as _det_exc:
                log.debug("[IssueDetector] 检测跳过: %s", _det_exc)
            return result

        except Exception as exc:
            elapsed = round(time.time() - start_time, 2)
            log.error("[%s] 执行异常: %s", task.task_id, exc, exc_info=True)
            # 同时写入 per-task 日志，确保 task.log 中包含异常详情
            try:
                task_logger.error("执行异常: %s", exc, exc_info=True)
            except Exception:
                pass
            error_result = {
                "task_id": task.task_id,
                "task_uid": task.task_uid,
                "pipeline": self.pipeline_name,
                "score": 0.0,
                "pass": False,
                "plan_rounds": 0,
                "gui_rounds_total": 0,
                "gui_steps_sequential": 0,
                "token_plan": 0,
                "token_gui": 0,
                "token_total": 0,
                "cost_usd": 0.0,
                "elapsed_time_sec": elapsed,
                "interrupted": True,
                "interrupt_reason": str(exc),
                "group_id": group_id,
                "result_dir": log_dir,
            }
            # ── 自动问题检测（异常场景）──
            try:
                from parallel_benchmark.logs.issue_detector import detect_issues as _detect_issues
                _exp_name = ""
                _out_dir = self.get_output_dir()
                if "ablation_" in _out_dir:
                    parts = _out_dir.replace("\\", "/").split("/")
                    for idx, p in enumerate(parts):
                        if p.startswith("ablation_"):
                            _exp_name = "/".join(parts[idx:idx+2])
                            break
                _detect_issues(
                    error_result, task.task_config, {},
                    experiment=_exp_name,
                )
            except Exception:
                pass
            return error_result

        finally:
            self.unregister_group_ports(group_id)
            if config:
                try:
                    cleanup_group_containers(config, log)
                except Exception:
                    pass
                with self._active_groups_lock:
                    self._active_groups.pop(group_id, None)
            self._memory_guard.release(args.vms_per_task)
            self._available_groups.put(group_id)

            # 清理线程局部上下文
            _thread_context.task_logger = None
            _thread_context.progress_state = None
            _thread_context.thread_name = None

            # 清理 per-task 日志 handler
            try:
                _file_handler.close()
                task_logger.removeHandler(_file_handler)
            except Exception:
                pass

    # ── Stage 方法（子类覆写） ──

    @abstractmethod
    def stage_init(self, task: TaskItem, config: ContainerSetConfig, log) -> bool:
        """
        环境初始化（重建容器、下载文件、禁用屏保等）。

        输入:
            task: 任务数据
            config: 容器组配置
            log: logger

        输出:
            bool, True=成功, False=失败
        """
        ...

    @abstractmethod
    def stage_execute(self, task: TaskItem, config: ContainerSetConfig,
                      log) -> Tuple[Dict, Any]:
        """
        Agent 执行任务。

        输入:
            task: 任务数据
            config: 容器组配置
            log: logger

        输出:
            (agent_result_dict, controller_vm1)
        """
        ...

    @abstractmethod
    def stage_evaluate(self, task: TaskItem, agent_result: Dict,
                       config: ContainerSetConfig, log) -> Dict:
        """
        评估 Agent 执行结果。

        输入:
            task: 任务数据
            agent_result: stage_execute 返回的结果字典
            config: 容器组配置
            log: logger

        输出:
            评估结果字典 {pass, score, reason, ...}
        """
        ...

    def pre_run_hook(self, tasks: List[TaskItem]):
        """
        并行调度前的预处理钩子。默认空实现。
        SearchWrite 覆写此方法执行 Stage0（OnlyOffice 文档准备）。

        输入:
            tasks: 待执行的任务列表
        """
        pass

    def post_run_hook(self, results: Dict[str, Any]):
        """
        并行调度后的后处理钩子。默认空实现。

        输入:
            results: 所有任务的结果字典
        """
        pass

    # ── 工具方法 ──

    # ── Final 模式方法 ──

    def _get_final_progress_path(self) -> str:
        """
        获取 final_progress.json 的路径。

        输出:
            文件路径字符串
        """
        return os.path.join(self.args.final, "final_progress.json")

    def _get_final_progress_md_path(self) -> str:
        """
        获取 final_progress.md 的路径。

        输出:
            文件路径字符串
        """
        return os.path.join(self.args.final, "final_progress.md")

    def _load_final_progress(self) -> Dict[str, Any]:
        """
        加载 final_progress.json。不存在则返回空结构。

        输出:
            进度字典 {"meta": {...}, "tasks": {...}}
        """
        path = self._get_final_progress_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"meta": {"created_at": "", "last_updated": "", "total_tasks": 0, "completed_tasks": 0},
                "tasks": {}}

    def _save_final_progress(self, progress: Dict[str, Any]):
        """
        保存 final_progress.json 和 final_progress.md。

        输入:
            progress: 进度字典
        """
        progress["meta"]["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not progress["meta"]["created_at"]:
            progress["meta"]["created_at"] = progress["meta"]["last_updated"]
        progress["meta"]["completed_tasks"] = len(progress["tasks"])

        # 写 JSON
        path = self._get_final_progress_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)

        # 写 Markdown
        self._write_final_progress_md(progress)

    def _write_final_progress_md(self, progress: Dict[str, Any]):
        """
        从 progress 字典生成 final_progress.md。

        输入:
            progress: 进度字典
        """
        md_path = self._get_final_progress_md_path()
        tasks = progress["tasks"]
        meta = progress["meta"]
        total = meta.get("total_tasks", len(tasks))
        completed = meta["completed_tasks"]

        lines = [
            f"# 实验进度",
            f"",
            f"更新时间: {meta['last_updated']} | 完成: {completed}/{total} ({completed/total*100:.1f}%)" if total > 0 else f"更新时间: {meta['last_updated']} | 完成: {completed}",
            f"",
            f"| Task ID | Pipeline | Status | Score | Agent Mode | GUI Agent | Time |",
            f"|---------|----------|--------|-------|------------|-----------|------|",
        ]

        for tid, info in sorted(tasks.items()):
            status = info.get("status", "-").upper()
            score = info.get("score", "-")
            if isinstance(score, float):
                score = f"{score:.1f}"
            lines.append(
                f"| {tid} | {info.get('pipeline', '-')} | {status} | {score} "
                f"| {info.get('agent_mode', '-')} | {info.get('gui_agent', '-')} "
                f"| {info.get('timestamp', '-')} |"
            )

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _update_final_progress_with_result(self, task_result: Dict[str, Any]):
        """
        将单个任务结果追加到 final_progress.json（线程安全）。

        输入:
            task_result: 任务结果字典
        """
        with self._results_lock:
            progress = self._load_final_progress()
            task_key = task_result.get("task_uid") or task_result.get("task_id")
            if not task_result.get("interrupted", False):
                progress["tasks"][task_key] = {
                    "pipeline": task_result.get("pipeline", self.pipeline_name),
                    "status": "pass" if task_result.get("pass", False) else "fail",
                    "score": task_result.get("score", 0.0),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "agent_mode": task_result.get("agent_mode", ""),
                    "gui_agent": task_result.get("gui_agent", ""),
                }
            self._save_final_progress(progress)

    def _resolve_output_json_path(self) -> str:
        """
        确定输出 JSON 文件路径。

        优先级: --final > output_dir_override > args.output_json_path > 默认路径

        默认路径在多机同步语义下注入 host_tag 作为命名空间，
        即 logs/<host_tag>/<pipeline>_<ts>/results.json，
        以避免多机同时运行同 condition 时彼此覆盖；显式覆盖路径不变。

        输出:
            JSON 文件绝对路径
        """
        # --final 模式：固定目录（显式覆盖，不注入 host_tag）
        if getattr(self.args, "final", "") and self.args.final:
            final_dir = self.args.final
            os.makedirs(final_dir, exist_ok=True)
            return os.path.join(final_dir, f"{self.pipeline_name}_results.json")

        # output_dir_override：上游显式指定，不注入 host_tag
        if self.output_dir_override:
            os.makedirs(self.output_dir_override, exist_ok=True)
            return os.path.join(self.output_dir_override,
                                f"{self.pipeline_name}_results.json")
        # --output-json-path：用户显式指定，不注入 host_tag
        if self.args.output_json_path:
            out_dir = os.path.dirname(self.args.output_json_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            return self.args.output_json_path
        # 默认分支：注入 host_tag 命名空间
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        host_tag = get_host_tag()
        logs_dir = os.path.join(UBUNTU_ENV_DIR, "logs", host_tag,
                                f"{self.pipeline_name}_{timestamp}")
        os.makedirs(logs_dir, exist_ok=True)
        return os.path.join(logs_dir, "results.json")

    def get_output_dir(self) -> str:
        """
        获取输出目录路径（用于执行记录等附属文件）。

        输出:
            目录路径字符串
        """
        json_path = self._resolve_output_json_path()
        return os.path.dirname(json_path)

    def _print_config_summary(self, tasks):
        """
        打印完整的运行配置摘要。

        输入:
            tasks: 待执行的 TaskItem 列表
        """
        args = self.args
        output_path = self._resolve_output_json_path()
        self.log.info("=" * 60)
        self.log.info("运行配置摘要")
        self.log.info("=" * 60)
        self.log.info("  Pipeline:        %s", self.pipeline_name)
        self.log.info("  Mode:            %s", args.mode)
        self.log.info("  Agent Mode:      %s", args.agent_mode)
        self.log.info("  GUI Agent:       %s", args.gui_agent)
        self.log.info("  VMs per Task:    %d", args.vms_per_task)
        self.log.info("  Max Parallel:    %d", args.max_parallel_tasks)
        self.log.info("  GUI Max Rounds:  %d", args.gui_max_rounds)
        self.log.info("  GUI Timeout:     %ds", args.gui_timeout)
        self.log.info("  Tasks:           %d 个", len(tasks))
        if len(tasks) <= 10:
            for t in tasks:
                self.log.info("    - %s", t.task_id)
        else:
            for t in tasks[:5]:
                self.log.info("    - %s", t.task_id)
            self.log.info("    ... (%d more)", len(tasks) - 5)
        self.log.info("  Output:          %s", output_path)
        self.log.info("  Save Result Dir: %s", args.save_result_dir or "(未启用)")
        self.log.info("  Skip Completed:  %s", args.skip_completed_dir or "(未启用)")
        self.log.info("  Reset Mode:      %s", args.reset_mode)
        self.log.info("  VM IP:           %s", args.vm_ip)
        self.log.info("  Test Mode:       %s", "YES" if args.test else "NO")
        self.log.info("  Final Mode:      %s", args.final if getattr(args, "final", "") else "(未启用)")
        self.log.info("=" * 60)

    # ── 入口 ──

    def main(self):
        """
        Pipeline 主入口。

        流程:
            1. 解析参数（如未提供）
            2. 设置日志
            3. 检查 conda 环境
            4. 消融覆盖
            5. 加载与过滤任务
            6. 初始化资源
            7. pre_run_hook
            8. 并行调度
            9. post_run_hook
            10. 清理资源
            11. 统计汇总
        """
        # 1. 参数
        if self.args is None:
            parser = self.build_parser()
            self.args = parser.parse_args()

        # 进度状态（仪表板用）
        # 如果 run_ablation.py 已注入了共享的 ProgressState，则复用它
        from progress_display import ProgressState, DashboardRenderer, ThreadLocalStdout
        if not hasattr(self, '_progress_state') or self._progress_state is None:
            self._progress_state = ProgressState()
        use_dashboard = not getattr(self.args, 'no_dashboard', False)
        self._dashboard = DashboardRenderer(self._progress_state, enabled=use_dashboard)

        # gui_only 强制 vms_per_task=1
        if self.args.agent_mode == "gui_only":
            self.args.vms_per_task = 1

        # 2. 日志
        log_format = (
            "%(asctime)s [%(levelname)s] %(message)s"
            if self.args.max_parallel_tasks <= 1
            else "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"
        )
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            datefmt="%Y-%m-%d %H:%M:%S",
            handlers=[logging.StreamHandler(sys.stdout)],
            force=True,
        )
        self.log = logging.getLogger(f"pipeline.{self.pipeline_name}")

        # 3. conda
        required_env = os.environ.get("REQUIRED_CONDA_ENV", "")
        strict_check = os.environ.get("REQUIRED_CONDA_ENV_STRICT", "0") == "1"
        ensure_conda_env(required_env, strict=strict_check)

        # 4. 消融
        self.apply_ablation_overrides()

        # 5. 任务
        tasks = self.load_and_filter_tasks()
        if not tasks:
            self.log.warning("无任务可执行")
            return

        # -- test 模式：限制任务数和轮次 --
        if self.args.test:
            self.args.max_parallel_tasks = 1
            self.args.gui_max_rounds = 2
            tasks = tasks[:1]
            self.log.info("[TEST MODE] 仅执行 1 个任务，gui_max_rounds=2")

        # -- confirm 模式：显示配置并等待确认 --
        if self.args.confirm:
            self._print_config_summary(tasks)
            answer = input("\n确认以上配置并开始执行？[y/N] ")
            if answer.strip().lower() != "y":
                self.log.info("用户取消执行")
                return

        self.log.info("=" * 60)
        self.log.info("[%s] 开始执行 %d 个任务", self.pipeline_name, len(tasks))
        self.log.info("  mode=%s, agent_mode=%s, gui_agent=%s, vms_per_task=%d",
                       self.args.mode, self.args.agent_mode, self.args.gui_agent,
                       self.args.vms_per_task)
        self.log.info("=" * 60)

        # 6. 资源
        self.setup_resources()

        try:
            # 7. pre hook
            self.pre_run_hook(tasks)

            # 8. 并行调度
            results = self.run_all_tasks(tasks)

            # 9. post hook
            self.post_run_hook(results)

            # 暴露结果和应跑任务列表供外部（如 run_ablation.py --record-to-master）消费
            self.last_results = results
            self.last_expected_task_ids = [t.task_id for t in tasks]

            # 10. 统计
            total = len(results)
            passed = sum(1 for r in results.values()
                         if r.get("pass", False))
            interrupted = sum(1 for r in results.values()
                              if r.get("interrupted", False))
            self.log.info("=" * 60)
            self.log.info("[%s] 完成: PASS=%d, FAIL=%d, INTERRUPTED=%d, TOTAL=%d",
                           self.pipeline_name, passed,
                           total - passed - interrupted, interrupted, total)
            self.log.info("=" * 60)

            # 生成统计报告
            from report_generator import generate_report
            report_dir = generate_report(results, self.get_output_dir(), log=self.log)
            self.log.info("统计报告: %s", report_dir)
        finally:
            # 11. 清理
            self.cleanup_resources()
