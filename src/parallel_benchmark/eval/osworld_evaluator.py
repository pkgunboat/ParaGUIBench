"""
OSWorld JSON 评测配置执行模块

封装 OSWorld 原生 JSON 评测配置的完整评估流程：
1. 执行 postconfig（下载评测脚本、保存文件等准备步骤）
2. 获取 result（从 VM 文件或命令行输出）
3. 获取 expected（从 HuggingFace 下载或读取规则）
4. 分发到对应评测函数并返回得分

使用方法:
    from parallel_benchmark.eval.osworld_evaluator import evaluate_osworld_task

    result = evaluate_osworld_task(
        evaluator_json_path="parallel_benchmark/eval/osworld_scripts/xxx.json",
        vm_ip="127.0.0.1",
        vm_port=5000,
        shared_host_dir="/home/benchmark/shared/group_0",
        log=logger,
    )
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

# ============================================================
# 路径适配（复用 adapter 模块）
# ============================================================

from parallel_benchmark.eval.osworld_scripts.adapter import adapt_result_path, PATH_MAPPING


_AGENT_RESULT_MISSING = object()


def _map_paths_in_string(text: str) -> str:
    """
    将字符串中所有 OSWorld 原生路径前缀替换为共享目录路径。
    与 adapt_result_path 不同，此函数处理的是包含路径的任意文本
    （如 shell 命令、Python 脚本源码），而非单独的路径字符串。

    输入:
        text: 可能包含 /home/user/Desktop/ 等路径的文本
    输出:
        替换后的文本
    """
    for old_prefix, new_prefix in PATH_MAPPING.items():
        text = text.replace(old_prefix, new_prefix)
    return text


# ============================================================
# SSH 与 VM 通信工具
# ============================================================

_SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
]


def _get_ssh_creds(vm_ip: str) -> Dict[str, Any]:
    """
    从 configs/deploy.yaml + 环境变量获取指定宿主机的 SSH 凭据。

    优先级：
        1. BENCH_SSH_USER / BENCH_SSH_PASSWORD 环境变量
        2. deploy.yaml.server.vm_user + server.ssh_password_env 指向的环境变量
        3. 当前登录用户名 + 空密码（用于 key-based auth）

    输入:
        vm_ip: 宿主机 IP（保留参数以便未来多机部署时按 IP 分桶读配置）
    输出:
        {"ssh_host": "user@ip", "ssh_password": str, "ssh_opts": list}
    """
    import sys as _sys
    # src/config_loader 可能还未加载，这里兜底拓展 sys.path
    _src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _src_dir not in _sys.path:
        _sys.path.insert(0, _src_dir)
    from config_loader import DeployConfig, get_ssh_password

    deploy = DeployConfig()
    vm_user = os.environ.get("BENCH_SSH_USER") or deploy.vm_user or os.environ.get("USER", "benchmark")
    password = get_ssh_password()
    return {
        "ssh_host": f"{vm_user}@{vm_ip}",
        "ssh_password": password,
        "ssh_opts": list(_SSH_OPTS),
    }


def _exec_on_vm(
    vm_ip: str, vm_port: int, command: str, timeout: int = 60,
) -> Dict[str, Any]:
    """
    通过 VM Python Server 执行命令。

    输入:
        vm_ip: 宿主机 IP
        vm_port: VM API 端口（如 5000）
        command: shell 命令字符串
        timeout: 请求超时秒数
    输出:
        VM 返回的 JSON 字典，包含 output / returncode / error 等字段
    """
    url = f"http://{vm_ip}:{vm_port}/execute"
    try:
        resp = requests.post(
            url,
            json={"command": command, "shell": True},
            timeout=timeout,
        )
        return resp.json()
    except Exception as exc:
        return {"status": "error", "error": str(exc), "returncode": -1}


def _download_url_to_local(
    url: str, local_path: str, log: logging.Logger,
) -> bool:
    """
    从 URL 下载文件到本地 Mac。

    输入:
        url: 远程 URL
        local_path: 本地保存路径
        log: logger
    输出:
        bool
    """
    try:
        # HF 镜像改写（BENCH_HF_BASE，见 config_loader.rewrite_hf_url）
        from config_loader import rewrite_hf_url
        url = rewrite_hf_url(url)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info("  URL 下载完成 → %s", os.path.basename(local_path))
        return True
    except Exception as exc:
        log.error("  URL 下载失败: %s → %s", url[:120], exc)
        return False


def _ssh_download_file(
    vm_ip: str, host_path: str, local_path: str, log: logging.Logger,
) -> bool:
    """
    通过 SSH 从宿主机下载单个文件到本地 Mac。
    使用 ssh + cat 方式，避免 scp 在路径含特殊字符时的转义问题。

    输入:
        vm_ip: 宿主机 IP
        host_path: 宿主机上的文件完整路径
        local_path: 本地保存路径
        log: logger
    输出:
        bool
    """
    creds = _get_ssh_creds(vm_ip)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    env = os.environ.copy()
    env["SSHPASS"] = creds["ssh_password"]

    cmd = (
        ["sshpass", "-e", "ssh"]
        + creds["ssh_opts"]
        + [creds["ssh_host"], f"cat {shlex.quote(host_path)}"]
    )
    try:
        with open(local_path, "wb") as f:
            proc = subprocess.run(
                cmd, stdout=f, stderr=subprocess.PIPE, env=env, timeout=120,
            )
        if proc.returncode == 0 and os.path.getsize(local_path) > 0:
            log.info("  SSH 下载成功: %s (%d bytes)",
                     os.path.basename(host_path), os.path.getsize(local_path))
            return True
        log.error("  SSH 下载失败: rc=%d, stderr=%s",
                  proc.returncode, proc.stderr.decode(errors="replace")[:300])
        return False
    except Exception as exc:
        log.error("  SSH 下载异常: %s", exc)
        return False


def _ssh_upload_bytes(
    vm_ip: str, content: bytes, host_path: str, log: logging.Logger,
) -> bool:
    """
    通过 SSH 上传字节内容到宿主机的指定路径。

    输入:
        vm_ip: 宿主机 IP
        content: 要上传的字节内容
        host_path: 宿主机目标路径
        log: logger
    输出:
        bool
    """
    creds = _get_ssh_creds(vm_ip)
    env = os.environ.copy()
    env["SSHPASS"] = creds["ssh_password"]

    # 创建父目录
    parent = os.path.dirname(host_path)
    mkdir_cmd = (
        ["sshpass", "-e", "ssh"]
        + creds["ssh_opts"]
        + [creds["ssh_host"], f"mkdir -p {shlex.quote(parent)}"]
    )
    subprocess.run(mkdir_cmd, env=env, capture_output=True, timeout=30)

    # 通过 stdin 管道写入
    write_cmd = (
        ["sshpass", "-e", "ssh"]
        + creds["ssh_opts"]
        + [creds["ssh_host"], f"cat > {shlex.quote(host_path)}"]
    )
    try:
        proc = subprocess.run(
            write_cmd, input=content, env=env, capture_output=True, timeout=60,
        )
        if proc.returncode == 0:
            return True
        log.error("  上传失败: %s", proc.stderr.decode(errors="replace")[:200])
        return False
    except Exception as exc:
        log.error("  上传异常: %s", exc)
        return False


def _vm_path_to_host_path(vm_path: str, shared_host_dir: str) -> str:
    """
    将 VM 内共享目录路径转换为宿主机文件路径。

    输入:
        vm_path: VM 内路径（已经过 adapt_result_path 映射，以 /home/user/shared/ 开头）
        shared_host_dir: 宿主机共享目录（如 /home/agentlab/shared/group_0）
    输出:
        宿主机上的完整路径
    """
    prefix = "/home/user/shared/"
    if vm_path.startswith(prefix):
        relative = vm_path[len(prefix):]
    else:
        relative = os.path.basename(vm_path)
    return os.path.join(shared_host_dir, relative)


# ============================================================
# Postconfig 执行
# ============================================================

def _run_postconfig(
    postconfig: List[Dict[str, Any]],
    vm_ip: str,
    vm_port: int,
    shared_host_dir: str,
    log: logging.Logger,
) -> None:
    """
    执行 OSWorld evaluator.postconfig 中的准备步骤。

    支持的 step type:
      - download: 下载文件到宿主机共享目录（自动路径映射 + Python 脚本内部路径替换）
      - execute: 在 VM 上执行命令（自动路径映射 + pyautogui 命令自动设置 DISPLAY）
      - activate_window: 尝试激活指定窗口（best-effort，失败不中断）
      - sleep: 等待指定秒数

    输入:
        postconfig: evaluator.postconfig 列表
        vm_ip: 宿主机 IP
        vm_port: VM API 端口
        shared_host_dir: 宿主机共享目录
        log: logger
    """
    if not postconfig:
        return

    for idx, step in enumerate(postconfig):
        stype = step.get("type", "")
        params = step.get("parameters", {})
        log.info("  postconfig [%d/%d] type=%s", idx + 1, len(postconfig), stype)

        if stype == "download":
            _pc_download(params, vm_ip, shared_host_dir, log)
        elif stype == "execute":
            _pc_execute(params, vm_ip, vm_port, log)
        elif stype == "activate_window":
            _pc_activate_window(params, vm_ip, vm_port, log)
        elif stype == "sleep":
            secs = params.get("seconds", 1)
            log.info("    sleep %.1f s", secs)
            time.sleep(secs)
        else:
            log.warning("    未知 postconfig 类型 '%s'，跳过", stype)


def _ensure_vm_link(
    vm_ip: str,
    vm_port: int,
    original_path: str,
    mapped_vm_path: str,
    log: logging.Logger,
) -> None:
    """
    在 VM 中创建原始 OSWorld 路径到 shared 路径的符号链接。
    """
    link_path = original_path.rstrip("/")
    target_path = mapped_vm_path.rstrip("/")
    if not link_path or link_path == target_path:
        return

    parent_dir = os.path.dirname(link_path)
    cmd = (
        "bash -c "
        f"\"mkdir -p {shlex.quote(parent_dir)} && "
        f"rm -rf {shlex.quote(link_path)} && "
        f"ln -s {shlex.quote(target_path)} {shlex.quote(link_path)}\""
    )
    result = _exec_on_vm(vm_ip, vm_port, cmd, timeout=60)
    if result.get("returncode", -1) != 0:
        log.warning(
            "    创建链接失败: %s -> %s (%s)",
            link_path,
            target_path,
            (result.get("error") or result.get("output", ""))[:200],
        )


def _pc_download(
    params: Dict[str, Any],
    vm_ip: str,
    shared_host_dir: str,
    log: logging.Logger,
) -> None:
    """
    postconfig download: 从 URL 下载文件到宿主机共享目录。
    如果文件是 Python 脚本，自动替换内部硬编码路径。

    流程: URL → 本地临时文件 → (可选) 路径替换 → SSH 上传到宿主机 shared
    """
    for f_spec in params.get("files", []):
        url = f_spec.get("url", "")
        original_path = f_spec.get("path", "")
        if not url or not original_path:
            continue

        # OSWorld 原始路径 → 共享目录路径
        mapped_vm_path = adapt_result_path(original_path)
        host_path = _vm_path_to_host_path(mapped_vm_path, shared_host_dir)

        log.info("    下载: %s", os.path.basename(url.split("?")[0]))
        log.info("      → 宿主机: %s", host_path)

        # 下载到本地临时文件
        ext = os.path.splitext(original_path)[1]
        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)

        try:
            if not _download_url_to_local(url, tmp_path, log):
                continue

            with open(tmp_path, "rb") as f_in:
                content = f_in.read()

            # Python 脚本：替换内部硬编码路径
            if original_path.endswith(".py"):
                try:
                    text = content.decode("utf-8")
                    text = _map_paths_in_string(text)
                    content = text.encode("utf-8")
                    log.info("      已替换脚本内部路径")
                except UnicodeDecodeError:
                    pass

            _ssh_upload_bytes(vm_ip, content, host_path, log)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def _cfg_download(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    shared_host_dir: str,
    log: logging.Logger,
) -> None:
    """
    config download: 下载任务输入文件到 shared，并在原始路径创建符号链接。
    """
    for f_spec in params.get("files", []):
        url = f_spec.get("url", "")
        original_path = f_spec.get("path", "")
        if not url or not original_path:
            continue

        mapped_vm_path = adapt_result_path(original_path)
        host_path = _vm_path_to_host_path(mapped_vm_path, shared_host_dir)

        log.info("    下载输入文件: %s", os.path.basename(url.split("?")[0]))
        log.info("      → 宿主机: %s", host_path)

        ext = os.path.splitext(original_path)[1]
        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)

        try:
            if not _download_url_to_local(url, tmp_path, log):
                continue
            with open(tmp_path, "rb") as f_in:
                content = f_in.read()
            if _ssh_upload_bytes(vm_ip, content, host_path, log):
                _ensure_vm_link(vm_ip, vm_port, original_path, mapped_vm_path, log)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def _build_vm_command(command: List[str]) -> str:
    """
    将 OSWorld 命令列表转换为可在 VM 上执行的 shell 命令字符串。

    输入:
        command: 命令列表，如 ["/bin/bash", "-c", "..."] 或 ["python", "-c", "..."]
    输出:
        shell 命令字符串
    """
    if not command:
        return ""

    prog = command[0]

    # bash -c "shell_command" 格式：对 shell 命令做路径映射后直接发送
    if prog in ("/bin/bash", "bash") and len(command) >= 3 and command[1] == "-c":
        shell_cmd = _map_paths_in_string(command[2])
        return shell_cmd

    # python -c "code" 格式：需要 DISPLAY 环境变量（pyautogui 等依赖）
    if prog in ("python", "python3") and len(command) >= 3 and command[1] == "-c":
        code = command[2]
        return (
            "DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
            f"python3 -c {shlex.quote(code)}"
        )

    # 通用格式：逐参数路径映射并拼接
    mapped = []
    for part in command:
        if "/home/user/" in part:
            mapped.append(adapt_result_path(part))
        else:
            mapped.append(part)
    return " ".join(shlex.quote(p) for p in mapped)


def _build_vm_command_raw(command: List[str]) -> str:
    """
    将命令列表按原始路径拼成 VM shell 命令，不做 shared 路径映射。
    """
    if not command:
        return ""

    prog = command[0]

    if prog in ("/bin/bash", "bash") and len(command) >= 3 and command[1] == "-c":
        return command[2]

    if prog in ("python", "python3") and len(command) >= 3 and command[1] == "-c":
        code = command[2]
        return (
            "DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
            f"python3 -c {shlex.quote(code)}"
        )

    return " ".join(shlex.quote(str(part)) for part in command)


def _pc_execute(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    postconfig execute: 在 VM 上执行命令。
    自动处理:
      - bash -c "..." 格式：提取 shell 命令，做路径映射后直接执行
      - python -c "..." 格式：自动设置 DISPLAY=:0 和 DBUS_SESSION_BUS_ADDRESS
      - 其它格式：逐参数路径映射后拼接
    """
    command = params.get("command", [])
    if not command:
        return

    if isinstance(command, list):
        cmd_str = _build_vm_command(command)
    else:
        cmd_str = _map_paths_in_string(str(command))

    log.info("    VM 执行: %s", cmd_str[:200])
    result = _exec_on_vm(vm_ip, vm_port, cmd_str, timeout=120)

    rc = result.get("returncode", -1)
    if rc != 0:
        log.warning("    返回码 %s: %s",
                    rc, (result.get("error") or result.get("output", ""))[:200])


