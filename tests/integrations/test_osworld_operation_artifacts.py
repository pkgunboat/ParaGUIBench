"""OSWorld Operation artifact source 的 host 快照安全边界测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

import paraguibench.integrations.osworld.operation_artifacts as operation_module
from paraguibench.integrations.osworld.operation_artifacts import (
    OSWorldOperationArtifactSource,
    OperationArtifactCaptureError,
    OperationArtifactSnapshot,
)


_TASK_ID = "Operation-FileOperate-CombinationDocs-005"


class _RetryableTemporaryDirectory:
    """模拟 cleanup 可失败并在后续调用重试的临时目录。"""

    def __init__(self, name: Path, *, failures: int) -> None:
        """保存目录名、预置失败次数与调用计数。

        输入参数：
            name：供 snapshot 访问的 host 测试目录。
            failures：cleanup 前若干次应拒绝的次数。
        输出返回值：
            无；构造阶段不删除目录。
        """

        self.name = str(name)
        self.failures = failures
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        """记录 cleanup 并在预置次数内模拟脱敏前的 I/O 失败。

        输入参数：无。
        输出返回值：
            无；超过预置失败次数后标准返回。
        """

        self.cleanup_calls += 1
        if self.cleanup_calls <= self.failures:
            raise OSError("PRIVATE CLEANUP PATH")


class _ManifestController:
    """返回可控 manifest 与文件字节的窄 guest 边界替身。"""

    def __init__(
        self,
        manifest: tuple[tuple[str, int, str], ...],
        files: dict[str, bytes],
    ) -> None:
        """保存未信任 manifest、文件表和单文件 getter 记录。

        输入参数：
            manifest：模拟 guest helper 返回的三元记录元组。
            files：完整 guest 绝对路径到原始字节的测试映射。
        输出返回值：
            无；构造阶段不读文件。
        """

        self.manifest = manifest
        self.files = files
        self.manifest_calls = 0
        self.file_calls: list[str] = []

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        **limits: Any,
    ) -> tuple[tuple[str, int, str], ...]:
        """返回注入的 manifest，并确认 source 提供全部正数上限。

        输入参数：
            guest_directory：source 验证后的 guest shared 根。
            limits：source 提供的文件、深度、大小、响应与超时上限。
        输出返回值：
            构造阶段注入的同一 manifest 元组。
        """

        assert guest_directory == "/home/oai/shared"
        assert limits and all(
            isinstance(value, (int, float)) and value > 0 for value in limits.values()
        )
        self.manifest_calls += 1
        return self.manifest

    def collect_file_bytes(
        self,
        guest_path: str,
        **limits: Any,
    ) -> bytes:
        """记录单文件读取并返回注入字节。

        输入参数：
            guest_path：source 由冻结 shared 根和相对路径拼出的路径。
            limits：单文件原始字节、响应与 timeout 上限。
        输出返回值：
            ``files`` 映射中的原始字节。
        """

        assert limits
        self.file_calls.append(guest_path)
        return self.files[guest_path]


class _ChangingManifestController(_ManifestController):
    """在单文件下载前后返回不同文件树的竞态替身。"""

    def __init__(
        self,
        manifests: tuple[tuple[tuple[str, int, str], ...], ...],
        files: dict[str, bytes],
    ) -> None:
        """保存依次返回的 manifest 与文件内容。

        输入参数：
            manifests：捕获前、下载后的 guest 文件树快照。
            files：首个 manifest 内文件的完整绝对路径字节映射。
        输出返回值：
            无；构造阶段不捕获文件。
        """

        if len(manifests) != 2:
            raise ValueError("changing manifest test double 必须精确提供两次快照")
        super().__init__(manifests[0], files)
        self._manifests = manifests
        self.manifest_calls = 0

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        **limits: Any,
    ) -> tuple[tuple[str, int, str], ...]:
        """按调用次序返回捕获前后的 manifest。

        输入参数：
            guest_directory：已验证的 guest shared 根。
            limits：必须均为正数的捕获资源上限。
        输出返回值：
            第一次返回下载依据，第二次返回稳定性复核依据。
        """

        assert guest_directory == "/home/oai/shared"
        assert limits and all(
            isinstance(value, (int, float)) and value > 0 for value in limits.values()
        )
        manifest = self._manifests[self.manifest_calls]
        self.manifest_calls += 1
        return manifest


def _record(relative_name: str, content: bytes) -> tuple[str, int, str]:
    """为一个合成 guest 文件生成 manifest 三元记录。

    输入参数：
        relative_name/content：相对 shared 根的 POSIX 路径与文件字节。
    输出返回值：
        ``(relative_name, len(content), sha256)``。
    """

    return relative_name, len(content), hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "relative_name",
    (
        "../private-escape.pdf",
        "/private-absolute.pdf",
        "private\ncontrol.pdf",
        "nested//private-empty-component.pdf",
    ),
)
def test_source_rejects_unsafe_manifest_paths_before_any_file_read(
    relative_name: str,
) -> None:
    """验证穿越、绝对、控制字符和空分量在下载前被拒绝。

    输入参数：
        relative_name：当前参数化的恶意 manifest 路径。
    输出返回值：
        无；source 抛脱敏错误，且单文件 getter 调用数为零。
    """

    content = b"private"
    controller = _ManifestController(
        (_record(relative_name, content),),
        {f"/home/oai/shared/{relative_name}": content},
    )

    with pytest.raises(OperationArtifactCaptureError) as captured:
        OSWorldOperationArtifactSource().capture(
            _TASK_ID,
            controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert controller.file_calls == []
    assert "private" not in str(captured.value)


def test_source_rejects_casefold_and_unicode_normalization_collisions() -> None:
    """验证 Linux 上不同、但可在 macOS host 折叠的路径不会合并。

    输入参数：无；manifest 同时含大小写冲突和 NFC/NFD 冲突。
    输出返回值：
        无；冲突必须在任何 host 目录创建或文件下载前失败。
    """

    content = b"same"
    names = (
        "Folder/a.pdf",
        "folder/b.pdf",
        "caf\u00e9/c.pdf",
        "cafe\u0301/d.pdf",
    )
    controller = _ManifestController(
        tuple(_record(name, content) for name in sorted(names)),
        {f"/home/oai/shared/{name}": content for name in names},
    )

    with pytest.raises(OperationArtifactCaptureError, match="路径"):
        OSWorldOperationArtifactSource().capture(
            _TASK_ID,
            controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert controller.file_calls == []


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_source_rejects_file_tree_changes_during_capture(mutation: str) -> None:
    """验证 manifest 与下载之间的缺失或额外文件不会被忽略。

    输入参数：
        mutation：``missing`` 表示文件树后续缺失，
            ``extra`` 表示文件树后续增加未捕获文件。
    输出返回值：
        无；source 必须二次冻结闭集并脱敏 fail closed。
    """

    content = b"stable-content"
    first = (_record("expected.pdf", content),)
    if mutation == "missing":
        second: tuple[tuple[str, int, str], ...] = ()
    else:
        second = first + (_record("private-extra.pdf", b"extra"),)
    controller = _ChangingManifestController(
        (first, second),
        {"/home/oai/shared/expected.pdf": content},
    )

    with pytest.raises(OperationArtifactCaptureError) as captured:
        OSWorldOperationArtifactSource().capture(
            _TASK_ID,
            controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert controller.manifest_calls == 2
    assert controller.file_calls == ["/home/oai/shared/expected.pdf"]
    assert "private-extra" not in str(captured.value)


def test_source_enforces_one_deadline_across_the_whole_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证所有 getter 共用一个总截止时间。

    输入参数：
        monkeypatch：pytest 提供的常量与 monotonic clock 替换器。
    输出返回值：
        无；时间在文件下载后耗尽时，末次 manifest
        不得再启动，整个 capture 必须脱敏 fail closed。
    """

    content = b"deadline-content"
    manifest = (_record("expected.pdf", content),)
    controller = _ManifestController(
        manifest,
        {"/home/oai/shared/expected.pdf": content},
    )
    observed_times = iter((100.0, 100.0, 100.0, 101.1))
    monkeypatch.setattr(
        operation_module,
        "_CAPTURE_TOTAL_TIMEOUT_SECONDS",
        1.0,
    )
    monkeypatch.setattr(
        operation_module.time,
        "monotonic",
        lambda: next(observed_times),
    )

    with pytest.raises(OperationArtifactCaptureError) as captured:
        OSWorldOperationArtifactSource().capture(
            _TASK_ID,
            controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert controller.manifest_calls == 1
    assert controller.file_calls == ["/home/oai/shared/expected.pdf"]
    assert "expected.pdf" not in str(captured.value)


def test_snapshot_close_is_redacted_and_retryable_after_cleanup_failure(
    tmp_path: Path,
) -> None:
    """验证 cleanup 失败不会伪装成 closed 或泄漏宿主路径。

    输入参数：
        tmp_path：pytest 提供的可访问合成快照目录。
    输出返回值：
        无；首次 close 失败后仍可读且可重试，第二次
        成功后才拒绝返回 artifact 根。
    """

    temporary_directory = _RetryableTemporaryDirectory(
        tmp_path,
        failures=1,
    )
    snapshot = OperationArtifactSnapshot(
        task_id=_TASK_ID,
        protocol_id=operation_module.OPERATION_PROTOCOL_ID,
        file_count=0,
        temporary_directory=temporary_directory,  # type: ignore[arg-type]
    )

    with pytest.raises(OperationArtifactCaptureError) as captured:
        snapshot.close()

    assert "PRIVATE" not in str(captured.value)
    assert snapshot.artifact_root() == tmp_path
    assert "closed=False" in repr(snapshot)
    snapshot.close()
    assert temporary_directory.cleanup_calls == 2
    with pytest.raises(OperationArtifactCaptureError, match="已关闭"):
        snapshot.artifact_root()


def test_source_cleanup_failure_does_not_override_redacted_capture_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证捕获失败路径上的 cleanup 异常不向外泄漏。

    输入参数：
        tmp_path：合成临时目录名。
        monkeypatch：将 TemporaryDirectory 替换为 cleanup 恒失败的窄替身。
    输出返回值：
        无；单文件 getter 失败后始终抛固定
        ``OperationArtifactCaptureError``，不保留 cleanup 正文。
    """

    content = b"missing-file-content"
    manifest = (_record("expected.pdf", content),)
    controller = _ManifestController(manifest, {})
    temporary_directory = _RetryableTemporaryDirectory(
        tmp_path,
        failures=10,
    )
    monkeypatch.setattr(
        operation_module.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: temporary_directory,
    )

    with pytest.raises(OperationArtifactCaptureError) as captured:
        OSWorldOperationArtifactSource().capture(
            _TASK_ID,
            controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert "PRIVATE" not in str(captured.value)
    assert temporary_directory.cleanup_calls >= 1
