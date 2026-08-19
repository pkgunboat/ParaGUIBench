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
from contextlib import contextmanager
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

import log_layout
import parallel_pattern
import stats_updater

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

# ── --final 模式进度文件锁（进程级）──
# final_progress.json 在混跑模式下被多个 pipeline 实例共享（同一 --final
# 目录），read-modify-write 必须用跨实例的进程级锁，per-instance 的
# _results_lock protect 不住（A、B 实例各持自己的锁同时写同一文件会丢条目）。
_FINAL_PROGRESS_LOCK = threading.Lock()


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
# Dashboard 会话上下文（run_all_tasks 与 ConditionScheduler 共用）
# ============================================================

@contextmanager
def dashboard_session(dashboard):
    """
    仪表板会话上下文管理器。

    功能:
        进入时摘除 root logger 的终端 StreamHandler（避免与 Rich 仪表板
        渲染冲突）、将 sys.stdout 切换为线程局部代理、启动仪表板；
        退出时按逆序恢复。dashboard 为 None 或未启用时为透明 no-op。

    输入:
        dashboard: DashboardRenderer 实例或 None
    """
    enabled = dashboard is not None and getattr(dashboard, "_enabled", False)
    if not enabled:
        yield
        return

    saved_handlers = []
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
        yield
    finally:
        # 停止仪表板 + 恢复 logging
        dashboard.stop()
        sys.stdout = _stdout_proxy._original

        root = logging.getLogger()
        for h in root.handlers[:]:
            if isinstance(h, logging.NullHandler):
                root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)


# ============================================================
# ResourcePool：condition 级共享资源池
# ============================================================

