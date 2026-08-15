"""13 个 legacy OSWorld artifact-family 任务的安全收尾边界。

收尾动作只能来自已摘要绑定的 evidence spec；本模块不读取
Agent final text，不接受任务载荷中的命令或路径覆盖，也不使用 shell。
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import PurePosixPath
import time
from types import MappingProxyType
from typing import Any, Callable

from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
    ArtifactEvidenceSpec,
    canonical_artifact_evidence_spec_json,
)


ARTIFACT_FINALIZER_SCHEMA_ID = "paraguibench.osworld.artifact-finalizer.v1"
OSWORLD_ARTIFACT_FINALIZER_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperation-003",
        "Operation-FileOperate-CombinationDocs-009",
        "Operation-FileOperate-CombinationDocs-010",
        "Operation-FileOperate-CombinationDocs-011",
        "Operation-FileOperate-CombinationDocs-012",
        "Operation-FileOperate-CombinationDocs-013",
        "Operation-FileOperate-CombinationDocs-014",
        "Operation-FileOperate-SearchAndWrite-001",
        "Operation-FileOperate-SearchAndWrite-003",
        "Operation-FileOperate-SearchAndWrite-005",
        "Operation-FileOperate-SearchAndWrite-009",
        "Operation-FileOperate-Settings-001",
        "Operation-WebOperate-SearchAndWrite-001",
    }
)
OSWORLD_ARTIFACT_FINALIZER_ACTIONS = MappingProxyType(
    {
        task_id: OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id].finalize_action_id
        for task_id in sorted(OSWORLD_ARTIFACT_FINALIZER_TASK_IDS)
    }
)


class OSWorldArtifactFinalizerError(RuntimeError):
    """表示收尾身份、controller 能力或动作结果无法可靠绑定。"""


_ARCHIVE_PDF_DIRECTORY_PROGRAM = r"""
import os
import signal
import stat
import sys
import zipfile


class FinalizeFailure(Exception):
    '''表示 guest 收尾边界不可满足且不回显敏感值。'''


def handle_timeout(_signum, _frame):
    '''功能：将真实时间计时器转换为固定失败。输入：信号参数。输出：始终抛错。'''
    raise FinalizeFailure()


def open_directory_without_symlinks(path):
    '''功能：逐级 nofollow 打开绝对目录。输入：POSIX 路径。输出：目录 fd。'''
    if not isinstance(path, str) or not path.startswith('/') or path.endswith('/'):
        raise FinalizeFailure()
    parts = path.split('/')[1:]
    if not parts or any(part in {'', '.', '..'} for part in parts):
        raise FinalizeFailure()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current_fd = os.open('/', flags)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def safe_name(name):
    '''功能：验证直接成员名。输入：文件名。输出：安全时返回 True。'''
    return (
        isinstance(name, str)
        and name not in {'', '.', '..'}
        and '/' not in name
        and '\\' not in name
        and '\x00' not in name
        and len(name.encode('utf-8', 'strict')) <= 255
        and all(character.isprintable() for character in name)
    )


def write_member(archive, directory_fd, name, expected_stat, max_item_bytes):
    '''功能：在 inode 复核后流式写入一个 PDF。输入：归档、fd、名称与上限。输出：无。'''
    file_fd = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        dir_fd=directory_fd,
    )
    try:
        observed = os.fstat(file_fd)
        if (
            not stat.S_ISREG(observed.st_mode)
            or (observed.st_dev, observed.st_ino)
            != (expected_stat.st_dev, expected_stat.st_ino)
            or observed.st_size != expected_stat.st_size
            or observed.st_size > max_item_bytes
        ):
            raise FinalizeFailure()
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100600 << 16
        written = 0
        with archive.open(info, mode='w', force_zip64=True) as destination:
            while True:
                chunk = os.read(file_fd, min(65536, max_item_bytes - written + 1))
                if not chunk:
                    break
                written += len(chunk)
                if written > max_item_bytes:
                    raise FinalizeFailure()
                destination.write(chunk)
        if written != observed.st_size:
            raise FinalizeFailure()
    finally:
        os.close(file_fd)


