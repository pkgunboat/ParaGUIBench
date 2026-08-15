"""OSWorld artifact-family 安全收尾动作的行为测试。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence
import zipfile

import pytest

import paraguibench.integrations.osworld.artifact_finalizer as artifact_finalizer_module
from paraguibench.integrations.osworld.artifact_finalizer import (
    OSWORLD_ARTIFACT_FINALIZER_ACTIONS,
    OSWORLD_ARTIFACT_FINALIZER_TASK_IDS,
    OSWorldArtifactFinalizer,
    OSWorldArtifactFinalizerError,
)
from paraguibench.integrations.osworld.controller import CommandResult


_FAKE_LIBREOFFICE_PROGRAM = r'''#!/usr/bin/env python3
"""为 Calc finalizer 安全测试模拟受攻击的 LibreOffice 边界。"""

import os
from pathlib import Path
import sys


def inherited_directory_fd(path):
    """功能：解析 macOS 不支持遍历的 /dev/fd 目录路径。输入：路径。输出：fd 或 None。"""
    text = str(path)
    prefix = '/dev/fd/'
    if text.startswith(prefix) and text[len(prefix):].isdigit():
        return int(text[len(prefix):])
    return None


def read_private_input(input_path, outdir):
    """功能：读取普通路径或继承目录 fd 中的输入。输入：输入/目录路径。输出：bytes。"""
    directory_fd = inherited_directory_fd(outdir)
    if directory_fd is None:
        return input_path.read_bytes()
    file_fd = os.open(input_path.name, os.O_RDONLY, dir_fd=directory_fd)
    try:
        chunks = []
        while True:
            chunk = os.read(file_fd, 65536)
            if not chunk:
                return b''.join(chunks)
            chunks.append(chunk)
    finally:
        os.close(file_fd)


def write_private_output(outdir, name, payload):
    """功能：写入普通路径或继承目录 fd。输入：目录、名称、字节。输出：无。"""
    directory_fd = inherited_directory_fd(outdir)
    if directory_fd is None:
        (outdir / name).write_bytes(payload)
        return
    file_fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        os.write(file_fd, payload)
    finally:
        os.close(file_fd)


def main():
    """功能：按测试模式转换或篡改路径。输入：LibreOffice argv/env。输出：进程返回码。"""
    mode = os.environ.get('PARAGUI_TEST_LIBREOFFICE_MODE', 'success')
    if mode == 'nonzero':
        raise SystemExit(41)
    arguments = sys.argv[1:]
    outdir = Path(arguments[arguments.index('--outdir') + 1])
    input_path = Path(arguments[-1])
    canonical_input = os.environ.get('PARAGUI_TEST_CANONICAL_INPUT')
    outside_path = os.environ.get('PARAGUI_TEST_OUTSIDE_PATH')
    canonical_target = os.environ.get('PARAGUI_TEST_CANONICAL_TARGET')
    if mode == 'input-symlink-swap':
        os.unlink(canonical_input)
        os.symlink(outside_path, canonical_input)
    payload = read_private_input(input_path, outdir)
    if mode == 'output-symlink-swap':
        if os.path.lexists(canonical_target):
            os.unlink(canonical_target)
        os.symlink(outside_path, canonical_target)
    if mode == 'output-directory-symlink-swap':
        canonical_directory = os.environ['PARAGUI_TEST_CANONICAL_DIRECTORY']
        moved_directory = os.environ['PARAGUI_TEST_MOVED_DIRECTORY']
        outside_directory = os.environ['PARAGUI_TEST_OUTSIDE_DIRECTORY']
        os.rename(canonical_directory, moved_directory)
        os.symlink(outside_directory, canonical_directory)
    expected_name = f'{input_path.stem}-Sheet1.csv'
    write_private_output(outdir, expected_name, b'converted:' + payload)
    if mode == 'extra-sidecar':
        write_private_output(outdir, f'{input_path.stem}-Sheet2.csv', b'extra')


main()
'''


def _install_fake_libreoffice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str,
) -> None:
    """安装仅当前测试可见的 fake LibreOffice 可执行文件。

    输入参数：
        tmp_path：pytest 隔离目录，用于承载临时可执行文件。
        monkeypatch：pytest 环境变量隔离器。
        mode：fake 外部边界要执行的固定测试场景。
    输出返回值：
        无；将 fake bin 前置到当前测试的 ``PATH``。
    """

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    executable = fake_bin / "libreoffice"
    executable.write_text(_FAKE_LIBREOFFICE_PROGRAM, encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    )
    monkeypatch.setenv("PARAGUI_TEST_LIBREOFFICE_MODE", mode)


class _LocalArgvController:
    """在测试主机上执行 finalizer 生成的结构化 argv。

    输入参数：
        无；构造后保存完整 argv 与超时记录。
    输出返回值：
        实例作为受控 guest execute 边界的本地替身。
    """

    def __init__(self) -> None:
        """初始化空调用记录。

        输入参数：
            无。
        输出返回值：
            无；实例可供单个测试调用。
        """

        self.calls: list[tuple[tuple[str, ...], float]] = []

    def execute_with_timeout(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        """以 shell=False 执行单个固定 argv 并返回结构化结果。

        输入参数：
            command：finalizer 生成的非空字符串 argv。
            timeout_seconds：本次动作的硬超时秒数。
        输出返回值：
            映射本地子进程返回码、stdout 与 stderr 的
            ``CommandResult``。
        """

        argv = list(command)
        self.calls.append((tuple(argv), timeout_seconds))
        if argv[0] == "python3":
            argv[0] = sys.executable
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            shell=False,
            text=True,
            timeout=timeout_seconds,
        )
        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


class _RecordingArgvController:
    """记录 GUI 收尾边界调用而不触发真实桌面输入。

    输入参数：
        无；每次命令都返回结构化成功。
    输出返回值：
        实例作为桌面 GUI 系统边界的合成替身。
    """

    def __init__(self) -> None:
        """初始化空调用记录。

        输入参数：无。
        输出返回值：无。
        """

        self.calls: list[tuple[tuple[str, ...], float]] = []

    def execute_with_timeout(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        """记录 shell-free argv 与当前剩余超时。

        输入参数：
            command：finalizer 生成的命令参数闭集。
            timeout_seconds：全局 finalize budget 的当前剩余值。
        输出返回值：
            返回码为零且无输出的 ``CommandResult``。
        """

        self.calls.append((tuple(command), timeout_seconds))
        return CommandResult(returncode=0, stdout="", stderr="")


class _FailingArgvController(_RecordingArgvController):
    """返回固定非零结果的 guest 边界替身。

    输入参数：
        无；继承命令记录能力。
    输出返回值：
        实例用于验证首个外部动作失败后立即关闭。
    """

    def execute_with_timeout(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> CommandResult:
        """记录调用并返回包含敏感合成输出的非零结果。

        输入参数：
            command：finalizer 产生的固定 argv。
            timeout_seconds：当前调用超时。
        输出返回值：
            返回码 1 和不应进入异常的合成输出。
        """

        self.calls.append((tuple(command), timeout_seconds))
        return CommandResult(
            returncode=1,
            stdout="synthetic-sensitive-stdout",
            stderr="synthetic-sensitive-stderr",
        )


def test_archive_pdf_directory_creates_only_the_direct_pdf_closed_set(
    tmp_path: Path,
) -> None:
    """验证 archive 动作仅将直接 PDF 成员写入最终 ZIP。

    输入参数：
        tmp_path：pytest 提供的隔离 guest home 根目录。
    输出返回值：
        无；断言公共 finalizer 产生可读且成员闭集正确的 ZIP。
    """

    guest_home = tmp_path / "guest-home"
    shared = guest_home / "shared"
    book = guest_home / "Desktop" / "book"
    nested = book / "nested"
    shared.mkdir(parents=True)
    nested.mkdir(parents=True)
    (book / "chapter 1.pdf").write_bytes(b"chapter-one")
    (book / "chapter-2.pdf").write_bytes(b"chapter-two")
    (book / ".hidden.pdf").write_bytes(b"hidden-direct-member")
    (book / "notes.txt").write_text("not evidence", encoding="utf-8")
    (nested / "hidden.pdf").write_bytes(b"nested")
    controller = _LocalArgvController()

    handled = OSWorldArtifactFinalizer().finalize(
        "Operation-FileOperate-BatchOperation-003",
        controller,
        guest_shared_dir=str(shared),
    )

    assert handled is True
    assert len(controller.calls) == 1
    argv, timeout_seconds = controller.calls[0]
    assert argv[:3] == ("python3", "-I", "-c")
    assert all("/bin/bash" not in argument for argument in argv)
    assert timeout_seconds == 30.0
    with zipfile.ZipFile(book / "book.zip", mode="r") as archive:
        assert archive.namelist() == ["chapter 1.pdf", "chapter-2.pdf"]
        assert archive.read("chapter 1.pdf") == b"chapter-one"
        assert archive.read("chapter-2.pdf") == b"chapter-two"


def test_save_action_strictly_activates_the_pinned_window_before_ctrl_s(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 LibreOffice 收尾仅对严格窗口标题发送 Ctrl+S。

    输入参数：
        tmp_path：用于构造规范 guest shared binding 的隔离目录。
    输出返回值：
        无；断言严格 ``wmctrl -Fa`` 先于固定保存 helper，且全程
        只使用 argv 命令。
    """

    shared = tmp_path / "guest-home" / "shared"
    shared.mkdir(parents=True)
    controller = _RecordingArgvController()
    monotonic_values = iter((100.0, 101.0, 103.0))
    monkeypatch.setattr(
        artifact_finalizer_module.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    handled = OSWorldArtifactFinalizer().finalize(
        "Operation-FileOperate-CombinationDocs-009",
        controller,
        guest_shared_dir=str(shared),
    )

    assert handled is True
    assert len(controller.calls) == 2
    activate_argv, activate_timeout = controller.calls[0]
    assert activate_argv == (
        "env",
        "DISPLAY=:0",
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
        "wmctrl",
        "-Fa",
        "lecture1-2021-with-ink.pptx - LibreOffice Impress",
    )
    save_argv, save_timeout = controller.calls[1]
    assert save_argv[:3] == ("python3", "-I", "-c")
    assert all("/bin/bash" not in argument for argument in save_argv)
    assert save_argv[-3:] == ("5.0", "1.0", "26.0")
    helper_program = save_argv[3]
    assert (
        helper_program.index("time.sleep(activation_settle_seconds)")
        < helper_program.index("pyautogui.hotkey('ctrl', 's')")
        < helper_program.index("time.sleep(post_save_settle_seconds)")
    )
    assert (activate_timeout, save_timeout) == (29.0, 27.0)


def test_calc_export_uses_the_frozen_filter_and_expected_sidecar_path(
    tmp_path: Path,
) -> None:
    """验证 Calc 导出从 spec 闭集而非任务载荷构造 argv。

    输入参数：
        tmp_path：用于构造隔离 guest home 和 shared binding。
    输出返回值：
        无；断言输入 workbook、预期 Sheet1 旁挂文件、固定过滤器与
        超时均完整传入单个 shell-free helper。
    """

    shared = tmp_path / "guest-home" / "shared"
    shared.mkdir(parents=True)
    controller = _RecordingArgvController()

    handled = OSWorldArtifactFinalizer().finalize(
        "Operation-FileOperate-CombinationDocs-013",
        controller,
        guest_shared_dir=str(shared),
    )

    assert handled is True
    assert len(controller.calls) == 1
    argv, timeout_seconds = controller.calls[0]
    assert argv[:3] == ("python3", "-I", "-c")
    assert str(tmp_path / "guest-home" / "Desktop" / "GRF-p5y.xlsx") in argv
    assert str(tmp_path / "guest-home" / "Desktop" / "GRF-p5y-Sheet1.csv") in argv
    assert (
        "csv:Text - txt - csv (StarCalc):44,34,UTF-8,,,,false,true,true,false,false,1"
    ) in argv
    assert all("/bin/bash" not in argument for argument in argv)
    assert argv[-3:] == ("134217728", "268435456", "29.0")
    assert timeout_seconds == 30.0


def test_calc_export_failure_preserves_previous_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证转换失败不会先删除上一份可信 CSV。

    输入参数：
        tmp_path：pytest 隔离 guest home 与 fake bin 根目录。
        monkeypatch：pytest 环境变量隔离器。
    输出返回值：
        无；断言失败被折叠为固定错误且旧 sidecar 字节保持不变。
    """

    guest_home = tmp_path / "guest-home"
    shared = guest_home / "shared"
    desktop = guest_home / "Desktop"
    shared.mkdir(parents=True)
    desktop.mkdir()
    (desktop / "GRF-p5y.xlsx").write_bytes(b"trusted-workbook")
    sidecar = desktop / "GRF-p5y-Sheet1.csv"
    sidecar.write_bytes(b"previous-sidecar")
    _install_fake_libreoffice(tmp_path, monkeypatch, mode="nonzero")

    with pytest.raises(
        OSWorldArtifactFinalizerError,
        match="^ARTIFACT_FINALIZE_ACTION_ERROR$",
    ):
        OSWorldArtifactFinalizer().finalize(
            "Operation-FileOperate-CombinationDocs-013",
            _LocalArgvController(),
            guest_shared_dir=str(shared),
        )

    assert sidecar.read_bytes() == b"previous-sidecar"


def test_calc_export_rejects_input_symlink_swap_without_committing_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证输入名在转换期间换链时 fail closed。

    输入参数：
        tmp_path：pytest 隔离 guest home、外部文件与 fake bin 根目录。
        monkeypatch：pytest 环境变量隔离器。
    输出返回值：
        无；断言 LibreOffice 不读取换链目标，且不提交新 sidecar。
    """

    guest_home = tmp_path / "guest-home"
    shared = guest_home / "shared"
    desktop = guest_home / "Desktop"
    shared.mkdir(parents=True)
    desktop.mkdir()
    workbook = desktop / "GRF-p5y.xlsx"
    workbook.write_bytes(b"trusted-workbook")
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"attacker-workbook")
    sidecar = desktop / "GRF-p5y-Sheet1.csv"
    _install_fake_libreoffice(
        tmp_path,
        monkeypatch,
        mode="input-symlink-swap",
    )
    monkeypatch.setenv("PARAGUI_TEST_CANONICAL_INPUT", str(workbook))
    monkeypatch.setenv("PARAGUI_TEST_OUTSIDE_PATH", str(outside))

    with pytest.raises(
        OSWorldArtifactFinalizerError,
        match="^ARTIFACT_FINALIZE_ACTION_ERROR$",
    ):
        OSWorldArtifactFinalizer().finalize(
            "Operation-FileOperate-CombinationDocs-013",
            _LocalArgvController(),
            guest_shared_dir=str(shared),
        )

    assert not sidecar.exists()
    assert outside.read_bytes() == b"attacker-workbook"


