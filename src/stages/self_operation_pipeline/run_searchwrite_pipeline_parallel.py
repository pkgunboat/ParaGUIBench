"""
Search&Write 共享文档任务 Pipeline — 多线程并行版本。

Agent 通过 OnlyOffice 共享链接在浏览器中协作编辑 xlsx 文档。
与 FileOperate Pipeline 的区别：不在 VM 本地编辑，而是通过在线共享文档。

4 阶段设计:
    Stage 0: OnlyOffice 文档准备（串行，上传模板 + 生成共享链接）
    Stage 1: VM 环境初始化（并行，无需 sshfs 挂载和文件下载）
    Stage 2: Agent 执行（并行，instruction 中注入共享链接）
    Stage 2.5: 触发 OnlyOffice 保存（关闭 Chrome → 等待回调）
    Stage 3: 评估（下载编辑后文件 → 逐单元格匹配）

用法:
    # 顺序执行（默认）
    python run_searchwrite_pipeline_parallel.py

    # 2 个任务并行，每任务 3 个 VM
    python run_searchwrite_pipeline_parallel.py -p 2 -n 3

    # 指定 VM IP，OnlyOffice 文档共享服务地址会自动检测
    python run_searchwrite_pipeline_parallel.py --vm-ip 10.1.110.114
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

import requests

# ============================================================
# 路径设置
# ============================================================

current_dir = os.path.dirname(os.path.abspath(__file__))
examples_dir = os.path.dirname(current_dir)          # src/stages/
ubuntu_env_dir = os.path.dirname(examples_dir)       # src/
repo_root = os.path.dirname(ubuntu_env_dir)          # repo root
parallel_benchmark_dir = os.path.join(ubuntu_env_dir, "parallel_benchmark")
onlyoffice_dir = os.path.join(repo_root, "docker", "onlyoffice")

if parallel_benchmark_dir not in sys.path:
    sys.path.insert(0, parallel_benchmark_dir)
if ubuntu_env_dir not in sys.path:
    sys.path.insert(0, ubuntu_env_dir)
if examples_dir not in sys.path:
    sys.path.insert(0, examples_dir)
if onlyoffice_dir not in sys.path:
    sys.path.insert(0, onlyoffice_dir)

# ============================================================
# 从原始 pipeline 导入可复用函数（不修改原文件）
# ============================================================

from run_QA_pipeline import (  # noqa: E402
    ensure_conda_env,
    load_task_config,
    extract_execution_summary,
    parse_prepare_script_path,
    _list_hf_files,
    TASKS_LIST_DIR,
)

from run_QA_pipeline_parallel import (  # noqa: E402
    execute_on_vm_with_ip,
    wait_for_vm_ready_with_ip,
    get_ssh_credentials,
    setup_logging,
    get_task_logger,
    rebuild_containers_parallel,
    cleanup_group_containers,
    stage2_execute_agent_parallel,
)

from parallel_agents.plan_agent_thought_action import (  # noqa: E402
    calculate_cost,
)

from desktop_env.controllers.python import PythonController  # noqa: E402
from parallel_agents_as_tools.seed18_gui_agent_as_tool import Seed18GUIAgentTool  # noqa: E402
from parallel_agents_as_tools.claude_gui_agent_as_tool import ClaudeGUIAgentTool  # noqa: E402
from parallel_agents_as_tools.kimi_gui_agent_as_tool import KimiGUIAgentTool  # noqa: E402

from desktop_env.providers.docker.parallel_manager import (  # noqa: E402
    ContainerSetConfig,
    MemoryGuard,
    allocate_ports_for_group,
    scan_remote_docker_ports,
)

# ============================================================
# OnlyOffice 工具
# ============================================================

from onlyoffice_benchmark_utils import (  # noqa: E402
    init_task_document,
    create_share_link_via_api,
    fetch_document_file_via_api,
    resolve_document_sharing_url,
)

# ============================================================
# 评估器导入
# ============================================================

sys.path.insert(0, os.path.join(parallel_benchmark_dir, "eval"))
from searchwrite_xlsx_evaluator import evaluate, evaluate_multi_file  # noqa: E402

# ============================================================
# 常量
# ============================================================

OUTPUT_JSON_PATH = os.path.join(
    ubuntu_env_dir, "logs", "run_searchwrite_pipeline_parallel.json"
)

# 本地 HuggingFace 缓存根目录
HF_DATA_DIR = os.path.join(parallel_benchmark_dir, "hf_data")

# 全局追踪：记录所有已启动的容器组（用于 atexit 清理）
_active_groups: Dict[int, ContainerSetConfig] = {}
_active_groups_lock = threading.Lock()

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

    与单次禁用屏保的区别：
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
        self._thread = None

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


# 6 个 Search&Write 任务的 task_id 列表
SEARCHWRITE_TASK_IDS = {
    "Operation-FileOperate-Search&write-006",
    "Operation-FileOperate-Search&write-008",
    "Operation-FileOperate-SearchAndWrite-002",
    "Operation-FileOperate-SearchAndWrite-004",
    "Operation-FileOperate-SearchAndWrite-005",
    "Operation-WebOperate-Search&write-001",
}


# ============================================================
# 任务扫描
# ============================================================

def scan_searchwrite_tasks(
    tasks_dir: str,
    allowed_task_ids: set = None,
) -> List[Tuple[str, str, Dict]]:
    """
    扫描 tasks_list 目录，筛选 Search&Write 任务。

    输入:
        tasks_dir: 任务 JSON 文件所在目录
        allowed_task_ids: 可选的 task_id 白名单；为 None 时使用默认的 SEARCHWRITE_TASK_IDS

    输出:
        [(task_uid, task_path, task_config), ...]，按 task_id 排序
    """
    target_ids = allowed_task_ids if allowed_task_ids is not None else SEARCHWRITE_TASK_IDS
    results = []
    if not os.path.isdir(tasks_dir):
        logging.getLogger("pipeline").error("任务目录不存在: %s", tasks_dir)
        return results

    for fname in os.listdir(tasks_dir):
        if not fname.endswith(".json"):
            continue

        task_path = os.path.join(tasks_dir, fname)
        try:
            config = load_task_config(task_path)
        except Exception:
            continue

        task_id = config.get("task_id", "")
        task_uid = config.get("task_uid", "")

        if task_id in target_ids and task_uid:
            results.append((task_uid, task_path, config))

    results.sort(key=lambda x: x[2].get("task_id", ""))
    return results


# ============================================================
# 辅助：获取任务的 xlsx 文件列表
# ============================================================

def _ensure_task_data_cached(task_uid: str, prepare_script_path: str, log: logging.Logger) -> bool:
    """
    确保任务数据已缓存到本地 hf_data/ 目录。
    如果缓存不存在，自动从 HuggingFace 下载。

    支持三种 URL 格式：
      1. HuggingFace tree 目录 URL → 列出文件后逐个下载
      2. HuggingFace resolve 直接文件 URL → 直接下载
      3. 外部 URL → 直接下载

    输入:
        task_uid: 任务 UID
        prepare_script_path: 逗号分隔的数据 URL
        log: logger

    输出:
        bool（是否缓存就绪）
    """
    from urllib.parse import unquote

    cache_dir = os.path.join(HF_DATA_DIR, "benchmark_dataset", task_uid)
    if os.path.isdir(cache_dir) and os.listdir(cache_dir):
        return True  # 缓存已存在

    if not prepare_script_path:
        return False

    os.makedirs(cache_dir, exist_ok=True)
    urls = [u.strip() for u in prepare_script_path.split(",") if u.strip()]
    downloaded = 0

    for url in urls:
        # 尝试 HuggingFace tree 目录格式
        try:
            repo_id, revision, subdir = parse_prepare_script_path(url)
            file_paths = _list_hf_files(repo_id, revision, subdir)
            for rel_path in file_paths:
                dl_url = f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{rel_path}"
                filename = os.path.basename(rel_path)
                dest = os.path.join(cache_dir, filename)
                if os.path.isfile(dest):
                    downloaded += 1
                    continue
                log.info("  下载 %s ← HF tree", filename)
                resp = requests.get(dl_url, timeout=120)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(resp.content)
                downloaded += 1
            continue
        except ValueError:
            pass  # 不是 HF tree 格式
        except Exception as exc:
            log.warning("  HF tree 下载失败: %s，尝试直接下载...", exc)

        # 直接下载模式
        filename = unquote(url.rstrip("/").split("/")[-1])
        dest = os.path.join(cache_dir, filename)
        if os.path.isfile(dest):
            downloaded += 1
            continue
        try:
            log.info("  下载 %s ← 直接URL", filename)
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                f.write(resp.content)
            downloaded += 1
        except Exception as exc:
            log.warning("  直接下载失败 %s: %s", filename, exc)

    return downloaded > 0


def _get_task_xlsx_files(task_uid: str) -> List[str]:
    """
    从本地 HF 缓存中获取指定任务的 xlsx 模板文件列表。

    输入:
        task_uid: 任务 UID

    输出:
        xlsx 文件名列表（如 ["QS_Top5_2025_Template.xlsx"]）
    """
    template_dir = os.path.join(HF_DATA_DIR, "benchmark_dataset", task_uid)
    if not os.path.isdir(template_dir):
        return []
    return [f for f in os.listdir(template_dir) if f.endswith(".xlsx")]


# ============================================================
# Stage 0: OnlyOffice 文档准备
# ============================================================

def stage0_prepare_documents(
    task_items: List[Tuple[str, str, Dict]],
    onlyoffice_base_url: str,
    onlyoffice_host_ip: str,
    log: logging.Logger,
) -> Dict[str, Dict[str, str]]:
    """
    Stage 0：上传模板到 OnlyOffice 服务器并生成共享链接。
    串行执行以避免 shared_links.json 并发写冲突。

    输入:
        task_items: [(task_uid, task_path, task_config), ...]
        onlyoffice_base_url: OnlyOffice 文档共享服务 base URL（如 http://10.1.110.114:5000）
        onlyoffice_host_ip: OnlyOffice 宿主机 IP（用于生成 Agent 可访问链接）
        log: logger

    输出:
        {task_uid: {filename: share_url}}
        如 {"43a02400-...": {"QS_Top5_2025_Template.xlsx": "http://..."}}
    """
    log.info("STAGE 0: OnlyOffice 文档准备")

    # 检查 OnlyOffice 服务可用性
    try:
        resp = requests.get(onlyoffice_base_url, timeout=10)
        log.info("OnlyOffice 服务可用 (status=%d)", resp.status_code)
    except Exception as exc:
        log.error("OnlyOffice 服务不可用 (%s): %s", onlyoffice_base_url, exc)
        return {}

    share_urls: Dict[str, Dict[str, str]] = {}

    for task_uid, _, task_config in task_items:
        task_id = task_config.get("task_id", "")
        uid_short = task_uid.split("-")[0]

        # 自动下载：若本地缓存不存在，从 HuggingFace 下载任务数据
        prepare_url = task_config.get("prepare_script_path", "")
        if not _get_task_xlsx_files(task_uid) and prepare_url:
            log.info("任务 %s (%s) 本地无缓存，尝试自动下载...", task_id, uid_short)
            _ensure_task_data_cached(task_uid, prepare_url, log)

        xlsx_files = _get_task_xlsx_files(task_uid)

        if not xlsx_files:
            log.warning("任务 %s (%s) 无 xlsx 模板文件（下载后仍未找到），跳过", task_id, uid_short)
            continue

        task_urls: Dict[str, str] = {}

        for xlsx_name in xlsx_files:
            stem = os.path.splitext(xlsx_name)[0]
            doc_id = f"{uid_short}_{stem}"
            local_path = os.path.join(
                HF_DATA_DIR, "benchmark_dataset", task_uid, xlsx_name
            )

            if not os.path.isfile(local_path):
                log.error("模板文件不存在: %s", local_path)
                continue

            try:
                init_task_document(doc_id, local_path, ext="xlsx")
                log.info("  文档准备成功: %s → %s", xlsx_name, doc_id)
            except Exception as exc:
                log.error("准备共享文档失败 %s: %s", xlsx_name, exc)
                continue

            # 通过 API 生成共享链接
            try:
                share_url = create_share_link_via_api(onlyoffice_base_url, doc_id)
                task_urls[xlsx_name] = share_url
                log.info("  共享链接: %s → %s", doc_id, share_url)
            except Exception as exc:
                log.error("创建共享链接失败 %s: %s", doc_id, exc)
                continue

        if task_urls:
            share_urls[task_uid] = task_urls

    log.info("Stage 0 完成: %d/%d 个任务已准备文档",
             len(share_urls), len(task_items))
    return share_urls


# ============================================================
# Stage 1: VM 初始化（轻量版，无 sshfs/下载）
# ============================================================

def disable_screensaver_parallel(
    vm_ip: str,
    vm_ports: List[int],
    log: logging.Logger,
) -> None:
    """
    在指定端口的所有 VM 中禁用屏保和锁屏（防黑屏第一层：预防）。

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


