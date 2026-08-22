"""
Webnavigate 批量任务 Pipeline — 多线程并行版本。

评估方式：Agent 在 Chrome 中打开目标网页并加入收藏夹，
评测时读取 Chrome Bookmarks 文件，与 answer 中的目标 URL 做匹配。

覆盖任务：Webnavigate-001~008 + settings-002（共 9 个）。

用法:
    # 默认 3 任务并行，Plan Agent 模式
    python run_webnavigate_pipeline_parallel.py -p 3 --vm-memory 2G

    # 单 GUI Agent 模式（每任务 1 VM）
    python run_webnavigate_pipeline_parallel.py --agent-mode gui_only -p 3

    # 串行模式
    python run_webnavigate_pipeline_parallel.py -p 1

    # 指定任务子集
    python run_webnavigate_pipeline_parallel.py --task-ids Webnavigate-001,Webnavigate-003
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

import requests

# ============================================================
# 路径设置
# ============================================================

current_dir = os.path.dirname(os.path.abspath(__file__))
ubuntu_env_dir = os.path.dirname(current_dir)
parallel_benchmark_dir = os.path.join(ubuntu_env_dir, "parallel_benchmark")
webmall_eval_assets_dir = os.path.join(current_dir, "webmall_eval_assets")

for _p in [parallel_benchmark_dir, ubuntu_env_dir, webmall_eval_assets_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ============================================================
# 从 QA 并行 pipeline 导入容器管理函数
# ============================================================

from config_loader import resolve_host_ip  # noqa: E402
from run_QA_pipeline_parallel import (  # noqa: E402
    rebuild_containers_parallel,
    cleanup_group_containers,
    init_vm_parallel,
    wait_for_vm_ready_with_ip,
    get_ssh_credentials,
)

# 多机同步：当前节点 host_tag，作为 logs/ 下的命名空间目录名
from pipelines._host_tag import get_host_tag  # noqa: E402

# ============================================================
# 从 Docker 并行管理器导入
# ============================================================

from desktop_env.providers.docker.parallel_manager import (  # noqa: E402
    ContainerSetConfig,
    MemoryGuard,
    allocate_ports_for_group,
    scan_remote_docker_ports,
)

# ============================================================
# Agent 相关组件
# ============================================================

from desktop_env.controllers.python import PythonController  # noqa: E402
from parallel_agents.plan_agent_thought_action import (  # noqa: E402
    PlanAgentThoughtAction,
    calculate_cost,
)
from parallel_agents_as_tools.agent_tool_registry import AgentToolRegistry  # noqa: E402
from parallel_agents_as_tools.seed18_gui_agent_as_tool import Seed18GUIAgentTool  # noqa: E402
from parallel_agents_as_tools.claude_gui_agent_as_tool import ClaudeGUIAgentTool  # noqa: E402
from parallel_agents_as_tools.kimi_gui_agent_as_tool import KimiGUIAgentTool  # noqa: E402
from config.api_config import get_api_config, get_model_name  # noqa: E402

# ============================================================
# 书签工具 + 评估器
# ============================================================

from webmall_eval_assets.bookmark_utils import (  # noqa: E402
    close_chrome_and_clear_bookmarks,
    read_bookmark_records,
)
from parallel_benchmark.eval.osworld_evaluator import (  # noqa: E402
    evaluate_osworld_task,
    prepare_osworld_task,
)
from parallel_benchmark.eval.active_tab_evaluator import (  # noqa: E402
    ActiveTabResultProvider,
)
from parallel_benchmark.eval.active_tab_probe import (  # noqa: E402
    capture_active_tab_snapshot,
)
from parallel_benchmark.eval.webnavigate_evaluation_router import (  # noqa: E402
    aggregate_active_tab_vm_results,
    aggregate_any_complete_vm_results,
    build_browser_vm_endpoints,
    resolve_evaluation_mode,
)

# ============================================================
# 常量
# ============================================================

TASKS_LIST_DIR = os.path.join(parallel_benchmark_dir, "tasks")

# 覆盖的任务 ID 列表（Webnavigate-001~011 + settings-001~003）
DEFAULT_TASK_IDS = [
    "Operation-WebOperate-WebNavigate-001",
    "Operation-WebOperate-WebNavigate-002",
    "Operation-WebOperate-WebNavigate-003",
    "Operation-WebOperate-WebNavigate-004",
    "Operation-WebOperate-WebNavigate-005",
    "Operation-WebOperate-WebNavigate-007",
    "Operation-WebOperate-WebNavigate-008",
    "Operation-WebOperate-WebNavigate-009",
    "Operation-WebOperate-WebNavigate-010",
    "Operation-WebOperate-WebNavigate-011",
    "Operation-WebOperate-Settings-001",
    "Operation-WebOperate-Settings-002",
    "Operation-WebOperate-Settings-003",
]

OUTPUT_JSON_PATH = os.path.join(
    ubuntu_env_dir, "logs",
    f"webnavigate_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    "results.json",
)

# 全局追踪：记录所有已启动的容器组（用于 atexit 清理）
_active_groups: Dict[int, ContainerSetConfig] = {}
_active_groups_lock = threading.Lock()


# ============================================================
# 任务扫描
# ============================================================

def scan_webnavigate_tasks(
    tasks_dir: str,
    task_ids: Optional[List[str]] = None,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    扫描 Webnavigate 任务 JSON 文件。

    输入:
        tasks_dir: 任务 JSON 所在目录
        task_ids: 指定的任务 ID 列表；为 None 则使用 DEFAULT_TASK_IDS
    输出:
        [(task_id, task_path, task_config), ...]
    """
    target_ids = task_ids if task_ids is not None else DEFAULT_TASK_IDS
    results = []

    for tid in target_ids:
        path = os.path.join(tasks_dir, f"{tid}.json")
        if not os.path.exists(path):
            logging.getLogger("webnavigate").warning("任务文件不存在: %s", path)
            continue
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        results.append((tid, path, config))

    return results


# ============================================================
# 日志系统
# ============================================================

def setup_logging(max_parallel: int) -> None:
    """
    配置日志系统。

    输入:
        max_parallel: 最大并行数
    """
    log_format = (
        "%(asctime)s [%(levelname)s] %(message)s"
        if max_parallel <= 1
        else "%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s"
    )
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def get_task_logger(group_id: int, task_id: str) -> logging.Logger:
    """
    获取带有组 ID 和任务 ID 前缀的 logger。

    输入:
        group_id: 容器组编号
        task_id: 任务 ID
    输出:
        logger 实例
    """
    short_id = task_id.split("-")[-1] if "-" in task_id else task_id[:8]
    return logging.getLogger(f"webnavigate.G{group_id}.{short_id}")


# ============================================================
# 活跃端口注册表（用于心跳线程动态端口列表）
# ============================================================

_active_ports: Dict[int, List[int]] = {}
_active_ports_lock = threading.Lock()


def register_group_ports(group_id: int, server_ports: List[int]) -> None:
    """
    注册某组的 VM server 端口到全局活跃端口表。

    输入:
        group_id: 容器组编号
        server_ports: 该组所有 VM 的 server 端口列表
    """
    with _active_ports_lock:
        _active_ports[group_id] = list(server_ports)


def unregister_group_ports(group_id: int) -> None:
    """
    注销某组的端口。

    输入:
        group_id: 容器组编号
    """
    with _active_ports_lock:
        _active_ports.pop(group_id, None)


def get_all_active_ports() -> List[int]:
    """
    获取所有活跃组的 server 端口（扁平化列表）。

    输出:
        所有活跃 VM 的 server 端口列表
    """
    with _active_ports_lock:
        ports = []
        for port_list in _active_ports.values():
            ports.extend(port_list)
        return ports


# ============================================================
# GlobalScreensaverHeartbeat — 支持动态端口列表
# ============================================================

