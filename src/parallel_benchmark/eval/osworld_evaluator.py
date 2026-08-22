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
import math
import os
import re
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
from parallel_benchmark.eval.json_object_metric import (
    check_direct_json_object as _check_direct_json_object,
)


_AGENT_RESULT_MISSING = object()
_REMOTE_FILE_PRESENT = "present"
_REMOTE_FILE_MISSING = "missing"
_REMOTE_FILE_ERROR = "error"


def _exact_match(result: Any, rules: Dict[str, Any]) -> float:
    """执行不依赖完整 OSWorld metrics 包的精确匹配。

    功能：
        实现原 ``desktop_env.evaluators.metrics.general.exact_match`` 的
        离散语义，同时避免仅评价字符串状态时加载图像、文档等重型依赖。
    输入参数：
        result: VM getter 返回的实际状态值。
        rules: OSWorld rule 对象，必须显式包含 ``expected``。
    输出返回值：
        实际值与期望值类型和值均相等时返回 ``1.0``，否则返回 ``0.0``。
    异常：
        rules 不是字典或缺少 ``expected`` 时抛出 ``ValueError``，由公开
        评价入口归入 ``evaluator_error``。
    """

    if not isinstance(rules, dict) or "expected" not in rules:
        raise ValueError("exact_match rules 必须包含 expected")
    return 1.0 if result == rules["expected"] else 0.0


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
    if not text:
        return text

    for old_prefix, new_prefix in PATH_MAPPING.items():
        old_root = old_prefix.rstrip("/")
        new_root = new_prefix.rstrip("/")
        # 路径根后必须是斜杠、空白、常见 shell 分隔符或字符串结尾，
        # 避免把 ``DesktopBackup`` 误认成 ``Desktop``。
        pattern = re.escape(old_root) + r"(?=$|[/\s'\"),;])"
        text = re.sub(pattern, new_root, text)

    # 非 Desktop/Documents 等标准目录也采用与 adapt_result_path 一致的
    # /home/user/ → /home/user/shared/ 兜底；排除已经完成的 shared 映射。
    text = re.sub(
        r"/home/user/(?!shared(?:/|$))",
        "/home/user/shared/",
        text,
    )
    if text == "/home/user":
        return "/home/user/shared"
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
        # 零字节文件也是 agent 的真实产出；应交给 metric 判为正常失败，
        # 不能在传输层把它误报成 evaluator_error。
        if proc.returncode == 0:
            log.info("  SSH 下载成功: %s (%d bytes)",
                     os.path.basename(host_path), os.path.getsize(local_path))
            return True
        log.error("  SSH 下载失败: rc=%d, stderr=%s",
                  proc.returncode, proc.stderr.decode(errors="replace")[:300])
        return False
    except Exception as exc:
        log.error("  SSH 下载异常: %s", exc)
        return False