def _launch_chrome(vm_ip: str, vm_port: int, url: str, log: logging.Logger) -> None:
    """
    在 VM 中启动 Chrome 并打开指定 URL。

    输入:
        vm_ip: VM IP
        vm_port: VM 端口
        url: 要打开的 URL
        log: logger
    """
    cmd = (
        'bash -c "nohup python3 -c \\"import subprocess, time, os; '
        'env = os.environ.copy(); '
        "env['DISPLAY'] = ':0'; "
        "subprocess.Popen(['google-chrome', '--no-first-run', '--no-default-browser-check', "
        f"'{url}'], env=env); "
        'time.sleep(2)\\" '
        '> /tmp/bootstrap_chrome.log 2>&1 &"'
    )
    _ = execute_on_vm_with_ip(vm_ip, vm_port, cmd)
    log.info("  Chrome 已启动: %s", url)


def init_vm_searchwrite(
    vm_ip: str,
    vm_port: int,
    vnc_port: int,
    rebuilt: bool,
    log: logging.Logger,
) -> bool:
    """
    Search&Write 任务的 VM 初始化（轻量版）。
    不需要 sshfs 挂载和文件下载，因为 Agent 通过浏览器访问 OnlyOffice。

    输入:
        vm_ip: VM IP
        vm_port: VM 端口
        vnc_port: VNC 端口
        rebuilt: 是否为重建后首次初始化
        log: logger

    输出:
        bool
    """
    log.info("初始化 VM (port %d, VNC http://%s:%d/)", vm_port, vm_ip, vnc_port)

    wait_time = 120 if rebuilt else 30
    if not wait_for_vm_ready_with_ip(vm_ip, vm_port, max_wait=wait_time):
        log.error("VM %d 无法响应", vm_port)
        return False

    # 启动 Chrome（打开 Bing，后续由 Agent 导航到共享链接）
    _launch_chrome(vm_ip, vm_port, "https://www.bing.com", log)

    log.info("VM %d 初始化成功", vm_port)
    return True