def main():
    '''功能：原子生成直接 PDF 成员 ZIP。输入：argv 路径与资源上限。输出：仅返回码。'''
    directory_fd = None
    temporary_created = False
    temporary_name = ''
    try:
        if len(sys.argv) != 8:
            raise FinalizeFailure()
        input_directory, output_path, suffix = sys.argv[1:4]
        max_items = int(sys.argv[4])
        max_item_bytes = int(sys.argv[5])
        max_total_bytes = int(sys.argv[6])
        timeout_seconds = float(sys.argv[7])
        if (
            suffix != '.pdf'
            or max_items <= 0
            or max_item_bytes <= 0
            or max_total_bytes <= 0
            or not 0.001 <= timeout_seconds <= 300.0
            or os.path.dirname(output_path) != input_directory
        ):
            raise FinalizeFailure()
        output_name = os.path.basename(output_path)
        if not safe_name(output_name) or not output_name.endswith('.zip'):
            raise FinalizeFailure()
        signal.signal(signal.SIGALRM, handle_timeout)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        directory_fd = open_directory_without_symlinks(input_directory)
        members = []
        total_bytes = 0
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                name = entry.name
                if name.startswith('.') or not name.endswith(suffix):
                    continue
                if not safe_name(name) or len(members) >= max_items:
                    raise FinalizeFailure()
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size < 0:
                    raise FinalizeFailure()
                total_bytes += metadata.st_size
                if metadata.st_size > max_item_bytes or total_bytes > max_total_bytes:
                    raise FinalizeFailure()
                members.append((name, metadata))
        if not members:
            raise FinalizeFailure()
        members.sort(key=lambda item: item[0].encode('utf-8', 'strict'))
        temporary_name = '.' + output_name + '.paraguibench.tmp'
        temporary_fd = os.open(
            temporary_name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        temporary_created = True
        with os.fdopen(temporary_fd, mode='w+b') as file_object:
            with zipfile.ZipFile(file_object, mode='w') as archive:
                for name, metadata in members:
                    write_member(
                        archive,
                        directory_fd,
                        name,
                        metadata,
                        max_item_bytes,
                    )
            file_object.flush()
            os.fsync(file_object.fileno())
        os.replace(
            temporary_name,
            output_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_created = False
        os.fsync(directory_fd)
    except Exception:
        if temporary_created and directory_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except Exception:
                pass
        raise SystemExit(97)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        if directory_fd is not None:
            os.close(directory_fd)


main()
""".strip()

_SAVE_ACTIVE_DOCUMENT_PROGRAM = r"""
import math
import os
import signal
import sys
import time


def handle_timeout(_signum, _frame):
    '''功能：在 guest 内硬中止超时保存。输入：信号参数。输出：抛出 TimeoutError。'''
    raise TimeoutError()


def main():
    '''功能：稳定已激活窗口后发送 Ctrl+S 并等待落盘。输入：前后等待及硬超时。输出：仅返回码。'''
    try:
        if len(sys.argv) != 4:
            raise ValueError()
        activation_settle_seconds = float(sys.argv[1])
        post_save_settle_seconds = float(sys.argv[2])
        timeout_seconds = float(sys.argv[3])
        if (
            not math.isfinite(activation_settle_seconds)
            or activation_settle_seconds not in {0.5, 5.0}
            or not math.isfinite(post_save_settle_seconds)
            or post_save_settle_seconds not in {0.5, 1.0}
            or not math.isfinite(timeout_seconds)
            or not 0.001 <= timeout_seconds <= 300.0
            or activation_settle_seconds + post_save_settle_seconds
            >= timeout_seconds
        ):
            raise ValueError()
        signal.signal(signal.SIGALRM, handle_timeout)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        os.environ['DISPLAY'] = ':0'
        os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'
        time.sleep(activation_settle_seconds)
        import pyautogui
        pyautogui.hotkey('ctrl', 's')
        time.sleep(post_save_settle_seconds)
    except Exception:
        raise SystemExit(97)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)


main()
""".strip()

_CALC_CSV_FILTER = (
    "csv:Text - txt - csv (StarCalc):44,34,UTF-8,,,,false,true,true,false,false,1"
)
_EXPORT_CALC_FIRST_SHEET_PROGRAM = r"""
import math
import os
import secrets
import signal
import stat
import subprocess
import sys
import time


class FinalizeFailure(Exception):
    '''表示 Calc 导出无法在固定边界内完成。'''


def handle_timeout(_signum, _frame):
    '''功能：将 guest 总时限转换为固定失败。输入：信号参数。输出：始终抛错。'''
    raise FinalizeFailure()


def open_directory_without_symlinks(path):
    '''功能：逐级 nofollow 打开输出目录。输入：POSIX 绝对路径。输出：目录 fd。'''
    if not isinstance(path, str) or not path.startswith('/') or path.endswith('/'):
        raise FinalizeFailure()
    parts = path.split('/')[1:]
    if not parts or any(part in {'', '.', '..'} for part in parts):
        raise FinalizeFailure()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current_fd = os.open('/', flags)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def safe_name(name):
    '''功能：验证单层输入或输出名。输入：文件名。输出：安全时返回 True。'''
    return (
        isinstance(name, str)
        and name not in {'', '.', '..'}
        and '/' not in name
        and '\\' not in name
        and '\x00' not in name
        and len(name.encode('utf-8', 'strict')) <= 255
        and all(character.isprintable() for character in name)
    )


def metadata_identity(metadata):
    '''功能：提取防换 inode 的稳定身份。输入：stat_result。输出：身份 tuple。'''
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def write_all(file_descriptor, payload):
    '''功能：完整写入单个内存块。输入：目标 fd 与 bytes。输出：写入字节数。'''
    view = memoryview(payload)
    written = 0
    while written < len(view):
        count = os.write(file_descriptor, view[written:])
        if count <= 0:
            raise FinalizeFailure()
        written += count
    return written


def copy_bounded(source_fd, destination_fd, max_bytes):
    '''功能：从已打开 inode 有界流式复制。输入：源/目标 fd 与上限。输出：总字节数。'''
    os.lseek(source_fd, 0, os.SEEK_SET)
    total = 0
    while True:
        chunk = os.read(source_fd, min(65536, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise FinalizeFailure()
        if write_all(destination_fd, chunk) != len(chunk):
            raise FinalizeFailure()
    os.fsync(destination_fd)
    return total


def create_private_directory(directory_fd):
    '''功能：在锚定目录创建 0700 随机私有目录。输入：目录 fd。输出：名称、fd、身份。'''
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    for _attempt in range(8):
        name = '.paraguibench-calc-' + secrets.token_hex(16) + '.tmp'
        try:
            os.mkdir(name, mode=0o700, dir_fd=directory_fd)
        except FileExistsError:
            continue
        private_fd = None
        try:
            private_fd = os.open(name, flags, dir_fd=directory_fd)
            os.fchmod(private_fd, 0o700)
            metadata = os.fstat(private_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise FinalizeFailure()
            return name, private_fd, (metadata.st_dev, metadata.st_ino)
        except Exception:
            if private_fd is not None:
                os.close(private_fd)
            try:
                os.rmdir(name, dir_fd=directory_fd)
            except Exception:
                pass
            raise
    raise FinalizeFailure()


def create_staging_file(directory_fd):
    '''功能：在目标目录创建随机 0600 staging。输入：目录 fd。输出：名称、fd。'''
    flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    for _attempt in range(8):
        name = '.paraguibench-calc-output-' + secrets.token_hex(16) + '.tmp'
        try:
            file_fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            os.close(file_fd)
            raise FinalizeFailure()
        return name, file_fd
    raise FinalizeFailure()


def private_directory_path(private_fd):
    '''功能：生成只指向持有目录 fd 的子进程路径。输入：私有目录 fd。输出：绝对路径。'''
    proc_path = f'/proc/{os.getpid()}/fd/{private_fd}'
    if os.path.isdir(proc_path):
        return proc_path
    dev_path = f'/dev/fd/{private_fd}'
    if os.path.isdir(dev_path):
        return dev_path
    raise FinalizeFailure()


def remaining_timeout(deadline, reserve_seconds):
    '''功能：计算共享 deadline 的子进程预算。输入：截止点与清理保留。输出：正秒数。'''
    remaining = deadline - time.monotonic() - reserve_seconds
    if not math.isfinite(remaining) or remaining < 0.001:
        raise FinalizeFailure()
    return remaining


def private_member_names(private_fd):
    '''功能：有界列举私有目录直接成员。输入：目录 fd。输出：名称 frozenset。'''
    names = []
    with os.scandir(private_fd) as entries:
        for entry in entries:
            if len(names) >= 3:
                raise FinalizeFailure()
            names.append(entry.name)
    return frozenset(names)


def require_input_stable(directory_fd, input_fd, input_name, identity):
    '''功能：复核持有 inode 与 canonical 名仍一致。输入：目录/文件 fd、名称、身份。输出：无。'''
    held = os.fstat(input_fd)
    named = os.stat(input_name, dir_fd=directory_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(held.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or metadata_identity(held) != identity
        or metadata_identity(named) != identity
    ):
        raise FinalizeFailure()


def require_directory_stable(directory_fd, output_directory):
    '''功能：复核 canonical 输出目录仍绑定 held fd。输入：held fd 与路径。输出：无。'''
    reopened_fd = open_directory_without_symlinks(output_directory)
    try:
        held = os.fstat(directory_fd)
        reopened = os.fstat(reopened_fd)
        if (held.st_dev, held.st_ino) != (reopened.st_dev, reopened.st_ino):
            raise FinalizeFailure()
    finally:
        os.close(reopened_fd)


def cleanup_private_members(private_fd):
    '''功能：nofollow 清理有界私有直接成员。输入：私有目录 fd。输出：成功布尔值。'''
    try:
        names = []
        with os.scandir(private_fd) as entries:
            for entry in entries:
                if len(names) >= 8:
                    return False
                names.append(entry.name)
        for name in names:
            metadata = os.stat(name, dir_fd=private_fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                os.rmdir(name, dir_fd=private_fd)
            else:
                os.unlink(name, dir_fd=private_fd)
        return True
    except Exception:
        return False


def main():
    '''功能：私有转换 Calc 快照并原子提交 CSV。输入：路径、过滤器、大小及超时。输出：仅返回码。'''
    directory_fd = None
    input_fd = None
    private_fd = None
    private_name = None
    private_identity = None
    output_fd = None
    staging_fd = None
    staging_name = None
    staging_committed = False
    operation_succeeded = False
    cleanup_succeeded = True
    try:
        if len(sys.argv) != 8:
            raise FinalizeFailure()
        input_path, output_directory, expected_path, filter_name = sys.argv[1:5]
        max_item_bytes = int(sys.argv[5])
        max_total_bytes = int(sys.argv[6])
        timeout_seconds = float(sys.argv[7])
        expected_filter = (
            'csv:Text - txt - csv (StarCalc):44,34,UTF-8,,,,'
            'false,true,true,false,false,1'
        )
        if (
            filter_name != expected_filter
            or max_item_bytes <= 0
            or max_total_bytes < max_item_bytes
            or not math.isfinite(timeout_seconds)
            or not 0.001 <= timeout_seconds <= 300.0
            or os.path.dirname(input_path) != output_directory
            or os.path.dirname(expected_path) != output_directory
        ):
            raise FinalizeFailure()
        input_name = os.path.basename(input_path)
        expected_name = os.path.basename(expected_path)
        if (
            not safe_name(input_name)
            or not safe_name(expected_name)
            or not input_name.endswith('.xlsx')
            or expected_name != input_name[:-5] + '-Sheet1.csv'
        ):
            raise FinalizeFailure()
        signal.signal(signal.SIGALRM, handle_timeout)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        directory_fd = open_directory_without_symlinks(output_directory)
        input_fd = os.open(
            input_name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        input_stat = os.fstat(input_fd)
        if (
            not stat.S_ISREG(input_stat.st_mode)
            or input_stat.st_size <= 0
            or input_stat.st_size > max_item_bytes
        ):
            raise FinalizeFailure()
        input_identity = metadata_identity(input_stat)
        private_name, private_fd, private_identity = create_private_directory(
            directory_fd
        )
        private_input_fd = os.open(
            input_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=private_fd,
        )
        try:
            copied_input_bytes = copy_bounded(
                input_fd,
                private_input_fd,
                max_item_bytes,
            )
        finally:
            os.close(private_input_fd)
        if copied_input_bytes != input_stat.st_size:
            raise FinalizeFailure()
        require_input_stable(directory_fd, input_fd, input_name, input_identity)
        private_path = private_directory_path(private_fd)
        environment = dict(os.environ)
        environment['DISPLAY'] = ':0'
        environment['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'
        result = subprocess.run(
            [
                'libreoffice',
                '--convert-to',
                filter_name,
                '--outdir',
                private_path,
                private_path + '/' + input_name,
            ],
            check=False,
            env=environment,
            pass_fds=(private_fd,),
            shell=False,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            timeout=remaining_timeout(deadline, 0.5),
        )
        if result.returncode != 0:
            raise FinalizeFailure()
        if private_member_names(private_fd) != frozenset(
            {input_name, expected_name}
        ):
            raise FinalizeFailure()
        private_input_stat = os.stat(
            input_name,
            dir_fd=private_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(private_input_stat.st_mode)
            or private_input_stat.st_size != copied_input_bytes
        ):
            raise FinalizeFailure()
        output_fd = os.open(
            expected_name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=private_fd,
        )
        output_stat = os.fstat(output_fd)
        if (
            not stat.S_ISREG(output_stat.st_mode)
            or output_stat.st_size <= 0
            or output_stat.st_size > max_item_bytes
            or copied_input_bytes + output_stat.st_size > max_total_bytes
        ):
            raise FinalizeFailure()
        require_input_stable(directory_fd, input_fd, input_name, input_identity)
        require_directory_stable(directory_fd, output_directory)
        staging_name, staging_fd = create_staging_file(directory_fd)
        copied_output_bytes = copy_bounded(
            output_fd,
            staging_fd,
            max_item_bytes,
        )
        if copied_output_bytes != output_stat.st_size:
            raise FinalizeFailure()
        if metadata_identity(os.fstat(output_fd)) != metadata_identity(output_stat):
            raise FinalizeFailure()
        if private_member_names(private_fd) != frozenset(
            {input_name, expected_name}
        ):
            raise FinalizeFailure()
        require_input_stable(directory_fd, input_fd, input_name, input_identity)
        require_directory_stable(directory_fd, output_directory)
        staging_stat = os.fstat(staging_fd)
        if (
            not stat.S_ISREG(staging_stat.st_mode)
            or staging_stat.st_nlink != 1
            or staging_stat.st_size != copied_output_bytes
        ):
            raise FinalizeFailure()
        os.replace(
            staging_name,
            expected_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        staging_committed = True
        committed = os.stat(
            expected_name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(committed.st_mode)
            or (committed.st_dev, committed.st_ino)
            != (staging_stat.st_dev, staging_stat.st_ino)
            or committed.st_size != staging_stat.st_size
        ):
            raise FinalizeFailure()
        os.fsync(directory_fd)
        operation_succeeded = True
    except Exception:
        operation_succeeded = False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        if staging_fd is not None:
            try:
                staging_identity = os.fstat(staging_fd)
                if not staging_committed and staging_name is not None:
                    named_staging = os.stat(
                        staging_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (named_staging.st_dev, named_staging.st_ino) == (
                        staging_identity.st_dev,
                        staging_identity.st_ino,
                    ):
                        os.unlink(staging_name, dir_fd=directory_fd)
            except Exception:
                cleanup_succeeded = False
            os.close(staging_fd)
        if output_fd is not None:
            os.close(output_fd)
        if input_fd is not None:
            os.close(input_fd)
        if private_fd is not None:
            cleanup_succeeded = (
                cleanup_private_members(private_fd) and cleanup_succeeded
            )
            os.close(private_fd)
            if private_name is not None and private_identity is not None:
                try:
                    named_private = os.stat(
                        private_name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                    if (
                        stat.S_ISDIR(named_private.st_mode)
                        and (named_private.st_dev, named_private.st_ino)
                        == private_identity
                    ):
                        os.rmdir(private_name, dir_fd=directory_fd)
                    else:
                        cleanup_succeeded = False
                except Exception:
                    cleanup_succeeded = False
        if directory_fd is not None:
            os.close(directory_fd)
    if not operation_succeeded or not cleanup_succeeded:
        raise SystemExit(97)


main()
""".strip()


class OSWorldArtifactFinalizer:
    """从固定 spec 注册表执行 shell-free artifact 收尾动作。"""

    def finalize(
        self,
        task_id: str,
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> bool:
        """对单个 canonical task 执行已版本化的收尾动作。

        输入参数：
            task_id：canonical 任务身份。
            controller：实现 ``execute_with_timeout`` 的受控 guest 边界。
            guest_shared_dir：prepare 阶段冻结的 ``.../shared`` 绝对路径。
        输出返回值：
            catalog 任务完成收尾返回 ``True``；非 catalog 任务返回
            ``False`` 且不发生 I/O。
        异常：
            OSWorldArtifactFinalizerError：身份、路径、controller 或动作失败。
        """

        if (
            not isinstance(task_id, str)
            or task_id not in OSWORLD_ARTIFACT_FINALIZER_TASK_IDS
        ):
            return False
        spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]
        _validate_spec_identity(spec)
        handler = _FINALIZE_ACTION_REGISTRY.get(spec.finalize_action_id)
        if handler is None:
            raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_ACTION_BLOCKED")
        if spec.finalize_action_id == "none":
            return True
        execute = getattr(controller, "execute_with_timeout", None)
        if not callable(execute):
            raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_CONTROLLER_ERROR")
        guest_home = _guest_home_from_shared(guest_shared_dir)
        try:
            options = json.loads(spec.finalize_options_json)
            handler(spec, options, guest_home, execute)
        except OSWorldArtifactFinalizerError:
            raise
        except Exception:
            raise OSWorldArtifactFinalizerError(
                "ARTIFACT_FINALIZE_ACTION_ERROR"
            ) from None
        return True


def _validate_spec_identity(spec: ArtifactEvidenceSpec) -> None:
    """复核 evidence spec 的 canonical 摘要。

    输入参数：
        spec：由固定 catalog 命中的收尾规格。
    输出返回值：
        无；摘要精确匹配时返回。
    异常：
        OSWorldArtifactFinalizerError：规格 schema 或摘要漂移。
    """

    try:
        canonical = canonical_artifact_evidence_spec_json(spec)
    except Exception:
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_SPEC_ERROR") from None
    observed = hashlib.sha256(canonical.encode("utf-8", "strict")).hexdigest()
    if observed != spec.evidence_spec_sha256:
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_SPEC_ERROR")


def _guest_home_from_shared(value: str | None) -> PurePosixPath:
    """从冻结 shared locator 安全还原 guest home。

    输入参数：
        value：末段严格为 ``shared`` 的规范 POSIX 绝对路径。
    输出返回值：
        shared 的非根父目录。
    异常：
        OSWorldArtifactFinalizerError：路径不规范或可逃逸。
    """

    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or value.endswith("/")
    ):
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_PATH_ERROR")
    shared = PurePosixPath(value)
    home = shared.parent
    if (
        not shared.is_absolute()
        or ".." in shared.parts
        or shared.name != "shared"
        or home == PurePosixPath("/")
        or str(shared) != value
    ):
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_PATH_ERROR")
    return home


def _finalize_none(
    _spec: ArtifactEvidenceSpec,
    _options: dict[str, Any],
    _guest_home: PurePosixPath,
    _execute: Callable[..., Any],
) -> None:
    """表示该任务无收尾副作用。

    输入参数：
        全部参数仅用于统一 handler 签名，不读取。
    输出返回值：
        无。
    """


def _finalize_archive_pdf_directory(
    spec: ArtifactEvidenceSpec,
    options: dict[str, Any],
    guest_home: PurePosixPath,
    execute: Callable[..., Any],
) -> None:
    """以固定 guest helper 将直接 PDF 成员原子归档。

    输入参数：
        spec：提供已冻结的数量、字节和超时上限。
        options：已由 spec validator 固定的输入目录、后缀与输出。
        guest_home：同一 Attempt 冻结的 guest home。
        execute：仅接受 shell-free argv 与超时的 controller 边界。
    输出返回值：
        无；guest helper 零返回码表示归档已原子完成。
    异常：
        OSWorldArtifactFinalizerError：超时无效、返回 schema 异常或命令失败。
    """

    timeout = _validated_timeout(spec.limits.finalize_timeout_seconds)
    input_path = guest_home / options["input_directory_relative_path"]
    output_path = guest_home / options["output_relative_path"]
    result = execute(
        [
            "python3",
            "-I",
            "-c",
            _ARCHIVE_PDF_DIRECTORY_PROGRAM,
            str(input_path),
            str(output_path),
            options["member_suffix"],
            str(spec.limits.max_items),
            str(spec.limits.max_single_item_bytes),
            str(spec.limits.max_total_bytes),
            str(max(0.001, timeout - 1.0)),
        ],
        timeout_seconds=timeout,
    )
    _require_zero_result(result)


def _finalize_save_active_document(
    spec: ArtifactEvidenceSpec,
    options: dict[str, Any],
    _guest_home: PurePosixPath,
    execute: Callable[..., Any],
) -> None:
    """严格激活固定 LibreOffice 窗口后发送保存快捷键。

    输入参数：
        spec：提供整个收尾动作的硬超时。
        options：已固定应用家族、大小写敏感的完整窗口标题，以及
            发送保存键前后的有界稳定等待。
        _guest_home：统一 handler 签名，本动作不使用路径。
        execute：带调用级超时的 shell-free controller 边界。
    输出返回值：
        无；严格激活与 Ctrl+S helper 均零返回码时完成。
    异常：
        OSWorldArtifactFinalizerError：窗口不存在、超时或保存失败。
    """

    timeout = _validated_timeout(spec.limits.finalize_timeout_seconds)
    deadline = time.monotonic() + timeout
    activation_timeout = _remaining_timeout(deadline)
    activation_result = execute(
        [
            "env",
            "DISPLAY=:0",
            "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
            "wmctrl",
            "-Fa",
            options["strict_window_title"],
        ],
        timeout_seconds=activation_timeout,
    )
    _require_zero_result(activation_result)
    save_timeout = _remaining_timeout(deadline)
    save_result = execute(
        [
            "python3",
            "-I",
            "-c",
            _SAVE_ACTIVE_DOCUMENT_PROGRAM,
            str(options["activation_settle_seconds"]),
            str(options["post_save_settle_seconds"]),
            str(max(0.001, save_timeout - 1.0)),
        ],
        timeout_seconds=save_timeout,
    )
    _require_zero_result(save_result)


def _finalize_export_calc_first_sheet(
    spec: ArtifactEvidenceSpec,
    options: dict[str, Any],
    guest_home: PurePosixPath,
    execute: Callable[..., Any],
) -> None:
    """以源 OSWorld 固定过滤器导出 Calc 首张表旁挂 CSV。

    输入参数：
        spec：提供旁挂路径闭集、字节上限和超时。
        options：固定的 workbook 与输出目录相对路径。
        guest_home：同一 Attempt 的冻结 guest home。
        execute：带超时的 shell-free controller 边界。
    输出返回值：
        无；私有转换产物经复核并原子替换为新 CSV 后完成。
    异常：
        OSWorldArtifactFinalizerError：spec 闭集漂移、超时或导出失败。
    """

    input_relative_path = options["input_relative_path"]
    output_directory_relative_path = options["output_directory_relative_path"]
    matching_slots = tuple(
        slot
        for slot in spec.artifact_slots
        if slot.getter_kind == "file-bundle"
        and len(slot.locator_relative_paths) == 2
        and slot.locator_relative_paths[0] == input_relative_path
    )
    if len(matching_slots) != 1:
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_SPEC_ERROR")
    expected_relative_path = matching_slots[0].locator_relative_paths[1]
    if PurePosixPath(expected_relative_path).parent != PurePosixPath(
        output_directory_relative_path
    ):
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_SPEC_ERROR")
    timeout = _validated_timeout(spec.limits.finalize_timeout_seconds)
    result = execute(
        [
            "python3",
            "-I",
            "-c",
            _EXPORT_CALC_FIRST_SHEET_PROGRAM,
            str(guest_home / input_relative_path),
            str(guest_home / output_directory_relative_path),
            str(guest_home / expected_relative_path),
            _CALC_CSV_FILTER,
            str(spec.limits.max_single_item_bytes),
            str(spec.limits.max_total_bytes),
            str(max(0.001, timeout - 1.0)),
        ],
        timeout_seconds=timeout,
    )
    _require_zero_result(result)


def _validated_timeout(value: object) -> float:
    """验证收尾超时值可供 host 和 guest 双重计时。

    输入参数：
        value：spec 中的超时秒数。
    输出返回值：
        范围 ``(0, 300]`` 内的有限 float。
    异常：
        OSWorldArtifactFinalizerError：值为 bool、非数值、非有限或越界。
    """

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 < float(value) <= 300.0
    ):
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_TIMEOUT_ERROR")
    return float(value)


def _remaining_timeout(deadline: float) -> float:
    """计算多步收尾动作共享的剩余时间。

    输入参数：
        deadline：基于 ``time.monotonic`` 的绝对截止点。
    输出返回值：
        严格大于 1 毫秒的剩余秒数。
    异常：
        OSWorldArtifactFinalizerError：全局 finalize budget 已耗尽。
    """

    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining < 0.001:
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_TIMEOUT_ERROR")
    return remaining


def _require_zero_result(result: object) -> None:
    """验证 guest argv 返回结果的最小成功 schema。

    输入参数：
        result：controller 返回的结构化命令结果。
    输出返回值：
        无；返回码严格为整数零且输出字段为字符串时返回。
    异常：
        OSWorldArtifactFinalizerError：命令失败或结果 schema 无效。
    """

    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    if (
        isinstance(returncode, bool)
        or not isinstance(returncode, int)
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
    ):
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_RESULT_ERROR")
    if returncode != 0:
        raise OSWorldArtifactFinalizerError("ARTIFACT_FINALIZE_ACTION_ERROR")


_FINALIZE_ACTION_REGISTRY: MappingProxyType[
    str,
    Callable[
        [ArtifactEvidenceSpec, dict[str, Any], PurePosixPath, Callable[..., Any]],
        None,
    ],
] = MappingProxyType(
    {
        "none": _finalize_none,
        "archive-pdf-directory": _finalize_archive_pdf_directory,
        "save-active-libreoffice-document": _finalize_save_active_document,
        "export-calc-first-sheet-csv": _finalize_export_calc_first_sheet,
    }
)


__all__ = [
    "ARTIFACT_FINALIZER_SCHEMA_ID",
    "OSWORLD_ARTIFACT_FINALIZER_ACTIONS",
    "OSWORLD_ARTIFACT_FINALIZER_TASK_IDS",
    "OSWorldArtifactFinalizer",
    "OSWorldArtifactFinalizerError",
]