def _remote_file_status(
    vm_ip: str,
    host_path: str,
    log: logging.Logger,
) -> str:
    """
    判定宿主机结果路径是存在、确实缺失，还是无法可靠探测。

    该探测用于区分两类语义：agent 没有创建目标文件属于正常 FAIL；
    SSH/权限等基础设施故障属于 evaluator_error。远端退出码 2 专门表示
    路径不存在，其余非零码均保守归为基础设施异常。

    输入:
        vm_ip: VM 所在宿主机 IP。
        host_path: 宿主机共享目录中的目标文件路径。
        log: 记录探测异常的 logger。
    输出:
        ``present``、``missing`` 或 ``error`` 三态字符串。
    """
    try:
        creds = _get_ssh_creds(vm_ip)
        env = os.environ.copy()
        env["SSHPASS"] = creds["ssh_password"]
        quoted_path = shlex.quote(host_path)
        remote_cmd = (
            f"if [ -f {quoted_path} ]; then exit 0; "
            f"elif [ ! -e {quoted_path} ]; then exit 2; "
            "else exit 3; fi"
        )
        cmd = (
            ["sshpass", "-e", "ssh"]
            + creds["ssh_opts"]
            + [creds["ssh_host"], remote_cmd]
        )
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=30,
        )
    except Exception as exc:
        log.error("  结果路径探测异常: %s", exc)
        return _REMOTE_FILE_ERROR

    if proc.returncode == 0:
        return _REMOTE_FILE_PRESENT
    if proc.returncode == 2:
        return _REMOTE_FILE_MISSING

    log.error(
        "  结果路径探测失败: rc=%d, stderr=%s",
        proc.returncode,
        proc.stderr.decode(errors="replace")[:300],
    )
    return _REMOTE_FILE_ERROR


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
      - launch: 在 VM 图形会话中启动 evaluator 要求的进程
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
        elif stype == "launch":
            _cfg_launch(params, vm_ip, vm_port, log)
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
        code = _map_paths_in_string(command[2])
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
    执行 config command，并统一映射命令中的 OSWorld 工作区路径。

    输入:
        params: 含 ``command`` 列表或字符串的 config 参数。
        vm_ip / vm_port: VM API 连接信息。
        log: logger。
    输出:
        无；命令执行失败写入日志，由上层 prepare 流程处理。
    """
    command = params.get("command", [])
    if not command:
        return

    if isinstance(command, list):
        cmd_str = _build_vm_command(command)
    else:
        cmd_str = _map_paths_in_string(str(command))

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
    在 VM 图形会话中打开共享工作区内的指定文件。

    输入:
        params: 含 OSWorld 原始 ``path`` 的 config 参数。
        vm_ip / vm_port: VM API 连接信息。
        log: logger。
    输出:
        无；实际打开命令在 VM 后台执行。
    """
    original_path = params.get("path", "")
    if not original_path:
        return

    mapped_path = adapt_result_path(original_path)
    cmd = (
        "bash -c "
        f"\"DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
        f"xdg-open {shlex.quote(mapped_path)} >/tmp/osw_open.log 2>&1 &\""
    )
    log.info("    打开文件: %s -> %s", original_path, mapped_path)
    _exec_on_vm(vm_ip, vm_port, cmd, timeout=30)


def _extract_remote_debugging_port(command: Any) -> Optional[int]:
    """从 Chrome launch 命令中提取远程调试端口。

    功能：兼容 OSWorld config 使用的命令列表或字符串，以及
    ``--remote-debugging-port=1337`` 和分离参数两种写法；仅返回合法
    TCP 端口，普通应用 launch 返回 ``None``。
    输入参数：
        command: config ``launch.parameters.command`` 的原始值。
    输出返回值：
        合法远程调试端口；未声明或声明无效时返回 ``None``。
    """

    candidate = ""
    if isinstance(command, list):
        tokens = [str(token) for token in command]
        for index, token in enumerate(tokens):
            if token.startswith("--remote-debugging-port="):
                candidate = token.split("=", 1)[1]
                break
            if token == "--remote-debugging-port" and index + 1 < len(tokens):
                candidate = tokens[index + 1]
                break
    else:
        match = re.search(
            r"--remote-debugging-port(?:=|\s+)(\d+)",
            str(command or ""),
        )
        if match:
            candidate = match.group(1)

    try:
        port = int(candidate)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _wait_for_guest_chromium_cdp(
    vm_ip: str,
    vm_port: int,
    debug_port: int,
    log: logging.Logger,
    timeout_sec: float = 15.0,
    poll_interval_sec: float = 0.25,
) -> bool:
    """等待 guest 内 Chrome CDP 完成 HTTP 级就绪。

    功能：通过现有 VM ``/execute`` 边界轮询 guest localhost 上的
    ``/json/version``，并要求返回 ``Browser`` 或 WebSocket 地址。
    该门禁位于同一 OSWorld config 的后续 socat/``--new-tab`` 之前，
    防止多个 Chrome 进程并发争抢默认 profile 的 SingletonLock。
    输入参数：
        vm_ip: VM API 所在宿主地址。
        vm_port: 当前 guest 的 Python Server 映射端口。
        debug_port: Chrome 在 guest localhost 上监听的 CDP 端口。
        log: 当前任务日志器。
        timeout_sec: 最长等待秒数。
        poll_interval_sec: 两次探测之间的等待秒数。
    输出返回值：
        CDP 在期限内返回有效版本对象时为 ``True``，否则为 ``False``。
    """

    version_url = f"http://127.0.0.1:{debug_port}/json/version"
    probe_code = (
        "import json, urllib.request; "
        f"payload=json.load(urllib.request.urlopen({version_url!r}, timeout=1)); "
        "raise SystemExit("
        "0 if (payload.get('Browser') or payload.get('webSocketDebuggerUrl')) "
        "else 2)"
    )
    probe_command = f"python -c {shlex.quote(probe_code)}"
    deadline = time.monotonic() + max(0.0, timeout_sec)
    last_detail = ""

    while time.monotonic() <= deadline:
        result = _exec_on_vm(
            vm_ip,
            vm_port,
            probe_command,
            timeout=5,
        )
        if result.get("returncode") == 0:
            log.info("    guest Chrome CDP 已就绪: %s", version_url)
            return True
        last_detail = str(
            result.get("error") or result.get("output") or ""
        ).strip()
        if time.monotonic() <= deadline:
            time.sleep(max(0.0, poll_interval_sec))

    log.error(
        "    guest Chrome CDP 未在 %.1f 秒内就绪: %s | %s",
        timeout_sec,
        version_url,
        last_detail[:300],
    )
    return False


