"""
QA 批量任务 Pipeline — 多线程并行版本。

基于 run_QA_pipeline.py，新增并行任务调度、动态端口分配、内存管理。
不修改任何原有系统代码，通过导入可复用函数 + 参数化重写实现。

用法:
    # 顺序执行（默认，行为与原版一致）
    python run_QA_pipeline_parallel.py

    # 2 个任务并行，每任务 3 个 VM，每 VM 分配 2G 内存
    python run_QA_pipeline_parallel.py -p 2 -n 3 --vm-memory 2G

    # 4 个任务并行，内存上限 40G
    python run_QA_pipeline_parallel.py -p 4 --memory-limit-gb 40
"""

from __future__ import annotations

import argparse
import atexit
import base64
import importlib.util
import json
import logging
import os


def _env_int(name: str, default: int) -> int:
    """Read a positive integer from env, falling back to default on bad input."""
    try:
        value = int(os.environ.get(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


# 本地版本的 execute_on_vm_with_ip，支持传入 vm_ip
def execute_on_vm_with_ip(vm_ip: str, vm_port: int, command: str, timeout: int = 60) -> Dict[str, Any]:
    """
    在指定 VM 上执行命令（使用指定的VM IP）。

    输入:
        vm_ip: VM 的 IP 地址
        vm_port: VM 端口
        command: 要执行的命令
        timeout: 超时时间（秒）
    输出:
        dict，包含 status/returncode/output/error 等信息
    """
    url = f"http://{vm_ip}:{vm_port}/execute"
    payload = {"command": command, "shell": True}
    try:
        connect_timeout = _env_int("ABLATION_VM_CONNECT_TIMEOUT", 5)
        response = requests.post(url, json=payload, timeout=(connect_timeout, timeout))
        result = response.json()
        if result.get("returncode", -1) != 0:
            error_msg = result.get("error", "") or result.get("message", "") or result.get("output", "")
            return {"status": "error", "error": error_msg, "returncode": result.get("returncode")}
        return result
    except requests.exceptions.RequestException as exc:
        return {"status": "error", "error": str(exc), "transport_error": True}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _is_vm_transport_error(result: Dict[str, Any]) -> bool:
    """True when /execute itself failed, not just the command inside the VM."""
    return bool(result.get("transport_error"))


def wait_for_vm_ready_with_ip(vm_ip: str, vm_port: int, max_wait: int = 30) -> bool:
    """
    检查 VM 是否就绪（可响应命令），使用指定的 VM IP。

    输入:
        vm_ip: VM 的 IP 地址
        vm_port: VM 端口
        max_wait: 最大等待秒数
    输出:
        bool
    """
    deadline = time.time() + max_wait
    probe_timeout = _env_int("ABLATION_VM_READY_PROBE_TIMEOUT", 5)
    while time.time() < deadline:
        try:
            result = execute_on_vm_with_ip(vm_ip, vm_port, "echo ready", timeout=probe_timeout)
            if result.get("status") == "success" or result.get("returncode") == 0:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _download_task_files_on_vm_with_ip(
    vm_ip: str,
    vm_port: int,
    prepare_url: str,
    timeout: int = 300,
    host_shared_dir: str = "",
) -> bool:
    """
    下载任务数据到 VM 的 /home/user/shared 目录。

    优先在 VM 内通过 wget 下载；如果 VM 下载失败且提供了 host_shared_dir，
    则回退到宿主机侧用 Python requests 下载到宿主机共享目录（sshfs 挂载后
    VM 内自动可见）。

    支持三种 URL 格式：
      1. HuggingFace tree 目录 URL（列出文件后逐个下载）
      2. HuggingFace resolve 直接文件 URL（直接下载）
      3. 外部 URL（如 arxiv.org，直接下载）

    prepare_url 可以是逗号分隔的多个 URL。

    输入:
        vm_ip: VM 的 IP 地址
        vm_port: VM 端口
        prepare_url: 任务 prepare_script_path URL（可逗号分隔多个）
        timeout: 超时时间（秒）
        host_shared_dir: 宿主机共享目录路径（可选，用于 VM 下载失败时的 fallback）
    输出:
        bool（是否下载成功）
    """
    import requests as _requests
    from urllib.parse import unquote

    def _host_download(dl_url: str, filename: str) -> bool:
        """宿主机侧 fallback 下载：用 requests 下载文件到 host_shared_dir。"""
        if not host_shared_dir:
            return False
        dest = os.path.join(host_shared_dir, filename)
        print(f"[host-fallback] 宿主机下载 {filename} ← {dl_url[:100]}...")
        try:
            resp = _requests.get(dl_url, timeout=timeout, stream=True)
            resp.raise_for_status()
            os.makedirs(os.path.dirname(dest) or host_shared_dir, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"[host-fallback] ✓ 宿主机下载成功: {filename} ({os.path.getsize(dest)} bytes)")
            return True
        except Exception as exc:
            print(f"[host-fallback] ✗ 宿主机下载也失败: {filename} — {exc}")
            if os.path.exists(dest):
                os.remove(dest)
            return False

    # 清理 shared 目录
    print("清理 shared 目录，避免残留文件干扰当前任务...")
    clean_cmd = "bash -c \"find /home/user/shared -mindepth 1 -maxdepth 1 -exec rm -rf {} +\""
    result = execute_on_vm_with_ip(vm_ip, vm_port, clean_cmd, timeout=30)
    if result.get("status") != "success":
        print("✗ 清理 shared 目录失败")
        print(result.get("error", "Unknown error"))
        return False
    # 同时清理宿主机共享目录（避免 sshfs 缓存不同步）
    if host_shared_dir and os.path.isdir(host_shared_dir):
        import glob as _glob
        for item in _glob.glob(os.path.join(host_shared_dir, "*")):
            try:
                if os.path.isfile(item):
                    os.remove(item)
                elif os.path.isdir(item):
                    import shutil
                    shutil.rmtree(item)
            except Exception:
                pass

    # 拆分逗号分隔的多 URL
    urls = [u.strip() for u in prepare_url.split(",") if u.strip()]
    downloaded = 0

    for url in urls:
        # 尝试 HuggingFace tree 目录格式（原有逻辑）
        try:
            repo_id, revision, subdir = parse_prepare_script_path(url)
            print(f"[HF tree] repo_id={repo_id}, revision={revision}, subdir={subdir}")
            file_paths = _list_hf_files(repo_id, revision, subdir)
            for rel_path in file_paths:
                dl_url = f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{rel_path}"
                dest_path = f"/home/user/shared/{rel_path}"
                dest_dir = os.path.dirname(dest_path)
                cmd = (
                    "bash -c "
                    f"\"mkdir -p '{dest_dir}' && wget -q -O '{dest_path}' '{dl_url}'\""
                )
                r = execute_on_vm_with_ip(vm_ip, vm_port, cmd, timeout=timeout)
                if r.get("status") != "success":
                    print(f"✗ VM 下载失败: {rel_path}，尝试宿主机 fallback...")
                    if not _host_download(dl_url, rel_path):
                        print(r.get("error", "Unknown error"))
                        return False
                downloaded += 1
            continue  # 成功处理，继续下一个 URL
        except ValueError:
            pass  # 不是 HF tree 格式，尝试直接下载
        except Exception as exc:
            # HF tree 格式解析成功但 API 调用失败（如 404），也尝试直接下载
            print(f"[HF tree] API 调用失败: {exc}，尝试直接下载...")

        # 直接下载模式：适用于 HF resolve URL 和外部 URL
        filename = unquote(url.rstrip("/").split("/")[-1])
        dest_path = f"/home/user/shared/{filename}"
        print(f"[direct] 直接下载 {filename} ← {url[:100]}...")
        cmd = (
            "bash -c "
            f"\"mkdir -p /home/user/shared && wget -q -O '{dest_path}' '{url}'\""
        )
        r = execute_on_vm_with_ip(vm_ip, vm_port, cmd, timeout=timeout)
        if r.get("status") != "success":
            print(f"✗ VM 直接下载失败: {filename}，尝试宿主机 fallback...")
            if _host_download(url, filename):
                downloaded += 1
            else:
                # 单个文件下载失败不中断，继续处理其他 URL
                pass
            continue
        downloaded += 1

    if downloaded == 0:
        print("✗ 所有文件下载失败")
        return False

    print(f"✓ 下载完成，总计: {downloaded} 个文件")
    return True


# ============================================================
# SSH 凭据：从 configs/deploy.yaml + 环境变量读取（开源版不写明文）
# ============================================================

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]


def get_ssh_credentials(vm_ip: str) -> Dict[str, Any]:
    """
    根据 vm_ip 返回 SSH 凭据。开源版不再在代码内保存任何机器密码/账号。
    凭据加载优先级：CLI/环境变量 > deploy.yaml > 默认。

    输入:
        vm_ip: Docker 宿主机 IP（仅用于拼接 ssh_host）
    输出:
        {
          "ssh_user", "ssh_password", "ssh_host",
          "conda_activate", "ssh_opts"
        }
    必需环境变量:
        BENCH_SSH_PASSWORD  —— 实际 SSH 密码（明文，必须 export）
    可选:
        BENCH_SSH_USER      —— 覆盖 deploy.yaml 的 server.vm_user
        BENCH_CONDA_ACTIVATE —— 自定义 conda 激活命令
    """
    import sys as _sys
    _src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _src_dir not in _sys.path:
        _sys.path.insert(0, _src_dir)
    from config_loader import DeployConfig, get_ssh_password

    deploy = DeployConfig()
    vm_user = os.environ.get("BENCH_SSH_USER") or deploy.vm_user
    password = get_ssh_password()
    conda_activate = os.environ.get(
        "BENCH_CONDA_ACTIVATE",
        f"source /home/{vm_user}/miniconda3/etc/profile.d/conda.sh "
        f"&& conda activate tonggui",
    )
    if not password:
        raise RuntimeError(
            "SSH 密码未设置。请 export BENCH_SSH_PASSWORD='<your-password>'，"
            "或在 configs/deploy.yaml 的 server.ssh_password_env 指向其它环境变量。"
        )
    return {
        "ssh_user": vm_user,
        "ssh_password": password,
        "ssh_host": f"{vm_user}@{vm_ip}",
        "conda_activate": conda_activate,
        "ssh_opts": list(_SSH_OPTS),
    }


def run_ssh_command(ssh_password: str, ssh_opts: list, ssh_host: str, cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """
    使用 sshpass 执行 SSH 命令（通过环境变量传递密码，避免特殊字符问题）
    """
    ssh_env = os.environ.copy()
    ssh_env['SSHPASS'] = ssh_password
    
    return subprocess.run(
        ["sshpass", "-e", "ssh"] + ssh_opts + [ssh_host, cmd],
        capture_output=True, text=True, timeout=timeout, env=ssh_env
    )


def popen_ssh_command(ssh_password: str, ssh_opts: list, ssh_host: str, cmd: str) -> subprocess.Popen:
    """
    使用 sshpass 执行 SSH 命令（异步版本，返回 Popen 对象）
    """
    ssh_env = os.environ.copy()
    ssh_env['SSHPASS'] = ssh_password
    
    return subprocess.Popen(
        ["sshpass", "-e", "ssh"] + ssh_opts + [ssh_host, cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ssh_env
    )


import queue
import shutil
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

if parallel_benchmark_dir not in sys.path:
    sys.path.insert(0, parallel_benchmark_dir)
if ubuntu_env_dir not in sys.path:
    sys.path.insert(0, ubuntu_env_dir)

# ============================================================
# 从原始 pipeline 导入可复用函数（不修改原文件）
# ============================================================

from run_QA_pipeline import (  # noqa: E402
    ensure_conda_env,
    ensure_sshpass_available,
    ensure_container_host_ssh_proxy,
    scan_qa_tasks,
    load_task_config,
    load_evaluator,
    parse_prepare_script_path,
    wait_for_vm_ready,
    download_task_files_on_vm,
    extract_execution_summary,
    ensure_docker_image_with_sshfs,
    get_guest_shared_ssh_target,
    stage3_evaluate,
    _list_hf_files,
    _download_files_with_wget,
    _flatten_shared_dir,
    TASKS_LIST_DIR,
    DEFAULT_QA_EVALUATOR_PATH,
)

# ============================================================
# 从 Docker 并行管理器导入
# ============================================================

from desktop_env.providers.docker.parallel_manager import (  # noqa: E402
    ContainerSetConfig,
    MemoryGuard,
    allocate_ports_for_group,
    build_container_set_config,
    is_protected_container,
    get_group_container_pattern,
    scan_remote_docker_ports,
    PROTECTED_CONTAINER_PREFIXES,
)

# ============================================================
# Agent 相关组件导入（不修改原模块）
# ============================================================

from desktop_env.controllers.python import PythonController  # noqa: E402
from parallel_agents.plan_agent_thought_action import PlanAgentThoughtAction, calculate_cost  # noqa: E402
from parallel_agents_as_tools.agent_tool_registry import AgentToolRegistry  # noqa: E402
from config.api_config import get_api_config, get_model_name  # noqa: E402
from parallel_agents_as_tools.seed18_gui_agent_as_tool import Seed18GUIAgentTool  # noqa: E402
from parallel_agents_as_tools.claude_gui_agent_as_tool import ClaudeGUIAgentTool  # noqa: E402
from parallel_agents_as_tools.kimi_gui_agent_as_tool import KimiGUIAgentTool  # noqa: E402

# ============================================================
# 常量
# ============================================================

OUTPUT_JSON_PATH = os.path.join(ubuntu_env_dir, "logs", "run_qa_pipeline_parallel.json")

# 全局追踪：记录所有已启动的容器组（用于 atexit 清理）
_active_groups: Dict[int, ContainerSetConfig] = {}
_active_groups_lock = threading.Lock()

# 活跃端口注册表（用于 GlobalScreensaverHeartbeat 动态端口列表）
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

    与固定端口心跳的区别：
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

        log = logging.getLogger("pipeline.heartbeat")

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
        logging.getLogger("pipeline.heartbeat").info(
            "GlobalScreensaverHeartbeat 已启动（间隔 %ds）", self.interval_sec
        )

    def stop(self) -> None:
        """停止心跳守护线程。"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logging.getLogger("pipeline.heartbeat").info("GlobalScreensaverHeartbeat 已停止")


def disable_screensaver_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> None:
    """
    在指定端口的所有 VM 中禁用屏保和锁屏（防黑屏第一层：预防）。
    通过 gsettings + xset 永久禁用屏保和 DPMS。

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
    logger_name = f"pipeline.G{group_id}.{uid_short}"
    return logging.getLogger(logger_name)


# ============================================================
# 参数化版本的容器管理函数
# ============================================================

def rebuild_containers_parallel(
    config: ContainerSetConfig,
    log: logging.Logger,
) -> bool:
    """
    参数化版本的容器重建（替代原始 rebuild_containers 的硬编码版本）。
    容器名称和端口来自 config.containers（已通过 allocate_ports_for_group 动态分配）。
    docker run 命令传入 -e RAM_SIZE / -e CPU_CORES 控制 QEMU 资源。
    清理阶段仅删除当前组的容器，不触碰 WebMall/OnlyOffice。

    输入:
        config: 容器组配置
        log: 任务专用 logger

    输出:
        bool（是否重建成功）
    """
    log.info("STAGE 1-0: 自动重建 Docker 容器 (组 %d, %d 个 VM)",
             config.group_id, config.num_vms)

    if not ensure_sshpass_available():
        return False

    base_image = "happysixd/osworld-docker"
    creds = get_ssh_credentials(config.vm_ip)
    ssh_password = creds["ssh_password"]
    ssh_host = creds["ssh_host"]
    ssh_opts = creds["ssh_opts"]
    conda_activate = creds["conda_activate"]

    try:
        # [0/3] 确保镜像存在
        if not ensure_docker_image_with_sshfs(
            ssh_password=ssh_password,
            ssh_opts=ssh_opts,
            conda_activate=conda_activate,
            base_image=base_image,
            target_image=config.docker_image,
            ssh_host=ssh_host,
        ):
            return False

        # [1/3] 仅删除当前组的同名容器
        log.info("[1/3] 查找并删除当前组的容器...")
        group_prefix = get_group_container_pattern(config.group_id)
        required_names = [c["name"] for c in config.containers]

        cmd = (
            f"{conda_activate} && echo '{ssh_password}' | sudo -S "
            "docker ps -a --format '{{.Names}}'"
        )
        result = run_ssh_command(ssh_password, ssh_opts, ssh_host, cmd, timeout=10)

        containers_to_delete = []
        for line in result.stdout.strip().split("\n"):
            name = line.strip()
            if not name:
                continue
            # 安全检查：保护容器绝不删除
            if is_protected_container(name):
                continue
            # 仅删除当前组的容器或同名容器
            if name in required_names or name.startswith(group_prefix):
                containers_to_delete.append(name)

        if containers_to_delete:
            log.info("  准备删除 %d 个容器: %s",
                     len(containers_to_delete), ", ".join(containers_to_delete))
            names_str = " ".join(containers_to_delete)
            cmd = (
                f"{conda_activate} && echo '{ssh_password}' | sudo -S "
                f"docker rm -f {names_str} 2>&1"
            )
            run_ssh_command(ssh_password, ssh_opts, ssh_host, cmd, timeout=60)
            log.info("  删除完成")
        else:
            log.info("  未发现需要删除的容器")

        # [2/3] 启动新容器
        # 注：端口冲突已在 allocate_ports_for_group() 阶段通过
        #      scan_remote_docker_ports() 扫描远程端口自动避开
        log.info("[2/3] 启动新容器...")
        for c in config.containers:
            cmd = (
                f"{conda_activate} && echo '{ssh_password}' | sudo -S docker run -d "
                f"--name {c['name']} "
                f"-p {c['server_port']}:5000 "
                f"-p {c['vnc_port']}:8006 "
                f"-p {c['chromium_port']}:9222 "
                f"-p {c['vlc_port']}:8080 "
                f"-e RAM_SIZE={config.vm_memory} "
                f"-e CPU_CORES={config.vm_cpu_cores} "
                f"--shm-size=2g --cap-add=NET_ADMIN --device=/dev/kvm "
                f"-v {config.qcow2_path}:/System.qcow2:ro "
                f"-v {config.shared_host_dir}:/shared "
                f"{config.docker_image}"
            )
            result = run_ssh_command(ssh_password, ssh_opts, ssh_host, cmd, timeout=60)
            if result.returncode == 0:
                log.info("  %s 启动成功 (s:%s v:%s)",
                         c["name"], c["server_port"], c["vnc_port"])
                if ensure_container_host_ssh_proxy(
                    ssh_password=ssh_password,
                    ssh_opts=ssh_opts,
                    ssh_host=ssh_host,
                    conda_activate=conda_activate,
                    container_name=c["name"],
                ):
                    log.info("  %s - host SSH proxy 已就绪 (guest -> host.lan:22)", c["name"])
                else:
                    log.error("  %s - host SSH proxy 启动失败", c["name"])
                    return False
            else:
                log.error("  %s 启动失败: %s", c["name"], result.stderr[:300])
                return False

        qemu_boot_wait = _env_int("ABLATION_QEMU_BOOT_WAIT", 120)
        log.info("等待容器内 QEMU VM 启动（%d 秒）...", qemu_boot_wait)
        time.sleep(qemu_boot_wait)

        # [3/3] 检查 sshfs
        log.info("[3/3] 并行检查 sshfs 是否已内置...")
        processes = []
        for c in config.containers:
            cmd = (
                f"{conda_activate} && echo '{ssh_password}' | sudo -S "
                f"docker exec {c['name']} bash -c 'which sshfs'"
            )
            proc = popen_ssh_command(ssh_password, ssh_opts, ssh_host, cmd)
            processes.append((c["name"], proc))

        all_success = True
        for name, proc in processes:
            stdout, stderr = proc.communicate(timeout=180)
            if proc.returncode == 0 and "/sshfs" in stdout:
                log.info("  %s - sshfs 已安装", name)
            else:
                log.error("  %s - sshfs 检查失败: %s", name, stderr[:200])
                all_success = False

        return all_success

    except FileNotFoundError:
        log.error("sshpass 未安装")
        return False
    except subprocess.TimeoutExpired:
        log.error("SSH 连接超时")
        return False
    except Exception as exc:
        log.error("重建容器失败: %s", exc)
        return False


def cleanup_group_containers(
    config: ContainerSetConfig,
    log: logging.Logger,
) -> None:
    """
    清理指定组的所有容器（任务完成或异常退出时调用）。

    输入:
        config: 容器组配置
        log: logger
    """
    creds = get_ssh_credentials(config.vm_ip)
    ssh_password = creds["ssh_password"]
    ssh_host = creds["ssh_host"]
    ssh_opts = creds["ssh_opts"]
    conda_activate = creds["conda_activate"]

    names = [str(c["name"]) for c in config.containers]
    if not names:
        return

    # 再次验证：不删除保护容器
    safe_names = [n for n in names if not is_protected_container(n)]
    if not safe_names:
        return

    names_str = " ".join(safe_names)
    cmd = (
        f"{conda_activate} && echo '{ssh_password}' | sudo -S "
        f"docker rm -f {names_str} 2>&1"
    )
    try:
        run_ssh_command(ssh_password, ssh_opts, ssh_host, cmd, timeout=60)
        log.info("已清理组 %d 的 %d 个容器", config.group_id, len(safe_names))
    except Exception as exc:
        log.warning("清理组 %d 容器失败: %s", config.group_id, exc)


def init_vm_parallel(
    vm_port: int,
    vnc_port: int,
    prepare_url: str,
    shared_host_dir: str,
    vm_ip: str,
    is_first_vm: bool,
    rebuilt: bool,
    log: logging.Logger,
) -> bool:
    """
    参数化版本的 VM 初始化（替代原始 init_vm 的硬编码版本）。
    sshfs 挂载路径和 VM IP 从参数传入而非硬编码。

    输入:
        vm_port: VM server 端口
        vnc_port: VNC 端口
        prepare_url: 任务数据 URL
        shared_host_dir: 宿主机共享目录路径（该组专用）
        vm_ip: 宿主机 IP
        is_first_vm: 是否为该组的第一个 VM（仅第一个 VM 下载数据）
        rebuilt: 是否为重建后首次初始化
        log: logger

    输出:
        bool
    """
    log.info("初始化 VM (port %d, VNC http://%s:%d/)", vm_port, vm_ip, vnc_port)

    wait_time = (
        _env_int("ABLATION_VM_READY_WAIT_REBUILT", 240)
        if rebuilt
        else _env_int("ABLATION_VM_READY_WAIT", 30)
    )
    if not wait_for_vm_ready_with_ip(vm_ip, vm_port, max_wait=wait_time):
        log.error("VM %d 无法响应", vm_port)
        return False

    # [1/5] 检查并安装 sshfs
    log.info("[1/5] 检查 sshfs...")
    vm_command_timeout = _env_int("ABLATION_VM_COMMAND_TIMEOUT", 60)
    vm_apt_timeout = _env_int("ABLATION_VM_APT_TIMEOUT", 130)
    result = execute_on_vm_with_ip(vm_ip, vm_port, "which sshfs", timeout=30)
    if result.get("status") != "success":
        if _is_vm_transport_error(result):
            log.error(
                "VM %d PythonController /execute 无响应，无法检查 sshfs: %s "
                "(VNC: http://%s:%d/)",
                vm_port,
                result.get("error", "Unknown"),
                vm_ip,
                vnc_port,
            )
            return False
        # 清理 apt-daily/unattended-upgrades 自动进程释放 lock（但不重建源、不 apt update）。
        # 镜像 cache 已包含 sshfs 候选版本，直接 install 通常 <5s 完成。
        _ = execute_on_vm_with_ip(
            vm_ip, vm_port,
            'bash -c "echo password | sudo -S systemctl mask --now packagekit || true; '
            'echo password | sudo -S systemctl stop apt-daily.service apt-daily-upgrade.service '
            'apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service || true; '
            'echo password | sudo -S fuser -k /var/lib/apt/lists/lock '
            '/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock 2>/dev/null || true"',
            timeout=30,
        )
        result = execute_on_vm_with_ip(
            vm_ip, vm_port,
            'bash -c "echo password | sudo -S DEBIAN_FRONTEND=noninteractive '
            'apt-get install -y -qq sshfs"',
            timeout=vm_apt_timeout,
        )
        if result.get("status") != "success":
            if _is_vm_transport_error(result):
                log.error(
                    "VM %d PythonController 在安装 sshfs 期间无响应: %s "
                    "(VNC: http://%s:%d/)",
                    vm_port,
                    result.get("error", "Unknown"),
                    vm_ip,
                    vnc_port,
                )
                return False
            log.error("安装 sshfs 失败: %s", result.get("error", "Unknown"))
            return False
        result = execute_on_vm_with_ip(vm_ip, vm_port, "which sshfs", timeout=30)
        if result.get("status") != "success":
            if _is_vm_transport_error(result):
                log.error(
                    "VM %d PythonController 在 sshfs 验证期间无响应: %s "
                    "(VNC: http://%s:%d/)",
                    vm_port,
                    result.get("error", "Unknown"),
                    vm_ip,
                    vnc_port,
                )
                return False
            log.error("sshfs 验证失败")
            return False
        log.info("  sshfs 安装完成")
    else:
        log.info("  sshfs 已安装")

    # [2/5] 准备 shared 目录
    log.info("[2/5] 准备 shared 目录...")
    cmd = (
        'bash -c "echo password | sudo -S fusermount3 -u /home/user/shared 2>/dev/null; '
        'mkdir -p /home/user/shared"'
    )
    result = execute_on_vm_with_ip(vm_ip, vm_port, cmd)
    if result.get("status") != "success":
        if _is_vm_transport_error(result):
            log.error(
                "VM %d PythonController 在准备 shared 目录期间无响应: %s "
                "(VNC: http://%s:%d/)",
                vm_port,
                result.get("error", "Unknown"),
                vm_ip,
                vnc_port,
            )
            return False
        log.error("准备 shared 目录失败: %s", result.get("error", "Unknown"))
        return False

    # [3/5] 挂载 shared（使用参数化路径）
    log.info("[3/5] 挂载 shared (%s)...", shared_host_dir)
    _creds = get_ssh_credentials(vm_ip)
    _guest_ssh_target = get_guest_shared_ssh_target(_creds["ssh_user"])
    # 用 base64 编码密码避免 shell 特殊字符（如反引号）被解释为命令替换
    _pw_b64 = base64.b64encode(_creds['ssh_password'].encode()).decode()
    cmd = (
        f"bash -c \"echo {_pw_b64} | base64 -d | sshfs {_guest_ssh_target}:{shared_host_dir} "
        "/home/user/shared -o password_stdin -o StrictHostKeyChecking=no\""
    )
    result = execute_on_vm_with_ip(vm_ip, vm_port, cmd, timeout=vm_command_timeout)
    if result.get("status") != "success":
        if _is_vm_transport_error(result):
            log.error(
                "VM %d PythonController 在挂载 shared 期间无响应: %s "
                "(VNC: http://%s:%d/)",
                vm_port,
                result.get("error", "Unknown"),
                vm_ip,
                vnc_port,
            )
            return False
        log.error("挂载 shared 失败: %s", result.get("error", "Unknown"))
        return False

    # [4/5] 验证挂载
    log.info("[4/5] 验证 shared 挂载...")
    result = execute_on_vm_with_ip(vm_ip, vm_port, "ls /home/user/shared", timeout=vm_command_timeout)
    if result.get("status") != "success":
        if _is_vm_transport_error(result):
            log.error(
                "VM %d PythonController 在验证 shared 挂载期间无响应: %s "
                "(VNC: http://%s:%d/)",
                vm_port,
                result.get("error", "Unknown"),
                vm_ip,
                vnc_port,
            )
            return False
        log.error("shared 挂载验证失败: %s", result.get("error", "Unknown"))
        return False

    # [5/5] 仅第一个 VM 下载任务数据
    if is_first_vm:
        if prepare_url:
            log.info("[5/5] 下载任务数据到 shared...")
            # 使用本地版本的下载函数（带 vm_ip）
            if not _download_task_files_on_vm_with_ip(vm_ip, vm_port, prepare_url):
                return False
        else:
            log.info("[5/5] 任务未提供 prepare_script_path，跳过下载")
    else:
        log.info("[5/5] 跳过下载（使用 shared 中的文件）")

    # [6/6] 启动 Chrome 并打开 Bing，最大化窗口
    log.info("[6/6] 启动 Chrome 并打开 Bing...")
    launch_script = (
        "import subprocess, time, os\n"
        "env = os.environ.copy()\n"
        "env['DISPLAY'] = ':0'\n"
        "subprocess.Popen(\n"
        "    ['google-chrome', '--no-first-run', '--no-default-browser-check', 'https://www.bing.com'],\n"
        "    env=env,\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        "    start_new_session=True,\n"
        ")\n"
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
    try:
        url = f"http://{vm_ip}:{vm_port}/execute"
        response = requests.post(
            url,
            json={"command": ["python", "-c", launch_script], "shell": False},
            timeout=20,
        )
        result = response.json()
        if result.get("returncode", 0) == 0 and result.get("status") == "success":
            log.info("  Chrome 已启动并最大化")
        else:
            log.warning("  Chrome 启动命令失败: %s", result.get("error") or result.get("message"))
    except Exception as exc:
        log.warning("  Chrome 启动命令异常: %s", exc)

    log.info("VM %d 初始化成功", vm_port)
    return True


def stage1_initialize_parallel(
    task_config: Dict[str, Any],
    config: ContainerSetConfig,
    log: logging.Logger,
) -> bool:
    """
    参数化版本的 Stage 1：环境初始化。

    输入:
        task_config: 任务配置
        config: 容器组配置
        log: logger

    输出:
        bool
    """
    log.info("STAGE 1: 环境初始化 (组 %d)", config.group_id)

    prepare_url = task_config.get("prepare_script_path", "")
    if not prepare_url:
        log.warning("任务配置缺少 prepare_script_path，将跳过下载步骤但继续执行任务")

    # 通过 SSH 在宿主机上创建共享目录
    log.info("通过 SSH 在宿主机上创建共享目录: %s", config.shared_host_dir)
    _creds = get_ssh_credentials(config.vm_ip)
    try:
        result = run_ssh_command(
            _creds["ssh_password"], _creds["ssh_opts"], _creds["ssh_host"],
            f"mkdir -p {config.shared_host_dir} && chmod 777 {config.shared_host_dir}",
            timeout=30,
        )
        if result.returncode == 0:
            log.info("共享目录创建成功")
        else:
            log.warning("共享目录创建失败: %s", result.stderr)
    except Exception as exc:
        log.warning("创建共享目录异常: %s", exc)

    max_attempts = _env_int("ABLATION_STAGE1_INIT_ATTEMPTS", 2)
    vm_pairs = config.get_vm_pairs()

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            log.warning(
                "Stage 1 第 %d/%d 次重试：清理并重建整组容器",
                attempt,
                max_attempts,
            )
            cleanup_group_containers(config, log)

        # 重建容器。QEMU guest/controller 不可达时，唯一可靠恢复动作是重建 guest。
        rebuilt = rebuild_containers_parallel(config, log)
        if not rebuilt:
            log.error("容器重建失败 (attempt %d/%d)", attempt, max_attempts)
            if attempt < max_attempts:
                continue
            return False

        # 初始化各 VM
        success_count = 0
        for idx, (vm_port, vnc_port) in enumerate(vm_pairs):
            if init_vm_parallel(
                vm_port=vm_port,
                vnc_port=vnc_port,
                prepare_url=prepare_url,
                shared_host_dir=config.shared_host_dir,
                vm_ip=config.vm_ip,
                is_first_vm=(idx == 0),
                rebuilt=True,
                log=log,
            ):
                success_count += 1
            else:
                log.warning("VM %d 初始化失败，继续下一个...", vm_port)

        if success_count == len(vm_pairs):
            break

        log.info(
            "初始化完成: %d/%d 个 VM 成功（不足，attempt %d/%d）",
            success_count,
            len(vm_pairs),
            attempt,
            max_attempts,
        )
        if attempt == max_attempts:
            return False
    else:
        return False

    # 禁用所有 VM 的屏保（防黑屏第一层：预防）
    vm_ports = config.get_server_ports()
    disable_screensaver_parallel(config.vm_ip, vm_ports, log)

    log.info("初始化完成: %d/%d 个 VM 成功", success_count, len(vm_pairs))
    return True


# ============================================================
# 参数化版本的 setup_environment（内联替代，不修改原模块）
# ============================================================

def setup_environment_parallel(
    vm_ip: str,
    vm_ports: List[int],
) -> Tuple[PythonController, List[PythonController], AgentToolRegistry]:
    """
    参数化的环境设置（替代原始 setup_environment 的硬编码版本）。
    逻辑与 run_plan_agent_thought_action.setup_environment() 完全一致，
    仅 vm_ip 和 ports 改为参数传入。不修改原始文件。

    输入:
        vm_ip: Docker 宿主机 IP
        vm_ports: 各 VM 的 server 端口列表

    输出:
        (controller_vm1, vm_controllers, registry)
    """
    vm_controllers: List[PythonController] = []

    for i, port in enumerate(vm_ports):
        try:
            controller = PythonController(vm_ip=vm_ip, server_port=port)
            screenshot = controller.get_screenshot()
            logging.getLogger("pipeline").info(
                "VM%d (port %d) connected - Screenshot: %d bytes",
                i + 1, port, len(screenshot) if screenshot else 0,
            )
            vm_controllers.append(controller)
        except Exception as e:
            logging.getLogger("pipeline").warning(
                "VM%d (port %d) connection failed: %s", i + 1, port, e,
            )
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
        logging.getLogger("pipeline").info(
            "ABLATION: GUI Agent 切换为 %s", gui_agent_override
        )

    return controller_vm1, vm_controllers, registry


def stage2_execute_agent_parallel(
    task_config: Dict[str, Any],
    task_uid: str,
    config: ContainerSetConfig,
    log: logging.Logger,
) -> Tuple[Dict[str, Any], PythonController]:
    """
    参数化版本的 Stage 2：Agent 执行任务。

    输入:
        task_config: 任务配置
        task_uid: 任务 UID
        config: 容器组配置
        log: logger

    输出:
        (result, controller_vm1)
    """
    log.info("STAGE 2: Agent 执行任务")

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

    log.info("任务描述: %s", task_instruction[:200])

    # 使用参数化的 setup_environment（不调用原始版本）
    vm_ports = config.get_server_ports()
    controller_vm1, vm_controllers, registry = setup_environment_parallel(
        vm_ip=config.vm_ip,
        vm_ports=vm_ports,
    )

    # 支持通过环境变量 ABLATION_PLAN_MODEL 覆盖 Plan Agent 模型
    plan_model = os.environ.get("ABLATION_PLAN_MODEL", "") or get_model_name("plan_agent")
    if os.environ.get("ABLATION_PLAN_MODEL"):
        log.info("ABLATION: Plan Agent 模型切换为 %s", plan_model)

    # 根据模型名称自动选择 API 配置（GPT-5.2 走 BigAI，其余走 DeerAPI）
    from config.api_config import get_api_config_for_model
    api_config = get_api_config_for_model(plan_model)
    log.info("Plan Agent API: %s", api_config["base_url"])

    # 从线程局部变量获取 per-task logger 和进度状态
    # （由 pipeline_base._run_single_task_wrapper 设置）
    _task_logger = None
    _progress_state = None
    _thread_name = ''
    try:
        # 多路径尝试导入 _thread_context（运行目录可能不同）
        import threading as _thr
        _thread_context = None
        try:
            from pipeline_v2.pipeline_base import _thread_context
        except ImportError:
            try:
                import sys as _sys
                import os as _os
                _pipeline_v2_dir = _os.path.join(
                    _os.path.dirname(_os.path.abspath(__file__)), "pipeline_v2"
                )
                if _pipeline_v2_dir not in _sys.path:
                    _sys.path.insert(0, _pipeline_v2_dir)
                from pipeline_base import _thread_context
            except ImportError:
                pass
        if _thread_context is not None:
            _task_logger = getattr(_thread_context, 'task_logger', None)
            _progress_state = getattr(_thread_context, 'progress_state', None)
            _thread_name = getattr(_thread_context, 'thread_name', '')
    except (ImportError, AttributeError):
        pass  # 向后兼容：非 pipeline_v2 调用路径无 _thread_context

    planner = PlanAgentThoughtAction(
        controller=controller_vm1,
        registry=registry,
        vm_controllers=vm_controllers,
        api_key=api_config["api_key"],
        base_url=api_config["base_url"],
        disable_code_agent=False,
        max_workers=config.num_vms,
        coordinator_model=plan_model,
        gui_step_budget=200,
        num_agents=config.num_vms,  # GUI Agent 数量与 VM 数量一致
        task_logger=_task_logger,
        progress_state=_progress_state,
        thread_name=_thread_name,
    )

    # 支持通过环境变量 ABLATION_ORACLE_PLAN_DIR 注入 oracle plan
    # 查找顺序：{task_id}.txt → {task_uid}.txt
    oracle_context = None
    oracle_plan_dir = os.environ.get("ABLATION_ORACLE_PLAN_DIR", "")
    if oracle_plan_dir:
        task_id_val = task_config.get("task_id", "")
        task_uid_val = task_config.get("task_uid", "")
        oracle_file = os.path.join(oracle_plan_dir, f"{task_id_val}.txt")
        if not os.path.isfile(oracle_file) and task_uid_val:
            oracle_file = os.path.join(oracle_plan_dir, f"{task_uid_val}.txt")
        if os.path.isfile(oracle_file):
            with open(oracle_file, "r", encoding="utf-8") as f:
                oracle_context = f.read().strip()
            log.info("ABLATION: 已加载 Oracle Plan (%d 字符): %s", len(oracle_context), oracle_file)
        else:
            log.warning("ABLATION: Oracle Plan 文件不存在 (尝试 task_id=%s, task_uid=%s)", task_id_val, task_uid_val)

    # 测试模式：Plan/GUI 各只跑 1 轮，仅验证 API 调用是否正常
    is_test_mode = os.environ.get("ABLATION_TEST_MODE") == "1"
    plan_max_rounds = 1 if is_test_mode else 10
    gui_max_rounds = 1 if is_test_mode else 25
    # 每个 GUI 子任务的超时时间（秒），默认 3600 即 1 小时，可通过环境变量覆盖
    subtask_timeout = int(os.environ.get("ABLATION_SUBTASK_TIMEOUT", "3600"))
    # 整体任务超时时间（秒），默认 7200 即 2 小时，可通过环境变量覆盖
    task_timeout = int(os.environ.get("ABLATION_TASK_TIMEOUT", "7200"))
    if is_test_mode:
        log.info("TEST MODE: plan_max_rounds=1, gui_max_rounds=1")
    log.info("超时配置: subtask_timeout=%ds, task_timeout=%ds", subtask_timeout, task_timeout)

    start_time = time.time()
    result = planner.execute_task(
        task=task_instruction,
        context=oracle_context,
        max_rounds=plan_max_rounds,
        max_rounds_per_subtask=gui_max_rounds,
        timeout_per_subtask=subtask_timeout,
        task_timeout=task_timeout,
    )
    elapsed_time = time.time() - start_time
    log.info("执行完成，耗时: %.2fs", elapsed_time)

    # 附加逐轮推理记录供 pipeline_base 写入 rounds.json
    result["rounds_record"] = planner.get_rounds_record()

    # 附加 ExecutionRecorder 详细执行记录（含 GUI Agent 各轮数据）
    # 由 pipeline_base 统一保存到 per-task 子目录中的 execution_record.json
    if planner.recorder:
        try:
            result["execution_record"] = planner.recorder.to_dict()
        except Exception as exc:
            log.warning("序列化 ExecutionRecorder 失败: %s", exc)
            result["execution_record"] = None
    else:
        result["execution_record"] = None

    return result, controller_vm1


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
    不经过 Plan Agent 任务分解，直接调用 GUI Agent（baseline 模式）。

    输入:
        task_config: 任务配置
        task_uid: 任务 UID
        config: 容器组配置（仅使用第一个 VM）
        log: logger
        gui_agent: GUI Agent 类型（seed18 / claude / kimi）
        max_rounds: 最大执行轮次
        gui_timeout: 超时时间（秒）
        output_dir: 执行记录输出目录（为空则使用 ubuntu_env/logs/）

    输出:
        (result, controller_vm1) — result 格式与 stage2_execute_agent_parallel 兼容
    """
    log.info("STAGE 2 [gui_only]: 单个 GUI Agent 独立执行任务")

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

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
    gui_model = gui_result.get("model_name", gui_agent)
    gui_token = gui_result.get("gui_token_usage", {})
    gui_steps = gui_result.get("steps", [])
    gui_rounds_timing = gui_result.get("rounds_timing", [])

    # 构建与 extract_execution_summary 兼容的 execution_record
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
    _record_dir = output_dir if output_dir else os.path.join(ubuntu_env_dir, "logs")
    os.makedirs(_record_dir, exist_ok=True)
    record_path = os.path.join(
        _record_dir, f"qa_gui_only_{task_uid}_{timestamp}.json"
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
# 单任务完整流程
# ============================================================

def run_single_task(
    task_uid: str,
    task_path: str,
    task_config: Dict[str, Any],
    available_groups: queue.Queue,
    args: argparse.Namespace,
    memory_guard: MemoryGuard,
    output_results: Dict[str, Any] = None,
    results_lock: threading.Lock = None,
    output_json_path: str = "",
) -> Dict[str, Any]:
    """
    单个任务的完整执行流程（在 Worker 线程中运行）。

    通过 available_groups 队列获取可用的 group_id，保证同一 group_id
    不会被两个线程同时使用，避免容器名称冲突。任务完成后归还 group_id。

    流程:
        0. available_groups.get() — 获取可用 group_id（阻塞直到有空闲组）
        1. memory_guard.acquire() — 申请内存额度
        2. allocate_ports_for_group() — 动态分配端口
        3. stage1_initialize_parallel() — 重建容器 + 初始化 VM
        4. stage2_execute_agent_parallel() — Agent 执行
        5. stage3_evaluate() — 评估（直接复用原函数）
        6. 清理容器 + memory_guard.release() + 归还 group_id

    输入:
        task_uid: 任务 UID
        task_path: 任务 JSON 路径
        task_config: 任务配置字典
        available_groups: 可用 group_id 队列（线程安全）
        args: 命令行参数（用于构建 ContainerSetConfig）
        memory_guard: 内存管理器

    输出:
        task_result 字典
    """
    # 0. 从队列获取可用 group_id（阻塞直到有空闲组）
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
    log.info("获得组 %d，开始执行任务 %s", group_id, task_uid[:8])

    instruction = task_config.get("instruction", "")
    expected_answer = task_config.get("answer", "")

    task_result: Dict[str, Any] = {
        "task_uid": task_uid,
        "instruction": instruction,
        "expected_answer": expected_answer,
        "model_output_answer": "",
        "plan_agent_model": "",
        "gui_agent_model": "",
        "plan_agent_total_rounds": 0,
        "evaluator_output": None,
        "plan_agent_last_round_output": "",
        "plan_agent_last_round_messages": [],
        "interrupted": False,
        "interrupt_reason": "",
        "group_id": group_id,
        "token_usage": None,
    }

    # 1. 申请内存额度
    if not memory_guard.acquire(config.num_vms):
        task_result["interrupted"] = True
        task_result["interrupt_reason"] = "memory_guard_timeout"
        log.error("内存申请超时，跳过任务")
        available_groups.put(group_id)  # 归还 group_id
        return task_result

    try:
        # 2. 动态分配端口（在 FileLock 保护下，含远程端口扫描）
        log.info("为组 %d 分配端口（扫描远程已用端口）...", group_id)
        creds = get_ssh_credentials(config.vm_ip)
        remote_ports = scan_remote_docker_ports(
            ssh_password=creds["ssh_password"],
            ssh_opts=creds["ssh_opts"],
            ssh_host=creds["ssh_host"],
            conda_activate=creds["conda_activate"],
        )
        config.containers = allocate_ports_for_group(
            config.num_vms, group_id, extra_used_ports=remote_ports,
        )

        # 注册端口到心跳服务
        vm_ports = config.get_server_ports()
        register_group_ports(group_id, vm_ports)

        # 注册到全局追踪（用于 atexit 清理）
        with _active_groups_lock:
            _active_groups[group_id] = config

        # 3. Stage 1: 环境初始化
        if not stage1_initialize_parallel(task_config, config, log):
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "stage1_initialize_failed"
            log.error("环境初始化失败，跳过当前任务")
            return task_result

        # 4. Stage 2: Agent 执行
        try:
            agent_mode = getattr(args, "agent_mode", "plan")
            if agent_mode == "gui_only":
                result, _ = stage2_execute_gui_only(
                    task_config, task_uid, config, log,
                    gui_agent=getattr(args, "gui_agent", "seed18"),
                    max_rounds=getattr(args, "gui_max_rounds", 200),
                    gui_timeout=getattr(args, "gui_timeout", 3600),
                    output_dir=os.environ.get("ABLATION_RECORD_DIR", ""),
                )
            else:
                result, _ = stage2_execute_agent_parallel(task_config, task_uid, config, log)
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
        execution_record = result.get("execution_record", {}) if isinstance(result, dict) else {}
        if execution_record:
            summary_info = extract_execution_summary(execution_record)
            task_result.update(summary_info)
        else:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "missing_execution_record"

        # 提取 token 消耗并计算费用
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

        # 5. Stage 3: 评估（直接复用原函数）
        try:
            eval_result = stage3_evaluate(task_config, result, task_path)
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
        # 6. 清理容器 + 释放内存 + 归还 group_id
        unregister_group_ports(group_id)
        cleanup_group_containers(config, log)
        memory_guard.release(config.num_vms)

        # 从全局追踪中移除
        with _active_groups_lock:
            _active_groups.pop(group_id, None)

        # 归还 group_id，让下一个等待的任务可以使用
        available_groups.put(group_id)
        log.info("组 %d 已释放", group_id)


# ============================================================
# atexit 清理：确保异常退出时也能清理容器
# ============================================================

def _atexit_cleanup() -> None:
    """程序退出时清理所有活跃的容器组。"""
    log = logging.getLogger("pipeline.cleanup")
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
        description="QA 批量任务 Pipeline — 多线程并行版本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  # 顺序执行（默认）\n"
            "  python run_QA_pipeline_parallel.py\n\n"
            "  # 2 个任务并行，每任务 3 个 VM\n"
            "  python run_QA_pipeline_parallel.py -p 2 -n 3\n\n"
            "  # 4 个任务并行，每 VM 2G 内存，总限制 40G\n"
            "  python run_QA_pipeline_parallel.py -p 4 --vm-memory 2G --memory-limit-gb 40\n\n"
            "  # 从文件读取任务子集重跑\n"
            "  python run_QA_pipeline_parallel.py -p 4 --task-list-file rerun_api_error.txt\n\n"
            "  # 指定 task_uid 重跑\n"
            "  python run_QA_pipeline_parallel.py --task-uids uid1,uid2,uid3\n"
        ),
    )
    parser.add_argument(
        "-p", "--max-parallel-tasks",
        type=int, default=1,
        help="最大并发任务数（默认 1 = 顺序执行）",
    )
    parser.add_argument(
        "-n", "--vms-per-task",
        type=int, default=5,
        help="每个任务启动的 VM 数量（默认 5，可设为 1-5）",
    )
    parser.add_argument(
        "--vm-memory",
        type=str, default="1G",
        help='每个 QEMU VM 内存（默认 "1G"，传入 Docker RAM_SIZE）',
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
        type=str, default="10.1.110.143",
        help="Docker 宿主机 IP（默认 10.1.110.143）",
    )
    parser.add_argument(
        "--shared-base-dir",
        type=str, default="/home/agentlab/shared",
        help="共享目录根路径（默认 /home/agentlab/shared）",
    )
    parser.add_argument(
        "--qcow2-path",
        type=str,
        default="/home/agentlab/code/parallel-efficient-benchmark/ubuntu_env/docker_vm_data/Ubuntu.qcow2",
        help="VM 磁盘镜像路径",
    )
    parser.add_argument(
        "--docker-image",
        type=str, default="happysixd/osworld-docker-sshfs",
        help="Docker 镜像名",
    )
    parser.add_argument(
        "--task-uids",
        type=str, default="",
        help="显式指定任务 UID 列表（逗号分隔）；为空则运行全部 QA 任务",
    )
    parser.add_argument(
        "--task-list-file",
        type=str, default="",
        help="从文件读取任务 UID 列表（每行一个 UID，忽略空行和 # 开头的注释行）",
    )
    parser.add_argument(
        "--output-json-path",
        type=str, default="",
        help="自定义输出 JSON 路径（默认 logs/run_qa_pipeline_parallel.json）",
    )
    parser.add_argument(
        "--agent-mode",
        type=str, default="plan",
        choices=["plan", "gui_only"],
        help="Agent 模式：plan（Plan Agent + 多 GUI Agent）或 gui_only（单个 GUI Agent 独立执行）",
    )
    parser.add_argument(
        "--gui-agent",
        type=str, default="seed18",
        help="GUI Agent 类型（默认 seed18，可选 claude / kimi）",
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
    return parser.parse_args()


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    """
    主流程：多线程并行任务调度器。

    1. 解析命令行参数
    2. 扫描 QA 任务列表
    3. 创建 MemoryGuard
    4. 使用 ThreadPoolExecutor 并行提交任务
    5. 收集结果并写入 JSON
    """
    args = parse_args()

    # 消融实验环境变量覆盖（run_ablation.py 通过 subprocess 环境变量传递）
    _ablation_agent_mode = os.environ.get("ABLATION_AGENT_MODE", "")
    _ablation_gui_agent = os.environ.get("ABLATION_GUI_AGENT", "")
    if _ablation_agent_mode:
        args.agent_mode = _ablation_agent_mode
    if _ablation_gui_agent and hasattr(args, "gui_agent"):
        args.gui_agent = _ablation_gui_agent

    setup_logging(args.max_parallel_tasks)
    log = logging.getLogger("pipeline.main")

    if _ablation_agent_mode or _ablation_gui_agent:
        log.info("消融环境变量覆盖: agent_mode=%s, gui_agent=%s",
                 _ablation_agent_mode or "(未覆盖)", _ablation_gui_agent or "(未覆盖)")

    # conda 环境检查
    required_env = os.environ.get("REQUIRED_CONDA_ENV", "")
    strict_check = os.environ.get("REQUIRED_CONDA_ENV_STRICT", "0") == "1"
    ensure_conda_env(required_env, strict=strict_check)

    agent_mode = getattr(args, "agent_mode", "plan")

    log.info("=" * 80)
    log.info("QA 批量任务 Pipeline — 多线程并行版本")
    log.info("  Agent 模式: %s | 并发数: %d | VM/任务: %d | VM 内存: %s | CPU: %s | 内存上限: %.1f GiB",
             agent_mode, args.max_parallel_tasks, args.vms_per_task,
             args.vm_memory, args.vm_cpu_cores, args.memory_limit_gb)
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
            "  [gui_only] GUI Agent: %s | 最大轮次: %d | 超时: %ds",
            args.gui_agent, args.gui_max_rounds, args.gui_timeout,
        )
    log.info("=" * 80)

    # 自定义输出路径
    if args.output_json_path:
        global OUTPUT_JSON_PATH
        OUTPUT_JSON_PATH = os.path.abspath(args.output_json_path)
        log.info("自定义输出路径: %s", OUTPUT_JSON_PATH)

    # 扫描全部 QA 任务
    task_items = scan_qa_tasks(TASKS_LIST_DIR)
    log.info("共检测到 QA 任务数量: %d", len(task_items))

    # 按 --task-list-file 或 --task-uids 过滤任务子集
    filter_uids = None
    if args.task_list_file:
        fpath = args.task_list_file
        if not os.path.isabs(fpath):
            fpath = os.path.join(current_dir, fpath)
        with open(fpath, "r", encoding="utf-8") as f:
            filter_uids = set(
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")
            )
        log.info("从文件 %s 加载了 %d 个任务 UID", fpath, len(filter_uids))
    elif args.task_uids:
        filter_uids = set(uid.strip() for uid in args.task_uids.split(",") if uid.strip())
        log.info("从命令行指定了 %d 个任务 UID", len(filter_uids))

    if filter_uids is not None:
        before = len(task_items)
        task_items = [
            (uid, path, cfg) for uid, path, cfg in task_items
            if uid in filter_uids
        ]
        log.info("过滤后保留 %d/%d 个任务", len(task_items), before)
        # 检查是否有指定的 UID 未找到
        found_uids = {uid for uid, _, _ in task_items}
        missing = filter_uids - found_uids
        if missing:
            log.warning("以下 %d 个 UID 未在任务列表中找到: %s",
                        len(missing), ", ".join(sorted(missing)[:5]))

    if not task_items:
        log.warning("未找到 QA 任务，退出")
        return

    # 创建内存管理器
    memory_guard = MemoryGuard(args.memory_limit_gb, args.vm_memory)

    # 创建 group_id 池（线程安全队列）
    # 保证同一 group_id 不会被两个线程同时使用，避免容器名称冲突
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
    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)

    # 并行调度
    completed_count = 0
    total_count = len(task_items)

    with ThreadPoolExecutor(
        max_workers=args.max_parallel_tasks,
        thread_name_prefix="Worker",
    ) as executor:
        futures = {}

        for i, (task_uid, task_path, task_config) in enumerate(task_items):
            log.info("提交任务 %d/%d | UID: %s",
                     i + 1, total_count, task_uid[:8])

            fut = executor.submit(
                run_single_task,
                task_uid, task_path, task_config,
                available_groups, args, memory_guard,
                output_results, results_lock, OUTPUT_JSON_PATH,
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

            # 实时持久化中间结果（防止意外中断丢失数据）
            evaluator_output = task_result.get("evaluator_output")
            status = "PASS" if evaluator_output and evaluator_output.get("pass") else "FAIL"
            if task_result.get("interrupted"):
                status = "INTERRUPTED"

            # 构造 token 消耗摘要
            token_info = task_result.get("token_usage") or {}
            task_cost = token_info.get("total_cost_usd", 0.0)
            task_tokens = (
                token_info.get("plan_agent", {}).get("total_tokens", 0)
                + token_info.get("gui_agent", {}).get("total_tokens", 0)
            )
            token_str = ""
            if task_tokens > 0:
                token_str = f" | tokens: {task_tokens:,}"
            if task_cost > 0:
                token_str += f" | 费用: ${task_cost:.4f}"

            log.info(
                "任务完成 %d/%d | UID: %s | 状态: %s%s",
                completed_count, total_count, task_uid[:8], status, token_str,
            )

            # 每完成一个任务就写一次中间结果
            try:
                with results_lock:
                    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
                        json.dump(output_results, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                log.warning("写入中间结果失败: %s", exc)

    # 停止心跳
    heartbeat.stop()

    # 写入最终结果
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(output_results, f, ensure_ascii=False, indent=2)

    log.info("=" * 80)
    log.info("全部任务执行完成 (%d/%d)", completed_count, total_count)
    log.info("输出结果文件: %s", OUTPUT_JSON_PATH)
    log.info("=" * 80)

    # 统计汇总
    passed = sum(
        1 for r in output_results.values()
        if r.get("evaluator_output") and r.get("evaluator_output", {}).get("pass")
    )
    interrupted = sum(1 for r in output_results.values() if r.get("interrupted"))

    # Token 数目与费用汇总
    total_plan_prompt = 0
    total_plan_completion = 0
    total_gui_prompt = 0
    total_gui_completion = 0
    total_cost_all = 0.0
    for r in output_results.values():
        token_info = r.get("token_usage") or {}
        plan_t = token_info.get("plan_agent", {})
        gui_t = token_info.get("gui_agent", {})
        total_plan_prompt += plan_t.get("prompt_tokens", 0)
        total_plan_completion += plan_t.get("completion_tokens", 0)
        total_gui_prompt += gui_t.get("prompt_tokens", 0)
        total_gui_completion += gui_t.get("completion_tokens", 0)
        total_cost_all += token_info.get("total_cost_usd", 0.0)

    total_all_tokens = total_plan_prompt + total_plan_completion + total_gui_prompt + total_gui_completion

    log.info("统计: 通过 %d | 中断 %d | 总计 %d", passed, interrupted, total_count)
    if total_all_tokens > 0:
        log.info(
            "Token 消耗: Plan Agent (prompt: %s, completion: %s) | "
            "GUI Agent (prompt: %s, completion: %s) | 总计: %s",
            f"{total_plan_prompt:,}", f"{total_plan_completion:,}",
            f"{total_gui_prompt:,}", f"{total_gui_completion:,}",
            f"{total_all_tokens:,}",
        )
    if total_cost_all > 0:
        log.info("总 Token 费用: $%.4f", total_cost_all)


if __name__ == "__main__":
    main()
