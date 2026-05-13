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
    read_bookmark_urls,
)

# ============================================================
# 常量
# ============================================================

TASKS_LIST_DIR = os.path.join(parallel_benchmark_dir, "tasks_list")

# 覆盖的任务 ID 列表（Webnavigate-001~011 + settings-001~003）
DEFAULT_TASK_IDS = [
    "Operation-WebOperate-Webnavigate-001",
    "Operation-WebOperate-Webnavigate-002",
    "Operation-WebOperate-Webnavigate-003",
    "Operation-WebOperate-Webnavigate-004",
    "Operation-WebOperate-Webnavigate-005",
    "Operation-WebOperate-Webnavigate-006",
    "Operation-WebOperate-Webnavigate-007",
    "Operation-WebOperate-Webnavigate-008",
    "Operation-WebOperate-Webnavigate-009",
    "Operation-WebOperate-Webnavigate-010",
    "Operation-WebOperate-Webnavigate-011",
    "Operation-WebOperate-settings-001",
    "Operation-WebOperate-settings-002",
    "Operation-WebOperate-settings-003",
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
        f"subprocess.Popen(['google-chrome', '--no-first-run', '--no-default-browser-check', '{start_url}'], env=env)\n"
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
) -> bool:
    """
    VM 环境重置。支持 rebuild（完全重建）和 clean（轻量级清理）两种模式。

    输入:
        config: 容器组配置
        log: logger
        mode: "rebuild"（默认，完全重建容器）或 "clean"（轻量级清理浏览器状态）
        prepare_url: 任务数据下载 URL（仅 rebuild 模式下第一个 VM 会下载）
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

        # 2. 初始化所有 VM（挂载 shared，按需下载数据文件）
        vm_pairs = config.get_vm_pairs()
        success_count = 0
        for idx, (vm_port, vnc_port) in enumerate(vm_pairs):
            if init_vm_parallel(
                vm_port=vm_port,
                vnc_port=vnc_port,
                prepare_url=prepare_url,
                shared_host_dir=config.shared_host_dir,
                vm_ip=vm_ip,
                is_first_vm=(idx == 0),
                rebuilt=True,
                log=log,
            ):
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
    elif gui_agent == "kimi":
        gui_tool = KimiGUIAgentTool(controller=controller_vm1)
    elif gui_agent == "seed18":
        gui_tool = Seed18GUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")
    elif gui_agent == "gpt54":
        from parallel_agents_as_tools.gpt54_gui_agent_as_tool import GPT54GUIAgentTool
        gui_tool = GPT54GUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")
    elif gui_agent == "gpt54_fc":
        from parallel_agents_as_tools.gpt_gui_agent_as_tool import GPTGUIAgentTool
        gui_tool = GPTGUIAgentTool(
            controller=controller_vm1,
            model_name="gpt-5.4-mini",
            api_config_key="pincc",
        )
    elif gui_agent == "qwen":
        # Qwen3-VL baseline：通过环境变量 BENCH_DEFAULT_QWEN_GUI_AGENT
        # 切换具体模型（如 qwen3-vl、qwen3-vl-235b-a22b）
        from parallel_agents_as_tools.qwen_gui_agent_as_tool import QwenGUIAgentTool
        gui_tool = QwenGUIAgentTool(controller=controller_vm1, prompt_mode="gui_only")
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

def stage3_evaluate(
    task_config: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> Dict[str, Any]:
    """
    从所有 VM 读取 Chrome 收藏夹，与任务 answer 中的目标 URL 做匹配。

    输入:
        task_config: 任务配置（含 answer 字段）
        config: 容器组配置
        log: logger
    输出:
        评估结果字典
    """
    log.info("STAGE 3: 书签评估")

    vm_ip = config.vm_ip
    vm_ports = config.get_server_ports()

    # 1. 从所有 VM 读取书签 URL（合并去重）
    per_vm_urls: Dict[int, List[str]] = {}
    errors: Dict[int, str] = {}
    all_urls: List[str] = []

    for port in vm_ports:
        controller = PythonController(vm_ip=vm_ip, server_port=port)
        try:
            urls = read_bookmark_urls(controller)
        except Exception as exc:
            urls = []
            errors[port] = str(exc)
        per_vm_urls[port] = urls
        all_urls.extend(urls)

    merged_urls = list(dict.fromkeys(all_urls))  # 去重保序

    log.info("收藏夹合并后 URL 数量: %d", len(merged_urls))
    for i, url in enumerate(merged_urls, 1):
        log.info("  %d. %s", i, url)

    if errors:
        for port, err in errors.items():
            log.warning("  VM %d 读取书签失败: %s", port, err)

    # 2. 调用 evaluator
    # 延迟导入以避免循环依赖
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
    evaluator_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator_module)

    eval_result = evaluator_module.evaluate(
        task=task_config,
        bookmark_urls=merged_urls,
    )

    # 附加 per-VM 调试信息
    eval_result["bookmark_per_vm_urls"] = {str(k): v for k, v in per_vm_urls.items()}
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
        if not reinitialize_vms(config, log, mode=reset_mode, prepare_url=prepare_url):
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "reinitialize_vms_failed"
            log.error("环境初始化失败，跳过当前任务")
            return task_result

        # 4. 清空收藏夹
        task_result["bookmark_reset"] = clear_bookmarks_parallel(
            config.vm_ip, vm_ports, log
        )

        # 重新打开浏览器（清空书签时会关闭 Chrome）
        open_browser_parallel(config.vm_ip, vm_ports, log)

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

        # 6. 书签评估（无 answer / 无 evaluator_path 的任务跳过自动评估）
        answer = task_config.get("answer", "")
        evaluator_path = task_config.get("evaluator_path", "")
        if not answer or not str(answer).strip() or not str(evaluator_path).strip():
            # 任务自身未声明评价目标或评价器路径 → 统一标记为 evaluator_error，
            # 由上层从 PASS/FAIL 统计中剔除（与 operation_evaluator 状态语义一致）
            task_result["evaluator_output"] = {
                "score": -1.0, "pass": False, "status": "evaluator_error",
                "reason": "任务未声明 answer 或 evaluator_path，无法自动评价",
            }
            log.info("任务 %s 未配置自动评价目标，跳过书签评估", task_id)
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
        unregister_group_ports(group_id)
        cleanup_group_containers(config, log)
        memory_guard.release(config.num_vms)

        with _active_groups_lock:
            _active_groups.pop(group_id, None)

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
        help="GUI Agent 类型（默认 seed18，可选 claude / kimi / gpt54 / gpt54_fc / qwen）",
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
    if short_id.startswith("Operation-"):
        return short_id
    if short_id.lower().startswith("webnavigate"):
        return f"Operation-WebOperate-{short_id}"
    if short_id.lower().startswith("settings"):
        return f"Operation-WebOperate-{short_id}"
    return short_id


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

    # 创建内存管理器
    memory_guard = MemoryGuard(args.memory_limit_gb, args.vm_memory)

    # 创建 group_id 池
    available_groups: queue.Queue = queue.Queue()
    for g in range(args.max_parallel_tasks):
        available_groups.put(g)
    log.info("已初始化 %d 个容器组槽位", args.max_parallel_tasks)

    # 启动全局防黑屏心跳
    heartbeat = GlobalScreensaverHeartbeat(vm_ip=args.vm_ip, interval_sec=180)
    heartbeat.start()

    # 结果收集
    output_results: Dict[str, Any] = {}
    results_lock = threading.Lock()
    output_json_path = os.path.abspath(
        args.output_json_path if args.output_json_path else OUTPUT_JSON_PATH
    )
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)

    # 并行调度
    completed_count = 0
    total_count = len(task_items)

    with ThreadPoolExecutor(
        max_workers=args.max_parallel_tasks,
        thread_name_prefix="Webnavigate",
    ) as executor:
        futures = {}

        for i, (task_id, task_path, task_config) in enumerate(task_items):
            log.info("提交任务 %d/%d | %s", i + 1, total_count, task_id)

            fut = executor.submit(
                run_single_task,
                task_id, task_path, task_config,
                available_groups, args, memory_guard,
                os.path.dirname(output_json_path),
                output_results, results_lock, output_json_path,
            )
            futures[fut] = (task_id, i + 1)

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
    total_cost = 0.0

    for tid, res in output_results.items():
        eval_out = res.get("evaluator_output") or {}
        is_passed = eval_out.get("pass", False)
        score = eval_out.get("score", 0.0)

        if res.get("interrupted"):
            status = "INTERRUPTED"
            interrupt_count += 1
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
        total_targets = match_detail.get("total_targets", 0)

        log.info(
            "  %s %s | 得分: %.2f (%d/%d URL){cost_str}".replace("{cost_str}", cost_str),
            status, tid, score, matched, total_targets,
        )

    log.info("-" * 40)
    log.info(
        "  PASS: %d | FAIL: %d | INTERRUPTED: %d | EVALUATOR_ERROR: %d | 总计: %d",
        pass_count, fail_count, interrupt_count, eval_error_count, total_count,
    )
    if total_cost > 0:
        log.info("  总 Token 费用: $%.4f", total_cost)
    log.info("输出结果文件: %s", output_json_path)
    log.info("=" * 80)


if __name__ == "__main__":
    main()