def _extract_process_termination_target(command: Any) -> Optional[str]:
    """识别可安全同步等待的简单进程终止命令。

    功能：
        仅接受 ``pkill <name>`` 与 ``killall <name>`` 两段式命令，提取
        精确进程名。带选项、管道或复合 shell 语句保持普通 launch
        语义，避免错误解释不受控命令。
    输入参数：
        command: OSWorld ``launch.parameters.command`` 的列表或字符串。
    输出返回值：
        命中安全形式时返回进程名；其它形式返回 ``None``。
    """

    if isinstance(command, list):
        tokens = [str(token) for token in command]
    else:
        try:
            tokens = shlex.split(str(command))
        except ValueError:
            return None
    if len(tokens) != 2:
        return None
    executable = os.path.basename(tokens[0])
    target = tokens[1]
    if executable not in {"pkill", "killall"} or target.startswith("-"):
        return None
    return target


def _build_synchronous_termination_command(
    command_body: str,
    process_name: str,
) -> str:
    """构造“终止并等待进程完全退出”的 VM shell 命令。

    功能：
        同步执行 OSWorld postconfig 中的简单 ``pkill/killall``，接受
        “进程不存在”的返回码 1，并轮询精确进程名至退出。这样后续
        Chrome/VLC 重启不会与仍在后台运行的终止命令发生竞态。
    输入参数：
        command_body: 已完成参数引用与路径映射的终止命令。
        process_name: 需要等待消失的精确进程名。
    输出返回值：
        可提交给 VM ``/execute`` 的 ``bash -c`` 命令字符串；五秒内
        进程未退出时以 124 返回，交由调用方升级为 ``RuntimeError``。
    """

    quoted_name = shlex.quote(process_name)
    script = (
        f"{command_body}; rc=$?; "
        'if [ "$rc" -gt 1 ]; then exit "$rc"; fi; '
        "for _osw_i in $(seq 1 50); do "
        f"if ! pgrep -x -- {quoted_name} >/dev/null 2>&1; then exit 0; fi; "
        "sleep 0.1; "
        "done; "
        "exit 124"
    )
    return f"bash -c {shlex.quote(script)}"


