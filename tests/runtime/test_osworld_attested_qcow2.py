"""OSWorld component candidate 的 qcow2 隔离快照测试。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

import pytest

from paraguibench.integrations.osworld.docker_session import OSWorldDockerConfig
from paraguibench.runtime.osworld_attested_qcow2 import (
    OSWorldAttestedDockerSession,
    OSWorldQcow2AttestationError,
)


class _RecordingDockerSession:
    """在 start 边界执行竞态动作的无 Docker 测试会话。"""

    def __init__(
        self,
        config: OSWorldDockerConfig,
        action: Callable[[OSWorldDockerConfig], None],
    ) -> None:
        """保存 candidate 传入的 snapshot 配置与竞态动作。

        输入参数：config 为 snapshot 路径配置；action 在模拟
            Docker 解析挂载路径时执行。
        输出返回值：无。
        """

        self.config = config
        self._action = action
        self.closed = False

    def start(self) -> str:
        """执行竞态并返回合法的合成容器 ID。

        输入参数：无。
        输出返回值：64 位小写十六进制容器 ID。
        """

        self._action(self.config)
        return "a" * 64

    def close(self) -> None:
        """记录 owned 会话已关闭。

        输入参数：无。
        输出返回值：无。
        """

        self.closed = True


def _config(qcow2_path: Path) -> OSWorldDockerConfig:
    """构造 component candidate 的合法测试配置。

    输入参数：qcow2_path 为待稳定绑定的源文件。
    输出返回值：固定镜像 digest 与 loopback 端口的配置。
    """

    return OSWorldDockerConfig(
        container_name="paraguibench-artifact-component-attested",
        image="example.invalid/osworld@sha256:" + "b" * 64,
        qcow2_path=qcow2_path,
        server_port=55011,
        vnc_port=58011,
        chromium_port=59232,
    )


def test_component_session_mounts_snapshot_after_source_path_swap(
    tmp_path: Path,
) -> None:
    """确认 source path 换 inode 后 Docker 仍只读已验快照。

    输入参数：tmp_path 为源文件与私有快照目录的隔离根。
    输出返回值：源路径变为恶意字节时，模拟 Docker 读到的
        仍为摘要验证过的原始字节。
    """

    trusted = b"trusted-osworld-component-qcow2"
    source = tmp_path / "Ubuntu.qcow2"
    source.write_bytes(trusted)
    observed: dict[str, object] = {}

    def factory(config: OSWorldDockerConfig) -> _RecordingDockerSession:
        """构造会在 start 换掉源路径的测试会话。

        输入参数：config 应指向私有快照。
        输出返回值：可记录挂载字节的合成会话。
        """

        observed["snapshot"] = config.qcow2_path

        def swap(_config: OSWorldDockerConfig) -> None:
            """换掉原路径并读取 candidate 交给 Docker 的路径。

            输入参数：_config 为私有 snapshot 配置。
            输出返回值：无；将字节记入 observed。
            """

            source.unlink()
            source.write_bytes(b"malicious-replacement")
            observed["mounted"] = _config.qcow2_path.read_bytes()

        return _RecordingDockerSession(config, swap)

    session = OSWorldAttestedDockerSession(
        config=_config(source),
        expected_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        session_factory=factory,
    )

    assert session.start() == "a" * 64
    assert observed["mounted"] == trusted
    assert (
        session.attests_closed_manifest(
            container_image="example.invalid/osworld@sha256:" + "b" * 64,
            extracted_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        )
        is False
    )
    snapshot = observed["snapshot"]
    assert isinstance(snapshot, Path)
    assert snapshot != source
    session.close()
    assert (
        session.attests_closed_manifest(
            container_image="example.invalid/osworld@sha256:" + "b" * 64,
            extracted_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        )
        is False
    )
    assert (
        session.attests_closed_manifest(
            container_image="example.invalid/other@sha256:" + "c" * 64,
            extracted_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        )
        is False
    )
    assert not snapshot.parent.exists()


def test_component_session_rejects_qcow2_not_matching_current_manifest_hash(
    tmp_path: Path,
) -> None:
    """确认 candidate 不能把镜像 A 记录为 manifest 声明的 B。

    输入参数：tmp_path 提供与预期摘要不同的 qcow2 A。
    输出返回值：start 必须抛出固定脱敏错误，且不得调用
        下层 Docker 工厂。
    """

    source = tmp_path / "Ubuntu.qcow2"
    source.write_bytes(b"image-A")
    called = False

    def factory(config: OSWorldDockerConfig) -> _RecordingDockerSession:
        """记录摘要不匹配时是否越过了 Docker 边界。

        输入参数：config 为本不应产生的 snapshot 配置。
        输出返回值：合成会话；正确实现不会调用本函数。
        """

        nonlocal called
        called = True
        return _RecordingDockerSession(config, lambda _config: None)

    session = OSWorldAttestedDockerSession(
        config=_config(source),
        expected_qcow2_sha256=hashlib.sha256(b"image-B").hexdigest(),
        session_factory=factory,
    )

    with pytest.raises(OSWorldQcow2AttestationError) as captured:
        session.start()

    assert str(captured.value) == "WEBMALL_CART_QCOW2_ATTESTATION_INVALID"
    assert called is False


def test_component_session_mounts_snapshot_during_source_content_aba(
    tmp_path: Path,
) -> None:
    """确认源 inode 原地 A→B→A 也不能改变已验挂载字节。

    输入参数：tmp_path 为源文件与私有快照目录的隔离根。
    输出返回值：模拟 Docker 在 ABA 窗口内仍读到 A，
        snapshot 且为私有 0400 独立 inode。
    """

    trusted = b"trusted-osworld-component-qcow2"
    malicious = b"X" * len(trusted)
    source = tmp_path / "Ubuntu.qcow2"
    source.write_bytes(trusted)
    original = source.stat()
    observed: dict[str, object] = {}

    def factory(config: OSWorldDockerConfig) -> _RecordingDockerSession:
        """构造会在 start 对源 inode 执行 ABA 的测试会话。

        输入参数：config 应指向不受源 inode 写入影响的快照。
        输出返回值：可记录挂载字节的合成会话。
        """

        observed["snapshot"] = config.qcow2_path

        def aba(_config: OSWorldDockerConfig) -> None:
            """在同 inode 上完成 A→B→A 并恢复 mtime。

            输入参数：_config 为私有 snapshot 配置。
            输出返回值：无；将字节记入 observed。
            """

            source.write_bytes(malicious)
            observed["mounted"] = _config.qcow2_path.read_bytes()
            source.write_bytes(trusted)
            os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))

        return _RecordingDockerSession(config, aba)

    session = OSWorldAttestedDockerSession(
        config=_config(source),
        expected_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        session_factory=factory,
    )

    assert session.start() == "a" * 64
    assert observed["mounted"] == trusted
    snapshot = observed["snapshot"]
    assert isinstance(snapshot, Path)
    assert snapshot.stat().st_mode & 0o777 == 0o400
    assert (snapshot.stat().st_dev, snapshot.stat().st_ino) != (
        source.stat().st_dev,
        source.stat().st_ino,
    )
    session.close()