class ResourcePool:
    """
    condition 级共享资源池（跨 pipeline 混跑的核心基础设施）。

    功能:
        集中管理原先 per-pipeline-instance 的并发资源，使多个 pipeline
        实例可以共享同一套资源，从而在一个 condition 内混跑:
        - available_groups: 全局 group_id 队列（0..N-1）。group_id 决定
          容器命名（osworld-g{gid}-vm{i}）与共享目录（group_{gid}），
          全局唯一队列保证混跑时不撞名。
        - memory_guard: 全局内存预算（MemoryGuard）。
        - active_groups / active_ports: 活跃容器组与端口注册表（带锁）。
          共享后 get_all_active_ports() 自动聚合所有 pipeline 的活跃端口，
          端口扫描与防黑屏心跳因此能看到全局视图。
        - heartbeat: 全局防黑屏心跳（单实例）。
        - atexit 清理: 进程异常退出时清理所有活跃容器（仅注册一次）。

    输入:
        max_parallel_tasks: 全局并发任务槽数（group_id 0..N-1）
        memory_limit_gb: 容器区内存预算（GiB）
        vm_memory: 单 VM 内存字符串（如 "2G"）
        vm_ip: Docker 宿主机 IP（心跳目标）

    生命周期:
        owner（standalone 时为 pipeline 自身，混跑时为 run_ablation
        的 interleaved 分支）负责调用 start()/stop()；非 owner 的
        pipeline 不得停止共享心跳。
    """

    def __init__(self, max_parallel_tasks: int, memory_limit_gb: float,
                 vm_memory: str, vm_ip: str):
        self.max_parallel_tasks = max_parallel_tasks
        self.memory_guard = MemoryGuard(memory_limit_gb, vm_memory)
        self.available_groups: "queue.Queue[int]" = queue.Queue()
        for i in range(max_parallel_tasks):
            self.available_groups.put(i)
        self.active_groups: Dict[int, Any] = {}
        self.active_groups_lock = threading.Lock()
        self.active_ports: Dict[int, List[int]] = {}
        self.active_ports_lock = threading.Lock()
        self.heartbeat = GlobalScreensaverHeartbeat(
            vm_ip=vm_ip,
            port_provider=self.get_all_active_ports,
        )
        self._atexit_registered = False

    def get_all_active_ports(self) -> List[int]:
        """
        获取所有活跃 VM server 端口（跨 pipeline 聚合的扁平列表）。

        输出:
            端口号列表
        """
        with self.active_ports_lock:
            return [p for ports in self.active_ports.values() for p in ports]

    def start(self) -> None:
        """
        启动资源池：开启心跳守护线程并注册 atexit 容器清理（幂等）。
        """
        self.heartbeat.start()
        if not self._atexit_registered:
            atexit.register(self._atexit_cleanup)
            self._atexit_registered = True

    def stop(self) -> None:
        """
        停止资源池：停止心跳守护线程。容器清理由各任务的 finally 负责，
        此处仅作为 atexit 兜底，不在正常停止时触发。
        """
        self.heartbeat.stop()

    def _atexit_cleanup(self) -> None:
        """
        进程退出兜底：清理所有仍在活跃表中的容器组。
        正常流程下任务的 finally 已清理，这里只兜异常退出。
        """
        with self.active_groups_lock:
            configs = list(self.active_groups.items())
        for _gid, config in configs:
            try:
                cleanup_group_containers(config, logging.getLogger())
            except Exception:
                pass


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
        resource_pool: 共享资源池（可选）。注入时与其他 pipeline 共享
            group 队列/内存预算/端口表/心跳（condition 内混跑场景）；
            为 None 时 setup_resources 自建并自管生命周期（standalone）。
    """

    def __init__(self, args=None, output_dir=None, resource_pool=None):
        self.args = args
        self.output_dir_override = output_dir
        self.resource_pool: Optional[ResourcePool] = resource_pool
        self._owns_pool = False
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
        self._resolved_output_json_path = None

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
                            choices=["seed18", "claude", "claude_anthropic", "kimi", "gpt", "gpt54", "qwen", "doubao"])
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
        parser.add_argument("--skip-service-health-check", action="store_true",
                            help="跳过 WebMall/OnlyOffice 外部服务健康检查")
        parser.add_argument("--service-health-timeout", type=float, default=8.0,
                            help="外部服务健康检查单请求超时时间（秒）")

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
        初始化公共资源（MemoryGuard、容器组队列、端口表、防黑屏心跳）。

        语义:
            - 注入了 resource_pool 时：所有资源成员**别名引用** pool 的
              对应成员（Queue/dict/Lock 按引用共享），下游代码
              （_run_single_task_wrapper 等）零改动即共享全局资源；
              生命周期（start/stop）由 pool 的 owner 负责。
            - 未注入时：自建 ResourcePool 并启动（standalone 旧行为）。
        """
        args = self.args
        if self.resource_pool is None:
            self.resource_pool = ResourcePool(
                max_parallel_tasks=args.max_parallel_tasks,
                memory_limit_gb=args.memory_limit_gb,
                vm_memory=args.vm_memory,
                vm_ip=args.vm_ip,
            )
            self._owns_pool = True
        pool = self.resource_pool
        self._memory_guard = pool.memory_guard
        self._available_groups = pool.available_groups
        self._active_groups = pool.active_groups
        self._active_groups_lock = pool.active_groups_lock
        self._active_ports = pool.active_ports
        self._active_ports_lock = pool.active_ports_lock
        self._heartbeat = pool.heartbeat
        if self._owns_pool:
            pool.start()

    def cleanup_resources(self):
        """
        资源收尾。仅当本实例拥有 pool（standalone 自建）时停止心跳；
        共享 pool 由其 owner（run_ablation interleaved 分支）统一停止。
        setup_resources 未执行时调用本方法是安全的 no-op。
        """
        if self._owns_pool and self.resource_pool is not None:
            self.resource_pool.stop()

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

        # 设置任务总数
        if hasattr(self, '_progress_state'):
            self._progress_state.set_task_total(len(tasks))

        dashboard = getattr(self, '_dashboard', None)
        with dashboard_session(dashboard):
            with ThreadPoolExecutor(max_workers=args.max_parallel_tasks) as executor:
                futures = {}
                for task in tasks:
                    future = executor.submit(self._run_single_task_wrapper, task)
                    futures[future] = task

                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        result = future.result()
                        self.record_task_result(task, result, dashboard=dashboard)
                    except Exception as exc:
                        self.log.error("[%s] 任务异常: %s", task.task_id, exc)

            return self._output_results

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
        import os  # 用于 INIT_ONLY 环境变量检查
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

        # --- per-task 文件日志（新结构：logs/by_task/<cond>/<prefix>/<task_id>/runs/<host>__<ts>/）---
        task_logger_name = f"pipeline.task.{task.task_id}"
        task_logger = logging.getLogger(task_logger_name)
        task_logger.setLevel(logging.DEBUG)
        task_logger.propagate = False  # 不向 root logger 传播，避免污染终端

        _run_ctx = self._resolve_run_context()
        _run_started_at = datetime.now().isoformat(timespec="seconds")
        log_dir = log_layout.run_dir(
            _run_ctx["logs_dir"], _run_ctx["condition"], task.task_id,
            _run_ctx["host"], _run_ctx["timestamp"])
        os.makedirs(log_dir, exist_ok=True)
        log_file_path = os.path.join(log_dir, "task.log")
        _log_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        _file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        _file_handler.setFormatter(_log_formatter)
        _stage_file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        _stage_file_handler.setFormatter(_log_formatter)
        task_logger.addHandler(_file_handler)
        log.addHandler(_stage_file_handler)
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
                elapsed = round(time.time() - start_time, 2)
                task_logger.error("[Stage 1] 环境初始化失败 (group=%d)", group_id)
                _if_result = {
                    "task_id": task.task_id,
                    "task_uid": task.task_uid,
                    "pipeline": self.pipeline_name,
                    "instruction": task.task_config.get("instruction", ""),
                    "agent_mode": args.agent_mode,
                    "gui_agent": args.gui_agent,
                    "status": "init_failed",
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
                    "interrupt_reason": "stage_init_failed",
                    "group_id": group_id,
                    "result_dir": log_dir,
                }
                self._finalize_run(
                    result=_if_result, logs_dir=_run_ctx["logs_dir"],
                    condition=_run_ctx["condition"], host=_run_ctx["host"],
                    timestamp=_run_ctx["timestamp"], run_dir=log_dir,
                    started_at=_run_started_at,
                    ended_at=datetime.now().isoformat(timespec="seconds"))
                return _if_result

            # ===== 只执行 stage1，跳过 stage2 和 stage3 =====
            if os.environ.get('INIT_ONLY') == '1':
                log.info("[INIT_ONLY] Stage 1 完成，跳过 stage2 和 stage3")
                task_logger.info("[INIT_ONLY] Stage 1 完成，跳过后续阶段")
                _io_result = {
                    "task_id": task.task_id,
                    "task_uid": task.task_uid,
                    "pipeline": self.pipeline_name,
                    "status": "init_only_success",
                    "score": 0.0,
                    "pass": False,
                    "interrupted": False,
                    "elapsed_time_sec": time.time() - start_time,
                    "result_dir": log_dir,
                }
                self._finalize_run(
                    result=_io_result, logs_dir=_run_ctx["logs_dir"],
                    condition=_run_ctx["condition"], host=_run_ctx["host"],
                    timestamp=_run_ctx["timestamp"], run_dir=log_dir,
                    started_at=_run_started_at,
                    ended_at=datetime.now().isoformat(timespec="seconds"))
                return _io_result

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
                _exp_name = f"{_run_ctx['condition']}/{log_layout.run_name(_run_ctx['host'], _run_ctx['timestamp'])}"
                _detect_issues(
                    result, task.task_config, agent_result,
                    experiment=_exp_name,
                    expected_agents=getattr(args, "vms_per_task", 0),
                )
            except Exception as _det_exc:
                log.debug("[IssueDetector] 检测跳过: %s", _det_exc)
            self._finalize_run(
                result=result, logs_dir=_run_ctx["logs_dir"],
                condition=_run_ctx["condition"], host=_run_ctx["host"],
                timestamp=_run_ctx["timestamp"], run_dir=log_dir,
                started_at=_run_started_at,
                ended_at=datetime.now().isoformat(timespec="seconds"))
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
                _exp_name = f"{_run_ctx['condition']}/{log_layout.run_name(_run_ctx['host'], _run_ctx['timestamp'])}"
                _detect_issues(
                    error_result, task.task_config, {},
                    experiment=_exp_name,
                )
            except Exception:
                pass
            self._finalize_run(
                result=error_result, logs_dir=_run_ctx["logs_dir"],
                condition=_run_ctx["condition"], host=_run_ctx["host"],
                timestamp=_run_ctx["timestamp"], run_dir=log_dir,
                started_at=_run_started_at,
                ended_at=datetime.now().isoformat(timespec="seconds"))
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
                log.removeHandler(_stage_file_handler)
                _stage_file_handler.close()
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

    def service_health_pipeline_names(self, tasks: List[TaskItem]) -> List[str]:
        """
        返回当前任务集需要检查的外部服务类型。

        默认使用 pipeline_name；不依赖外部服务的 pipeline 会在
        service_health 模块中自然返回空检查项。子类可基于任务内容缩小范围。
        """
        return [self.pipeline_name]

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

        使用进程级 _FINAL_PROGRESS_LOCK：混跑模式下多个 pipeline 实例
        写同一个 final_progress.json，per-instance 锁防不住跨实例竞态。

        输入:
            task_result: 任务结果字典
        """
        with _FINAL_PROGRESS_LOCK:
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
        cached = getattr(self, "_resolved_output_json_path", None)
        if cached:
            return cached

        # --final 模式：固定目录（显式覆盖，不注入 host_tag）
        if getattr(self.args, "final", "") and self.args.final:
            final_dir = self.args.final
            os.makedirs(final_dir, exist_ok=True)
            path = os.path.join(final_dir, f"{self.pipeline_name}_results.json")
            setattr(self, "_resolved_output_json_path", path)
            return path

        # output_dir_override：上游显式指定，不注入 host_tag
        if self.output_dir_override:
            os.makedirs(self.output_dir_override, exist_ok=True)
            path = os.path.join(self.output_dir_override,
                                f"{self.pipeline_name}_results.json")
            setattr(self, "_resolved_output_json_path", path)
            return path
        # --output-json-path：用户显式指定，不注入 host_tag
        if self.args.output_json_path:
            out_dir = os.path.dirname(self.args.output_json_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            setattr(self, "_resolved_output_json_path", self.args.output_json_path)
            return self.args.output_json_path
        # 默认分支：注入 host_tag 命名空间
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        host_tag = get_host_tag()
        logs_dir = os.path.join(UBUNTU_ENV_DIR, "logs", host_tag,
                                f"{self.pipeline_name}_{timestamp}")
        os.makedirs(logs_dir, exist_ok=True)
        path = os.path.join(logs_dir, "results.json")
        setattr(self, "_resolved_output_json_path", path)
        return path

    def get_output_dir(self) -> str:
        """
        获取输出目录路径（用于执行记录等附属文件）。

        输出:
            目录路径字符串
        """
        json_path = self._resolve_output_json_path()
        return os.path.dirname(json_path)

    def _resolve_run_context(self) -> dict:
        """
        解析本次运行的上下文（新日志结构所需）。

        来源:
            logs_dir   = <UBUNTU_ENV_DIR>/logs
            condition  = 环境变量 PARABENCH_CONDITION，缺省 "standalone"
            host       = get_host_tag()
            timestamp  = 环境变量 PARABENCH_RUN_TS（由 run_ablation 注入），
                         standalone 时本实例首次调用生成一次并缓存
        输出: {"logs_dir","condition","host","timestamp"}
        """
        cached = getattr(self, "_run_context_cache", None)
        if cached:
            return cached
        ts = os.environ.get("PARABENCH_RUN_TS")
        if not ts:
            ts = getattr(self, "_standalone_run_ts", None)
            if not ts:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._standalone_run_ts = ts
        ctx = {
            "logs_dir": os.path.join(UBUNTU_ENV_DIR, "logs"),
            "condition": os.environ.get("PARABENCH_CONDITION", "standalone"),
            "host": get_host_tag(),
            "timestamp": ts,
        }
        self._run_context_cache = ctx
        return ctx

    def _finalize_run(self, *, result, logs_dir, condition, host, timestamp,
                      run_dir, started_at, ended_at):
        """
        一次 run 收尾：写 meta.json + 更新 latest 软链接 + 增量刷新统计。

        输入:
            result: _handle_one_task 组装的结果字典
            logs_dir/condition/host/timestamp: 运行上下文
            run_dir: 该 run 目录（task.log 等已落在此）
            started_at/ended_at: ISO 时间串
        副作用: run_dir/meta.json、by_task latest 软链接、stats/ 五级文件。
                任何异常不向上抛（写统计失败不应让任务整体失败），仅记日志。
        """
        try:
            lookup = getattr(self, "_parallel_lookup", None)
            if lookup is None:
                lookup = parallel_pattern.ParallelPatternLookup()
                self._parallel_lookup = lookup
            meta = stats_updater.build_meta(
                result, condition=condition, host=host, timestamp=timestamp,
                parallel_lookup=lookup, started_at=started_at,
                ended_at=ended_at, pipeline=self.pipeline_name)
            with open(os.path.join(run_dir, "meta.json"), "w",
                      encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            task_dir = log_layout.by_task_dir(logs_dir, condition,
                                              result["task_id"])
            log_layout.update_latest_symlink(
                task_dir, log_layout.run_name(host, timestamp))
            updater = getattr(self, "_stats_updater", None)
            if updater is None:
                updater = stats_updater.StatsUpdater(logs_dir)
                self._stats_updater = updater
            updater.on_run_finished(meta)
        except Exception as exc:  # 统计失败不致命
            try:
                self.log.warning("[STATS] run 收尾失败: %s", exc)
            except Exception:
                pass

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
        self.log.info("  Service Health:  %s",
                      "SKIP" if args.skip_service_health_check else f"{args.service_health_timeout:.1f}s timeout")
        self.log.info("  Test Mode:       %s", "YES" if args.test else "NO")
        self.log.info("  Final Mode:      %s", args.final if getattr(args, "final", "") else "(未启用)")
        self.log.info("=" * 60)

    # ── 入口（三段式：prepare_run / run_all_tasks / finalize_run） ──

    def prepare_run(self, *, manage_logging: bool = True) -> List[TaskItem]:
        """
        运行前准备（原 main() 的 1-8 步，不含 dashboard 创建与任务调度）。

        流程:
            progress 默认值 → gui_only 强制 n=1 → (可选) logging 配置 →
            conda 检查 → 消融覆盖 → 任务加载过滤 → test 截断 →
            confirm 确认 → 服务健康检查 → setup_resources → pre_run_hook

        输入:
            manage_logging: True 时执行 logging.basicConfig(force=True)
                （standalone 行为）；混跑时传 False，由上层统一配置一次，
                避免多个 pipeline 反复重置 root logger。
        输出:
            待执行的 TaskItem 列表；空列表表示无任务或用户取消。
        异常:
            服务健康检查/pre_run_hook 失败向上抛出，由调用方决定
            中止（standalone）还是跳过该 pipeline（混跑出错隔离）。
        """
        # 进度状态（仪表板用）
        # 如果 run_ablation.py 已注入了共享的 ProgressState，则复用它
        from progress_display import ProgressState
        if not hasattr(self, '_progress_state') or self._progress_state is None:
            self._progress_state = ProgressState()

        # gui_only 强制 vms_per_task=1
        if self.args.agent_mode == "gui_only":
            self.args.vms_per_task = 1

        # 日志
        if manage_logging:
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

        # conda
        required_env = os.environ.get("REQUIRED_CONDA_ENV", "")
        strict_check = os.environ.get("REQUIRED_CONDA_ENV_STRICT", "0") == "1"
        ensure_conda_env(required_env, strict=strict_check)

        # 消融
        self.apply_ablation_overrides()

        # 任务
        tasks = self.load_and_filter_tasks()
        if not tasks:
            self.log.warning("无任务可执行")
            return []

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
                return []

        self.log.info("=" * 60)
        self.log.info("[%s] 开始执行 %d 个任务", self.pipeline_name, len(tasks))
        self.log.info("  mode=%s, agent_mode=%s, gui_agent=%s, vms_per_task=%d",
                       self.args.mode, self.args.agent_mode, self.args.gui_agent,
                       self.args.vms_per_task)
        self.log.info("=" * 60)

        # 外部服务健康检查。必须在资源初始化和 Agent 执行前完成，
        # 避免服务虽返回 200 但实际是默认空站点时继续跑任务。
        skip_health = (
            getattr(self.args, "skip_service_health_check", False)
            or os.environ.get("PARAGUIBENCH_SKIP_SERVICE_HEALTH", "") == "1"
        )
        if skip_health:
            self.log.warning("[ServiceHealth] 已跳过外部服务健康检查")
        else:
            from service_health import ensure_pipeline_services_healthy
            for health_pipeline in self.service_health_pipeline_names(tasks):
                ensure_pipeline_services_healthy(
                    health_pipeline,
                    _DEPLOY,
                    timeout=getattr(self.args, "service_health_timeout", 8.0),
                    log=self.log,
                )

        # 资源
        self.setup_resources()

        # pre hook（SearchWrite 在此串行执行 Stage0 文档准备）
        self.pre_run_hook(tasks)

        # 供 finalize_run 计算 last_expected_task_ids
        self._prepared_tasks = tasks
        return tasks

    def record_task_result(self, task: TaskItem, result: Dict[str, Any],
                           dashboard=None) -> None:
        """
        记录单个任务结果（线程安全，混跑时由 ConditionScheduler 直接调用）。

        副作用:
            1. 增量写入本 pipeline 的 results json（整文件覆盖式）
            2. 更新 ProgressState 完成计数 + 刷新 dashboard（如提供）
            3. --final 模式下更新 final_progress.json

        输入:
            task: 任务数据
            result: _run_single_task_wrapper 返回的结果字典
            dashboard: DashboardRenderer 实例（可选，用于即时刷新）
        """
        output_json_path = self._resolve_output_json_path()
        task_key = task.task_uid or task.task_id
        with self._results_lock:
            self._output_results[task_key] = result
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(self._output_results, f,
                          ensure_ascii=False, indent=2)

        # 更新 ProgressState
        if getattr(self, '_progress_state', None):
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
                pipeline=self.pipeline_name,
            )
            if dashboard:
                dashboard.update()

        # --final 模式
        if getattr(self.args, "final", "") and self.args.final:
            self._update_final_progress_with_result(result)

    def finalize_run(self, results: Dict[str, Any]) -> None:
        """
        运行收尾（原 main() 的 10-11 步）。

        副作用:
            post_run_hook、暴露 last_results/last_expected_task_ids、
            输出 PASS/FAIL 统计日志、生成统计报告。

        输入:
            results: 所有任务的结果字典 {task_key: result}
        """
        # post hook
        self.post_run_hook(results)

        # 暴露结果和应跑任务列表供外部（如 run_ablation.py --record-to-master）消费
        self.last_results = results
        self.last_expected_task_ids = [
            t.task_id for t in getattr(self, "_prepared_tasks", [])
        ]

        # 统计
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

    def main(self):
        """
        Pipeline 主入口（standalone 模式）。

        流程:
            1. 解析参数（如未提供）
            2. 创建 dashboard
            3. prepare_run（日志/conda/消融/任务/健康检查/资源/pre hook）
            4. run_all_tasks 并行调度
            5. finalize_run（post hook/统计/报告）
            6. finally: 清理资源
        """
        # 1. 参数
        if self.args is None:
            parser = self.build_parser()
            self.args = parser.parse_args()

        # 2. 进度状态 + dashboard（仅 standalone 入口创建；
        #    混跑模式由 ConditionScheduler 统一管理 dashboard）
        from progress_display import ProgressState, DashboardRenderer
        if not hasattr(self, '_progress_state') or self._progress_state is None:
            self._progress_state = ProgressState()
        use_dashboard = not getattr(self.args, 'no_dashboard', False)
        self._dashboard = DashboardRenderer(self._progress_state, enabled=use_dashboard)

        try:
            # 3. 准备
            tasks = self.prepare_run(manage_logging=True)
            if not tasks:
                return

            # 4. 并行调度
            results = self.run_all_tasks(tasks)

            # 5. 收尾
            self.finalize_run(results)
        finally:
            # 6. 清理（setup_resources 未执行时为安全 no-op）
            self.cleanup_resources()