def stage1_initialize(
    config: ContainerSetConfig,
    log: logging.Logger,
) -> bool:
    """
    Stage 1: VM 环境初始化。
    重建容器 → 等待 VM 就绪 → 禁用屏保 → 启动 Chrome。

    输入:
        config: 容器组配置
        log: logger

    输出:
        bool
    """
    log.info("STAGE 1: VM 环境初始化 (组 %d)", config.group_id)

    # 重建容器
    rebuilt = rebuild_containers_parallel(config, log)
    if not rebuilt:
        log.error("容器重建失败")
        return False

    # 初始化各 VM
    vm_pairs = config.get_vm_pairs()
    success_count = 0
    for vm_port, vnc_port in vm_pairs:
        if init_vm_searchwrite(
            vm_ip=config.vm_ip,
            vm_port=vm_port,
            vnc_port=vnc_port,
            rebuilt=True,
            log=log,
        ):
            success_count += 1
        else:
            log.warning("VM %d 初始化失败", vm_port)

    # 禁用屏保（防黑屏第一层）
    if success_count > 0:
        disable_screensaver_parallel(config.vm_ip, config.get_server_ports(), log)

    log.info("初始化完成: %d/%d 个 VM 成功", success_count, len(vm_pairs))
    return success_count == len(vm_pairs)


