"""
QA 批量任务 Pipeline（自动筛选 task_type=QA）
包含：
1. Docker 容器自动重建
2. VM 初始化（挂载 shared、下载任务数据到 /shared）
3. Agent 执行任务
4. 评估（默认使用 evaluator_path，缺失则使用 file_search_readonly_evaluator）
5. 汇总输出完整 JSON 结果
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List, Tuple, Any

import requests

import os


def run_ssh_command(ssh_password: str, ssh_opts: list, ssh_host: str, cmd: str, timeout: int = 30, env: dict = None) -> subprocess.CompletedProcess:
    """
    使用 sshpass 执行 SSH 命令（通过环境变量传递密码，避免特殊字符问题）
    """
    # 使用环境变量传递密码，避免特殊字符（如反引号）被 bash 解释
    ssh_env = os.environ.copy()
    ssh_env['SSHPASS'] = ssh_password
    if env:
        ssh_env.update(env)
    
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


# 添加 parallel_benchmark 到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
ubuntu_env_dir = os.path.dirname(current_dir)
parallel_benchmark_dir = os.path.join(ubuntu_env_dir, "parallel_benchmark")

if parallel_benchmark_dir not in sys.path:
    sys.path.insert(0, parallel_benchmark_dir)
if ubuntu_env_dir not in sys.path:
    sys.path.insert(0, ubuntu_env_dir)

from desktop_env.controllers.python import PythonController
from parallel_agents.plan_agent_thought_action import PlanAgentThoughtAction
from config.api_config import get_api_config

TASKS_LIST_DIR = os.path.join(parallel_benchmark_dir, "tasks_list")
DEFAULT_QA_EVALUATOR_PATH = os.path.join("eval", "file_search_readonly_evaluator.py")
OUTPUT_JSON_PATH = os.path.join(ubuntu_env_dir, "logs", "run_qa_pipeline_all.json")


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

    输入:
        None
    输出:
        bool（True 表示可用）
    """
    if shutil.which("sshpass"):
        return True

    print("\n✗ 错误: sshpass未安装，无法通过 SSH 自动重建容器")
    print("  将自动尝试安装: sudo apt-get update && sudo apt-get install -y sshpass")

    print("\n⏳ 正在尝试自动安装 sshpass...")
    try:
        update_result = subprocess.run(
            ["sudo", "apt-get", "update", "-qq"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if update_result.returncode != 0:
            print("  ✗ apt-get update 失败")
            if update_result.stderr:
                print(update_result.stderr[:500])
            return False

        install_result = subprocess.run(
            ["sudo", "apt-get", "install", "-y", "-qq", "sshpass"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if install_result.returncode != 0:
            print("  ✗ apt-get install sshpass 失败")
            if install_result.stderr:
                print(install_result.stderr[:500])
            return False

        if shutil.which("sshpass"):
            print("  ✓ sshpass 已安装")
            return True

        print("  ✗ 安装完成但仍未找到 sshpass")
        return False
    except subprocess.TimeoutExpired:
        print("  ✗ 自动安装超时")
        return False
    except Exception as exc:
        print(f"  ✗ 自动安装失败: {exc}")
        return False


def load_run_plan_agent_module():
    """
    动态加载 run_plan_agent_thought_action.py 模块（不修改原文件）。

    输入:
        None
    输出:
        module
    """
    runner_path = os.path.join(
        parallel_benchmark_dir,
        "process",
        "run_plan_agent_thought_action.py",
    )
    if not os.path.exists(runner_path):
        raise FileNotFoundError(f"未找到运行器文件: {runner_path}")
    spec = importlib.util.spec_from_file_location("run_plan_agent_thought_action", runner_path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("无法加载 run_plan_agent_thought_action 模块")
    spec.loader.exec_module(module)
    return module


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


def scan_qa_tasks(tasks_dir: str) -> List[Tuple[str, str, Dict[str, Any]]]:
    """
    扫描 tasks_list 目录，筛选 task_type=QA 的任务。

    输入:
        tasks_dir: tasks_list 目录路径
    输出:
        [(task_uid, task_path, task_config), ...]
    """
    if not os.path.isdir(tasks_dir):
        raise FileNotFoundError(f"未找到任务目录: {tasks_dir}")

    qa_tasks: List[Tuple[str, str, Dict[str, Any]]] = []
    for root, _, files in os.walk(tasks_dir):
        for filename in files:
            if not filename.endswith(".json"):
                continue
            task_path = os.path.join(root, filename)
            try:
                task_config = load_task_config(task_path)
            except Exception:
                continue
            if task_config.get("task_type") != "QA":
                continue
            task_uid = task_config.get("task_uid", "")
            if not task_uid:
                continue
            qa_tasks.append((task_uid, task_path, task_config))

    # 保持稳定顺序：优先按 task_id，再按 task_uid
    qa_tasks.sort(key=lambda item: (item[2].get("task_id", ""), item[0]))
    return qa_tasks


def parse_prepare_script_path(url: str) -> Tuple[str, str, str]:
    """
    解析 prepare_script_path，提取 repo_id、revision 与子目录。

    输入:
        url: HuggingFace 数据集目录 URL
    输出:
        (repo_id, revision, subdir)
    """
    prefix = "https://huggingface.co/datasets/"
    if not url.startswith(prefix):
        raise ValueError(f"无法解析 prepare_script_path: {url}")
    rel = url[len(prefix):]
    parts = rel.split("/")
    if len(parts) < 4 or parts[2] != "tree":
        raise ValueError(f"无法解析 prepare_script_path: {url}")
    repo_id = "/".join(parts[:2])
    revision = parts[3]
    subdir = "/".join(parts[4:])
    return repo_id, revision, subdir


def execute_on_vm(vm_port: int, command: str) -> Dict[str, Any]:
    """
    在指定 VM 上执行命令。

    输入:
        vm_port: VM 端口
        command: 要执行的命令
    输出:
        dict，包含 status/returncode/output/error 等信息
    """
    from config_loader import DeployConfig
    vm_host = os.environ.get("BENCH_VM_HOST") or DeployConfig().vm_host
    url = f"http://{vm_host}:{vm_port}/execute"
    payload = {"command": command}
    try:
        response = requests.post(url, json=payload, timeout=60)
        result = response.json()
        if result.get("returncode", -1) != 0:
            error_msg = result.get("error", "") or result.get("output", "")
            return {"status": "error", "error": error_msg, "returncode": result.get("returncode")}
        return result
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _list_hf_files(repo_id: str, revision: str, subdir: str) -> Tuple[str, ...]:
    """
    通过 HuggingFace API 获取子目录下全部文件路径（递归）。

    输入:
        repo_id: 数据集仓库 ID
        revision: 分支或提交
        subdir: 子目录路径
    输出:
        文件路径列表（相对 repo 根目录）
    """
    api_url = f"https://huggingface.co/api/datasets/{repo_id}/tree/{revision}/{subdir}?recursive=1"
    response = requests.get(api_url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    file_paths = [item["path"] for item in payload
                  if item.get("type") == "file"
                  and not os.path.basename(item["path"]).startswith("~$")]
    return tuple(file_paths)


def _download_files_with_wget(vm_port: int, file_paths: Tuple[str, ...], repo_id: str, revision: str) -> bool:
    """
    使用 wget 在 VM 上下载文件到 /home/user/shared。

    输入:
        vm_port: VM 端口
        file_paths: 文件路径列表（相对 repo 根目录）
        repo_id: 数据集仓库 ID
        revision: 分支或提交
    输出:
        bool（是否下载成功）
    """
    if not file_paths:
        print("✗ 未获取到任何文件路径，跳过下载")
        return False

    downloaded = 0
    for rel_path in file_paths:
        url = f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{rel_path}"
        dest_path = f"/home/user/shared/{rel_path}"
        dest_dir = os.path.dirname(dest_path)
        cmd = (
            "bash -c "
            f"\"mkdir -p '{dest_dir}' && wget -q -O '{dest_path}' '{url}'\""
        )
        result = execute_on_vm(vm_port, cmd)
        if result.get("status") != "success":
            print(f"✗ 下载失败: {rel_path}")
            print(result.get("error", "Unknown error"))
            return False
        downloaded += 1

    print(f"✓ wget 下载完成，总计: {downloaded} 个文件")
    return True


def _flatten_shared_dir(vm_port: int, subdir: str) -> bool:
    """
    将 subdir 下的文件扁平化到 /home/user/shared 根目录，并删除 subdir 父目录。

    输入:
        vm_port: VM 端口
        subdir: 子目录路径
    输出:
        bool（是否整理成功）
    """
    if not subdir:
        print("⚠️  subdir 为空，跳过整理")
        return True
    parent_dir = os.path.dirname(subdir)
    if not parent_dir:
        print("⚠️  subdir 无父目录，跳过删除父目录")
        return True

    src_root = f"/home/user/shared/{subdir}"
    parent_root = f"/home/user/shared/{parent_dir}"
    cmd = (
        "bash -c "
        f"\"find '{src_root}' -type f -print0 | "
        "xargs -0 -I{} mv -f {} /home/user/shared/ && "
        f"rm -rf '{parent_root}'\""
    )
    result = execute_on_vm(vm_port, cmd)
    if result.get("status") != "success":
        print("✗ 共享目录整理失败")
        print(result.get("error", "Unknown error"))
        return False
    print("✓ 已将嵌套文件移动到 shared 根目录，并删除嵌套父目录")
    return True


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
            result = execute_on_vm(vm_port, "echo ready")
            if result.get("status") == "success" or result.get("returncode") == 0:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def ensure_docker_image_with_sshfs(
    ssh_password: str,
    ssh_opts: list[str],
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
        base_image: 基础镜像标签
        target_image: 目标镜像标签（预装 sshfs）
        ssh_host: 远端主机
    输出:
        bool（镜像存在或构建成功）
    """
    print("\n[0/3] 检查并构建预装 sshfs 的镜像...")
    inspect_cmd = (
        f"{conda_activate} && echo '{ssh_password}' | sudo -S "
        f"docker image inspect {target_image} >/dev/null 2>&1"
    )
    result = run_ssh_command(ssh_password, ssh_opts, ssh_host, inspect_cmd, timeout=30)
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
        f"{conda_activate} && echo '{ssh_password}' | sudo -S bash -c "
        f"\"cat <<'EOF' | docker build -t {target_image} -\n"
        f"{dockerfile_text}"
        "EOF\""
    )
    result = run_ssh_command(ssh_password, ssh_opts, ssh_host, build_cmd, timeout=600)
    if result.returncode != 0:
        print("  ✗ 镜像构建失败")
        if result.stderr:
            print(result.stderr[:500])
        return False
    print(f"  ✓ 镜像构建完成: {target_image}")
    return True


def rebuild_containers() -> bool:
    """
    通过 SSH 自动重建 Docker 容器（无交互）。

    输入:
        None
    输出:
        bool（是否重建成功）
    """
    print("\n" + "=" * 80)
    print("STAGE 1-0: 自动重建Docker容器（VNC 8006-8010）")
    print("=" * 80)

    if not ensure_sshpass_available():
        return False

    # 所有机密/路径/主机从 configs/deploy.yaml + env 读取
    from config_loader import DeployConfig, get_ssh_password
    _deploy = DeployConfig()
    base_image = os.environ.get("BENCH_BASE_IMAGE", "happysixd/osworld-docker")
    docker_image = os.environ.get("BENCH_DOCKER_IMAGE", "happysixd/osworld-docker-sshfs")
    ssh_password = get_ssh_password()
    if not ssh_password:
        print("✗ 未设置 SSH 密码。请 export BENCH_SSH_PASSWORD=<password> 或修改 deploy.yaml")
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
    # conda 环境激活命令；可通过 env BENCH_CONDA_ACTIVATE 自定义
    conda_activate = os.environ.get(
        "BENCH_CONDA_ACTIVATE",
        f"source /home/{vm_user}/miniconda3/etc/profile.d/conda.sh && conda activate parallelbenchmark",
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

        print("\n[1/3] 查找并删除占用端口或同名容器...")
        required_ports = [5000, 5001, 5002, 5003, 5004, 8006, 8007, 8008, 8009, 8010]
        required_names = [c["name"] for c in containers]

        cmd = f"{conda_activate} && echo '{ssh_password}' | sudo -S docker ps -a --format '{{{{.Names}}}}|||{{{{.Ports}}}}'"
        result = run_ssh_command(ssh_password, ssh_opts, ssh_host, cmd, timeout=10)

        containers_to_delete = []
        for line in result.stdout.strip().split("\n"):
            if not line or vm_user in line:
                continue
            parts = line.split("|||")
            if len(parts) < 1:
                continue
            name = parts[0]
            ports = parts[1] if len(parts) > 1 else ""

            if name in required_names:
                if name not in containers_to_delete:
                    containers_to_delete.append(name)
                continue

            for port in required_ports:
                if f":{port}->" in ports or (f":{port}/" in ports and "->" not in ports):
                    if name not in containers_to_delete:
                        containers_to_delete.append(name)
                    break

        if containers_to_delete:
            print(f"  准备删除 {len(containers_to_delete)} 个容器...")
            container_names = " ".join(containers_to_delete)
            cmd = f"{conda_activate} && echo '{ssh_password}' | sudo -S docker rm -f {container_names} 2>&1"
            run_ssh_command(ssh_password, ssh_opts, ssh_host, cmd, timeout=60)
            print(f"  ✓ 删除完成: {', '.join(containers_to_delete)}")
        else:
            print("  未发现需要删除的容器")

        print("\n[2/3] 启动新容器...")
        for c in containers:
            cmd = f"""{conda_activate} && echo '{ssh_password}' | sudo -S docker run -d \\
                --name {c['name']} \\
                -p {c['server_port']}:5000 \\
                -p {c['vnc_port']}:8006 \\
                -p {c['chromium_port']}:9222 \\
                -p {c['vlc_port']}:8080 \\
                --shm-size=2g --cap-add=NET_ADMIN --device=/dev/kvm \\
                -v {qcow2_path}:/System.qcow2:ro \\
                -v {shared_path}:/shared \\
                {docker_image}"""
            result = run_ssh_command(ssh_password, ssh_opts, ssh_host, cmd, timeout=60)
            if result.returncode == 0:
                print(f"  ✓ {c['name']} 启动成功")
            else:
                print(f"  ✗ {c['name']} 启动失败")
                return False

        print("⏳ 等待容器稳定（15秒）后再安装sshfs...")
        time.sleep(15)

        print("\n[3/3] 并行检查sshfs是否已内置...")
        processes = []
        for c in containers:
            cmd = (
                f"{conda_activate} && echo '{ssh_password}' | sudo -S docker exec {c['name']} "
                "bash -c 'which sshfs'"
            )
            proc = popen_ssh_command(ssh_password, ssh_opts, ssh_host, cmd)
            processes.append((c["name"], proc))

        all_success = True
        for name, proc in processes:
            stdout, stderr = proc.communicate(timeout=180)
            if proc.returncode == 0 and "/sshfs" in stdout:
                print(f"  ✓ {name} - sshfs已安装")
            else:
                print(f"  ✗ {name} - sshfs安装失败: {stderr[:200]}")
                all_success = False

        if not all_success:
            return False

        return True

    except FileNotFoundError:
        print("\n✗ 错误: sshpass未安装")
        return False
    except subprocess.TimeoutExpired:
        print("\n✗ SSH连接超时")
        return False
    except Exception as exc:
        print(f"\n✗ 重建容器失败: {exc}")
        return False


def download_task_files_on_vm(vm_port: int, prepare_url: str) -> bool:
    """
    使用 wget 下载任务数据到 /home/user/shared。

    输入:
        vm_port: VM 端口
        prepare_url: 任务 prepare_script_path URL
    输出:
        bool（是否下载成功）
    """
    print("清理 shared 目录，避免残留文件干扰当前任务...")
    clean_cmd = "bash -c \"find /home/user/shared -mindepth 1 -maxdepth 1 -exec rm -rf {} +\""
    result = execute_on_vm(vm_port, clean_cmd)
    if result.get("status") != "success":
        print("✗ 清理 shared 目录失败")
        print(result.get("error", "Unknown error"))
        return False

    repo_id, revision, subdir = parse_prepare_script_path(prepare_url)
    print(f"解析结果: repo_id={repo_id}, revision={revision}, subdir={subdir}")
    try:
        file_paths = _list_hf_files(repo_id, revision, subdir)
    except Exception as exc:
        print(f"✗ 获取文件列表失败: {exc}")
        return False

    if not _download_files_with_wget(vm_port, file_paths, repo_id, revision):
        return False

    if not _flatten_shared_dir(vm_port, subdir):
        return False

    check_cmd = "bash -c \"ls -la '/home/user/shared' | head -n 50\""
    result = execute_on_vm(vm_port, check_cmd)
    if result.get("status") != "success":
        print("⚠️  目录校验失败")
        print(result.get("error", "Unknown error"))
        return False
    print("共享目录检查结果(前50行):")
    print(result.get("output", ""))
    return True


def bootstrap_chrome_window(vm_port: int) -> bool:
    """
    在 VM 中自动打开并最大化 Chrome 窗口。

    输入:
        vm_port: VM 端口
    输出:
        bool（执行成功返回 True，失败返回 False）
    """
    # 采用异步触发：不等待 Chrome 启动结果，避免初始化阶段因长启动而阻塞。
    cmd = (
        "bash -c \"nohup python3 -c \\\"import subprocess, time, pyautogui; "
        "subprocess.Popen(['google-chrome']); "
        "time.sleep(2); "
        "pyautogui.hotkey('alt', 'f10')\\\" "
        "> /tmp/bootstrap_chrome_window.log 2>&1 &\""
    )
    _ = execute_on_vm(vm_port, cmd)
    print(f"✓ VM {vm_port} 已触发 Chrome 自动打开与最大化（异步执行）")
    return True


def init_vm(vm_port: int, vnc_port: int, prepare_url: str, rebuilt: bool = False) -> bool:
    """
    初始化单个 VM（挂载 shared、下载任务数据）。

    输入:
        vm_port: VM 端口
        vnc_port: VNC 端口
        prepare_url: 任务数据 URL
        rebuilt: 是否为重建后首次初始化
    输出:
        bool
    """
    print(f"\n{'=' * 60}")
    from config_loader import DeployConfig as _DC
    _vm_host_hint = _DC().vm_host
    print(f"初始化 VM (port {vm_port}, VNC http://{_vm_host_hint}:{vnc_port}/)")
    print(f"{'=' * 60}")

    wait_time = 30 if rebuilt else 5
    if not wait_for_vm_ready(vm_port, max_wait=wait_time):
        print(f"  ✗ VM {vm_port} 无法响应")
        return False

    print("[1/5] 检查并安装sshfs...")
    result = execute_on_vm(vm_port, "which sshfs")
    if result.get("status") != "success":
        # 避免 packagekitd 占用 apt 锁，先停止并禁用服务
        _ = execute_on_vm(
            vm_port,
            'bash -c "echo password | sudo -S systemctl stop packagekit || true; '
            'echo password | sudo -S systemctl disable packagekit || true"'
        )
        result = execute_on_vm(vm_port, 'bash -c "echo password | sudo -S apt update -qq"')
        if result.get("status") != "success":
            print(f"  ✗ apt update失败: {result.get('error', 'Unknown error')}")
            return False
        result = execute_on_vm(
            vm_port,
            'bash -c "echo password | sudo -S DEBIAN_FRONTEND=noninteractive apt install -y -qq sshfs"'
        )
        if result.get("status") != "success":
            print(f"  ✗ 安装sshfs失败: {result.get('error', 'Unknown error')}")
            return False
        result = execute_on_vm(vm_port, "which sshfs")
        if result.get("status") != "success":
            print(f"  ✗ sshfs验证失败: {result.get('error', 'Unknown error')}")
            return False
        print("  ✓ sshfs安装完成")
    else:
        print("  ✓ sshfs已安装")

    print("[2/5] 准备shared目录...")
    cmd = 'bash -c "echo password | sudo -S fusermount3 -u /home/user/shared 2>/dev/null; mkdir -p /home/user/shared"'
    result = execute_on_vm(vm_port, cmd)
    if result.get("status") != "success":
        print(f"  ✗ Failed: {result.get('error', 'Unknown error')}")
        return False
    print("  ✓ 完成")

    print("[3/5] 挂载shared文件夹...")
    from config_loader import DeployConfig as _DC, get_ssh_password
    _deploy = _DC()
    _pw = get_ssh_password()
    if not _pw:
        print("✗ 未设置 SSH 密码。请 export BENCH_SSH_PASSWORD=<password>")
        return False
    # 通过 base64 传递密码以避开 shell 特殊字符（反引号等）
    import base64 as _b64
    _pw_b64 = _b64.b64encode(_pw.encode()).decode()
    cmd = (
        f'bash -c "echo {_pw_b64} | base64 -d | sshfs '
        f'{_deploy.vm_user}@{_deploy.vm_host}:{_deploy.shared_base_dir} /home/user/shared '
        f'-o password_stdin -o StrictHostKeyChecking=no"'
    )
    result = execute_on_vm(vm_port, cmd)
    if result.get("status") != "success":
        print(f"  ✗ Failed: {result.get('error', 'Unknown error')}")
        return False
    print("  ✓ 完成")

    print("[4/5] 验证shared挂载...")
    result = execute_on_vm(vm_port, "ls /home/user/shared")
    if result.get("status") != "success":
        print(f"  ✗ Failed: {result.get('error', 'Unknown error')}")
        return False
    print("  ✓ 完成")

    if vm_port == 5000:
        if prepare_url:
            print("[5/6] 下载任务数据到shared...")
            if not download_task_files_on_vm(vm_port, prepare_url):
                return False
        else:
            print("[5/6] 任务未提供 prepare_script_path，跳过下载")
    else:
        print("[5/6] 跳过下载（使用shared中的文件）")

    print("[6/6] 自动打开并最大化 Chrome...")
    bootstrap_chrome_window(vm_port)

    print(f"\n✅ VM {vm_port} 初始化成功！")
    print(f"   VNC: http://{_deploy.vm_host}:{vnc_port}/?resize=scale&reconnect=true&autoconnect=true")
    return True


def stage1_initialize(task_config: Dict[str, Any]) -> bool:
    """
    Stage 1: 环境初始化（重建容器 + 下载任务数据）。

    输入:
        task_config: 任务配置
    输出:
        bool
    """
    print("\n" + "=" * 80)
    print("STAGE 1: 环境初始化")
    print("=" * 80)

    prepare_url = task_config.get("prepare_script_path", "")
    if not prepare_url:
        print("\n⚠️  任务配置缺少 prepare_script_path，将跳过下载步骤但继续执行任务")

    rebuilt = rebuild_containers()
    if not rebuilt:
        print("\n✗ 容器重建失败，终止初始化")
        return False

    vms = [(5000, 8006), (5001, 8007), (5002, 8008), (5003, 8009), (5004, 8010)]
    success_count = 0
    for vm_port, vnc_port in vms:
        if init_vm(vm_port, vnc_port, prepare_url, rebuilt=rebuilt):
            success_count += 1
        else:
            print(f"\n⚠️  VM {vm_port} 初始化失败，继续下一个...")

    print(f"\n初始化完成：{success_count}/{len(vms)} 个VM成功")
    return success_count == len(vms)


def stage2_execute_agent(task_config: Dict[str, Any], task_uid: str) -> Tuple[Dict[str, Any], PythonController]:
    """
    Stage 2: 执行 Agent 任务。

    输入:
        task_config: 任务配置
        task_uid: 任务 UID
    输出:
        (result, controller_vm1)
    """
    print("\n" + "=" * 80)
    print("STAGE 2: Agent执行任务")
    print("=" * 80)

    task_instruction = task_config.get("instruction", "")
    if not task_instruction:
        raise ValueError("任务配置缺少 instruction")

    print(f"\n任务描述: {task_instruction}\n")

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
        coordinator_model="gpt-5-2025-08-07",
    )

    start_time = time.time()
    result = planner.execute_task(
        task=task_instruction,
        max_rounds=10,
        max_rounds_per_subtask=20,
        timeout_per_subtask=600,
    )
    elapsed_time = time.time() - start_time
    print(f"\n执行完成，耗时: {elapsed_time:.2f}s")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(ubuntu_env_dir, "logs")
    os.makedirs(output_dir, exist_ok=True)
    record_path = os.path.join(output_dir, f"test_qa_execution_{task_uid}_{timestamp}.json")

    if planner.recorder:
        try:
            planner.recorder.save_to_file(record_path)
            print(f"✓ 执行记录已保存: {record_path}")
        except Exception as exc:
            print(f"⚠️  保存执行记录失败: {exc}")

    return result, controller_vm1


def load_evaluator(evaluator_path: str):
    """
    动态加载评估脚本。

    输入:
        evaluator_path: 相对 parallel_benchmark 的评估脚本路径
    输出:
        module
    """
    evaluator_abs = os.path.join(parallel_benchmark_dir, evaluator_path)
    if not os.path.exists(evaluator_abs):
        raise FileNotFoundError(f"未找到评估脚本: {evaluator_abs}")
    spec = importlib.util.spec_from_file_location("file_search_readonly_evaluator", evaluator_abs)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("无法加载评估脚本模块")
    spec.loader.exec_module(module)
    return module


def _extract_answer_via_llm(final_answer: str, task_instruction: str,
                            steps: list = None) -> str:
    """
    当 GUI Agent 的输出是描述式文本（而非结构化 <answer> 标签）时，
    调用 LLM 从执行记录中提取简洁答案。

    输入:
        final_answer: GUI Agent 的原始输出文本
        task_instruction: 任务指令（包含答案格式要求）
        steps: GUI Agent 的执行步骤列表（可选），用于提供更多上下文
    输出:
        提取后的答案文本（不含 <answer> 标签）
    """
    import re

    # 已经包含 <answer> 标签 → 直接提取
    match = re.search(r'<answer>(.*?)</answer>', final_answer, re.DOTALL)
    if match:
        return match.group(1).strip()

    # 短答案且非描述式 → 直接返回
    if len(final_answer) < 50 and not final_answer.lower().startswith("i "):
        return final_answer

    # 从最后几轮 steps 的 thought 中提取上下文（agent 的推理过程常包含答案线索）
    steps_context = ""
    if steps:
        recent_thoughts = []
        for s in steps[-5:]:
            thought = s.get("thought", "")
            if thought:
                recent_thoughts.append(thought[:300])
        if recent_thoughts:
            steps_context = "\n\nAgent 最近的推理过程：\n" + "\n---\n".join(recent_thoughts)

    # 调用 LLM 提取答案
    try:
        from config.api_config import get_api_config_for_model
        from openai import OpenAI

        extract_model = os.environ.get("QA_ANSWER_EXTRACT_MODEL", "gpt-4o-mini")
        api_config = get_api_config_for_model(extract_model)
        client = OpenAI(
            api_key=api_config["api_key"],
            base_url=api_config["base_url"],
        )

        prompt = (
            "你是一个答案提取助手。根据以下任务指令、GUI Agent 的执行总结和推理过程，"
            "提取最终答案。\n\n"
            f"任务指令：\n{task_instruction}\n\n"
            f"Agent 执行总结：\n{final_answer[:2000]}"
            f"{steps_context[:3000]}\n\n"
            "请根据 Agent 的推理过程和发现，直接输出最可能的答案值。"
            "不要加 <answer> 标签或任何解释。"
            "如果完全无法推断答案，输出 unknown。"
        )

        response = client.chat.completions.create(
            model=extract_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )
        extracted = response.choices[0].message.content.strip()

        # LLM 可能返回带标签的答案
        match = re.search(r'<answer>(.*?)</answer>', extracted, re.DOTALL)
        if match:
            extracted = match.group(1).strip()

        print(f"  LLM 答案提取: '{final_answer[:80]}...' → '{extracted}'")
        return extracted
    except Exception as exc:
        print(f"⚠️ LLM 答案提取失败: {exc}，使用原始 final_answer")
        return final_answer


def stage3_evaluate(task_config: Dict[str, Any], agent_result: Dict[str, Any], task_path: str) -> Dict[str, Any]:
    """
    Stage 3: 结果评估（使用 evaluator_path，缺失则回退默认）。

    输入:
        task_config: 任务配置
        agent_result: Stage2 的执行结果
        task_path: 任务 JSON 路径
    输出:
        评估结果字典
    """
    print("\n" + "=" * 80)
    print("STAGE 3: 结果评估")
    print("=" * 80)

    evaluator_path = task_config.get("evaluator_path", "")
    if not evaluator_path:
        evaluator_path = DEFAULT_QA_EVALUATOR_PATH

    evaluator = load_evaluator(evaluator_path)

    execution_record = agent_result.get("execution_record", {})
    final_answer = execution_record.get("summary", {}).get("final_answer", "").strip()
    if not final_answer:
        # fallback: 与 extract_execution_summary() 保持一致，
        # 从 plan_agent 最后一轮 response 中提取答案
        plan_rounds = execution_record.get("plan_agent", {}).get("rounds", []) or []
        if plan_rounds:
            last_prediction = plan_rounds[-1].get("model_prediction", {}) or {}
            final_answer = (last_prediction.get("response", "") or "").strip()
    if not final_answer:
        print("⚠️ 未获取到 final_answer，评估将使用空答案。")

    # GUI-only 模式下 Agent 常返回描述式文本而非结构化答案，
    # 通过 LLM 后处理提取简洁答案以提高评估准确性
    if final_answer and execution_record.get("summary", {}).get("mode") == "gui_only":
        instruction = task_config.get("instruction", "")
        steps = execution_record.get("steps", [])
        final_answer = _extract_answer_via_llm(final_answer, instruction, steps)

    result = evaluator.evaluate(task_path, final_answer)
    print("\n评估结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def extract_execution_summary(execution_record: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 execution_record 提取模型名、轮次与最后一轮输出。

    输入:
        execution_record: PlanAgentThoughtAction 返回的 execution_record
    输出:
        摘要字典，包含模型名、总轮次、最后一轮输出与最终答案等
    """
    plan_agent = execution_record.get("plan_agent", {})
    plan_agent_model = plan_agent.get("model_name", "")
    plan_agent_rounds = plan_agent.get("rounds", []) or []
    plan_agent_total_rounds = plan_agent.get("summary", {}).get("total_rounds")
    if plan_agent_total_rounds is None:
        plan_agent_total_rounds = len(plan_agent_rounds)

    last_round_output = ""
    last_round_messages: List[Dict[str, Any]] = []
    if plan_agent_rounds:
        last_round = plan_agent_rounds[-1]
        model_prediction = last_round.get("model_prediction", {}) or {}
        last_round_output = model_prediction.get("response", "") or ""
        last_round_messages = model_prediction.get("messages", []) or []

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
        "plan_agent_last_round_messages": last_round_messages,
        "model_output_answer": final_answer,
        "gui_agent_model": ", ".join(gui_models) if gui_models else "",
    }


def save_output_checkpoint(output_results: Dict[str, Any], output_path: str) -> None:
    """
    将当前累计结果即时写入 JSON（用于中断保护和增量落盘）。

    输入:
        output_results: 累计任务结果
        output_path: 输出 JSON 路径
    输出:
        None
    """
    with open(output_path, "w", encoding="utf-8") as file_obj:
        json.dump(output_results, file_obj, ensure_ascii=False, indent=2)


def main() -> None:
    """
    主流程。

    输入:
        None
    输出:
        None
    """
    required_env = os.environ.get("REQUIRED_CONDA_ENV", "")
    strict_check = os.environ.get("REQUIRED_CONDA_ENV_STRICT", "0") == "1"
    ensure_conda_env(required_env, strict=strict_check)

    print("=" * 80)
    print("QA 批量任务 Pipeline（自动筛选 task_type=QA）")
    print("=" * 80)

    task_items = scan_qa_tasks(TASKS_LIST_DIR)
    print(f"共检测到 QA 任务数量: {len(task_items)}")

    output_results: Dict[str, Any] = {}
    os.makedirs(os.path.dirname(OUTPUT_JSON_PATH), exist_ok=True)
    save_output_checkpoint(output_results, OUTPUT_JSON_PATH)

    for index, (task_uid, task_path, task_config) in enumerate(task_items, start=1):
        print("\n" + "#" * 80)
        print(f"开始任务 {index}/{len(task_items)} | UID: {task_uid}")
        print(f"任务配置: {task_path}")
        print("#" * 80)

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
        }

        try:
            if not stage1_initialize(task_config):
                task_result["interrupted"] = True
                task_result["interrupt_reason"] = "stage1_initialize_failed"
                output_results[task_uid] = task_result
                save_output_checkpoint(output_results, OUTPUT_JSON_PATH)
                print("\n✗ 环境初始化失败，跳过当前任务")
                continue

            try:
                result, _ = stage2_execute_agent(task_config, task_uid)
            except Exception as exc:
                task_result["interrupted"] = True
                task_result["interrupt_reason"] = f"stage2_execute_exception: {exc}"
                output_results[task_uid] = task_result
                save_output_checkpoint(output_results, OUTPUT_JSON_PATH)
                print(f"\n✗ Agent执行失败: {exc}")
                continue

            execution_record = result.get("execution_record", {}) if isinstance(result, dict) else {}
            if execution_record:
                summary_info = extract_execution_summary(execution_record)
                task_result.update(summary_info)
            else:
                task_result["interrupted"] = True
                task_result["interrupt_reason"] = "missing_execution_record"

            try:
                eval_result = stage3_evaluate(task_config, result, task_path)
                task_result["evaluator_output"] = eval_result
            except Exception as exc:
                task_result["interrupted"] = True
                task_result["interrupt_reason"] = f"stage3_evaluate_exception: {exc}"
                print(f"\n✗ 评估失败: {exc}")

            output_results[task_uid] = task_result
            save_output_checkpoint(output_results, OUTPUT_JSON_PATH)
        except KeyboardInterrupt:
            task_result["interrupted"] = True
            task_result["interrupt_reason"] = "keyboard_interrupt"
            output_results[task_uid] = task_result
            save_output_checkpoint(output_results, OUTPUT_JSON_PATH)
            print("\n⚠️ 检测到手动中断，已保存当前进度到输出 JSON。")
            break

        print("\n" + "=" * 80)
        print("当前任务执行完成")
        print("=" * 80)
        print(f"任务UID: {task_uid}")
        print("=" * 80)

    save_output_checkpoint(output_results, OUTPUT_JSON_PATH)

    print("\n" + "=" * 80)
    print("全部任务执行完成")
    print("=" * 80)
    print(f"输出结果文件: {OUTPUT_JSON_PATH}")


if __name__ == "__main__":
    main()
