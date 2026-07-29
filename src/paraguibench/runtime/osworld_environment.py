"""为单任务 disposable OSWorld session 准备固定资产与可见 shared 目录。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from paraguibench.runtime.assets import (
    load_asset_manifest,
    verify_asset_directory,
)


class OSWorldEnvironmentError(RuntimeError):
    """表示 OSWorld session、资产缓存或 guest 准备未通过门禁。"""


class OSWorldTaskEnvironment:
    """组合 owned Docker session、controller 与 download-only 资产缓存。"""

    def __init__(
        self,
        *,
        repo_root: Path,
        asset_cache_root: Path,
        docker_session: Any,
        controller: Any,
        ready_timeout: float = 360.0,
    ) -> None:
        """构造尚未启动的单任务环境。

        输入参数：
            repo_root：包含 canonical task 与 asset manifest 的仓库根目录。
            asset_cache_root：repo 外、已由 fetch 命令校验的资产缓存根目录。
            docker_session：只创建并清理自身容器 ID 的 session。
            controller：连接本次 loopback 映射端口的 OSWorld controller。
            ready_timeout：QEMU guest agent-server 的最大就绪等待秒数。
        输出返回值：
            无；构造阶段不访问 Docker、网络或 guest。
        """

        if ready_timeout <= 0:
            raise ValueError("ready_timeout 必须大于零")
        self._repo_root = repo_root.resolve()
        self._asset_cache_root = asset_cache_root.resolve()
        self._docker_session = docker_session
        self.controller = controller
        self._ready_timeout = ready_timeout
        self._started = False
        self._prepared = False
        self._guest_shared_dir: str | None = None

    @property
    def guest_shared_dir(self) -> str | None:
        """返回当前 guest 动态推导的 shared 目录。

        输入参数：
            无。
        输出返回值：
            ``prepare`` 成功后返回 POSIX 绝对路径，否则返回 ``None``。
        """

        return self._guest_shared_dir

    def start(self) -> None:
        """启动 owned Docker/KVM session 并等待 guest controller 就绪。

        输入参数：
            无。
        输出返回值：
            无；成功后环境可进入 ``prepare``。
        异常：
            OSWorldEnvironmentError：重复启动或 controller 未在期限内就绪。
        """

        if self._started:
            raise OSWorldEnvironmentError("OSWorld task environment 已启动")
        self._docker_session.start()
        self._started = True
        self.controller.wait_until_ready(timeout=self._ready_timeout)

    def prepare(self, task: Mapping[str, Any]) -> None:
        """验证 host 资产闭集，上传到动态 guest home 并打开 shared 目录。

        输入参数：
            task：可信 canonical task；必须声明仓库相对 ``asset_manifest``。
        输出返回值：
            无；所有文件在 host 和 guest 均满足 SHA-256，且目录已对 Agent 可见。
        异常：
            OSWorldEnvironmentError：生命周期、路径、缓存、上传或 guest 摘要失败。
        """

        if not self._started:
            raise OSWorldEnvironmentError("环境未启动，不能准备 task")
        if self._prepared:
            raise OSWorldEnvironmentError("当前环境已经准备过 task")
        manifest_relative = task.get("asset_manifest")
        if not isinstance(manifest_relative, str) or not manifest_relative:
            raise OSWorldEnvironmentError("task 缺少 asset_manifest")
        manifest_path = _resolve_repo_manifest(
            self._repo_root,
            manifest_relative,
        )
        manifest = load_asset_manifest(manifest_path)
        _validate_cache_component(manifest.asset_set_id)
        cache_directory = self._asset_cache_root / manifest.asset_set_id
        verification = verify_asset_directory(manifest, cache_directory)
        if not verification.ok:
            raise OSWorldEnvironmentError(
                "host 资产缓存未通过闭集大小与 SHA-256 校验"
            )

        desktop_path = PurePosixPath(self.controller.get_desktop_path())
        guest_home = desktop_path.parent
        guest_shared = guest_home / "shared"
        self._guest_shared_dir = str(guest_shared)
        expected_paths: set[str] = set()
        for asset in manifest.files:
            local_path = cache_directory / asset.path
            guest_path = guest_shared / PurePosixPath(asset.path)
            self.controller.upload_file(local_path, str(guest_path))
            digest_result = self.controller.execute(
                ["sha256sum", "--", str(guest_path)]
            )
            observed_digest = str(digest_result.stdout).split(maxsplit=1)[0]
            if (
                digest_result.returncode != 0
                or observed_digest != asset.sha256
            ):
                raise OSWorldEnvironmentError(
                    "guest 资产未通过上传后 SHA-256 校验"
                )
            expected_paths.add(asset.path)

        listing_result = self.controller.execute(
            [
                "find",
                str(guest_shared),
                "-type",
                "f",
                "-printf",
                "%P\n",
            ]
        )
        if listing_result.returncode != 0:
            raise OSWorldEnvironmentError("guest 无法枚举 shared 资产")
        observed_paths = {
            line.strip()
            for line in str(listing_result.stdout).splitlines()
            if line.strip()
        }
        if observed_paths != expected_paths:
            raise OSWorldEnvironmentError(
                "guest shared 目录不满足资产闭集契约"
            )
        self.controller.open_path(str(guest_shared))
        self._prepared = True

    def close(self) -> None:
        """幂等清理本环境拥有的 Docker session。

        输入参数：
            无。
        输出返回值：
            无；从未启动或已清理时直接返回。
        """

        if not self._started:
            return
        self._docker_session.close()
        self._started = False


def _validate_cache_component(asset_set_id: str) -> None:
    """验证 asset_set_id 可安全作为单层缓存目录名。

    输入参数：
        asset_set_id：asset manifest 的稳定集合标识。
    输出返回值：
        无；安全标识正常返回。
    异常：
        OSWorldEnvironmentError：值为空、含路径分隔或特殊目录语义。
    """

    if (
        not asset_set_id
        or "/" in asset_set_id
        or "\\" in asset_set_id
        or asset_set_id in {".", ".."}
    ):
        raise OSWorldEnvironmentError("asset_set_id 不能作为安全缓存目录")


def _resolve_repo_manifest(repo_root: Path, relative_value: str) -> Path:
    """解析仓库内 asset manifest 并拒绝路径链中的符号链接。

    输入参数：
        repo_root：已 resolve 的仓库根目录。
        relative_value：canonical task 声明的 POSIX 相对路径。
    输出返回值：
        位于仓库内、不经过符号链接的普通文件绝对路径。
    异常：
        OSWorldEnvironmentError：路径绝对、穿越、含反斜杠、符号链接或
        不是普通文件。
    """

    if "\\" in relative_value:
        raise OSWorldEnvironmentError("asset_manifest 必须是 POSIX 相对路径")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise OSWorldEnvironmentError("asset_manifest 不得指向仓库外部")
    candidate = repo_root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise OSWorldEnvironmentError("asset_manifest 不是普通仓库文件")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as error:
        raise OSWorldEnvironmentError(
            "asset_manifest 不得指向仓库外部"
        ) from error
    if not resolved.is_file():
        raise OSWorldEnvironmentError("asset_manifest 不是普通仓库文件")
    return resolved