def _cfg_command(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    config command: 主要用于任务前置目录准备。
    """
    command = params.get("command", [])
    if not command:
        return

    if isinstance(command, list):
        cmd_str = _build_vm_command_raw(command)
    else:
        cmd_str = str(command)

    log.info("    准备目录/命令: %s", cmd_str[:200])
    result = _exec_on_vm(vm_ip, vm_port, cmd_str, timeout=120)
    if result.get("returncode", -1) != 0:
        log.warning(
            "    command 执行失败: %s",
            (result.get("error") or result.get("output", ""))[:200],
        )


def _cfg_open(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    config open: 打开指定文件。
    """
    original_path = params.get("path", "")
    if not original_path:
        return

    cmd = (
        "bash -c "
        f"\"DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
        f"xdg-open {shlex.quote(original_path)} >/tmp/osw_open.log 2>&1 &\""
    )
    log.info("    打开文件: %s", original_path)
    _exec_on_vm(vm_ip, vm_port, cmd, timeout=30)


def _cfg_launch(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    config launch: 后台启动一个应用或服务。
    """
    command = params.get("command", [])
    if not command:
        return

    if isinstance(command, list):
        cmd_body = " ".join(shlex.quote(str(part)) for part in command)
    else:
        cmd_body = str(command)

    cmd = (
        'bash -c "'
        "DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
        f"nohup {cmd_body} >/tmp/osw_launch.log 2>&1 &\""
    )
    log.info("    启动进程: %s", cmd_body[:200])
    _exec_on_vm(vm_ip, vm_port, cmd, timeout=30)


def _cfg_chrome_open_tabs(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    config chrome_open_tabs: 打开若干 Chrome 标签页。
    """
    urls = params.get("urls_to_open", [])
    if not urls:
        return

    quoted_urls = " ".join(shlex.quote(url) for url in urls)
    cmd = (
        'bash -c "'
        "DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
        f"nohup google-chrome --new-tab {quoted_urls} >/tmp/osw_chrome_tabs.log 2>&1 &\""
    )
    log.info("    打开 Chrome 标签页: %s", ", ".join(urls[:3]))
    _exec_on_vm(vm_ip, vm_port, cmd, timeout=30)


def _pc_activate_window(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    postconfig activate_window: 尝试激活指定窗口（best-effort）。
    使用 wmctrl -a 命令，失败则忽略（窗口大概率仍在前台）。
    """
    window_name = params.get("window_name", "")
    if not window_name:
        return

    log.info("    尝试激活窗口: %s", window_name)
    cmd = f"DISPLAY=:0 wmctrl -a {shlex.quote(window_name)} 2>/dev/null || true"
    _exec_on_vm(vm_ip, vm_port, cmd, timeout=15)


def _run_config(
    config_steps: List[Dict[str, Any]],
    vm_ip: str,
    vm_port: int,
    shared_host_dir: str,
    log: logging.Logger,
) -> None:
    """
    执行 OSWorld 任务前置 config。
    """
    if not config_steps:
        return

    for idx, step in enumerate(config_steps):
        stype = step.get("type", "")
        params = step.get("parameters", {})
        log.info("  config [%d/%d] type=%s", idx + 1, len(config_steps), stype)

        if stype == "download":
            _cfg_download(params, vm_ip, vm_port, shared_host_dir, log)
        elif stype == "command":
            _cfg_command(params, vm_ip, vm_port, log)
        elif stype == "execute":
            _pc_execute(params, vm_ip, vm_port, log)
        elif stype == "open":
            _cfg_open(params, vm_ip, vm_port, log)
        elif stype == "launch":
            _cfg_launch(params, vm_ip, vm_port, log)
        elif stype == "chrome_open_tabs":
            _cfg_chrome_open_tabs(params, vm_ip, vm_port, log)
        elif stype == "activate_window":
            _pc_activate_window(params, vm_ip, vm_port, log)
        elif stype == "sleep":
            secs = params.get("seconds", 1)
            log.info("    sleep %.1f s", secs)
            time.sleep(secs)
        else:
            log.warning("    未知 config 类型 '%s'，跳过", stype)


def prepare_osworld_task(
    evaluator_json_path: str,
    vm_ip: str,
    vm_port: int,
    shared_host_dir: str,
    log: logging.Logger,
) -> bool:
    """
    使用 OSWorld JSON 的 config 字段准备任务输入文件和初始窗口。
    """
    try:
        with open(evaluator_json_path, "r", encoding="utf-8") as f:
            osw_config = json.load(f)
    except Exception as exc:
        log.error("加载 OSWorld JSON 失败: %s → %s", evaluator_json_path, exc)
        return False

    config_steps = osw_config.get("config", [])
    if not config_steps:
        log.info("OSWorld config 为空，跳过前置初始化")
        return True

    try:
        log.info("执行 OSWorld config (%d 步)...", len(config_steps))
        _run_config(config_steps, vm_ip, vm_port, shared_host_dir, log)
        return True
    except Exception as exc:
        log.error("执行 OSWorld config 失败: %s", exc, exc_info=True)
        return False


# ============================================================
# Result 获取
# ============================================================

def _get_result(
    result_config: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    shared_host_dir: str,
    work_dir: str,
    log: logging.Logger,
) -> Tuple[Any, str]:
    """
    获取评测结果数据。

    输入:
        result_config: evaluator.result 配置
        vm_ip / vm_port: VM 连接信息
        shared_host_dir: 宿主机共享目录
        work_dir: 本地临时工作目录
        log: logger
    输出:
        (data, type_str)
        - vm_file → data 是本地文件路径
        - vm_command_line → data 是命令 stdout 字符串
    """
    rtype = result_config.get("type", "")

    if rtype == "vm_file":
        path = _get_result_file(result_config, vm_ip, shared_host_dir, work_dir, log)
        return path, "vm_file"

    if rtype == "background_image_in_slide":
        path = _get_result_background_image(
            result_config, vm_ip, shared_host_dir, work_dir, log,
        )
        if path is _AGENT_RESULT_MISSING:
            return path, "missing_agent_result"
        return path, "vm_file"

    if rtype == "vm_command_line":
        output = _get_result_command(result_config, vm_ip, vm_port, log)
        return output, "vm_command_line"

    log.error("未知 result type: %s", rtype)
    return None, rtype


def _normalize_multi_files(cfg: Dict[str, Any]) -> Tuple[List[str], List[str], set]:
    """
    将 result/expected 配置的 path/dest 归一化为并行列表，兼容 OSWorld multi 语义。

    对齐 OSWorld getters.file.get_vm_file / get_cloud_file 的行为：
      - multi=False（或缺省）：path/dest 为单个字符串，包成单元素列表；
      - multi=True：path/dest 本身即为等长列表，列表中的所有文件都要下载到同一目录，
        使得诸如 sheet_print 依赖的 "<xlsx_stem>-Sheet1.csv" 旁挂文件与主 xlsx 同目录。

    输入:
        cfg: result 或 expected 配置字典（含 path / dest / multi / gives）
    输出:
        (paths, dests, gives)
        - paths: 原始路径/URL 列表
        - dests: 与 paths 等长的本地文件名列表（缺省时由各自 path 的 basename 推导）
        - gives: 需要返回给评测函数的下标集合（缺省 {0}，即主文件）
    """
    raw_path = cfg.get("path", "")
    raw_dest = cfg.get("dest", None)

    if cfg.get("multi", False) or isinstance(raw_path, list):
        paths: List[str] = list(raw_path) if isinstance(raw_path, list) else [raw_path]
        if isinstance(raw_dest, list):
            dests: List[str] = list(raw_dest)
        elif raw_dest is not None:
            dests = [raw_dest]
        else:
            dests = [os.path.basename(str(p).split("?")[0]) for p in paths]
    else:
        paths = [raw_path]
        dests = [raw_dest if raw_dest is not None
                 else os.path.basename(str(raw_path).split("?")[0])]

    # 补齐 dest（长度不足时用 path 的 basename 兜底），保证与 paths 等长
    while len(dests) < len(paths):
        dests.append(os.path.basename(str(paths[len(dests)]).split("?")[0]))

    gives = set(cfg.get("gives", [0]))
    return paths, dests, gives


def _get_result_file(
    cfg: Dict[str, Any],
    vm_ip: str,
    shared_host_dir: str,
    work_dir: str,
    log: logging.Logger,
) -> Optional[str]:
    """
    vm_file 类型：从宿主机共享目录下载结果文件到本地。

    路径映射链:
      OSWorld 原始路径 → adapt_result_path → /home/user/shared/...
      → _vm_path_to_host_path → 宿主机路径
      → SSH 下载到本地 work_dir

    支持 multi 语义：当 path/dest 为列表（multi=True）时，把列表中的所有文件都下载到
    同一个 work_dir/result 目录（旁挂 CSV 与主 xlsx 同目录），并按 gives（默认 {0}）返回主文件。
    """
    paths, dests, gives = _normalize_multi_files(cfg)
    result_dir = os.path.join(work_dir, "result")

    log.info("获取结果文件 (%d 个):", len(paths))
    given: List[Optional[str]] = []
    for i, (original_path, dest_name) in enumerate(zip(paths, dests)):
        vm_path = adapt_result_path(original_path)
        host_path = _vm_path_to_host_path(vm_path, shared_host_dir)
        local_path = os.path.join(result_dir, dest_name)

        log.info("  [%d] 原始路径: %s", i, original_path)
        log.info("      宿主机路径: %s", host_path)

        ok = _ssh_download_file(vm_ip, host_path, local_path, log)
        if i in gives:
            given.append(local_path if ok else None)

    if not given:
        return None
    # 主文件下载失败则视为获取结果失败
    if given[0] is None:
        return None
    # gives 只含单个下标（含默认 {0}）时返回字符串，兼容 compare_table 等按 str 路径消费的评测函数
    return given[0] if len(given) == 1 else given


def _extract_slide_background_image(
    pptx_local_path: str,
    slide_index: int,
    dest_path: str,
    log: logging.Logger,
) -> Optional[str]:
    """
    从本地已下载的 .pptx 中解压出指定页幻灯片的背景图片，写入 dest_path。

    移植自 desktop_env/evaluators/getters/impress.py:get_background_image_in_slide，
    但改为直接对本地文件操作（本评测走 SSH+共享目录，没有 OSWorld env/get_vm_file）。
    """
    import xml.etree.ElementTree as ET
    import zipfile

    bg_tag = "{http://schemas.openxmlformats.org/presentationml/2006/main}bgPr"
    image_tag = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
    embed_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
    rels_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}

    try:
        with zipfile.ZipFile(pptx_local_path, "r") as myzip:
            names = myzip.namelist()
            slide_xml = "ppt/slides/slide{}.xml".format(slide_index + 1)
            if slide_xml not in names:
                log.warning("背景图片提取: 幻灯片 %s 不存在于 pptx", slide_xml)
                return None

            image_id = None
            with myzip.open(slide_xml) as f:
                root = ET.parse(f).getroot()
                for child in root.iter(bg_tag):
                    for element in child.iter(image_tag):
                        if embed_attr in element.attrib:
                            image_id = element.attrib[embed_attr]
                            break
                    if image_id is not None:
                        break
            if image_id is None:
                log.warning("背景图片提取: 幻灯片 %d 未设置背景图片(bgPr blip)", slide_index)
                return None

            rels_xml = "ppt/slides/_rels/slide{}.xml.rels".format(slide_index + 1)
            if rels_xml not in names:
                log.warning("背景图片提取: 缺少关系文件 %s", rels_xml)
                return None

            image_zip_path = None
            with myzip.open(rels_xml) as f:
                root = ET.parse(f).getroot()
                for rel in root.findall("r:Relationship", rels_ns):
                    if "image" in rel.attrib.get("Type", "") and rel.attrib.get("Id") == image_id:
                        target = rel.attrib.get("Target", "")
                        if target.startswith(".."):
                            image_zip_path = os.path.normpath(
                                os.path.join("ppt/slides", target)
                            ).replace("\\", "/")
                        else:
                            log.warning("背景图片提取: 非 zip 内相对路径 Target=%s，跳过", target)
                        break

            if not image_zip_path or image_zip_path not in names:
                log.warning(
                    "背景图片提取: 未能定位 zip 内图片 (id=%s, path=%s)",
                    image_id, image_zip_path,
                )
                return None

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with myzip.open(image_zip_path) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            log.info(
                "背景图片提取成功: slide=%d, %s -> %s",
                slide_index, image_zip_path, dest_path,
            )
            return dest_path
    except Exception as exc:
        log.error("背景图片提取异常: %s", exc, exc_info=True)
        return None


def _get_result_background_image(
    cfg: Dict[str, Any],
    vm_ip: str,
    shared_host_dir: str,
    work_dir: str,
    log: logging.Logger,
) -> Any:
    """
    background_image_in_slide 类型：下载 pptx 后提取指定页背景图片。
    """
    ppt_file_path = cfg.get("ppt_file_path", "")
    slide_index = int(cfg.get("slide_index", 0))
    dest_name = cfg.get("dest") or "background_image.png"

    pptx_local = _get_result_file(
        {"path": ppt_file_path, "dest": os.path.basename(ppt_file_path)},
        vm_ip, shared_host_dir, work_dir, log,
    )
    if not pptx_local:
        log.error("background_image_in_slide: 下载 pptx 失败 (%s)", ppt_file_path)
        return None

    dest_path = os.path.join(work_dir, "result", dest_name)
    extracted = _extract_slide_background_image(pptx_local, slide_index, dest_path, log)
    if extracted is None:
        return _AGENT_RESULT_MISSING
    return extracted


def _get_result_command(
    cfg: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> Optional[str]:
    """
    vm_command_line 类型：在 VM 上执行命令并返回 stdout。
    命令中的路径会自动映射。
    """
    command = cfg.get("command", "")
    if not command:
        return None

    mapped = _map_paths_in_string(command) if isinstance(command, str) else command
    log.info("执行结果命令: %s", mapped)

    result = _exec_on_vm(vm_ip, vm_port, mapped, timeout=120)
    output = result.get("output", "")

    if result.get("returncode", -1) != 0:
        log.warning("结果命令非零返回: rc=%s, err=%s",
                    result.get("returncode"), (result.get("error", ""))[:300])

    log.info("命令输出 (%d chars): %s", len(output), output[:500])
    return output


def _persist_result_data(
    result_data: Any,
    result_type: str,
    save_result_dir: str,
    log: logging.Logger,
) -> str:
    """
    Persist the evaluated result artifact before the temporary eval directory
    is removed, so failed runs can still be inspected afterwards.
    """
    if not save_result_dir or result_data is None:
        return ""

    os.makedirs(save_result_dir, exist_ok=True)

    if result_type == "vm_file" and isinstance(result_data, str) and os.path.exists(result_data):
        basename = os.path.basename(os.path.normpath(result_data)) or "result"
        dst = os.path.join(save_result_dir, basename)
        if os.path.isdir(result_data):
            if os.path.exists(dst):
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(result_data, dst)
        else:
            shutil.copy2(result_data, dst)
        log.info("OSWorld 评测结果文件已保存: %s", dst)
        return dst

    if result_type == "vm_command_line":
        dst = os.path.join(save_result_dir, "result.txt")
        with open(dst, "w", encoding="utf-8") as f:
            f.write(str(result_data))
        log.info("OSWorld 评测命令输出已保存: %s", dst)
        return dst

    return ""


# ============================================================
# Expected 获取
# ============================================================

def _get_expected(
    expected_config: Dict[str, Any],
    work_dir: str,
    log: logging.Logger,
) -> Tuple[Any, str]:
    """
    获取期望结果。

    输入:
        expected_config: evaluator.expected 配置
        work_dir: 本地临时工作目录
        log: logger
    输出:
        (data, type_str)
        - cloud_file → data 是本地文件路径
        - rule → data 是 rules 字典
    """
    etype = expected_config.get("type", "")

    if etype == "cloud_file":
        paths, dests, gives = _normalize_multi_files(expected_config)
        expected_dir = os.path.join(work_dir, "expected")

        log.info("获取期望文件 (%d 个):", len(paths))
        given: List[Optional[str]] = []
        for i, (url, dest) in enumerate(zip(paths, dests)):
            local_path = os.path.join(expected_dir, dest)
            log.info("  [%d] %s", i, url[:120])
            ok = _download_url_to_local(url, local_path, log)
            if i in gives:
                given.append(local_path if ok else None)

        if not given or given[0] is None:
            return None, "cloud_file"
        primary = given[0] if len(given) == 1 else given
        return primary, "cloud_file"

    if etype == "rule":
        return expected_config.get("rules", {}), "rule"

    log.error("未知 expected type: %s", etype)
    return None, etype


# ============================================================
# 评测函数分发
# ============================================================

_cached_eval_funcs: Optional[Dict[str, Any]] = None


def _load_eval_funcs() -> Dict[str, Any]:
    """
    懒加载评测函数映射表，避免顶层导入失败影响模块加载。

    输出:
        {函数名: 函数对象} 映射
    """
    global _cached_eval_funcs
    if _cached_eval_funcs is not None:
        return _cached_eval_funcs

    from desktop_env.evaluators.metrics.general import (
        check_direct_json_object,
        check_include_exclude,
    )
    from desktop_env.evaluators.metrics.table import (
        compare_table,
        compare_conference_city_in_order,
    )
    from desktop_env.evaluators.metrics.docs import (
        compare_references,
        compare_docx_files,
        compare_docx_files_and_ignore_new_lines,
    )
    from desktop_env.evaluators.metrics.slides import compare_pptx_files
    from desktop_env.evaluators.metrics.chrome import (
        compare_pdfs,
        compare_archive,
        is_expected_bookmarks,
    )
    from desktop_env.evaluators.metrics.vlc import compare_images
    from desktop_env.evaluators.metrics.vscode import compare_text_file

    _cached_eval_funcs = {
        "check_direct_json_object": check_direct_json_object,
        "check_include_exclude": check_include_exclude,
        "compare_table": compare_table,
        "compare_conference_city_in_order": compare_conference_city_in_order,
        "compare_references": compare_references,
        "compare_docx_files": compare_docx_files,
        "compare_docx_files_and_ignore_new_lines": compare_docx_files_and_ignore_new_lines,
        "compare_pptx_files": compare_pptx_files,
        "compare_pdfs": compare_pdfs,
        "compare_archive": compare_archive,
        "compare_images": compare_images,
        "compare_text_file": compare_text_file,
        "is_expected_bookmarks": is_expected_bookmarks,
    }
    return _cached_eval_funcs


def _dispatch_eval(
    func_name: str,
    result_data: Any,
    expected_data: Any,
    options: Dict[str, Any],
    log: logging.Logger,
) -> float:
    """
    根据评测函数名分发到对应的 metrics 函数。

    不同函数的参数签名差异:
      - check_direct_json_object(result, rules) — result 是 JSON 字符串
      - compare_table(result, expected, **options) — 文件路径 + options 含 rules
      - compare_archive(pred_path, gold_path, **kwargs) — 文件路径 + kwargs
      - compare_pptx_files(file1, file2, **options) — 文件路径 + options
      - compare_pdfs(pdf1, pdf2) — 文件路径，无 options
      - compare_images(file1, file2) — 文件路径，无 options
      - compare_conference_city_in_order(file1, rules) — 文件路径 + rules，无 options
      - is_expected_bookmarks(bookmarks, rules) — result 数据 + rules，无 options
      - compare_references(file1, file2, **options) — 文件路径 + options

    输入:
        func_name: 评测函数名
        result_data: 结果数据（文件路径或命令输出字符串）
        expected_data: 期望数据（文件路径或 rules 字典）
        options: evaluator.options
        log: logger
    输出:
        float 评分 0.0 ~ 1.0
    """
    func_map = _load_eval_funcs()
    func = func_map.get(func_name)

    if func is None:
        log.error("未注册的评测函数: %s (可用: %s)", func_name, list(func_map.keys()))
        return 0.0

    log.info("调用评测函数: %s", func_name)

    try:
        # check_direct_json_object / check_include_exclude 签名特殊：(result, rules_dict)
        if func_name in {"check_direct_json_object", "check_include_exclude"}:
            return float(func(result_data, expected_data))

        # 部分 OSWorld 原生函数不接受 **options
        if func_name in {
            "compare_pdfs",
            "compare_images",
            "compare_conference_city_in_order",
            "is_expected_bookmarks",
        }:
            return float(func(result_data, expected_data))

        # 其余函数统一签名: (result_path, expected_path, **options)
        return float(func(result_data, expected_data, **options))

    except Exception as exc:
        log.error("评测函数 %s 执行异常: %s", func_name, exc, exc_info=True)
        return 0.0


# ============================================================
# 主入口
# ============================================================

def evaluate_osworld_task(
    evaluator_json_path: str,
    vm_ip: str,
    vm_port: int,
    shared_host_dir: str,
    log: logging.Logger,
    save_result_dir: str = "",
) -> Dict[str, Any]:
    """
    使用 OSWorld JSON 配置评估任务结果。

    完整流程:
      1. 加载 OSWorld JSON 评测配置
      2. 执行 postconfig（下载评测脚本、Ctrl+S 保存文件等）
      3. 获取 result（从 VM 文件或命令行输出）
      4. 获取 expected（从 HuggingFace 下载或读取规则）
      5. 分发到对应评测函数
      6. 返回评分结果

    输入:
        evaluator_json_path: OSWorld JSON 配置文件的完整路径
        vm_ip: VM 宿主机 IP
        vm_port: VM API 端口
        shared_host_dir: 宿主机共享目录（如 /home/agentlab/shared/group_0）
        log: logger
        save_result_dir: 可选；保存评测 result 产物的本地目录

    输出:
        {"score": float, "pass": bool, "reason": str, "func": str, ...}
    """
    # 1. 加载 JSON
    try:
        with open(evaluator_json_path, "r", encoding="utf-8") as f:
            osw_config = json.load(f)
    except Exception as exc:
        log.error("加载 OSWorld JSON 失败: %s → %s", evaluator_json_path, exc)
        return {
            "score": -1.0, "pass": False, "status": "evaluator_error",
            "reason": f"JSON 加载失败: {exc}", "func": "",
        }

    evaluator = osw_config.get("evaluator", {})
    func_name = evaluator.get("func", "")
    postconfig = evaluator.get("postconfig", [])
    result_cfg = evaluator.get("result", {})
    expected_cfg = evaluator.get("expected", {})
    options = evaluator.get("options", {})

    if not func_name:
        return {
            "score": -1.0, "pass": False, "status": "evaluator_error",
            "reason": "JSON 缺少 evaluator.func", "func": "",
        }

    log.info("=" * 50)
    log.info("OSWorld 评测开始: func=%s", func_name)
    if isinstance(result_cfg, dict):
        log.info("  result_type=%s, expected_type=%s",
                 result_cfg.get("type"),
                 expected_cfg.get("type") if isinstance(expected_cfg, dict) else None)

    # 创建临时工作目录
    work_dir = tempfile.mkdtemp(prefix="osw_eval_")
    saved_result_path = ""

    try:
        # 2. 执行 postconfig
        if postconfig:
            log.info("执行 postconfig (%d 步)...", len(postconfig))
            _run_postconfig(postconfig, vm_ip, vm_port, shared_host_dir, log)

        # 归一化为并行列表：func 为 list 时表示多指标合取（全部通过才算 pass），
        # result / expected 为与 func 等长的并行列表；func 为标量时保持单指标行为。
        if isinstance(func_name, list):
            func_names = list(func_name)
            result_cfgs = result_cfg if isinstance(result_cfg, list) else [result_cfg]
            expected_cfgs = expected_cfg if isinstance(expected_cfg, list) else [expected_cfg]
            options_list = options if isinstance(options, list) else [options] * len(func_names)
            if not (len(func_names) == len(result_cfgs) == len(expected_cfgs)):
                return {
                    "score": -1.0,
                    "pass": False,
                    "status": "evaluator_error",
                    "reason": (
                        "多指标配置长度不一致: "
                        f"func={len(func_names)} result={len(result_cfgs)} "
                        f"expected={len(expected_cfgs)}"
                    ),
                    "func": func_names,
                }
        else:
            func_names = [func_name]
            result_cfgs = [result_cfg]
            expected_cfgs = [expected_cfg]
            options_list = [options]

        # 3~5. 逐指标获取 result / expected 并分发评测，取最小分（合取）
        sub_scores: List[float] = []
        missing_result_reasons: List[str] = []
        for idx, (fn, rcfg, ecfg) in enumerate(
            zip(func_names, result_cfgs, expected_cfgs)
        ):
            opt = options_list[idx] if idx < len(options_list) else {}

            log.info("[指标 %d/%d] 获取 result... func=%s",
                     idx + 1, len(func_names), fn)
            result_data, _result_type = _get_result(
                rcfg, vm_ip, vm_port, shared_host_dir, work_dir, log,
            )
            if result_data is _AGENT_RESULT_MISSING:
                reason = f"指标 {idx + 1}: 未找到应由 agent 产出的背景图"
                log.info("%s，按正常失败计 0 分", reason)
                missing_result_reasons.append(reason)
                sub_scores.append(0.0)
                continue
            if result_data is None:
                return {
                    "score": -1.0,
                    "pass": False,
                    "status": "evaluator_error",
                    "reason": f"获取评测结果失败 (指标 {idx + 1}: {fn})",
                    "func": func_name,
                }
            # 仅持久化首个指标的 result 产物（保持既有单指标行为不变）
            if idx == 0:
                saved_result_path = _persist_result_data(
                    result_data, _result_type, save_result_dir, log,
                )

            log.info("[指标 %d/%d] 获取 expected...", idx + 1, len(func_names))
            expected_data, _expected_type = _get_expected(ecfg, work_dir, log)
            if expected_data is None:
                result = {
                    "score": -1.0,
                    "pass": False,
                    "status": "evaluator_error",
                    "reason": f"获取期望结果失败 (指标 {idx + 1}: {fn})",
                    "func": func_name,
                }
                if saved_result_path:
                    result["saved_result_path"] = saved_result_path
                return result

            sub = _dispatch_eval(fn, result_data, expected_data, opt, log)
            sub_val = float(sub) if isinstance(sub, (int, float)) else 0.0
            log.info("[指标 %d/%d] func=%s -> score=%.4f",
                     idx + 1, len(func_names), fn, sub_val)
            sub_scores.append(sub_val)

        # 合取：整体分为各子指标最小值，全部为 1.0 才通过
        score_val = min(sub_scores) if sub_scores else 0.0
        # 严格通过：仅当 score == 1.0 才算 pass（与 operation_evaluator 对齐）
        passed = score_val >= 1.0 - 1e-9

        log.info("OSWorld 评测完成: func=%s, sub_scores=%s, score=%.4f, pass=%s",
                 func_name, sub_scores, score_val, passed)

        result = {
            "score": score_val,
            "pass": passed,
            "status": "ok",
            "reason": f"OSWorld {func_name}: score={score_val:.4f}/1.00",
            "func": func_name,
        }
        if missing_result_reasons:
            result["reason"] += "; " + "; ".join(missing_result_reasons)
        if saved_result_path:
            result["saved_result_path"] = saved_result_path
        return result

    except Exception as exc:
        log.error("OSWorld 评测异常: %s", exc, exc_info=True)
        result = {
            "score": -1.0, "pass": False, "status": "evaluator_error",
            "reason": f"评测异常: {exc}", "func": func_name,
        }
        if saved_result_path:
            result["saved_result_path"] = saved_result_path
        return result

    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
