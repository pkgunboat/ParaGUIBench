"""最小 OSWorld agent-server controller 契约测试。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paraguibench.integrations.osworld import OSWorldController


class _FakeResponse:
    """提供 controller 测试所需的最小 requests response。"""

    def __init__(
        self,
        *,
        content: bytes = b"",
        payload: dict[str, Any] | None = None,
    ) -> None:
        """构造合成 HTTP 响应。

        输入参数：
            content：截图等二进制响应正文。
            payload：execute 等接口返回的 JSON object。
        输出返回值：
            无；保存给测试期间的 controller 调用。
        """

        self.content = content
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        """模拟成功响应的状态检查。

        输入参数：
            无。
        输出返回值：
            无；本 fake 只表示成功，因此不抛异常。
        """

    def json(self) -> dict[str, Any]:
        """返回构造时注入的 JSON object。

        输入参数：
            无。
        输出返回值：
            合成响应字典。
        """

        return dict(self._payload)


class _FakeSession:
    """记录 controller 请求且不访问网络的最小 session。"""

    def __init__(self) -> None:
        """初始化请求记录列表。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.uploaded_content: bytes | None = None

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        """记录 GET 并返回合成 PNG 字节。

        输入参数：
            url：controller 构造的 loopback endpoint。
            kwargs：超时等请求选项。
        输出返回值：
            包含合成截图字节的响应。
        """

        self.requests.append(("GET", url, kwargs))
        return _FakeResponse(content=b"synthetic-png")

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        """记录 POST 并返回合成 command result。

        输入参数：
            url：controller 构造的 loopback endpoint。
            kwargs：JSON body 与超时等请求选项。
        输出返回值：
            包含成功 command result 的响应。
        """

        self.requests.append(("POST", url, kwargs))
        if url.endswith("/setup/upload"):
            self.uploaded_content = kwargs["files"]["file_data"][1].read()
            return _FakeResponse()
        if url.endswith("/desktop_path"):
            return _FakeResponse(payload={"desktop_path": "/home/oai/Desktop"})
        if url.endswith("/setup/open_file"):
            return _FakeResponse()
        return _FakeResponse(
            payload={
                "status": "success",
                "output": "ready\n",
                "error": "",
                "returncode": 0,
            }
        )


def test_controller_uses_loopback_and_shell_false_for_guest_commands() -> None:
    """验证 controller 只连本机映射端口且命令永远使用 argv。

    输入参数：
        无；使用 fake session 捕获 screenshot 与 execute 请求。
    输出返回值：
        无；shell 必须为 false，命令不得拼成 shell 字符串。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    assert controller.get_screenshot() == b"synthetic-png"
    result = controller.execute(["python", "-c", "print('ready')"])

    assert result.returncode == 0
    assert result.stdout == "ready\n"
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/execute"
    assert kwargs["json"] == {
        "command": ["python", "-c", "print('ready')"],
        "shell": False,
    }


def test_controller_uploads_verified_host_file_to_safe_guest_path(
    tmp_path: Path,
) -> None:
    """验证任务资产通过 upload endpoint 写入受控 guest 绝对路径。

    输入参数：
        tmp_path：pytest 提供的本地临时资产目录。
    输出返回值：
        无；上传使用 multipart，guest home 从 controller 查询而非硬编码。
    """

    local_asset = tmp_path / "paper.pdf"
    local_asset.write_bytes(b"verified-pdf")
    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    desktop_path = controller.get_desktop_path()
    guest_path = str(Path(desktop_path).parent / "shared" / "paper.pdf")
    controller.upload_file(local_asset, guest_path)

    assert desktop_path == "/home/oai/Desktop"
    assert session.uploaded_content == b"verified-pdf"
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/setup/upload"
    assert kwargs["data"] == {"file_path": "/home/oai/shared/paper.pdf"}


def test_controller_waits_for_screenshot_and_opens_safe_guest_path() -> None:
    """验证 readiness 使用截图 endpoint，打开目录使用结构化 JSON。

    输入参数：
        无；fake session 的截图请求立即成功。
    输出返回值：
        无；open_file 只接收经过验证的 guest 绝对路径。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    controller.wait_until_ready(timeout=1.0)
    controller.open_path("/home/oai/shared")

    assert session.requests[0][1].endswith("/screenshot")
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/setup/open_file"
    assert kwargs["json"] == {"path": "/home/oai/shared"}