# ============================================================
# Stage 2: Agent 执行（注入共享链接到 instruction）
# ============================================================

def _build_instruction_with_share_urls(
    original_instruction: str,
    share_urls: Dict[str, str],
) -> str:
    """
    在原始 instruction 后追加 OnlyOffice 共享链接信息。

    输入:
        original_instruction: 原始任务描述
        share_urls: {filename: share_url}

    输出:
        augmented instruction
    """
    links_text = ""
    for filename, url in share_urls.items():
        links_text += f"- {filename}: {url}\n"

    augmented = (
        "IMPORTANT DOCUMENT LINKS:\n"
        f"{links_text}\n"
        "Use the exact link above whenever a GUI subtask needs to open or edit "
        "the shared spreadsheet. Include the relevant link verbatim in those "
        "GUI subtask descriptions.\n\n"
        f"{original_instruction}\n\n"
        "The document is available as an online shared spreadsheet. "
        "All team members can open the same link and edit simultaneously. "
        "Please open the link above in Chrome. "
        "Each team member should search for and fill in their assigned portion. "
        "Edit the cells directly in the browser. "
        "After finishing all edits, close the browser tab to save."
    )
    return augmented


# ============================================================
# Stage 2.5: 触发 OnlyOffice 保存
# ============================================================

def stage2_5_trigger_save(
    config: ContainerSetConfig,
    task_uid: str,
    share_urls: Dict[str, str],
    onlyoffice_base_url: str,
    log: logging.Logger,
) -> bool:
    """
    Stage 2.5：关闭所有 VM 的 Chrome → 等待 OnlyOffice 回调保存。

    Agent 执行完后，OnlyOffice 需要收到"编辑器关闭"信号才会触发回调保存。
    强制关闭 Chrome 可以触发此信号。

    输入:
        config: 容器组配置
        task_uid: 任务 UID
        share_urls: {filename: share_url}（用于验证文件更新）
        onlyoffice_base_url: OnlyOffice base URL
        log: logger

    输出:
        bool（保存是否成功触发）
    """
    log.info("STAGE 2.5: 触发 OnlyOffice 保存")

    # 关闭所有 VM 的 Chrome
    vm_pairs = config.get_vm_pairs()
    for vm_port, _ in vm_pairs:
        cmd = "pkill -f google-chrome || true"
        execute_on_vm_with_ip(config.vm_ip, vm_port, cmd)
        log.info("  VM %d: Chrome 已关闭", vm_port)

    # 等待 OnlyOffice 回调完成
    log.info("  等待 20 秒让 OnlyOffice 完成文档保存回调...")
    time.sleep(20)

    # 验证文件已更新
    uid_short = task_uid.split("-")[0]
    all_ok = True
    for filename in share_urls:
        stem = os.path.splitext(filename)[0]
        doc_id = f"{uid_short}_{stem}"
        try:
            content = fetch_document_file_via_api(onlyoffice_base_url, doc_id)
            if content and len(content) > 0:
                log.info("  文件 %s 可下载 (%d bytes)", doc_id, len(content))
            else:
                log.warning("  文件 %s 内容为空", doc_id)
                all_ok = False
        except Exception as exc:
            log.warning("  验证文件 %s 失败: %s", doc_id, exc)
            all_ok = False

    return all_ok


# ============================================================
# Stage 3: 评估
# ============================================================