class GlobalScreensaverHeartbeat:
    """
    全局防黑屏心跳守护线程。
    每次心跳时从 get_all_active_ports() 动态获取端口列表。

    输入:
        vm_ip: VM 宿主 IP
        interval_sec: 心跳间隔（秒），默认 180
    """

    def __init__(self, vm_ip: str, interval_sec: int = 180):
        self.vm_ip = vm_ip
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _heartbeat_loop(self) -> None:
        """心跳循环主体。"""
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

        log = logging.getLogger("webnavigate.heartbeat")

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.interval_sec):
                break
            ports = get_all_active_ports()
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
                    pass

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
        logging.getLogger("webnavigate.heartbeat").info(
            "GlobalScreensaverHeartbeat 已启动（间隔 %ds）", self.interval_sec
        )

    def stop(self) -> None:
        """停止心跳守护线程。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logging.getLogger("webnavigate.heartbeat").info("GlobalScreensaverHeartbeat 已停止")


# ============================================================
# VM 环境辅助函数
# ============================================================

def disable_screensaver_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> None:
    """
    在指定端口的所有 VM 中禁用屏保和锁屏。

    输入:
        vm_ip: VM 宿主 IP
        vm_ports: VM server 端口列表
        log: logger
    """
    log.info("禁用所有 VM 的屏保和锁屏...")
    disable_script = (
        "import subprocess, os\n"
        "env = os.environ.copy()\n"
        "env['DISPLAY'] = ':0'\n"
        "env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'\n"
        "cmds = [\n"
        "    ['gsettings', 'set', 'org.gnome.desktop.session', 'idle-delay', '0'],\n"
        "    ['gsettings', 'set', 'org.gnome.desktop.screensaver', 'lock-enabled', 'false'],\n"
        "    ['gsettings', 'set', 'org.gnome.desktop.screensaver', 'idle-activation-enabled', 'false'],\n"
        "]\n"
        "for cmd in cmds:\n"
        "    try:\n"
        "        subprocess.run(cmd, env=env, capture_output=True, timeout=5)\n"
        "    except Exception:\n"
        "        pass\n"
        "try:\n"
        "    subprocess.run(['xset', 's', 'off'], env=env, capture_output=True, timeout=5)\n"
        "    subprocess.run(['xset', '-dpms'], env=env, capture_output=True, timeout=5)\n"
        "    subprocess.run(['xset', 's', 'noblank'], env=env, capture_output=True, timeout=5)\n"
        "except Exception:\n"
        "    pass\n"
        "print('screensaver_disabled')\n"
    )

    for port in vm_ports:
        try:
            url = f"http://{vm_ip}:{port}/execute"
            payload = json.dumps({
                "command": ["python", "-c", disable_script],
                "shell": False,
            })
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                data=payload,
                timeout=15,
            )
            if resp.status_code == 200:
                output = resp.json().get("output", "")
                if "screensaver_disabled" in output:
                    log.info("  VM %d 屏保已禁用", port)
                else:
                    log.warning("  VM %d 屏保禁用返回异常: %s", port, output[:100])
            else:
                log.warning("  VM %d 屏保禁用失败 (HTTP %d)", port, resp.status_code)
        except Exception as exc:
            log.warning("  VM %d 屏保禁用失败: %s", port, exc)


def open_browser_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
    start_url: str = "https://www.bing.com",
) -> None:
    """
    在指定端口的所有 VM 中打开 Chrome 并最大化。

    输入:
        vm_ip: VM 宿主 IP
        vm_ports: VM server 端口列表
        log: logger
        start_url: 浏览器首页 URL
    """
    log.info("在所有 VM 中打开 Chrome 并导航到 %s...", start_url)
    launch_script = (
        "import subprocess, time, os\n"
        "env = os.environ.copy()\n"
        "env['DISPLAY'] = ':0'\n"
        f"subprocess.Popen(['google-chrome', '--no-first-run', '--no-default-browser-check', '{start_url}'], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)\n"
        "time.sleep(3)\n"
        "try:\n"
        "    subprocess.run(['wmctrl', '-r', ':ACTIVE:', '-b', 'add,maximized_vert,maximized_horz'], env=env, capture_output=True, timeout=3)\n"
        "except Exception:\n"
        "    try:\n"
        "        import pyautogui\n"
        "        pyautogui.hotkey('alt', 'F10')\n"
        "    except Exception:\n"
        "        pass\n"
        "print('browser_opened')\n"
    )

    for port in vm_ports:
        try:
            url = f"http://{vm_ip}:{port}/execute"
            payload = json.dumps({
                "command": ["python", "-c", launch_script],
                "shell": False,
            })
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                data=payload,
                timeout=20,
            )
            if resp.status_code == 200:
                log.info("  VM %d Chrome 已打开并最大化", port)
            else:
                log.warning("  VM %d Chrome 启动失败 (HTTP %d)", port, resp.status_code)
        except Exception as exc:
            log.warning("  VM %d Chrome 启动失败: %s", port, exc)


def _wait_for_chromium_debug_endpoints(
    endpoints: List[Any],
    log: logging.Logger,
    timeout_sec: float = 20.0,
    poll_interval_sec: float = 0.5,
) -> bool:
    """等待当前容器组的全部动态 CDP 端点完成页面级握手。

    功能：在 Agent 开始前先轮询每台 VM 对应的 ``/json/version``，
    再通过实际 Playwright CDP 探针读取活动页，并确认原 config 已打开
    Google Shopping；两层均成功才将端点标记为就绪，避免端口转发
    存活、但浏览器页面不可附着或初始标签页未打开的假就绪。
    输入参数：
        endpoints: 具有 ``vm_ip``、``chromium_port`` 的成对 VM 端点。
        log: 当前任务日志器。
        timeout_sec: 整组共享的最长等待秒数。
        poll_interval_sec: 未全部就绪时的轮询间隔秒数。
    输出返回值：
        全部端点在期限内就绪返回 ``True``，否则返回 ``False``。
    """

    pending = {
        (
            str(endpoint.vm_ip),
            int(endpoint.server_port),
            int(endpoint.chromium_port),
        )
        for endpoint in endpoints
    }
    if not pending:
        log.error("active-tab Stage 1 没有可等待的 Chromium 端点")
        return False

    last_errors: Dict[Tuple[str, int, int], str] = {}
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while pending and time.monotonic() <= deadline:
        for vm_ip, server_port, chromium_port in list(pending):
            url = f"http://{vm_ip}:{chromium_port}/json/version"
            try:
                response = requests.get(url, timeout=2)
                payload = response.json() if response.status_code == 200 else {}
                if payload.get("Browser") or payload.get("webSocketDebuggerUrl"):
                    snapshot = capture_active_tab_snapshot(
                        vm_ip,
                        chromium_port,
                        log,
                        server_port=server_port,
                    )
                    if snapshot.page_kind != "google_shopping":
                        raise RuntimeError(
                            "CDP 可读，但活动页不是原 OSWorld config "
                            "要求的 Google Shopping"
                        )
                    locale = str(snapshot.locale or "").lower()
                    if not locale.startswith("en"):
                        raise RuntimeError(
                            "CDP 可读且活动页是 Google Shopping，"
                            f"但实际页面 locale 不是英文: {snapshot.locale!r}"
                        )
                    pending.remove((vm_ip, server_port, chromium_port))
                    last_errors.pop(
                        (vm_ip, server_port, chromium_port),
                        None,
                    )
                    log.info(
                        "  Chromium CDP 页面握手已就绪: %s:%d",
                        vm_ip,
                        chromium_port,
                    )
            except Exception as exc:
                last_errors[
                    (vm_ip, server_port, chromium_port)
                ] = str(exc)
                continue
        if pending and time.monotonic() <= deadline:
            time.sleep(max(0.0, poll_interval_sec))

    if pending:
        log.error(
            "Chromium CDP 未在 %.1f 秒内就绪: %s",
            timeout_sec,
            sorted(pending),
        )
        for endpoint in sorted(pending):
            if endpoint in last_errors:
                log.error(
                    "  CDP %s:%d 最后错误: %s",
                    endpoint[0],
                    endpoint[2],
                    last_errors[endpoint],
                )
        return False
    return True


def _prepare_browser_after_reset(
    task_config: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> bool:
    """在清空书签并关闭 Chrome 后按评价模式重建浏览器状态。

    功能：
        历史 bookmark 任务继续使用普通 Chrome 启动；OSWorld 模式
        均在每台 VM 上执行原 JSON 的 config。active-tab 还需等待
        同容器动态 CDP 与 Google Shopping 页面全部就绪；
        profile-state 只要求原 config 成功，不误用 Shopping 专用握手。
    输入参数：
        task_config: 含 ``evaluation_mode`` 与 ``evaluator_path`` 的任务。
        config: 当前容器组配置，包含同 VM 成对端口和共享目录。
        log: 当前任务日志器。
    输出返回值：
        浏览器已按对应协议准备完成返回 ``True``；任一 VM 初始化或
        CDP readiness 失败返回 ``False``。
    """

    evaluation_mode = resolve_evaluation_mode(task_config)
    endpoints = build_browser_vm_endpoints(config)
    if evaluation_mode == "bookmark":
        open_browser_parallel(
            config.vm_ip,
            [endpoint.server_port for endpoint in endpoints],
            log,
        )
        return True

    evaluator_path = _resolve_osworld_evaluator_path(task_config)
    for endpoint in endpoints:
        prepared = prepare_osworld_task(
            evaluator_path,
            endpoint.vm_ip,
            endpoint.server_port,
            config.shared_host_dir,
            log,
        )
        if not prepared:
            log.error(
                "VM server_port=%d 的 OSWorld config 初始化失败",
                endpoint.server_port,
            )
            return False
    if evaluation_mode == "osworld_profile_state":
        return True
    return _wait_for_chromium_debug_endpoints(endpoints, log)


def prepare_agent_start_context(
    task_config: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> Dict[str, Any]:
    """根据任务配置向 Agent 显式呈现已下载的本地 PDF。

    功能：仅对声明了 ``agent_start_context`` 的任务生效；先校验
    guest 路径位于 ``/home/user/shared``、文件名与
    ``prepare_script_path`` 的下载对象一致，再通过 VM 的参数化
    ``/execute`` 端点检查 PDF 签名并在 Chrome 中打开本地文件。
    这一步位于 Agent 执行前，任一目标 VM 失败都会返回失败，
    由调用方中断任务，避免 Agent 在指称对象不可见时继续执行。

    输入参数：
        task_config: 任务 JSON 配置；``agent_start_context`` 可选。
        config: 容器组配置，提供 VM IP 和 server 端口。
        log: 当前任务日志器。
    输出返回值：
        结果字典；``ok`` 表示是否就绪，``applied`` 表示是否
        存在任务级启动上下文，失败时 ``errors`` 列出端口与原因。
    """
    start_context = task_config.get("agent_start_context")
    if start_context in (None, ""):
        return {"ok": True, "applied": False, "reason": "not_configured"}
    if not isinstance(start_context, dict):
        reason = "agent_start_context 必须是对象"
        log.error(reason)
        return {"ok": False, "applied": True, "errors": [reason]}

    context_type = str(start_context.get("type") or "").strip()
    open_with = str(start_context.get("open_with") or "").strip()
    guest_path = str(start_context.get("guest_path") or "").strip()
    target = str(start_context.get("target") or "all_vms").strip()

    if context_type != "local_pdf" or open_with != "chrome":
        reason = (
            "agent_start_context 仅支持 "
            "type=local_pdf 且 open_with=chrome"
        )
        log.error(reason)
        return {"ok": False, "applied": True, "errors": [reason]}

    shared_root = "/home/user/shared"
    normalized_path = os.path.normpath(guest_path)
    try:
        is_shared_path = (
            os.path.isabs(normalized_path)
            and os.path.commonpath([shared_root, normalized_path]) == shared_root
            and normalized_path != shared_root
        )
    except ValueError:
        is_shared_path = False
    if not is_shared_path or not normalized_path.lower().endswith(".pdf"):
        reason = (
            "agent_start_context.guest_path 必须是 "
            "/home/user/shared 下的 PDF 绝对路径"
        )
        log.error("%s: %s", reason, guest_path)
        return {"ok": False, "applied": True, "errors": [reason]}

    prepare_urls = [
        item.strip()
        for item in str(task_config.get("prepare_script_path") or "").split(",")
        if item.strip()
    ]
    prepared_filenames = {
        unquote(os.path.basename(urlsplit(url).path))
        for url in prepare_urls
        if os.path.basename(urlsplit(url).path)
    }
    if os.path.basename(normalized_path) not in prepared_filenames:
        reason = (
            "agent_start_context.guest_path 文件名与 "
            "prepare_script_path 下载对象不一致"
        )
        log.error("%s: %s", reason, normalized_path)
        return {"ok": False, "applied": True, "errors": [reason]}

    vm_ports = list(config.get_server_ports())
    if target == "first_vm":
        vm_ports = vm_ports[:1]
    elif target != "all_vms":
        reason = "agent_start_context.target 仅支持 all_vms 或 first_vm"
        log.error(reason)
        return {"ok": False, "applied": True, "errors": [reason]}
    if not vm_ports:
        reason = "agent_start_context 没有可用的目标 VM"
        log.error(reason)
        return {"ok": False, "applied": True, "errors": [reason]}

    ready_marker = "agent_start_context_ready:"
    launch_script = (
        "import os, subprocess, time\n"
        "from pathlib import Path\n"
        f"path = {normalized_path!r}\n"
        f"shared_root = {shared_root!r}\n"
        "real_path = os.path.realpath(path)\n"
        "real_root = os.path.realpath(shared_root)\n"
        "if os.path.commonpath([real_root, real_path]) != real_root:\n"
        "    raise RuntimeError('prepared PDF escapes shared root')\n"
        "if not os.path.isfile(real_path) or os.path.getsize(real_path) <= 5:\n"
        "    raise RuntimeError('prepared PDF is missing or empty')\n"
        "with open(real_path, 'rb') as file_obj:\n"
        "    if file_obj.read(5) != b'%PDF-':\n"
        "        raise RuntimeError('prepared object is not a PDF')\n"
        "env = os.environ.copy()\n"
        "env['DISPLAY'] = ':0'\n"
        "result = subprocess.run(\n"
        "    ['google-chrome', '--no-first-run', '--no-default-browser-check', "
        "     '--new-window', Path(real_path).as_uri()],\n"
        "    env=env, capture_output=True, text=True, timeout=20,\n"
        ")\n"
        "if result.returncode != 0:\n"
        "    raise RuntimeError('Chrome failed: ' + result.stderr[-500:])\n"
        "time.sleep(2)\n"
        f"print({ready_marker!r} + real_path)\n"
    )

    errors: List[str] = []
    for port in vm_ports:
        try:
            response = requests.post(
                f"http://{config.vm_ip}:{port}/execute",
                json={
                    "command": ["python", "-c", launch_script],
                    "shell": False,
                },
                timeout=30,
            )
            response_data = response.json() if response.status_code == 200 else {}
            output = str(response_data.get("output") or "")
            if (
                response.status_code != 200
                or response_data.get("status") != "success"
                or response_data.get("returncode") != 0
                or ready_marker not in output
            ):
                detail = (
                    response_data.get("error")
                    or response_data.get("message")
                    or output
                    or f"HTTP {response.status_code}"
                )
                errors.append(f"VM {port}: {detail}")
                continue
            log.info("  VM %d 已在 Chrome 打开任务 PDF: %s", port, normalized_path)
        except Exception as exc:
            errors.append(f"VM {port}: {exc}")

    if errors:
        for error in errors:
            log.error("Agent 启动上下文准备失败: %s", error)
        return {
            "ok": False,
            "applied": True,
            "guest_path": normalized_path,
            "errors": errors,
        }

    return {
        "ok": True,
        "applied": True,
        "guest_path": normalized_path,
        "prepared_vm_ports": vm_ports,
    }


def clear_bookmarks_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> Dict[int, Dict[str, Any]]:
    """
    在指定端口的所有 VM 上关闭浏览器并清空 Bookmarks。

    输入:
        vm_ip: VM 宿主 IP
        vm_ports: VM server 端口列表
        log: logger
    输出:
        Dict[port, result]
    """
    log.info("清空所有 VM 的 Chrome 收藏夹...")
    results: Dict[int, Dict[str, Any]] = {}
    for port in vm_ports:
        controller = PythonController(vm_ip=vm_ip, server_port=port)
        try:
            results[port] = close_chrome_and_clear_bookmarks(controller)
            log.info("  VM %d 收藏夹已清空", port)
        except Exception as exc:
            results[port] = {"ok": False, "error": str(exc), "server_port": port}
            log.warning("  VM %d 收藏夹清空失败: %s", port, exc)
    return results


def _bookmark_reset_succeeded(
    reset_results: Dict[int, Dict[str, Any]],
    vm_ports: List[int],
) -> bool:
    """判断全部预期 VM 是否完成无错误的书签重置。

    功能：把 ``clear_bookmarks_parallel`` 的逐 VM 诊断转换为 Stage 1
    门禁；要求每个预期端口都有字典结果、``ok`` 严格为 ``True``，且
    ``error`` 与 ``clear_errors`` 均为空。这样可避免 Chrome 未关闭、
    旧书签残留或备份文件清理失败后继续执行，污染任务隔离与评分。
    输入参数：
        reset_results: 以 VM server 端口为键的书签清理结果。
        vm_ports: 本轮任务必须成功重置的完整 VM server 端口列表。
    输出返回值：
        全部 VM 均有无错误成功证据时返回 ``True``，否则返回 ``False``。
    """

    if not vm_ports:
        return False
    for port in vm_ports:
        vm_result = reset_results.get(port)
        if not isinstance(vm_result, dict):
            return False
        if vm_result.get("ok") is not True:
            return False
        if str(vm_result.get("error") or "").strip():
            return False
        if vm_result.get("clear_errors"):
            return False
    return True


# ============================================================
# VM 环境初始化（整合多个步骤）
# ============================================================

def clean_browser_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> None:
    """
    在指定端口的所有 VM 中清空浏览器状态（不重建容器的轻量级清理方案）。

    输入:
        vm_ip: VM 宿主 IP
        vm_ports: VM server 端口列表
        log: logger
    """
    log.info("清空所有 VM 的浏览器状态...")
    clean_script = (
        "import subprocess, os, time\n"
        "for proc_name in ['google-chrome', 'chromium', 'chrome', 'chromium-browser']:\n"
        "    subprocess.run(['pkill', '-9', '-f', proc_name], capture_output=True)\n"
        "time.sleep(0.5)\n"
        "\n"
        "profile_dirs = [\n"
        "    os.path.expanduser('~/.config/google-chrome/Default'),\n"
        "    os.path.expanduser('~/.config/chromium/Default'),\n"
        "]\n"
        "files_to_remove = [\n"
        "    'Current Session', 'Current Tabs',\n"
        "    'Last Session', 'Last Tabs',\n"
        "    'Cookies', 'Cookies-journal',\n"
        "    'History', 'History-journal',\n"
        "    'Visited Links',\n"
        "    'Top Sites', 'Top Sites-journal',\n"
        "    'Bookmarks', 'Bookmarks.bak',\n"
        "    'Login Data', 'Login Data-journal',\n"
        "    'Web Data', 'Web Data-journal',\n"
        "]\n"
        "removed = 0\n"
        "for profile_dir in profile_dirs:\n"
        "    if not os.path.isdir(profile_dir):\n"
        "        continue\n"
        "    for fname in files_to_remove:\n"
        "        fpath = os.path.join(profile_dir, fname)\n"
        "        if os.path.exists(fpath):\n"
        "            try:\n"
        "                os.remove(fpath)\n"
        "                removed += 1\n"
        "            except Exception:\n"
        "                pass\n"
        "    cache_dir = os.path.join(profile_dir, 'Cache')\n"
        "    if os.path.isdir(cache_dir):\n"
        "        subprocess.run(['rm', '-rf', cache_dir], capture_output=True)\n"
        "        removed += 1\n"
        "print(f'cleaned:{removed}')\n"
    )

    for port in vm_ports:
        try:
            url = f"http://{vm_ip}:{port}/execute"
            payload = json.dumps({
                "command": ["python", "-c", clean_script],
                "shell": False,
            })
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                data=payload,
                timeout=30,
            )
            if resp.status_code == 200:
                output = resp.json().get("output", "")
                log.info("  VM %d 浏览器已清理 (%s)", port, output.strip())
            else:
                log.warning("  VM %d 浏览器清理失败 (HTTP %d)", port, resp.status_code)
        except Exception as exc:
            log.warning("  VM %d 浏览器清理失败: %s", port, exc)


def reinitialize_vms(
    config: ContainerSetConfig,
    log: logging.Logger,
    mode: str = "rebuild",
    prepare_url: str = "",
    task_uid: str = "",
) -> bool:
    """
    VM 环境重置。支持 rebuild（完全重建）和 clean（轻量级清理）两种模式。

    输入:
        config: 容器组配置
        log: logger
        mode: "rebuild"（默认，完全重建容器）或 "clean"（轻量级清理浏览器状态）
        prepare_url: 任务数据下载 URL（仅 rebuild 模式下第一个 VM 会下载）
        task_uid: 当前任务 UID，用于下载缓存目录命名
    输出:
        bool（是否成功）
    """
    vm_ip = config.vm_ip
    vm_ports = config.get_server_ports()

    if mode == "rebuild":
        log.info("环境初始化：重建容器 + 初始化 VM (组 %d)", config.group_id)

        # 1. 重建容器
        if not rebuild_containers_parallel(config, log):
            log.error("容器重建失败")
            return False

        # 2. 初始化所有 VM。普通 WebNavigate/Settings 任务不需要 shared
        # 挂载；只有带 prepare_url 的任务才走 QA 初始化路径下载文件。
        vm_pairs = config.get_vm_pairs()
        success_count = 0
        needs_shared_mount = bool(prepare_url)
        for idx, (vm_port, vnc_port) in enumerate(vm_pairs):
            if needs_shared_mount:
                ok = init_vm_parallel(
                    vm_port=vm_port,
                    vnc_port=vnc_port,
                    prepare_url=prepare_url,
                    shared_host_dir=config.shared_host_dir,
                    vm_ip=vm_ip,
                    is_first_vm=(idx == 0),
                    rebuilt=True,
                    log=log,
                    task_uid=task_uid,
                )
            else:
                log.info(
                    "初始化 VM (port %d, VNC http://%s:%d/) [browser-only]",
                    vm_port, vm_ip, vnc_port,
                )
                ok = wait_for_vm_ready_with_ip(vm_ip, vm_port, max_wait=120)
                if ok:
                    log.info("VM %d 初始化成功（无需 shared 挂载）", vm_port)
                else:
                    log.error("VM %d 无法响应", vm_port)

            if ok:
                success_count += 1
            else:
                log.warning("VM %d 初始化失败，继续下一个...", vm_port)

        if success_count < len(vm_pairs):
            log.warning("仅 %d/%d 个 VM 初始化成功", success_count, len(vm_pairs))

        # 3. 禁用锁屏/屏保
        disable_screensaver_parallel(vm_ip, vm_ports, log)

        # 4. 打开 Chrome 并最大化
        open_browser_parallel(vm_ip, vm_ports, log)

        log.info("环境初始化完成（rebuild）：%d/%d 个 VM 就绪", success_count, len(vm_pairs))
        return success_count == len(vm_pairs)

    elif mode == "clean":
        log.info("环境重置：清空浏览器状态（轻量模式，组 %d）", config.group_id)
        clean_browser_parallel(vm_ip, vm_ports, log)
        disable_screensaver_parallel(vm_ip, vm_ports, log)
        open_browser_parallel(vm_ip, vm_ports, log)
        log.info("环境重置完成（clean）")
        return True

    else:
        log.error("未知的 reset_mode: %s", mode)
        return False


# ============================================================
# Agent 环境设置
# ============================================================

def setup_environment_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> Tuple[PythonController, List[PythonController], AgentToolRegistry]:
    """
    创建 PythonController 和 AgentToolRegistry。

    输入:
        vm_ip: Docker 宿主机 IP
        vm_ports: 各 VM 的 server 端口列表
        log: logger
    输出:
        (controller_vm1, vm_controllers, registry)
    """
    vm_controllers: List[PythonController] = []

    for i, port in enumerate(vm_ports):
        try:
            controller = PythonController(vm_ip=vm_ip, server_port=port)
            screenshot = controller.get_screenshot()
            log.info(
                "VM%d (port %d) connected - Screenshot: %d bytes",
                i + 1, port, len(screenshot) if screenshot else 0,
            )
            vm_controllers.append(controller)
        except Exception as e:
            log.warning("VM%d (port %d) connection failed: %s", i + 1, port, e)
            vm_controllers.append(PythonController(vm_ip=vm_ip, server_port=port))

    controller_vm1 = vm_controllers[0]

    # 支持通过环境变量 ABLATION_GUI_AGENT 切换 GUI Agent
    gui_agent_override = os.environ.get("ABLATION_GUI_AGENT", "seed18")
    # GPT-5.4-mini 参数：
    # GPT54_USE_RESPONSE_ID=1（默认）启用有状态模式，通过 previous_response_id 让服务端维护历史，
    # 符合 Azure computer-use 单图合约 + 启用 Responses API prompt caching。
    # GPT54_USE_RESPONSE_ID=0 切回无状态 message 模式（需注意 Azure 多图限制）。
    # GPT54_MAX_IMAGES=N：有状态模式下触发会话重置的阈值（N 轮截图后重置，丢弃历史）；
    # 无状态模式下控制 input 携带的最近 N 张截图。空串或未设置 → None（不限制/不重置）。
    gpt54_use_rid = os.environ.get("GPT54_USE_RESPONSE_ID", "1") == "1"
    gpt54_max_img_str = os.environ.get("GPT54_MAX_IMAGES", "")
    gpt54_max_img = int(gpt54_max_img_str) if gpt54_max_img_str else None
    registry = AgentToolRegistry(
        controller_vm1,
        vm_controllers=vm_controllers,
        use_seed18_gui=(gui_agent_override == "seed18"),
        use_kimi_gui=(gui_agent_override == "kimi"),
        use_gpt_gui=(gui_agent_override == "gpt"),
        use_qwen_gui=(gui_agent_override == "qwen"),
        use_doubao_gui=(gui_agent_override == "doubao"),
        use_gpt54_gui=(gui_agent_override == "gpt54"),
        use_claude_gui=(gui_agent_override == "claude"),
        use_claude_anthropic_gui=(
            gui_agent_override in ("claude_anthropic", "anthropic_claude")
        ),
        gpt54_use_response_id=gpt54_use_rid,
        gpt54_max_images=gpt54_max_img,
    )
    if gui_agent_override != "seed18":
        log.info("ABLATION: GUI Agent 切换为 %s", gui_agent_override)

    return controller_vm1, vm_controllers, registry


# ============================================================
# Stage 2: Agent 执行（Plan Agent 模式）
# ============================================================

def stage2_execute_plan(
    task_config: Dict[str, Any],
    task_id: str,
    config: ContainerSetConfig,
    log: logging.Logger,
    output_dir: str = "",
) -> Tuple[Dict[str, Any], PythonController]:
    """
    Plan Agent + 多 GUI Agent 执行任务。

    输入:
        task_config: 任务配置
        task_id: 任务 ID
        config: 容器组配置
        log: logger
        output_dir: 执行记录输出目录
    输出:
        (result, controller_vm1)
    """
    log.info("STAGE 2 [plan]: Plan Agent + 多 GUI Agent 执行任务")

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

    start_context_result = prepare_agent_start_context(task_config, config, log)
    if not start_context_result.get("ok"):
        raise RuntimeError(
            "agent_start_context_failed: "
            + "; ".join(start_context_result.get("errors") or ["未知错误"])
        )

    log.info("任务描述: %s", task_instruction[:200])

    vm_ports = config.get_server_ports()
    controller_vm1, vm_controllers, registry = setup_environment_parallel(
        vm_ip=config.vm_ip,
        vm_ports=vm_ports,
        log=log,
    )

    # 支持通过环境变量 ABLATION_PLAN_MODEL 覆盖 Plan Agent 模型
    plan_model = os.environ.get("ABLATION_PLAN_MODEL", "") or get_model_name("plan_agent")
    if os.environ.get("ABLATION_PLAN_MODEL"):
        log.info("ABLATION: Plan Agent 模型切换为 %s", plan_model)

    # 根据模型名称自动选择 API 配置（GPT-5.2 走 BigAI，其余走 DeerAPI）
    from config.api_config import get_api_config_for_model
    api_config = get_api_config_for_model(plan_model)
    log.info("Plan Agent API: %s", api_config["base_url"])

    planner = PlanAgentThoughtAction(
        controller=controller_vm1,
        registry=registry,
        vm_controllers=vm_controllers,
        api_key=api_config["api_key"],
        base_url=api_config["base_url"],
        disable_code_agent=False,
        max_workers=config.num_vms,
        coordinator_model=plan_model,
        num_agents=config.num_vms,
        gui_step_budget=200,
    )

    # 支持通过环境变量 ABLATION_ORACLE_PLAN_DIR 注入 oracle plan
    # 查找顺序：{task_id}.txt → {task_uid}.txt
    oracle_context = None
    oracle_plan_dir = os.environ.get("ABLATION_ORACLE_PLAN_DIR", "")
    if oracle_plan_dir:
        task_uid_val = task_config.get("task_uid", "")
        oracle_file = os.path.join(oracle_plan_dir, f"{task_id}.txt")
        if not os.path.isfile(oracle_file) and task_uid_val:
            oracle_file = os.path.join(oracle_plan_dir, f"{task_uid_val}.txt")
        if os.path.isfile(oracle_file):
            with open(oracle_file, "r", encoding="utf-8") as f:
                oracle_context = f.read().strip()
            log.info("ABLATION: 已加载 Oracle Plan (%d 字符): %s", len(oracle_context), oracle_file)
        else:
            log.warning("ABLATION: Oracle Plan 文件不存在 (尝试 task_id=%s, task_uid=%s)", task_id, task_uid_val)

    # 测试模式：Plan/GUI 各只跑 1 轮，仅验证 API 调用是否正常
    is_test_mode = os.environ.get("ABLATION_TEST_MODE") == "1"
    plan_max_rounds = 1 if is_test_mode else 10
    gui_max_rounds = 1 if is_test_mode else 25
    if is_test_mode:
        log.info("TEST MODE: plan_max_rounds=1, gui_max_rounds=1")

    start_time = time.time()
    result = planner.execute_task(
        task=task_instruction,
        context=oracle_context,
        max_rounds=plan_max_rounds,
        max_rounds_per_subtask=gui_max_rounds,
    )
    elapsed_time = time.time() - start_time
    log.info("Plan Agent 执行完成，耗时: %.2fs", elapsed_time)

    # 保存执行记录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _record_dir = output_dir if output_dir else os.path.join(
        ubuntu_env_dir, "logs", get_host_tag())
    os.makedirs(_record_dir, exist_ok=True)
    record_path = os.path.join(
        _record_dir, f"webnavigate_execution_{task_id}_{timestamp}.json"
    )
    if planner.recorder:
        try:
            planner.recorder.save_to_file(record_path)
            log.info("执行记录已保存: %s", record_path)
        except Exception as exc:
            log.warning("保存执行记录失败: %s", exc)

    return result, controller_vm1


# ============================================================
# Stage 2: Agent 执行（纯 GUI Agent 模式）
# ============================================================

def stage2_execute_gui_only(
    task_config: Dict[str, Any],
    task_id: str,
    config: ContainerSetConfig,
    log: logging.Logger,
    gui_agent: str = "seed18",
    max_rounds: int = 200,
    gui_timeout: int = 3600,
    output_dir: str = "",
) -> Tuple[Dict[str, Any], PythonController]:
    """
    单个 GUI Agent 在单台 VM 上完成任务（不经过 Plan Agent）。

    输入:
        task_config: 任务配置
        task_id: 任务 ID
        config: 容器组配置（仅使用第一个 VM）
        log: logger
        gui_agent: GUI Agent 类型（seed18/claude/kimi）
        max_rounds: 最大执行轮次
        gui_timeout: 超时时间（秒）
        output_dir: 执行记录输出目录
    输出:
        (result, controller_vm1)
    """
    log.info("STAGE 2 [gui_only]: 单个 GUI Agent 独立执行任务")

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

    start_context_result = prepare_agent_start_context(task_config, config, log)
    if not start_context_result.get("ok"):
        raise RuntimeError(
            "agent_start_context_failed: "
            + "; ".join(start_context_result.get("errors") or ["未知错误"])
        )

    log.info("任务描述: %s", task_instruction[:200])
    log.info("GUI Agent: %s | 最大轮次: %d | 超时: %ds", gui_agent, max_rounds, gui_timeout)

    vm_ports = config.get_server_ports()
    first_port = vm_ports[0]
    controller_vm1 = PythonController(vm_ip=config.vm_ip, server_port=first_port)

    try:
        screenshot = controller_vm1.get_screenshot()
        log.info(
            "VM1 (port %d) connected - Screenshot: %d bytes",
            first_port, len(screenshot) if screenshot else 0,
        )
    except Exception as e:
        log.warning("VM1 (port %d) connection warning: %s", first_port, e)

    # 根据 gui_agent 参数创建对应的 Tool 实例
    if gui_agent == "claude":
        gui_tool = ClaudeGUIAgentTool(controller=controller_vm1)
    elif gui_agent in ("claude_anthropic", "anthropic_claude"):
        from parallel_agents_as_tools.claude_anthropic_gui_agent_as_tool import (
            ClaudeAnthropicGUIAgentTool,
        )
        gui_tool = ClaudeAnthropicGUIAgentTool(controller=controller_vm1)
    elif gui_agent == "kimi":
        gui_tool = KimiGUIAgentTool(controller=controller_vm1)
    elif gui_agent == "seed18":
        gui_tool = Seed18GUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")
    elif gui_agent == "gpt54":
        from parallel_agents_as_tools.gpt54_gui_agent_as_tool import GPT54GUIAgentTool
        gui_tool = GPT54GUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")
    elif gui_agent == "gpt54_fc":
        from parallel_agents_as_tools.gpt_gui_agent_as_tool import GPTGUIAgentTool
        from config.api_config import get_model_name
        gui_tool = GPTGUIAgentTool(
            controller=controller_vm1,
            model_name=os.environ.get("GPT54_FC_MODEL", get_model_name("gpt54_fc_gui_agent") or "gpt-5.4"),
            api_config_key=os.environ.get("GPT54_FC_API_CONFIG", "deerapi"),
        )
    elif gui_agent == "qwen":
        # Qwen3-VL baseline：模型由 BENCH_DEFAULT_QWEN_GUI_AGENT 控制
        from parallel_agents_as_tools.qwen_gui_agent_as_tool import QwenGUIAgentTool
        gui_tool = QwenGUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")
    elif gui_agent == "holo3":
        # Holo3-35B-A3B baseline：本地自托管 vLLM，base_url 由 HOLO3_BASE_URL 控制
        from parallel_agents_as_tools.holo3_gui_agent_as_tool import Holo3GUIAgentTool
        gui_tool = Holo3GUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")
    else:
        log.warning("未知的 gui_agent: %s，fallback 到 seed18", gui_agent)
        gui_tool = Seed18GUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")

    start_time = time.time()
    gui_result = gui_tool.execute(
        task=task_instruction,
        max_rounds=max_rounds,
        timeout=gui_timeout,
    )
    elapsed_time = time.time() - start_time
    log.info("纯 GUI Agent 执行完成，耗时: %.2fs", elapsed_time)

    # 格式转换：GUI Agent result → Pipeline 统一格式
    final_answer = gui_result.get("result", "")
    gui_status = gui_result.get("status", "failure")
    gui_model = gui_result.get("model_name", "unknown")
    gui_token = gui_result.get("gui_token_usage", {})
    gui_steps = gui_result.get("steps", [])
    gui_rounds_timing = gui_result.get("rounds_timing", [])

    execution_record = {
        "plan_agent": {
            "model_name": "",
            "rounds": [],
            "summary": {"total_rounds": 0},
        },
        "devices": [{
            "device_id": f"{config.vm_ip}:{first_port}",
            "agents": [{
                "model_name": gui_model,
                "summary": {
                    "total_rounds": len(gui_steps),
                    "final_status": gui_status,
                },
            }],
        }],
        "summary": {
            "final_answer": final_answer,
            "status": gui_status,
            "total_rounds": len(gui_steps),
            "mode": "gui_only",
        },
        "steps": gui_steps,
        "rounds_timing": gui_rounds_timing,
    }

    token_usage = {
        "plan_agent": {},
        "gui_agent": gui_token,
        "plan_agent_model": "",
        "gui_agent_model": gui_model,
    }

    result = {
        "execution_record": execution_record,
        "token_usage": token_usage,
    }

    # 保存执行记录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _record_dir = output_dir if output_dir else os.path.join(
        ubuntu_env_dir, "logs", get_host_tag())
    os.makedirs(_record_dir, exist_ok=True)
    record_path = os.path.join(
        _record_dir, f"webnavigate_gui_only_{task_id}_{timestamp}.json"
    )
    try:
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump({
                "task_id": task_id,
                "instruction": task_instruction,
                "gui_result": gui_result,
                "elapsed_time": elapsed_time,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info("执行记录已保存: %s", record_path)
    except Exception as exc:
        log.warning("保存执行记录失败: %s", exc)

    return result, controller_vm1


# ============================================================
# Stage 3: 书签评估
# ============================================================

def _load_webnavigate_bookmark_evaluator() -> Any:
    """按文件路径加载 WebNavigate 书签评价器。"""
    eval_dir = os.path.join(parallel_benchmark_dir, "eval")
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)

    import importlib.util
    evaluator_path = os.path.join(
        parallel_benchmark_dir, "eval", "webnavigate_bookmark_evaluator.py"
    )
    spec = importlib.util.spec_from_file_location(
        "webnavigate_bookmark_evaluator", evaluator_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载评价器: {evaluator_path}")
    evaluator_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator_module)
    return evaluator_module


def _webnavigate_bookmark_evaluator_knows_task(task_config: Dict[str, Any]) -> bool:
    """判断书签评价器是否有该任务的内置规则或明确不可评测声明。"""
    task_id = task_config.get("task_id", "")
    evaluator_module = _load_webnavigate_bookmark_evaluator()
    if hasattr(evaluator_module, "has_task_entry"):
        return bool(evaluator_module.has_task_entry(task_id))
    if hasattr(evaluator_module, "has_rule"):
        return bool(evaluator_module.has_rule(task_id))
    return False


def _has_supported_webnavigate_evaluation(
    task_config: Dict[str, Any],
) -> bool:
    """判断任务是否声明了当前 runner 可执行的评价协议。

    功能：
        历史 bookmark 任务继续接受内置规则，或非空 answer 与
        evaluator_path 的旧组合；OSWorld 模式不依赖 bookmark answer。
        active-tab 要求 Google Shopping adapter，profile-state 要求
        Chrome profile adapter；二者均要求显式 JSON 路径和
        any-complete 聚合声明。未知模式返回不支持。
    输入参数：
        task_config: 待执行的 WebNavigate 任务配置。
    输出返回值：
        当前 runner 能安全分派该任务时返回 ``True``。
    """

    try:
        evaluation_mode = resolve_evaluation_mode(task_config)
    except Exception:
        return False

    if evaluation_mode == "osworld_active_tab":
        return bool(
            str(task_config.get("evaluator_path") or "").strip()
            and task_config.get("active_tab_adapter")
            == "google_shopping_selected_filters_v1"
            and task_config.get("vm_aggregation") == "any_complete"
        )

    if evaluation_mode == "osworld_profile_state":
        return bool(
            str(task_config.get("evaluator_path") or "").strip()
            and task_config.get("profile_state_adapter")
            == "chrome_profile_name_v1"
            and task_config.get("vm_aggregation") == "any_complete"
        )

    if _webnavigate_bookmark_evaluator_knows_task(task_config):
        return True
    return bool(
        str(task_config.get("answer") or "").strip()
        and str(task_config.get("evaluator_path") or "").strip()
    )


def _resolve_osworld_evaluator_path(
    task_config: Dict[str, Any],
) -> str:
    """安全解析 WebNavigate 任务声明的 OSWorld evaluator JSON。

    功能：以 ``src/parallel_benchmark`` 为唯一可信根目录解析任务中的
    ``evaluator_path``，拒绝绝对路径、父目录逃逸、非 JSON 文件以及
    不存在的配置，避免评价路由读取任务范围外的文件。
    输入参数：
        task_config: 含相对 ``evaluator_path`` 的任务配置字典。
    输出返回值：
        已确认存在且位于可信根目录内的 evaluator JSON 绝对路径。
    异常：
        路径为空、逃逸可信目录、扩展名错误或文件不存在时抛出
        ``ValueError``。
    """

    raw_path = task_config.get("evaluator_path", "")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("OSWorld 任务缺少 evaluator_path")

    trusted_root = os.path.realpath(parallel_benchmark_dir)
    resolved_path = os.path.realpath(
        os.path.join(trusted_root, raw_path.strip())
    )
    try:
        inside_trusted_root = (
            os.path.commonpath([trusted_root, resolved_path])
            == trusted_root
        )
    except ValueError:
        inside_trusted_root = False
    if not inside_trusted_root:
        raise ValueError("evaluator_path 逃逸 parallel_benchmark 可信目录")
    if not resolved_path.lower().endswith(".json"):
        raise ValueError("OSWorld evaluator_path 必须指向 JSON 文件")
    if not os.path.isfile(resolved_path):
        raise ValueError(f"OSWorld evaluator JSON 不存在: {resolved_path}")
    return resolved_path


def _evaluate_active_tab_task(
    task_config: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> Dict[str, Any]:
    """逐 VM 完整评价 OSWorld active-tab 状态并执行 any-complete 聚合。

    功能：从每条容器记录构造同 VM 的 server/CDP 端点，为每台 VM
    创建独立但可在该 VM 多指标间复用的 ``ActiveTabResultProvider``，
    调用 generic OSWorld evaluator 完成整组 AND 指标后，才在 VM 间
    聚合；禁止把不同 VM 的 query 与 filter 子指标拼接。
    输入参数：
        task_config: active-tab 任务配置，含 adapter、聚合方式和 JSON 路径。
        config: 当前容器组配置及共享目录。
        log: 当前任务日志器。
    输出返回值：
        含整体三态评分与 ``per_vm_results`` 诊断的评价结果字典。
    异常：
        adapter、聚合方式、路径或端点配置无效时抛出 ``ValueError``。
    """

    adapter = task_config.get("active_tab_adapter")
    if adapter != "google_shopping_selected_filters_v1":
        raise ValueError(f"不支持的 active_tab_adapter: {adapter!r}")
    aggregation = task_config.get("vm_aggregation")
    if aggregation != "any_complete":
        raise ValueError(f"不支持的 active-tab vm_aggregation: {aggregation!r}")

    evaluator_path = _resolve_osworld_evaluator_path(task_config)
    endpoints = build_browser_vm_endpoints(config)
    per_vm_results: List[Dict[str, Any]] = []
    for vm_index, endpoint in enumerate(endpoints):
        provider = ActiveTabResultProvider(
            lambda endpoint=endpoint: capture_active_tab_snapshot(
                endpoint.vm_ip,
                endpoint.chromium_port,
                log,
                server_port=endpoint.server_port,
            )
        )
        vm_result = evaluate_osworld_task(
            evaluator_path,
            endpoint.vm_ip,
            endpoint.server_port,
            config.shared_host_dir,
            log,
            result_provider=provider,
        )
        annotated_result = dict(vm_result)
        annotated_result.update({
            "vm_index": vm_index,
            "server_port": endpoint.server_port,
            "chromium_port": endpoint.chromium_port,
        })
        per_vm_results.append(annotated_result)

    aggregated = aggregate_active_tab_vm_results(per_vm_results)
    aggregated.update({
        "task_id": task_config.get("task_id", ""),
        "evaluation_mode": "osworld_active_tab",
        "vm_aggregation": aggregation,
    })
    return aggregated


def _evaluate_profile_state_task(
    task_config: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> Dict[str, Any]:
    """逐 VM 评价 Chrome Profile 状态并执行 any-complete 聚合。

    功能：
        对每台 VM 独立调用通用 OSWorld evaluator，完整读取并精确匹配
        该 VM 的 Chrome Profile 状态；只有单台 VM 的整组评价通过后
        才允许整体通过，不跨 VM 拼接状态或分数。
    输入参数：
        task_config: profile-state 任务配置，含 adapter、聚合方式和
            OSWorld evaluator JSON 路径。
        config: 当前容器组配置及共享目录。
        log: 当前任务日志器。
    输出返回值：
        含整体三态评分、公开模式和 ``per_vm_results`` 诊断的字典。
    异常：
        adapter、聚合方式、路径或端点配置无效时抛出 ``ValueError``。
    """

    adapter = task_config.get("profile_state_adapter")
    if adapter != "chrome_profile_name_v1":
        raise ValueError(f"不支持的 profile_state_adapter: {adapter!r}")
    aggregation = task_config.get("vm_aggregation")
    if aggregation != "any_complete":
        raise ValueError(
            f"不支持的 profile-state vm_aggregation: {aggregation!r}"
        )

    evaluator_path = _resolve_osworld_evaluator_path(task_config)
    endpoints = build_browser_vm_endpoints(config)
    per_vm_results: List[Dict[str, Any]] = []
    for vm_index, endpoint in enumerate(endpoints):
        vm_result = evaluate_osworld_task(
            evaluator_path,
            endpoint.vm_ip,
            endpoint.server_port,
            config.shared_host_dir,
            log,
        )
        annotated_result = dict(vm_result)
        annotated_result.update({
            "vm_index": vm_index,
            "server_port": endpoint.server_port,
            "chromium_port": endpoint.chromium_port,
        })
        per_vm_results.append(annotated_result)

    aggregated = aggregate_any_complete_vm_results(
        per_vm_results,
        evaluation_label="profile-state",
    )
    aggregated.update({
        "task_id": task_config.get("task_id", ""),
        "evaluation_mode": "osworld_profile_state",
        "vm_aggregation": aggregation,
    })
    return aggregated


def stage3_evaluate(
    task_config: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> Dict[str, Any]:
    """
    按任务声明分派 bookmark、OSWorld active-tab 或 profile-state 评价。

    输入:
        task_config: 含 ``evaluation_mode`` 的任务配置；旧任务缺省 bookmark
        config: 容器组配置
        log: logger
    输出:
        带 ``status``、``pass``、``score`` 的评估结果字典
    """
    try:
        evaluation_mode = resolve_evaluation_mode(task_config)
    except Exception as exc:
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": f"WebNavigate 评价模式配置错误: {exc}",
            "task_id": task_config.get("task_id", ""),
        }

    if evaluation_mode == "osworld_active_tab":
        log.info("STAGE 3: OSWorld active-tab 评估")
        try:
            return _evaluate_active_tab_task(task_config, config, log)
        except Exception as exc:
            log.error("active-tab 评价失败: %s", exc, exc_info=True)
            return {
                "pass": False,
                "score": -1.0,
                "status": "evaluator_error",
                "reason": f"active-tab evaluator_exception: {exc}",
                "task_id": task_config.get("task_id", ""),
                "evaluation_mode": evaluation_mode,
            }

    if evaluation_mode == "osworld_profile_state":
        log.info("STAGE 3: OSWorld Chrome profile-state 评估")
        try:
            return _evaluate_profile_state_task(task_config, config, log)
        except Exception as exc:
            log.error("profile-state 评价失败: %s", exc, exc_info=True)
            return {
                "pass": False,
                "score": -1.0,
                "status": "evaluator_error",
                "reason": f"profile-state evaluator_exception: {exc}",
                "task_id": task_config.get("task_id", ""),
                "evaluation_mode": evaluation_mode,
            }

    log.info("STAGE 3: 书签评估")

    vm_ip = config.vm_ip
    vm_ports = config.get_server_ports()

    # 1. 从所有 VM 读取结构化书签（合并时保留文件夹层级）
    per_vm_records: Dict[int, List[Dict[str, Any]]] = {}
    errors: Dict[int, str] = {}
    all_records: List[Dict[str, Any]] = []
    successful_reads = 0

    for port in vm_ports:
        controller = PythonController(vm_ip=vm_ip, server_port=port)
        try:
            records = read_bookmark_records(controller)
            successful_reads += 1
        except Exception as exc:
            records = []
            errors[port] = str(exc)
        per_vm_records[port] = records
        all_records.extend(records)

    # 相同 URL 位于不同文件夹时是不同状态证据，不得仅按 URL 去重。
    merged_records: List[Dict[str, Any]] = []
    seen_record_keys: set[Tuple[str, Tuple[str, ...]]] = set()
    for record in all_records:
        url = str(record.get("url") or "").strip()
        folder_path = tuple(str(item) for item in (record.get("folder_path") or []))
        key = (url, folder_path)
        if url and key not in seen_record_keys:
            seen_record_keys.add(key)
            merged_records.append(record)
    merged_urls = list(dict.fromkeys(
        str(record.get("url") or "").strip()
        for record in merged_records
        if str(record.get("url") or "").strip()
    ))

    log.info(
        "收藏夹合并后记录数: %d | 唯一 URL 数: %d",
        len(merged_records),
        len(merged_urls),
    )
    for index, record in enumerate(merged_records, 1):
        log.info(
            "  %d. %s | folder=%s",
            index,
            record.get("url", ""),
            "/".join(record.get("folder_path") or []),
        )

    if errors:
        for port, err in errors.items():
            log.warning("  VM %d 读取书签失败: %s", port, err)

    if successful_reads == 0:
        return {
            "pass": False,
            "score": -1.0,
            "status": "evaluator_error",
            "reason": "所有 VM 的 Bookmarks 文件均读取失败，无法评分。",
            "task_id": task_config.get("task_id", ""),
            "bookmark_per_vm_records": {
                str(key): value for key, value in per_vm_records.items()
            },
            "bookmark_errors": {str(key): value for key, value in errors.items()},
        }

    # 2. 调用 evaluator
    evaluator_module = _load_webnavigate_bookmark_evaluator()

    eval_result = evaluator_module.evaluate(
        task=task_config,
        bookmark_urls=merged_urls,
        bookmark_records=merged_records,
    )

    # 附加 per-VM 调试信息
    eval_result["bookmark_per_vm_records"] = {
        str(key): value for key, value in per_vm_records.items()
    }
    eval_result["bookmark_per_vm_urls"] = {
        str(key): list(dict.fromkeys(
            str(record.get("url") or "").strip()
            for record in value
            if str(record.get("url") or "").strip()
        ))
        for key, value in per_vm_records.items()
    }
    eval_result["bookmark_errors"] = {str(k): v for k, v in errors.items()}

    log.info(
        "评估结果: pass=%s | score=%.2f | %s",
        eval_result.get("pass"),
        eval_result.get("score", 0.0),
        eval_result.get("reason", ""),
    )

    return eval_result


# ============================================================
# 单任务完整流程（Worker 线程主函数）
# ============================================================

def run_single_task(
    task_id: str,
    task_path: str,
    task_config: Dict[str, Any],
    available_groups: queue.Queue,
    args: argparse.Namespace,
    memory_guard: MemoryGuard,
    output_dir: str = "",
    output_results: Dict[str, Any] = None,
    results_lock: threading.Lock = None,
    output_json_path: str = "",
) -> Dict[str, Any]:
    """
    单个 Webnavigate 任务的完整执行流程。

    流程:
        0. 获取可用 group_id
        1. 申请内存额度
        2. 分配端口
        3. 重建容器 + 初始化 VM + 禁用屏保 + 打开浏览器
        4. 清空收藏夹
        5. Agent 执行任务
        6. 书签评估
        7. 清理

    输入:
        task_id: 任务 ID
        task_path: 任务 JSON 路径
        task_config: 任务配置字典
        available_groups: 可用 group_id 队列
        args: 命令行参数
        memory_guard: 内存管理器
        output_dir: 输出目录
    输出:
        task_result 字典
    """
    group_id = available_groups.get()

    config = ContainerSetConfig(
        group_id=group_id,
        num_vms=args.vms_per_task,
        vm_memory=args.vm_memory,
        vm_cpu_cores=args.vm_cpu_cores,
        shared_host_dir=f"{args.shared_base_dir}/group_{group_id}",
        vm_ip=args.vm_ip,
        docker_image=args.docker_image,
        qcow2_path=args.qcow2_path,
    )

    log = get_task_logger(group_id, task_id)
    log.info("获得组 %d，开始执行任务 %s", group_id, task_id)

    task_result: Dict[str, Any] = {
        "task_id": task_id,
        "task_uid": task_config.get("task_uid", ""),
        "instruction": task_config.get("instruction", ""),
        "answer": task_config.get("answer", ""),
        "model_output_answer": "",
        "plan_agent_model": "",
        "gui_agent_model": "",
        "evaluator_output": None,
        "interrupted": False,
        "interrupt_reason": "",
        "group_id": group_id,
        "token_usage": None,
        "bookmark_reset": {},
    }

    # 1. 申请内存额度
    if not memory_guard.acquire(config.num_vms):
        task_result["interrupted"] = True
        task_result["interrupt_reason"] = "memory_guard_timeout"
        log.error("内存申请超时，跳过任务")
        available_groups.put(group_id)
        return task_result

    try:
        # 2. 分配端口（含远程端口扫描，自动避开已占用端口）
        log.info("为组 %d 分配端口（扫描远程已用端口）...", group_id)
        _creds_port = get_ssh_credentials(config.vm_ip)
        remote_ports = scan_remote_docker_ports(
            ssh_password=_creds_port["ssh_password"],
            ssh_opts=_creds_port["ssh_opts"],
            ssh_host=_creds_port["ssh_host"],
            conda_activate=_creds_port["conda_activate"],
        )
        config.containers = allocate_ports_for_group(
            config.num_vms, group_id, extra_used_ports=remote_ports,
        )

        with _active_groups_lock:
            _active_groups[group_id] = config

        vm_ports = config.get_server_ports()
        register_group_ports(group_id, vm_ports)

        # 确保远端共享目录存在
        _creds = get_ssh_credentials(config.vm_ip)
        mkdir_cmd = f"{_creds['conda_activate']} && mkdir -p {config.shared_host_dir}"
        subprocess.run(
            ["sshpass", "-p", _creds["ssh_password"], "ssh"]
            + _creds["ssh_opts"] + [_creds["ssh_host"], mkdir_cmd],
            capture_output=True, text=True, timeout=30,
        )

        # 3. 重建容器 + 初始化 VM（如有 prepare_script_path 则下载文件）
        reset_mode = getattr(args, "reset_mode", "rebuild")
        prepare_url = task_config.get("prepare_script_path", "")
        if not reinitialize_vms(
            config,
            log,
            mode=reset_mode,
            prepare_url=prepare_url,
            task_uid=task_config.get("task_uid", ""),
        ):
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "reinitialize_vms_failed"
            log.error("环境初始化失败，跳过当前任务")
            return task_result

        # 4. 清空收藏夹
        task_result["bookmark_reset"] = clear_bookmarks_parallel(
            config.vm_ip, vm_ports, log
        )
        if not _bookmark_reset_succeeded(
            task_result["bookmark_reset"],
            vm_ports,
        ):
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "bookmark_reset_failed"
            log.error(
                "至少一台 VM 的书签重置失败，停止浏览器准备与 Agent 执行: %s",
                task_result["bookmark_reset"],
            )
            return task_result

        # 清空书签会关闭 Chrome；按评价模式选择普通启动或原 OSWorld
        # remote-debugging config，且必须在 Agent 执行前确认就绪。
        if not _prepare_browser_after_reset(task_config, config, log):
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "browser_prepare_failed"
            log.error("浏览器评价环境准备失败，跳过当前任务")
            return task_result

        # 5. Agent 执行任务
        try:
            agent_mode = getattr(args, "agent_mode", "plan")
            if agent_mode == "gui_only":
                result, _ = stage2_execute_gui_only(
                    task_config, task_id, config, log,
                    gui_agent=getattr(args, "gui_agent", "seed18"),
                    max_rounds=getattr(args, "gui_max_rounds", 200),
                    gui_timeout=getattr(args, "gui_timeout", 3600),
                    output_dir=output_dir,
                )
            else:
                result, _ = stage2_execute_plan(
                    task_config, task_id, config, log,
                    output_dir=output_dir,
                )
        except Exception as exc:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = f"stage2_exception: {exc}"
            log.error("Agent 执行失败: %s", exc)
            return task_result

        # 保存 Plan Agent 执行状态（避免 API 错误等被静默吞掉）
        if isinstance(result, dict):
            if not result.get("success", True):
                task_result["plan_agent_error"] = result.get("error", "unknown_error")
            if result.get("status"):
                task_result["plan_agent_status"] = result["status"]

        # 提取执行摘要
        execution_record = (
            result.get("execution_record", {}) if isinstance(result, dict) else {}
        )
        if execution_record:
            summary = execution_record.get("summary", {})
            task_result["model_output_answer"] = summary.get("final_answer", "")
        else:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "missing_execution_record"

        # 提取 token 消耗
        raw_token = result.get("token_usage") if isinstance(result, dict) else None
        if raw_token:
            plan_usage = raw_token.get("plan_agent", {})
            gui_usage = raw_token.get("gui_agent", {})
            plan_model = raw_token.get("plan_agent_model", "")
            gui_model = raw_token.get("gui_agent_model", "unknown")
            plan_cost = calculate_cost(plan_usage, plan_model)
            gui_cost = calculate_cost(gui_usage, gui_model)
            task_result["plan_agent_model"] = plan_model
            task_result["gui_agent_model"] = gui_model
            task_result["token_usage"] = {
                "plan_agent": {
                    **plan_usage,
                    "model": plan_model,
                    "cost_usd": plan_cost["total_cost"],
                },
                "gui_agent": {
                    **gui_usage,
                    "model": gui_model,
                    "cost_usd": gui_cost["total_cost"],
                },
                "total_cost_usd": plan_cost["total_cost"] + gui_cost["total_cost"],
            }

        # ---- 中间保存：Stage 2 结果先落盘，防止 Stage 3 崩溃丢失执行记录 ----
        if output_results is not None and results_lock is not None and output_json_path:
            try:
                with results_lock:
                    output_results[task_id] = dict(task_result)
                    with open(output_json_path, "w", encoding="utf-8") as _f:
                        json.dump(output_results, _f, ensure_ascii=False, indent=2, default=str)
                log.info("Stage 2 结果已中间保存: %s", task_id)
            except Exception as _save_exc:
                log.warning("[中间保存] 写入失败: %s", _save_exc)

        # 6. 按显式模式评价；active-tab 不依赖 bookmark answer。
        if not _has_supported_webnavigate_evaluation(task_config):
            # 任务未声明当前 runner 支持的完整评价协议 → 统一标记为
            # evaluator_error，由上层从 PASS/FAIL 统计中剔除。
            task_result["evaluator_output"] = {
                "score": -1.0, "pass": False, "status": "evaluator_error",
                "reason": "任务未声明当前 WebNavigate runner 支持的完整评价协议",
            }
            log.info("任务 %s 未配置受支持评价协议，跳过 Stage 3", task_id)
        else:
            try:
                eval_result = stage3_evaluate(task_config, config, log)
                task_result["evaluator_output"] = eval_result
            except Exception as exc:
                # 评估异常：评价器自身故障，区别于 agent 中断
                task_result["evaluator_output"] = {
                    "pass": False, "score": -1.0, "status": "evaluator_error",
                    "reason": f"evaluator_exception: {exc}",
                }
                log.error("评估失败: %s", exc)

        log.info("任务 %s 执行完成", task_id)
        return task_result

    finally:
        # 7. 清理
        # 每一级都用 finally 保护下一级：端口注销或容器清理失败不得阻断
        # 内存额度、active group 与队列槽位的归还。
        try:
            try:
                try:
                    unregister_group_ports(group_id)
                finally:
                    cleanup_group_containers(config, log)
            finally:
                memory_guard.release(config.num_vms)
        finally:
            try:
                with _active_groups_lock:
                    _active_groups.pop(group_id, None)
            finally:
                available_groups.put(group_id)
                log.info("组 %d 已释放", group_id)


# ============================================================
# atexit 清理
# ============================================================

def _atexit_cleanup() -> None:
    """程序退出时清理所有活跃的容器组。"""
    log = logging.getLogger("webnavigate.cleanup")
    with _active_groups_lock:
        groups = dict(_active_groups)

    if not groups:
        return

    log.info("程序退出，清理 %d 个活跃容器组...", len(groups))
    for group_id, config in groups.items():
        try:
            cleanup_group_containers(config, log)
        except Exception as exc:
            log.warning("清理组 %d 失败: %s", group_id, exc)


atexit.register(_atexit_cleanup)


# ============================================================
# 参数解析
# ============================================================

def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    输出:
        argparse.Namespace
    """
    parser = argparse.ArgumentParser(
        description="Webnavigate 批量任务 Pipeline — 多线程并行版本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  # 默认 3 任务并行，Plan Agent 模式\n"
            "  python run_webnavigate_pipeline_parallel.py -p 3 --vm-memory 2G\n\n"
            "  # 纯 GUI Agent 模式\n"
            "  python run_webnavigate_pipeline_parallel.py --agent-mode gui_only -p 3\n\n"
            "  # 指定任务子集\n"
            "  python run_webnavigate_pipeline_parallel.py --task-ids Webnavigate-001,Webnavigate-003\n"
        ),
    )
    parser.add_argument(
        "-p", "--max-parallel-tasks",
        type=int, default=3,
        help="最大并发任务数（默认 3）",
    )
    parser.add_argument(
        "-n", "--vms-per-task",
        type=int, default=5,
        help="每个任务启动的 VM 数量（默认 5，gui_only 模式自动设为 1）",
    )
    parser.add_argument(
        "--vm-memory",
        type=str, default="2G",
        help='每个 VM 内存（默认 "2G"）',
    )
    parser.add_argument(
        "--vm-cpu-cores",
        type=str, default="1",
        help='每个 VM CPU 核数（默认 "1"）',
    )
    parser.add_argument(
        "--memory-limit-gb",
        type=float, default=48.0,
        help="容器区可用总内存上限 GiB（默认 48.0）",
    )
    parser.add_argument(
        "--vm-ip",
        type=str, default=resolve_host_ip("auto"),
        help="Docker 宿主机 IP（默认自动探测当前设备的默认出口 IP）",
    )
    parser.add_argument(
        "--shared-base-dir",
        type=str, default="/home/benchmark/shared",
        help="共享目录根路径（默认 /home/benchmark/shared）",
    )
    parser.add_argument(
        "--qcow2-path",
        type=str,
        default="./resources/Ubuntu.qcow2",
        help="VM 磁盘镜像路径（默认 ./resources/Ubuntu.qcow2）",
    )
    parser.add_argument(
        "--docker-image",
        type=str, default="happysixd/osworld-docker-sshfs",
        help="Docker 镜像名",
    )
    parser.add_argument(
        "--reset-mode",
        type=str, default="rebuild",
        choices=["rebuild", "clean"],
        help="任务间环境重置策略（默认 rebuild；clean 为轻量级清理不重建容器）",
    )
    parser.add_argument(
        "--gui-agent",
        type=str, default="seed18",
        help="GUI Agent 类型（默认 seed18，可选 claude/kimi）",
    )
    parser.add_argument(
        "--agent-mode",
        type=str, default="plan",
        choices=["plan", "gui_only"],
        help="Agent 模式：plan（默认）或 gui_only",
    )
    parser.add_argument(
        "--gui-max-rounds",
        type=int, default=200,
        help="纯 GUI Agent 模式的最大执行轮次（默认 200）",
    )
    parser.add_argument(
        "--gui-timeout",
        type=int, default=3600,
        help="纯 GUI Agent 模式的超时时间（秒，默认 3600）",
    )
    parser.add_argument(
        "--task-ids",
        type=str, default="",
        help="指定任务 ID 列表（逗号分隔，如 Webnavigate-001,settings-002）。"
             "会自动补全为完整 task_id 格式。为空则使用全部 9 个默认任务。",
    )
    parser.add_argument(
        "--task-list-file",
        type=str, default="",
        help="从文件读取任务 ID 列表（每行一个 ID，忽略空行和 # 开头的注释行）",
    )
    parser.add_argument(
        "--output-json-path",
        type=str, default="",
        help="自定义输出 JSON 路径（默认 logs/webnavigate_<timestamp>/results.json）",
    )
    parser.add_argument(
        "--skip-completed-dir",
        type=str, default="",
        help="跳过已完成任务：指定历史结果目录路径。"
             "从 results.json（key=task_id）和 webnavigate_execution_*_.json 文件名中提取已完成 task_id。"
             "支持逗号分隔多个目录。",
    )
    return parser.parse_args()


