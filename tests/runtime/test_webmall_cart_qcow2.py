"""WebMall Cart reference 的 attempt-owned qcow2 稳定绑定测试。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

import pytest

from paraguibench.integrations.osworld.docker_session import (
    OSWorldDockerConfig,
)
from paraguibench.runtime.webmall_cart_qcow2 import (
    WebMallCartAttestedDockerSession,
    WebMallCartQcow2AttestationError,
)


class _FakeDockerSession:
    """记录实际传给 Docker 的 pinned 路径且不启动容器。"""

    def __init__(
        self,
        config: OSWorldDockerConfig,
        *,
        on_start: Callable[[OSWorldDockerConfig], None],
    ) -> None:
        """保存 pinned config 与启动边界动作。

        输入参数：config 为候选会话生成的新配置；on_start 用于
            在 Docker 解析路径的时刻模拟竞态。
        输出返回值：无。
        """

        self.config = config
        self._on_start = on_start
        self.closed = False

    def start(self) -> str:
        """执行竞态动作并返回合成容器 ID。

        输入参数：无。
        输出返回值：64 位合成容器 ID。
        """

        self._on_start(self.config)
        return "a" * 64

    def close(self) -> None:
        """记录 owned 容器已清理。

        输入参数：无。
        输出返回值：无。
        """

        self.closed = True


def _config(qcow2_path: Path) -> OSWorldDockerConfig:
    """构造只用于 attestation 单元测试的 Docker 配置。

    输入参数：qcow2_path 为待固定源文件。
    输出返回值：固定 digest、loopback 端口的合法配置。
    """

    return OSWorldDockerConfig(
        container_name="paraguibench-cart-reference-attested",
        image="example.invalid/osworld@sha256:" + "b" * 64,
        qcow2_path=qcow2_path,
        server_port=55001,
        vnc_port=58001,
        chromium_port=59222,
    )


def test_attested_session_mounts_pinned_inode_when_source_path_is_swapped(
    tmp_path: Path,
) -> None:
    """验证 doctor 后替换原路径不会改变 Docker 实际获得的 qcow2。

    输入参数：tmp_path 提供原始文件与 attempt-owned pin 目录。
    输出返回值：无；源路径已变为恶意字节，pinned 路径仍是
        已验证内容，close 后临时目录被删除。
    """

    trusted = b"trusted-qcow2-bytes"
    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(trusted)
    observed: dict[str, object] = {}

    def factory(config: OSWorldDockerConfig) -> _FakeDockerSession:
        """构造使原路径换 inode 的无 I/O Docker fake。

        输入参数：config 持有应安全独立的 pinned 路径。
        输出返回值：只记录启动的 fake session。
        """

        observed["pinned_path"] = config.qcow2_path

        def swap_source(_config: OSWorldDockerConfig) -> None:
            """在 Docker start 时替换用户提供的原始路径。

            输入参数：_config 为未修改的 pinned 配置。
            输出返回值：无。
            """

            qcow2_path.unlink()
            qcow2_path.write_bytes(b"malicious-replacement")
            observed["mounted_bytes"] = _config.qcow2_path.read_bytes()

        session = _FakeDockerSession(config, on_start=swap_source)
        observed["session"] = session
        return session

    session = WebMallCartAttestedDockerSession(
        config=_config(qcow2_path),
        expected_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        session_factory=factory,
    )

    assert session.start() == "a" * 64
    pinned_path = observed["pinned_path"]
    assert isinstance(pinned_path, Path)
    assert pinned_path != qcow2_path
    assert observed["mounted_bytes"] == trusted
    assert qcow2_path.read_bytes() == b"malicious-replacement"
    session.close()

    assert observed["session"].closed is True  # type: ignore[union-attr]
    assert not pinned_path.parent.exists()


def test_attested_session_rejects_swap_of_private_pinned_path(
    tmp_path: Path,
) -> None:
    """验证 Docker start 窗口中直接替换 pinned path 也不能产生证据。

    输入参数：tmp_path 提供已验证源文件。
    输出返回值：无；start 抛固定错误、已启动 fake 被清理且
        attempt-owned pin 不残留。
    """

    trusted = b"trusted-qcow2-bytes"
    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(trusted)
    observed: dict[str, object] = {}

    def factory(config: OSWorldDockerConfig) -> _FakeDockerSession:
        """构造在 start 内替换 pinned leaf 的 fake。

        输入参数：config 为 attempt-owned 配置。
        输出返回值：fake session。
        """

        observed["pinned_path"] = config.qcow2_path

        def swap_pin(_config: OSWorldDockerConfig) -> None:
            """用新 inode 替换随机私有 pin。

            输入参数：_config 为 pinned 配置。
            输出返回值：无。
            """

            _config.qcow2_path.unlink()
            _config.qcow2_path.write_bytes(b"malicious-pinned-replacement")

        session = _FakeDockerSession(config, on_start=swap_pin)
        observed["session"] = session
        return session

    session = WebMallCartAttestedDockerSession(
        config=_config(qcow2_path),
        expected_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        session_factory=factory,
    )

    with pytest.raises(WebMallCartQcow2AttestationError) as captured:
        session.start()

    assert str(captured.value) == "WEBMALL_CART_QCOW2_ATTESTATION_INVALID"
    assert observed["session"].closed is True  # type: ignore[union-attr]
    pinned_path = observed["pinned_path"]
    assert isinstance(pinned_path, Path)
    assert not pinned_path.parent.exists()


def test_attested_session_snapshot_isolated_from_source_content_aba(
    tmp_path: Path,
) -> None:
    """验证原路径同 inode 内容 A→B→A 不能影响 Docker 快照。

    输入参数：tmp_path 提供源 qcow2 与 attempt-owned 快照目录。
    输出返回值：无；即使攻击者原地写入同尺寸 B，在 Docker
        读取后恢复 A 和 mtime，Docker 读到的仍必须是隔离的 A。
    """

    trusted = b"trusted-qcow2-bytes"
    malicious = b"X" * len(trusted)
    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(trusted)
    source_before = qcow2_path.stat()
    observed: dict[str, object] = {}

    def factory(config: OSWorldDockerConfig) -> _FakeDockerSession:
        """构造在 Docker 路径解析窗口修改源 inode 的 fake。

        输入参数：config 应指向内容隔离的快照。
        输出返回值：记录 Docker 实际读取字节的 fake session。
        """

        observed["pinned_path"] = config.qcow2_path

        def content_aba(_config: OSWorldDockerConfig) -> None:
            """原地完成同尺寸 A→B→A 并恢复 mtime。

            输入参数：_config 为 Docker 实际获得的快照路径。
            输出返回值：无；将 Docker 窗口中读到的字节写入
                ``observed``。
            """

            qcow2_path.write_bytes(malicious)
            observed["mounted_bytes"] = _config.qcow2_path.read_bytes()
            qcow2_path.write_bytes(trusted)
            os.utime(
                qcow2_path,
                ns=(source_before.st_atime_ns, source_before.st_mtime_ns),
            )

        return _FakeDockerSession(config, on_start=content_aba)

    session = WebMallCartAttestedDockerSession(
        config=_config(qcow2_path),
        expected_qcow2_sha256=hashlib.sha256(trusted).hexdigest(),
        session_factory=factory,
    )

    assert session.start() == "a" * 64
    assert observed["mounted_bytes"] == trusted
    pinned_path = observed["pinned_path"]
    assert isinstance(pinned_path, Path)
    assert (pinned_path.stat().st_dev, pinned_path.stat().st_ino) != (
        qcow2_path.stat().st_dev,
        qcow2_path.stat().st_ino,
    )
    assert pinned_path.stat().st_mode & 0o777 == 0o400
    session.close()