def _cfg_launch(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    启动应用，并使其文件参数与 execute/result 使用相同路径映射。

    简单 ``pkill/killall`` 终止命令会同步等待目标进程完全退出；其它
    应用仍在图形会话中后台启动。该区分保证 OSWorld 常见的
    ``pkill chrome → google-chrome`` postconfig 严格按顺序执行。

    输入:
        params: 含 ``command`` 列表或字符串的 config 参数。
        vm_ip / vm_port: VM API 连接信息。
        log: logger。
    输出:
        无；终止命令完成后返回，其它应用在 VM 图形会话中后台启动。
    异常:
        RuntimeError: VM API 返回非零码，说明必需进程未可靠启动。
    """
    command = params.get("command", [])
    if not command:
        return

    debug_port = _extract_remote_debugging_port(command)
    if isinstance(command, list):
        cmd_body = _build_vm_command(command)
    else:
        cmd_body = _map_paths_in_string(str(command))

    termination_target = _extract_process_termination_target(command)
    if termination_target is not None:
        cmd = _build_synchronous_termination_command(
            cmd_body,
            termination_target,
        )
        log.info("    同步终止进程: %s", termination_target)
    else:
        cmd = (
            'bash -c "'
            "DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus "
            f"nohup {cmd_body} >/tmp/osw_launch.log 2>&1 &\""
        )
        log.info("    启动进程: %s", cmd_body[:200])
    result = _exec_on_vm(vm_ip, vm_port, cmd, timeout=30)
    returncode = result.get("returncode", -1)
    if returncode != 0:
        detail = result.get("error") or result.get("output", "")
        raise RuntimeError(
            f"launch 执行失败（returncode={returncode}）: {str(detail)[:200]}"
        )
    if debug_port is not None and not _wait_for_guest_chromium_cdp(
        vm_ip,
        vm_port,
        debug_port,
        log,
    ):
        raise RuntimeError(
            f"launch 已提交，但 guest Chrome CDP 端口 {debug_port} 未就绪"
        )


def _cfg_chrome_open_tabs(
    params: Dict[str, Any],
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> None:
    """
    config chrome_open_tabs: 打开若干 Chrome 标签页。

    输入:
        params: 含 ``urls_to_open`` URL 列表的 config 参数。
        vm_ip / vm_port: VM API 连接信息。
        log: logger。
    输出:
        无；Chrome 在图形会话中后台打开指定标签页。
    异常:
        RuntimeError: VM API 返回非零码，说明必需标签页未可靠打开。
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
    result = _exec_on_vm(vm_ip, vm_port, cmd, timeout=30)
    returncode = result.get("returncode", -1)
    if returncode != 0:
        detail = result.get("error") or result.get("output", "")
        raise RuntimeError(
            "chrome_open_tabs 执行失败"
            f"（returncode={returncode}）: {str(detail)[:200]}"
        )


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
    *,
    result_provider: Optional[Any] = None,
) -> Tuple[Any, str]:
    """
    获取评测结果数据。

    输入:
        result_config: evaluator.result 配置
        vm_ip / vm_port: VM 连接信息
        shared_host_dir: 宿主机共享目录
        work_dir: 本地临时工作目录
        log: logger
        result_provider: 可选的活动页结果提供器；需实现
            ``get_result(result_config) -> (data, result_type)``。仅
            ``active_tab_url_parse`` 和 ``active_tab_html_parse`` 使用。
    输出:
        (data, type_str)
        - vm_file → data 是本地文件路径
        - vm_command_line → data 是命令 stdout 字符串
        - active_tab_* → data 由注入的 result_provider 投影
    异常:
        active-tab result 未注入 provider，或 provider 本身读取失败时抛出
        异常，由 ``evaluate_osworld_task`` 统一返回 evaluator_error。
    """
    rtype = result_config.get("type", "")

    if rtype in {"active_tab_url_parse", "active_tab_html_parse"}:
        if result_provider is None:
            raise RuntimeError(
                f"{rtype} 需要注入 result_provider，无法使用通用 VM getter"
            )
        return result_provider.get_result(result_config)

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

    if rtype == "bookmarks":
        bookmarks = _get_result_bookmarks(vm_ip, vm_port, log)
        return bookmarks, "bookmarks"

    if rtype == "profile_name":
        profile_name = _get_result_profile_name(vm_ip, vm_port, log)
        return profile_name, "profile_name"

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

    if not paths or any(not isinstance(path, str) or not path for path in paths):
        raise ValueError("multi path 必须是非空字符串列表")
    if isinstance(raw_dest, list) and len(dests) != len(paths):
        raise ValueError(
            f"multi path/dest 长度不一致: path={len(paths)} dest={len(dests)}"
        )
    if len(dests) != len(paths) or any(
        not isinstance(dest, str) or not dest for dest in dests
    ):
        raise ValueError("multi dest 必须与 path 一一对应且均为非空字符串")

    raw_gives = cfg.get("gives", [0])
    if not isinstance(raw_gives, (list, tuple, set)):
        raise ValueError("multi gives 必须是下标列表")
    gives = set(raw_gives)
    if not gives or any(
        not isinstance(index, int) or index < 0 or index >= len(paths)
        for index in gives
    ):
        raise ValueError("multi gives 含越界或非法下标")
    return paths, dests, gives


