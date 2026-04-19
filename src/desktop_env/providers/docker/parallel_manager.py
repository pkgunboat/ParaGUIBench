"""
Docker 并行容器组管理器。

复用 DockerProvider 的端口扫描机制（从 VNC=8006, Server=5000, Chromium=9222, VLC=8080
向上递增查找可用端口），扩展为多组并行容器管理。同时提供内存预算管理和容器保护机制，
确保 WebMall/OnlyOffice 等服务不会被误删或端口冲突。

不修改 DockerProvider 原始代码。通过共享同一把 FileLock 实现互斥。

依赖:
    - psutil: 扫描系统端口
    - docker: 扫描 Docker 容器端口
    - filelock: 端口分配互斥锁（与 DockerProvider 共享 /tmp/docker_port_allocation.lck）
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import psutil
from filelock import FileLock

logger = logging.getLogger("desktopenv.providers.docker.parallel_manager")

# ============================================================
# 保护配置 — WebMall / OnlyOffice 等共存服务
# ============================================================

# 保护端口 — 即使 WebMall/OnlyOffice 未运行也绝不分配给 QEMU VM
PROTECTED_PORTS: frozenset = frozenset({
    # OnlyOffice
    80, 443, 5001,
    # WebMall docker-compose 映射端口
    8081, 8082, 8083, 8084,
    # WebMall 商店实际访问端口
    9081, 9082, 9083, 9084, 9085,
    # Elasticsearch
    9200,
})

# 保护容器名称前缀 — 删除容器时若匹配则跳过
PROTECTED_CONTAINER_PREFIXES: tuple = (
    "WebMall_",
    "onlyoffice-",
)

# ============================================================
# 端口扫描配置 — 与 DockerProvider 完全一致的起始端口
# ============================================================

# 各端口类型的扫描起始值（来自 DockerProvider.start_emulator）
PORT_START: Dict[str, int] = {
    "server": 5000,
    "vnc": 8006,
    "chromium": 9222,
    "vlc": 8080,
}

# 与 DockerProvider 共享的 FileLock 路径
_TEMP_DIR = Path("/tmp") if platform.system() != "Windows" else Path("C:/temp")
LOCK_FILE: str = str(_TEMP_DIR / "docker_port_allocation.lck")

# 端口扫描上限
_PORT_MAX = 65354

# 进程内已预留但尚未实际占用的端口（解决并行分配时的 TOCTOU 竞争）
# key: port, value: group_id（方便按组释放）
_reserved_ports: Dict[int, int] = {}
_reserved_lock = threading.Lock()


# ============================================================
# 容器组配置数据类
# ============================================================

@dataclass
class ContainerSetConfig:
    """
    一组容器的完整配置（对应一个并行任务槽位）。

    属性:
        group_id: 组编号（0, 1, 2, ...），用于容器命名和共享目录隔离
        num_vms: 该组 VM 数量（1-5）
        vm_memory: QEMU 内存大小，如 "1G"、"2G"，传入 Docker 环境变量 RAM_SIZE
        vm_cpu_cores: QEMU CPU 核数，如 "1"、"2"，传入 Docker 环境变量 CPU_CORES
        containers: 各 VM 的端口/名称配置列表
        shared_host_dir: 宿主机上该组的共享目录路径
        vm_ip: Docker 宿主机 IP
        docker_image: Docker 镜像名
        qcow2_path: VM 磁盘镜像路径
    """
    group_id: int
    num_vms: int
    vm_memory: str = "1G"
    vm_cpu_cores: str = "1"
    containers: List[Dict[str, object]] = field(default_factory=list)
    shared_host_dir: str = ""
    vm_ip: str = "10.1.110.114"
    docker_image: str = "happysixd/osworld-docker-sshfs"
    qcow2_path: str = ""

    def get_server_ports(self) -> List[int]:
        """
        获取该组所有 VM 的 server 端口列表（用于 PythonController 连接）。

        输出:
            server_port 列表，如 [5000, 5005, 5014]
        """
        return [int(c["server_port"]) for c in self.containers]

    def get_vm_pairs(self) -> List[tuple]:
        """
        获取 (server_port, vnc_port) 配对列表（用于 init_vm）。

        输出:
            [(server_port, vnc_port), ...]
        """
        return [(int(c["server_port"]), int(c["vnc_port"])) for c in self.containers]


# ============================================================
# 端口分配（复用 DockerProvider 扫描机制）
# ============================================================

def _get_used_ports() -> Set[int]:
    """
    获取所有已占用端口（系统 + Docker + 保护列表）。
    逻辑与 DockerProvider._get_used_ports() 一致，额外叠加 PROTECTED_PORTS。

    输出:
        已占用端口集合
    """
    # 系统端口（与 DockerProvider 一致：psutil.net_connections）
    system_ports: Set[int] = set()
    try:
        system_ports = {conn.laddr.port for conn in psutil.net_connections()}
    except (psutil.AccessDenied, OSError) as exc:
        logger.warning("psutil.net_connections() 失败: %s，仅使用 Docker 端口", exc)

    # Docker 容器端口（与 DockerProvider 一致：docker API）
    docker_ports: Set[int] = set()
    try:
        import docker
        client = docker.from_env()
        for container in client.containers.list():
            ports = container.attrs.get("NetworkSettings", {}).get("Ports")
            if ports:
                for port_mappings in ports.values():
                    if port_mappings:
                        docker_ports.update(
                            int(p["HostPort"]) for p in port_mappings
                            if p.get("HostPort")
                        )
    except Exception as exc:
        logger.warning("Docker API 端口扫描失败: %s，仅使用系统端口", exc)

    return system_ports | docker_ports | set(PROTECTED_PORTS)


def allocate_ports_for_group(
    num_vms: int,
    group_id: int,
    extra_used_ports: Optional[Set[int]] = None,
) -> List[Dict[str, object]]:
    """
    在 FileLock 保护下为一组 VM 分配端口。

    从 PORT_START 各起始值（VNC=8006, Server=5000, Chromium=9222, VLC=8080）
    开始递增扫描，跳过已占用端口和 PROTECTED_PORTS。
    所有 num_vms * 4 个端口在同一次加锁期间完成，避免 TOCTOU 竞争。
    使用与 DockerProvider 相同的 FileLock 路径，保证互斥。

    输入:
        num_vms: 要分配的 VM 数量
        group_id: 组编号，用于容器命名
        extra_used_ports: 额外的已占用端口集合（如远程机器上的端口），
                          与本地扫描结果合并，确保不分配这些端口

    输出:
        容器配置列表，每项包含:
        {"name": "osworld-g0-vm1", "server_port": 5000, "vnc_port": 8006,
         "chromium_port": 9222, "vlc_port": 8080}

    异常:
        RuntimeError: 端口耗尽时抛出
    """
    lock = FileLock(LOCK_FILE, timeout=30)
    with lock:
        # 合并系统已占用端口 + 进程内已预留端口 + 远程端口，防止冲突
        with _reserved_lock:
            used = _get_used_ports() | set(_reserved_ports.keys())
        if extra_used_ports:
            used |= extra_used_ports
        allocated: Set[int] = set()  # 本次已分配的端口（避免同组内冲突）
        containers: List[Dict[str, object]] = []

        for i in range(num_vms):
            ports: Dict[str, object] = {}
            for port_type, start in PORT_START.items():
                port = start
                while port < _PORT_MAX:
                    if port not in used and port not in allocated:
                        ports[f"{port_type}_port"] = port
                        allocated.add(port)
                        break
                    port += 1
                else:
                    raise RuntimeError(
                        f"端口耗尽: 类型={port_type}, 起始={start}, "
                        f"组={group_id}, VM={i+1}"
                    )
            ports["name"] = f"osworld-g{group_id}-vm{i+1}"
            containers.append(ports)

        # 将本次分配的端口加入全局预留集合
        with _reserved_lock:
            for p in allocated:
                _reserved_ports[p] = group_id

        logger.info(
            "组 %d 端口分配完成: %s",
            group_id,
            ", ".join(
                f"{c['name']}(s:{c['server_port']} v:{c['vnc_port']})"
                for c in containers
            ),
        )
        return containers


# ============================================================
# 容器保护检查
# ============================================================

def is_protected_container(name: str) -> bool:
    """
    检查容器名称是否属于保护列表（WebMall / OnlyOffice 等）。
    清理容器时应调用此函数，若返回 True 则跳过删除。

    输入:
        name: 容器名称

    输出:
        True 表示受保护，不可删除
    """
    return any(name.startswith(prefix) for prefix in PROTECTED_CONTAINER_PREFIXES)


def release_ports_for_group(group_id: int) -> None:
    """
    释放指定组的所有预留端口（容器清理时调用）。

    输入:
        group_id: 组编号
    """
    with _reserved_lock:
        to_remove = [p for p, gid in _reserved_ports.items() if gid == group_id]
        for p in to_remove:
            del _reserved_ports[p]
        if to_remove:
            logger.info("组 %d 释放 %d 个预留端口", group_id, len(to_remove))


def get_group_container_pattern(group_id: int) -> str:
    """
    获取某个组的容器名称匹配前缀（用于清理时精确匹配）。

    输入:
        group_id: 组编号

    输出:
        名称前缀字符串，如 "osworld-g0-vm"
    """
    return f"osworld-g{group_id}-vm"


# ============================================================
# 内存预算管理器
# ============================================================

class MemoryGuard:
    """
    内存预算管理器（线程安全）。

    在启动新容器组之前检查宿主机剩余内存是否足够，
    不足时阻塞等待其他 Worker 释放资源后再继续。
    使用 threading.Condition 实现等待/通知机制。

    用法:
        guard = MemoryGuard(memory_limit_gb=48.0, vm_memory_str="1G")
        if guard.acquire(num_vms=5):
            try:
                # 启动容器并执行任务
                ...
            finally:
                guard.release(num_vms=5)
    """

    def __init__(self, memory_limit_gb: float, vm_memory_str: str):
        """
        初始化内存管理器。

        输入:
            memory_limit_gb: 容器区可用总内存上限（GiB）
            vm_memory_str: 单个 VM 内存配置字符串，如 "1G"、"2G"、"512M"
        """
        self._per_vm_cost = self._parse_memory(vm_memory_str) + 0.2  # RSS 额外开销
        self._memory_limit = memory_limit_gb
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._active_vms = 0

    @staticmethod
    def _parse_memory(s: str) -> float:
        """
        解析内存字符串为 GiB 浮点数。

        输入:
            s: 内存字符串，如 "1G"、"2G"、"512M"

        输出:
            GiB 值（浮点数）
        """
        s = s.strip().upper()
        if s.endswith("G"):
            return float(s[:-1])
        if s.endswith("M"):
            return float(s[:-1]) / 1024.0
        return float(s)

    @property
    def per_vm_cost_gb(self) -> float:
        """单个 VM 的预估内存开销（GiB）"""
        return self._per_vm_cost

    @property
    def active_vms(self) -> int:
        """当前活跃的 VM 总数"""
        with self._lock:
            return self._active_vms

    def acquire(self, num_vms: int, timeout: float = 600) -> bool:
        """
        申请 num_vms 个 VM 的内存额度。如果当前可用额度不足，
        阻塞等待直到其他 Worker 调用 release() 释放内存。

        输入:
            num_vms: 需要的 VM 数量
            timeout: 最大等待秒数（默认 600 秒）

        输出:
            True 表示申请成功，False 表示超时
        """
        needed = num_vms * self._per_vm_cost
        with self._condition:
            while (self._active_vms * self._per_vm_cost + needed) > self._memory_limit:
                logger.info(
                    "内存不足: 需要 %.1f GiB (当前活跃 %d VM, %.1f GiB), "
                    "等待释放... (上限 %.1f GiB)",
                    needed, self._active_vms,
                    self._active_vms * self._per_vm_cost,
                    self._memory_limit,
                )
                if not self._condition.wait(timeout=timeout):
                    logger.error("内存等待超时 (%ds)", timeout)
                    return False
            self._active_vms += num_vms
            logger.info(
                "内存申请成功: +%d VM (当前活跃 %d VM, 预估占用 %.1f / %.1f GiB)",
                num_vms, self._active_vms,
                self._active_vms * self._per_vm_cost,
                self._memory_limit,
            )
            return True

    def release(self, num_vms: int) -> None:
        """
        释放 num_vms 个 VM 的内存额度，并唤醒所有等待线程。

        输入:
            num_vms: 释放的 VM 数量
        """
        with self._condition:
            self._active_vms = max(0, self._active_vms - num_vms)
            logger.info(
                "内存释放: -%d VM (当前活跃 %d VM, 预估占用 %.1f / %.1f GiB)",
                num_vms, self._active_vms,
                self._active_vms * self._per_vm_cost,
                self._memory_limit,
            )
            self._condition.notify_all()

    def get_real_available_memory_gb(self) -> float:
        """
        读取 /proc/meminfo (通过 psutil) 获取实际可用内存。
        可作为双重保险在 acquire 之外额外检查。

        输出:
            可用内存（GiB）
        """
        try:
            mem = psutil.virtual_memory()
            return mem.available / (1024 ** 3)
        except Exception:
            return 0.0


# ============================================================
# 辅助函数
# ============================================================

def scan_remote_docker_ports(
    ssh_password: str,
    ssh_opts: List[str],
    ssh_host: str,
    conda_activate: str = "",
) -> Set[int]:
    """
    通过 SSH 扫描远程 Docker 宿主机上所有容器占用的宿主机端口。

    解析 `docker ps --format '{{.Ports}}'` 输出，提取宿主机端口映射。
    该函数用于跨机器场景下，将远程已占用端口传入 allocate_ports_for_group()
    的 extra_used_ports 参数，确保分配的端口不会与远程已有容器冲突。

    输入:
        ssh_password: SSH 密码
        ssh_opts: SSH 选项列表（如 ["-o", "StrictHostKeyChecking=no", ...]）
        ssh_host: SSH 主机地址（如 "user@10.1.110.114"）
        conda_activate: conda 激活命令前缀（可为空）

    输出:
        远程已占用的宿主机端口集合
    """
    remote_ports: Set[int] = set()
    try:
        prefix = f"{conda_activate} && " if conda_activate else ""
        cmd = (
            f"{prefix}echo '{ssh_password}' | sudo -S "
            "docker ps --format '{{.Ports}}'"
        )
        env = dict(os.environ)
        env["SSHPASS"] = ssh_password
        result = subprocess.run(
            ["sshpass", "-e", "ssh"] + ssh_opts + [ssh_host, cmd],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode != 0:
            logger.warning("远程 Docker 端口扫描失败 (rc=%d): %s",
                           result.returncode, result.stderr[:200])
            return remote_ports

        # 解析端口映射，格式: "0.0.0.0:8006->8006/tcp, :::5000->5000/tcp, ..."
        _PORT_RE = re.compile(r"(?:\d+\.\d+\.\d+\.\d+|:::?):(\d+)->")
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            for match in _PORT_RE.finditer(line):
                remote_ports.add(int(match.group(1)))

        if remote_ports:
            logger.info("远程 Docker 已占用端口 (%d 个): %s",
                        len(remote_ports),
                        ", ".join(str(p) for p in sorted(remote_ports)))
    except FileNotFoundError:
        logger.warning("sshpass 未安装，跳过远程端口扫描")
    except subprocess.TimeoutExpired:
        logger.warning("远程端口扫描 SSH 超时")
    except Exception as exc:
        logger.warning("远程端口扫描异常: %s", exc)

    return remote_ports


def build_container_set_config(
    group_id: int,
    num_vms: int,
    vm_memory: str = "1G",
    vm_cpu_cores: str = "1",
    vm_ip: str = "10.1.110.114",
    shared_base_dir: str = "/home/yuzedong/shared",
    docker_image: str = "happysixd/osworld-docker-sshfs",
    qcow2_path: str = "",
    extra_used_ports: Optional[Set[int]] = None,
) -> ContainerSetConfig:
    """
    构建一个完整的 ContainerSetConfig（含动态端口分配）。

    输入:
        group_id: 组编号
        num_vms: VM 数量
        vm_memory: QEMU 内存（如 "1G"）
        vm_cpu_cores: QEMU CPU 核数（如 "1"）
        vm_ip: Docker 宿主机 IP
        shared_base_dir: 共享目录根路径
        docker_image: Docker 镜像名
        qcow2_path: VM 磁盘镜像路径
        extra_used_ports: 额外的已占用端口集合（如远程端口）

    输出:
        ContainerSetConfig 实例（containers 已填充动态分配的端口）
    """
    containers = allocate_ports_for_group(num_vms, group_id, extra_used_ports)
    shared_host_dir = f"{shared_base_dir}/group_{group_id}"

    return ContainerSetConfig(
        group_id=group_id,
        num_vms=num_vms,
        vm_memory=vm_memory,
        vm_cpu_cores=vm_cpu_cores,
        containers=containers,
        shared_host_dir=shared_host_dir,
        vm_ip=vm_ip,
        docker_image=docker_image,
        qcow2_path=qcow2_path,
    )