def test_calc_export_atomically_replaces_raced_output_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证目标名被换成链接时原子替换链接本身而不跟随。

    输入参数：
        tmp_path：pytest 隔离 guest home、外部哨兵与 fake bin 根目录。
        monkeypatch：pytest 环境变量隔离器。
    输出返回值：
        无；断言目标成为可信普通文件且链接目标字节未被写入。
    """

    guest_home = tmp_path / "guest-home"
    shared = guest_home / "shared"
    desktop = guest_home / "Desktop"
    shared.mkdir(parents=True)
    desktop.mkdir()
    (desktop / "GRF-p5y.xlsx").write_bytes(b"trusted-workbook")
    sidecar = desktop / "GRF-p5y-Sheet1.csv"
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"outside-guard")
    _install_fake_libreoffice(
        tmp_path,
        monkeypatch,
        mode="output-symlink-swap",
    )
    monkeypatch.setenv("PARAGUI_TEST_CANONICAL_TARGET", str(sidecar))
    monkeypatch.setenv("PARAGUI_TEST_OUTSIDE_PATH", str(outside))

    handled = OSWorldArtifactFinalizer().finalize(
        "Operation-FileOperate-CombinationDocs-013",
        _LocalArgvController(),
        guest_shared_dir=str(shared),
    )

    assert handled is True
    assert not sidecar.is_symlink()
    assert sidecar.read_bytes() == b"converted:trusted-workbook"
    assert outside.read_bytes() == b"outside-guard"


def test_calc_export_rejects_additional_sheet_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证转换私有目录出现额外 Sheet2 时拒绝整次提交。

    输入参数：
        tmp_path：pytest 隔离 guest home 与 fake bin 根目录。
        monkeypatch：pytest 环境变量隔离器。
    输出返回值：
        无；断言旧 Sheet1 保持且额外 sidecar 不进入 canonical 目录。
    """

    guest_home = tmp_path / "guest-home"
    shared = guest_home / "shared"
    desktop = guest_home / "Desktop"
    shared.mkdir(parents=True)
    desktop.mkdir()
    (desktop / "GRF-p5y.xlsx").write_bytes(b"trusted-workbook")
    sidecar = desktop / "GRF-p5y-Sheet1.csv"
    sidecar.write_bytes(b"previous-sidecar")
    _install_fake_libreoffice(
        tmp_path,
        monkeypatch,
        mode="extra-sidecar",
    )

    with pytest.raises(
        OSWorldArtifactFinalizerError,
        match="^ARTIFACT_FINALIZE_ACTION_ERROR$",
    ):
        OSWorldArtifactFinalizer().finalize(
            "Operation-FileOperate-CombinationDocs-013",
            _LocalArgvController(),
            guest_shared_dir=str(shared),
        )

    assert sidecar.read_bytes() == b"previous-sidecar"
    assert not (desktop / "GRF-p5y-Sheet2.csv").exists()


