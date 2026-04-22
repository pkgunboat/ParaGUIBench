"""
WebMall 批量任务 Pipeline
包含：
1. Docker 容器自动重建
2. VM 初始化（挂载 shared）
3. Agent 执行任务
4. 评估（string → 收藏夹 URL 精确匹配；cart → AT 检测购物车商品 slug；checkout → AT 验证订单确认页）
5. 汇总输出完整 JSON 结果

与 run_QA_pipeline.py 的区别：
- 任务来源：extra_docker_env/tasks/（WebMall 任务）
- 无需 HuggingFace 文件下载（商店运行在宿主机 9081-9084）
- 评估方式根据 answer_type 区分（string / cart / checkout）
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import threading

import requests

# ===== 路径设置 =====
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

from desktop_env.controllers.python import PythonController
from parallel_agents.plan_agent_thought_action import PlanAgentThoughtAction, calculate_cost
from config.api_config import get_api_config, get_model_name
from config_loader import resolve_host_ip

# WebMall: 基于收藏夹(Bookmarks)的 string 任务评测辅助工具（外层仓库副本）
from webmall_eval_assets.bookmark_utils import close_chrome_and_clear_bookmarks, read_bookmark_urls

# WebMall: 基于 Accessibility Tree 的 Cart / Checkout 评价器
from webmall_eval_assets.cart_evaluator_from_at import (
    create_checkpoints_from_urls,
    detect_vm_all_carts,
    evaluate_all_vms,
)
from webmall_eval_assets.checkout_evaluator_from_at import (
    ExpectedCheckout,
    extract_checkout_info,
    extract_checkout_info_with_recovery,
    get_at as get_checkout_at,
    verify_checkout,
)


# ===== 全局配置 =====
# VM_IP 自动探测当前设备默认路由出口 IP；通过 configs/deploy.yaml
# 的 services.webmall.host_ip 可覆盖（见 DeployConfig.webmall_host）。
VM_IP = resolve_host_ip("auto")
WEBMALL_TASKS_DIR = os.path.join(extra_docker_env_dir, "tasks")
OUTPUT_JSON_PATH = os.path.join(ubuntu_env_dir, "logs", "run_webmall_pipeline_all.json")

# 默认测试任务列表（每种类型各选一个 expected_urls 最少的任务）
DEFAULT_TASK_UIDS = [
    "03f89eaeaaaa47aaa48f83c5f8a2ff47",  # string: CheapestProductSearch (1 URL)
    "3feea8377a87475e8a95f93af0e13738",  # cart:   FindSubstitutes (1 URL)
    "232c21f186804b808fea91e18ccec092",  # checkout: Checkout (1 URL)
]

# 从第几个任务开始执行（1-based，含该任务）
START_INDEX = 1

# 任务间环境重置策略：
#   "rebuild"  — 每个任务前重建 Docker 容器（最彻底，约 70-100 秒/任务）
#   "clean"    — 仅清空浏览器状态（快速，约 5-10 秒/任务，不重建容器）
RESET_MODE = "rebuild"


# =====================================================================
# 一、环境检查与工具函数
# =====================================================================

def ensure_conda_env(expected_env: str, strict: bool = True) -> None:
    """
    检查当前 conda 环境是否符合预期。

    输入:
        expected_env: 期望的 conda 环境名称
        strict: 是否严格校验（True 不匹配直接退出）
    输出:
        None（不符合时直接退出）
    """
    if not expected_env:
        return
    current_env = os.environ.get("CONDA_DEFAULT_ENV", "")
    if current_env != expected_env:
        print(f"⚠️ 当前 conda 环境为: {current_env or 'None'}，期望: {expected_env}")
        if strict:
            print("请切换到正确的 conda 环境后再运行该脚本。")
            sys.exit(1)


def ensure_sshpass_available() -> bool:
    """
    检查 sshpass 是否可用，必要时尝试自动安装。

    输入: None
    输出: bool（True 表示可用）
    """
    if shutil.which("sshpass"):
        return True
    print("\n✗ 错误: sshpass 未安装")
    print("  尝试自动安装: sudo apt-get update && sudo apt-get install -y sshpass")
    try:
        subprocess.run(["sudo", "apt-get", "update", "-qq"], capture_output=True, text=True, timeout=300)
        subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "sshpass"], capture_output=True, text=True, timeout=300)
        if shutil.which("sshpass"):
            print("  ✓ sshpass 已安装")
            return True
        print("  ✗ 安装完成但仍未找到 sshpass")
        return False
    except Exception as exc:
        print(f"  ✗ 自动安装失败: {exc}")
        return False


def execute_on_vm(vm_port: int, command: str, timeout: int = 60) -> Dict[str, Any]:
    """
    在指定 VM 上执行命令。

    输入:
        vm_port: VM 端口
        command: 要执行的命令
        timeout: 请求超时时间
    输出:
        dict，包含 status/returncode/output/error 等信息
    """
    url = f"http://{VM_IP}:{vm_port}/execute"
    payload = {"command": command}
    try:
        response = requests.post(url, json=payload, timeout=timeout)
        result = response.json()
        if result.get("returncode", -1) != 0:
            error_msg = result.get("error", "") or result.get("output", "")
            return {"status": "error", "error": error_msg, "returncode": result.get("returncode")}
        return result
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def wait_for_vm_ready(vm_port: int, max_wait: int = 30) -> bool:
    """
    检查 VM 是否就绪（可响应命令）。

    输入:
        vm_port: VM 端口
        max_wait: 最大等待秒数
    输出:
        bool
    """
    for _ in range(max_wait):
        try:
            result = execute_on_vm(vm_port, "echo ready", timeout=10)
            if result.get("status") == "success" or result.get("returncode") == 0:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# =====================================================================
# 二、任务加载
# =====================================================================

def load_task_config(task_path: str) -> Dict[str, Any]:
    """
    读取任务 JSON 配置。

    输入:
        task_path: 任务 JSON 路径
    输出:
        任务配置字典
    """
    with open(task_path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def scan_webmall_tasks(
    tasks_dir: str,
    task_uids: Optional[List[str]] = None,
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    扫描 WebMall 任务目录，筛选指定任务。

    输入:
        tasks_dir: 任务目录路径
        task_uids: 如果指定，只加载这些 task_uid 的任务；为 None 则加载全部
    输出:
        [(task_uid, task_path, task_config), ...]
    """
    if not os.path.isdir(tasks_dir):
        raise FileNotFoundError(f"未找到任务目录: {tasks_dir}")

    all_tasks: List[Tuple[str, str, Dict[str, Any]]] = []
    for filename in os.listdir(tasks_dir):
        if not filename.endswith(".json"):
            continue
        task_path = os.path.join(tasks_dir, filename)
        try:
            task_config = load_task_config(task_path)
        except Exception:
            continue
        task_uid = task_config.get("task_uid", "")
        if not task_uid:
            continue
        # 如果指定了 task_uids，只加载指定的
        if task_uids is not None and task_uid not in task_uids:
            continue
        all_tasks.append((task_uid, task_path, task_config))

    # 按 task_uids 的顺序排列（如果指定了）
    if task_uids is not None:
        uid_order = {uid: i for i, uid in enumerate(task_uids)}
        all_tasks.sort(key=lambda item: uid_order.get(item[0], 999))
    else:
        all_tasks.sort(key=lambda item: (item[2].get("task_id", ""), item[0]))

    return all_tasks


