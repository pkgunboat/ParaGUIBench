"""一次性检查首个 OSWorld GUI-only live run 的全部部署门禁。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
)
from paraguibench.runtime.assets import (
    AssetManifest,
    verify_asset_directory,
)

_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{1,127}")


@dataclass(frozen=True)
class DoctorCheck:
    """保存一个不含配置值或路径的部署检查结果。"""

    name: str
    passed: bool


@dataclass(frozen=True)
class DoctorReport:
    """保存完整门禁列表，并提供总体是否通过。"""

    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        """判断全部 doctor check 是否通过。

        输入参数：
            无。
        输出返回值：
            全部 ``passed=True`` 时返回 ``True``。
        """

        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class OSWorldDoctorConfig:
    """保存 doctor 所需的非敏感路径、digest、端口和凭据引用。"""

    image_manifest: OSWorldImageManifest
    qcow2_path: Path
    asset_manifest: AssetManifest
    asset_cache_root: Path
    server_port: int
    vnc_port: int
    api_key_env: str
    base_url_env: str

    def __post_init__(self) -> None:
        """验证 doctor 配置形状，不读取文件正文或环境变量值。

        输入参数：
            无；读取 dataclass 字段。
        输出返回值：
            无；合法配置正常返回。
        异常：
            ValueError：路径、端口或环境变量引用无效。
        """

        if not isinstance(self.image_manifest, OSWorldImageManifest):
            raise TypeError("image_manifest 类型无效")
        if not self.qcow2_path.is_absolute() or self.qcow2_path.is_symlink():
            raise ValueError("qcow2_path 必须是绝对且非符号链接路径")
        if not self.asset_cache_root.is_absolute():
            raise ValueError("asset_cache_root 必须是绝对路径")
        for port in (self.server_port, self.vnc_port):
            if (
                not isinstance(port, int)
                or isinstance(port, bool)
                or not 1024 <= port <= 65535
            ):
                raise ValueError("doctor 端口必须位于 1024–65535")
        if self.server_port == self.vnc_port:
            raise ValueError("server_port 与 vnc_port 不得相同")
        for name in (self.api_key_env, self.base_url_env):
            if (
                not isinstance(name, str)
                or _ENV_NAME_PATTERN.fullmatch(name) is None
            ):
                raise ValueError("凭据和 endpoint 必须通过大写环境变量名引用")


def inspect_osworld_prerequisites(
    config: OSWorldDoctorConfig,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    environment: Mapping[str, str] | None = None,
    python_version: Sequence[int] | None = None,
    kvm_probe: Callable[[], bool] | None = None,
    port_probe: Callable[[int], bool] | None = None,
) -> DoctorReport:
    """执行全部本地检查，不因单项失败而短路。

    输入参数：
        config：仅含非敏感值和环境变量引用的 doctor 配置。
        command_runner：subprocess-compatible runner；测试可注入 fake。
        environment：只按两个显式引用读取的环境 Mapping。
        python_version：测试可注入的 ``(major, minor)``。
        kvm_probe：测试可替换的 KVM 可用性检查。
        port_probe：测试可替换的 loopback 端口检查。
    输出返回值：
        固定顺序、只含检查名称和布尔状态的 ``DoctorReport``。
    """

    runner = command_runner or subprocess.run
    env = environment if environment is not None else os.environ
    version = tuple(python_version or sys.version_info[:2])
    probe_kvm = kvm_probe or _default_kvm_probe
    probe_port = port_probe or _loopback_port_available
    checks: list[DoctorCheck] = []

    checks.append(
        DoctorCheck(
            "python_version",
            len(version) >= 2 and (3, 11) <= tuple(version[:2]) < (3, 14),
        )
    )
    checks.append(DoctorCheck("kvm", _safe_boolean_probe(probe_kvm)))
    checks.append(
        DoctorCheck(
            "docker_daemon",
            _docker_command_ok(
                runner,
                ["docker", "version", "--format", "{{.Server.Version}}"],
            ),
        )
    )
    checks.append(
        DoctorCheck(
            "container_image",
            _docker_command_ok(
                runner,
                [
                    "docker",
                    "image",
                    "inspect",
                    config.image_manifest.container_image,
                ],
            ),
        )
    )
    checks.append(
        DoctorCheck(
            "qcow2_digest",
            _qcow2_digest_matches(
                config.qcow2_path,
                config.image_manifest.extracted_sha256,
            ),
        )
    )
    checks.append(
        DoctorCheck(
            "asset_cache",
            _asset_cache_ok(config),
        )
    )
    checks.append(
        DoctorCheck(
            "server_port",
            _safe_port_probe(probe_port, config.server_port),
        )
    )
    checks.append(
        DoctorCheck(
            "vnc_port",
            _safe_port_probe(probe_port, config.vnc_port),
        )
    )
    checks.append(
        DoctorCheck(
            "api_key",
            bool(env.get(config.api_key_env)),
        )
    )
    checks.append(
        DoctorCheck(
            "model_base_url",
            _safe_https_base_url(env.get(config.base_url_env)),
        )
    )
    return DoctorReport(checks=tuple(checks))


def _docker_command_ok(
    runner: Callable[..., subprocess.CompletedProcess[str]],
    command: list[str],
) -> bool:
    """执行无 shell Docker 探针并只保留成功布尔值。

    输入参数：
        runner：subprocess-compatible 函数。
        command：固定模板生成的 Docker argv。
    输出返回值：
        命令在期限内 returncode=0 时返回 ``True``。
    """

    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False


def _qcow2_digest_matches(path: Path, expected: str | None) -> bool:
    """验证普通 qcow2 文件与已固定 extracted digest 一致。

    输入参数：
        path：目标服务器上的绝对 qcow2 路径。
        expected：manifest 中的 extracted SHA-256；未固定时为 ``None``。
    输出返回值：
        文件安全存在且完整摘要一致时返回 ``True``。
    """

    if (
        expected is None
        or not path.is_file()
        or path.is_symlink()
    ):
        return False
    try:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected
    except OSError:
        return False


def _asset_cache_ok(config: OSWorldDoctorConfig) -> bool:
    """验证任务资产缓存满足 manifest 的大小、摘要和闭集契约。

    输入参数：
        config：包含 asset manifest 与缓存根目录的 doctor 配置。
    输出返回值：
        完整校验通过时返回 ``True``，任一异常或不一致返回 ``False``。
    """

    try:
        directory = (
            config.asset_cache_root / config.asset_manifest.asset_set_id
        )
        return verify_asset_directory(config.asset_manifest, directory).ok
    except Exception:
        return False


def _default_kvm_probe() -> bool:
    """检查当前进程能否读写 Linux KVM device。

    输入参数：
        无。
    输出返回值：
        ``/dev/kvm`` 存在且当前进程可读写时返回 ``True``。
    """

    return Path("/dev/kvm").exists() and os.access(
        "/dev/kvm",
        os.R_OK | os.W_OK,
    )


def _loopback_port_available(port: int) -> bool:
    """短暂绑定 loopback 检查一个 TCP 端口当前可用。

    输入参数：
        port：已通过范围校验的 host 端口。
    输出返回值：
        能独占绑定时返回 ``True``，否则返回 ``False``。
    """

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _safe_boolean_probe(probe: Callable[[], bool]) -> bool:
    """执行无参数探针并把异常折叠为失败。

    输入参数：
        probe：返回布尔状态的检查函数。
    输出返回值：
        仅在探针明确返回 truthy 时为 ``True``。
    """

    try:
        return bool(probe())
    except Exception:
        return False


def _safe_port_probe(probe: Callable[[int], bool], port: int) -> bool:
    """执行端口探针并把异常折叠为失败。

    输入参数：
        probe：接收端口的检查函数。
        port：待检查 host 端口。
    输出返回值：
        仅在探针明确返回 truthy 时为 ``True``。
    """

    try:
        return bool(probe(port))
    except Exception:
        return False


def _safe_https_base_url(value: str | None) -> bool:
    """验证环境中 endpoint 为无凭据、query/fragment 的 HTTPS URL。

    输入参数：
        value：从显式 base_url_env 引用取得的可空字符串。
    输出返回值：
        URL 满足安全边界时返回 ``True``；不会把值写入结果。
    """

    if not isinstance(value, str) or not value:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return bool(
        parts.scheme == "https"
        and parts.hostname
        and parts.username is None
        and parts.password is None
        and not parts.query
        and not parts.fragment
    )