def _expand_task_id(short_id: str) -> str:
    """
    将简短的任务 ID 扩展为完整格式。

    输入:
        short_id: 如 "Webnavigate-001" 或 "settings-002"
    输出:
        完整 task_id: 如 "Operation-WebOperate-Webnavigate-001"
    """
    short_id = short_id.strip()
    candidate = short_id
    if not candidate.startswith("Operation-") and (
        candidate.lower().startswith("webnavigate")
        or candidate.lower().startswith("settings")
    ):
        candidate = f"Operation-WebOperate-{candidate}"

    # CLI 历史示例使用 Webnavigate/settings，但文件系统上的 canonical
    # 名称为 WebNavigate/Settings。按 casefold 映射既保留兼容，又避免构造
    # 一个实际不存在的文件路径。
    for canonical_id in DEFAULT_TASK_IDS:
        if canonical_id.casefold() == candidate.casefold():
            return canonical_id
    return candidate


def _build_preflight_skip_result(
    task_id: str,
    task_config: Dict[str, Any],
) -> Dict[str, Any]:
    """为 skip_eval 任务构造无需启动 Agent/VM 的结果。

    功能：在并行调度前保留任务完整性和跳过原因，同时明确产生
    ``status=skip`` 三态结果，避免不可评任务消耗模型与容器资源。
    输入参数：task_id 为 canonical ID；task_config 为任务 JSON 配置。
    输出返回值：与 run_single_task 主要字段兼容的跳过结果字典。
    """
    reason = str(
        task_config.get("skip_eval_reason")
        or "任务已标记 skip_eval=true，跳过执行与评估。"
    )
    return {
        "task_id": task_id,
        "task_uid": task_config.get("task_uid", ""),
        "instruction": task_config.get("instruction", ""),
        "answer": task_config.get("answer", ""),
        "model_output_answer": "",
        "evaluator_output": {
            "pass": None,
            "score": None,
            "status": "skip",
            "reason": reason,
            "task_id": task_id,
        },
        "interrupted": False,
        "interrupt_reason": "",
        "group_id": None,
        "token_usage": None,
        "bookmark_reset": {},
        "skipped": True,
        "skip_eval_reason": reason,
        "evaluation_reason": reason,
    }


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    """
    主流程：多线程并行 Webnavigate 任务调度器。

    1. 解析参数 + 环境检查
    2. 加载任务
    3. 创建 MemoryGuard + group_id 池 + 心跳线程
    4. ThreadPoolExecutor 并行提交任务
    5. 收集结果并写入 JSON
    """
    args = parse_args()
    setup_logging(args.max_parallel_tasks)
    log = logging.getLogger("webnavigate.main")

    # ------ ablation 框架环境变量覆盖 ------
    _ablation_agent_mode = os.environ.get("ABLATION_AGENT_MODE", "")
    _ablation_gui_agent = os.environ.get("ABLATION_GUI_AGENT", "")
    if _ablation_agent_mode:
        args.agent_mode = _ablation_agent_mode
        log.info("[ablation] 环境变量覆盖 agent_mode=%s", _ablation_agent_mode)
    if _ablation_gui_agent:
        args.gui_agent = _ablation_gui_agent
        log.info("[ablation] 环境变量覆盖 gui_agent=%s", _ablation_gui_agent)

    agent_mode = getattr(args, "agent_mode", "plan")

    # gui_only 模式：强制 -n 1
    if agent_mode == "gui_only" and args.vms_per_task > 1:
        log.warning(
            "[gui_only] --vms-per-task=%d > 1，gui_only 模式每个 agent 仅绑定 1 台 VM，"
            "已自动覆盖为 -n 1",
            args.vms_per_task,
        )
        args.vms_per_task = 1

    log.info("=" * 80)
    log.info("Webnavigate 批量任务 Pipeline — 多线程并行版本")
    log.info(
        "  Agent 模式: %s | 并发数: %d | VM/任务: %d | VM 内存: %s | 内存上限: %.1f GiB",
        agent_mode, args.max_parallel_tasks, args.vms_per_task,
        args.vm_memory, args.memory_limit_gb,
    )
    log.info("=" * 80)

    # 加载任务
    task_ids = None
    if args.task_list_file:
        # 从文件读取任务 ID（每行一个，忽略空行和 # 注释行）
        with open(args.task_list_file, "r", encoding="utf-8") as f:
            raw_ids = [
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
        task_ids = [_expand_task_id(tid) for tid in raw_ids]
        log.info("从文件 %s 读取了 %d 个任务 ID", args.task_list_file, len(task_ids))
    elif args.task_ids:
        raw_ids = [tid.strip() for tid in args.task_ids.split(",") if tid.strip()]
        task_ids = [_expand_task_id(tid) for tid in raw_ids]

    task_items = scan_webnavigate_tasks(TASKS_LIST_DIR, task_ids=task_ids)
    log.info("共加载 %d 个 Webnavigate 任务", len(task_items))

    # 跳过已完成的任务
    if args.skip_completed_dir:
        completed_task_ids: set = set()
        for one_dir in args.skip_completed_dir.split(","):
            one_dir = one_dir.strip()
            if not one_dir or not os.path.isdir(one_dir):
                continue
            # 方式 1: 从 results.json 提取 key（task_id）
            rj = os.path.join(one_dir, "results.json")
            if os.path.isfile(rj):
                try:
                    with open(rj, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        completed_task_ids.update(data.keys())
                except Exception:
                    pass
            # 方式 2: 从 webnavigate_execution_{task_id}_{timestamp}.json 文件名提取
            for fname in os.listdir(one_dir):
                m = re.match(r"webnavigate_execution_(.+)_\d{8}_\d{6}\.json$", fname)
                if m:
                    completed_task_ids.add(m.group(1))
        if completed_task_ids:
            before_count = len(task_items)
            task_items = [
                (tid, path, cfg) for tid, path, cfg in task_items
                if tid not in completed_task_ids
            ]
            skipped = before_count - len(task_items)
            log.info("跳过已完成任务: %d 个（来自 %s）", skipped, args.skip_completed_dir)

    for i, (tid, path, cfg) in enumerate(task_items, 1):
        target_url_count = len(
            [u.strip() for u in cfg.get("answer", "").split(",") if u.strip()]
        )
        log.info("  %d. %s (%d 个目标 URL)", i, tid, target_url_count)

    if not task_items:
        log.warning("未找到 Webnavigate 任务（全部已跳过或无匹配），退出")
        return

    preflight_skips = {
        task_id: _build_preflight_skip_result(task_id, task_config)
        for task_id, _, task_config in task_items
        if task_config.get("skip_eval")
    }
    task_items = [
        item for item in task_items if not item[2].get("skip_eval")
    ]
    if preflight_skips:
        log.info(
            "调度前跳过 %d 个 skip_eval 任务，不启动 Agent 或 VM。",
            len(preflight_skips),
        )

    # 创建内存管理器
    memory_guard = MemoryGuard(args.memory_limit_gb, args.vm_memory)

    # 创建 group_id 池
    available_groups: queue.Queue = queue.Queue()
    for g in range(args.max_parallel_tasks):
        available_groups.put(g)
    log.info("已初始化 %d 个容器组槽位", args.max_parallel_tasks)

    # 启动全局防黑屏心跳
    heartbeat = None
    if task_items:
        heartbeat = GlobalScreensaverHeartbeat(vm_ip=args.vm_ip, interval_sec=180)
        heartbeat.start()

    # 结果收集
    output_results: Dict[str, Any] = dict(preflight_skips)
    results_lock = threading.Lock()
    output_json_path = os.path.abspath(
        args.output_json_path if args.output_json_path else OUTPUT_JSON_PATH
    )
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)

    # 并行调度
    completed_count = len(preflight_skips)
    total_count = completed_count + len(task_items)

    with ThreadPoolExecutor(
        max_workers=args.max_parallel_tasks,
        thread_name_prefix="Webnavigate",
    ) as executor:
        futures = {}

        for i, (task_id, task_path, task_config) in enumerate(task_items):
            submitted_index = len(preflight_skips) + i + 1
            log.info("提交任务 %d/%d | %s", submitted_index, total_count, task_id)

            fut = executor.submit(
                run_single_task,
                task_id, task_path, task_config,
                available_groups, args, memory_guard,
                os.path.dirname(output_json_path),
                output_results, results_lock, output_json_path,
            )
            futures[fut] = (task_id, submitted_index)

        # 收集结果
        for fut in as_completed(futures):
            task_id, index = futures[fut]
            try:
                task_result = fut.result()
            except Exception as exc:
                log.error("任务 %s 异常: %s", task_id, exc)
                task_result = {
                    "task_id": task_id,
                    "interrupted": True,
                    "interrupt_reason": f"uncaught_exception: {exc}",
                }

            with results_lock:
                output_results[task_id] = task_result
                completed_count += 1

            # 判定状态
            eval_out = task_result.get("evaluator_output") or {}
            is_passed = eval_out.get("pass", False)
            score = eval_out.get("score", 0.0)

            if task_result.get("interrupted"):
                status = "INTERRUPTED"
            elif eval_out.get("status") == "evaluator_error":
                # 评价器自身无法给出有意义的判定：从 PASS/FAIL 统计中剔除
                status = "EVALUATOR_ERROR"
            elif is_passed:
                status = "PASS"
            else:
                status = "FAIL"

            log.info(
                "任务完成 %d/%d | %s | 状态: %s | 得分: %.2f",
                completed_count, total_count, task_id, status, score,
            )

            # 实时持久化中间结果
            try:
                with results_lock:
                    with open(output_json_path, "w", encoding="utf-8") as f:
                        json.dump(output_results, f, ensure_ascii=False, indent=2, default=str)
            except Exception as exc:
                log.warning("写入中间结果失败: %s", exc)

    # 停止心跳
    if heartbeat is not None:
        heartbeat.stop()

    # 写入最终结果
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output_results, f, ensure_ascii=False, indent=2, default=str)

    # 汇总
    log.info("=" * 80)
    log.info("全部任务执行完成 (%d/%d)", completed_count, total_count)
    log.info("=" * 80)

    pass_count = 0
    fail_count = 0
    interrupt_count = 0
    eval_error_count = 0
    skip_count = 0
    total_cost = 0.0

    for tid, res in output_results.items():
        eval_out = res.get("evaluator_output") or {}
        is_passed = eval_out.get("pass", False)
        raw_score = eval_out.get("score")
        score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0

        if res.get("interrupted"):
            status = "INTERRUPTED"
            interrupt_count += 1
        elif eval_out.get("status") == "skip":
            status = "SKIP"
            skip_count += 1
        elif eval_out.get("status") == "evaluator_error":
            status = "EVALUATOR_ERROR"
            eval_error_count += 1
        elif is_passed:
            status = "PASS"
            pass_count += 1
        else:
            status = "FAIL"
            fail_count += 1

        token_info = res.get("token_usage") or {}
        task_cost = token_info.get("total_cost_usd", 0.0)
        total_cost += task_cost
        cost_str = f" | 费用: ${task_cost:.4f}" if task_cost > 0 else ""

        match_detail = eval_out.get("match_detail", {})
        matched = match_detail.get("matched_count", 0)
        total_targets = match_detail.get(
            "expected_count", match_detail.get("total_targets", 0)
        )

        log.info(
            "  %s %s | 得分: %.2f (%d/%d URL){cost_str}".replace("{cost_str}", cost_str),
            status, tid, score, matched, total_targets,
        )

    log.info("-" * 40)
    log.info(
        "  PASS: %d | FAIL: %d | SKIP: %d | INTERRUPTED: %d | EVALUATOR_ERROR: %d | 总计: %d",
        pass_count, fail_count, skip_count, interrupt_count, eval_error_count, total_count,
    )
    effective_count = pass_count + fail_count
    if effective_count:
        log.info(
            "  有效评价通过率: %.2f%% (%d/%d；已剔除 SKIP/INTERRUPTED/EVALUATOR_ERROR)",
            pass_count * 100.0 / effective_count,
            pass_count,
            effective_count,
        )
    if total_cost > 0:
        log.info("  总 Token 费用: $%.4f", total_cost)
    log.info("输出结果文件: %s", output_json_path)
    log.info("=" * 80)


if __name__ == "__main__":
    main()