# =====================================================================
# 二点五、收藏夹(Bookmarks)重置（用于 string 任务评测）
# =====================================================================

def clear_bookmarks_on_all_vms(vm_ip: str = VM_IP, port_start: int = 5000, port_end: int = 5004) -> Dict[int, Dict[str, Any]]:
    """在所有 VM 上关闭浏览器并清空 Bookmarks(url 节点)。"""
    results: Dict[int, Dict[str, Any]] = {}
    for port in range(port_start, port_end + 1):
        controller = PythonController(vm_ip=vm_ip, server_port=port)
        try:
            results[port] = close_chrome_and_clear_bookmarks(controller)
        except Exception as exc:
            results[port] = {"ok": False, "error": str(exc), "server_port": port}
    return results

# =====================================================================
# 三、环境初始化（Docker 容器重建 + VM 初始化）
# =====================================================================

def ensure_docker_image_with_sshfs(
    ssh_password: str,
    ssh_opts: List[str],
    conda_activate: str,
    base_image: str,
    target_image: str,
    ssh_host: str = "",
) -> bool:
    """
    确保远端存在预装 sshfs 的 Docker 镜像。

    输入:
        ssh_password: 远端 sudo 密码
        ssh_opts: SSH 连接选项
        conda_activate: 远端 conda 激活命令
        base_image / target_image: 镜像标签
        ssh_host: 远端主机
    输出:
        bool
    """
    print("\n[0/3] 检查预装 sshfs 的镜像...")
    inspect_cmd = (
        f"{conda_activate} && echo {ssh_password} | sudo -S "
        f"docker image inspect {target_image} >/dev/null 2>&1"
    )
    result = subprocess.run(
        ["sshpass", "-p", ssh_password, "ssh"] + ssh_opts + [ssh_host, inspect_cmd],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        print(f"  ✓ 镜像已存在: {target_image}")
        return True

    print(f"  ⏳ 未找到镜像 {target_image}，开始构建...")
    dockerfile_text = (
        f"FROM {base_image}\n"
        "RUN apt-get update -qq "
        "&& DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sshfs \\\n"
        "    && apt-get clean && rm -rf /var/lib/apt/lists/*\n"
    )
    build_cmd = (
        f"{conda_activate} && echo {ssh_password} | sudo -S bash -c "
        f"\"cat <<'EOF' | docker build -t {target_image} -\n"
        f"{dockerfile_text}"
        "EOF\""
    )
    result = subprocess.run(
        ["sshpass", "-p", ssh_password, "ssh"] + ssh_opts + [ssh_host, build_cmd],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        print("  ✗ 镜像构建失败")
        return False
    print(f"  ✓ 镜像构建完成: {target_image}")
    return True


def rebuild_containers() -> bool:
    """
    通过 SSH 自动重建 Docker 容器（无交互）。

    输入: None
    输出: bool（是否重建成功）
    """
    print("\n" + "=" * 80)
    print("STAGE 1-0: 自动重建 Docker 容器（VNC 8006-8010）")
    print("=" * 80)

    if not ensure_sshpass_available():
        return False

    # 机密/路径/主机从 configs/deploy.yaml + env 读取
    import sys as _sys
    _src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _src_dir not in _sys.path:
        _sys.path.insert(0, _src_dir)
    from config_loader import DeployConfig, get_ssh_password
    _deploy = DeployConfig()
    base_image = os.environ.get("BENCH_BASE_IMAGE", "happysixd/osworld-docker")
    docker_image = os.environ.get("BENCH_DOCKER_IMAGE", "happysixd/osworld-docker-sshfs")
    ssh_password = get_ssh_password()
    if not ssh_password:
        print("✗ 未设置 SSH 密码。请 export BENCH_SSH_PASSWORD=<password>")
        return False
    qcow2_path = _deploy.qcow2_path
    shared_path = _deploy.shared_base_dir
    vm_user = _deploy.vm_user
    vm_host = _deploy.vm_host
    ssh_host = f"{vm_user}@{vm_host}"

    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
    ]
    conda_activate = os.environ.get(
        "BENCH_CONDA_ACTIVATE",
        f"source /home/{vm_user}/miniconda3/etc/profile.d/conda.sh && conda activate tonggui",
    )

    containers = [
        {"name": "osworld-vm1", "server_port": 5000, "vnc_port": 8006, "chromium_port": 9220, "vlc_port": 8080},
        {"name": "osworld-vm2", "server_port": 5001, "vnc_port": 8007, "chromium_port": 9221, "vlc_port": 8081},
        {"name": "osworld-vm3", "server_port": 5002, "vnc_port": 8008, "chromium_port": 9222, "vlc_port": 8082},
        {"name": "osworld-vm4", "server_port": 5003, "vnc_port": 8009, "chromium_port": 9223, "vlc_port": 8083},
        {"name": "osworld-vm5", "server_port": 5004, "vnc_port": 8010, "chromium_port": 9224, "vlc_port": 8084},
    ]

    try:
        if not ensure_docker_image_with_sshfs(
            ssh_password=ssh_password,
            ssh_opts=ssh_opts,
            conda_activate=conda_activate,
            base_image=base_image,
            target_image=docker_image,
        ):
            return False

        # 删除旧容器
        print("\n[1/3] 查找并删除占用端口或同名容器...")
        required_ports = [5000, 5001, 5002, 5003, 5004, 8006, 8007, 8008, 8009, 8010]
        required_names = [c["name"] for c in containers]

        cmd = f"{conda_activate} && echo {ssh_password} | sudo -S docker ps -a --format '{{{{.Names}}}}|||{{{{.Ports}}}}'"
        result = subprocess.run(
            ["sshpass", "-p", ssh_password, "ssh"] + ssh_opts + [ssh_host, cmd],
            capture_output=True, text=True, timeout=10,
        )
        containers_to_delete = []
        for line in result.stdout.strip().split("\n"):
            if not line or vm_user in line:
                continue
            parts = line.split("|||")
            name = parts[0]
            ports = parts[1] if len(parts) > 1 else ""
            if name in required_names:
                if name not in containers_to_delete:
                    containers_to_delete.append(name)
                continue
            for port in required_ports:
                if f":{port}->" in ports:
                    if name not in containers_to_delete:
                        containers_to_delete.append(name)
                    break

        if containers_to_delete:
            container_names = " ".join(containers_to_delete)
            cmd = f"{conda_activate} && echo {ssh_password} | sudo -S docker rm -f {container_names} 2>&1"
            subprocess.run(
                ["sshpass", "-p", ssh_password, "ssh"] + ssh_opts + [ssh_host, cmd],
                capture_output=True, text=True, timeout=60,
            )
            print(f"  ✓ 删除完成: {', '.join(containers_to_delete)}")
        else:
            print("  未发现需要删除的容器")

        # 启动新容器
        print("\n[2/3] 启动新容器...")
        for c in containers:
            cmd = f"""{conda_activate} && echo {ssh_password} | sudo -S docker run -d \
                --name {c['name']} \
                -p {c['server_port']}:5000 \
                -p {c['vnc_port']}:8006 \
                -p {c['chromium_port']}:9222 \
                -p {c['vlc_port']}:8080 \
                --shm-size=2g --cap-add=NET_ADMIN --device=/dev/kvm \
                -v {qcow2_path}:/System.qcow2:ro \
                -v {shared_path}:/shared \
                {docker_image}"""
            result = subprocess.run(
                ["sshpass", "-p", ssh_password, "ssh"] + ssh_opts + [ssh_host, cmd],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                print(f"  ✓ {c['name']} 启动成功")
            else:
                print(f"  ✗ {c['name']} 启动失败")
                return False

        print("⏳ 等待容器稳定（15秒）...")
        time.sleep(15)

        # 检查 sshfs
        print("\n[3/3] 检查 sshfs 是否已内置...")
        processes = []
        for c in containers:
            cmd = (
                f"{conda_activate} && echo {ssh_password} | sudo -S docker exec {c['name']} "
                "bash -c 'which sshfs'"
            )
            proc = subprocess.Popen(
                ["sshpass", "-p", ssh_password, "ssh"] + ssh_opts + [ssh_host, cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            processes.append((c["name"], proc))

        all_success = True
        for name, proc in processes:
            stdout, stderr = proc.communicate(timeout=180)
            if proc.returncode == 0 and "/sshfs" in stdout:
                print(f"  ✓ {name} - sshfs 已安装")
            else:
                print(f"  ✗ {name} - sshfs 安装失败: {stderr[:200]}")
                all_success = False

        return all_success

    except FileNotFoundError:
        print("\n✗ 错误: sshpass 未安装")
        return False
    except subprocess.TimeoutExpired:
        print("\n✗ SSH 连接超时")
        return False
    except Exception as exc:
        print(f"\n✗ 重建容器失败: {exc}")
        return False


def init_vm(vm_port: int, vnc_port: int, rebuilt: bool = False) -> bool:
    """
    初始化单个 VM（挂载 shared 目录，WebMall 不需要下载数据文件）。

    输入:
        vm_port: VM 端口
        vnc_port: VNC 端口
        rebuilt: 是否为重建后首次初始化
    输出:
        bool
    """
    print(f"\n{'=' * 60}")
    print(f"初始化 VM (port {vm_port}, VNC http://{VM_IP}:{vnc_port}/)")
    print(f"{'=' * 60}")

    wait_time = 30 if rebuilt else 5
    if not wait_for_vm_ready(vm_port, max_wait=wait_time):
        print(f"  ✗ VM {vm_port} 无法响应")
        return False

    # 检查 sshfs
    print("[1/4] 检查 sshfs...")
    result = execute_on_vm(vm_port, "which sshfs")
    if result.get("status") != "success":
        _ = execute_on_vm(vm_port, 'bash -c "echo password | sudo -S systemctl stop packagekit || true; echo password | sudo -S systemctl disable packagekit || true"')
        execute_on_vm(vm_port, 'bash -c "echo password | sudo -S apt update -qq"')
        result = execute_on_vm(vm_port, 'bash -c "echo password | sudo -S DEBIAN_FRONTEND=noninteractive apt install -y -qq sshfs"')
        if result.get("status") != "success":
            print(f"  ✗ 安装 sshfs 失败: {result.get('error', '')}")
            return False
        print("  ✓ sshfs 安装完成")
    else:
        print("  ✓ sshfs 已安装")

    # 准备 shared 目录
    print("[2/4] 准备 shared 目录...")
    cmd = 'bash -c "echo password | sudo -S fusermount3 -u /home/user/shared 2>/dev/null; mkdir -p /home/user/shared"'
    execute_on_vm(vm_port, cmd)
    print("  ✓ 完成")

    # 挂载 shared
    print("[3/4] 挂载 shared 文件夹...")
    import sys as _sys
    _src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _src_dir not in _sys.path:
        _sys.path.insert(0, _src_dir)
    from config_loader import DeployConfig as _DC, get_ssh_password
    _deploy = _DC()
    _pw = get_ssh_password()
    if not _pw:
        print("✗ 未设置 SSH 密码")
        return False
    import base64 as _b64
    _pw_b64 = _b64.b64encode(_pw.encode()).decode()
    cmd = (
        f'bash -c "echo {_pw_b64} | base64 -d | sshfs '
        f'{_deploy.vm_user}@{_deploy.vm_host}:{_deploy.shared_base_dir} /home/user/shared '
        f'-o password_stdin -o StrictHostKeyChecking=no"'
    )
    result = execute_on_vm(vm_port, cmd)
    if result.get("status") != "success":
        print(f"  ✗ 挂载失败: {result.get('error', '')}")
        return False
    print("  ✓ 完成")

    # 验证
    print("[4/4] 验证 shared 挂载...")
    result = execute_on_vm(vm_port, "ls /home/user/shared")
    if result.get("status") != "success":
        print(f"  ✗ 验证失败: {result.get('error', '')}")
        return False
    print("  ✓ 完成")

    print(f"\n✅ VM {vm_port} 初始化成功！")
    print(f"   VNC: http://{VM_IP}:{vnc_port}/?resize=scale&reconnect=true&autoconnect=true")
    return True


def clean_browser_on_all_vms() -> None:
    """
    在所有 VM 中清空浏览器状态（不重建容器的轻量级清理方案）。

    功能：Kill Chrome 进程 → 删除 Session/Tabs/Cookies/History/Bookmarks 等文件
    输入：无
    输出：无
    """
    print("\n[PreTask] 清空所有 VM 的浏览器状态...")
    clean_script = (
        "import subprocess, os, glob, time\n"
        "# 1. Kill 所有 Chrome/Chromium 进程\n"
        "for proc_name in ['google-chrome', 'chromium', 'chrome', 'chromium-browser']:\n"
        "    subprocess.run(['pkill', '-9', '-f', proc_name], capture_output=True)\n"
        "time.sleep(0.5)\n"
        "\n"
        "# 2. 清理 Chrome profile 数据文件\n"
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
        "    # 清理 Cache 目录\n"
        "    cache_dir = os.path.join(profile_dir, 'Cache')\n"
        "    if os.path.isdir(cache_dir):\n"
        "        subprocess.run(['rm', '-rf', cache_dir], capture_output=True)\n"
        "        removed += 1\n"
        "print(f'cleaned:{removed}')\n"
    )

    for vm_port in [5000, 5001, 5002, 5003, 5004]:
        try:
            url = f"http://{VM_IP}:{vm_port}/execute"
            resp = requests.post(url, json={"command": clean_script}, timeout=30)
            if resp.status_code == 200:
                output = resp.json().get("output", "")
                print(f"  ✓ VM {vm_port} 浏览器已清理 ({output.strip()})")
            else:
                print(f"  ⚠ VM {vm_port} 浏览器清理失败 (HTTP {resp.status_code})")
        except Exception as exc:
            print(f"  ⚠ VM {vm_port} 浏览器清理失败: {exc}")


def open_browser_on_all_vms(start_url: str = "https://www.bing.com") -> None:
    """
    在所有 VM 中打开 Google Chrome 浏览器，最大化窗口，并导航到指定首页。

    功能：启动 Chrome → 等待窗口出现 → 最大化 → 导航到 start_url
    输入：
        start_url: 浏览器启动后打开的首页 URL，默认为必应首页
    输出：无
    """
    print(f"\n[PostInit] 在所有 VM 中打开 Chrome 并导航到 {start_url}...")
    launch_script = (
        "import subprocess, time, os\n"
        "env = os.environ.copy()\n"
        "env['DISPLAY'] = ':0'\n"
        "# 启动 Chrome，打开指定 URL\n"
        f"subprocess.Popen(['google-chrome', '--no-first-run', '--no-default-browser-check', '{start_url}'], env=env)\n"
        "time.sleep(3)\n"
        "# 最大化窗口（Alt+F10 或 wmctrl）\n"
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

    for vm_port in [5000, 5001, 5002, 5003, 5004]:
        try:
            url = f"http://{VM_IP}:{vm_port}/execute"
            resp = requests.post(url, json={"command": launch_script}, timeout=20)
            if resp.status_code == 200:
                print(f"  ✓ VM {vm_port} Chrome 已打开并最大化")
            else:
                print(f"  ⚠ VM {vm_port} Chrome 启动失败 (HTTP {resp.status_code})")
        except Exception as exc:
            print(f"  ⚠ VM {vm_port} Chrome 启动失败: {exc}")


def disable_screensaver_on_all_vms() -> None:
    """
    在所有 VM 中禁用屏保和锁屏，避免测试期间 VM 自动锁屏浪费 Agent 操作轮次。

    功能：通过 PythonController 在每个 VM 内执行 gsettings/xset 命令
    输入：无
    输出：无
    """
    print("\n[PostInit] 禁用所有 VM 的屏保和锁屏...")
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

    for vm_port in [5000, 5001, 5002, 5003, 5004]:
        try:
            url = f"http://{VM_IP}:{vm_port}/execute"
            # VM Python Server 要求列表格式: {"command": ["python", "-c", code], "shell": false}
            payload = json.dumps({"command": ["python", "-c", disable_script], "shell": False})
            resp = requests.post(url, headers={"Content-Type": "application/json"},
                                 data=payload, timeout=15)
            if resp.status_code == 200:
                output = resp.json().get("output", "")
                if "screensaver_disabled" in output:
                    print(f"  ✓ VM {vm_port} 屏保已禁用")
                else:
                    print(f"  ⚠ VM {vm_port} 屏保禁用返回异常: {output[:100]}")
            else:
                print(f"  ⚠ VM {vm_port} 屏保禁用失败 (HTTP {resp.status_code})")
        except Exception as exc:
            print(f"  ⚠ VM {vm_port} 屏保禁用失败: {exc}")


class ScreensaverHeartbeat:
    """
    防黑屏心跳守护线程。

    在任务执行期间，每隔 interval_sec 秒向所有 VM 发送 dbus-send + xset 命令，
    主动关闭屏保并重置空闲计时器。
    使用 `dbus-send SetActive false` + `xset s reset`：
      - dbus-send 直接通知 GNOME ScreenSaver 关闭（对黑屏和锁屏均有效）
      - xset s reset 重置 X11 屏保空闲计时器
    注意：VM 上未安装 xdotool，因此使用 dbus-send 替代。

    输入：
        vm_ip: VM 宿主 IP
        vm_ports: VM Python Server 端口列表
        interval_sec: 心跳间隔（秒），默认 180（3 分钟）
    """

    def __init__(
        self,
        vm_ip: str = VM_IP,
        vm_ports: Optional[List[int]] = None,
        interval_sec: int = 180,
    ):
        self.vm_ip = vm_ip
        self.vm_ports = vm_ports or [5000, 5001, 5002, 5003, 5004]
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _heartbeat_loop(self) -> None:
        """
        心跳循环主体，在后台线程中运行。
        每隔 interval_sec 秒向所有 VM 发送屏保重置命令。

        输入：无
        输出：无
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

        while not self._stop_event.is_set():
            # 等待 interval_sec 秒，或者被提前唤醒（stop）
            if self._stop_event.wait(timeout=self.interval_sec):
                break

            for port in self.vm_ports:
                try:
                    url = f"http://{self.vm_ip}:{port}/execute"
                    payload = json.dumps({"command": ["python", "-c", heartbeat_script], "shell": False})
                    requests.post(url, headers={"Content-Type": "application/json"},
                                  data=payload, timeout=10)
                except Exception:
                    pass  # 静默忽略，不影响主流程

    def start(self) -> None:
        """
        启动心跳守护线程。

        输入：无
        输出：无
        """
        if self._thread is not None and self._thread.is_alive():
            return  # 已在运行
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="screensaver-heartbeat",
            daemon=True,
        )
        self._thread.start()
        print(f"[ScreensaverHeartbeat] 已启动（间隔 {self.interval_sec}s）")

    def stop(self) -> None:
        """
        停止心跳守护线程。

        输入：无
        输出：无
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        print("[ScreensaverHeartbeat] 已停止")


def verify_vm_network() -> Dict[str, Dict[str, bool]]:
    """
    验证所有 VM 到 WebMall 商店的网络可达性。

    功能：在每个 VM 内通过 curl 测试 4 个商店 URL 的连通性
    输入：无
    输出：Dict[str, Dict[str, bool]]，如 {"5000": {"9081": True, "9082": False, ...}}
    """
    print("\n[PostInit] 验证 VM 到 WebMall 商店的网络可达性...")
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

    for vm_port in [5000, 5001, 5002, 5003, 5004]:
        port_key = str(vm_port)
        results[port_key] = {}
        try:
            script = check_script_template.format(
                ports=shop_ports, vm_ip=VM_IP
            )
            url = f"http://{VM_IP}:{vm_port}/execute"
            resp = requests.post(url, json={"command": script}, timeout=30)
            if resp.status_code == 200:
                resp_data = resp.json()
                output = resp_data.get("output", "")
                # 解析输出中的字典
                try:
                    import ast
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

    # 打印汇总
    all_ok = True
    for vm_port_str, port_results in results.items():
        failed_ports = [p for p, ok in port_results.items() if not ok]
        if failed_ports:
            all_ok = False
            print(f"  ⚠ VM {vm_port_str}: 无法访问商店端口 {', '.join(failed_ports)}")
        else:
            print(f"  ✓ VM {vm_port_str}: 所有商店可达")

    if all_ok:
        print("  ✓ 所有 VM 网络可达性正常")
    else:
        print("  ⚠ 部分 VM 网络可达性异常，任务执行可能受影响")

    return results


def reinitialize_vms(mode: Optional[str] = None) -> bool:
    """
    每个任务前重置 VM 环境，根据 mode 选择重置策略。

    功能：
      mode="rebuild" — 销毁旧容器 → 创建新容器 → 挂载 shared → 禁用锁屏 → 验证网络（最彻底）
      mode="clean"   — 仅清空浏览器状态 + 禁用锁屏（快速，不重建容器）
    输入：
      mode: 重置模式，默认使用全局 RESET_MODE
    输出：
      bool，是否成功
    """
    effective_mode = mode or RESET_MODE

    if effective_mode == "rebuild":
        print("\n" + "=" * 80)
        print("任务环境重置：重建容器 + 初始化 VM")
        print("=" * 80)

        # 1. 重建容器
        if not rebuild_containers():
            print("\n✗ 容器重建失败")
            return False

        # 2. 初始化所有 VM（挂载 shared）
        vms = [(5000, 8006), (5001, 8007), (5002, 8008), (5003, 8009), (5004, 8010)]
        success_count = 0
        for vm_port, vnc_port in vms:
            if init_vm(vm_port, vnc_port, rebuilt=True):
                success_count += 1
            else:
                print(f"\n⚠️  VM {vm_port} 初始化失败，继续下一个...")

        if success_count < len(vms):
            print(f"\n⚠️ 仅 {success_count}/{len(vms)} 个 VM 初始化成功")

        # 3. 禁用锁屏/屏保
        disable_screensaver_on_all_vms()

        # 4. 打开 Chrome 并最大化，导航到必应首页
        open_browser_on_all_vms()

        # 5. 验证网络可达性
        verify_vm_network()

        print(f"\n✅ 环境重置完成（rebuild）：{success_count}/{len(vms)} 个 VM 就绪")
        return success_count == len(vms)

    elif effective_mode == "clean":
        print("\n" + "=" * 80)
        print("任务环境重置：清空浏览器状态（轻量模式）")
        print("=" * 80)

        # 1. 清空所有 VM 的浏览器状态
        clean_browser_on_all_vms()

        # 2. 禁用锁屏/屏保（每次都执行，因为 VM 可能被重新激活过）
        disable_screensaver_on_all_vms()

        # 3. 打开 Chrome 并最大化，导航到必应首页
        open_browser_on_all_vms()

        print("\n✅ 环境重置完成（clean）")
        return True

    else:
        print(f"\n✗ 未知的 RESET_MODE: {effective_mode}，支持 'rebuild' 或 'clean'")
        return False


def stage1_initialize() -> bool:
    """
    Stage 1: 首次环境初始化（始终使用 rebuild 模式创建容器）。
    WebMall 任务不需要下载数据文件，因此比 QA Pipeline 更简洁。

    输入: None
    输出: bool
    """
    print("\n" + "=" * 80)
    print("STAGE 1: 首次环境初始化（WebMall 模式：无需下载数据）")
    print("=" * 80)

    # 首次初始化始终使用 rebuild 模式，确保容器存在
    return reinitialize_vms(mode="rebuild")


# =====================================================================
# 四、Agent 执行
# =====================================================================

def load_run_plan_agent_module():
    """
    动态加载 run_plan_agent_thought_action.py 模块。

    输入: None
    输出: module
    """
    runner_path = os.path.join(parallel_benchmark_dir, "process", "run_plan_agent_thought_action.py")
    if not os.path.exists(runner_path):
        raise FileNotFoundError(f"未找到运行器文件: {runner_path}")
    spec = importlib.util.spec_from_file_location("run_plan_agent_thought_action", runner_path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("无法加载运行器模块")
    spec.loader.exec_module(module)
    return module


BOOKMARK_EVAL_MODE_MARKER = "BOOKMARK_EVAL_MODE"


def rewrite_webmall_string_instruction(task_instruction: str) -> str:
    """
    将 WebMall string 类任务的 instruction 从“输出URL列表”改为“把答案页加入收藏夹”。

    设计原则：
    - 评测侧只读取收藏夹 URL，因此不要求 Agent 在 final_answer 里手抄 URL（避免 OCR/抄写错误）。
    - 为避免重复改写，本函数会检测标记串 `BOOKMARK_EVAL_MODE_MARKER`，已存在则直接返回原文。

    输入:
        task_instruction: 原始 instruction 文本
    输出:
        str: 改写后的 instruction 文本
    """
    if not task_instruction:
        return task_instruction
    if BOOKMARK_EVAL_MODE_MARKER in task_instruction:
        return task_instruction

    # 尝试裁剪掉“最终汇报URL列表”的尾部段落（不同任务模板可能略有差异）
    patterns = [
        r"(?is)\\nAfter completing the task,.*$",
        r"(?is)\\nAfter solving the task,.*$",
    ]
    trimmed = task_instruction
    for pat in patterns:
        m = re.search(pat, trimmed)
        if m:
            trimmed = trimmed[: m.start()].rstrip()
            break

    suffix = (
        f"\n\n[{BOOKMARK_EVAL_MODE_MARKER}]\n"
        "After completing the task:\n"
        "- Do NOT manually type or copy any URL into the final answer.\n"
        "- For each correct product offer page you find, add it to Chrome bookmarks (Ctrl+D) and confirm it is saved.\n"
        "- If there are multiple correct offers, bookmark all of them.\n"
        '- When you are done, respond with "Done".\n'
    )
    return trimmed + suffix


def stage2_execute_agent(
    task_config: Dict[str, Any],
    task_uid: str,
) -> Tuple[Dict[str, Any], PythonController]:
    """
    Stage 2: 执行 Agent 任务。

    输入:
        task_config: 任务配置
        task_uid: 任务 UID
    输出:
        (result, controller_vm1)
    """
    print("\n" + "=" * 80)
    print("STAGE 2: Agent 执行任务")
    print("=" * 80)

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

    # 仅对 string 任务改写 instruction：用“收藏夹”代替“手抄URL列表”
    if task_config.get("answer_type") == "string":
        task_instruction = rewrite_webmall_string_instruction(task_instruction)

    print(f"\n任务描述:\n{task_instruction}\n")

    runner = load_run_plan_agent_module()
    controller_vm1, vm_controllers, registry = runner.setup_environment()
    api_config = get_api_config("deerapi")
    planner = PlanAgentThoughtAction(
        controller=controller_vm1,
        registry=registry,
        vm_controllers=vm_controllers,
        api_key=api_config["api_key"],
        base_url=api_config["base_url"],
        disable_code_agent=False,
        max_workers=5,
        coordinator_model=get_model_name("plan_agent"),
    )

    start_time = time.time()
    result = planner.execute_task(
        task=task_instruction,
        max_rounds=10,
        # 单次 GUI Agent 调用的最大轮次（每个 tool call 内部循环步数上限）
        max_rounds_per_subtask=50,
        timeout_per_subtask=600,
    )
    elapsed_time = time.time() - start_time
    print(f"\n执行完成，耗时: {elapsed_time:.2f}s")

    # 保存执行记录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(ubuntu_env_dir, "logs")
    os.makedirs(output_dir, exist_ok=True)
    record_path = os.path.join(output_dir, f"webmall_execution_{task_uid}_{timestamp}.json")
    if planner.recorder:
        try:
            planner.recorder.save_to_file(record_path)
            print(f"✓ 执行记录已保存: {record_path}")
        except Exception as exc:
            print(f"⚠️  保存执行记录失败: {exc}")

    return result, controller_vm1


# =====================================================================
# 五、评估模块
# =====================================================================

def normalize_url(url: str) -> str:
    """
    归一化 URL：去除末尾斜杠和协议前缀。

    输入: url 字符串
    输出: 归一化后的 URL
    """
    url = url.strip().rstrip("/")
    for prefix in ("http://", "https://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    return url


def parse_agent_answer_urls(final_answer: str) -> List[str]:
    """
    从 Agent 的 final_answer 中解析 URL 列表。
    Agent 被要求以 ### 分隔多个 URL。

    输入:
        final_answer: Agent 返回的最终答案文本
    输出:
        URL 列表
    """
    if not final_answer:
        return []

    # 去除 "Done" 等无结果标识
    stripped = final_answer.strip()
    if stripped.lower() in ("done", "none", "n/a", "no result", "no results"):
        return []

    # 尝试 ### 分隔
    if "###" in stripped:
        parts = stripped.split("###")
    else:
        # 回退：按换行或空格拆分
        parts = re.split(r'[\n\s]+', stripped)

    urls = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 提取 URL 模式
        url_match = re.search(r'https?://[^\s<>"\']+', part)
        if url_match:
            urls.append(url_match.group(0))
        elif re.match(r'[\w.-]+:\d+/', part):
            urls.append(f"http://{part}")

    return urls


def evaluate_string_task(
    expected_urls: List[str],
    submitted_urls: List[str],
) -> Dict[str, Any]:
    """
    评估 string 类型任务：对比 Agent 提交的 URL 与标准答案 URL（归一化后精确匹配）。

    输入:
        expected_urls: 标准答案 URL 列表
        submitted_urls: Agent 提交的 URL 列表
    输出:
        评估结果字典
    """
    norm_expected = {normalize_url(u): u for u in expected_urls}
    norm_submitted = {normalize_url(u): u for u in submitted_urls}

    matched = []
    wrong = []
    for norm_sub, orig_sub in norm_submitted.items():
        if norm_sub in norm_expected:
            matched.append(orig_sub)
        else:
            wrong.append(orig_sub)

    matched_norm = set(normalize_url(u) for u in matched)
    missing = [u for u in expected_urls if normalize_url(u) not in matched_norm]

    matched_count = len(matched)
    max_score = len(expected_urls)
    recall = matched_count / max_score if max_score > 0 else 0.0
    precision = matched_count / len(submitted_urls) if submitted_urls else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # 严格判定：recall=1（全部期望 URL 匹配）且 precision=1（无多余 URL）
    passed = (matched_count == max_score and len(wrong) == 0) if max_score > 0 else False

    # 归一化 score 到 [0, 1]，与 pipeline_base 的 pass 判定 (score == 1.0) 兼容
    score = matched_count / max_score if max_score > 0 else 0.0

    return {
        "score": score,
        "matched_count": matched_count,
        "max_score": max_score,
        "passed": passed,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "matched_urls": matched,
        "wrong_urls": wrong,
        "missing_urls": missing,
        "detail": f"匹配 {matched_count}/{max_score} 个期望 URL，错误提交 {len(wrong)} 个",
    }


def get_chrome_url_from_accessibility_tree(
    vm_ip: str, server_port: int, timeout: int = 30,
) -> Optional[str]:
    """
    从 Accessibility Tree 提取 Chrome 地址栏 URL（不依赖 chromium_port）。

    输入:
        vm_ip: 虚拟机 IP
        server_port: Python Server 端口
        timeout: 超时时间
    输出:
        Chrome 地址栏 URL，未找到返回 None
    """
    try:
        resp = requests.get(f"http://{vm_ip}:{server_port}/accessibility", timeout=timeout)
        if resp.status_code != 200:
            return None
        at_xml = resp.json().get("AT", "")
        if not at_xml:
            return None
        entry_matches = re.findall(r'<entry[^>]*>([^<]+)</entry>', at_xml)
        for match in entry_matches:
            match = match.strip()
            if re.match(r'[\w.-]+:\d+/', match) or re.match(r'[\w.-]+\.\w+/', match):
                return f"http://{match}" if not match.startswith("http") else match
        return None
    except Exception:
        return None


def get_all_vm_chrome_urls(
    vm_ip: str = VM_IP,
    port_start: int = 5000,
    port_end: int = 5004,
) -> List[str]:
    """
    从所有 VM 实例获取 Chrome 地址栏 URL。

    输入:
        vm_ip: 虚拟机 IP
        port_start: 起始端口
        port_end: 结束端口
    输出:
        所有获取到的 URL 列表
    """
    urls = []
    for port in range(port_start, port_end + 1):
        url = get_chrome_url_from_accessibility_tree(vm_ip, port)
        if url:
            urls.append(url)
    return urls


# (placeholder) bookmark collection helpers
def evaluate_browser_url_task(
    expected_urls: List[str],
    answer_type: str,
    final_answer: str = "",
) -> Dict[str, Any]:
    """
    评估 cart/checkout 类型任务：
    1. 从 Agent 的 final_answer 中解析 URL
    2. 从各 VM 的 Chrome 地址栏获取 URL（通过 AT）
    3. 综合两个来源进行 URL 匹配

    输入:
        expected_urls: 标准答案 URL 列表
        answer_type: "cart" 或 "checkout"
        final_answer: Agent 返回的最终答案文本
    输出:
        评估结果字典
    """
    # 来源 1：Agent 返回的 URL
    answer_urls = parse_agent_answer_urls(final_answer)

    # 来源 2：VM 浏览器当前 URL
    print("  从各 VM 获取 Chrome 浏览器 URL...")
    browser_urls = get_all_vm_chrome_urls()
    for i, url in enumerate(browser_urls):
        print(f"    VM{i}: {url}")

    # 合并去重
    all_submitted = list(dict.fromkeys(answer_urls + browser_urls))

    # URL 匹配
    eval_result = evaluate_string_task(expected_urls, all_submitted)
    eval_result["answer_type"] = answer_type
    eval_result["answer_urls"] = answer_urls
    eval_result["browser_urls"] = browser_urls
    eval_result["detail"] = (
        f"[{answer_type}] {eval_result['detail']} | "
        f"answer提供{len(answer_urls)}个URL, 浏览器提供{len(browser_urls)}个URL"
    )
    return eval_result


def stage3_evaluate(
    task_config: Dict[str, Any],
    agent_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Stage 3: 结果评估，根据 answer_type 选择评估方式。

    输入:
        task_config: 任务配置
        agent_result: Stage2 的执行结果
    输出:
        评估结果字典
    """
    print("\n" + "=" * 80)
    print("STAGE 3: 结果评估")
    print("=" * 80)

    answer_type = task_config.get("answer_type", "string")
    expected_urls = task_config.get("expected_urls", [])

    # 提取 Agent 的 final_answer
    execution_record = agent_result.get("execution_record", {})
    final_answer = execution_record.get("summary", {}).get("final_answer", "").strip()
    if not final_answer:
        print("⚠️ 未获取到 final_answer，评估将使用空答案。")

    print(f"\n任务类型: {answer_type}")
    print(f"期望 URL ({len(expected_urls)} 个):")
    for i, url in enumerate(expected_urls, 1):
        print(f"  {i}. {url}")
    print(f"\nAgent final_answer:\n  {final_answer[:500]}{'...' if len(final_answer) > 500 else ''}")

    if answer_type == "string":
        # string 类型：不再依赖 Agent 手抄 URL；改为读取所有 VM 的收藏夹(Bookmarks)
        print("\nstring 任务评测方式：读取所有 VM 的收藏夹 URL（合并去重，仅保留 /product/）")

        per_vm_urls: Dict[int, List[str]] = {}
        errors: Dict[int, str] = {}
        all_urls: List[str] = []

        for port in range(5000, 5004 + 1):
            controller = PythonController(vm_ip=VM_IP, server_port=port)
            try:
                urls = read_bookmark_urls(controller)
            except Exception as exc:
                urls = []
                errors[port] = str(exc)
            per_vm_urls[port] = urls
            all_urls.extend(urls)

        merged_urls = list(dict.fromkeys(all_urls))
        merged_product_urls = [u for u in merged_urls if "/product/" in u]

        print(f"\n收藏夹合并后 URL 数量: {len(merged_urls)} (product: {len(merged_product_urls)})")
        for i, url in enumerate(merged_product_urls, 1):
            print(f"  {i}. {url}")

        eval_result = evaluate_string_task(expected_urls, merged_product_urls)
        eval_result["bookmark_urls"] = merged_product_urls
        eval_result["bookmark_all_urls"] = merged_urls
        eval_result["bookmark_per_vm_urls"] = per_vm_urls
        eval_result["bookmark_errors"] = errors
        eval_result["detail"] = (
            f"[bookmark] {eval_result['detail']} | "
            f"bookmarked_product_urls={len(merged_product_urls)}"
        )
    elif answer_type == "cart":
        # cart 类型：基于 AT 检测各 VM 各商店购物车中的商品 slug
        print("\ncart 任务评测方式：基于 Accessibility Tree 检测购物车内容")
        server_ports = list(range(5000, 5004 + 1))
        checkpoints = create_checkpoints_from_urls(expected_urls)

        all_cart_results: Dict[str, Any] = {}
        for port in server_ports:
            results = detect_vm_all_carts(VM_IP, port, VM_IP, wait_time=3.0)
            all_cart_results[f"{VM_IP}:{port}"] = results

        vm_eval_results = evaluate_all_vms(all_cart_results, checkpoints)

        matched_count = sum(1 for cp in checkpoints if cp.flag)
        total_expected = len(checkpoints)

        # 统计各 VM 中的多余（unexpected）商品数
        total_unexpected = sum(
            len(res.unexpected_products)
            for res in vm_eval_results.values()
        )

        # 严格判定：recall=1（全部期望商品匹配）且无多余商品
        passed = (matched_count == total_expected and total_unexpected == 0) if total_expected else False
        recall = matched_count / total_expected if total_expected else 0.0

        # 修正 precision：matched / (matched + unexpected)
        total_detected = matched_count + total_unexpected
        precision = matched_count / total_detected if total_detected > 0 else (1.0 if matched_count == 0 else 0.0)
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

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
        # checkout 类型：基于 AT 验证订单确认页（商品 + 账单信息）
        print("\ncheckout 任务评测方式：基于 Accessibility Tree 验证订单确认页")
        server_ports = list(range(5000, 5004 + 1))

        # 从 task_config 构建期望的 checkout 信息
        product_url = expected_urls[0] if expected_urls else ""
        from urllib.parse import urlparse as _urlparse
        product_slug = _urlparse(product_url).path.rstrip("/").split("/")[-1]
        user_details = task_config.get("user_details", {})
        expected_checkout = ExpectedCheckout(
            product_slug=product_slug,
            shop_port=_urlparse(product_url).port or 0,
            user_details=user_details,
        )
        print(f"  期望商品 slug: {product_slug}")
        print(f"  用户信息: {user_details}")

        port_results = []
        passed_any = False
        best_score = 0.0

        for port in server_ports:
            co_result = extract_checkout_info_with_recovery(VM_IP, port)
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

        # 打印每个 VM 端口的验证详情
        for pr in port_results:
            port = pr["port"]
            if pr.get("error"):
                print(f"  VM:{port} — {pr['error']}")
            elif pr.get("checks"):
                status_str = " ".join(
                    f"{'✓' if v else '✗'}{k}" for k, v in pr["checks"].items()
                )
                print(f"  VM:{port} — score={pr['score']:.1%} {status_str}")

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
        eval_result = {"score": 0, "max_score": 0, "detail": f"未知的 answer_type: {answer_type}"}

    # 打印结果
    print(f"\n{'─' * 60}")
    print(f"评估结果:")
    print(f"  得分: {eval_result.get('score', 0)}/{eval_result.get('max_score', 0)}")
    print(f"  召回率: {eval_result.get('recall', 0):.1%}")
    print(f"  精确率: {eval_result.get('precision', 0):.1%}")
    print(f"  F1: {eval_result.get('f1', 0):.2f}")
    print(f"  详情: {eval_result.get('detail', '')}")
    if eval_result.get("matched_urls"):
        print(f"  ✅ 匹配:")
        for url in eval_result["matched_urls"]:
            print(f"     {url}")
    if eval_result.get("missing_urls"):
        print(f"  ❌ 未匹配:")
        for url in eval_result["missing_urls"]:
            print(f"     {url}")
    print(f"{'─' * 60}")

    return eval_result


# =====================================================================
# 六、执行记录摘要提取
# =====================================================================

def extract_execution_summary(execution_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 execution_record 提取模型名、轮次与最终答案等摘要信息。

    输入:
        execution_record: PlanAgentThoughtAction 返回的 execution_record
    输出:
        摘要字典
    """
    plan_agent = execution_record.get("plan_agent", {})
    plan_agent_model = plan_agent.get("model_name", "")
    plan_agent_rounds = plan_agent.get("rounds", []) or []
    plan_agent_total_rounds = plan_agent.get("summary", {}).get("total_rounds")
    if plan_agent_total_rounds is None:
        plan_agent_total_rounds = len(plan_agent_rounds)

    last_round_output = ""
    if plan_agent_rounds:
        last_round = plan_agent_rounds[-1]
        model_prediction = last_round.get("model_prediction", {}) or {}
        last_round_output = model_prediction.get("response", "") or ""

    final_answer = execution_record.get("summary", {}).get("final_answer", "") or ""
    if not final_answer and last_round_output:
        final_answer = last_round_output

    gui_models: List[str] = []
    for device in execution_record.get("devices", []) or []:
        for agent in device.get("agents", []) or []:
            model_name = agent.get("model_name")
            if model_name and model_name not in gui_models:
                gui_models.append(model_name)

    return {
        "plan_agent_model": plan_agent_model,
        "plan_agent_total_rounds": plan_agent_total_rounds,
        "plan_agent_last_round_output": last_round_output,
        "model_output_answer": final_answer,
        "gui_agent_model": ", ".join(gui_models) if gui_models else "",
    }


# =====================================================================
# 七、WebMall 商店可达性检查
# =====================================================================

def check_webmall_shops() -> bool:
    """
    检查 4 个 WebMall 商店是否可访问。

    输入: None
    输出: bool（全部可访问返回 True）
    """
    print("\n检查 WebMall 商店可达性...")
    shops = [
        ("E-Store Athletes", 9081),
        ("TechTalk", 9082),
        ("CamelCases", 9083),
        ("Hardware Cafe", 9084),
    ]
    all_ok = True
    for name, port in shops:
        try:
            resp = requests.get(f"http://{VM_IP}:{port}", timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                print(f"  ✓ {name} (:{port}) - 正常")
            else:
                print(f"  ✗ {name} (:{port}) - HTTP {resp.status_code}")
                all_ok = False
        except Exception as exc:
            print(f"  ✗ {name} (:{port}) - 连接失败: {exc}")
            all_ok = False
    return all_ok


# =====================================================================
# 八、主流程
# =====================================================================

def main() -> None:
    """
    主流程：
    1. 环境检查（conda + WebMall 商店）
    2. 加载任务
    3. 环境初始化（Docker 容器重建）
    4. 逐任务执行 Agent + 评估
    5. 汇总输出
    """
    # conda 环境检查
    required_env = os.environ.get("REQUIRED_CONDA_ENV", "parallelbenchmark")
    strict_check = os.environ.get("REQUIRED_CONDA_ENV_STRICT", "1") == "1"
    ensure_conda_env(required_env, strict=strict_check)

    print("=" * 80)
    print("WebMall 批量任务 Pipeline")
    print("=" * 80)

    # 检查商店可达性
    if not check_webmall_shops():
        print("\n⚠️  部分 WebMall 商店不可达，请检查服务状态后再运行")
        sys.exit(1)

    # 加载任务
    task_items = scan_webmall_tasks(WEBMALL_TASKS_DIR, task_uids=DEFAULT_TASK_UIDS)
    print(f"\n共加载 {len(task_items)} 个 WebMall 任务:")
    for i, (uid, path, cfg) in enumerate(task_items, 1):
        print(f"  {i}. [{cfg.get('answer_type', '?')}] {cfg.get('task_tag', '')} | {uid}")

    # 环境初始化
    if not stage1_initialize():
        print("\n✗ 环境初始化失败，终止流程")
        sys.exit(1)

    # 启动防黑屏心跳守护线程（每 3 分钟向所有 VM 发送屏保重置命令）
    heartbeat = ScreensaverHeartbeat(interval_sec=180)
    heartbeat.start()

    # 执行任务
    output_results: Dict[str, Any] = {}
    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)

    for index, (task_uid, task_path, task_config) in enumerate(task_items, start=1):
        if index < START_INDEX:
            continue

        print("\n" + "#" * 80)
        print(f"任务 {index}/{len(task_items)} | UID: {task_uid}")
        print(f"类型: {task_config.get('answer_type', '?')} | 标签: {task_config.get('task_tag', '')}")
        print(f"配置文件: {task_path}")
        print("#" * 80)

        # ★ 每个任务前重建容器，确保完全干净的环境
        if not reinitialize_vms():
            print(f"\n⚠️  任务 {task_uid} 环境重置失败，跳过")
            output_results[task_uid] = {
                "task_uid": task_uid,
                "interrupted": True,
                "interrupt_reason": "reinitialize_vms_failed",
            }
            continue

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
            "token_usage": None,
        }

        task_result["bookmark_reset"] = {}
        # 对 string 类型任务：任务开始前清空所有 VM 的收藏夹 URL（容器已重建，作为安全网保留）
        if task_config.get("answer_type") == "string":
            print("\n[PreTask] 清空所有 VM 的 Chrome 收藏夹（仅删除 url 节点，保留文件夹结构）...")
            task_result["bookmark_reset"] = clear_bookmarks_on_all_vms()
        else:
            task_result["bookmark_reset"] = {}

        # Stage 2: 执行 Agent
        try:
            result, _ = stage2_execute_agent(task_config, task_uid)
        except Exception as exc:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = f"stage2_execute_exception: {exc}"
            output_results[task_uid] = task_result
            print(f"\n✗ Agent 执行失败: {exc}")
            continue

        # 提取执行摘要
        execution_record = result.get("execution_record", {}) if isinstance(result, dict) else {}
        if execution_record:
            summary_info = extract_execution_summary(execution_record)
            task_result.update(summary_info)
        else:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "missing_execution_record"

        # 提取 token 消耗数据并计算费用
        raw_token = result.get("token_usage") if isinstance(result, dict) else None
        if raw_token:
            plan_usage = raw_token.get("plan_agent", {})
            gui_usage = raw_token.get("gui_agent", {})
            plan_model = raw_token.get("plan_agent_model", "")
            # GUI Agent 模型：从 Plan Agent 返回值中动态获取
            gui_model = raw_token.get("gui_agent_model", "unknown")
            plan_cost = calculate_cost(plan_usage, plan_model)
            gui_cost = calculate_cost(gui_usage, gui_model)
            task_result["token_usage"] = {
                "plan_agent": {**plan_usage, "model": plan_model, "cost_usd": plan_cost["total_cost"]},
                "gui_agent": {**gui_usage, "model": gui_model, "cost_usd": gui_cost["total_cost"]},
                "total_cost_usd": plan_cost["total_cost"] + gui_cost["total_cost"],
            }

        # Stage 3: 评估
        try:
            eval_result = stage3_evaluate(task_config, result)
            task_result["evaluator_output"] = eval_result
        except Exception as exc:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = f"stage3_evaluate_exception: {exc}"
            print(f"\n✗ 评估失败: {exc}")

        output_results[task_uid] = task_result

        print("\n" + "=" * 80)
        print("当前任务执行完成")
        print("=" * 80)
        print(f"任务 UID: {task_uid}")
        print(f"评估得分: {task_result.get('evaluator_output', {}).get('score', 'N/A')}/{task_result.get('evaluator_output', {}).get('max_score', 'N/A')}")
        print("=" * 80)

    # 停止防黑屏心跳守护线程
    heartbeat.stop()

    # 保存结果
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as file_obj:
        json.dump(output_results, file_obj, ensure_ascii=False, indent=2)

    # 汇总
    print("\n" + "=" * 80)
    print("全部任务执行完成 - 汇总")
    print("=" * 80)

    total_cost_all = 0.0
    for uid, res in output_results.items():
        eval_out = res.get("evaluator_output") or {}
        # 判定通过条件：优先使用 passed 字段（cart/checkout），否则要求 score == max_score 且 max_score > 0
        if "passed" in eval_out:
            is_passed = eval_out["passed"]
        else:
            is_passed = eval_out.get("score", 0) == eval_out.get("max_score", 0) and eval_out.get("max_score", 0) > 0
        status = "✅" if is_passed else "❌"
        interrupted = " (中断)" if res.get("interrupted") else ""
        token_info = res.get("token_usage") or {}
        task_cost = token_info.get("total_cost_usd", 0.0)
        total_cost_all += task_cost
        cost_str = f" | 费用: ${task_cost:.4f}" if task_cost > 0 else ""
        print(
            f"  {status} {uid} [{res.get('answer_type', '?')}] "
            f"{res.get('task_tag', '')} "
            f"| 得分: {eval_out.get('score', 'N/A')}/{eval_out.get('max_score', 'N/A')}"
            f"{cost_str}{interrupted}"
        )

    if total_cost_all > 0:
        print(f"\n总 Token 费用: ${total_cost_all:.4f}")
    print(f"\n输出结果文件: {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
