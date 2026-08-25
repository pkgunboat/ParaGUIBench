#!/usr/bin/env python3
"""Build and verify a deterministic ParaGUIBench cleanroom source bundle.

The builder intentionally works from the current Git working tree, including
non-ignored untracked migration sources.  It never reads process environment
values and only admits a narrow public-source allowlist after the repository
security scanner has passed.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import re
import runpy
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator, Optional, Sequence


SCHEMA_VERSION = "paraguibench.release-bundle.v1"
ARCHIVE_ROOT = "ParaGUIBench"
FIXED_FILE_MODE = 0o644
DEFAULT_MAX_FILES = 5_000
DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_BYTES = 384 * 1024 * 1024
DEFAULT_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_ENV_EXAMPLE_BYTES = 64 * 1024
ENV_EXAMPLE_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=\Z")

ROOT_FILE_ALLOWLIST = frozenset(
    {
        ".env.example",
        ".gitignore",
        "CHANGELOG.md",
        "CITATION.cff",
        "CONTEXT.md",
        "CONTRIBUTING.md",
        "INSTALL.md",
        "LICENSE",
        "NOTICE",
        "README.md",
        "README_zh-CN.md",
        "SECURITY.md",
        "pyproject.toml",
    }
)
PUBLIC_SOURCE_ROOTS = (
    ".github",
    "benchmark",
    "configs",
    "deploy",
    "docs",
    "environments",
    "scripts",
    "src",
    "tests",
    "website",
)
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".artifacts",
        ".cache",
        ".logs",
        ".mypy_cache",
        ".pytest_cache",
        ".results",
        ".ruff_cache",
        ".runs",
        ".tox",
        ".zcode",
        "__pycache__",
        "artifacts",
        "build",
        "cache",
        "caches",
        "dist",
        "htmlcov",
        "log",
        "logs",
        "node_modules",
        "output",
        "outputs",
        "results",
        "run",
        "run-logs",
        "run_logs",
        "runs",
        # methods_runner 在运行时创建的未入库软链别名（见 docs/methods-provenance.md）
        "tasks_list",
    }
)
FORBIDDEN_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".bz2",
        ".gz",
        ".img",
        ".iso",
        ".key",
        ".ova",
        ".p12",
        ".pem",
        ".pfx",
        ".qcow2",
        ".tar",
        ".tgz",
        ".vdi",
        ".vhd",
        ".vhdx",
        ".vmdk",
        ".xz",
        ".zip",
    }
)
TEMPORARY_SUFFIXES = frozenset(
    {
        ".bak",
        ".backup",
        ".crdownload",
        ".download",
        ".orig",
        ".part",
        ".rej",
        ".swp",
        ".swo",
        ".temp",
        ".tmp",
    }
)
SENSITIVE_DATA_BASENAMES = frozenset(
    {
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "service-account.json",
    }
)
SENSITIVE_PATH_TOKEN = re.compile(
    r"(?i)(?:"
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r")"
)
SAFE_BUNDLE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")


class ReleaseBundleError(RuntimeError):
    """功能：表示 release bundle 构建或验证的脱敏失败。

    输入参数：使用 ``RuntimeError`` 的标准消息参数。
    输出返回值：无；该异常不应包含凭据、文件内容或绝对路径。
    """


@dataclass(frozen=True)
class BundleLimits:
    """功能：定义构建与验证共用的资源上限。

    输入参数：文件数、单文件、总字节、归档和清单的最大值。
    输出返回值：不可变的限额配置。
    """

    max_files: int = DEFAULT_MAX_FILES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES


DEFAULT_LIMITS = BundleLimits()


@dataclass(frozen=True)
class SourceFile:
    """功能：保存已从工作树一次性冻结的公开源文件。

    输入参数：``path`` 为 POSIX 相对路径，``payload`` 为文件字节，
    ``sha256`` 为对应的小写十六进制摘要。
    输出返回值：不可变的源文件快照。
    """

    path: str
    payload: bytes
    sha256: str


@dataclass(frozen=True)
class ReleaseArtifacts:
    """功能：描述构建成功后的本地三件套。

    输入参数：归档、JSON 清单、SHA256 sidecar 路径及摘要统计。
    输出返回值：不可变的构建结果。
    """

    archive_path: Path
    manifest_path: Path
    checksum_path: Path
    archive_sha256: str
    source_tree_sha256: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class BundleVerification:
    """功能：保存离线验证成功后的可审计结果。

    输入参数：归档摘要、源树摘要、文件数和总字节。
    输出返回值：不可变的验证结果。
    """

    archive_sha256: str
    source_tree_sha256: str
    file_count: int
    total_bytes: int


def _validate_limits(limits: BundleLimits) -> None:
    """功能：确认所有双向资源门禁均为正整数且逻辑一致。

    输入参数：``limits`` 为构建和验证共用的 ``BundleLimits``。
    输出返回值：无；布尔值、浮点值、非正值或单文件上限大于
    总字节上限时抛出 ``ReleaseBundleError``。
    """

    values = (
        limits.max_files,
        limits.max_file_bytes,
        limits.max_total_bytes,
        limits.max_archive_bytes,
        limits.max_manifest_bytes,
    )
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in values
    ):
        raise ReleaseBundleError("release 资源上限必须为正整数。")
    if limits.max_file_bytes > limits.max_total_bytes:
        raise ReleaseBundleError("release 单文件上限不得大于总字节上限。")


def _fixed_subprocess_environment() -> dict[str, str]:
    """功能：创建不含宿主凭据值的子进程环境。

    输入参数：无。
    输出返回值：仅含固定 PATH、locale 和 Python 安全开关的字典；
    该函数不读取 ``os.environ``。
    """

    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }


def _sha256_bytes(payload: bytes) -> str:
    """功能：计算字节串的 SHA-256 摘要。

    输入参数：``payload`` 为待摘要字节。
    输出返回值：64 位小写十六进制字符串。
    """

    return hashlib.sha256(payload).hexdigest()


def _require_regular_nofollow(path: Path, *, purpose: str) -> os.stat_result:
    """功能：使用 ``lstat`` 确认输入产物是普通文件而非符号链接。

    输入参数：``path`` 为待验证路径，``purpose`` 为不含路径值的产物类别。
    输出返回值：不跟随链接获取的 ``stat_result``。
    """

    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise ReleaseBundleError(f"{purpose}缺失或无法读取。") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise ReleaseBundleError(f"{purpose}不得是符号链接。")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ReleaseBundleError(f"{purpose}必须是普通文件。")
    return file_stat


@contextlib.contextmanager
def _open_regular_nofollow(
    path: Path,
    *,
    purpose: str,
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    """功能：不跟随最终组件地打开 release 普通文件并锁定同一 inode。

    输入参数：``path`` 为产物路径，``purpose`` 为不含路径值的类别名。
    输出返回值：上下文中产出 ``(二进制句柄, 初始 stat)``，退出时关闭句柄。
    """

    path_stat = _require_regular_nofollow(path, purpose=purpose)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseBundleError(f"{purpose}在打开前发生替换或无法读取。") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or (
            path_stat.st_dev,
            path_stat.st_ino,
        ) != (opened_stat.st_dev, opened_stat.st_ino):
            raise ReleaseBundleError(f"{purpose}在打开前发生替换。")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            yield handle, opened_stat
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _stable_file_identity(
    file_stat: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    """功能：提取能检测读取期间文件替换或改写的稳定属性。

    输入参数：``file_stat`` 为已打开文件描述符的 stat 结果。
    输出返回值：设备、inode、mode、大小、mtime_ns 与 ctime_ns 元组。
    """

    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _read_regular_file_bounded(path: Path, *, max_bytes: int, purpose: str) -> bytes:
    """功能：从 nofollow 普通文件有界读取并复核读取期间稳定性。

    输入参数：``path`` 为输入，``max_bytes`` 为硬上限，``purpose`` 为脱敏类别名。
    输出返回值：不超过上限且从稳定 inode 读得的字节。
    """

    with _open_regular_nofollow(path, purpose=purpose) as (handle, initial_stat):
        if initial_stat.st_size > max_bytes:
            raise ReleaseBundleError(f"{purpose}超出大小上限。")
        payload = handle.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ReleaseBundleError(f"{purpose}超出大小上限。")
        final_stat = os.fstat(handle.fileno())
        if len(payload) != final_stat.st_size or _stable_file_identity(
            initial_stat
        ) != _stable_file_identity(final_stat):
            raise ReleaseBundleError(f"{purpose}在读取期间发生改写。")
        return payload


def _sha256_file(path: Path, *, max_bytes: int) -> tuple[str, int]:
    """功能：在大小门禁内流式计算普通文件 SHA-256。

    输入参数：``path`` 为文件，``max_bytes`` 为允许读取的最大字节。
    输出返回值：``(sha256, byte_count)`` 二元组。
    """

    digest = hashlib.sha256()
    byte_count = 0
    with _open_regular_nofollow(path, purpose="release 产物") as (
        handle,
        initial_stat,
    ):
        if initial_stat.st_size > max_bytes:
            raise ReleaseBundleError("release 产物大小超出门禁。")
        while chunk := handle.read(min(1024 * 1024, max_bytes + 1 - byte_count)):
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise ReleaseBundleError("release 产物大小超出门禁。")
            digest.update(chunk)
        final_stat = os.fstat(handle.fileno())
        if byte_count != final_stat.st_size or _stable_file_identity(
            initial_stat
        ) != _stable_file_identity(final_stat):
            raise ReleaseBundleError("release 产物在读取期间发生改写。")
    return digest.hexdigest(), byte_count


def _validate_relative_path(path: str) -> PurePosixPath:
    """功能：验证并解析一个严格 POSIX 仓库相对路径。

    输入参数：``path`` 为 Git、manifest 或 tar 提供的路径字符串。
    输出返回值：通过绝对路径、``..``、反斜杠和控制字符门禁的
    ``PurePosixPath``。
    """

    if not isinstance(path, str) or not path or "\\" in path:
        raise ReleaseBundleError("release 包含非法相对路径。")
    if any(unicodedata.category(character) == "Cc" for character in path):
        raise ReleaseBundleError("release 包含非法相对路径。")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or path != parsed.as_posix():
        raise ReleaseBundleError("release 包含非法相对路径。")
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise ReleaseBundleError("release 包含非法相对路径。")
    return parsed


def _is_explicitly_excluded(path: PurePosixPath) -> bool:
    """功能：判断路径是否属于凭据、运行状态、镜像或临时产物。

    输入参数：``path`` 为已验证的相对路径。
    输出返回值：必须排除时返回 ``True``，否则返回 ``False``。
    """

    if path.as_posix() == ".env.example":
        return False
    folded_parts = tuple(part.casefold() for part in path.parts)
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in folded_parts[:-1]):
        return True
    if any(
        part.startswith(".venv") or part.startswith(".env") for part in folded_parts
    ):
        return True
    filename = folded_parts[-1]
    if (
        filename in {".ds_store", "thumbs.db"}
        or filename.startswith((".#", "._"))
        or filename.endswith("~")
    ):
        return True
    suffix = PurePosixPath(filename).suffix.casefold()
    if (
        suffix in FORBIDDEN_BINARY_SUFFIXES
        or suffix in TEMPORARY_SUFFIXES
        or filename in SENSITIVE_DATA_BASENAMES
        or SENSITIVE_PATH_TOKEN.search(path.as_posix()) is not None
    ):
        return True
    return False


def _is_allowlisted_source(path: PurePosixPath) -> bool:
    """功能：判断路径是否属于 cleanroom 所需的公开源码类别。

    输入参数：``path`` 为已验证且未命中拒绝规则的相对路径。
    输出返回值：只有源码、测试、任务/清单、部署配置、文档或
    网站源文件返回 ``True``。
    """

    relative = path.as_posix()
    if len(path.parts) == 1:
        return relative in ROOT_FILE_ALLOWLIST
    # 方法系统（vendored 原项目代码与入口）不属于公开 cleanroom 闭集；
    # 其运行依赖内网环境事实，见 docs/methods-provenance.md。
    # deploy/methods-services/ 是同一方法区的验证服务栈（含打包者环境
    # 地址的迁移文档与原版服务代码），同样留在闭集之外。
    if relative.startswith(
        (
            "src/paraguibench/methods_runner/",
            "tests/methods/",
            "deploy/methods-services/",
        )
    ):
        return False
    suffix = path.suffix.casefold()
    if relative.startswith(".github/workflows/"):
        return suffix in {".yaml", ".yml"}
    if relative.startswith("src/paraguibench/"):
        return suffix == ".py" or path.name == "py.typed"
    if relative.startswith("scripts/"):
        return suffix in {".mjs", ".py", ".sh"}
    if relative.startswith("tests/"):
        return suffix in {
            ".html",
            ".js",
            ".json",
            ".jsonl",
            ".md",
            ".py",
            ".txt",
            ".yaml",
            ".yml",
        }
    if relative.startswith("benchmark/tasks/"):
        return suffix == ".json"
    if relative.startswith("benchmark/manifests/"):
        return suffix == ".json"
    if relative.startswith("benchmark/schemas/"):
        return suffix == ".json"
    if relative.startswith("benchmark/provenance/"):
        return suffix in {".json", ".jsonl", ".md"}
    if relative.startswith("benchmark/assets/manifests/"):
        return suffix == ".json"
    if relative.startswith("benchmark/gold/manifests/"):
        return suffix == ".json"
    if relative.startswith("benchmark/fixtures/"):
        return suffix == ".json"
    if relative.startswith("configs/examples/"):
        return suffix in {".json", ".md", ".toml", ".yaml", ".yml"}
    if relative.startswith("docs/"):
        return suffix in {".json", ".jsonl", ".md"}
    if relative.startswith("environments/"):
        return suffix in {".json", ".md", ".php"}
    if relative.startswith("deploy/"):
        return suffix in {
            ".conf",
            ".example",
            ".json",
            ".md",
            ".php",
            ".service",
            ".sh",
            ".yaml",
            ".yml",
        }
    if relative.startswith("website/"):
        if len(path.parts) == 2:
            return path.name in {
                "DESIGN.md",
                "README.md",
                "index.html",
                "package-lock.json",
                "package.json",
                "vite.config.js",
            }
        if path.parts[1] not in {"public", "scripts", "src"}:
            return False
        return suffix in {
            ".css",
            ".html",
            ".ico",
            ".js",
            ".json",
            ".jsx",
            ".md",
            ".mjs",
            ".png",
            ".svg",
            ".webp",
        }
    return False


def _run_security_scan(repo_root: Path) -> None:
    """功能：在枚举打包文件前执行仓库脱敏安全扫描。

    输入参数：``repo_root`` 为候选 Git 工作树根目录。
    输出返回值：无；扫描脚本缺失、超时或非零退出时抛出
    ``ReleaseBundleError``，且不回显子进程输出。
    """

    scanner = repo_root / "scripts" / "security" / "scan_repository.py"
    if not scanner.is_file() or scanner.is_symlink():
        raise ReleaseBundleError("仓库安全扫描器缺失或类型非法。")
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(scanner), "--root", str(repo_root)],
            cwd=repo_root,
            env=_fixed_subprocess_environment(),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseBundleError("仓库安全扫描未能完成。") from exc
    if completed.returncode != 0:
        raise ReleaseBundleError("仓库安全扫描未通过，已停止打包。")


def _preflight_public_tree_nodes(repo_root: Path) -> None:
    """功能：在 Git 枚举前拒绝公开源根中 Git 不会列出的链接或 special file。

    输入参数：``repo_root`` 为已通过静态安全扫描的工作树。
    输出返回值：无；枚举只读取固定公开顶层的文件类型，不跟随链接，
    并主动剪枝缓存、运行、build 与 node_modules 目录。
    """

    for root_filename in ROOT_FILE_ALLOWLIST:
        root_path = repo_root / root_filename
        try:
            root_stat = root_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ReleaseBundleError("release 根源文件无法检查类型。") from exc
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISREG(root_stat.st_mode):
            raise ReleaseBundleError("release 根源文件必须是非链接普通文件。")

    for root_name in PUBLIC_SOURCE_ROOTS:
        public_root = repo_root / root_name
        try:
            public_root_stat = public_root.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ReleaseBundleError("release 公开源根无法检查类型。") from exc
        if stat.S_ISLNK(public_root_stat.st_mode) or not stat.S_ISDIR(
            public_root_stat.st_mode
        ):
            raise ReleaseBundleError("release 公开源根必须是非链接真实目录。")
        for current_directory, directory_names, filenames in os.walk(
            public_root,
            topdown=True,
            followlinks=False,
        ):
            safe_directories: list[str] = []
            for directory_name in directory_names:
                if (
                    directory_name.casefold() in EXCLUDED_DIRECTORY_NAMES
                    or directory_name.casefold().startswith(".venv")
                ):
                    continue
                directory_path = Path(current_directory) / directory_name
                try:
                    directory_stat = directory_path.lstat()
                except OSError as exc:
                    raise ReleaseBundleError(
                        "release 公开源目录无法检查类型。"
                    ) from exc
                if stat.S_ISLNK(directory_stat.st_mode):
                    raise ReleaseBundleError("release 公开源树不得包含符号链接。")
                if not stat.S_ISDIR(directory_stat.st_mode):
                    raise ReleaseBundleError("release 公开源树目录节点类型非法。")
                safe_directories.append(directory_name)
            directory_names[:] = safe_directories
            for filename in filenames:
                file_path = Path(current_directory) / filename
                try:
                    file_stat = file_path.lstat()
                except OSError as exc:
                    raise ReleaseBundleError(
                        "release 公开源文件无法检查类型。"
                    ) from exc
                if stat.S_ISLNK(file_stat.st_mode):
                    raise ReleaseBundleError("release 公开源树不得包含符号链接。")
                if not stat.S_ISREG(file_stat.st_mode):
                    raise ReleaseBundleError("release 公开源树不得包含 special file。")


def _git_candidate_paths(repo_root: Path) -> list[str]:
    """功能：获取 Git tracked 与非忽略 untracked 候选路径。

    输入参数：``repo_root`` 为通过安全扫描的 Git 工作树。
    输出返回值：经严格 UTF-8 解码、去重并按字符串排序的路径列表。
    """

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            env=_fixed_subprocess_environment(),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        decoded = completed.stdout.decode("utf-8", errors="strict")
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ReleaseBundleError("无法获取可复现的 Git 候选文件集。") from exc
    return sorted({path for path in decoded.split("\0") if path})


def _read_source_file(
    repo_root: Path,
    relative_path: PurePosixPath,
    *,
    max_bytes: int,
) -> bytes:
    """功能：通过逐级 dirfd 不跟随链接地有界读取公开源文件。

    输入参数：``repo_root`` 为工作树，``relative_path`` 为已验证路径，
    ``max_bytes`` 为读取前与读取中同时执行的单文件硬上限。
    输出返回值：从同一稳定 inode 读取的有界字节快照。
    """

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    directory_descriptor = -1
    file_descriptor = -1
    try:
        directory_descriptor = os.open(repo_root, directory_flags)
        for component in relative_path.parts[:-1]:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=directory_descriptor,
            )
            next_stat = os.fstat(next_descriptor)
            if not stat.S_ISDIR(next_stat.st_mode):
                os.close(next_descriptor)
                raise ReleaseBundleError("release 源路径的中间组件必须是真实目录。")
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        file_descriptor = os.open(
            relative_path.name,
            file_flags,
            dir_fd=directory_descriptor,
        )
        initial_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(initial_stat.st_mode):
            raise ReleaseBundleError("release 源候选必须是普通文件。")
        if initial_stat.st_size > max_bytes:
            raise ReleaseBundleError("release 源文件超出单文件大小上限。")
        chunks: list[bytes] = []
        byte_count = 0
        while True:
            chunk = os.read(
                file_descriptor,
                min(1024 * 1024, max_bytes + 1 - byte_count),
            )
            if not chunk:
                break
            chunks.append(chunk)
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise ReleaseBundleError("release 源文件超出单文件大小上限。")
        final_stat = os.fstat(file_descriptor)
        if byte_count != final_stat.st_size or _stable_file_identity(
            initial_stat
        ) != _stable_file_identity(final_stat):
            raise ReleaseBundleError("release 源文件在读取期间发生改写。")
        return b"".join(chunks)
    except ReleaseBundleError:
        raise
    except OSError as exc:
        raise ReleaseBundleError("release 源路径含链接、类型非法或无法读取。") from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _collect_source_files(
    repo_root: Path, limits: BundleLimits
) -> tuple[SourceFile, ...]:
    """功能：按白名单冻结 dirty Git 工作树的公开源文件。

    输入参数：``repo_root`` 为仓库根，``limits`` 为文件数和字节上限。
    输出返回值：按 POSIX 路径排序的不可变 ``SourceFile`` 元组。
    """

    selected: list[SourceFile] = []
    collision_keys: dict[str, str] = {}
    total_bytes = 0
    for raw_path in _git_candidate_paths(repo_root):
        relative_path = _validate_relative_path(raw_path)
        if _is_explicitly_excluded(relative_path) or not _is_allowlisted_source(
            relative_path
        ):
            continue
        collision_key = unicodedata.normalize("NFC", raw_path).casefold()
        previous_path = collision_keys.get(collision_key)
        if previous_path is not None and previous_path != raw_path:
            raise ReleaseBundleError("release 源路径存在大小写或 Unicode 归一化冲突。")
        collision_keys[collision_key] = raw_path
        payload = _read_source_file(
            repo_root,
            relative_path,
            max_bytes=limits.max_file_bytes,
        )
        if raw_path == ".env.example":
            _validate_root_env_example(payload)
        total_bytes += len(payload)
        if total_bytes > limits.max_total_bytes:
            raise ReleaseBundleError("release 源文件总字节超出上限。")
        selected.append(
            SourceFile(path=raw_path, payload=payload, sha256=_sha256_bytes(payload))
        )
        if len(selected) > limits.max_files:
            raise ReleaseBundleError("release 源文件数超出上限。")
    if not selected:
        raise ReleaseBundleError("release 白名单中没有可打包源文件。")
    return tuple(selected)


def _validate_root_env_example(payload: bytes) -> None:
    """功能：验证公开根环境模板只声明唯一变量名与空值。

    输入参数：``payload`` 为 nofollow、有界读取或归档验证后的模板字节。
    输出返回值：无；仅接受严格 UTF-8 的注释、空行和 ``NAME=``；
    非空值、重复键、export、非法行、NUL、CR 或超限均脱敏失败关闭。
    """

    if len(payload) > MAX_ENV_EXAMPLE_BYTES:
        raise ReleaseBundleError("release 根 .env.example 超出大小上限。")
    if b"\0" in payload:
        raise ReleaseBundleError("release 根 .env.example 含非法字节。")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReleaseBundleError(
            "release 根 .env.example 必须是严格 UTF-8 文本。"
        ) from exc
    if "\r" in text:
        raise ReleaseBundleError("release 根 .env.example 含非法换行。")
    names: set[str] = set()
    for line in text.split("\n"):
        if not line or line.startswith("#"):
            continue
        match = ENV_EXAMPLE_ASSIGNMENT.fullmatch(line)
        if match is None:
            raise ReleaseBundleError("release 根 .env.example 行格式无效。")
        name = match.group(1)
        if name in names:
            raise ReleaseBundleError("release 根 .env.example 变量重复。")
        names.add(name)


def _scan_frozen_source_files(
    repo_root: Path,
    files: Sequence[SourceFile],
    *,
    limits: BundleLimits,
) -> None:
    """功能：对 bounded-read 后的确切字节快照复用仓库扫描器规则。

    输入参数：``repo_root`` 为工作树，``files`` 为待打包快照，``limits`` 为读取上限。
    输出返回值：无；扫描器本身与快照不一致、规则无效或快照
    命中任一高置信度规则时均脱敏拒绝。
    """

    scanner_relative_path = "scripts/security/scan_repository.py"
    frozen_scanner = next(
        (
            source_file
            for source_file in files
            if source_file.path == scanner_relative_path
        ),
        None,
    )
    if frozen_scanner is None:
        raise ReleaseBundleError("release 快照缺少必需的安全扫描器。")
    scanner_path = PurePosixPath(scanner_relative_path)
    current_scanner_before = _read_source_file(
        repo_root,
        scanner_path,
        max_bytes=limits.max_file_bytes,
    )
    if current_scanner_before != frozen_scanner.payload:
        raise ReleaseBundleError("release 安全扫描器在冻结期间发生变更。")
    try:
        namespace = runpy.run_path(
            str(repo_root / scanner_relative_path),
            run_name="paraguibench_frozen_security_scanner",
        )
    except (Exception, SystemExit) as exc:
        raise ReleaseBundleError("release 冻结字节安全规则无法加载。") from exc
    current_scanner_after = _read_source_file(
        repo_root,
        scanner_path,
        max_bytes=limits.max_file_bytes,
    )
    if current_scanner_after != frozen_scanner.payload:
        raise ReleaseBundleError("release 安全扫描器在冻结期间发生变更。")

    rules = namespace.get("RULES")
    max_text_file_bytes = namespace.get("MAX_TEXT_FILE_BYTES")
    if (
        not isinstance(rules, (list, tuple))
        or len(rules) > 128
        or not isinstance(max_text_file_bytes, int)
        or isinstance(max_text_file_bytes, bool)
        or max_text_file_bytes <= 0
        or max_text_file_bytes > limits.max_file_bytes
    ):
        raise ReleaseBundleError("release 冻结字节安全规则无效。")
    patterns: list[re.Pattern[str]] = []
    for rule in rules:
        pattern = getattr(rule, "pattern", None)
        if not isinstance(pattern, re.Pattern):
            raise ReleaseBundleError("release 冻结字节安全规则无效。")
        patterns.append(pattern)

    for source_file in files:
        if (
            len(source_file.payload) > max_text_file_bytes
            or b"\0" in source_file.payload
        ):
            continue
        text = source_file.payload.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if any(pattern.search(line) is not None for pattern in patterns):
                raise ReleaseBundleError("冻结的 release 源字节未通过安全扫描。")


def _source_tree_sha256(records: Iterable[tuple[str, int, str]]) -> str:
    """功能：根据排序路径、固定 mode、字节数和文件摘要计算源树摘要。

    输入参数：``records`` 为按路径排序的 ``(path, size, sha256)`` 记录；
    函数不需要按文件大小分配占位字节。
    输出返回值：与 mtime、uid、gid 和工作树绝对路径无关的 SHA-256。
    """

    digest = hashlib.sha256()
    for path, size, sha256 in records:
        record = f"{path}\0{FIXED_FILE_MODE:04o}\0{size}\0{sha256}\n"
        digest.update(record.encode("utf-8"))
    return digest.hexdigest()


def _write_deterministic_archive(path: Path, files: Sequence[SourceFile]) -> None:
    """功能：将内存快照写成 gzip 头和 tar 元数据均固定的归档。

    输入参数：``path`` 为临时输出文件，``files`` 为排序源文件。
    输出返回值：无；成功时在 ``path`` 写入完整 tar.gz。
    """

    with path.open("wb") as raw_output:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw_output,
            mtime=0,
        ) as compressed_output:
            with tarfile.open(
                fileobj=compressed_output,
                mode="w",
                format=tarfile.GNU_FORMAT,
            ) as archive:
                for source_file in files:
                    member = tarfile.TarInfo(name=f"{ARCHIVE_ROOT}/{source_file.path}")
                    member.size = len(source_file.payload)
                    member.mode = FIXED_FILE_MODE
                    member.mtime = 0
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    member.type = tarfile.REGTYPE
                    archive.addfile(member, io.BytesIO(source_file.payload))


def _atomic_create(path: Path, payload: bytes) -> None:
    """功能：在同一目录中以固定权限原子创建且绝不覆盖目标文件。

    输入参数：``path`` 为目标路径，``payload`` 为待写入字节。
    输出返回值：无；先写入同目录临时 inode，再通过不覆盖 hard link
    发布，并在目标已存在时 fail closed。
    """

    descriptor, temporary_name = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, FIXED_FILE_MODE)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary_path, path, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            temporary_path.unlink()


def build_release_bundle(
    repo_root: Path,
    output_directory: Path,
    *,
    name: str = "paraguibench-cleanroom",
    limits: BundleLimits = DEFAULT_LIMITS,
) -> ReleaseArtifacts:
    """功能：将当前 dirty Git 工作树冻结为确定性 cleanroom 三件套。

    输入参数：``repo_root`` 为源仓库，``output_directory`` 为本地输出目录，
    ``name`` 为安全 ASCII 产物前缀，``limits`` 为双向资源门禁。
    输出返回值：``ReleaseArtifacts``；安全扫描在任何候选文件
    枚举和输出写入之前执行。
    """

    if SAFE_BUNDLE_NAME.fullmatch(name) is None:
        raise ReleaseBundleError("release 产物名称必须为有限长度的小写 ASCII 标识。")
    _validate_limits(limits)
    resolved_root = repo_root.resolve()
    if not resolved_root.is_dir():
        raise ReleaseBundleError("release 仓库根目录无效。")
    _run_security_scan(resolved_root)
    _preflight_public_tree_nodes(resolved_root)
    files = _collect_source_files(resolved_root, limits)
    _scan_frozen_source_files(resolved_root, files, limits=limits)
    source_tree_sha256 = _source_tree_sha256(
        (source_file.path, len(source_file.payload), source_file.sha256)
        for source_file in files
    )
    total_bytes = sum(len(source_file.payload) for source_file in files)

    try:
        output_directory.mkdir(parents=True, exist_ok=True)
        output_directory_stat = output_directory.lstat()
    except OSError as exc:
        raise ReleaseBundleError("release 输出目录无法创建或检查。") from exc
    if stat.S_ISLNK(output_directory_stat.st_mode) or not stat.S_ISDIR(
        output_directory_stat.st_mode
    ):
        raise ReleaseBundleError("release 输出目录必须是非链接真实目录。")
    archive_path = output_directory / f"{name}.tar.gz"
    manifest_path = output_directory / f"{name}.manifest.json"
    checksum_path = output_directory / f"{name}.sha256"
    target_paths = (archive_path, manifest_path, checksum_path)
    if any(os.path.lexists(path) for path in target_paths):
        raise ReleaseBundleError("release 目标三件套中已存在同名产物。")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{name}-", suffix=".tar.gz", dir=output_directory
    )
    os.close(descriptor)
    temporary_archive = Path(temporary_name)
    created_targets: list[Path] = []
    try:
        _write_deterministic_archive(temporary_archive, files)
        archive_sha256, archive_bytes = _sha256_file(
            temporary_archive,
            max_bytes=limits.max_archive_bytes,
        )
        os.chmod(temporary_archive, FIXED_FILE_MODE)
        os.link(temporary_archive, archive_path, follow_symlinks=False)
        created_targets.append(archive_path)

        manifest = {
            "archive": {
                "bytes": archive_bytes,
                "name": archive_path.name,
                "root": ARCHIVE_ROOT,
                "sha256": archive_sha256,
            },
            "files": [
                {
                    "bytes": len(source_file.payload),
                    "mode": f"{FIXED_FILE_MODE:04o}",
                    "path": source_file.path,
                    "sha256": source_file.sha256,
                }
                for source_file in files
            ],
            "schema_version": SCHEMA_VERSION,
            "source_tree": {
                "file_count": len(files),
                "sha256": source_tree_sha256,
                "total_bytes": total_bytes,
            },
        }
        manifest_payload = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(manifest_payload) > limits.max_manifest_bytes:
            raise ReleaseBundleError("release JSON 清单超出大小上限。")
        _atomic_create(manifest_path, manifest_payload)
        created_targets.append(manifest_path)
        manifest_sha256 = _sha256_bytes(manifest_payload)
        checksum_payload = (
            f"{archive_sha256}  {archive_path.name}\n"
            f"{manifest_sha256}  {manifest_path.name}\n"
        ).encode("ascii")
        _atomic_create(checksum_path, checksum_payload)
        created_targets.append(checksum_path)
    except ReleaseBundleError:
        for created_target in reversed(created_targets):
            with contextlib.suppress(OSError):
                created_target.unlink()
        raise
    except OSError as exc:
        for created_target in reversed(created_targets):
            with contextlib.suppress(OSError):
                created_target.unlink()
        raise ReleaseBundleError("release 三件套无法安全发布。") from exc
    finally:
        with contextlib.suppress(OSError):
            temporary_archive.unlink()
    return ReleaseArtifacts(
        archive_path=archive_path,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        archive_sha256=archive_sha256,
        source_tree_sha256=source_tree_sha256,
        file_count=len(files),
        total_bytes=total_bytes,
    )


def _parse_checksum_payload(
    payload: bytes,
    *,
    archive_name: str,
    manifest_name: str,
) -> dict[str, str]:
    """功能：解析严格两行 GNU sha256sum 格式的 sidecar 字节。

    输入参数：``payload`` 为已经 nofollow 有界读取的 sidecar，
    ``archive_name`` 和 ``manifest_name`` 为必须精确出现一次的基名。
    输出返回值：从基名到 SHA-256 的字典。
    """

    if len(payload) > 1024 or b"\0" in payload:
        raise ReleaseBundleError("release SHA256 sidecar 格式无效。")
    try:
        lines = payload.decode("ascii", errors="strict").splitlines()
    except UnicodeError as exc:
        raise ReleaseBundleError("release SHA256 sidecar 格式无效。") from exc
    expected_names = {archive_name, manifest_name}
    checksums: dict[str, str] = {}
    for line in lines:
        digest, separator, filename = line.partition("  ")
        if (
            separator != "  "
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or filename not in expected_names
            or filename in checksums
        ):
            raise ReleaseBundleError("release SHA256 sidecar 格式无效。")
        checksums[filename] = digest
    if set(checksums) != expected_names:
        raise ReleaseBundleError("release SHA256 sidecar 条目不完整。")
    return checksums


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """功能：将 JSON object pair 列表转换为不允许重复 key 的字典。

    输入参数：``pairs`` 为 ``json.loads`` 按源顺序提供的键值对。
    输出返回值：键唯一时返回字典；否则抛出 ``ValueError``。
    """

    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError("duplicate JSON key")
        parsed[key] = value
    return parsed


def _reject_nonfinite_json_constant(value: str) -> object:
    """功能：拒绝 Python JSON 解析器默认容忍的 NaN/Infinity。

    输入参数：``value`` 为非标准常量文本。
    输出返回值：不返回，始终抛出 ``ValueError``。
    """

    raise ValueError(f"non-finite JSON constant: {value}")


def _parse_manifest_payload(
    payload: bytes, *, limits: BundleLimits
) -> dict[str, object]:
    """功能：在大小门禁内严格解析 release JSON 清单字节。

    输入参数：``payload`` 为已经 nofollow 有界读取的清单字节，
    ``limits`` 提供最大清单字节。
    输出返回值：JSON 顶层对象；非对象或解析失败时拒绝。
    """

    try:
        if len(payload) > limits.max_manifest_bytes:
            raise ReleaseBundleError("release JSON 清单超出大小上限。")
        parsed = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except ReleaseBundleError:
        raise
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise ReleaseBundleError("release JSON 清单无法解析。") from exc
    if not isinstance(parsed, dict):
        raise ReleaseBundleError("release JSON 清单顶层必须是对象。")
    canonical_payload = (
        json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if payload != canonical_payload:
        raise ReleaseBundleError("release JSON 清单不是确定性规范编码。")
    return parsed


def verify_release_bundle(
    archive_path: Path,
    manifest_path: Path,
    checksum_path: Path,
    *,
    limits: BundleLimits = DEFAULT_LIMITS,
) -> BundleVerification:
    """功能：离线验证归档、JSON 文件清单与 SHA256 sidecar 的一致性。

    输入参数：三件套路径以及可选双向资源门禁 ``limits``。
    输出返回值：全部字节、元数据和路径通过校验时返回
    ``BundleVerification``；该函数不解压到文件系统。
    """

    _validate_limits(limits)
    checksum_payload = _read_regular_file_bounded(
        checksum_path,
        max_bytes=1024,
        purpose="release SHA256 sidecar",
    )
    checksums = _parse_checksum_payload(
        checksum_payload,
        archive_name=archive_path.name,
        manifest_name=manifest_path.name,
    )
    canonical_checksum_payload = (
        f"{checksums[archive_path.name]}  {archive_path.name}\n"
        f"{checksums[manifest_path.name]}  {manifest_path.name}\n"
    ).encode("ascii")
    if checksum_payload != canonical_checksum_payload:
        raise ReleaseBundleError("release SHA256 sidecar 不是确定性规范编码。")
    manifest_payload = _read_regular_file_bounded(
        manifest_path,
        max_bytes=limits.max_manifest_bytes,
        purpose="release JSON 清单",
    )
    manifest_sha256 = _sha256_bytes(manifest_payload)
    if checksums[manifest_path.name] != manifest_sha256:
        raise ReleaseBundleError("release JSON 清单 SHA-256 与 sidecar 不一致。")
    manifest = _parse_manifest_payload(manifest_payload, limits=limits)
    archive_metadata = manifest.get("archive")
    source_tree = manifest.get("source_tree")
    file_entries = manifest.get("files")
    if (
        set(manifest) != {"archive", "files", "schema_version", "source_tree"}
        or manifest.get("schema_version") != SCHEMA_VERSION
        or not isinstance(archive_metadata, dict)
        or not isinstance(source_tree, dict)
        or not isinstance(file_entries, list)
    ):
        raise ReleaseBundleError("release JSON 清单 schema 无效。")
    if (
        set(archive_metadata) != {"bytes", "name", "root", "sha256"}
        or not isinstance(archive_metadata.get("bytes"), int)
        or isinstance(archive_metadata.get("bytes"), bool)
        or archive_metadata.get("bytes", 0) <= 0
        or archive_metadata.get("bytes", 0) > limits.max_archive_bytes
        or not isinstance(archive_metadata.get("name"), str)
        or not isinstance(archive_metadata.get("root"), str)
        or not isinstance(archive_metadata.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", archive_metadata.get("sha256", "")) is None
    ):
        raise ReleaseBundleError("release JSON 清单归档元数据类型无效。")
    if not file_entries or len(file_entries) > limits.max_files:
        raise ReleaseBundleError("release JSON 清单文件数超出上限。")
    if (
        set(source_tree) != {"file_count", "sha256", "total_bytes"}
        or not isinstance(source_tree.get("file_count"), int)
        or isinstance(source_tree.get("file_count"), bool)
        or not isinstance(source_tree.get("total_bytes"), int)
        or isinstance(source_tree.get("total_bytes"), bool)
        or not isinstance(source_tree.get("sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", source_tree.get("sha256", "")) is None
    ):
        raise ReleaseBundleError("release JSON 清单源树统计类型无效。")

    expected: dict[str, dict[str, object]] = {}
    collision_keys: dict[str, str] = {}
    total_bytes = 0
    source_records_for_digest: list[tuple[str, int, str]] = []
    for entry in file_entries:
        if not isinstance(entry, dict) or set(entry) != {
            "bytes",
            "mode",
            "path",
            "sha256",
        }:
            raise ReleaseBundleError("release JSON 清单文件条目无效。")
        relative_path = entry.get("path")
        if not isinstance(relative_path, str):
            raise ReleaseBundleError("release JSON 清单文件路径无效。")
        parsed_path = _validate_relative_path(relative_path)
        collision_key = unicodedata.normalize("NFC", relative_path).casefold()
        if collision_key in collision_keys:
            raise ReleaseBundleError("release JSON 清单存在路径冲突或重复。")
        collision_keys[collision_key] = relative_path
        file_bytes = entry.get("bytes")
        file_sha256 = entry.get("sha256")
        if (
            _is_explicitly_excluded(parsed_path)
            or not _is_allowlisted_source(parsed_path)
            or entry.get("mode") != f"{FIXED_FILE_MODE:04o}"
            or not isinstance(file_bytes, int)
            or isinstance(file_bytes, bool)
            or file_bytes < 0
            or file_bytes > limits.max_file_bytes
            or not isinstance(file_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", file_sha256) is None
        ):
            raise ReleaseBundleError("release JSON 清单文件条目越界或非法。")
        total_bytes += file_bytes
        if total_bytes > limits.max_total_bytes:
            raise ReleaseBundleError("release JSON 清单总字节超出上限。")
        expected[relative_path] = entry
        source_records_for_digest.append((relative_path, file_bytes, file_sha256))
    if list(expected) != sorted(expected):
        raise ReleaseBundleError("release JSON 清单文件顺序不确定。")

    seen: set[str] = set()
    archive_sha256 = ""
    archive_bytes = 0
    try:
        with _open_regular_nofollow(archive_path, purpose="release 归档") as (
            archive_handle,
            archive_initial_stat,
        ):
            if archive_initial_stat.st_size > limits.max_archive_bytes:
                raise ReleaseBundleError("release 归档超出大小上限。")
            archive_digest = hashlib.sha256()
            while chunk := archive_handle.read(
                min(1024 * 1024, limits.max_archive_bytes + 1 - archive_bytes)
            ):
                archive_bytes += len(chunk)
                if archive_bytes > limits.max_archive_bytes:
                    raise ReleaseBundleError("release 归档超出大小上限。")
                archive_digest.update(chunk)
            archive_after_hash_stat = os.fstat(archive_handle.fileno())
            if (
                archive_bytes != archive_after_hash_stat.st_size
                or _stable_file_identity(archive_initial_stat)
                != _stable_file_identity(archive_after_hash_stat)
            ):
                raise ReleaseBundleError("release 归档在摘要期间发生改写。")
            archive_sha256 = archive_digest.hexdigest()
            if checksums[archive_path.name] != archive_sha256:
                raise ReleaseBundleError("release 归档 SHA-256 与 sidecar 不一致。")
            if archive_metadata != {
                "bytes": archive_bytes,
                "name": archive_path.name,
                "root": ARCHIVE_ROOT,
                "sha256": archive_sha256,
            }:
                raise ReleaseBundleError("release 归档元数据与 JSON 清单不一致。")
            archive_handle.seek(0)
            gzip_header = archive_handle.read(10)
            if (
                len(gzip_header) != 10
                or gzip_header[:3] != b"\x1f\x8b\x08"
                or int.from_bytes(gzip_header[4:8], "little") != 0
            ):
                raise ReleaseBundleError("release gzip 头部无效或 mtime 未固定为 0。")
            archive_handle.seek(0)
            with tarfile.open(fileobj=archive_handle, mode="r:gz") as archive:
                expected_paths = list(expected)
                for member_index, member in enumerate(archive):
                    if not member.isreg():
                        raise ReleaseBundleError(
                            "release 归档只允许固定权限的普通文件。"
                        )
                    prefix = f"{ARCHIVE_ROOT}/"
                    if not member.name.startswith(prefix):
                        raise ReleaseBundleError("release 归档根目录或路径无效。")
                    relative_path = member.name[len(prefix) :]
                    _validate_relative_path(relative_path)
                    if (
                        member_index >= len(expected_paths)
                        or relative_path != expected_paths[member_index]
                    ):
                        raise ReleaseBundleError("release 归档文件顺序不确定。")
                    entry = expected.get(relative_path)
                    if entry is None or relative_path in seen:
                        raise ReleaseBundleError("release 归档文件与 JSON 清单不一致。")
                    if (
                        member.mode != FIXED_FILE_MODE
                        or member.mtime != 0
                        or member.uid != 0
                        or member.gid != 0
                        or member.uname != ""
                        or member.gname != ""
                        or member.size != entry["bytes"]
                    ):
                        raise ReleaseBundleError("release 归档文件元数据不确定或越界。")
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ReleaseBundleError("release 归档文件无法读取。")
                    payload = extracted.read(limits.max_file_bytes + 1)
                    if (
                        len(payload) != entry["bytes"]
                        or _sha256_bytes(payload) != entry["sha256"]
                    ):
                        raise ReleaseBundleError(
                            "release 归档文件 SHA-256 或字节数不一致。"
                        )
                    if relative_path == ".env.example":
                        _validate_root_env_example(payload)
                    seen.add(relative_path)
            archive_final_stat = os.fstat(archive_handle.fileno())
            if _stable_file_identity(archive_initial_stat) != _stable_file_identity(
                archive_final_stat
            ):
                raise ReleaseBundleError("release 归档在验证期间发生改写。")
    except ReleaseBundleError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBundleError("release 归档无法解析。") from exc
    if seen != set(expected):
        raise ReleaseBundleError("release 归档缺少 JSON 清单声明的文件。")
    computed_tree_sha256 = _source_tree_sha256(source_records_for_digest)
    if source_tree != {
        "file_count": len(expected),
        "sha256": computed_tree_sha256,
        "total_bytes": total_bytes,
    }:
        raise ReleaseBundleError("release 源树统计或 SHA-256 不一致。")
    return BundleVerification(
        archive_sha256=archive_sha256,
        source_tree_sha256=computed_tree_sha256,
        file_count=len(expected),
        total_bytes=total_bytes,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """功能：构造 release bundle 的 build/verify 命令行解析器。

    输入参数：无。
    输出返回值：不接受凭据或 endpoint 参数的 ``ArgumentParser``。
    """

    parser = argparse.ArgumentParser(
        description="构建或离线验证 ParaGUIBench cleanroom release bundle。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser(
        "build", help="从当前 Git 工作树构建确定性包。"
    )
    build_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--name", default="paraguibench-cleanroom")
    verify_parser = subparsers.add_parser("verify", help="离线验证本地三件套。")
    verify_parser.add_argument("--archive", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--checksums", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """功能：执行 build/verify CLI 并只输出脱敏的摘要统计。

    输入参数：``argv`` 为可选命令行序列，``None`` 时使用进程命令行。
    输出返回值：成功返回 0，安全门禁失败返回 1。
    """

    arguments = build_argument_parser().parse_args(argv)
    try:
        if arguments.command == "build":
            result = build_release_bundle(
                arguments.repo_root,
                arguments.output_dir,
                name=arguments.name,
            )
            print(
                "release bundle 构建通过："
                f"{result.file_count} 个文件，源树 SHA-256={result.source_tree_sha256}，"
                f"归档 SHA-256={result.archive_sha256}。"
            )
        else:
            result = verify_release_bundle(
                arguments.archive,
                arguments.manifest,
                arguments.checksums,
            )
            print(
                "release bundle 验证通过："
                f"{result.file_count} 个文件，源树 SHA-256={result.source_tree_sha256}，"
                f"归档 SHA-256={result.archive_sha256}。"
            )
    except ReleaseBundleError as exc:
        print(f"release bundle 门禁失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
