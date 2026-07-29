"""连接本机端口映射后的 OSWorld guest agent server。

该模块基于 OSWorld PythonController 的 HTTP 协议重新实现最小接口；不迁移
旧 controller 的隐式 shell、完整环境变量日志或远程 SSH 路径。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import time
from typing import Any
from urllib.parse import urlsplit


class OSWorldControllerError(RuntimeError):
    """表示 controller 配置、传输或 guest 返回契约异常。"""


@dataclass(frozen=True)
class CommandResult:
    """保存一次 guest argv 命令的结构化结果。"""

    returncode: int
    stdout: str
    stderr: str


class OSWorldController:
    """通过仅绑定 loopback 的 HTTP endpoint 控制单个 OSWorld guest。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        session: Any | None = None,
    ) -> None:
        """构造 controller 并限制 endpoint 为本机 HTTP 端口。

        输入参数：
            base_url：形如 ``http://127.0.0.1:<port>`` 的 agent-server 地址。
            timeout：每个 HTTP 请求的超时秒数。
            session：可选 requests-compatible session；测试可注入 fake。
        输出返回值：
            无；实例保存受限 endpoint 与会话。
        异常：
            OSWorldControllerError：URL 非 loopback、含凭据或附加路径。
        """

        _validate_loopback_base_url(base_url)
        if timeout <= 0:
            raise OSWorldControllerError("controller timeout 必须大于零")
        if session is None:
            import requests

            session = requests.Session()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session

    def get_screenshot(self) -> bytes:
        """读取带光标的当前 guest 截图。

        输入参数：
            无。
        输出返回值：
            agent server 返回的 PNG/JPEG 原始字节。
        异常：
            requests HTTP 异常由底层抛出；空响应转为
            ``OSWorldControllerError``。
        """

        response = self._session.get(
            f"{self._base_url}/screenshot",
            timeout=self._timeout,
        )
        response.raise_for_status()
        content = bytes(response.content)
        if not content:
            raise OSWorldControllerError("guest screenshot 响应为空")
        return content

    def execute(self, command: Sequence[str]) -> CommandResult:
        """在 guest 内以 ``shell=False`` 执行一个 argv 命令。

        输入参数：
            command：非空字符串参数序列；不会拼接成 shell 文本。
        输出返回值：
            guest 的退出码、标准输出和标准错误。
        异常：
            OSWorldControllerError：命令字段或 guest JSON 响应无效。
        """

        if isinstance(command, (str, bytes)) or not command:
            raise OSWorldControllerError("guest command 必须是非空 argv")
        argv = list(command)
        if not all(isinstance(item, str) and "\x00" not in item for item in argv):
            raise OSWorldControllerError("guest argv 只能包含无 NUL 的字符串")
        response = self._session.post(
            f"{self._base_url}/execute",
            json={"command": argv, "shell": False},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise OSWorldControllerError("guest execute 返回失败状态")
        returncode = payload.get("returncode")
        stdout = payload.get("output", "")
        stderr = payload.get("error", "")
        if (
            not isinstance(returncode, int)
            or isinstance(returncode, bool)
            or not isinstance(stdout, str)
            or not isinstance(stderr, str)
        ):
            raise OSWorldControllerError("guest execute 返回字段类型异常")
        return CommandResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def get_desktop_path(self) -> str:
        """查询 guest 当前用户的 Desktop 绝对路径。

        输入参数：
            无。
        输出返回值：
            经过 POSIX 绝对路径校验的 Desktop 路径；runtime 可由其父目录推导
            当前 guest home，避免硬编码用户名。
        异常：
            OSWorldControllerError：响应缺失或路径含父目录跳转。
        """

        response = self._session.post(
            f"{self._base_url}/desktop_path",
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        desktop_path = (
            payload.get("desktop_path") if isinstance(payload, dict) else None
        )
        if not isinstance(desktop_path, str):
            raise OSWorldControllerError("guest 未返回 desktop_path")
        parsed = PurePosixPath(desktop_path)
        if not parsed.is_absolute() or ".." in parsed.parts:
            raise OSWorldControllerError("guest desktop_path 不是安全绝对路径")
        return desktop_path

    def upload_file(self, local_path: Path, guest_path: str) -> None:
        """把已在 host 校验的普通文件上传到安全 guest 绝对路径。

        输入参数：
            local_path：host 上已验证大小与 SHA-256 的普通文件。
            guest_path：guest 内无 ``..`` 的 POSIX 绝对目标路径。
        输出返回值：
            无；成功时 agent server 已完整接收 multipart 文件。
        异常：
            OSWorldControllerError：本地文件、guest 路径或 mkdir 结果无效。
        """

        if not local_path.is_file() or local_path.is_symlink():
            raise OSWorldControllerError("上传来源必须是普通且非符号链接文件")
        parsed_guest_path = PurePosixPath(guest_path)
        if (
            not parsed_guest_path.is_absolute()
            or ".." in parsed_guest_path.parts
            or guest_path.endswith("/")
        ):
            raise OSWorldControllerError("guest upload 目标必须是安全绝对文件路径")
        mkdir_result = self.execute(
            ["mkdir", "-p", str(parsed_guest_path.parent)]
        )
        if mkdir_result.returncode != 0:
            raise OSWorldControllerError("guest 无法创建资产目标目录")
        with local_path.open("rb") as file:
            response = self._session.post(
                f"{self._base_url}/setup/upload",
                data={"file_path": guest_path},
                files={
                    "file_data": (
                        local_path.name,
                        file,
                        "application/octet-stream",
                    )
                },
                timeout=self._timeout,
            )
            response.raise_for_status()

    def wait_until_ready(
        self,
        *,
        timeout: float,
        interval: float = 2.0,
    ) -> None:
        """轮询截图 endpoint，直到 guest 图形环境可用或超时。

        输入参数：
            timeout：总等待上限秒数。
            interval：失败请求之间的等待秒数。
        输出返回值：
            无；首次取得非空截图即返回。
        异常：
            OSWorldControllerError：参数无效或期限内始终未就绪。
        """

        if timeout <= 0 or interval <= 0:
            raise OSWorldControllerError("readiness timeout/interval 必须大于零")
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.get_screenshot()
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise OSWorldControllerError(
                        "OSWorld guest 未在期限内就绪"
                    ) from None
                time.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    def open_path(self, guest_path: str) -> None:
        """请求 guest 使用默认桌面应用打开安全绝对路径。

        输入参数：
            guest_path：要向 Agent 展示的文件或目录 POSIX 绝对路径。
        输出返回值：
            无；agent server 接受请求后返回。
        异常：
            OSWorldControllerError：路径不是安全绝对路径。
        """

        parsed = PurePosixPath(guest_path)
        if not parsed.is_absolute() or ".." in parsed.parts:
            raise OSWorldControllerError("open_path 需要安全 guest 绝对路径")
        response = self._session.post(
            f"{self._base_url}/setup/open_file",
            json={"path": guest_path},
            timeout=self._timeout,
        )
        response.raise_for_status()


def _validate_loopback_base_url(base_url: str) -> None:
    """验证 agent-server URL 只暴露在 loopback。

    输入参数：
        base_url：待验证的 controller endpoint。
    输出返回值：
        无；安全 URL 正常返回。
    异常：
        OSWorldControllerError：协议、主机、端口、凭据或路径不符合要求。
    """

    parts = urlsplit(base_url)
    is_loopback = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    has_userinfo = parts.username is not None or parts.password is not None
    has_extra = (
        parts.path not in {"", "/"} or bool(parts.query) or bool(parts.fragment)
    )
    try:
        has_port = parts.port is not None
    except ValueError as error:
        raise OSWorldControllerError("controller endpoint 端口无效") from error
    if (
        parts.scheme != "http"
        or not is_loopback
        or not has_port
        or has_userinfo
        or has_extra
    ):
        raise OSWorldControllerError(
            "controller endpoint 必须是无凭据的 loopback HTTP origin"
        )