def stage3_evaluate(
    task_uid: str,
    task_config: Dict[str, Any],
    share_urls: Dict[str, str],
    onlyoffice_base_url: str,
    log: logging.Logger,
    save_result_dir: str = "",
) -> Dict[str, Any]:
    """
    Stage 3：下载 Agent 编辑后的 xlsx 并与 GT 进行逐单元格比对评估。

    流程:
        1. 通过 API 下载 Agent 编辑后的 xlsx
        2. 读取本地 GT 和模板
        3. 调用评估器
        4. SAW-004 等多文件任务取平均分
        5. 保存结果文件（如指定 save_result_dir）

    输入:
        task_uid: 任务 UID
        task_config: 任务配置
        share_urls: {filename: share_url}
        onlyoffice_base_url: OnlyOffice base URL
        log: logger
        save_result_dir: 结果文件持久化目录（可选，为空则不保存）

    输出:
        评估结果字典（包含 saved_result_path 字段）
    """
    task_id = task_config.get("task_id", "")
    uid_short = task_uid.split("-")[0]
    log.info("STAGE 3: 评估 %s", task_id)

    xlsx_files = list(share_urls.keys())

    if not xlsx_files:
        return {"score": 0.0, "pass": False, "reason": "无 xlsx 文件可评估"}

    file_pairs: List[Dict[str, str]] = []
    # 追踪已下载但无 GT 的 Agent 结果文件（用于人工评价模式）
    result_only_files: List[Dict[str, str]] = []

    for xlsx_name in xlsx_files:
        stem = os.path.splitext(xlsx_name)[0]
        doc_id = f"{uid_short}_{stem}"

        # 下载 Agent 编辑后的文件
        try:
            content = fetch_document_file_via_api(onlyoffice_base_url, doc_id)
        except Exception as exc:
            log.error("下载结果文件失败 %s: %s", doc_id, exc)
            continue

        if not content:
            log.error("结果文件为空: %s", doc_id)
            continue

        # 保存到临时文件
        result_tmp = os.path.join(
            tempfile.gettempdir(),
            "searchwrite_results",
            task_uid,
            xlsx_name,
        )
        os.makedirs(os.path.dirname(result_tmp), exist_ok=True)
        with open(result_tmp, "wb") as f:
            f.write(content)
        log.info("  结果文件已下载: %s (%d bytes)", xlsx_name, len(content))

        # 本地 GT 路径
        gt_path = os.path.join(HF_DATA_DIR, "answer_files", task_uid, xlsx_name)
        if not os.path.isfile(gt_path):
            log.warning("GT 文件不存在: %s，记录为人工评价", gt_path)
            result_only_files.append({"name": xlsx_name, "result": result_tmp})
            continue

        # 本地模板路径
        template_path = os.path.join(
            HF_DATA_DIR, "benchmark_dataset", task_uid, xlsx_name
        )
        if not os.path.isfile(template_path):
            log.error("模板文件不存在: %s", template_path)
            continue

        file_pairs.append({
            "name": xlsx_name,
            "template": template_path,
            "gt": gt_path,
            "result": result_tmp,
        })

    # 所有文件都没有 GT：保存 Agent 结果供人工评价
    if not file_pairs and result_only_files:
        log.warning("GT 不可用，跳过自动评估，保存 Agent 结果供人工评价")
        saved_path = ""
        if save_result_dir:
            try:
                task_save_dir = os.path.join(save_result_dir, task_id)
                os.makedirs(task_save_dir, exist_ok=True)
                for item in result_only_files:
                    dst = os.path.join(task_save_dir, item["name"])
                    shutil.copy2(item["result"], dst)
                saved_path = task_save_dir
                log.info("Agent 结果已保存: %s", task_save_dir)
            except Exception as exc:
                log.warning("保存结果失败: %s", exc)
        return {
            "score": -1, "pass": None, "status": "unknown",
            "reason": f"GT 不可用 ({task_id})，已保存结果供人工评价",
            "saved_result_path": saved_path,
        }

    if not file_pairs:
        return {"score": 0.0, "pass": False, "reason": "无有效文件可评估"}

    # 先保存结果文件（在评估之前，确保即使评估崩溃也能保留 Agent 产物）
    saved_path = ""
    if save_result_dir and file_pairs:
        try:
            task_save_dir = os.path.join(save_result_dir, task_id)
            os.makedirs(task_save_dir, exist_ok=True)
            for pair in file_pairs:
                dst = os.path.join(task_save_dir, pair["name"])
                shutil.copy2(pair["result"], dst)
            saved_path = task_save_dir
            log.info("Agent 结果文件已保存到: %s", task_save_dir)
        except Exception as exc:
            log.warning("保存结果文件失败: %s", exc)

    # 单文件直接评估，多文件用 evaluate_multi_file
    if len(file_pairs) == 1:
        pair = file_pairs[0]
        try:
            eval_result = evaluate(
                template_path=pair["template"],
                gt_path=pair["gt"],
                result_path=pair["result"],
            )
        except Exception as exc:
            log.error("评估异常: %s", exc)
            eval_result = {"score": 0.0, "pass": False, "reason": str(exc)}
    else:
        try:
            eval_result = evaluate_multi_file(file_pairs)
        except Exception as exc:
            log.error("评估异常: %s", exc)
            eval_result = {"score": 0.0, "pass": False, "reason": str(exc)}

    log.info("评估结果: score=%.2f, pass=%s, cells=%d/%d",
             eval_result.get("score", 0),
             eval_result.get("pass", False),
             eval_result.get("matched_cells", 0),
             eval_result.get("total_cells", 0))

    eval_result["saved_result_path"] = saved_path
    return eval_result


# ============================================================
# Stage 2: 纯 GUI Agent 执行（gui_only 模式）
# ============================================================