def _get_result_file(
    cfg: Dict[str, Any],
    vm_ip: str,
    shared_host_dir: str,
    work_dir: str,
    log: logging.Logger,
) -> Any:
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
    given: List[Any] = []
    for i, (original_path, dest_name) in enumerate(zip(paths, dests)):
        vm_path = adapt_result_path(original_path)
        host_path = _vm_path_to_host_path(vm_path, shared_host_dir)
        local_path = os.path.join(result_dir, dest_name)

        log.info("  [%d] 原始路径: %s", i, original_path)
        log.info("      宿主机路径: %s", host_path)

        remote_status = _remote_file_status(vm_ip, host_path, log)
        if remote_status == _REMOTE_FILE_MISSING:
            log.info("      结果文件不存在")
            if i in gives:
                given.append(_AGENT_RESULT_MISSING)
            # 非 gives 文件是 metric 所需的旁挂产物；缺失仍属于 agent
            # 产出不完整，保留本地缺失状态让 metric 正常判 0。
            continue
        if remote_status == _REMOTE_FILE_ERROR:
            return None

        if not _ssh_download_file(vm_ip, host_path, local_path, log):
            # 文件已确认存在却下载失败，只能归因于 SSH/权限/传输故障。
            return None
        if i in gives:
            given.append(local_path)

    if not given:
        return None
    if any(item is _AGENT_RESULT_MISSING for item in given):
        return _AGENT_RESULT_MISSING
    # gives 只含单个下标（含默认 {0}）时返回字符串，兼容 compare_table 等按 str 路径消费的评测函数
    return given[0] if len(given) == 1 else given


