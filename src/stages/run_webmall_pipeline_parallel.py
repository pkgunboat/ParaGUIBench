"""
WebMall 批量任务 Pipeline — 多线程并行版本。

基于 run_webmall_pipeline.py，新增并行任务调度、动态端口分配、内存管理。
不修改任何原有系统代码，通过导入可复用函数 + 参数化重写实现。

与串行版的区别：
- 3 个 WebMall 任务（Search/Cart/Checkout）可同时执行
- 每个任务独占一组容器（group_id 隔离），通过 queue.Queue 互斥分配
- 动态端口分配（allocate_ports_for_group），不再硬编码 5000-5004
- MemoryGuard 控制总内存占用
- GlobalScreensaverHeartbeat 支持动态端口列表

用法:
    # 3 任务全并行（推荐配置）
    python run_webmall_pipeline_parallel.py -p 3 --vm-memory 2G --memory-limit-gb 48

    # 串行模式（与原版一致的 fallback）
    python run_webmall_pipeline_parallel.py -p 1

    # 节省内存模式
    python run_webmall_pipeline_parallel.py -p 2 --vm-memory 1G --memory-limit-gb 30
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse as _urlparse

import requests

# ============================================================
# 路径设置
# ============================================================

current_dir = os.path.dirname(os.path.abspath(__file__))
ubuntu_env_dir = os.path.dirname(current_dir)
parallel_benchmark_dir = os.path.join(ubuntu_env_dir, "parallel_benchmark")
extra_docker_env_dir = os.path.join(ubuntu_env_dir, "extra_docker_env")
webmall_dir = os.path.join(extra_docker_env_dir, "WebMall")
webmall_eval_assets_dir = os.path.join(current_dir, "webmall_eval_assets")

if parallel_benchmark_dir not in sys.path:
    sys.path.insert(0, parallel_benchmark_dir)
if ubuntu_env_dir not in sys.path:
    sys.path.insert(0, ubuntu_env_dir)
if webmall_eval_assets_dir not in sys.path:
    sys.path.insert(0, webmall_eval_assets_dir)

# ============================================================
# 从原始 WebMall pipeline 导入可复用函数（不修改原文件）
# ============================================================

from config_loader import resolve_host_ip  # noqa: E402
from run_webmall_pipeline import (  # noqa: E402
    ensure_conda_env,
    scan_webmall_tasks,
    rewrite_webmall_string_instruction,
    evaluate_string_task,
    extract_execution_summary,
    check_webmall_shops,
    WEBMALL_TASKS_DIR,
    DEFAULT_TASK_UIDS,
)

# 多机同步：当前节点 host_tag，作为 logs/ 下的命名空间目录名
from pipelines._host_tag import get_host_tag  # noqa: E402

# ============================================================
# 从 QA 并行 pipeline 导入容器管理函数（不修改原文件）
# ============================================================

from run_QA_pipeline_parallel import (  # noqa: E402
    rebuild_containers_parallel,
    cleanup_group_containers,
    init_vm_parallel,
    get_ssh_credentials,
)

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
# Agent 相关组件导入（不修改原模块）
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

# WebMall: 基于收藏夹(Bookmarks)的 string 任务评测辅助工具
from webmall_eval_assets.bookmark_utils import (  # noqa: E402
    close_chrome_and_clear_bookmarks,
    read_bookmark_urls,
)

# WebMall: 基于 Accessibility Tree 的 Cart / Checkout 评价器
from webmall_eval_assets.cart_evaluator_from_at import (  # noqa: E402
    create_checkpoints_from_urls,
    detect_vm_all_carts,
    evaluate_all_vms,
)
from webmall_eval_assets.checkout_evaluator_from_at import (  # noqa: E402
    ExpectedCheckout,
    extract_checkout_info,
    extract_checkout_info_with_recovery,
    get_at as get_checkout_at,
    verify_checkout,
)

# ============================================================
# 常量
# ============================================================

OUTPUT_JSON_PATH = os.path.join(
    ubuntu_env_dir, "logs",
    f"webmall_formal_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    "results.json",
)

# 全局追踪：记录所有已启动的容器组（用于 atexit 清理）
_active_groups: Dict[int, ContainerSetConfig] = {}
_active_groups_lock = threading.Lock()


# ============================================================
# 日志系统
# ============================================================

def setup_logging(max_parallel: int) -> None:
    """
    配置日志系统。多线程模式下使用带线程标识的格式。

    输入:
        max_parallel: 最大并行数（用于决定日志格式）
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


def get_task_logger(group_id: int, task_uid: str) -> logging.Logger:
    """
    获取带有组 ID 和任务 UID 前缀的 logger。

    输入:
        group_id: 容器组编号
        task_uid: 任务 UID

    输出:
        配置好的 logger 实例
    """
    uid_short = task_uid[:8] if task_uid else "unknown"
    logger_name = f"webmall.G{group_id}.{uid_short}"
    return logging.getLogger(logger_name)


# ============================================================
# 活跃端口注册表（用于 GlobalScreensaverHeartbeat 动态端口列表）
# ============================================================