def stage2_execute_gui_only(
    task_config: Dict[str, Any],
    task_uid: str,
    config: "ContainerSetConfig",
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
        task_config: 任务配置（instruction 应已包含共享链接信息）
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
        _record_dir, f"searchwrite_gui_only_{task_uid}_{timestamp}.json"
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
    task_share_urls: Dict[str, str],
    available_groups: queue.Queue,
    args: argparse.Namespace,
    memory_guard: MemoryGuard,
    output_results: Dict[str, Any] = None,
    results_lock: threading.Lock = None,
    output_json_path: str = "",
) -> Dict[str, Any]:
    """
    单个 Search&Write 任务的完整执行流程（Worker 线程）。

    通过 available_groups 队列获取可用 group_id，保证同一 group_id
    不会被两个线程同时使用。任务完成后归还 group_id。

    流程:
        0. available_groups.get() — 获取可用 group_id
        1. memory_guard.acquire() — 申请内存额度
        2. allocate_ports_for_group() — 分配端口
        3. Stage 1 初始化 — 重建容器 + VM 初始化
        4. Stage 2 Agent 执行 — instruction 注入共享链接
        5. Stage 2.5 触发保存 — 关闭 Chrome + 等待回调
        6. Stage 3 评估 — 下载文件 + 单元格比对
        7. 清理 — 容器清理 + 内存释放 + 归还 group_id

    输入:
        task_uid: 任务 UID
        task_path: 任务 JSON 路径
        task_config: 任务配置字典
        task_share_urls: 该任务的 {filename: share_url} 映射
        available_groups: 可用 group_id 队列
        args: 命令行参数
        memory_guard: 内存管理器

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

    task_id = task_config.get("task_id", "")
    log = get_task_logger(group_id, task_uid)
    log.info("获得组 %d，开始执行任务 %s (%s)", group_id, task_uid[:8], task_id)

    instruction = task_config.get("instruction", "")

    task_result: Dict[str, Any] = {
        "task_uid": task_uid,
        "task_id": task_id,
        "instruction": instruction,
        "model_output_answer": "",
        "plan_agent_model": "",
        "gui_agent_model": "",
        "plan_agent_total_rounds": 0,
        "evaluator_output": None,
        "token_usage": None,
        "plan_agent_last_round_output": "",
        "plan_agent_last_round_messages": [],
        "interrupted": False,
        "interrupt_reason": "",
        "group_id": group_id,
        "share_urls": task_share_urls,
    }

    # 申请内存额度
    if not memory_guard.acquire(config.num_vms):
        task_result["interrupted"] = True
        task_result["interrupt_reason"] = "memory_guard_timeout"
        log.error("内存申请超时，跳过任务")
        available_groups.put(group_id)
        return task_result

    try:
        # 分配端口（含远程端口扫描，自动避开已占用端口）
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

        # 注册端口到心跳服务
        register_group_ports(group_id, config.get_server_ports())

        # Stage 1: VM 初始化
        if not stage1_initialize(config, log):
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "stage1_initialize_failed"
            log.error("VM 初始化失败，跳过任务")
            return task_result

        # Stage 2: Agent 执行（注入共享链接到 instruction）
        augmented_instruction = _build_instruction_with_share_urls(
            instruction, task_share_urls,
        )
        # 临时替换 instruction
        modified_config = dict(task_config)
        modified_config["instruction"] = augmented_instruction

        agent_mode = getattr(args, "agent_mode", "plan")
        try:
            if agent_mode == "gui_only":
                result, _ = stage2_execute_gui_only(
                    modified_config, task_uid, config, log,
                    gui_agent=getattr(args, "gui_agent", "seed18"),
                    max_rounds=getattr(args, "gui_max_rounds", 200),
                    gui_timeout=getattr(args, "gui_timeout", 3600),
                    output_dir=os.environ.get("ABLATION_RECORD_DIR", ""),
                )
            else:
                result, _ = stage2_execute_agent_parallel(
                    modified_config, task_uid, config, log,
                )
        except Exception as exc:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = f"stage2_exception: {exc}"
            log.error("Agent 执行失败: %s", exc)
            return task_result

        # Plan 模式执行记录保存（与 gui_only 模式保持一致的输出逻辑）
        if agent_mode != "gui_only":
            _record_dir = os.environ.get("ABLATION_RECORD_DIR", "") or os.path.join(ubuntu_env_dir, "logs")
            os.makedirs(_record_dir, exist_ok=True)
            _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            _record_path = os.path.join(_record_dir, f"searchwrite_plan_{task_uid}_{_ts}.json")
            try:
                with open(_record_path, "w", encoding="utf-8") as _f:
                    json.dump({
                        "task_uid": task_uid,
                        "instruction": task_config.get("instruction", ""),
                        "agent_mode": "plan",
                        "result": result,
                    }, _f, ensure_ascii=False, indent=2, default=str)
                log.info("Plan 模式执行记录已保存: %s", _record_path)
            except Exception as _exc:
                log.warning("保存 Plan 模式执行记录失败: %s", _exc)

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

        # Stage 2.5: 触发 OnlyOffice 保存
        stage2_5_trigger_save(
            config, task_uid, task_share_urls, args.onlyoffice_url, log,
        )

        # Stage 3: 评估
        try:
            eval_result = stage3_evaluate(
                task_uid=task_uid,
                task_config=task_config,
                share_urls=task_share_urls,
                onlyoffice_base_url=args.onlyoffice_url,
                log=log,
                save_result_dir=getattr(args, "save_result_dir", ""),
            )
            task_result["evaluator_output"] = eval_result
        except Exception as exc:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = f"stage3_exception: {exc}"
            task_result["evaluator_output"] = {
                "pass": False, "score": 0.0,
                "error": f"evaluator_exception: {exc}",
            }
            log.error("评估失败: %s", exc)

        log.info("任务 %s 执行完成", task_uid[:8])
        return task_result

    finally:
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
        description="Search&Write 共享文档任务 Pipeline — 多线程并行版本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  # 顺序执行（默认）\n"
            "  python run_searchwrite_pipeline_parallel.py\n\n"
            "  # 2 个任务并行，每任务 3 个 VM\n"
            "  python run_searchwrite_pipeline_parallel.py -p 2 -n 3\n"
        ),
    )
    parser.add_argument(
        "-p", "--max-parallel-tasks",
        type=int, default=1,
        help="最大并发任务数（默认 1）",
    )
    parser.add_argument(
        "-n", "--vms-per-task",
        type=int, default=5,
        help="每个任务启动的 VM 数量（默认 5）",
    )
    parser.add_argument(
        "--vm-memory",
        type=str, default="1G",
        help='每个 QEMU VM 内存（默认 "1G"）',
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
        default="/home/agentlab/code/parallel-efficient-benchmark/"
                "ubuntu_env/docker_vm_data/Ubuntu.qcow2",
        help="VM 磁盘镜像路径",
    )
    parser.add_argument(
        "--docker-image",
        type=str, default="happysixd/osworld-docker-sshfs",
        help="Docker 镜像名",
    )
    parser.add_argument(
        "--onlyoffice-url",
        type=str, default="",
        help="OnlyOffice 文档共享服务 URL（默认读 deploy.yaml/ONLYOFFICE_HOST_IP；也可用 ONLYOFFICE_URL 覆盖）",
    )
    parser.add_argument(
        "--onlyoffice-host-ip",
        type=str, default="10.1.110.114",
        help="OnlyOffice 宿主机 IP（默认 10.1.110.114）",
    )
    parser.add_argument(
        "--save-result-dir",
        type=str, default="",
        help="Agent 结果文件持久化目录（按 task_id 子目录保存，用于事后复现和判断）。"
             "不指定则不保存。",
    )
    parser.add_argument(
        "--output-json-path",
        type=str, default="",
        help="自定义输出 JSON 路径（默认 logs/run_searchwrite_pipeline_parallel.json）",
    )
    parser.add_argument(
        "--task-list-file",
        type=str, default="",
        help="任务列表文件路径，每行一个 task_id（覆盖内置 SEARCHWRITE_TASK_IDS）",
    )
    parser.add_argument(
        "--skip-completed-dir",
        type=str, default="",
        help="跳过已完成任务：指定历史结果目录路径，该目录下每个子目录名视为已完成的 task_id。"
             "支持逗号分隔多个目录。",
    )
    # ---------- gui_only 模式参数 ----------
    parser.add_argument(
        "--agent-mode",
        type=str, default="plan",
        choices=["plan", "gui_only"],
        help="Agent 模式：plan（Plan Agent 分解 + 多 VM）或 gui_only（单 GUI Agent + 单 VM）",
    )
    parser.add_argument(
        "--gui-agent",
        type=str, default="seed18",
        choices=["seed18", "claude", "kimi"],
        help="gui_only 模式下使用的 GUI Agent 类型（默认 seed18）",
    )
    parser.add_argument(
        "--gui-max-rounds",
        type=int, default=200,
        help="gui_only 模式下 GUI Agent 最大执行轮次（默认 200）",
    )
    parser.add_argument(
        "--gui-timeout",
        type=int, default=3600,
        help="gui_only 模式下 GUI Agent 超时时间（秒，默认 3600）",
    )
    return parser.parse_args()


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    """
    主流程：Search&Write 共享文档任务并行调度器。

    1. 解析参数 + 扫描任务
    2. Stage 0：上传模板 + 生成共享链接（串行）
    3. 并行提交 Stage 1-3
    4. 收集结果 + 写 JSON
    """
    args = parse_args()
    try:
        from config_loader import DeployConfig
        deploy = DeployConfig()
        default_host = os.environ.get(
            "ONLYOFFICE_HOST_IP",
            deploy.onlyoffice_host or deploy.vm_host,
        )
        if not args.onlyoffice_url:
            args.onlyoffice_url = os.environ.get(
                "ONLYOFFICE_URL",
                f"http://{default_host}:{deploy.onlyoffice_flask_port}",
            )
        if not args.onlyoffice_host_ip:
            args.onlyoffice_host_ip = default_host
    except Exception:
        pass

    # 消融实验环境变量覆盖（run_ablation.py 通过 subprocess 环境变量传递）
    _ablation_agent_mode = os.environ.get("ABLATION_AGENT_MODE", "")
    _ablation_gui_agent = os.environ.get("ABLATION_GUI_AGENT", "")
    if _ablation_agent_mode:
        args.agent_mode = _ablation_agent_mode
    if _ablation_gui_agent:
        args.gui_agent = _ablation_gui_agent

    # gui_only 模式下强制 vms_per_task=1
    if args.agent_mode == "gui_only":
        args.vms_per_task = 1

    setup_logging(args.max_parallel_tasks)
    log = logging.getLogger("pipeline.main")
    args.onlyoffice_url = resolve_document_sharing_url(
        args.onlyoffice_url, args.onlyoffice_host_ip, log=log,
    )

    # 打印消融覆盖信息
    if _ablation_agent_mode or _ablation_gui_agent:
        log.info("消融环境变量覆盖: agent_mode=%s, gui_agent=%s",
                 _ablation_agent_mode or "(未覆盖)", _ablation_gui_agent or "(未覆盖)")

    # conda 环境检查
    required_env = os.environ.get("REQUIRED_CONDA_ENV", "")
    strict_check = os.environ.get("REQUIRED_CONDA_ENV_STRICT", "0") == "1"
    ensure_conda_env(required_env, strict=strict_check)

    log.info("=" * 80)
    log.info("Search&Write 共享文档任务 Pipeline — 多线程并行版本")
    log.info("  模式: %s | 并发数: %d | VM/任务: %d | VM 内存: %s | CPU: %s",
             args.agent_mode, args.max_parallel_tasks, args.vms_per_task,
             args.vm_memory, args.vm_cpu_cores)
    if args.agent_mode == "gui_only":
        log.info("  GUI Agent: %s | 最大轮次: %d | 超时: %ds",
                 args.gui_agent, args.gui_max_rounds, args.gui_timeout)
    log.info("  OnlyOffice: %s", args.onlyoffice_url)
    if args.save_result_dir:
        os.makedirs(args.save_result_dir, exist_ok=True)
        log.info("  结果文件保存目录: %s", args.save_result_dir)
    else:
        log.info("  结果文件保存: 未启用（使用 --save-result-dir 开启）")
    log.info("=" * 80)

    # 扫描任务（若指定了 --task-list-file，覆盖内置 SEARCHWRITE_TASK_IDS）
    allowed_task_ids = None
    if args.task_list_file:
        fpath = args.task_list_file
        if not os.path.isabs(fpath):
            fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), fpath)
        with open(fpath, "r", encoding="utf-8") as f:
            allowed_task_ids = set(
                line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")
            )
        log.info("从文件 %s 加载了 %d 个任务 ID", fpath, len(allowed_task_ids))

    task_items = scan_searchwrite_tasks(TASKS_LIST_DIR, allowed_task_ids=allowed_task_ids)
    log.info("共检测到 Search&Write 任务数量: %d", len(task_items))

    # 跳过已完成的任务
    if args.skip_completed_dir:
        completed_task_ids: set = set()
        for one_dir in args.skip_completed_dir.split(","):
            one_dir = one_dir.strip()
            if not one_dir or not os.path.isdir(one_dir):
                continue
            for name in os.listdir(one_dir):
                if os.path.isdir(os.path.join(one_dir, name)):
                    completed_task_ids.add(name)
        if completed_task_ids:
            before_count = len(task_items)
            task_items = [
                (uid, path, cfg) for uid, path, cfg in task_items
                if cfg.get("task_id", "") not in completed_task_ids
            ]
            skipped = before_count - len(task_items)
            log.info("跳过已完成任务: %d 个（来自 %s）", skipped, args.skip_completed_dir)

    if not task_items:
        log.warning("未找到 Search&Write 任务，退出")
        return

    for i, (uid, _, cfg) in enumerate(task_items):
        log.info("  [%d] %s (UID: %s)", i + 1, cfg.get("task_id", ""), uid[:8])

    # Stage 0: 准备文档（串行）
    share_urls_map = stage0_prepare_documents(
        task_items, args.onlyoffice_url, args.onlyoffice_host_ip, log,
    )

    # 过滤无共享链接的任务
    valid_items = [
        (uid, path, cfg) for uid, path, cfg in task_items
        if uid in share_urls_map
    ]
    if not valid_items:
        log.error("所有任务的文档准备失败，退出")
        return
    log.info("有效任务数: %d/%d", len(valid_items), len(task_items))

    # 创建内存管理器和 group_id 池
    memory_guard = MemoryGuard(args.memory_limit_gb, args.vm_memory)
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

    completed_count = 0
    total_count = len(valid_items)

    with ThreadPoolExecutor(
        max_workers=args.max_parallel_tasks,
        thread_name_prefix="Worker",
    ) as executor:
        futures = {}

        for i, (task_uid, task_path, task_config) in enumerate(valid_items):
            task_share_urls = share_urls_map.get(task_uid, {})
            log.info("提交任务 %d/%d | %s (UID: %s, %d 个共享文档)",
                     i + 1, total_count,
                     task_config.get("task_id", ""), task_uid[:8],
                     len(task_share_urls))

            fut = executor.submit(
                run_single_task,
                task_uid, task_path, task_config, task_share_urls,
                available_groups, args, memory_guard,
                output_results, results_lock, output_json_path,
            )
            futures[fut] = (task_uid, i + 1)

        # 收集结果
        for fut in as_completed(futures):
            task_uid, _index = futures[fut]
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

            # 打印状态
            evaluator_output = task_result.get("evaluator_output")
            if task_result.get("interrupted"):
                status = "INTERRUPTED"
            elif evaluator_output and evaluator_output.get("pass"):
                status = "PASS"
            else:
                status = "FAIL"

            task_id = task_result.get("task_id", "")
            score_str = ""
            if evaluator_output and "score" in evaluator_output:
                score_str = f" (score: {evaluator_output['score']:.2f})"

            log.info("任务完成 %d/%d | %s | 状态: %s%s",
                     completed_count, total_count, task_id, status, score_str)

            # 实时持久化
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

    log.info("=" * 80)
    log.info("全部任务执行完成 (%d/%d)", completed_count, total_count)
    log.info("输出结果文件: %s", output_json_path)
    log.info("=" * 80)

    # 统计汇总
    passed = sum(
        1 for r in output_results.values()
        if r.get("evaluator_output") and r.get("evaluator_output", {}).get("pass")
    )
    interrupted = sum(1 for r in output_results.values() if r.get("interrupted"))
    failed = total_count - passed - interrupted

    log.info("统计: 通过 %d | 失败 %d | 中断 %d | 总计 %d",
             passed, failed, interrupted, total_count)

    # 输出详细得分
    log.info("-" * 60)
    for uid, res in sorted(output_results.items(), key=lambda x: x[1].get("task_id", "")):
        tid = res.get("task_id", uid[:8])
        ev = res.get("evaluator_output")
        if res.get("interrupted"):
            log.info("  %s: INTERRUPTED (%s)", tid, res.get("interrupt_reason", ""))
        elif ev:
            log.info("  %s: %s (score=%.2f, cells=%d/%d)",
                     tid,
                     "PASS" if ev.get("pass") else "FAIL",
                     ev.get("score", 0),
                     ev.get("matched_cells", 0),
                     ev.get("total_cells", 0))
        else:
            log.info("  %s: NO_EVAL", tid)


if __name__ == "__main__":
    main()
