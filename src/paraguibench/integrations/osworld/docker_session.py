"""以本机 Docker/KVM 启动一个可追踪所有权的 OSWorld session。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Any

_CONTAINER_NAME_PATTERN = re.compile(r"^paraguibench-[a-z0-9][a-z0-9_.-]{0,100}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
_RAM_SIZE_PATTERN = re.compile(r"^[1-9][0-9]*[GM]$")


class OSWorldDockerSessionError(RuntimeError):
    """表示 Docker session 配置、启动或清理失败。"""


@dataclass(frozen=True)
class OSWorldDockerConfig:
    """描述一个单 VM、loopback-only 的 OSWorld Docker session。"""

    container_name: str
    image: str
    qcow2_path: Path
    server_port: int
    vnc_port: int
    chromium_port: int
    ram_size: str = "8G"
    cpu_cores: int = 4

    def __post_init__(self) -> None:
        """在任何 Docker 命令执行前验证配置。

        输入参数：
            无；读取数据类字段。
        输出返回值：
            无；合法配置正常返回。
        异常：
            OSWorldDockerSessionError：名称、digest、磁盘、端口或资源不安全。
        """

        if not _CONTAINER_NAME_PATTERN.fullmatch(self.container_name):
            raise OSWorldDockerSessionError(
                "container_name 必须使用受限的 paraguibench- 前缀"
            )
        _validate_image_reference(self.image)
        if (
            not self.qcow2_path.is_absolute()
            or not self.qcow2_path.is_file()
            or self.qcow2_path.is_symlink()
        ):
            raise OSWorldDockerSessionError(
                "qcow2_path 必须是绝对、普通且非符号链接文件"
            )
        ports = (self.server_port, self.vnc_port, self.chromium_port)
        for port in ports:
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not 1024 <= port <= 65535
            ):
                raise OSWorldDockerSessionError("Docker 映射端口范围无效")
        if len(set(ports)) != len(ports):
            raise OSWorldDockerSessionError(
                "server、VNC 与 Chromium 主机端口必须互不相同"
            )
        if not _RAM_SIZE_PATTERN.fullmatch(self.ram_size):
            raise OSWorldDockerSessionError("RAM_SIZE 格式无效")
        if (
            not isinstance(self.cpu_cores, int)
            or isinstance(self.cpu_cores, bool)
            or not 1 <= self.cpu_cores <= 64
        ):
            raise OSWorldDockerSessionError("CPU_CORES 必须在 1 到 64 之间")


class OSWorldDockerSession:
    """只创建新容器，并只按返回的容器 ID 清理自身资源。"""

    def __init__(
        self,
        config: OSWorldDockerConfig,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        """构造尚未启动的 Docker session。

        输入参数：
            config：已验证的单 VM Docker 配置。
            runner：可选 subprocess-compatible 调用器；测试可注入 fake。
        输出返回值：
            无；不会在构造阶段调用 Docker。
        """

        self.config = config
        self._runner = runner if runner is not None else subprocess.run
        self._container_id: str | None = None

    @property
    def container_id(self) -> str | None:
        """返回本 session 当前拥有的容器 ID。

        输入参数：
            无。
        输出返回值：
            启动后返回 Docker ID，未启动或清理后返回 ``None``。
        """

        return self._container_id

    def start(self) -> str:
        """启动一个固定镜像 digest、只读 qcow2 的新容器。

        输入参数：
            无；全部参数来自构造时的安全配置。
        输出返回值：
            Docker 返回并验证后的容器 ID。
        异常：
            OSWorldDockerSessionError：重复启动、Docker 失败或 ID 无效。
        """

        if self._container_id is not None:
            raise OSWorldDockerSessionError("Docker session 已经启动")
        command = _docker_run_command(self.config)
        result = self._runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise OSWorldDockerSessionError("Docker 无法启动 OSWorld session")
        container_id = str(result.stdout or "").strip()
        if not _CONTAINER_ID_PATTERN.fullmatch(container_id):
            raise OSWorldDockerSessionError("Docker 返回的容器 ID 无效")
        self._container_id = container_id
        return container_id

    def close(self) -> None:
        """按精确容器 ID 删除本 session 及其匿名卷。

        输入参数：
            无。
        输出返回值：
            无；未启动或已清理时幂等返回。
        异常：
            OSWorldDockerSessionError：本 session 拥有的容器无法清理。
        """

        container_id = self._container_id
        if container_id is None:
            return
        result = self._runner(
            ["docker", "rm", "-fv", container_id],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise OSWorldDockerSessionError("无法清理本次 OSWorld 容器")
        self._container_id = None

    def __enter__(self) -> OSWorldDockerSession:
        """进入上下文并启动容器。

        输入参数：
            无。
        输出返回值：
            已启动的当前 session。
        """

        self.start()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> None:
        """离开上下文时清理本 session 拥有的容器。

        输入参数：
            exception_type：with body 的异常类型或 ``None``。
            exception：with body 的异常对象或 ``None``。
            traceback：异常 traceback 或 ``None``。
        输出返回值：
            无；不吞掉 with body 异常。
        """

        self.close()


def _validate_image_reference(image: str) -> None:
    """验证 Docker image 明确固定到 sha256 digest。

    输入参数：
        image：``repository@sha256:<64 hex>`` 形式的镜像引用。
    输出返回值：
        无；合法引用正常返回。
    异常：
        OSWorldDockerSessionError：引用可变、含空白或 digest 无效。
    """

    if not isinstance(image, str) or image.count("@sha256:") != 1:
        raise OSWorldDockerSessionError("Docker image 必须固定 sha256 digest")
    repository, digest = image.split("@sha256:", 1)
    if (
        not repository
        or any(character.isspace() for character in repository)
        or not _DIGEST_PATTERN.fullmatch(digest)
    ):
        raise OSWorldDockerSessionError("Docker image digest 引用格式无效")


def _docker_run_command(config: OSWorldDockerConfig) -> list[str]:
    """构造不经 shell 的单 VM Docker argv。

    输入参数：
        config：已验证的 OSWorld Docker 配置。
    输出返回值：
        可直接交给 ``subprocess.run`` 的参数列表；端口仅绑定 loopback，
        qcow2 只读，镜像禁止隐式拉取。
    """

    qcow2_mount = f"{config.qcow2_path.resolve()}:/System.qcow2:ro"
    return [
        "docker",
        "run",
        "-d",
        "--pull=never",
        "--name",
        config.container_name,
        "--label",
        "paraguibench.owned=true",
        "-p",
        f"127.0.0.1:{config.server_port}:5000",
        "-p",
        f"127.0.0.1:{config.vnc_port}:8006",
        "-p",
        f"127.0.0.1:{config.chromium_port}:9222",
        "-e",
        f"RAM_SIZE={config.ram_size}",
        "-e",
        f"CPU_CORES={config.cpu_cores}",
        "--shm-size=2g",
        "--cap-add=NET_ADMIN",
        "--device=/dev/kvm",
        "-v",
        qcow2_mount,
        config.image,
    ]
