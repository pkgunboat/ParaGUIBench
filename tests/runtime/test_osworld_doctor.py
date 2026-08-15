"""OSWorld doctor 一次列出全部部署门禁且不回显敏感值的测试。"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
)
from paraguibench.runtime.assets import (
    ResolvedTaskAssets,
    TaskAssetMode,
    load_asset_manifest,
)
from paraguibench.runtime.doctor import (
    OSWorldDoctorConfig,
    inspect_osworld_prerequisites,
)
from paraguibench.runtime.gold_assets import load_gold_asset_manifest
from paraguibench.runtime.osworld_gold import bind_osworld_task_gold


REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_MANIFEST_PATH = (
    REPO_ROOT
    / "benchmark"
    / "gold"
    / "manifests"
    / "Operation-FileOperate-CombinationDocs-015.json"
)


def test_doctor_does_not_require_asset_cache_for_asset_free_task(
    tmp_path: Path,
) -> None:
    """验证零资产任务的 asset_cache 门禁明确通过而不创建目录。

    输入参数：
        tmp_path：pytest 提供的 qcow2 与未使用缓存根目录。
    输出返回值：
        无；所有合成部署条件满足时 doctor 通过，且不存在的资产缓存保持
        不存在。
    """

    qcow2_content = b"asset-free-qcow2"
    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(qcow2_content)
    cache_root = tmp_path / "unused-cache"
    image_manifest = OSWorldImageManifest(
        protocol_ids=("osworld.desktop.v1", "osworld.chrome.v1"),
        environment_id="osworld-ubuntu-x86_64",
        extracted_path="Ubuntu.qcow2",
        extracted_sha256=hashlib.sha256(qcow2_content).hexdigest(),
        materialization_status="verified_reproducible_materialization",
        container_image="example/osworld@sha256:" + "e" * 64,
    )

    def fake_runner(
        command: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        """让两个无副作用 Docker 探针返回成功。

        输入参数：
            command：doctor 生成的 shell-free Docker argv。
        输出返回值：
            returncode=0 的合成进程结果。
        """

        return subprocess.CompletedProcess(command, 0, "", "")

    config = OSWorldDoctorConfig(
        image_manifest=image_manifest,
        qcow2_path=qcow2_path,
        task_assets=ResolvedTaskAssets(
            mode=TaskAssetMode.NONE,
            manifest=None,
        ),
        asset_cache_root=cache_root,
        server_port=5101,
        vnc_port=8101,
        chromium_port=9222,
        api_key_env="PARAGUIBENCH_TEST_API_KEY",
        base_url_env="PARAGUIBENCH_TEST_BASE_URL",
        task_gold=bind_osworld_task_gold("synthetic-asset-free", None),
        gold_cache_root=tmp_path / "unused-gold-cache",
    )

    report = inspect_osworld_prerequisites(
        config,
        command_runner=fake_runner,
        environment={
            "PARAGUIBENCH_TEST_API_KEY": "present-but-never-serialized",
            "PARAGUIBENCH_TEST_BASE_URL": "https://example.test/v1",
        },
        python_version=(3, 12),
        kvm_probe=lambda: True,
        port_probe=lambda _: True,
        dependency_probe=lambda module_name: module_name == "playwright",
    )

    checks = {check.name: check.passed for check in report.checks}
    assert report.ok is True
    assert checks["asset_cache"] is True
    assert checks["gold_cache"] is True
    assert checks["chromium_port"] is True
    assert checks["playwright_dependency"] is True
    assert not cache_root.exists()


def test_doctor_accepts_loopback_http_model_base_url(tmp_path: Path) -> None:
    """验证 doctor 接受回环 HTTP 模型 endpoint，并拒绝公网 HTTP。

    输入参数：
        tmp_path：pytest 提供的合成 qcow2 目录。
    输出返回值：
        无；``model_base_url`` 对 ``http://127.0.0.1`` 通过，对公网 HTTP、
        非法端口、越界端口、端口 0 和前导空白失败。
    """

    qcow2_content = b"loopback-http-qcow2"
    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(qcow2_content)
    image_manifest = OSWorldImageManifest(
        protocol_ids=("osworld.desktop.v1", "osworld.chrome.v1"),
        environment_id="osworld-ubuntu-x86_64",
        extracted_path="Ubuntu.qcow2",
        extracted_sha256=hashlib.sha256(qcow2_content).hexdigest(),
        materialization_status="verified_reproducible_materialization",
        container_image="example/osworld@sha256:" + "a" * 64,
    )
    config = OSWorldDoctorConfig(
        image_manifest=image_manifest,
        qcow2_path=qcow2_path,
        task_assets=ResolvedTaskAssets(
            mode=TaskAssetMode.NONE,
            manifest=None,
        ),
        asset_cache_root=tmp_path / "unused-cache",
        server_port=5101,
        vnc_port=8101,
        chromium_port=9222,
        api_key_env="PARAGUIBENCH_TEST_API_KEY",
        base_url_env="PARAGUIBENCH_TEST_BASE_URL",
        task_gold=bind_osworld_task_gold("synthetic-asset-free", None),
        gold_cache_root=tmp_path / "unused-gold-cache",
    )

    def fake_runner(
        command: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        """让 Docker 探针返回成功。

        输入参数：
            command：doctor 生成的 shell-free Docker argv。
        输出返回值：
            returncode=0 的合成进程结果。
        """

        return subprocess.CompletedProcess(command, 0, "", "")

    accepted = inspect_osworld_prerequisites(
        config,
        command_runner=fake_runner,
        environment={
            "PARAGUIBENCH_TEST_API_KEY": "present-but-never-serialized",
            "PARAGUIBENCH_TEST_BASE_URL": "http://127.0.0.1:8000/v1",
        },
        python_version=(3, 12),
        kvm_probe=lambda: True,
        port_probe=lambda _: True,
        dependency_probe=lambda module_name: module_name == "playwright",
    )
    rejected = inspect_osworld_prerequisites(
        config,
        command_runner=fake_runner,
        environment={
            "PARAGUIBENCH_TEST_API_KEY": "present-but-never-serialized",
            "PARAGUIBENCH_TEST_BASE_URL": "http://api.example.test/v1",
        },
        python_version=(3, 12),
        kvm_probe=lambda: True,
        port_probe=lambda _: True,
        dependency_probe=lambda module_name: module_name == "playwright",
    )
    accepted_checks = {check.name: check.passed for check in accepted.checks}
    rejected_checks = {check.name: check.passed for check in rejected.checks}
    assert accepted_checks["model_base_url"] is True
    assert rejected_checks["model_base_url"] is False
    assert "api.example.test" not in repr(rejected)
    for malformed in (
        "http://localhost:notaport/v1",
        "https://api.example.test:70000/v1",
        " http://127.0.0.1:8000/v1",
        "http://127.0.0.1:0/v1",
    ):
        report = inspect_osworld_prerequisites(
            config,
            command_runner=fake_runner,
            environment={
                "PARAGUIBENCH_TEST_API_KEY": "present-but-never-serialized",
                "PARAGUIBENCH_TEST_BASE_URL": malformed,
            },
            python_version=(3, 12),
            kvm_probe=lambda: True,
            port_probe=lambda _: True,
            dependency_probe=lambda module_name: module_name == "playwright",
        )
        checks = {check.name: check.passed for check in report.checks}
        assert checks["model_base_url"] is False
        assert malformed not in repr(report)


def test_doctor_reports_all_checks_and_only_credential_presence(
    tmp_path: Path,
) -> None:
    """验证 doctor 不短路，并且结果不包含 key、URL 或绝对路径值。

    输入参数：
        tmp_path：pytest 提供的合成 qcow2、资产和 manifest 根目录。
    输出返回值：
        无；缺失 key 与占用端口同时报告，其余 fake 门禁通过。
    """

    qcow2_content = b"synthetic-qcow2"
    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(qcow2_content)
    asset_content = b"asset"
    cache_root = tmp_path / "asset-cache"
    cache_dir = cache_root / "synthetic-assets"
    cache_dir.mkdir(parents=True)
    (cache_dir / "paper.pdf").write_bytes(asset_content)
    asset_manifest_path = tmp_path / "asset-manifest.json"
    asset_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_set_id": "synthetic-assets",
                "source": {
                    "provider": "huggingface_dataset",
                    "repository": "example/assets",
                    "revision": "c" * 40,
                    "base_path": "task",
                    "license_status": "unverified",
                },
                "distribution_policy": "download_only",
                "files": [
                    {
                        "path": "paper.pdf",
                        "size": len(asset_content),
                        "sha256": hashlib.sha256(asset_content).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    image_manifest = OSWorldImageManifest(
        protocol_ids=("osworld.desktop.v1", "osworld.chrome.v1"),
        environment_id="osworld-ubuntu-x86_64",
        extracted_path="Ubuntu.qcow2",
        extracted_sha256=hashlib.sha256(qcow2_content).hexdigest(),
        materialization_status="verified_reproducible_materialization",
        container_image="example/osworld@sha256:" + "d" * 64,
    )

    def fake_runner(
        command: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        """让 Docker daemon 与 image inspect 两项均通过。

        输入参数：
            command：doctor 生成的 shell-free Docker argv。
        输出返回值：
            returncode=0 且不携带敏感输出。
        """

        assert command[0] == "docker"
        return subprocess.CompletedProcess(command, 0, "", "")

    config = OSWorldDoctorConfig(
        image_manifest=image_manifest,
        qcow2_path=qcow2_path,
        task_assets=ResolvedTaskAssets(
            mode=TaskAssetMode.PINNED_DOWNLOAD_MANIFEST,
            manifest=load_asset_manifest(asset_manifest_path),
        ),
        asset_cache_root=cache_root,
        server_port=5101,
        vnc_port=8101,
        chromium_port=9222,
        api_key_env="PARAGUIBENCH_TEST_API_KEY",
        base_url_env="PARAGUIBENCH_TEST_BASE_URL",
        task_gold=bind_osworld_task_gold("synthetic-pinned-asset", None),
        gold_cache_root=tmp_path / "unused-gold-cache",
    )
    report = inspect_osworld_prerequisites(
        config,
        command_runner=fake_runner,
        environment={
            "PARAGUIBENCH_TEST_BASE_URL": "https://private.example.test/v1",
        },
        python_version=(3, 12),
        kvm_probe=lambda: True,
        port_probe=lambda port: port != 8101,
        dependency_probe=lambda _module_name: False,
    )

    checks = {check.name: check.passed for check in report.checks}
    assert report.ok is False
    assert checks["api_key"] is False
    assert checks["vnc_port"] is False
    assert checks["chromium_port"] is True
    assert checks["playwright_dependency"] is False
    assert checks["qcow2_digest"] is True
    assert checks["asset_cache"] is True
    assert checks["gold_cache"] is True
    serialized = repr(report)
    assert "private.example.test" not in serialized
    assert str(qcow2_path) not in serialized


def _private_gold_context(
    tmp_path: Path,
    content: bytes,
) -> tuple[object, Path]:
    """构造与 015 spec 绑定、字节身份可由测试控制的 gold context。

    输入参数：
        tmp_path：pytest 提供的缓存父目录。
        content：写入离线 cache 的合成 BibTeX 字节。
    输出返回值：
        已绑定 task gold，以及尚未填充的绝对 cache 根。
    """

    source_manifest = load_gold_asset_manifest(GOLD_MANIFEST_PATH)
    source_entry = source_manifest.entries[0]
    entry = replace(
        source_entry,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    manifest = replace(source_manifest, entries=(entry,))
    task_gold = bind_osworld_task_gold(
        "Operation-FileOperate-CombinationDocs-015",
        manifest,
        task_uid="9f55fdb6-a749-4170-91a2-bebddd3492d7",
        evaluator_path=(
            "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
        ),
    )
    return task_gold, tmp_path / "gold-cache"


def _passing_doctor_config(
    tmp_path: Path,
    *,
    task_gold: object,
    gold_cache_root: Path,
) -> OSWorldDoctorConfig:
    """构造除 evaluator gold 外全部可由 fake probe 通过的 doctor config。

    输入参数：
        tmp_path：pytest 提供的 qcow2 目录。
        task_gold：待验证的已绑定 evaluator gold context。
        gold_cache_root：doctor 只读检查的私有缓存根。
    输出返回值：
        qcow2 摘要、端口及环境引用均合法的配置。
    """

    qcow2_content = b"gold-doctor-qcow2"
    qcow2_path = tmp_path / "gold-doctor.qcow2"
    qcow2_path.write_bytes(qcow2_content)
    return OSWorldDoctorConfig(
        image_manifest=OSWorldImageManifest(
            protocol_ids=("osworld.desktop.v1", "osworld.chrome.v1"),
            environment_id="osworld-ubuntu-x86_64",
            extracted_path="Ubuntu.qcow2",
            extracted_sha256=hashlib.sha256(qcow2_content).hexdigest(),
            materialization_status="verified_reproducible_materialization",
            container_image="example/osworld@sha256:" + "f" * 64,
        ),
        qcow2_path=qcow2_path,
        task_assets=ResolvedTaskAssets(mode=TaskAssetMode.NONE, manifest=None),
        asset_cache_root=tmp_path / "unused-assets",
        server_port=5102,
        vnc_port=8102,
        chromium_port=9223,
        api_key_env="PARAGUIBENCH_TEST_API_KEY",
        base_url_env="PARAGUIBENCH_TEST_BASE_URL",
        task_gold=task_gold,
        gold_cache_root=gold_cache_root,
    )


def _inspect_with_all_external_probes_passing(
    config: OSWorldDoctorConfig,
):
    """执行 doctor，并让 Docker/KVM/端口/依赖/凭据检查全部通过。

    输入参数：
        config：只让 gold cache 状态变化的 doctor 配置。
    输出返回值：
        完整 ``DoctorReport``。
    """

    def fake_runner(
        command: list[str],
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        """模拟 Docker daemon 与 image 均可用。

        输入参数：
            command：doctor 生成的固定无 shell argv。
        输出返回值：
            returncode=0 的合成结果。
        """

        return subprocess.CompletedProcess(command, 0, "", "")

    return inspect_osworld_prerequisites(
        config,
        command_runner=fake_runner,
        environment={
            "PARAGUIBENCH_TEST_API_KEY": "present-not-serialized",
            "PARAGUIBENCH_TEST_BASE_URL": "https://example.test/v1",
        },
        python_version=(3, 12),
        kvm_probe=lambda: True,
        port_probe=lambda _: True,
        dependency_probe=lambda _: True,
    )


def test_doctor_accepts_private_verified_evaluator_gold(tmp_path: Path) -> None:
    """验证 doctor 在 VM 启动前完整校验 015 的离线 gold。

    输入参数：
        tmp_path：pytest 提供的私有 cache 与 qcow2 目录。
    输出返回值：
        无；0700/0600、size/SHA 匹配时 gold_cache 和总体均通过。
    """

    content = b"@article{doctor, title={Pinned}}\n"
    task_gold, cache_root = _private_gold_context(tmp_path, content)
    manifest_id = task_gold.manifest.manifest_id
    directory = cache_root / manifest_id / "blobs"
    directory.mkdir(parents=True, mode=0o700)
    for private_directory in (cache_root, cache_root / manifest_id, directory):
        os.chmod(private_directory, 0o700)
    target = directory / "0000"
    target.write_bytes(content)
    os.chmod(target, 0o600)

    report = _inspect_with_all_external_probes_passing(
        _passing_doctor_config(
            tmp_path,
            task_gold=task_gold,
            gold_cache_root=cache_root,
        )
    )

    checks = {check.name: check.passed for check in report.checks}
    assert report.ok is True
    assert checks["gold_cache"] is True


def test_doctor_reports_missing_gold_without_creating_cache_or_leaking(
    tmp_path: Path,
) -> None:
    """验证必需 gold 缺失时 doctor 继续列全检查并安全失败。

    输入参数：
        tmp_path：pytest 提供的不存在 cache 与合成 qcow2 目录。
    输出返回值：
        无；只有 gold_cache 失败，缓存不创建，报告不含路径/key/正文。
    """

    content = b"private-bibtex-sentinel"
    task_gold, cache_root = _private_gold_context(tmp_path, content)
    report = _inspect_with_all_external_probes_passing(
        _passing_doctor_config(
            tmp_path,
            task_gold=task_gold,
            gold_cache_root=cache_root,
        )
    )

    checks = {check.name: check.passed for check in report.checks}
    assert report.ok is False
    assert checks["gold_cache"] is False
    assert sum(not check.passed for check in report.checks) == 1
    assert not cache_root.exists()
    rendered = repr(report)
    assert str(cache_root) not in rendered
    assert "private-bibtex-sentinel" not in rendered
    assert "osworld-gold:" not in rendered
