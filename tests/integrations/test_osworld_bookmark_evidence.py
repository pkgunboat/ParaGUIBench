"""OSWorld Chrome Bookmarks 受控基线重置与证据源测试。"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from paraguibench.integrations.osworld.bookmark_contracts import (
    CHROME_BOOKMARKS_PROTOCOL_ID,
    OSWORLD_BOOKMARK_TASK_IDS,
)
from paraguibench.integrations.osworld.bookmark_evidence import (
    BOOKMARKS_MAX_FILE_BYTES,
    OSWorldBookmarkEvidenceError,
    OSWorldChromeBookmarkEvidenceSource,
    parse_chrome_bookmarks_json,
)
from paraguibench.integrations.osworld.controller import CommandResult


class _BookmarkController:
    """记录书签 prepare/capture 固定 I/O 的单 VM fake。"""

    def __init__(self, bookmarks: bytes) -> None:
        """保存待返回的 Bookmarks 字节并初始化调用记录。

        输入参数：
            bookmarks：``read_file`` 应返回的合成 JSON bytes。
        输出返回值：
            无。
        """

        self.bookmarks = bookmarks
        self.executed: list[list[str]] = []
        self.launched: list[list[str]] = []
        self.reads: list[tuple[str, int, int, float]] = []
        self.chrome_exit_waits: list[float] = []
        self.cdp_waits: list[tuple[int, float]] = []
        self.activated_windows: list[str] = []

    def get_desktop_path(self) -> str:
        """返回非硬编码用户名的 guest Desktop 路径。

        输入参数：无。
        输出返回值：合成 POSIX 绝对路径。
        """

        return "/dynamic-user/Desktop"

    def execute(self, command: list[str]) -> CommandResult:
        """记录同步 argv 并模拟成功重置/关闭。

        输入参数：
            command：受控 evidence source 发出的 argv。
        输出返回值：
            returncode=0 的固定命令结果。
        """

        self.executed.append(list(command))
        return CommandResult(returncode=0, stdout="", stderr="")

    def launch(self, command: list[str]) -> None:
        """记录固定 Chrome 启动 argv。

        输入参数：
            command：异步启动 argv。
        输出返回值：
            无。
        """

        self.launched.append(list(command))

    def wait_for_chrome_exit(self, *, timeout: float) -> None:
        """记录等待 Chrome 完整落盘的截止时间。

        输入参数：
            timeout：最长等待秒数。
        输出返回值：
            无。
        """

        self.chrome_exit_waits.append(timeout)

    def wait_for_chrome_cdp(self, *, port: int, timeout: float) -> None:
        """记录重置后 Chrome 就绪等待。

        输入参数：
            port：固定 guest CDP 端口。
            timeout：最长等待秒数。
        输出返回值：
            无。
        """

        self.cdp_waits.append((port, timeout))

    def activate_window(self, window_name: str) -> None:
        """记录将 Chrome 放到任务前台的结构化调用。

        输入参数：
            window_name：固定窗口标题。
        输出返回值：
            无。
        """

        self.activated_windows.append(window_name)

    def collect_file_bytes(
        self,
        guest_path: str,
        *,
        max_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        """记录无符号链接的有界文件采集并返回快照。

        输入参数：
            guest_path：evidence source 从动态 home 推导的路径。
            max_bytes：原始 Bookmarks 文件的协议上限。
            max_response_bytes：base64 JSON/HTTP envelope 协议上限。
            timeout_seconds：guest helper 与 HTTP 共用的截止秒数。
        输出返回值：
            构造时保存的 Bookmarks bytes。
        """

        self.reads.append(
            (
                guest_path,
                max_bytes,
                max_response_bytes,
                timeout_seconds,
            )
        )
        return self.bookmarks


def _task() -> dict[str, str]:
    """返回一个已冻结身份的 canonical bookmark 任务。

    输入参数：无。
    输出返回值：Settings-002 的最小可信绑定字段。
    """

    return {
        "task_id": "Operation-WebOperate-Settings-002",
        "task_uid": "ef47625b-cd1b-46ca-a16c-b0ac0c99c2cc",
        "task_source": "",
        "task_type": "self",
        "task_tag": "WebOperate",
        "evaluator_path": "eval/webnavigate_bookmark_evaluator.py",
    }


def _bookmarks_payload() -> bytes:
    """构造同 URL 跨文件夹与同路径重复的 Chrome JSON。

    输入参数：无。
    输出返回值：严格 UTF-8 JSON bytes。
    """

    return json.dumps(
        {
            "checksum": "ignored-version-field",
            "roots": {
                "bookmark_bar": {
                    "type": "folder",
                    "name": "Bookmarks bar",
                    "children": [
                        {
                            "type": "folder",
                            "name": "My Favorite Authors",
                            "children": [
                                {
                                    "type": "url",
                                    "name": "Jim",
                                    "url": "https://jimfan.me/",
                                },
                                {
                                    "type": "url",
                                    "name": "duplicate",
                                    "url": "https://jimfan.me/",
                                },
                            ],
                        }
                    ],
                },
                "other": {
                    "type": "folder",
                    "name": "Other bookmarks",
                    "children": [
                        {
                            "type": "url",
                            "name": "Jim elsewhere",
                            "url": "https://jimfan.me/",
                        }
                    ],
                },
            },
            "version": 1,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_prepare_resets_fixed_dynamic_profile_before_relaunching_chrome() -> None:
    """验证任务前只对动态 home 下固定 profile 执行基线重置。

    输入参数：
        无；使用可记录调用的 controller fake。
    输出返回值：
        无；先停 Chrome、后执行固定重置 helper，再重启并聚焦。
    """

    controller = _BookmarkController(_bookmarks_payload())
    source = OSWorldChromeBookmarkEvidenceSource()

    source.prepare(_task(), controller)

    assert controller.executed[0] == ["pkill", "chrome"]
    assert controller.executed[1][:4] == ["python", "-I", "-c", source.reset_program]
    assert controller.executed[1][4:] == [
        "/dynamic-user/.config/google-chrome/Default/Bookmarks"
    ]
    assert controller.chrome_exit_waits == [15.0]
    assert controller.launched == [["google-chrome", "--remote-debugging-port=1337"]]
    assert controller.cdp_waits == [(1337, 15.0)]
    assert controller.activated_windows == ["Google Chrome"]


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("task_source", "OSWorld"),
        ("task_type", "OSWorld脚本"),
        ("task_tag", "QA"),
    ],
)
def test_prepare_rejects_complete_bookmark_identity_drift_before_guest_io(
    field: str,
    drifted_value: str,
) -> None:
    """验证分类身份漂移不会先修改 Chrome profile。

    输入参数：
        field：pytest 注入的来源、类型或标签字段。
        drifted_value：与正式 binding 不一致的替代值。
    输出返回值：
        无；evidence source 在任何 controller I/O 前拒绝完整身份漂移。
    """

    task = {**_task(), field: drifted_value}
    controller = _BookmarkController(_bookmarks_payload())

    with pytest.raises(
        OSWorldBookmarkEvidenceError,
        match="bookmark task 身份绑定无效",
    ):
        OSWorldChromeBookmarkEvidenceSource().prepare(task, controller)

    assert controller.executed == []
    assert controller.launched == []


@pytest.mark.parametrize("task_id", sorted(OSWORLD_BOOKMARK_TASK_IDS))
def test_all_eleven_canonical_task_bindings_match_the_evidence_catalog(
    task_id: str,
) -> None:
    """验证 11 个当前 canonical task 的 ID/UID/evaluator 三元绑定未漂移。

    输入参数：
        task_id：pytest 注入的固定 bookmark 任务 ID。
    输出返回值：
        无；读取仓库内 canonical JSON 并通过 prepare 身份门。
    """

    repo_root = Path(__file__).resolve().parents[2]
    task = json.loads(
        (repo_root / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    controller = _BookmarkController(_bookmarks_payload())

    OSWorldChromeBookmarkEvidenceSource().prepare(task, controller)

    assert controller.executed[0] == ["pkill", "chrome"]
    assert controller.launched == [["google-chrome", "--remote-debugging-port=1337"]]


def test_real_reset_program_atomically_clears_primary_and_backup(
    tmp_path: Path,
) -> None:
    """验证生产 guest helper 的真实文件效果，而非仅验证 argv。

    输入参数：
        tmp_path：pytest 提供的隔离临时 guest home。
    输出返回值：
        无；主 Bookmarks 被原子替换为可解析空基线，
        ``Bookmarks.bak`` 被删除，且 helper 不输出内容。
    """

    profile = tmp_path / ".config" / "google-chrome" / "Default"
    profile.mkdir(parents=True)
    bookmarks_path = profile / "Bookmarks"
    bookmarks_path.write_bytes(_bookmarks_payload())
    backup_path = profile / "Bookmarks.bak"
    backup_path.write_bytes(_bookmarks_payload())

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            OSWorldChromeBookmarkEvidenceSource().reset_program,
            str(bookmarks_path),
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert result.stdout == b""
    assert result.stderr == b""
    assert not backup_path.exists()
    observation = parse_chrome_bookmarks_json(bookmarks_path.read_bytes())
    assert observation.records == ()
    assert not (profile / ".paraguibench-bookmarks-reset").exists()


def test_real_reset_program_refuses_symlinked_profile_ancestor(
    tmp_path: Path,
) -> None:
    """验证 guest helper 不会穿越被替换为符号链接的 profile 祖先。

    输入参数：
        tmp_path：pytest 提供的隔离文件系统根。
    输出返回值：
        无；helper 必须非零退出，链接目标中的旧 URL 不变。
    """

    outside_profile = tmp_path / "outside" / "google-chrome" / "Default"
    outside_profile.mkdir(parents=True)
    outside_bookmarks = outside_profile / "Bookmarks"
    original = _bookmarks_payload()
    outside_bookmarks.write_bytes(original)
    config = tmp_path / "home" / ".config"
    config.mkdir(parents=True)
    (config / "google-chrome").symlink_to(
        tmp_path / "outside" / "google-chrome",
        target_is_directory=True,
    )
    requested_path = config / "google-chrome" / "Default" / "Bookmarks"

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            OSWorldChromeBookmarkEvidenceSource().reset_program,
            str(requested_path),
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode != 0
    assert outside_bookmarks.read_bytes() == original


def test_capture_stops_chrome_before_bounded_read_and_preserves_hierarchy() -> None:
    """验证 capture 先同步 Chrome，再以 4 MiB 上限读取并保留层级。

    输入参数：
        无；使用含跨文件夹重复 URL 的合法 JSON。
    输出返回值：
        无；同 URL+路径去重，不同路径保留，repr 不泄露。
    """

    controller = _BookmarkController(_bookmarks_payload())
    source = OSWorldChromeBookmarkEvidenceSource()

    observations = source.capture(
        CHROME_BOOKMARKS_PROTOCOL_ID,
        controller,
    )

    assert controller.executed == [["pkill", "chrome"]]
    assert controller.chrome_exit_waits == [15.0]
    assert controller.reads == [
        (
            "/dynamic-user/.config/google-chrome/Default/Bookmarks",
            4 * 1024 * 1024,
            6 * 1024 * 1024,
            15.0,
        )
    ]
    assert len(observations) == 1
    assert [record.folder_path for record in observations[0].records] == [
        ("bookmark_bar", "My Favorite Authors"),
        ("other",),
    ]
    assert [record.url for record in observations[0].records] == [
        "https://jimfan.me/",
        "https://jimfan.me/",
    ]
    assert "jimfan" not in repr(observations[0])
    assert "Favorite" not in repr(observations[0])


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        b'{"roots":[],"version":1}',
        b'{"roots":{"bookmark_bar":{"type":"url","url":"https://example.test/"}}}',
        b'{"roots":{},"roots":{}}',
        b'{"roots":{},"version":NaN}',
    ],
)
def test_invalid_or_ambiguous_json_fails_closed(payload: bytes) -> None:
    """验证损坏、schema 漂移、重复键与 NaN 均不会产生部分快照。

    输入参数：
        payload：pytest 注入的不可信 Bookmarks bytes。
    输出返回值：
        无；只抛不回显 payload 的固定 evidence error。
    """

    with pytest.raises(OSWorldBookmarkEvidenceError, match="Bookmarks") as error:
        parse_chrome_bookmarks_json(payload)
    assert "example.test" not in str(error.value)


def test_parser_enforces_file_record_depth_and_string_limits() -> None:
    """验证文件、URL 数、层深和文件夹字节上限均 fail-closed。

    输入参数：
        无；分别构造四类超限输入。
    输出返回值：
        无；所有超限输入都被拒绝。
    """

    oversized_file = b"{" + b" " * BOOKMARKS_MAX_FILE_BYTES + b"}"
    too_many_urls = {
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "name": "bar",
                "children": [
                    {
                        "type": "url",
                        "name": "x",
                        "url": f"https://example.test/{index}",
                    }
                    for index in range(4097)
                ],
            }
        }
    }
    deep_node: dict[str, object] = {
        "type": "url",
        "name": "x",
        "url": "https://example.test/",
    }
    for index in range(32):
        deep_node = {
            "type": "folder",
            "name": f"folder-{index}",
            "children": [deep_node],
        }
    too_deep = {
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "name": "bar",
                "children": [deep_node],
            }
        }
    }
    oversized_folder = {
        "roots": {
            "x" * 1025: {
                "type": "folder",
                "name": "bar",
                "children": [],
            }
        }
    }

    for payload in (
        oversized_file,
        json.dumps(too_many_urls).encode(),
        json.dumps(too_deep).encode(),
        json.dumps(oversized_folder).encode(),
    ):
        with pytest.raises(OSWorldBookmarkEvidenceError):
            parse_chrome_bookmarks_json(payload)


def test_prepare_rejects_identity_drift_before_guest_side_effects() -> None:
    """验证 task UID/evaluator 身份漂移时不执行 guest 命令。

    输入参数：
        无；故意替换 Settings-002 的 task UID。
    输出返回值：
        无；prepare 抛固定绑定错误且调用记录为空。
    """

    controller = _BookmarkController(_bookmarks_payload())
    source = OSWorldChromeBookmarkEvidenceSource()

    with pytest.raises(OSWorldBookmarkEvidenceError, match="身份"):
        source.prepare({**_task(), "task_uid": "drifted"}, controller)
    assert controller.executed == []
    assert controller.launched == []


def test_unknown_protocol_and_unsafe_dynamic_home_fail_closed() -> None:
    """验证未知协议与不安全 Desktop 路径不会读取任意文件。

    输入参数：
        无；传入未注册协议并使 fake 返回根目录 Desktop。
    输出返回值：
        无；两种情况均抛固定错误且无读取。
    """

    controller = _BookmarkController(_bookmarks_payload())
    source = OSWorldChromeBookmarkEvidenceSource()
    with pytest.raises(OSWorldBookmarkEvidenceError, match="protocol"):
        source.capture("untrusted.protocol", controller)

    controller.get_desktop_path = lambda: "/Desktop"  # type: ignore[method-assign]
    with pytest.raises(OSWorldBookmarkEvidenceError, match="home"):
        source.capture(CHROME_BOOKMARKS_PROTOCOL_ID, controller)
    assert controller.reads == []