def test_calc_export_rejects_output_directory_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 canonical 输出目录换链后拒绝向失去身份的路径提交。

    输入参数：
        tmp_path：pytest 隔离 guest home、攻击目录与 fake bin 根目录。
        monkeypatch：pytest 环境变量隔离器。
    输出返回值：
        无；断言 held 目录仅用于清理，攻击目录没有任何产物。
    """

    guest_home = tmp_path / "guest-home"
    shared = guest_home / "shared"
    desktop = guest_home / "Desktop"
    moved_desktop = guest_home / "Desktop-held"
    outside_directory = tmp_path / "outside-directory"
    shared.mkdir(parents=True)
    desktop.mkdir()
    outside_directory.mkdir()
    (desktop / "GRF-p5y.xlsx").write_bytes(b"trusted-workbook")
    _install_fake_libreoffice(
        tmp_path,
        monkeypatch,
        mode="output-directory-symlink-swap",
    )
    monkeypatch.setenv("PARAGUI_TEST_CANONICAL_DIRECTORY", str(desktop))
    monkeypatch.setenv("PARAGUI_TEST_MOVED_DIRECTORY", str(moved_desktop))
    monkeypatch.setenv(
        "PARAGUI_TEST_OUTSIDE_DIRECTORY",
        str(outside_directory),
    )

    with pytest.raises(
        OSWorldArtifactFinalizerError,
        match="^ARTIFACT_FINALIZE_ACTION_ERROR$",
    ):
        OSWorldArtifactFinalizer().finalize(
            "Operation-FileOperate-CombinationDocs-013",
            _LocalArgvController(),
            guest_shared_dir=str(shared),
        )

    assert desktop.is_symlink()
    assert tuple(outside_directory.iterdir()) == ()
    assert not (moved_desktop / "GRF-p5y-Sheet1.csv").exists()


def test_finalizer_catalog_is_exactly_the_thirteen_remaining_tasks() -> None:
    """验证 finalizer 只覆盖剩余 13 件 artifact-family 任务。

    输入参数：
        无；使用公共不可变 catalog。
    输出返回值：
        无；断言 1 个 archive、2 个 export、7 个 strict-save 与 3 个
        none 组成唯一动作闭集。
    """

    assert frozenset(OSWORLD_ARTIFACT_FINALIZER_ACTIONS) == (
        OSWORLD_ARTIFACT_FINALIZER_TASK_IDS
    )
    assert len(OSWORLD_ARTIFACT_FINALIZER_TASK_IDS) == 13
    action_ids = tuple(OSWORLD_ARTIFACT_FINALIZER_ACTIONS.values())
    assert action_ids.count("archive-pdf-directory") == 1
    assert action_ids.count("export-calc-first-sheet-csv") == 2
    assert action_ids.count("save-active-libreoffice-document") == 7
    assert action_ids.count("none") == 3


@pytest.mark.parametrize(
    "none_task_id",
    (
        "Operation-FileOperate-CombinationDocs-010",
        "Operation-FileOperate-CombinationDocs-011",
        "Operation-FileOperate-SearchAndWrite-009",
    ),
)
def test_none_action_is_zero_io_and_previously_migrated_task_is_not_claimed(
    tmp_path: Path,
    none_task_id: str,
) -> None:
    """验证三项 none 收尾零 I/O，且不误报已迁移的第 15 任务。

    输入参数：
        tmp_path：构造规范 shared binding，none 路径不应读取它。
        none_task_id：固定目录中参数化的三个 ``none`` 任务之一。
    输出返回值：
        无；断言 13-task 中的 none 返回 True，CombinationDocs-015
        返回 False，两者均不调用 controller。
    """

    controller = _RecordingArgvController()
    finalizer = OSWorldArtifactFinalizer()

    assert (
        finalizer.finalize(
            none_task_id,
            controller,
            guest_shared_dir=str(tmp_path / "not-read"),
        )
        is True
    )
    assert (
        finalizer.finalize(
            "Operation-FileOperate-CombinationDocs-015",
            controller,
            guest_shared_dir=str(tmp_path / "not-read"),
        )
        is False
    )
    assert controller.calls == []


def test_strict_window_failure_stops_before_save_and_redacts_guest_output(
    tmp_path: Path,
) -> None:
    """验证严格窗口激活失败后不发送 Ctrl+S 且异常脱敏。

    输入参数：
        tmp_path：构造有效 guest shared binding。
    输出返回值：
        无；断言仅发生首次 ``wmctrl -Fa`` 调用，且错误不含
        guest stdout、stderr 或窗口标题。
    """

    shared = tmp_path / "guest-home" / "shared"
    shared.mkdir(parents=True)
    controller = _FailingArgvController()

    with pytest.raises(OSWorldArtifactFinalizerError) as caught:
        OSWorldArtifactFinalizer().finalize(
            "Operation-FileOperate-CombinationDocs-009",
            controller,
            guest_shared_dir=str(shared),
        )

    assert str(caught.value) == "ARTIFACT_FINALIZE_ACTION_ERROR"
    assert len(controller.calls) == 1
    assert "synthetic-sensitive" not in str(caught.value)
    assert "lecture1-2021" not in str(caught.value)


def test_invalid_shared_binding_and_missing_controller_fail_before_io(
    tmp_path: Path,
) -> None:
    """验证路径与 controller 能力在收尾副作用之前完整预检。

    输入参数：
        tmp_path：仅用于构造不规范和规范 shared locator。
    输出返回值：
        无；断言两种失败均使用固定机器错误码，已提供的
        recording controller 不发生任何 I/O。
    """

    controller = _RecordingArgvController()
    finalizer = OSWorldArtifactFinalizer()

    with pytest.raises(
        OSWorldArtifactFinalizerError,
        match="^ARTIFACT_FINALIZE_PATH_ERROR$",
    ):
        finalizer.finalize(
            "Operation-FileOperate-BatchOperation-003",
            controller,
            guest_shared_dir=str(tmp_path / "guest-home" / ".." / "shared"),
        )
    assert controller.calls == []

    valid_shared = tmp_path / "guest-home" / "shared"
    valid_shared.mkdir(parents=True)
    with pytest.raises(
        OSWorldArtifactFinalizerError,
        match="^ARTIFACT_FINALIZE_CONTROLLER_ERROR$",
    ):
        finalizer.finalize(
            "Operation-FileOperate-BatchOperation-003",
            object(),
            guest_shared_dir=str(valid_shared),
        )
    assert controller.calls == []


def test_archive_rejects_symlinked_pdf_without_writing_an_output(
    tmp_path: Path,
) -> None:
    """验证 PDF 成员符号链接使 archive 整体失败关闭。

    输入参数：
        tmp_path：提供 guest home 与不在 book 目录内的合成目标文件。
    输出返回值：
        无；断言固定错误码、ZIP 不存在，且链接目标内容未被修改。
    """

    guest_home = tmp_path / "guest-home"
    shared = guest_home / "shared"
    book = guest_home / "Desktop" / "book"
    shared.mkdir(parents=True)
    book.mkdir(parents=True)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"must-not-be-read-or-changed")
    (book / "linked.pdf").symlink_to(outside)

    with pytest.raises(
        OSWorldArtifactFinalizerError,
        match="^ARTIFACT_FINALIZE_ACTION_ERROR$",
    ):
        OSWorldArtifactFinalizer().finalize(
            "Operation-FileOperate-BatchOperation-003",
            _LocalArgvController(),
            guest_shared_dir=str(shared),
        )

    assert not (book / "book.zip").exists()
    assert outside.read_bytes() == b"must-not-be-read-or-changed"
