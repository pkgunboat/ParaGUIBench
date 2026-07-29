"""代表任务 OSWorld 环境准备与 owned cleanup 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from paraguibench.runtime.osworld_environment import (
    OSWorldEnvironmentError,
    OSWorldTaskEnvironment,
)


class _DockerSession:
    """记录当前环境是否只启动/关闭自身 Docker session。"""

    def __init__(self, calls: list[str]) -> None:
        """保存调用记录。

        输入参数：
            calls：测试共享的阶段列表。
        输出返回值：
            无。
        """

        self.calls = calls

    def start(self) -> str:
        """记录启动并返回合成 owned container ID。

        输入参数：
            无。
        输出返回值：
            合成容器 ID。
        """

        self.calls.append("docker.start")
        return "a" * 64

    def close(self) -> None:
        """记录只清理本 session。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls.append("docker.close")


class _Controller:
    """模拟 ready guest、文件上传、摘要校验和目录打开。"""

    def __init__(self, calls: list[str]) -> None:
        """初始化 guest 内存文件表。

        输入参数：
            calls：测试共享的阶段列表。
        输出返回值：
            无。
        """

        self.calls = calls
        self.files: dict[str, bytes] = {}

    def wait_until_ready(self, *, timeout: float) -> None:
        """记录 controller readiness。

        输入参数：
            timeout：runtime 提供的启动上限。
        输出返回值：
            无。
        """

        assert timeout > 0
        self.calls.append("controller.ready")

    def get_desktop_path(self) -> str:
        """返回合成 guest 当前用户 Desktop。

        输入参数：
            无。
        输出返回值：
            POSIX 绝对路径。
        """

        return "/home/oai/Desktop"

    def upload_file(self, local_path: Path, guest_path: str) -> None:
        """把本地 bytes 保存到 guest 内存文件表。

        输入参数：
            local_path：已验证的 host 文件。
            guest_path：runtime 推导的 guest shared 目标。
        输出返回值：
            无。
        """

        self.files[guest_path] = local_path.read_bytes()
        self.calls.append("controller.upload")

    def execute(self, command: list[str]) -> Any:
        """模拟 sha256sum 与 find 的结构化命令结果。

        输入参数：
            command：runtime 生成的 shell-free argv。
        输出返回值：
            具有 returncode/stdout/stderr 属性的合成对象。
        """

        class Result:
            """保存一次合成 guest 命令结果。"""

            returncode = 0
            stdout = ""
            stderr = ""

        result = Result()
        if command[0] == "sha256sum":
            guest_path = command[-1]
            result.stdout = (
                hashlib.sha256(self.files[guest_path]).hexdigest()
                + "  "
                + guest_path
                + "\n"
            )
        elif command[0] == "find":
            result.stdout = "\n".join(
                sorted(Path(path).name for path in self.files)
            ) + "\n"
        return result

    def open_path(self, guest_path: str) -> None:
        """记录 runtime 打开的 shared 目录。

        输入参数：
            guest_path：已准备完成的 guest 目录。
        输出返回值：
            无。
        """

        assert guest_path == "/home/oai/shared"
        self.calls.append("controller.open_path")


def test_environment_uploads_exact_verified_asset_set_and_cleans_owned_session(
    tmp_path: Path,
) -> None:
    """验证 host 固定资产经摘要校验上传并打开，结束时只关自身 session。

    输入参数：
        tmp_path：pytest 提供的合成 repo、cache 和 manifest 根目录。
    输出返回值：
        无；两个文件必须上传至动态 guest home 下的 shared 目录。
    """

    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    cache_root = tmp_path / "cache"
    task_cache = cache_root / "synthetic-assets"
    task_cache.mkdir(parents=True)
    files = {"diagram.jpg": b"image", "paper.pdf": b"pdf"}
    manifest_files: list[dict[str, Any]] = []
    for name, content in files.items():
        (task_cache / name).write_bytes(content)
        manifest_files.append(
            {
                "path": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    manifest_path = manifest_root / "synthetic.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "synthetic-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/assets",
                    "revision": "c" * 40,
                    "base_path": "dataset/task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": manifest_files,
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    controller = _Controller(calls)
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=cache_root,
        docker_session=_DockerSession(calls),
        controller=controller,
        ready_timeout=300,
    )
    task = {
        "task_id": "synthetic-task",
        "instruction": "Inspect the shared folder.",
        "asset_manifest": str(manifest_path.relative_to(repo_root)),
    }

    environment.start()
    environment.prepare(task)
    environment.close()

    assert set(controller.files) == {
        "/home/oai/shared/diagram.jpg",
        "/home/oai/shared/paper.pdf",
    }
    assert environment.guest_shared_dir == "/home/oai/shared"
    assert calls == [
        "docker.start",
        "controller.ready",
        "controller.upload",
        "controller.upload",
        "controller.open_path",
        "docker.close",
    ]


def test_environment_rejects_symlinked_asset_manifest_before_cache_access(
    tmp_path: Path,
) -> None:
    """验证仓库内 manifest 末端符号链接不会因 resolve 而绕过门禁。

    输入参数：
        tmp_path：pytest 提供的合成 repo 与外部 manifest 目录。
    输出返回值：
        无；prepare 在读取资产缓存前以普通文件契约失败。
    """

    repo_root = tmp_path / "repo"
    manifest_root = repo_root / "benchmark" / "assets" / "manifests"
    manifest_root.mkdir(parents=True)
    target = manifest_root / "real.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "synthetic-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/assets",
                    "revision": "d" * 40,
                    "base_path": "task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "paper.pdf",
                        "size": 1,
                        "sha256": hashlib.sha256(b"x").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    link = manifest_root / "linked.json"
    link.symlink_to(target)
    calls: list[str] = []
    environment = OSWorldTaskEnvironment(
        repo_root=repo_root,
        asset_cache_root=tmp_path / "cache",
        docker_session=_DockerSession(calls),
        controller=_Controller(calls),
    )
    environment.start()

    with pytest.raises(OSWorldEnvironmentError, match="普通仓库文件"):
        environment.prepare(
            {
                "task_id": "synthetic-task",
                "instruction": "Inspect.",
                "asset_manifest": "benchmark/assets/manifests/linked.json",
            }
        )

    environment.close()
