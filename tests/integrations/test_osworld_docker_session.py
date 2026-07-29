"""OSWorld Docker/KVM session 的所有权与命令边界测试。"""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from paraguibench.integrations.osworld import (
    OSWorldDockerConfig,
    OSWorldDockerSession,
)


class _DockerRunner:
    """记录 Docker argv，并返回固定容器 ID。"""

    def __init__(self) -> None:
        """初始化命令记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """记录命令并模拟 ``docker run``/``docker rm`` 成功。

        输入参数：
            command：待执行的 Docker argv。
            kwargs：capture、text、timeout 等 subprocess 选项。
        输出返回值：
            ``docker run`` 返回合成容器 ID，其余命令返回空成功结果。
        """

        self.commands.append(list(command))
        stdout = "a" * 64 + "\n" if command[1] == "run" else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def test_session_starts_new_loopback_container_and_removes_only_owned_id(
    tmp_path: Path,
) -> None:
    """验证 session 不扫描/删除旧环境，只清理自己创建的容器 ID。

    输入参数：
        tmp_path：pytest 提供的合成 qcow2 文件目录。
    输出返回值：
        无；Docker 命令必须固定镜像 digest、loopback 端口和只读磁盘。
    """

    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(b"synthetic-qcow2")
    image = "example/osworld@sha256:" + "b" * 64
    runner = _DockerRunner()
    config = OSWorldDockerConfig(
        container_name="paraguibench-smoke-attempt-001",
        image=image,
        qcow2_path=qcow2_path,
        server_port=55001,
        vnc_port=58001,
        ram_size="8G",
        cpu_cores=4,
    )
    session = OSWorldDockerSession(config, runner=runner)

    container_id = session.start()
    session.close()

    assert container_id == "a" * 64
    run_command = runner.commands[0]
    assert run_command[:3] == ["docker", "run", "-d"]
    assert "127.0.0.1:55001:5000" in run_command
    assert "127.0.0.1:58001:8006" in run_command
    assert f"{qcow2_path.resolve()}:/System.qcow2:ro" in run_command
    assert run_command[-1] == image
    assert runner.commands[1] == ["docker", "rm", "-fv", "a" * 64]
    assert all(command[1] != "ps" for command in runner.commands)
