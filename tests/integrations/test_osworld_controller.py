"""最小 OSWorld agent-server controller 契约测试。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from PIL import Image
import pytest

import paraguibench.integrations.osworld.controller as controller_module
import paraguibench.integrations.osworld.operation_artifacts as operation_module
from paraguibench.integrations.osworld import OSWorldController
from paraguibench.integrations.osworld.controller import (
    OSWorldGuestPathMissingError,
)


class _FakeResponse:
    """提供 controller 测试所需的最小 requests response。"""

    def __init__(
        self,
        *,
        content: bytes | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """构造合成 HTTP 响应。

        输入参数：
            content：截图等二进制响应正文；为 ``None`` 时
                从 payload 生成紧凑 UTF-8 JSON envelope。
            payload：execute 等接口返回的 JSON object。
        输出返回值：
            无；保存给测试期间的 controller 调用。
        """

        if content is None:
            content = (
                json.dumps(payload, separators=(",", ":")).encode("utf-8")
                if payload is not None
                else b""
            )
        self.content = content
        self._payload = payload or {}
        self.headers = headers or {}
        self.closed = False

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

    def iter_content(self, chunk_size: int) -> object:
        """按 controller 请求的正整数 chunk 上限流式返回正文。

        输入参数：
            chunk_size：单次切片上限。
        输出返回值：
            可迭代的 bytes 分块。
        """

        assert isinstance(chunk_size, int) and chunk_size > 0
        return (
            self.content[index : index + chunk_size]
            for index in range(0, len(self.content), chunk_size)
        )

    def close(self) -> None:
        """记录 controller 已关闭流式响应。

        输入参数：无。
        输出返回值：无。
        """

        self.closed = True


class _FakeSession:
    """记录 controller 请求且不访问网络的最小 session。"""

    def __init__(
        self,
        *,
        file_content: bytes = b'{"profile":{"name":"Thomas"}}',
    ) -> None:
        """初始化请求记录列表。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.uploaded_content: bytes | None = None
        self.file_content = file_content

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
        if url.endswith("/file"):
            return _FakeResponse(content=self.file_content)
        if url.endswith("/setup/launch"):
            return _FakeResponse(content=b"launched")
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