_active_ports: Dict[int, List[int]] = {}  # group_id -> [server_port, ...]
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
    从全局活跃端口表中注销某组的端口。

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
    全局防黑屏心跳守护线程，支持动态端口列表。

    与原版 ScreensaverHeartbeat 的区别：
    - 端口列表不在初始化时固定，而是每次心跳时从 get_all_active_ports() 动态获取
    - 适用于多任务并行场景：不同组的 VM 在不同时间启动和关闭

    输入：
        vm_ip: VM 宿主 IP
        interval_sec: 心跳间隔（秒），默认 180（3 分钟）
    """

    def __init__(self, vm_ip: str, interval_sec: int = 180):
        self.vm_ip = vm_ip
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _heartbeat_loop(self) -> None:
        """
        心跳循环主体，在后台线程中运行。
        每隔 interval_sec 秒向所有活跃 VM 发送屏保重置命令。
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

        log = logging.getLogger("webmall.heartbeat")

        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.interval_sec):
                break

            # 动态获取当前活跃的端口列表
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
        logging.getLogger("webmall.heartbeat").info(
            "GlobalScreensaverHeartbeat 已启动（间隔 %ds）", self.interval_sec
        )

    def stop(self) -> None:
        """停止心跳守护线程。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logging.getLogger("webmall.heartbeat").info("GlobalScreensaverHeartbeat 已停止")


# ============================================================
# 参数化辅助函数（替代原文件中硬编码端口的版本）
# ============================================================

def clear_bookmarks_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> Dict[int, Dict[str, Any]]:
    """
    在指定端口的所有 VM 上关闭浏览器并清空 Bookmarks。
    参数化版本，替代原 clear_bookmarks_on_all_vms() 的硬编码端口。

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


def disable_screensaver_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> None:
    """
    在指定端口的所有 VM 中禁用屏保和锁屏。
    参数化版本，替代原 disable_screensaver_on_all_vms() 的硬编码端口。

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
    在指定端口的所有 VM 中打开 Google Chrome 浏览器并最大化。
    参数化版本，替代原 open_browser_on_all_vms() 的硬编码端口。

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
            resp = requests.post(url, json={"command": launch_script}, timeout=20)
            if resp.status_code == 200:
                log.info("  VM %d Chrome 已打开并最大化", port)
            else:
                log.warning("  VM %d Chrome 启动失败 (HTTP %d)", port, resp.status_code)
        except Exception as exc:
            log.warning("  VM %d Chrome 启动失败: %s", port, exc)


def clean_browser_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> None:
    """
    在指定端口的所有 VM 中清空浏览器状态（不重建容器的轻量级清理方案）。
    参数化版本，替代原 clean_browser_on_all_vms() 的硬编码端口。

    输入:
        vm_ip: VM 宿主 IP
        vm_ports: VM server 端口列表
        log: logger
    """
    log.info("清空所有 VM 的浏览器状态...")
    clean_script = (
        "import subprocess, os, glob, time\n"
        "for proc_name in ['google-chrome', 'chromium', 'chrome', 'chromium-browser']:\n"
        "    subprocess.run(['pkill', '-9', '-f', proc_name], capture_output=True)\n"
        "time.sleep(0.5)\n"
        "\n"
        "profile_dirs = [\n"
        "    os.path.expanduser('~/.config/google-chrome/Default'),\n"
        "    os.path.expanduser('~/.config/chromium/Default'),\n"
        "    os.path.expanduser('~/snap/chromium/common/chromium/Default'),\n"
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
            resp = requests.post(url, json={"command": clean_script}, timeout=30)
            if resp.status_code == 200:
                output = resp.json().get("output", "")
                log.info("  VM %d 浏览器已清理 (%s)", port, output.strip())
            else:
                log.warning("  VM %d 浏览器清理失败 (HTTP %d)", port, resp.status_code)
        except Exception as exc:
            log.warning("  VM %d 浏览器清理失败: %s", port, exc)


def verify_vm_network_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> Dict[str, Dict[str, bool]]:
    """
    验证指定 VM 到 WebMall 商店的网络可达性。
    参数化版本，替代原 verify_vm_network() 的硬编码端口。

    输入:
        vm_ip: VM 宿主 IP
        vm_ports: VM server 端口列表
        log: logger
    输出:
        Dict[vm_port_str, Dict[shop_port_str, bool]]
    """
    log.info("验证 VM 到 WebMall 商店的网络可达性...")
    shop_ports = ["9081", "9082", "9083", "9084"]
    results: Dict[str, Dict[str, bool]] = {}

    check_script_template = (
        "import subprocess\n"
        "results = {{}}\n"
        "for port in {ports}:\n"
        "    url = f'http://{vm_ip}:{{port}}'\n"
        "    try:\n"
        "        r = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{{http_code}}', '--max-time', '5', url],\n"
        "                           capture_output=True, text=True, timeout=10)\n"
        "        results[port] = r.stdout.strip() == '200'\n"
        "    except Exception:\n"
        "        results[port] = False\n"
        "print(results)\n"
    )

    for vm_port in vm_ports:
        port_key = str(vm_port)
        results[port_key] = {}
        try:
            import ast
            script = check_script_template.format(ports=shop_ports, vm_ip=vm_ip)
            url = f"http://{vm_ip}:{vm_port}/execute"
            resp = requests.post(url, json={"command": script}, timeout=30)
            if resp.status_code == 200:
                resp_data = resp.json()
                output = resp_data.get("output", "")
                try:
                    parsed = ast.literal_eval(output.strip().split("\n")[-1])
                    for sp in shop_ports:
                        results[port_key][sp] = bool(parsed.get(sp, False))
                except Exception:
                    for sp in shop_ports:
                        results[port_key][sp] = False
            else:
                for sp in shop_ports:
                    results[port_key][sp] = False
        except Exception:
            for sp in shop_ports:
                results[port_key][sp] = False

    all_ok = True
    for vm_port_str, port_results in results.items():
        failed_ports = [p for p, ok in port_results.items() if not ok]
        if failed_ports:
            all_ok = False
            log.warning("  VM %s: 无法访问商店端口 %s", vm_port_str, ", ".join(failed_ports))
        else:
            log.info("  VM %s: 所有商店可达", vm_port_str)

    if all_ok:
        log.info("  所有 VM 网络可达性正常")
    else:
        log.warning("  部分 VM 网络可达性异常，任务执行可能受影响")

    return results


def reinitialize_vms_parallel(
    config: ContainerSetConfig,
    log: logging.Logger,
    mode: str = "rebuild",
) -> bool:
    """
    参数化版本的 VM 环境重置。
    整合 rebuild/init/disable_screensaver/open_browser/verify_network 子函数。

    输入:
        config: 容器组配置
        log: logger
        mode: "rebuild" 或 "clean"
    输出:
        bool（是否成功）
    """
    vm_ip = config.vm_ip
    vm_ports = config.get_server_ports()

    if mode == "rebuild":
        log.info("任务环境重置：重建容器 + 初始化 VM (组 %d)", config.group_id)

        # 1. 重建容器
        if not rebuild_containers_parallel(config, log):
            log.error("容器重建失败")
            return False

        # 2. 初始化所有 VM（挂载 shared，WebMall 不需要下载数据文件）
        vm_pairs = config.get_vm_pairs()
        success_count = 0
        for idx, (vm_port, vnc_port) in enumerate(vm_pairs):
            if init_vm_parallel(
                vm_port=vm_port,
                vnc_port=vnc_port,
                prepare_url="",  # WebMall 不需要下载数据
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

        # 5. 验证网络可达性
        verify_vm_network_parallel(vm_ip, vm_ports, log)

        log.info("环境重置完成（rebuild）：%d/%d 个 VM 就绪", success_count, len(vm_pairs))
        return success_count == len(vm_pairs)

    elif mode == "clean":
        log.info("任务环境重置：清空浏览器状态（轻量模式，组 %d）", config.group_id)
        clean_browser_parallel(vm_ip, vm_ports, log)
        disable_screensaver_parallel(vm_ip, vm_ports, log)
        open_browser_parallel(vm_ip, vm_ports, log)
        log.info("环境重置完成（clean）")
        return True

    else:
        log.error("未知的 RESET_MODE: %s", mode)
        return False


# ============================================================
# 参数化版本的 setup_environment
# ============================================================

def setup_env_webmall_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> Tuple[PythonController, List[PythonController], AgentToolRegistry]:
    """
    参数化的环境设置（替代原始 setup_environment 的硬编码端口）。
    逻辑与 run_plan_agent_thought_action.setup_environment() 一致，
    仅 vm_ip 和 ports 改为参数传入。

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

    # 创建工具注册表（支持通过环境变量 ABLATION_GUI_AGENT 切换 GUI Agent）
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
# 纯 GUI Agent 模式的 Agent 执行
# ============================================================

