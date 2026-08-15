"""ReadonlyPPT-002/-003 固定来源目录与锁文件排除合同。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat


PRESENTATION_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


class ReadonlyPPTSourceError(RuntimeError):
    """表示固定 Lee 来源目录或成员身份不符合严格闭集。"""


@dataclass(frozen=True, slots=True)
class ReadonlyPPTSourceMember:
    """描述一个固定来源目录成员的不可变物理身份。

    输入参数：path 为目录内唯一文件名；size 为精确字节数；sha256 为
        小写完整摘要；media_type 仅对可交付正式文件非空。
    输出返回值：供 source verifier 与 manifest generator 共享同一事实合同。
    """

    path: str
    size: int
    sha256: str
    media_type: str | None


@dataclass(frozen=True, slots=True)
class VerifiedReadonlyPPTSource:
    """保存通过严格两成员核验后的安全公开投影。

    输入参数：task_id/task_uid 固定 canonical 来源身份；deliverable_members
        只允许包含正式 PPTX，不包含已核验的 Office 锁文件。
    输出返回值：generator 可直接消费的不可变正式文件闭集。
    """

    task_id: str
    task_uid: str
    deliverable_members: tuple[ReadonlyPPTSourceMember, ...]


_PRESENTATION = ReadonlyPPTSourceMember(
    path="mechine learning.pptx",
    size=97_411,
    sha256="fb688cacaf7bbb1227447fe5e43eeed6c0783d378ca1184d09c3015e5f08f264",
    media_type=PRESENTATION_MEDIA_TYPE,
)
_LOCK_FILE = ReadonlyPPTSourceMember(
    path="~$mechine learning.pptx",
    size=165,
    sha256="6907f9789ec20d0aee0f01875ab9aa54cf6ac1ead16e179da9de4bb7c54dd18e",
    media_type=None,
)
_TASK_UIDS = {
    "InformationRetrieval-FileSearch-ReadonlyPPT-002": (
        "c65ead66-0dca-40e9-993f-affc35bde5bc"
    ),
    "InformationRetrieval-FileSearch-ReadonlyPPT-003": (
        "163c86bd-de63-4311-8da5-ff750e8f7961"
    ),
}
_SOURCE_MEMBERS = (_PRESENTATION, _LOCK_FILE)


def readonly_ppt_task_assets(
    task_id: str,
) -> tuple[str, tuple[ReadonlyPPTSourceMember, ...]]:
    """返回一个受支持任务的固定 UID 与可交付文件闭集。

    输入参数：task_id 必须精确属于 ReadonlyPPT-002/-003。
    输出返回值：canonical task UID 与只含正式 PPTX 的单元素 tuple。
    异常：ReadonlyPPTSourceError：任务身份不在固定闭集。
    """

    task_uid = _TASK_UIDS.get(task_id)
    if task_uid is None:
        raise ReadonlyPPTSourceError("ReadonlyPPT source task identity 无效")
    return task_uid, (_PRESENTATION,)


def verify_readonly_ppt_source_directory(
    task_id: str,
    source_directory: Path,
) -> VerifiedReadonlyPPTSource:
    """核验一个固定 Lee 目录并只投影正式 PPTX。

    输入参数：task_id 固定任务身份；source_directory 为该任务 UID 对应的
        Lee revision 目录，必须恰好包含正式 PPTX 与唯一已知锁文件。
    输出返回值：只含正式 PPTX 的不可变公开投影；锁文件不进入返回值。
    异常：ReadonlyPPTSourceError：目录、成员闭集、普通文件身份、大小、
        SHA-256 或读取稳定性任一漂移。
    """

    task_uid, deliverable_members = readonly_ppt_task_assets(task_id)
    if not isinstance(source_directory, Path) or not source_directory.is_absolute():
        raise ReadonlyPPTSourceError("ReadonlyPPT source directory 无效")
    directory_descriptors: list[int] = []
    member_descriptors: list[tuple[int, ReadonlyPPTSourceMember, tuple[int, ...]]] = []
    try:
        directory_flags = os.O_RDONLY
        for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
            directory_flags |= getattr(os, name, 0)
        if getattr(os, "O_DIRECTORY", 0) == 0 or getattr(os, "O_NOFOLLOW", 0) == 0:
            raise OSError
        absolute = Path(os.path.abspath(os.fspath(source_directory)))
        if absolute.name != task_uid:
            raise OSError
        directory_descriptors.append(os.open(absolute.anchor, directory_flags))
        for part in absolute.parts[1:]:
            directory_descriptors.append(
                os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_descriptors[-1],
                )
            )
        directory_descriptor = directory_descriptors[-1]
        directory_before = os.fstat(directory_descriptor)
        if not stat.S_ISDIR(directory_before.st_mode):
            raise OSError
        names_before = frozenset(os.listdir(directory_descriptor))
        expected_names = {member.path for member in _SOURCE_MEMBERS}
        if names_before != expected_names:
            raise OSError
        for contract in _SOURCE_MEMBERS:
            descriptor, identity = _verify_source_member(
                directory_descriptor,
                contract,
            )
            member_descriptors.append((descriptor, contract, identity))
        for descriptor, contract, identity in member_descriptors:
            descriptor_after = os.fstat(descriptor)
            path_after = os.stat(
                contract.path,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                _file_identity(descriptor_after) != identity
                or _file_identity(path_after) != identity
            ):
                raise OSError
        directory_after = os.fstat(directory_descriptor)
        names_after = frozenset(os.listdir(directory_descriptor))
        if names_after != names_before or _directory_identity(
            directory_after
        ) != _directory_identity(directory_before):
            raise OSError
    except (OSError, RuntimeError, ValueError):
        raise ReadonlyPPTSourceError("ReadonlyPPT source closure 无效") from None
    finally:
        for descriptor, _contract, _identity in reversed(member_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in reversed(directory_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return VerifiedReadonlyPPTSource(
        task_id=task_id,
        task_uid=task_uid,
        deliverable_members=deliverable_members,
    )


def _verify_source_member(
    directory_descriptor: int,
    contract: ReadonlyPPTSourceMember,
) -> tuple[int, tuple[int, ...]]:
    """通过同一 held 目录与文件 FD 核验一个固定成员。

    输入参数：directory_descriptor 为任务目录 FD；contract 固定唯一文件名、
        大小与摘要。
    输出返回值：摘要匹配后仍保持打开的文件 FD 与读取前身份；
        调用方将 FD 持有到整个两成员闭包复核结束。
    异常：OSError：路径成员、类型、链接数、大小、读取或摘要漂移。
    """

    path_status = os.stat(
        contract.path,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISREG(path_status.st_mode)
        or path_status.st_nlink != 1
        or path_status.st_size != contract.size
    ):
        raise OSError
    file_flags = os.O_RDONLY
    for name in ("O_CLOEXEC", "O_NOFOLLOW"):
        file_flags |= getattr(os, name, 0)
    descriptor = os.open(
        contract.path,
        file_flags,
        dir_fd=directory_descriptor,
    )
    try:
        before = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(path_status):
            raise OSError
        digest = hashlib.sha256()
        remaining = contract.size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise OSError
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError
        after = os.fstat(descriptor)
        if (
            _file_identity(after) != _file_identity(before)
            or digest.hexdigest() != contract.sha256
        ):
            raise OSError
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _file_identity(before)


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """投影来源文件读取前后的稳定 inode 身份。

    输入参数：metadata 为 ``Path.lstat`` 返回的 stat 结果。
    输出返回值：设备、inode、mode、链接数、大小、mtime/ctime 纳秒元组。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """投影 held 来源目录的稳定物理身份。

    输入参数：metadata 为同一目录 FD 枚举前后的 ``fstat`` 结果。
    输出返回值：设备、inode、mode、链接数与 mtime/ctime 纳秒元组。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = [
    "PRESENTATION_MEDIA_TYPE",
    "ReadonlyPPTSourceError",
    "ReadonlyPPTSourceMember",
    "VerifiedReadonlyPPTSource",
    "readonly_ppt_task_assets",
    "verify_readonly_ppt_source_directory",
]