def _extract_slide_background_image(
    pptx_local_path: str,
    slide_index: int,
    dest_path: str,
    log: logging.Logger,
) -> Optional[str]:
    """从本地 PPTX 提取指定幻灯片的背景图片。

    移植自 desktop_env/evaluators/getters/impress.py:get_background_image_in_slide，
    但改为直接对本地文件操作（本评测走 SSH+共享目录，没有 OSWorld env/get_vm_file）。

    输入参数：pptx_local_path 为本地 PPTX；slide_index 为零起始页码；
    dest_path 为图片输出路径；log 为日志器。
    输出返回值：成功时返回 dest_path；页、关系或图片缺失及异常时返回 None。
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
    """获取 ``background_image_in_slide`` 类型的评价结果。

    输入参数：cfg 为结果配置；vm_ip 为 VM 地址；shared_host_dir 为共享
    目录；work_dir 为本地临时目录；log 为日志器。
    输出返回值：成功时返回背景图片本地路径；Agent 未生成结果时返回
    ``_AGENT_RESULT_MISSING``；传输或配置故障时返回 None。
    """
    ppt_file_path = cfg.get("ppt_file_path", "")
    slide_index = int(cfg.get("slide_index", 0))
    dest_name = cfg.get("dest") or "background_image.png"

    pptx_local = _get_result_file(
        {"path": ppt_file_path, "dest": os.path.basename(ppt_file_path)},
        vm_ip, shared_host_dir, work_dir, log,
    )
    if pptx_local is _AGENT_RESULT_MISSING:
        return _AGENT_RESULT_MISSING
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


def _get_result_profile_name(
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> Optional[str]:
    """从 Linux VM 的 Chrome Preferences 读取 Profile 名称。

    功能：
        通过 VM API 只读获取
        ``~/.config/google-chrome/Default/Preferences``，严格解析
        ``profile.name``，供 OSWorld ``profile_name`` result 使用。
    输入参数：
        vm_ip: VM 所在宿主机地址。
        vm_port: VM Python API 端口。
        log: 当前评价日志器。
    输出返回值：
        ``profile.name`` 的字符串值；文件读取或 JSON 解析失败时返回
        ``None``，由公开评价入口归入 ``evaluator_error``。文件可读且
        JSON 合法、但目标结构或字段不存在时返回空字符串，使其按
        Agent 的可评分错误状态正常 FAIL，避免删除字段逃避评分。
    """

    command = 'cat "$HOME/.config/google-chrome/Default/Preferences"'
    result = _exec_on_vm(vm_ip, vm_port, command, timeout=60)
    if result.get("returncode", -1) != 0:
        log.error(
            "读取 Chrome Preferences 失败: %s",
            (result.get("error") or result.get("output", ""))[:300],
        )
        return None

    try:
        payload = json.loads(result.get("output", ""))
    except (TypeError, json.JSONDecodeError) as exc:
        log.error("解析 Chrome Preferences 失败: %s", exc)
        return None

    if not isinstance(payload, dict):
        log.warning("Chrome Preferences 根节点不是对象，按缺失名称评价")
        return ""
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        log.warning("Chrome Preferences 缺少 profile 对象，按缺失名称评价")
        return ""
    profile_name = profile.get("name")
    if not isinstance(profile_name, str):
        log.warning(
            "Chrome Preferences 缺少字符串 profile.name，按缺失名称评价"
        )
        return ""
    return profile_name


def _get_result_bookmarks(
    vm_ip: str,
    vm_port: int,
    log: logging.Logger,
) -> Optional[Dict[str, Any]]:
    """
    从 Linux VM 读取 Chrome Bookmarks，并返回 OSWorld metric 所需 roots。

    输入:
        vm_ip / vm_port: VM API 连接信息。
        log: logger。
    输出:
        Chrome ``roots`` 字典；文件读取或 JSON 解析失败时返回 ``None``，
        由主评测流程归类为 evaluator_error。
    """
    command = "cat \"$HOME/.config/google-chrome/Default/Bookmarks\""
    result = _exec_on_vm(vm_ip, vm_port, command, timeout=60)
    if result.get("returncode", -1) != 0:
        log.error(
            "读取 Chrome Bookmarks 失败: %s",
            (result.get("error") or result.get("output", ""))[:300],
        )
        return None

    try:
        payload = json.loads(result.get("output", ""))
    except (TypeError, json.JSONDecodeError) as exc:
        log.error("解析 Chrome Bookmarks 失败: %s", exc)
        return None

    roots = payload.get("roots", {}) if isinstance(payload, dict) else {}
    return roots if isinstance(roots, dict) else None


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
        all_downloaded = True
        for i, (url, dest) in enumerate(zip(paths, dests)):
            local_path = os.path.join(expected_dir, dest)
            log.info("  [%d] %s", i, url[:120])
            ok = _download_url_to_local(url, local_path, log)
            all_downloaded = all_downloaded and ok
            if i in gives:
                given.append(local_path if ok else None)

        # expected 中的旁挂文件也是 evaluator gold 的组成部分；任一下载失败
        # 都必须上报 evaluator_error，不能让 metric 因本地文件缺失而记 agent 0 分。
        if not all_downloaded or not given or any(path is None for path in given):
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

    from desktop_env.evaluators.metrics.general import check_include_exclude
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
    from desktop_env.evaluators.metrics.image import compare_images
    from desktop_env.evaluators.metrics.vscode import compare_text_file

    _cached_eval_funcs = {
        "check_direct_json_object": _check_direct_json_object,
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
    if func_name in {"check_direct_json_object", "exact_match"}:
        # 两个离散指标始终分发到轻量唯一实现，不读取进程级完整 metric
        # 缓存。这样既避免无关图像依赖，也保证任务结果不受 worker
        # 执行顺序影响。
        func_map = {
            "check_direct_json_object": _check_direct_json_object,
            "exact_match": _exact_match,
        }
    else:
        func_map = _load_eval_funcs()
    func = func_map.get(func_name)

    if func is None:
        available = ", ".join(sorted(func_map))
        raise LookupError(f"未注册的评测函数: {func_name}; 可用: {available}")

    log.info("调用评测函数: %s", func_name)
    options = options or {}

    try:
        # 三个离散规则指标签名特殊：(result, rules_dict)
        if func_name in {
            "check_direct_json_object",
            "check_include_exclude",
            "exact_match",
        }:
            return float(func(result_data, expected_data))

        # 部分 OSWorld 原生函数不接受 **options
        if func_name in {
            "compare_pdfs",
            "compare_conference_city_in_order",
            "is_expected_bookmarks",
        }:
            return float(func(result_data, expected_data))

        # 其余函数统一签名: (result_path, expected_path, **options)
        return float(func(result_data, expected_data, **options))

    except Exception as exc:
        log.error("评测函数 %s 执行异常: %s", func_name, exc, exc_info=True)
        raise RuntimeError(f"评测函数 {func_name} 执行异常: {exc}") from exc


def _validate_metric_score(func_name: str, score: Any) -> float:
    """
    校验 metric 返回值，防止 NaN、无穷值或越界值污染汇总。

    输入:
        func_name: 当前 metric 名称，用于错误信息。
        score: metric 原始返回值。
    输出:
        位于 ``[0, 1]`` 的有限浮点评分。
    异常:
        返回值不是有效分数时抛出 ``ValueError``，由主流程记 evaluator_error。
    """
    if not isinstance(score, (int, float)):
        raise ValueError(f"metric {func_name} 返回非数值: {type(score).__name__}")
    value = float(score)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"metric {func_name} 返回非法分数: {value!r}")
    return value


def _metric_score_threshold(func_name: str, options: Dict[str, Any]) -> float:
    """
    读取单项 metric 的显式通过阈值，缺省保持离散指标 1.0 契约。

    输入:
        func_name: metric 名称，用于错误信息。
        options: evaluator.options；连续指标可声明 ``score_threshold``。
    输出:
        位于 ``[0, 1]`` 的有限浮点阈值。
    异常:
        配置无效时抛出 ``ValueError``，避免静默改变通过语义。
    """
    raw_threshold = (options or {}).get("score_threshold", 1.0)
    if not isinstance(raw_threshold, (int, float)):
        raise ValueError(f"metric {func_name} 的 score_threshold 不是数值")
    threshold = float(raw_threshold)
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError(f"metric {func_name} 的 score_threshold 越界: {threshold!r}")
    return threshold


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
    *,
    result_provider: Optional[Any] = None,
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
        result_provider: 可选活动页结果提供器；同一对象会按配置顺序供所有
            active-tab 子指标复用，以保证多指标读取同一浏览器状态。

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
            if not (
                len(func_names)
                == len(result_cfgs)
                == len(expected_cfgs)
                == len(options_list)
            ):
                return {
                    "score": -1.0,
                    "pass": False,
                    "status": "evaluator_error",
                    "reason": (
                        "多指标配置长度不一致: "
                        f"func={len(func_names)} result={len(result_cfgs)} "
                        f"expected={len(expected_cfgs)} options={len(options_list)}"
                    ),
                    "func": func_names,
                }
        else:
            func_names = [func_name]
            result_cfgs = [result_cfg]
            expected_cfgs = [expected_cfg]
            options_list = [options]

        if not func_names:
            raise ValueError("evaluator.func 不能为空列表")

        # 3~5. 逐指标获取 result / expected 并分发评测；原始分保留用于分析，
        # 通过与否按各指标显式阈值合取。
        sub_scores: List[float] = []
        sub_thresholds: List[float] = []
        sub_passes: List[bool] = []
        missing_result_reasons: List[str] = []
        for idx, (fn, rcfg, ecfg) in enumerate(
            zip(func_names, result_cfgs, expected_cfgs)
        ):
            opt = options_list[idx] if idx < len(options_list) else {}
            opt = opt or {}
            threshold = _metric_score_threshold(fn, opt)
            sub_thresholds.append(threshold)

            log.info("[指标 %d/%d] 获取 result... func=%s",
                     idx + 1, len(func_names), fn)
            if result_provider is None:
                # 保留旧的 6 个位置参数调用形状，兼容既有测试及外部代码对
                # ``_get_result`` 的轻量 monkeypatch。
                result_data, _result_type = _get_result(
                    rcfg, vm_ip, vm_port, shared_host_dir, work_dir, log,
                )
            else:
                result_data, _result_type = _get_result(
                    rcfg,
                    vm_ip,
                    vm_port,
                    shared_host_dir,
                    work_dir,
                    log,
                    result_provider=result_provider,
                )
            if result_data is _AGENT_RESULT_MISSING:
                reason = f"指标 {idx + 1}: 未找到应由 agent 产出的结果文件或背景图"
                log.info("%s，按正常失败计 0 分", reason)
                missing_result_reasons.append(reason)
                sub_scores.append(0.0)
                sub_passes.append(False)
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
            sub_val = _validate_metric_score(fn, sub)
            sub_pass = sub_val >= threshold - 1e-9
            log.info(
                "[指标 %d/%d] func=%s -> score=%.4f threshold=%.4f pass=%s",
                idx + 1, len(func_names), fn, sub_val, threshold, sub_pass,
            )
            sub_scores.append(sub_val)
            sub_passes.append(sub_pass)

        # 合取：整体原始分取最小值，所有子指标达到各自阈值才通过。
        score_val = min(sub_scores) if sub_scores else 0.0
        passed = bool(sub_passes) and all(sub_passes)

        log.info(
            "OSWorld 评测完成: func=%s, sub_scores=%s, thresholds=%s, "
            "score=%.4f, pass=%s",
            func_name, sub_scores, sub_thresholds, score_val, passed,
        )

        result = {
            "score": score_val,
            "pass": passed,
            "status": "ok",
            "reason": f"OSWorld {func_name}: score={score_val:.4f}/1.00",
            "func": func_name,
            "sub_scores": sub_scores,
            "score_thresholds": sub_thresholds,
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