class _LocalExecuteSession(_FakeSession):
    """在本地以真实 ``shell=False`` 执行 guest argv。"""

    def __init__(self) -> None:
        """初始化请求记录与最后一次 guest stdout 投影。

        输入参数：
            无。
        输出返回值：
            无；``last_stdout`` 在真实执行前为 ``None``。
        """

        super().__init__()
        self.last_stdout: str | None = None

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        """执行 controller 提交的 argv 并包装 agent-server envelope。

        输入参数：
            url：controller 构造的 execute endpoint。
            kwargs：包含 ``command`` 与 ``shell=False`` 的请求。
        输出返回值：
            包含真实子进程 stdout/stderr/returncode 的合成响应。
        """

        self.requests.append(("POST", url, kwargs))
        request_json = kwargs["json"]
        assert request_json["shell"] is False
        completed = subprocess.run(
            request_json["command"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        self.last_stdout = completed.stdout
        return _FakeResponse(
            payload={
                "status": "success",
                "output": completed.stdout,
                "error": completed.stderr,
                "returncode": completed.returncode,
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
    result = controller.execute(["python3", "-c", "print('ready')"])

    assert result.returncode == 0
    assert result.stdout == "ready\n"
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/execute"
    assert kwargs["json"] == {
        "command": ["python3", "-c", "print('ready')"],
        "shell": False,
    }


def test_controller_executes_argv_with_call_scoped_timeout() -> None:
    """验证 finalizer 可为固定 argv 指定单次有界超时。

    输入参数：
        无；使用 fake session 记录 execute 请求。
    输出返回值：
        无；命令保持 ``shell=False``，HTTP timeout 精确使用调用方
        提供的有限秒数，并返回严格 ``CommandResult``。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        timeout=30.0,
        session=session,
    )

    result = controller.execute_with_timeout(
        ["wmctrl", "-Fa", "Target Window"],
        timeout_seconds=7.5,
    )

    assert (result.returncode, result.stdout, result.stderr) == (
        0,
        "ready\n",
        "",
    )
    method, url, kwargs = session.requests[-1]
    assert (method, url) == ("POST", "http://127.0.0.1:55001/execute")
    assert kwargs == {
        "json": {
            "command": ["wmctrl", "-Fa", "Target Window"],
            "shell": False,
        },
        "timeout": 7.5,
    }


@pytest.mark.parametrize(
    "timeout_seconds",
    (True, 0, -1, float("nan"), float("inf"), 300.001, "7.5"),
)
def test_controller_rejects_invalid_call_scoped_timeout_before_io(
    timeout_seconds: object,
) -> None:
    """验证单次 execute 超时必须是 ``0 < value <= 300`` 的有限数。

    输入参数：
        timeout_seconds：布尔、非正数、非有限数、超限数或字符串。
    输出返回值：
        无；controller 在任何 HTTP I/O 前抛脱敏领域错误。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    with pytest.raises(Exception) as captured:
        controller.execute_with_timeout(
            ["true"],
            timeout_seconds=timeout_seconds,  # type: ignore[arg-type]
        )

    assert type(captured.value).__name__ == "OSWorldControllerError"
    assert session.requests == []


def test_controller_owned_requests_session_disables_environment_proxies() -> None:
    """验证生产 controller 自建 session 不继承宿主代理变量。

    输入参数：
        无；仅构造 loopback controller，不发起 HTTP 请求。
    输出返回值：
        无；requests ``trust_env`` 必须显式为 ``False``。
    """

    controller = OSWorldController("http://127.0.0.1:55001")
    try:
        assert controller._session.trust_env is False
    finally:
        controller._session.close()


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


def test_controller_reads_bounded_guest_file_and_launches_argv() -> None:
    """验证评价证据下载有大小上限，应用启动保持 shell=false argv。

    输入参数：
        无；读取固定 Chrome Preferences 路径并启动 Chrome。
    输出返回值：
        无；两个请求均只发送结构化字段，不拼接 shell 文本。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    content = controller.read_file(
        "/home/oai/.config/google-chrome/Default/Preferences",
        max_bytes=1024,
    )
    controller.launch(["google-chrome", "--remote-debugging-port=1337"])

    assert content == b'{"profile":{"name":"Thomas"}}'
    read_method, read_url, read_kwargs = session.requests[-2]
    assert read_method == "POST"
    assert read_url == "http://127.0.0.1:55001/file"
    assert read_kwargs["data"] == {
        "file_path": ("/home/oai/.config/google-chrome/Default/Preferences")
    }
    assert read_kwargs["stream"] is True
    launch_method, launch_url, launch_kwargs = session.requests[-1]
    assert launch_method == "POST"
    assert launch_url == "http://127.0.0.1:55001/setup/launch"
    assert launch_kwargs["json"] == {
        "command": ["google-chrome", "--remote-debugging-port=1337"],
        "shell": False,
    }


def test_controller_rejects_unsafe_file_paths_and_invalid_limits() -> None:
    """验证 controller 在请求前拒绝路径逃逸和无界 guest 文件读取。

    输入参数：
        无；使用相对路径、父目录跳转与超限配置。
    输出返回值：
        无；三个调用均抛配置错误且 fake session 未收到请求。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    for guest_path, max_bytes in (
        ("relative/Preferences", 1024),
        ("/home/oai/../root/secret", 1024),
        ("/home/oai/file", 0),
        ("/home/oai/file", 20 * 1024 * 1024),
    ):
        try:
            controller.read_file(guest_path, max_bytes=max_bytes)
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
        else:
            raise AssertionError("不安全 guest 文件读取应被拒绝")

    assert session.requests == []


def test_controller_stops_streaming_guest_file_at_protocol_limit() -> None:
    """验证 guest 返回过大文件时不会先将整个响应装入内存。

    输入参数：
        无；fake response 以多个 chunk 返回 32 bytes。
    输出返回值：
        无；8-byte 协议上限在流式累计阶段就触发类型安全错误。
    """

    session = _FakeSession(file_content=b"x" * 32)
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.read_file("/home/oai/file", max_bytes=8)
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "上限" in str(error)
    else:
        raise AssertionError("过大 guest 文件应在流式读取中被拒绝")

    _, _, kwargs = session.requests[-1]
    assert kwargs["stream"] is True


def test_controller_waits_for_guest_chrome_cdp_and_activates_window() -> None:
    """验证 Chrome setup 在后续 socat/新标签页之前具有 HTTP 就绪门禁。

    输入参数：
        无；fake guest 首次 CDP 探测即返回成功。
    输出返回值：
        无；controller 使用 shell=false 固定 Python probe，并通过
        agent-server 结构化 endpoint 激活 Google Chrome 窗口。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    controller.wait_for_chrome_cdp(
        port=1337,
        timeout=1.0,
        interval=0.01,
    )
    controller.activate_window("Google Chrome")

    execute_request = session.requests[-2]
    assert execute_request[1].endswith("/execute")
    probe_argv = execute_request[2]["json"]["command"]
    assert probe_argv[0] == "python3"
    assert probe_argv[-1] == "1337"
    assert "ProxyHandler({})" in probe_argv[2]
    assert execute_request[2]["json"]["shell"] is False
    activate_request = session.requests[-1]
    assert activate_request[1].endswith("/setup/activate_window")
    assert activate_request[2]["json"] == {
        "window_name": "Google Chrome",
        "strict": False,
        "by_class": False,
    }


def test_controller_waits_until_old_chrome_processes_are_absent() -> None:
    """验证 profile capture 在重启前等待旧 Chrome 进程完整退出。

    输入参数：
        无；fake guest 第一次报告仍在运行，第二次报告不存在。
    输出返回值：
        无；controller 仅以固定 ``pgrep -x chrome`` argv 轮询两次。
    """

    class _ChromeExitSession(_FakeSession):
        """为 Chrome 退出门禁提供 0→1 两次返回码。"""

        def __init__(self) -> None:
            """初始化基础请求记录和固定返回码队列。

            输入参数：无。
            输出返回值：无。
            """

            super().__init__()
            self._returncodes = [0, 1]

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            """为 pgrep 请求返回下一项状态，其余请求复用基础 fake。

            输入参数：
                url：controller 构造的 endpoint。
                kwargs：结构化 JSON 与 timeout。
            输出返回值：
                包含下一项 pgrep returncode 的合成响应。
            """

            if url.endswith("/execute"):
                self.requests.append(("POST", url, kwargs))
                return _FakeResponse(
                    payload={
                        "status": "success",
                        "output": "",
                        "error": "",
                        "returncode": self._returncodes.pop(0),
                    }
                )
            return super().post(url, **kwargs)

    session = _ChromeExitSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    controller.wait_for_chrome_exit(timeout=1.0, interval=0.001)

    assert len(session.requests) == 2
    assert all(
        request[2]["json"]
        == {
            "command": ["pgrep", "-x", "chrome"],
            "shell": False,
        }
        for request in session.requests
    )


def test_controller_lists_guest_directory_with_bounded_fixed_argv() -> None:
    """验证目录证据通过公开接口返回确定性成员名。

    输入参数：
        无；fake guest 返回一个明确的 v1 JSON 成功对象。
    输出返回值：
        无；controller 必须返回按 UTF-8 字节排序的不可变名称
        tuple，且 guest 命令使用隔离 Python 与 ``shell=False``。
    """

    class _DirectoryListingSession(_FakeSession):
        """返回受版本约束的目录列表 JSON。"""

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            """响应目录列表命令并保留完整请求记录。

            输入参数：
                url：controller 构造的 loopback endpoint。
                kwargs：包含固定 argv 的结构化请求参数。
            输出返回值：
                包含成功 execute envelope 与 v1 列表对象的响应。
            """

            self.requests.append(("POST", url, kwargs))
            return _FakeResponse(
                payload={
                    "status": "success",
                    "output": (
                        '{"schema_version":"paraguibench.osworld.'
                        'directory-listing.v1","status":"success",'
                        '"entries":["chapter-01.pdf","chapter-02.pdf"]}'
                    ),
                    "error": "",
                    "returncode": 0,
                }
            )

    session = _DirectoryListingSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    entries = controller.list_directory(
        "/guest/profile/Desktop/chapters",
        max_entries=32,
        max_name_bytes=255,
        max_response_bytes=4096,
    )

    assert entries == ("chapter-01.pdf", "chapter-02.pdf")
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/execute"
    assert kwargs["json"]["shell"] is False
    argv = kwargs["json"]["command"]
    assert argv[:3] == ["python3", "-I", "-c"]
    assert argv[-4:] == [
        "/guest/profile/Desktop/chapters",
        "32",
        "255",
        "4096",
    ]


def test_controller_rejects_unsafe_directory_paths_without_echoing_them() -> None:
    """验证目录枚举在传输前拒绝所有非安全绝对路径。

    输入参数：
        无；依次提供相对路径、父目录跳转、NUL、ASCII/Unicode
        控制字符与非规范分隔路径。
    输出返回值：
        无；每个输入均必须产生脱敏的 controller 错误，且
        fake session 不得收到任何请求。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )
    unsafe_paths: tuple[object, ...] = (
        "relative/private-members",
        "/guest/private-members/../outside",
        "/guest/private-members\x00/archive",
        "/guest/private-members\n/archive",
        "/guest/private-members\u202e/archive",
        "/guest/private-members\u2028/archive",
        "/guest//private-members",
        "/guest/./private-members",
        17,
    )

    for unsafe_path in unsafe_paths:
        try:
            controller.list_directory(
                unsafe_path,  # type: ignore[arg-type]
                max_entries=32,
                max_name_bytes=255,
                max_response_bytes=4096,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert str(unsafe_path) not in str(error)
            assert "private-members" not in str(error)
        else:
            raise AssertionError("非安全 guest 目录路径应被拒绝")

    assert session.requests == []


def test_controller_rejects_unbounded_directory_listing_limits() -> None:
    """验证目录枚举的三项资源上限都是严格有界整数。

    输入参数：
        无；提供零值、布尔值和超出各自安全闭区间的值。
    输出返回值：
        无；所有无界配置均在网络请求前产生脱敏错误。
    """

    invalid_limits = (
        {
            "max_entries": 0,
            "max_name_bytes": 255,
            "max_response_bytes": 4096,
        },
        {
            "max_entries": True,
            "max_name_bytes": 255,
            "max_response_bytes": 4096,
        },
        {
            "max_entries": 4097,
            "max_name_bytes": 255,
            "max_response_bytes": 4096,
        },
        {
            "max_entries": 32,
            "max_name_bytes": 0,
            "max_response_bytes": 4096,
        },
        {
            "max_entries": 32,
            "max_name_bytes": 256,
            "max_response_bytes": 4096,
        },
        {
            "max_entries": 32,
            "max_name_bytes": 255,
            "max_response_bytes": 127,
        },
        {
            "max_entries": 32,
            "max_name_bytes": 255,
            "max_response_bytes": 1024 * 1024 + 1,
        },
    )

    for limits in invalid_limits:
        session = _FakeSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.list_directory(
                "/guest/profile/Desktop/chapters",
                **limits,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "/guest/profile" not in str(error)
        else:
            raise AssertionError("无界 guest 目录枚举配置应被拒绝")
        assert session.requests == []


def test_controller_rejects_unsafe_directory_member_names_without_echo() -> None:
    """验证 guest 无法借成员名绕过路径、UTF-8 和资源边界。

    输入参数：
        无；fake guest 分别返回空名、特殊分量、分隔符、重复名、
        超长多字节名、控制字符与非法 Unicode surrogate。
    输出返回值：
        无；每个响应都必须 fail closed，且异常不得包含成员值。
    """

    invalid_entries = (
        [""],
        ["."],
        [".."],
        ["private/member"],
        ["private\x00member"],
        ["private\nmember"],
        ["private\u202emember"],
        ["private\u2028member"],
        ["\ud800"],
        ["é" * 33],
        ["duplicate", "duplicate"],
    )

    for entries in invalid_entries:
        output = json.dumps(
            {
                "schema_version": ("paraguibench.osworld.directory-listing.v1"),
                "status": "success",
                "entries": entries,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        class _InvalidMemberSession(_FakeSession):
            """向 controller 注入当前非法成员响应。"""

            def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                """记录请求并返回合成 execute envelope。

                输入参数：
                    url：controller 构造的 endpoint。
                    kwargs：结构化 execute 参数。
                输出返回值：
                    包含当前非法成员 JSON 的成功传输响应。
                """

                self.requests.append(("POST", url, kwargs))
                return _FakeResponse(
                    payload={
                        "status": "success",
                        "output": output,
                        "error": "",
                        "returncode": 0,
                    }
                )

        session = _InvalidMemberSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.list_directory(
                "/guest/profile/Desktop/chapters",
                max_entries=32,
                max_name_bytes=64,
                max_response_bytes=4096,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            for entry in entries:
                if entry and entry.encode("utf-8", "ignore"):
                    assert entry not in str(error)
        else:
            raise AssertionError("非安全 guest 目录成员名应被拒绝")


def test_controller_rejects_duplicate_directory_listing_json_keys() -> None:
    """验证重复 JSON 键不能以“后值覆盖”绕过 schema 绑定。

    输入参数：
        无；fake guest 先声明错误 schema，再用同名键覆盖为
        正确 v1 schema。
    输出返回值：
        无；公开接口必须拒绝整个响应，不能接受后一个值。
    """

    duplicate_key_output = (
        '{"schema_version":"untrusted-private-schema",'
        '"schema_version":"paraguibench.osworld.directory-listing.v1",'
        '"status":"success","entries":[]}'
    )

    class _DuplicateKeySession(_FakeSession):
        """返回含重复 schema 键的 execute 响应。"""

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            """记录请求并注入重复键 JSON。

            输入参数：
                url：controller 构造的 endpoint。
                kwargs：结构化 execute 参数。
            输出返回值：
                传输成功但证据 JSON 无效的响应。
            """

            self.requests.append(("POST", url, kwargs))
            return _FakeResponse(
                payload={
                    "status": "success",
                    "output": duplicate_key_output,
                    "error": "",
                    "returncode": 0,
                }
            )

    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=_DuplicateKeySession(),
    )
    try:
        controller.list_directory(
            "/guest/profile/Desktop/chapters",
            max_entries=32,
            max_name_bytes=255,
            max_response_bytes=4096,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "untrusted-private-schema" not in str(error)
    else:
        raise AssertionError("重复 directory listing JSON 键应被拒绝")


def test_controller_guest_program_rejects_symlink_directory_traversal(
    tmp_path: Path,
) -> None:
    """验证 guest 枚举程序不跟随目录符号链接越界。

    输入参数：
        tmp_path：pytest 提供的临时根目录；测试在其中创建目录
        符号链接与不应暴露的链接目标成员。
    输出返回值：
        无；通过公开 controller 执行的固定 guest 程序必须失败
        关闭，且异常不回显链接路径或目标成员。
    """

    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / "private-member.txt").write_text(
        "synthetic private content",
        encoding="utf-8",
    )
    symlink_directory = tmp_path / "listing-link"
    symlink_directory.symlink_to(outside_directory, target_is_directory=True)

    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=_LocalExecuteSession(),
    )
    try:
        controller.list_directory(
            str(symlink_directory),
            max_entries=32,
            max_name_bytes=255,
            max_response_bytes=4096,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert str(symlink_directory) not in str(error)
        assert "private-member.txt" not in str(error)
    else:
        raise AssertionError("guest 目录符号链接穿越应被拒绝")


def test_controller_guest_program_lists_real_directory_deterministically(
    tmp_path: Path,
) -> None:
    """验证固定 guest 程序可安全枚举真实非链接目录。

    输入参数：
        tmp_path：pytest 提供的临时根目录；测试写入 ASCII 与
        多字节 UTF-8 文件名。
    输出返回值：
        无；公开接口返回真实 guest 程序产生的 UTF-8 字节
        确定性顺序。
    """

    directory = tmp_path / "safe-directory"
    directory.mkdir()
    for name in ("zeta.txt", "alpha.txt", "éclair.txt"):
        (directory / name).write_bytes(b"synthetic")
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=_LocalExecuteSession(),
    )

    entries = controller.list_directory(
        str(directory),
        max_entries=32,
        max_name_bytes=255,
        max_response_bytes=4096,
    )

    assert entries == ("alpha.txt", "zeta.txt", "éclair.txt")


def test_controller_guest_program_bounds_total_listing_response_bytes(
    tmp_path: Path,
) -> None:
    """验证 guest 程序在源端限制整个列表 JSON 的 UTF-8 大小。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试创建足以使
        成功 JSON 超过 128 字节的多个成员。
    输出返回值：
        无；controller 必须 fail closed，且 guest stdout 本身不得
        超过调用方声明的 128 字节。
    """

    directory = tmp_path / "response-limit"
    directory.mkdir()
    for index in range(8):
        (directory / f"private-member-{index:02d}.txt").write_bytes(b"x")
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.list_directory(
            str(directory),
            max_entries=32,
            max_name_bytes=255,
            max_response_bytes=128,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "private-member" not in str(error)
        assert str(directory) not in str(error)
    else:
        raise AssertionError("超过总响应上限的目录列表应被拒绝")

    assert session.last_stdout is not None
    assert len(session.last_stdout.encode("utf-8", "strict")) <= 128


def test_controller_guest_program_stops_after_member_count_limit(
    tmp_path: Path,
) -> None:
    """验证 guest 程序在超过成员数上限时不返回部分列表。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试创建两个成员但
        把协议上限设为一。
    输出返回值：
        无；controller 必须 fail closed，guest stdout 只含固定错误对象，
        不得泄露任一部分成员名。
    """

    directory = tmp_path / "entry-limit"
    directory.mkdir()
    for name in ("private-first.txt", "private-second.txt"):
        (directory / name).write_bytes(b"x")
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.list_directory(
            str(directory),
            max_entries=1,
            max_name_bytes=255,
            max_response_bytes=4096,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "private-first" not in str(error)
        assert "private-second" not in str(error)
    else:
        raise AssertionError("超过成员数上限的列表应被整体拒绝")

    assert session.last_stdout is not None
    assert "private-first" not in session.last_stdout
    assert "private-second" not in session.last_stdout


def test_controller_collects_pixel_hash_from_extensionless_image(
    tmp_path: Path,
) -> None:
    """验证专用 getter 以 Pillow 像素字节哈希无扩展名图像。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试在其中保存
        一个不带文件后缀的真实 PNG。
    输出返回值：
        无；返回记录必须等于 ``sha256(Image.tobytes())`` 与
        原成员名，而不是压缩文件 SHA-256。
    """

    directory = tmp_path / "pixel-hash"
    directory.mkdir()
    image = Image.new("RGB", (2, 1), color=(17, 29, 43))
    expected_pixel_digest = hashlib.sha256(image.tobytes()).hexdigest()
    image_path = directory / "mountain-asset"
    image.save(image_path, format="PNG")
    assert hashlib.sha256(image_path.read_bytes()).hexdigest() != (
        expected_pixel_digest
    )
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    records = controller.collect_image_pixel_hashes(
        str(directory),
        max_entries=8,
        max_name_bytes=255,
        max_compressed_item_bytes=1024 * 1024,
        max_total_compressed_bytes=2 * 1024 * 1024,
        max_pixels_per_image=1024,
        max_decoded_item_bytes=1024 * 1024,
        max_total_decoded_bytes=2 * 1024 * 1024,
        max_response_bytes=4096,
        timeout_seconds=2.5,
    )

    assert records == ((expected_pixel_digest, "mountain-asset"),)
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/execute"
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == 2.5
    assert kwargs["json"]["shell"] is False
    argv = kwargs["json"]["command"]
    assert argv[:3] == ["python3", "-I", "-c"]
    assert argv[-10:] == [
        str(directory),
        "8",
        "255",
        str(1024 * 1024),
        str(2 * 1024 * 1024),
        "1024",
        str(1024 * 1024),
        str(2 * 1024 * 1024),
        "4096",
        "2.5",
    ]


def test_controller_raises_typed_missing_for_absent_final_image_directory(
    tmp_path: Path,
) -> None:
    """验证仅最终取证目录 ENOENT 映射为 typed missing。

    输入参数：
        tmp_path：pytest 提供的临时根；测试创建已存在的
            父目录，但不创建最终 artifact 目录。
    输出返回值：
        无：guest 必须返回不含路径的 ``status=missing``，
        公开 getter 仅将其映射为专用 typed missing 异常。
    """

    existing_parent = tmp_path / "existing-parent"
    existing_parent.mkdir()
    missing_directory = existing_parent / "private-not-produced"
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.collect_image_pixel_hashes(
            str(missing_directory),
            max_entries=8,
            max_name_bytes=255,
            max_compressed_item_bytes=1024 * 1024,
            max_total_compressed_bytes=2 * 1024 * 1024,
            max_pixels_per_image=1024,
            max_decoded_item_bytes=1024 * 1024,
            max_total_decoded_bytes=2 * 1024 * 1024,
            max_response_bytes=4096,
            timeout_seconds=2.5,
        )
    except OSWorldGuestPathMissingError as error:
        assert str(missing_directory) not in str(error)
        assert "private-not-produced" not in str(error)
    else:
        raise AssertionError("最终图像目录 ENOENT 应产生 typed missing")
    assert session.last_stdout is not None
    assert json.loads(session.last_stdout) == {
        "records": [],
        "schema_version": ("paraguibench.osworld.image-pixel-hashes.v1"),
        "status": "missing",
    }
    assert str(missing_directory) not in session.last_stdout
    assert "private-not-produced" not in session.last_stdout


def test_controller_returns_success_empty_tuple_for_existing_empty_directory(
    tmp_path: Path,
) -> None:
    """验证已存在的空目录与 ENOENT typed missing 严格区分。

    输入参数：
        tmp_path：pytest 提供的临时根；测试显式创建一个
            存在但没有任何成员的 artifact 目录。
    输出返回值：
        无：guest 必须返回 ``status=success`` 与空 records，
        公开 getter 返回空 tuple 而不抛 typed missing。
    """

    empty_directory = tmp_path / "existing-empty-directory"
    empty_directory.mkdir()
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    records = controller.collect_image_pixel_hashes(
        str(empty_directory),
        max_entries=8,
        max_name_bytes=255,
        max_compressed_item_bytes=1024 * 1024,
        max_total_compressed_bytes=2 * 1024 * 1024,
        max_pixels_per_image=1024,
        max_decoded_item_bytes=1024 * 1024,
        max_total_decoded_bytes=2 * 1024 * 1024,
        max_response_bytes=4096,
        timeout_seconds=2.5,
    )

    assert records == ()
    assert session.last_stdout is not None
    assert json.loads(session.last_stdout) == {
        "records": [],
        "schema_version": ("paraguibench.osworld.image-pixel-hashes.v1"),
        "status": "success",
    }


def test_controller_global_timeout_caps_http_and_guest_deadlines(
    tmp_path: Path,
) -> None:
    """验证 controller 全局上限同时收窄 HTTP 与 guest 计时器。

    输入参数：
        tmp_path：pytest 提供的临时根；测试使用存在的
            空目录、1.25 秒 controller 上限和 2.5 秒 getter 上限。
    输出返回值：
        无：HTTP timeout 与固定 guest argv 必须都使用较小的
        1.25 秒，避免客户端断开后 guest 继续长时间运行。
    """

    empty_directory = tmp_path / "globally-bounded-empty-directory"
    empty_directory.mkdir()
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        timeout=1.25,
        session=session,
    )

    records = controller.collect_image_pixel_hashes(
        str(empty_directory),
        max_entries=8,
        max_name_bytes=255,
        max_compressed_item_bytes=1024 * 1024,
        max_total_compressed_bytes=2 * 1024 * 1024,
        max_pixels_per_image=1024,
        max_decoded_item_bytes=1024 * 1024,
        max_total_decoded_bytes=2 * 1024 * 1024,
        max_response_bytes=4096,
        timeout_seconds=2.5,
    )

    assert records == ()
    request = session.requests[-1][2]
    assert request["timeout"] == 1.25
    assert request["json"]["command"][-1] == "1.25"


def test_controller_rejects_effective_timeout_below_one_millisecond() -> None:
    """验证更小的 controller 全局上限不能绕过 getter 下界。

    输入参数：
        无；controller 全局 timeout 为 0.000999 秒，getter
            请求为合法的 2.5 秒。
    输出返回值：
        无：两个上限取小后低于 1 毫秒，必须在传输前
        产生脱敏 controller 错误。
    """

    session = _FakeSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        timeout=0.000_999,
        session=session,
    )

    try:
        controller.collect_image_pixel_hashes(
            "/guest/existing-image-directory",
            max_entries=8,
            max_name_bytes=255,
            max_compressed_item_bytes=1024 * 1024,
            max_total_compressed_bytes=2 * 1024 * 1024,
            max_pixels_per_image=1024,
            max_decoded_item_bytes=1024 * 1024,
            max_total_decoded_bytes=2 * 1024 * 1024,
            max_response_bytes=4096,
            timeout_seconds=2.5,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "/guest/existing" not in str(error)
    else:
        raise AssertionError("有效截止低于 1 毫秒应被拒绝")

    assert session.requests == []


def test_controller_accepts_one_millisecond_timeout_without_rewriting() -> None:
    """验证跨平台 timeout 安全下界本身可被精确传输。

    输入参数：
        无；fake agent-server 返回有效空目录，调用方请求
            1 毫秒的有限 getter timeout。
    输出返回值：
        无：HTTP timeout 和 guest argv 必须一致保留 0.001 秒，
        不得放大或舍入该已签名资源上限。
    """

    class _EmptyPixelDirectorySession(_FakeSession):
        """返回已存在空图像目录的成功证据。"""

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            """记录固定 execute 请求并返回空 records。

            输入参数：
                url：controller 构造的 execute endpoint。
                kwargs：包含 argv、HTTP timeout 和 stream 标志。
            输出返回值：
                闭集成功 envelope，其 guest stdout 是空目录证据。
            """

            self.requests.append(("POST", url, kwargs))
            return _FakeResponse(
                payload={
                    "status": "success",
                    "output": (
                        '{"records":[],"schema_version":"paraguibench.'
                        'osworld.image-pixel-hashes.v1","status":"success"}'
                    ),
                    "error": "",
                    "returncode": 0,
                }
            )

    session = _EmptyPixelDirectorySession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    records = controller.collect_image_pixel_hashes(
        "/guest/existing-empty-image-directory",
        max_entries=8,
        max_name_bytes=255,
        max_compressed_item_bytes=1024 * 1024,
        max_total_compressed_bytes=2 * 1024 * 1024,
        max_pixels_per_image=1024,
        max_decoded_item_bytes=1024 * 1024,
        max_total_decoded_bytes=2 * 1024 * 1024,
        max_response_bytes=4096,
        timeout_seconds=0.001,
    )

    assert records == ()
    request = session.requests[-1][2]
    assert request["timeout"] == 0.001
    assert request["json"]["command"][-1] == "0.001"


def test_controller_guest_setitimer_stops_image_collection(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """验证 guest 内部计时器独立于 HTTP 客户端中止收集。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试放入一张可正常解码的 PNG。
        monkeypatch：在 production guest program 的计时器之后注入固定慢点，
            避免依赖平台对一纳秒 timer 的舍入和进程调度竞态。
    输出返回值：
        无：忽略 requests timeout 的本地 fake server 仍必须由 guest
        ``setitimer`` 中止，即使它尚未完成 Pillow 导入。
    """

    directory = tmp_path / "setitimer-deadline"
    directory.mkdir()
    private_name = "private-valid-image"
    Image.new("RGB", (8, 8), color=(43, 47, 53)).save(
        directory / private_name,
        format="PNG",
    )
    delayed_program = controller_module._IMAGE_PIXEL_HASH_GUEST_PROGRAM.replace(
        "import hashlib",
        "import time\ntime.sleep(0.05)\n\nimport hashlib",
        1,
    )
    assert delayed_program != controller_module._IMAGE_PIXEL_HASH_GUEST_PROGRAM
    monkeypatch.setattr(
        controller_module,
        "_IMAGE_PIXEL_HASH_GUEST_PROGRAM",
        delayed_program,
    )
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.collect_image_pixel_hashes(
            str(directory),
            max_entries=8,
            max_name_bytes=255,
            max_compressed_item_bytes=1024 * 1024,
            max_total_compressed_bytes=2 * 1024 * 1024,
            max_pixels_per_image=1024,
            max_decoded_item_bytes=1024 * 1024,
            max_total_decoded_bytes=2 * 1024 * 1024,
            max_response_bytes=4096,
            timeout_seconds=0.01,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert private_name not in str(error)
        assert str(directory) not in str(error)
    else:
        raise AssertionError("guest setitimer 应在硬截止时失败关闭")

    assert session.last_stdout is not None
    assert session.last_stdout == ""
    assert session.requests[-1][2]["timeout"] == 0.01
    assert session.requests[-1][2]["json"]["command"][-1] == "0.01"


def test_controller_preserves_guest_observation_order_for_pixel_hashes(
    tmp_path: Path,
) -> None:
    """验证专用 getter 不对 guest 观察结果重排序。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试在其中按非
            字典序创建两张真实 PNG。
    输出返回值：
        无；返回 tuple 必须与立即采样的 ``os.scandir``
        观察顺序一致。
    """

    directory = tmp_path / "observed-order"
    directory.mkdir()
    expected_by_name: dict[str, str] = {}
    for name, color in (
        ("z-last-by-name", (1, 2, 3)),
        ("a-first-by-name", (101, 102, 103)),
    ):
        image = Image.new("RGB", (2, 1), color=color)
        expected_by_name[name] = hashlib.sha256(image.tobytes()).hexdigest()
        image.save(directory / name, format="PNG")
    observed_names = tuple(entry.name for entry in os.scandir(directory))
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=_LocalExecuteSession(),
    )

    records = controller.collect_image_pixel_hashes(
        str(directory),
        max_entries=8,
        max_name_bytes=255,
        max_compressed_item_bytes=1024 * 1024,
        max_total_compressed_bytes=2 * 1024 * 1024,
        max_pixels_per_image=1024,
        max_decoded_item_bytes=1024 * 1024,
        max_total_decoded_bytes=2 * 1024 * 1024,
        max_response_bytes=4096,
        timeout_seconds=2.5,
    )

    assert records == tuple((expected_by_name[name], name) for name in observed_names)


def test_controller_rejects_symlink_and_fifo_image_members_without_echo(
    tmp_path: Path,
) -> None:
    """验证 guest helper 只接受通过 nofollow 打开的普通文件。

    输入参数：
        tmp_path：pytest 提供的临时根；测试分别创建
            指向真实 PNG 的成员符号链接、FIFO 与目录链接。
    输出返回值：
        无；两类非普通成员均必须 fail closed，且 host
        异常与 guest stdout 不得回显路径或成员名。
    """

    target = tmp_path / "outside-private-image"
    Image.new("RGB", (1, 1), color=(3, 5, 7)).save(
        target,
        format="PNG",
    )
    symlink_directory = tmp_path / "symlink-member-directory"
    symlink_directory.mkdir()
    symlink_name = "private-image-link"
    (symlink_directory / symlink_name).symlink_to(target)
    fifo_directory = tmp_path / "fifo-member-directory"
    fifo_directory.mkdir()
    fifo_name = "private-image-pipe"
    os.mkfifo(fifo_directory / fifo_name)
    outside_directory = tmp_path / "outside-private-directory"
    outside_directory.mkdir()
    outside_name = "private-outside-image"
    Image.new("RGB", (1, 1), color=(11, 13, 17)).save(
        outside_directory / outside_name,
        format="PNG",
    )
    linked_directory = tmp_path / "linked-image-directory"
    linked_directory.symlink_to(outside_directory, target_is_directory=True)

    for directory, private_name in (
        (symlink_directory, symlink_name),
        (fifo_directory, fifo_name),
        (linked_directory, outside_name),
    ):
        session = _LocalExecuteSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_image_pixel_hashes(
                str(directory),
                max_entries=8,
                max_name_bytes=255,
                max_compressed_item_bytes=1024 * 1024,
                max_total_compressed_bytes=2 * 1024 * 1024,
                max_pixels_per_image=1024,
                max_decoded_item_bytes=1024 * 1024,
                max_total_decoded_bytes=2 * 1024 * 1024,
                max_response_bytes=4096,
                timeout_seconds=2.5,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert str(directory) not in str(error)
            assert private_name not in str(error)
        else:
            raise AssertionError("非普通图像目录成员应被拒绝")

        assert session.last_stdout is not None
        assert private_name not in session.last_stdout
        assert str(directory) not in session.last_stdout


def test_controller_escalates_pillow_decompression_bomb_warning(
    tmp_path: Path,
) -> None:
    """验证 Pillow 解压炸弹警告不会被当作可继续证据。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试保存一张
            110 像素 PNG，但将 Pillow 警告阈值设为 100。
    输出返回值：
        无；``DecompressionBombWarning`` 必须升格为整体
        收集失败，且错误不回显名称或路径。
    """

    directory = tmp_path / "bomb-warning"
    directory.mkdir()
    private_name = "private-bomb-image"
    Image.new("RGB", (11, 10), color=(13, 17, 19)).save(
        directory / private_name,
        format="PNG",
    )
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.collect_image_pixel_hashes(
            str(directory),
            max_entries=8,
            max_name_bytes=255,
            max_compressed_item_bytes=1024 * 1024,
            max_total_compressed_bytes=2 * 1024 * 1024,
            max_pixels_per_image=100,
            max_decoded_item_bytes=1024 * 1024,
            max_total_decoded_bytes=2 * 1024 * 1024,
            max_response_bytes=4096,
            timeout_seconds=2.5,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert private_name not in str(error)
        assert str(directory) not in str(error)
    else:
        raise AssertionError("Pillow 解压炸弹警告应导致收集失败")

    assert session.last_stdout is not None
    assert private_name not in session.last_stdout
    assert str(directory) not in session.last_stdout


def test_controller_enforces_all_guest_image_collection_limits(
    tmp_path: Path,
) -> None:
    """验证 guest 端逐项执行文件与图像资源上限。

    输入参数：
        tmp_path：pytest 提供的临时根；测试为成员数、
            名称、单项/总压缩字节、单项/总解码字节和
            响应字节分别构造越界目录。
    输出返回值：
        无；每一种越界都必须整体 fail closed，guest stdout
        与 host 异常不回显成员名或目录。
    """

    cases: list[tuple[Path, dict[str, int]]] = []

    entry_directory = tmp_path / "entry-budget"
    entry_directory.mkdir()
    for name, color in (
        ("entry-private-one", (1, 2, 3)),
        ("entry-private-two", (4, 5, 6)),
    ):
        Image.new("RGB", (2, 2), color=color).save(
            entry_directory / name,
            format="PNG",
        )
    cases.append((entry_directory, {"max_entries": 1}))

    name_directory = tmp_path / "name-budget"
    name_directory.mkdir()
    Image.new("RGB", (2, 2), color=(7, 8, 9)).save(
        name_directory / "private-long-name",
        format="PNG",
    )
    cases.append((name_directory, {"max_name_bytes": 4}))

    compressed_item_directory = tmp_path / "compressed-item-budget"
    compressed_item_directory.mkdir()
    compressed_item_path = compressed_item_directory / "private-item"
    Image.new("RGB", (2, 2), color=(10, 11, 12)).save(
        compressed_item_path,
        format="PNG",
    )
    cases.append(
        (
            compressed_item_directory,
            {"max_compressed_item_bytes": (compressed_item_path.stat().st_size - 1)},
        )
    )

    compressed_total_directory = tmp_path / "compressed-total-budget"
    compressed_total_directory.mkdir()
    compressed_sizes: list[int] = []
    for name, color in (
        ("private-total-one", (13, 14, 15)),
        ("private-total-two", (16, 17, 18)),
    ):
        path = compressed_total_directory / name
        Image.new("RGB", (2, 2), color=color).save(path, format="PNG")
        compressed_sizes.append(path.stat().st_size)
    cases.append(
        (
            compressed_total_directory,
            {
                "max_compressed_item_bytes": max(compressed_sizes),
                "max_total_compressed_bytes": sum(compressed_sizes) - 1,
            },
        )
    )

    decoded_item_directory = tmp_path / "decoded-item-budget"
    decoded_item_directory.mkdir()
    Image.new("RGB", (2, 2), color=(19, 20, 21)).save(
        decoded_item_directory / "private-decoded-item",
        format="PNG",
    )
    cases.append((decoded_item_directory, {"max_decoded_item_bytes": 11}))

    decoded_total_directory = tmp_path / "decoded-total-budget"
    decoded_total_directory.mkdir()
    for name, color in (
        ("private-decoded-one", (22, 23, 24)),
        ("private-decoded-two", (25, 26, 27)),
    ):
        Image.new("RGB", (2, 2), color=color).save(
            decoded_total_directory / name,
            format="PNG",
        )
    cases.append(
        (
            decoded_total_directory,
            {
                "max_decoded_item_bytes": 12,
                "max_total_decoded_bytes": 23,
            },
        )
    )

    response_directory = tmp_path / "response-budget"
    response_directory.mkdir()
    Image.new("RGB", (2, 2), color=(28, 29, 30)).save(
        response_directory / ("private-response-" + "x" * 80),
        format="PNG",
    )
    cases.append((response_directory, {"max_response_bytes": 128}))

    for directory, overrides in cases:
        limits = {
            "max_entries": 8,
            "max_name_bytes": 255,
            "max_compressed_item_bytes": 1024 * 1024,
            "max_total_compressed_bytes": 2 * 1024 * 1024,
            "max_pixels_per_image": 1024,
            "max_decoded_item_bytes": 1024 * 1024,
            "max_total_decoded_bytes": 2 * 1024 * 1024,
            "max_response_bytes": 4096,
            "timeout_seconds": 2.5,
        }
        limits.update(overrides)
        session = _LocalExecuteSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_image_pixel_hashes(
                str(directory),
                **limits,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert str(directory) not in str(error)
            for entry in os.scandir(directory):
                assert entry.name not in str(error)
        else:
            raise AssertionError("guest 图像收集资源越界应被拒绝")

        assert session.last_stdout is not None
        assert str(directory) not in session.last_stdout
        for entry in os.scandir(directory):
            assert entry.name not in session.last_stdout


def test_controller_rejects_invalid_image_limits_before_transport() -> None:
    """验证 host 在网络请求前收窄图像 getter 上限。

    输入参数：
        无；测试向资源与 timeout 字段注入布尔值、零值、
            非有限数、超大值或单项大于总量的不自洽组合。
    输出返回值：
        无；所有无效组合均必须产生脱敏 controller 错误，
        fake session 不得收到任何请求。
    """

    invalid_overrides: tuple[dict[str, object], ...] = (
        {"max_entries": 0},
        {"max_entries": True},
        {"max_entries": 4_097},
        {"max_name_bytes": 0},
        {"max_name_bytes": 256},
        {"max_compressed_item_bytes": 0},
        {"max_compressed_item_bytes": 536_870_913},
        {"max_total_compressed_bytes": 1_073_741_825},
        {
            "max_compressed_item_bytes": 65,
            "max_total_compressed_bytes": 64,
        },
        {"max_pixels_per_image": 0},
        {"max_pixels_per_image": 268_435_457},
        {"max_decoded_item_bytes": 0},
        {"max_decoded_item_bytes": 536_870_913},
        {"max_total_decoded_bytes": 2_147_483_649},
        {
            "max_decoded_item_bytes": 65,
            "max_total_decoded_bytes": 64,
        },
        {"max_response_bytes": 127},
        {"max_response_bytes": 1_048_577},
        {"timeout_seconds": 0.0},
        {"timeout_seconds": 0.000_999},
        {"timeout_seconds": -1},
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": 10**400},
        {"timeout_seconds": "30.0"},
        {"timeout_seconds": 300.000_001},
    )

    for overrides in invalid_overrides:
        limits: dict[str, object] = {
            "max_entries": 8,
            "max_name_bytes": 255,
            "max_compressed_item_bytes": 1024 * 1024,
            "max_total_compressed_bytes": 2 * 1024 * 1024,
            "max_pixels_per_image": 1024,
            "max_decoded_item_bytes": 1024 * 1024,
            "max_total_decoded_bytes": 2 * 1024 * 1024,
            "max_response_bytes": 4096,
            "timeout_seconds": 2.5,
        }
        limits.update(overrides)
        session = _FakeSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_image_pixel_hashes(
                "/guest/private-image-directory",
                **limits,  # type: ignore[arg-type]
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "/guest/private" not in str(error)
        else:
            raise AssertionError("无效图像收集上限应在传输前被拒绝")
        assert session.requests == []


def test_controller_bounds_execute_envelope_before_json_decode() -> None:
    """验证专用 execute 在 JSON 解码前限制 HTTP envelope。

    输入参数：
        无；测试分别注入超限 ``Content-Length`` 和未声明
            长度但实际超限的流式响应。
    输出返回值：
        无；两种响应都在 JSON/Pillow 处理前被拒绝并关闭，
        且错误不包含 guest 路径。
    """

    max_response_bytes = 128
    max_envelope_bytes = 4096 + 4 * max_response_bytes
    responses = (
        _FakeResponse(
            content=b"{}",
            headers={"Content-Length": str(max_envelope_bytes + 1)},
        ),
        _FakeResponse(content=b"x" * (max_envelope_bytes + 1)),
    )

    for response in responses:

        class _OversizedEnvelopeSession(_FakeSession):
            """返回当前超限 HTTP 正文的 fake session。"""

            def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                """记录 execute 请求并返回预置超限响应。

                输入参数：
                    url：controller 构造的 loopback endpoint。
                    kwargs：固定 argv、timeout 和 stream 选项。
                输出返回值：
                    当前循环的超限 ``_FakeResponse``。
                """

                self.requests.append(("POST", url, kwargs))
                return response

        session = _OversizedEnvelopeSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_image_pixel_hashes(
                "/guest/private-image-directory",
                max_entries=8,
                max_name_bytes=255,
                max_compressed_item_bytes=1024 * 1024,
                max_total_compressed_bytes=2 * 1024 * 1024,
                max_pixels_per_image=1024,
                max_decoded_item_bytes=1024 * 1024,
                max_total_decoded_bytes=2 * 1024 * 1024,
                max_response_bytes=max_response_bytes,
                timeout_seconds=2.5,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "/guest/private" not in str(error)
        else:
            raise AssertionError("HTTP execute envelope 超限应被拒绝")

        assert response.closed is True
        assert session.requests[-1][2]["stream"] is True


def test_controller_sends_every_regular_member_to_pillow(
    tmp_path: Path,
) -> None:
    """验证 helper 不会按后缀或可解码性静默跳过成员。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试同时放入
            真实 PNG 与不可由 Pillow 解码的普通文件。
    输出返回值：
        无；不可解码的直接普通成员必须使整体收集
        失败，证明它未被 suffix filter 跳过。
    """

    directory = tmp_path / "all-regular-members"
    directory.mkdir()
    Image.new("RGB", (2, 2), color=(31, 37, 41)).save(
        directory / "public-image.png",
        format="PNG",
    )
    private_name = "private-not-an-image.txt"
    (directory / private_name).write_bytes(b"not an image")
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.collect_image_pixel_hashes(
            str(directory),
            max_entries=8,
            max_name_bytes=255,
            max_compressed_item_bytes=1024 * 1024,
            max_total_compressed_bytes=2 * 1024 * 1024,
            max_pixels_per_image=1024,
            max_decoded_item_bytes=1024 * 1024,
            max_total_decoded_bytes=2 * 1024 * 1024,
            max_response_bytes=4096,
            timeout_seconds=2.5,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert private_name not in str(error)
        assert str(directory) not in str(error)
    else:
        raise AssertionError("不可解码的普通成员不应被静默跳过")

    assert session.last_stdout is not None
    assert private_name not in session.last_stdout
    assert str(directory) not in session.last_stdout


def test_controller_rejects_untrusted_pixel_hash_record_schema() -> None:
    """验证 host 独立复核 guest 图像摘要的闭集 schema。

    输入参数：
        无；测试分别注入大写摘要、额外字段、重复名称
            和含分隔符的成员名。
    输出返回值：
        无；每个非法记录都必须产生脱敏 controller 错误，
        不得依赖 guest 的自我校验。
    """

    lowercase_digest = "a" * 64
    invalid_record_sets = (
        [{"name": "private-upper", "sha256": "A" * 64}],
        [
            {
                "name": "private-extra",
                "sha256": lowercase_digest,
                "extra": "private-content",
            }
        ],
        [
            {"name": "private-duplicate", "sha256": lowercase_digest},
            {"name": "private-duplicate", "sha256": "b" * 64},
        ],
        [{"name": "private/member", "sha256": lowercase_digest}],
    )

    for invalid_records in invalid_record_sets:
        guest_output = json.dumps(
            {
                "records": invalid_records,
                "schema_version": ("paraguibench.osworld.image-pixel-hashes.v1"),
                "status": "success",
            },
            separators=(",", ":"),
        )

        class _InvalidPixelRecordSession(_FakeSession):
            """返回当前非法图像摘要记录集。"""

            def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                """记录请求并包装非法 guest stdout。

                输入参数：
                    url：controller 构造的 execute endpoint。
                    kwargs：固定 argv、timeout 和 stream 选项。
                输出返回值：
                    带有非法证据 stdout 的成功传输 envelope。
                """

                self.requests.append(("POST", url, kwargs))
                return _FakeResponse(
                    payload={
                        "status": "success",
                        "output": guest_output,
                        "error": "",
                        "returncode": 0,
                    }
                )

        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=_InvalidPixelRecordSession(),
        )
        try:
            controller.collect_image_pixel_hashes(
                "/guest/private-image-directory",
                max_entries=8,
                max_name_bytes=255,
                max_compressed_item_bytes=1024 * 1024,
                max_total_compressed_bytes=2 * 1024 * 1024,
                max_pixels_per_image=1024,
                max_decoded_item_bytes=1024 * 1024,
                max_total_decoded_bytes=2 * 1024 * 1024,
                max_response_bytes=4096,
                timeout_seconds=2.5,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "private" not in str(error)
        else:
            raise AssertionError("非法图像摘要记录 schema 应被拒绝")


def test_controller_rejects_boolean_bounded_execute_returncode() -> None:
    """验证 JSON 布尔值不能伪装成整数零退出码。

    输入参数：
        无；fake agent-server 返回 ``returncode: false`` 与其余
            结构正确的空图像记录。
    输出返回值：
        无；专用有界 execute 必须严格要求非 bool 的整数
        ``returncode == 0``，而不得利用 Python 的 ``False == 0``。
    """

    class _BooleanReturncodeSession(_FakeSession):
        """返回布尔 execute returncode 的 fake session。"""

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            """记录请求并注入非法布尔退出码。

            输入参数：
                url：controller 构造的 execute endpoint。
                kwargs：固定 argv、timeout 和 stream 选项。
            输出返回值：
                stdout schema 正确但 returncode 为 False 的 envelope。
            """

            self.requests.append(("POST", url, kwargs))
            return _FakeResponse(
                payload={
                    "status": "success",
                    "output": (
                        '{"records":[],"schema_version":"paraguibench.'
                        'osworld.image-pixel-hashes.v1","status":"success"}'
                    ),
                    "error": "",
                    "returncode": False,
                }
            )

    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=_BooleanReturncodeSession(),
    )
    try:
        controller.collect_image_pixel_hashes(
            "/guest/private-image-directory",
            max_entries=8,
            max_name_bytes=255,
            max_compressed_item_bytes=1024 * 1024,
            max_total_compressed_bytes=2 * 1024 * 1024,
            max_pixels_per_image=1024,
            max_decoded_item_bytes=1024 * 1024,
            max_total_decoded_bytes=2 * 1024 * 1024,
            max_response_bytes=4096,
            timeout_seconds=2.5,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "/guest/private" not in str(error)
    else:
        raise AssertionError("bool execute returncode 应被严格拒绝")


def test_controller_collects_bounded_binary_file_with_fixed_guest_argv(
    tmp_path: Path,
) -> None:
    """验证单文件 getter 通过固定 helper 安全返回二进制字节。

    输入参数：
        tmp_path：pytest 提供的临时目录；测试写入一个
            无扩展名、同时含 NUL、非 UTF-8 与换行的文件。
    输出返回值：
        无：公开 getter 必须原样返回 bytes，并且只提交
        一次 ``python -I -c``、``shell=False`` 的有界 execute。
    """

    expected = b"\x00ParaGUI\xff\nBibTeX\r\n"
    file_path = tmp_path / "binary-artifact"
    file_path.write_bytes(expected)
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    content = controller.collect_file_bytes(
        str(file_path),
        max_bytes=1024,
        max_response_bytes=4096,
        timeout_seconds=2.5,
    )

    assert content == expected
    assert len(session.requests) == 1
    method, url, kwargs = session.requests[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/execute"
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == 2.5
    assert kwargs["json"]["shell"] is False
    argv = kwargs["json"]["command"]
    assert argv[:3] == ["python3", "-I", "-c"]
    assert argv[-4:] == [str(file_path), "1024", "4096", "2.5"]


def test_controller_collects_complete_bounded_artifact_tree_manifest(
    tmp_path: Path,
) -> None:
    """验证 Operation getter 递归枚举完整常规文件树并哈希。

    输入参数：
        tmp_path：pytest 提供的临时 guest 替身根；测试创建根文件、
            空文件、嵌套目录与二进制内容。
    输出返回值：
        无；公开 getter 必须返回按 UTF-8 路径排序的
        ``(relative_path, size, sha256)`` 闭集，且只提交一次
        ``python -I -c``、``shell=False`` 有界命令。
    """

    nested = tmp_path / "nested"
    nested.mkdir()
    files = {
        "alpha.bin": b"\x00\xffoperation",
        "empty.txt": b"",
        "nested/report.pdf": b"%PDF-1.7\nsynthetic\n",
    }
    for relative_name, content in files.items():
        destination = tmp_path / relative_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    manifest = controller.collect_artifact_tree_manifest(
        str(tmp_path),
        max_files=8,
        max_nodes=16,
        max_depth=4,
        max_name_bytes=255,
        max_file_bytes=1024,
        max_total_bytes=4096,
        max_response_bytes=4096,
        timeout_seconds=2.5,
    )

    assert manifest == tuple(
        (
            relative_name,
            len(content),
            hashlib.sha256(content).hexdigest(),
        )
        for relative_name, content in sorted(files.items())
    )
    assert len(session.requests) == 1
    method, url, kwargs = session.requests[0]
    assert method == "POST"
    assert url == "http://127.0.0.1:55001/execute"
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == 2.5
    assert kwargs["json"]["shell"] is False
    argv = kwargs["json"]["command"]
    assert argv[:3] == ["python3", "-I", "-c"]
    assert argv[-9:] == [
        str(tmp_path),
        "8",
        "16",
        "4",
        "255",
        "1024",
        "4096",
        "4096",
        "2.5",
    ]


def test_controller_artifact_tree_rejects_symlinks_and_special_files(
    tmp_path: Path,
) -> None:
    """验证递归 manifest helper 在 guest 内拒绝软链与特殊文件。

    输入参数：
        tmp_path：pytest 提供的临时 guest 替身根；测试分别构造
            目录成员软链、根目录软链和 FIFO。
    输出返回值：
        无；每种对象都只产生固定脱敏 guest 错误，公开 getter
        不得返回部分 manifest 或泄漏路径/成员名。
    """

    real_file = tmp_path / "private-real-file"
    real_file.write_bytes(b"private-content")
    member_symlink_root = tmp_path / "member-symlink-root"
    member_symlink_root.mkdir()
    (member_symlink_root / "private-link").symlink_to(real_file)
    real_root = tmp_path / "private-real-root"
    real_root.mkdir()
    (real_root / "ordinary.txt").write_bytes(b"ordinary")
    root_symlink = tmp_path / "private-root-link"
    root_symlink.symlink_to(real_root, target_is_directory=True)
    fifo_root = tmp_path / "fifo-root"
    fifo_root.mkdir()
    os.mkfifo(fifo_root / "private-fifo")

    for rejected_root in (member_symlink_root, root_symlink, fifo_root):
        session = _LocalExecuteSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        with pytest.raises(Exception) as captured:
            controller.collect_artifact_tree_manifest(
                str(rejected_root),
                max_files=8,
                max_nodes=16,
                max_depth=4,
                max_name_bytes=255,
                max_file_bytes=1024,
                max_total_bytes=4096,
                max_response_bytes=4096,
                timeout_seconds=2.5,
            )

        assert type(captured.value).__name__ == "OSWorldControllerError"
        assert "private" not in str(captured.value)
        assert session.last_stdout is not None
        assert json.loads(session.last_stdout) == {
            "error_code": "collection_failed",
            "schema_version": ("paraguibench.osworld.artifact-tree-manifest.v1"),
            "status": "error",
        }
        assert "private" not in session.last_stdout


def test_controller_artifact_tree_bounds_empty_directory_nodes(
    tmp_path: Path,
) -> None:
    """验证宽目录和空目录也消耗文件树节点预算。

    输入参数：
        tmp_path：pytest 提供的 guest 根；测试创建四个空目录，
            但只允许三个总成员节点。
    输出返回值：
        无；guest helper 必须在无界物化成员名之前脱敏失败。
    """

    for index in range(4):
        (tmp_path / f"empty-{index}").mkdir()
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    with pytest.raises(Exception) as captured:
        controller.collect_artifact_tree_manifest(
            str(tmp_path),
            max_files=3,
            max_nodes=3,
            max_depth=4,
            max_name_bytes=255,
            max_file_bytes=1024,
            max_total_bytes=4096,
            max_response_bytes=4096,
            timeout_seconds=2.5,
        )

    assert type(captured.value).__name__ == "OSWorldControllerError"
    assert "empty-" not in str(captured.value)
    assert session.last_stdout is not None
    assert json.loads(session.last_stdout) == {
        "error_code": "collection_failed",
        "schema_version": "paraguibench.osworld.artifact-tree-manifest.v1",
        "status": "error",
    }


def test_controller_maps_any_single_file_enoent_to_typed_missing(
    tmp_path: Path,
) -> None:
    """验证目标文件或任一祖先分量 ENOENT 都是 typed missing。

    输入参数：
        tmp_path：pytest 提供的临时根；测试分别使最终文件
            和中间父目录不存在。
    输出返回值：
        无：两种真实 ENOENT 都必须返回脱敏 ``status=missing``，
        公开 getter 只抛 ``OSWorldGuestPathMissingError``。
    """

    existing_parent = tmp_path / "existing-parent"
    existing_parent.mkdir()
    missing_paths = (
        existing_parent / "private-missing-file",
        tmp_path / "private-missing-parent" / "artifact.bin",
    )

    for missing_path in missing_paths:
        session = _LocalExecuteSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_file_bytes(
                str(missing_path),
                max_bytes=1024,
                max_response_bytes=4096,
                timeout_seconds=2.5,
            )
        except OSWorldGuestPathMissingError as error:
            assert str(missing_path) not in str(error)
            assert "private-missing" not in str(error)
        else:
            raise AssertionError("single-file ENOENT 应产生 typed missing")

        assert session.last_stdout is not None
        assert json.loads(session.last_stdout) == {
            "schema_version": "paraguibench.osworld.single-file.v1",
            "status": "missing",
        }
        assert str(missing_path) not in session.last_stdout


def test_controller_returns_empty_bytes_for_existing_empty_file(
    tmp_path: Path,
) -> None:
    """验证已存在的空文件不会被误判为 missing。

    输入参数：
        tmp_path：pytest 提供的临时根；测试显式创建一个
            零字节普通文件。
    输出返回值：
        无：guest 必须返回空的 canonical base64 成功对象，
        公开 getter 返回 ``b""``。
    """

    file_path = tmp_path / "existing-empty-artifact"
    file_path.write_bytes(b"")
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    content = controller.collect_file_bytes(
        str(file_path),
        max_bytes=1024,
        max_response_bytes=4096,
        timeout_seconds=2.5,
    )

    assert content == b""
    assert session.last_stdout is not None
    assert json.loads(session.last_stdout) == {
        "content_base64": "",
        "encoding": "base64",
        "schema_version": "paraguibench.osworld.single-file.v1",
        "size_bytes": 0,
        "status": "success",
    }


def test_controller_rejects_nonregular_or_symlinked_single_file_paths(
    tmp_path: Path,
) -> None:
    """验证单文件 getter 只接受无软链穿越的普通文件。

    输入参数：
        tmp_path：pytest 提供的临时根；测试构造祖先软链、最终
            软链、FIFO、目录，以及普通文件充当祖先的 ENOTDIR。
    输出返回值：
        无：每种对象都必须产生脱敏的普通 controller 错误，
        不得误映射为 ``OSWorldGuestPathMissingError``。
    """

    real_directory = tmp_path / "private-real-directory"
    real_directory.mkdir()
    regular_file = real_directory / "private-regular-file"
    regular_file.write_bytes(b"private-content")

    ancestor_symlink = tmp_path / "private-ancestor-symlink"
    ancestor_symlink.symlink_to(real_directory, target_is_directory=True)
    final_symlink = tmp_path / "private-final-symlink"
    final_symlink.symlink_to(regular_file)
    fifo_path = tmp_path / "private-fifo"
    os.mkfifo(fifo_path)
    not_directory = tmp_path / "private-not-directory"
    not_directory.write_bytes(b"not-a-directory")

    rejected_paths = (
        ancestor_symlink / regular_file.name,
        final_symlink,
        fifo_path,
        real_directory,
        not_directory / "private-child",
    )
    for rejected_path in rejected_paths:
        session = _LocalExecuteSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_file_bytes(
                str(rejected_path),
                max_bytes=1024,
                max_response_bytes=4096,
                timeout_seconds=2.5,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "private-" not in str(error)
        else:
            raise AssertionError("非普通文件或软链路径应被拒绝")

        assert session.last_stdout is not None
        assert json.loads(session.last_stdout) == {
            "error_code": "collection_failed",
            "schema_version": "paraguibench.osworld.single-file.v1",
            "status": "error",
        }
        assert "private-" not in session.last_stdout


def test_controller_enforces_single_file_raw_and_guest_response_limits(
    tmp_path: Path,
) -> None:
    """验证 guest 在读取与 base64 输出前分别执行硬上限。

    输入参数：
        tmp_path：pytest 提供的临时根；测试构造原始文件超限，
            以及原始文件合法但编码后响应会超限两种情况。
    输出返回值：
        无：两种情况均须返回固定脱敏错误，且不得泄漏内容。
    """

    raw_limited_path = tmp_path / "private-raw-limited"
    raw_limited_path.write_bytes(b"private")
    response_limited_path = tmp_path / "private-response-limited"
    response_limited_path.write_bytes(b"S" * 400)
    cases = (
        (raw_limited_path, 6, 4096),
        (response_limited_path, 400, 512),
    )

    for file_path, max_bytes, max_response_bytes in cases:
        session = _LocalExecuteSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_file_bytes(
                str(file_path),
                max_bytes=max_bytes,
                max_response_bytes=max_response_bytes,
                timeout_seconds=2.5,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "private" not in str(error)
        else:
            raise AssertionError("单文件资源上限应在 guest 内强制执行")

        assert session.last_stdout is not None
        assert json.loads(session.last_stdout) == {
            "error_code": "collection_failed",
            "schema_version": "paraguibench.osworld.single-file.v1",
            "status": "error",
        }
        assert "private" not in session.last_stdout


def test_controller_reads_operation_source_maximum_file_size(
    tmp_path: Path,
) -> None:
    """验证 Operation source 公布的最大原文件可完整传输。

    输入参数：
        tmp_path：pytest 提供的本地 guest helper 合成目录。
    输出返回值：
        无；原文件上限必须已为 base64、内层 JSON
        与 agent-server envelope 保留足够安全预算。
    """

    payload = b"A" * operation_module._MAX_FILE_BYTES
    file_path = tmp_path / "operation-boundary.bin"
    file_path.write_bytes(payload)
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=_LocalExecuteSession(),
    )

    observed = controller.collect_file_bytes(
        str(file_path),
        max_bytes=operation_module._MAX_FILE_BYTES,
        max_response_bytes=operation_module._MAX_FILE_RESPONSE_BYTES,
        timeout_seconds=5.0,
    )

    assert observed == payload


def test_controller_bounds_single_file_http_envelope_by_caller_limit() -> None:
    """验证单文件 getter 用调用方上限约束完整 HTTP envelope。

    输入参数：
        无；分别伪造超限 ``Content-Length`` 与无长度声明、
            但以合法 JSON 尾随空白填充到超限的响应。
    输出返回值：
        无：即使 envelope 与 guest stdout 的 JSON 均合法，
        也必须在解析证据前拒绝超过 512 字节的 HTTP 正文。
    """

    guest_stdout = json.dumps(
        {
            "content_base64": "",
            "encoding": "base64",
            "schema_version": "paraguibench.osworld.single-file.v1",
            "size_bytes": 0,
            "status": "success",
        },
        separators=(",", ":"),
    )
    envelope = json.dumps(
        {
            "status": "success",
            "output": guest_stdout,
            "error": "",
            "returncode": 0,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(envelope) < 513
    responses = (
        _FakeResponse(
            content=envelope,
            headers={"Content-Length": "513"},
        ),
        _FakeResponse(content=envelope + b" " * (513 - len(envelope))),
    )

    for response in responses:

        class _OversizedSingleFileEnvelopeSession(_FakeSession):
            """返回当前合法但超限的 execute HTTP 响应。"""

            def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                """记录单次 POST 并返回预置 envelope。

                输入参数：
                    url：controller 构造的 loopback execute endpoint。
                    kwargs：固定 argv、timeout 与 stream 参数。
                输出返回值：
                    当前循环注入的超限响应。
                """

                self.requests.append(("POST", url, kwargs))
                return response

        session = _OversizedSingleFileEnvelopeSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_file_bytes(
                "/guest/private-artifact",
                max_bytes=1,
                max_response_bytes=512,
                timeout_seconds=2.5,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "private-artifact" not in str(error)
        else:
            raise AssertionError("超限单文件 HTTP envelope 应被拒绝")

        assert response.closed is True
        assert len(session.requests) == 1
        assert session.requests[0][2]["stream"] is True


def test_controller_enforces_wall_clock_deadline_while_streaming_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证持续滴流的 HTTP 响应不能重置 getter 总截止时间。

    输入参数：
        monkeypatch：把 monotonic clock 固定为请求开始 10.0、
            第一个响应块到达后 11.1，超过 1 秒预算。
    输出返回值：
        无；即使 envelope 内容本身合法，controller 也必须
        关闭响应并以固定脱敏错误中止。
    """

    guest_stdout = json.dumps(
        {
            "content_base64": "",
            "encoding": "base64",
            "schema_version": "paraguibench.osworld.single-file.v1",
            "size_bytes": 0,
            "status": "success",
        },
        separators=(",", ":"),
    )
    response = _FakeResponse(
        payload={
            "status": "success",
            "output": guest_stdout,
            "error": "",
            "returncode": 0,
        }
    )

    class _DripStreamingSession(_FakeSession):
        """返回内容合法但 wall clock 已超时的响应。"""

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            """记录请求并返回已构造响应。

            输入参数：
                url：controller 固定 execute endpoint。
                kwargs：含超时与结构化 argv 的请求选项。
            输出返回值：
                合法 JSON envelope 响应。
            """

            self.requests.append(("POST", url, kwargs))
            return response

    observed_times = iter((10.0, 11.1))
    monkeypatch.setattr(
        controller_module.time,
        "monotonic",
        lambda: next(observed_times),
    )
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=_DripStreamingSession(),
    )

    with pytest.raises(Exception) as captured:
        controller.collect_file_bytes(
            "/guest/private-artifact",
            max_bytes=1,
            max_response_bytes=512,
            timeout_seconds=1.0,
        )

    assert type(captured.value).__name__ == "OSWorldControllerError"
    assert "private-artifact" not in str(captured.value)
    assert response.closed is True


def test_controller_rejects_malformed_single_file_guest_payloads() -> None:
    """验证 host 严格复核单文件 guest JSON 与 canonical base64。

    输入参数：
        无；注入非法 base64、非规范 padding bits、声明长度不符、
            超过原始上限、额外字段和错误 encoding 的成功对象。
    输出返回值：
        无：全部未信任响应必须转换成不回显字段值的 controller
        错误，不能把畸形证据交给 evaluator。
    """

    base_payload: dict[str, object] = {
        "content_base64": "WA==",
        "encoding": "base64",
        "schema_version": "paraguibench.osworld.single-file.v1",
        "size_bytes": 1,
        "status": "success",
    }
    payloads: list[dict[str, object]] = []
    for overrides in (
        {"content_base64": "PRIVATE***=", "size_bytes": 1},
        {"content_base64": "WR==", "size_bytes": 1},
        {"content_base64": "WA==", "size_bytes": 2},
        {"content_base64": "WFk=", "size_bytes": 2},
        {"encoding": "BASE64"},
        {"private_extra_field": "PRIVATE_MARKER"},
    ):
        payload = dict(base_payload)
        payload.update(overrides)
        payloads.append(payload)

    for guest_payload in payloads:

        class _MalformedSingleFileSession(_FakeSession):
            """把当前畸形 guest 对象包装为合法 agent-server envelope。"""

            def post(self, url: str, **kwargs: Any) -> _FakeResponse:
                """记录 execute 并返回预置畸形 stdout。

                输入参数：
                    url：controller 构造的 loopback endpoint。
                    kwargs：固定 argv、timeout 与 stream 参数。
                输出返回值：
                    envelope 合法、guest payload 非法的响应。
                """

                self.requests.append(("POST", url, kwargs))
                return _FakeResponse(
                    payload={
                        "status": "success",
                        "output": json.dumps(
                            guest_payload,
                            separators=(",", ":"),
                        ),
                        "error": "",
                        "returncode": 0,
                    }
                )

        session = _MalformedSingleFileSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_file_bytes(
                "/guest/private-artifact",
                max_bytes=1,
                max_response_bytes=4096,
                timeout_seconds=2.5,
            )
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "PRIVATE" not in str(error)
            assert "private-artifact" not in str(error)
        else:
            raise AssertionError("畸形单文件 guest payload 应被拒绝")

        assert len(session.requests) == 1


def test_controller_rejects_invalid_single_file_inputs_before_transport() -> None:
    """验证单文件路径、资源上限与 timeout 在 POST 前闭集校验。

    输入参数：
        无；逐项注入非规范路径、bool/非整数/越界资源限制，
            以及非有限、过小或过大的时间值。
    输出返回值：
        无：全部输入都必须产生脱敏 controller 错误，且 fake
        session 不得记录任何网络请求。
    """

    invalid_overrides: tuple[dict[str, object], ...] = (
        {"guest_path": "relative/private-artifact"},
        {"guest_path": "/"},
        {"guest_path": "/guest/../private-artifact"},
        {"guest_path": "/guest//private-artifact"},
        {"guest_path": "/guest/private-artifact/"},
        {"guest_path": "/guest/private\nartifact"},
        {"guest_path": 7},
        {"max_bytes": 0},
        {"max_bytes": True},
        {"max_bytes": 536_870_913},
        {"max_bytes": 1.0},
        {"max_response_bytes": 511},
        {"max_response_bytes": True},
        {"max_response_bytes": 16_777_217},
        {"max_response_bytes": 512.0},
        {"timeout_seconds": 0.0},
        {"timeout_seconds": 0.000_999},
        {"timeout_seconds": -1},
        {"timeout_seconds": True},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": 10**400},
        {"timeout_seconds": "2.5"},
        {"timeout_seconds": 300.000_001},
    )

    for overrides in invalid_overrides:
        parameters: dict[str, object] = {
            "guest_path": "/guest/private-artifact",
            "max_bytes": 1,
            "max_response_bytes": 512,
            "timeout_seconds": 2.5,
        }
        parameters.update(overrides)
        session = _FakeSession()
        controller = OSWorldController(
            "http://127.0.0.1:55001",
            session=session,
        )
        try:
            controller.collect_file_bytes(
                parameters["guest_path"],
                max_bytes=parameters["max_bytes"],
                max_response_bytes=parameters["max_response_bytes"],
                timeout_seconds=parameters["timeout_seconds"],
            )  # type: ignore[arg-type]
        except Exception as error:
            assert type(error).__name__ == "OSWorldControllerError"
            assert "private-artifact" not in str(error)
        else:
            raise AssertionError("非法单文件输入应在 POST 前被拒绝")
        assert session.requests == []

    globally_limited_session = _FakeSession()
    globally_limited_controller = OSWorldController(
        "http://127.0.0.1:55001",
        timeout=0.000_999,
        session=globally_limited_session,
    )
    try:
        globally_limited_controller.collect_file_bytes(
            "/guest/private-artifact",
            max_bytes=1,
            max_response_bytes=512,
            timeout_seconds=2.5,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "private-artifact" not in str(error)
    else:
        raise AssertionError("有效单文件截止低于 1 毫秒应被拒绝")
    assert globally_limited_session.requests == []


def test_controller_preserves_single_file_one_millisecond_timeout() -> None:
    """验证单文件 getter 精确保留合法的 1 毫秒截止。

    输入参数：
        无；fake guest 返回空文件成功对象，调用方请求安全下界
            ``0.001`` 秒。
    输出返回值：
        无：HTTP timeout 与 guest argv 都必须原样使用 0.001，
        且空文件仍正确返回。
    """

    class _EmptySingleFileSession(_FakeSession):
        """返回已存在空文件的闭集成功证据。"""

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            """记录 execute 并返回空文件成功 envelope。

            输入参数：
                url：controller 构造的 execute endpoint。
                kwargs：包含固定 argv、HTTP timeout 与 stream 标志。
            输出返回值：
                guest stdout 为 single-file.v1 空文件对象的响应。
            """

            self.requests.append(("POST", url, kwargs))
            return _FakeResponse(
                payload={
                    "status": "success",
                    "output": (
                        '{"content_base64":"","encoding":"base64",'
                        '"schema_version":"paraguibench.osworld.'
                        'single-file.v1","size_bytes":0,"status":"success"}'
                    ),
                    "error": "",
                    "returncode": 0,
                }
            )

    session = _EmptySingleFileSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    content = controller.collect_file_bytes(
        "/guest/existing-empty-artifact",
        max_bytes=1,
        max_response_bytes=512,
        timeout_seconds=0.001,
    )

    assert content == b""
    request = session.requests[-1][2]
    assert request["timeout"] == 0.001
    assert request["json"]["command"][-1] == "0.001"


def test_controller_guest_setitimer_stops_single_file_collection(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """验证单文件 guest 计时器独立于 HTTP 客户端生效。

    输入参数：
        tmp_path：pytest 提供的临时根；测试创建一个可读普通文件。
        monkeypatch：在 production helper 已启动计时器后注入固定
            50ms 慢点，以确定性验证 10ms guest 截止。
    输出返回值：
        无：本地 fake server 即使忽略 requests timeout，guest
        也必须被计时器中止，公开 getter 返回脱敏错误。
    """

    file_path = tmp_path / "private-setitimer-artifact"
    file_path.write_bytes(b"bounded-content")
    delayed_program = controller_module._SINGLE_FILE_GUEST_PROGRAM.replace(
        "import base64",
        "import time\ntime.sleep(0.05)\n\nimport base64",
        1,
    )
    assert delayed_program != controller_module._SINGLE_FILE_GUEST_PROGRAM
    monkeypatch.setattr(
        controller_module,
        "_SINGLE_FILE_GUEST_PROGRAM",
        delayed_program,
    )
    session = _LocalExecuteSession()
    controller = OSWorldController(
        "http://127.0.0.1:55001",
        session=session,
    )

    try:
        controller.collect_file_bytes(
            str(file_path),
            max_bytes=1024,
            max_response_bytes=4096,
            timeout_seconds=0.01,
        )
    except Exception as error:
        assert type(error).__name__ == "OSWorldControllerError"
        assert "private-setitimer" not in str(error)
        assert str(file_path) not in str(error)
    else:
        raise AssertionError("single-file guest setitimer 应中止收集")

    assert session.last_stdout == ""
    assert session.requests[-1][2]["timeout"] == 0.01
    assert session.requests[-1][2]["json"]["command"][-1] == "0.01"


def test_controller_reports_only_uninjected_loopback_transport_as_production() -> None:
    """确认 component candidate 不接受精确类型内的 fake HTTP session。

    输入参数：无；分别构造默认 requests session 和可注入 fake。
    输出返回值：只有内部新建、``trust_env=False`` 的默认
        loopback transport 返回 True；测试 fake 返回 False。
    """

    production = OSWorldController("http://127.0.0.1:55001")
    injected = OSWorldController(
        "http://127.0.0.1:55001",
        session=_FakeSession(),
    )

    assert production.uses_production_transport() is True
    assert injected.uses_production_transport() is False