def stage2_execute_gui_only(
    task_config: Dict[str, Any],
    task_uid: str,
    config: ContainerSetConfig,
    log: logging.Logger,
    gui_agent: str = "seed18",
    max_rounds: int = 200,
    gui_timeout: int = 3600,
    output_dir: str = "",
) -> Tuple[Dict[str, Any], PythonController]:
    """
    纯 GUI Agent 模式的 Stage 2：单个 GUI Agent 在单台 VM 上完成完整任务。
    不经过 Plan Agent 任务分解，直接调用 GUI Agent。

    输入:
        task_config: 任务配置
        task_uid: 任务 UID
        config: 容器组配置（仅使用第一个 VM）
        log: logger
        gui_agent: GUI Agent 类型（目前仅支持 seed18）
        max_rounds: 最大执行轮次
        gui_timeout: 超时时间（秒）
        output_dir: 执行记录输出目录（为空则使用 ubuntu_env/logs/）

    输出:
        (result, controller_vm1) — result 格式与 stage2_execute_parallel 兼容
    """
    log.info("STAGE 2 [gui_only]: 单个 GUI Agent 独立执行任务")

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

    # 仅对 string 任务改写 instruction
    if task_config.get("answer_type") == "string":
        task_instruction = rewrite_webmall_string_instruction(task_instruction)

    log.info("任务描述: %s", task_instruction[:200])

    log.info("GUI Agent: %s | 最大轮次: %d | 超时: %ds", gui_agent, max_rounds, gui_timeout)

    # 仅使用第一个 VM
    vm_ports = config.get_server_ports()
    first_port = vm_ports[0]
    controller_vm1 = PythonController(vm_ip=config.vm_ip, server_port=first_port)

    try:
        screenshot = controller_vm1.get_screenshot()
        log.info("VM1 (port %d) connected - Screenshot: %d bytes",
                 first_port, len(screenshot) if screenshot else 0)
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
    # stage2_execute_parallel 返回的 result 结构包含 execution_record 和 token_usage
    final_answer = gui_result.get("result", "")
    gui_status = gui_result.get("status", "failure")
    gui_model = gui_result.get("model_name", gui_agent)
    gui_token = gui_result.get("gui_token_usage", {})
    gui_steps = gui_result.get("steps", [])
    gui_rounds_timing = gui_result.get("rounds_timing", [])

    # 构建与 extract_execution_summary 兼容的 execution_record
    # 需要包含 plan_agent / devices / summary 结构，以便复用现有的摘要提取逻辑
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

    # 构建与 Plan Agent 兼容的 token_usage
    token_usage = {
        "plan_agent": {},  # 纯 GUI Agent 不消耗 Plan Agent token
        "gui_agent": gui_token,
        "plan_agent_model": "",
        "gui_agent_model": gui_model,
    }

    result = {
        "execution_record": execution_record,
        "token_usage": token_usage,
    }

    # 保存执行记录（与 results.json 同目录）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _record_dir = output_dir if output_dir else os.path.join(
        ubuntu_env_dir, "logs", get_host_tag())
    os.makedirs(_record_dir, exist_ok=True)
    record_path = os.path.join(
        _record_dir, f"webmall_gui_only_{task_uid}_{timestamp}.json"
    )
    try:
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump({
                "task_uid": task_uid,
                "instruction": task_instruction,
                "gui_result": gui_result,
                "elapsed_time": elapsed_time,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info("执行记录已保存: %s", record_path)
    except Exception as exc:
        log.warning("保存执行记录失败: %s", exc)

    return result, controller_vm1


# ============================================================
# 参数化版本的 Agent 执行（Plan Agent + 多 GUI Agent）
# ============================================================

def stage2_execute_parallel(
    task_config: Dict[str, Any],
    task_uid: str,
    config: ContainerSetConfig,
    log: logging.Logger,
    gui_agent: str = "seed18",
    output_dir: str = "",
) -> Tuple[Dict[str, Any], PythonController]:
    """
    参数化版本的 Stage 2：Agent 执行任务。

    输入:
        task_config: 任务配置
        task_uid: 任务 UID
        config: 容器组配置
        log: logger
        gui_agent: GUI Agent 类型
        output_dir: 执行记录输出目录（为空则使用 ubuntu_env/logs/）

    输出:
        (result, controller_vm1)
    """
    log.info("STAGE 2: Agent 执行任务")

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

    # 仅对 string 任务改写 instruction：用"收藏夹"代替"手抄URL列表"
    if task_config.get("answer_type") == "string":
        task_instruction = rewrite_webmall_string_instruction(task_instruction)

    log.info("任务描述: %s", task_instruction[:200])

    _ = gui_agent  # plan 模式下由 ABLATION_GUI_AGENT 环境变量控制

    # 使用参数化的 setup_environment
    vm_ports = config.get_server_ports()
    controller_vm1, vm_controllers, registry = setup_env_webmall_parallel(
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
        num_agents=config.num_vms,  # GUI Agent 数量与 VM 数量一致
        gui_step_budget=200,  # 全局 GUI 动作预算：所有 GUI Agent 调用累计不超过 200 步
    )

    # 支持通过环境变量 ABLATION_ORACLE_PLAN_DIR 注入 oracle plan
    oracle_context = None
    oracle_plan_dir = os.environ.get("ABLATION_ORACLE_PLAN_DIR", "")
    if oracle_plan_dir:
        task_id = task_config.get("task_id", "")
        # webmall 任务用 task_uid 作为文件名（因为 task_id 可能不唯一）
        oracle_file = os.path.join(oracle_plan_dir, f"{task_id}.txt")
        if not os.path.isfile(oracle_file):
            oracle_file = os.path.join(oracle_plan_dir, f"{task_uid}.txt")
        if os.path.isfile(oracle_file):
            with open(oracle_file, "r", encoding="utf-8") as f:
                oracle_context = f.read().strip()
            log.info("ABLATION: 已加载 Oracle Plan (%d 字符): %s", len(oracle_context), oracle_file)
        else:
            log.warning("ABLATION: Oracle Plan 文件不存在: task_id=%s, task_uid=%s", task_id, task_uid)

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
    log.info("执行完成，耗时: %.2fs", elapsed_time)

    # 保存执行记录（与 results.json 同目录）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _record_dir = output_dir if output_dir else os.path.join(
        ubuntu_env_dir, "logs", get_host_tag())
    os.makedirs(_record_dir, exist_ok=True)
    record_path = os.path.join(
        _record_dir, f"webmall_execution_{task_uid}_{timestamp}.json"
    )
    if planner.recorder:
        try:
            planner.recorder.save_to_file(record_path)
            log.info("执行记录已保存: %s", record_path)
        except Exception as exc:
            log.warning("保存执行记录失败: %s", exc)

    return result, controller_vm1


# ============================================================
# 参数化版本的评估
# ============================================================

def stage3_evaluate_parallel(
    task_config: Dict[str, Any],
    agent_result: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> Dict[str, Any]:
    """
    参数化版本的 Stage 3：结果评估。
    根据 answer_type 选择评估方式，所有端口从 config 中获取而非硬编码。

    输入:
        task_config: 任务配置
        agent_result: Stage2 的执行结果
        config: 容器组配置
        log: logger
    输出:
        评估结果字典
    """
    log.info("STAGE 3: 结果评估")

    vm_ip = config.vm_ip
    vm_ports = config.get_server_ports()
    answer_type = task_config.get("answer_type", "string")
    expected_urls = task_config.get("expected_urls", [])

    # 提取 Agent 的 final_answer
    execution_record = agent_result.get("execution_record", {})
    final_answer = execution_record.get("summary", {}).get("final_answer", "").strip()
    if not final_answer:
        log.warning("未获取到 final_answer，评估将使用空答案。")

    log.info("任务类型: %s | 期望 URL: %d 个", answer_type, len(expected_urls))
    log.info("Agent final_answer: %s", final_answer[:300] if final_answer else "(空)")

    if answer_type == "string":
        # string 类型：读取所有 VM 的收藏夹(Bookmarks)
        log.info("string 任务评测方式：读取所有 VM 的收藏夹 URL")

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

        merged_urls = list(dict.fromkeys(all_urls))
        merged_product_urls = [u for u in merged_urls if "/product/" in u]

        log.info(
            "收藏夹合并后 URL 数量: %d (product: %d)",
            len(merged_urls), len(merged_product_urls),
        )
        for i, url in enumerate(merged_product_urls, 1):
            log.info("  %d. %s", i, url)

        eval_result = evaluate_string_task(expected_urls, merged_product_urls)
        eval_result["bookmark_urls"] = merged_product_urls
        eval_result["bookmark_all_urls"] = merged_urls
        eval_result["bookmark_per_vm_urls"] = {str(k): v for k, v in per_vm_urls.items()}
        eval_result["bookmark_errors"] = {str(k): v for k, v in errors.items()}
        eval_result["detail"] = (
            f"[bookmark] {eval_result['detail']} | "
            f"bookmarked_product_urls={len(merged_product_urls)}"
        )

    elif answer_type == "cart":
        # cart 类型：基于 AT 检测各 VM 各商店购物车中的商品 slug
        log.info("cart 任务评测方式：基于 Accessibility Tree 检测购物车内容")
        checkpoints = create_checkpoints_from_urls(expected_urls)

        all_cart_results: Dict[str, Any] = {}
        for port in vm_ports:
            results = detect_vm_all_carts(vm_ip, port, vm_ip, wait_time=3.0)
            all_cart_results[f"{vm_ip}:{port}"] = results

        vm_eval_results = evaluate_all_vms(all_cart_results, checkpoints)

        matched_count = sum(1 for cp in checkpoints if cp.flag)
        total_expected = len(checkpoints)

        total_unexpected = sum(
            len(res.unexpected_products) for res in vm_eval_results.values()
        )

        passed = (
            (matched_count == total_expected and total_unexpected == 0)
            if total_expected else False
        )
        recall = matched_count / total_expected if total_expected else 0.0
        total_detected = matched_count + total_unexpected
        precision = (
            matched_count / total_detected
            if total_detected > 0
            else (1.0 if matched_count == 0 else 0.0)
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0 else 0.0
        )

        # 归一化 score 到 [0, 1]，与 pipeline_base 的 pass 判定 (score == 1.0) 兼容
        score = matched_count / total_expected if total_expected > 0 else 0.0

        eval_result = {
            "score": score,
            "matched_count": matched_count,
            "max_score": total_expected,
            "passed": passed,
            "recall": recall,
            "precision": precision,
            "f1": f1,
            "matched_urls": [cp.value for cp in checkpoints if cp.flag],
            "missing_urls": [cp.value for cp in checkpoints if not cp.flag],
            "detail": f"[cart/AT] 匹配 {matched_count}/{total_expected} 个期望商品",
            "evaluation_results": {
                vm_key: {
                    "score": res.score,
                    "total_weight": res.total_weight,
                    "matched": [cp.slug for cp in res.matched_checkpoints],
                    "unmatched": [cp.slug for cp in res.unmatched_checkpoints],
                    "unexpected": res.unexpected_products,
                }
                for vm_key, res in vm_eval_results.items()
            },
        }

    elif answer_type == "checkout":
        # checkout 类型：基于 AT 验证订单确认页
        log.info("checkout 任务评测方式：基于 Accessibility Tree 验证订单确认页")

        product_url = expected_urls[0] if expected_urls else ""
        product_slug = _urlparse(product_url).path.rstrip("/").split("/")[-1]
        user_details = task_config.get("user_details", {})
        expected_checkout = ExpectedCheckout(
            product_slug=product_slug,
            shop_port=_urlparse(product_url).port or 0,
            user_details=user_details,
        )
        log.info("  期望商品 slug: %s", product_slug)

        port_results = []
        passed_any = False
        best_score = 0.0

        for port in vm_ports:
            co_result = extract_checkout_info_with_recovery(vm_ip, port)
            if co_result.error and not co_result.is_checkout_page:
                port_results.append({"port": port, "passed": False, "error": co_result.error})
                continue

            co_result = verify_checkout(co_result, expected_checkout)

            checks = co_result.checks or {}
            port_score = sum(checks.values()) / len(checks) if checks else 0.0
            port_passed = bool(checks) and all(checks.values())

            port_results.append({
                "port": port,
                "passed": port_passed,
                "score": port_score,
                "checks": checks,
                "page_url": co_result.page_url,
                "order_number": co_result.order_number,
                "billing_info": co_result.billing_info,
                "product_name": co_result.product_name,
                "error": co_result.error,
                "recovery_used": co_result.recovery_used,
                "recovery_url": co_result.recovery_url,
            })

            if port_passed:
                passed_any = True
            if port_score > best_score:
                best_score = port_score

        for pr in port_results:
            port = pr["port"]
            if pr.get("error"):
                log.info("  VM:%d -- %s", port, pr["error"])
            elif pr.get("checks"):
                status_str = " ".join(
                    f"{'V' if v else 'X'}{k}" for k, v in pr["checks"].items()
                )
                log.info("  VM:%d -- score=%.1f%% %s", port, pr["score"] * 100, status_str)

        eval_result = {
            "score": 1 if passed_any else 0,
            "max_score": 1,
            "passed": passed_any,
            "recall": best_score,
            "precision": best_score,
            "f1": best_score,
            "matched_urls": expected_urls if passed_any else [],
            "missing_urls": [] if passed_any else expected_urls,
            "detail": f"[checkout/AT] {'通过' if passed_any else '未通过'} (best_score={best_score:.1%})",
            "port_results": port_results,
        }

    else:
        eval_result = {
            "score": 0,
            "max_score": 0,
            "detail": f"未知的 answer_type: {answer_type}",
        }

    log.info(
        "评估结果: 得分=%s/%s | 召回率=%.1f%% | 精确率=%.1f%% | F1=%.2f | %s",
        eval_result.get("score", 0), eval_result.get("max_score", 0),
        eval_result.get("recall", 0) * 100,
        eval_result.get("precision", 0) * 100,
        eval_result.get("f1", 0),
        eval_result.get("detail", ""),
    )

    return eval_result


# ============================================================
# 单任务完整流程（Worker 线程主函数）
# ============================================================

def run_single_webmall_task(
    task_uid: str,
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
    单个 WebMall 任务的完整执行流程（在 Worker 线程中运行）。

    流程:
        0. available_groups.get() — 获取可用 group_id
        1. memory_guard.acquire() — 申请内存额度
        2. allocate_ports_for_group() — 动态分配端口
        3. reinitialize_vms_parallel() — 重建容器 + 初始化 VM + 禁用屏保 + 打开浏览器
        4. clear_bookmarks（仅 string 类型）
        5. stage2_execute_parallel() — Agent 执行
        6. stage3_evaluate_parallel() — 评估
        7. 清理容器 + 释放资源 + 归还 group_id

    输入:
        task_uid: 任务 UID
        task_path: 任务 JSON 路径
        task_config: 任务配置字典
        available_groups: 可用 group_id 队列（线程安全）
        args: 命令行参数
        memory_guard: 内存管理器

    输出:
        task_result 字典
    """
    # 0. 从队列获取可用 group_id
    group_id = available_groups.get()

    # 构建容器组配置
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

    log = get_task_logger(group_id, task_uid)
    log.info(
        "获得组 %d，开始执行任务 %s [%s] %s",
        group_id, task_uid[:8],
        task_config.get("answer_type", "?"),
        task_config.get("task_tag", ""),
    )

    instruction_raw = task_config.get("instruction", "")
    instruction = instruction_raw
    if task_config.get("answer_type") == "string":
        instruction = rewrite_webmall_string_instruction(instruction)
    expected_answer = task_config.get("answer", "")

    task_result: Dict[str, Any] = {
        "task_uid": task_uid,
        "task_tag": task_config.get("task_tag", ""),
        "answer_type": task_config.get("answer_type", ""),
        "instruction": instruction,
        "instruction_raw": instruction_raw,
        "expected_answer": expected_answer,
        "expected_urls": task_config.get("expected_urls", []),
        "model_output_answer": "",
        "plan_agent_model": "",
        "gui_agent_model": "",
        "plan_agent_total_rounds": 0,
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
        # 2. 动态分配端口（含远程端口扫描，自动避开已占用端口）
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

        # 注册到全局追踪
        with _active_groups_lock:
            _active_groups[group_id] = config

        # 注册端口到心跳服务
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

        # 3. 重建容器 + 初始化 VM
        if not reinitialize_vms_parallel(config, log, mode=args.reset_mode):
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "reinitialize_vms_failed"
            log.error("环境重置失败，跳过当前任务")
            return task_result

        # 4. 对 string 类型任务：清空收藏夹
        if task_config.get("answer_type") == "string":
            task_result["bookmark_reset"] = clear_bookmarks_parallel(
                config.vm_ip, vm_ports, log
            )

        # 5. Stage 2: Agent 执行
        try:
            agent_mode = getattr(args, "agent_mode", "plan")
            if agent_mode == "gui_only":
                result, _ = stage2_execute_gui_only(
                    task_config, task_uid, config, log,
                    gui_agent=args.gui_agent,
                    max_rounds=getattr(args, "gui_max_rounds", 200),
                    gui_timeout=getattr(args, "gui_timeout", 3600),
                    output_dir=output_dir,
                )
            else:
                result, _ = stage2_execute_parallel(
                    task_config, task_uid, config, log,
                    gui_agent=args.gui_agent,
                    output_dir=output_dir,
                )
        except Exception as exc:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = f"stage2_execute_exception: {exc}"
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
            summary_info = extract_execution_summary(execution_record)
            task_result.update(summary_info)
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
                    output_results[task_uid] = dict(task_result)
                    with open(output_json_path, "w", encoding="utf-8") as _f:
                        json.dump(output_results, _f, ensure_ascii=False, indent=2)
                log.info("Stage 2 结果已中间保存: %s", task_uid[:8])
            except Exception as _save_exc:
                log.warning("[中间保存] 写入失败: %s", _save_exc)

        # 6. Stage 3: 评估
        try:
            eval_result = stage3_evaluate_parallel(task_config, result, config, log)
            task_result["evaluator_output"] = eval_result
        except Exception as exc:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = f"stage3_evaluate_exception: {exc}"
            task_result["evaluator_output"] = {
                "pass": False, "score": 0.0,
                "error": f"evaluator_exception: {exc}",
            }
            log.error("评估失败: %s", exc)

        log.info("任务 %s 执行完成", task_uid[:8])
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
    log = logging.getLogger("webmall.cleanup")
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
        description="WebMall 批量任务 Pipeline — 多线程并行版本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  # 默认 3 个代表任务并行\n"
            "  python run_webmall_pipeline_parallel.py -p 3 --vm-memory 2G --memory-limit-gb 48\n\n"
            "  # 运行全部任务（正式评测）\n"
            "  python run_webmall_pipeline_parallel.py --all -p 3 --memory-limit-gb 48\n\n"
            "  # 串行模式\n"
            "  python run_webmall_pipeline_parallel.py -p 1\n\n"
            "  # 指定任务子集\n"
            "  python run_webmall_pipeline_parallel.py --task-uids uid1,uid2,uid3\n"
        ),
    )
    parser.add_argument(
        "-p", "--max-parallel-tasks",
        type=int, default=3,
        help="最大并发任务数（默认 3，对应 3 个 WebMall 任务）",
    )
    parser.add_argument(
        "-n", "--vms-per-task",
        type=int, default=5,
        help="每个任务启动的 VM 数量（默认 5，可设为 1-5）",
    )
    parser.add_argument(
        "--vm-memory",
        type=str, default="2G",
        help='每个 QEMU VM 内存（默认 "2G"，传入 Docker RAM_SIZE）',
    )
    parser.add_argument(
        "--vm-cpu-cores",
        type=str, default="1",
        help='每个 VM CPU 核数（默认 "1"，传入 Docker CPU_CORES）',
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
        help="任务间环境重置策略（默认 rebuild）",
    )
    parser.add_argument(
        "--gui-agent",
        type=str, default="seed18",
        help="GUI Agent 类型（默认 seed18）",
    )
    parser.add_argument(
        "--agent-mode",
        type=str, default="plan",
        choices=["plan", "gui_only"],
        help="Agent 模式：plan（Plan Agent + 多 GUI Agent）或 gui_only（单个 GUI Agent 独立执行）",
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
        "--task-uids",
        type=str, default="",
        help="显式指定任务 UID 列表（逗号分隔）；为空则使用默认任务",
    )
    parser.add_argument(
        "--task-list-file",
        type=str, default="",
        help="从文件读取任务 UID 列表（每行一个 UID，忽略空行和 # 开头的注释行）",
    )
    parser.add_argument(
        "--all",
        action="store_true", default=False,
        help="加载任务目录下的全部任务（忽略 --task-uids 和 DEFAULT_TASK_UIDS）",
    )
    parser.add_argument(
        "--output-json-path",
        type=str, default="",
        help="自定义输出 JSON 路径（默认使用 logs/webmall_formal_<timestamp>/results.json）",
    )
    return parser.parse_args()


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    """
    主流程：多线程并行 WebMall 任务调度器。

    1. 解析参数 + 环境检查
    2. 检查 WebMall 商店可达性
    3. 加载任务
    4. 创建 MemoryGuard + group_id 池 + 心跳线程
    5. ThreadPoolExecutor 并行提交任务
    6. 收集结果并写入 JSON
    """
    args = parse_args()
    setup_logging(args.max_parallel_tasks)
    log = logging.getLogger("webmall.main")

    # ------ ablation 框架环境变量覆盖 ------
    _ablation_agent_mode = os.environ.get("ABLATION_AGENT_MODE", "")
    _ablation_gui_agent = os.environ.get("ABLATION_GUI_AGENT", "")
    if _ablation_agent_mode:
        args.agent_mode = _ablation_agent_mode
        log.info("[ablation] 环境变量覆盖 agent_mode=%s", _ablation_agent_mode)
    if _ablation_gui_agent:
        args.gui_agent = _ablation_gui_agent
        log.info("[ablation] 环境变量覆盖 gui_agent=%s", _ablation_gui_agent)

    # conda 环境检查
    required_env = os.environ.get("REQUIRED_CONDA_ENV", "parallelbenchmark")
    strict_check = os.environ.get("REQUIRED_CONDA_ENV_STRICT", "1") == "1"
    ensure_conda_env(required_env, strict=strict_check)

    agent_mode = getattr(args, "agent_mode", "plan")
    log.info("=" * 80)
    log.info("WebMall 批量任务 Pipeline — 多线程并行版本")
    log.info(
        "  Agent 模式: %s | 并发数: %d | VM/任务: %d | VM 内存: %s | CPU: %s | 内存上限: %.1f GiB",
        agent_mode, args.max_parallel_tasks, args.vms_per_task,
        args.vm_memory, args.vm_cpu_cores, args.memory_limit_gb,
    )
    if agent_mode == "gui_only":
        # gui_only 模式：每个 agent 绑定一台 VM，强制 -n 1
        if args.vms_per_task >= 2:
            log.warning(
                "  [gui_only] --vms-per-task=%d >= 2，gui_only 模式每个 agent 仅绑定 1 台 VM，"
                "已自动覆盖为 -n 1",
                args.vms_per_task,
            )
            args.vms_per_task = 1
        log.info(
            "  [gui_only] GUI 最大轮次: %d | 超时: %ds | VM/任务: %d",
            args.gui_max_rounds, args.gui_timeout, args.vms_per_task,
        )
    log.info("=" * 80)

    # 检查商店可达性（仅一次，所有任务共享 4 个商店实例）
    if not check_webmall_shops():
        log.error("部分 WebMall 商店不可达，请检查服务状态后再运行")
        sys.exit(1)

    # 加载任务
    # 优先级：--all > --task-list-file > --task-uids > DEFAULT_TASK_UIDS
    task_uids: Optional[List[str]] = None
    if getattr(args, "all", False):
        task_uids = None  # scan_webmall_tasks(task_uids=None) 加载全部
        log.info("已启用 --all 模式，将加载任务目录下的全部任务")
    elif args.task_list_file:
        fpath = args.task_list_file
        if not os.path.isabs(fpath):
            fpath = os.path.join(current_dir, fpath)
        with open(fpath, "r", encoding="utf-8") as f:
            task_uids = [
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
        log.info("从文件 %s 加载了 %d 个任务 UID", fpath, len(task_uids))
    elif args.task_uids:
        task_uids = [uid.strip() for uid in args.task_uids.split(",") if uid.strip()]
    if task_uids is None and not getattr(args, "all", False):
        task_uids = DEFAULT_TASK_UIDS

    task_items = scan_webmall_tasks(WEBMALL_TASKS_DIR, task_uids=task_uids)
    log.info("共加载 %d 个 WebMall 任务:", len(task_items))
    for i, (uid, path, cfg) in enumerate(task_items, 1):
        log.info(
            "  %d. [%s] %s | %s",
            i, cfg.get("answer_type", "?"), cfg.get("task_tag", ""), uid,
        )

    if not task_items:
        log.warning("未找到 WebMall 任务，退出")
        return

    # 创建内存管理器
    memory_guard = MemoryGuard(args.memory_limit_gb, args.vm_memory)

    # 创建 group_id 池（线程安全队列）
    available_groups: queue.Queue = queue.Queue()
    for g in range(args.max_parallel_tasks):
        available_groups.put(g)
    log.info("已初始化 %d 个容器组槽位", args.max_parallel_tasks)

    # 启动全局防黑屏心跳守护线程
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
        thread_name_prefix="WebMall",
    ) as executor:
        futures = {}

        for i, (task_uid, task_path, task_config) in enumerate(task_items):
            log.info(
                "提交任务 %d/%d | UID: %s | [%s] %s",
                i + 1, total_count, task_uid[:8],
                task_config.get("answer_type", "?"),
                task_config.get("task_tag", ""),
            )

            fut = executor.submit(
                run_single_webmall_task,
                task_uid, task_path, task_config,
                available_groups, args, memory_guard,
                os.path.dirname(output_json_path),
                output_results, results_lock, output_json_path,
            )
            futures[fut] = (task_uid, i + 1)

        # 收集结果
        for fut in as_completed(futures):
            task_uid, index = futures[fut]
            try:
                task_result = fut.result()
            except Exception as exc:
                log.error("任务 %s 异常: %s", task_uid[:8], exc)
                task_result = {
                    "task_uid": task_uid,
                    "interrupted": True,
                    "interrupt_reason": f"uncaught_exception: {exc}",
                }

            with results_lock:
                output_results[task_uid] = task_result
                completed_count += 1

            # 判定状态
            eval_out = task_result.get("evaluator_output") or {}
            if "passed" in eval_out:
                is_passed = eval_out["passed"]
            else:
                is_passed = (
                    eval_out.get("score", 0) == eval_out.get("max_score", 0)
                    and eval_out.get("max_score", 0) > 0
                )
            if task_result.get("interrupted"):
                status = "INTERRUPTED"
            elif is_passed:
                status = "PASS"
            else:
                status = "FAIL"
            log.info(
                "任务完成 %d/%d | UID: %s | [%s] | 状态: %s",
                completed_count, total_count, task_uid[:8],
                task_result.get("answer_type", "?"), status,
            )

            # 实时持久化中间结果
            try:
                with results_lock:
                    with open(output_json_path, "w", encoding="utf-8") as f:
                        json.dump(output_results, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                log.warning("写入中间结果失败: %s", exc)

    # 停止心跳
    heartbeat.stop()

    # 写入最终结果
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output_results, f, ensure_ascii=False, indent=2)

    # 汇总
    log.info("=" * 80)
    log.info("全部任务执行完成 (%d/%d)", completed_count, total_count)
    log.info("=" * 80)

    total_cost_all = 0.0
    for uid, res in output_results.items():
        eval_out = res.get("evaluator_output") or {}
        if "passed" in eval_out:
            is_passed = eval_out["passed"]
        else:
            is_passed = (
                eval_out.get("score", 0) == eval_out.get("max_score", 0)
                and eval_out.get("max_score", 0) > 0
            )
        status = "PASS" if is_passed else "FAIL"
        interrupted = " (中断)" if res.get("interrupted") else ""
        token_info = res.get("token_usage") or {}
        task_cost = token_info.get("total_cost_usd", 0.0)
        total_cost_all += task_cost
        cost_str = f" | 费用: ${task_cost:.4f}" if task_cost > 0 else ""
        log.info(
            "  %s %s [%s] %s | 得分: %s/%s%s%s",
            status, uid[:8], res.get("answer_type", "?"),
            res.get("task_tag", ""),
            eval_out.get("score", "N/A"), eval_out.get("max_score", "N/A"),
            cost_str, interrupted,
        )

    if total_cost_all > 0:
        log.info("总 Token 费用: $%.4f", total_cost_all)
    log.info("输出结果文件: %s", output_json_path)
    log.info("=" * 80)


if __name__ == "__main__":
    main()
