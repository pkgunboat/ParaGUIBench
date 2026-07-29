"""OSWorld doctor 一次列出全部部署门禁且不回显敏感值的测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifest,
)
from paraguibench.runtime.assets import load_asset_manifest
from paraguibench.runtime.doctor import (
    OSWorldDoctorConfig,
    inspect_osworld_prerequisites,
)


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
        environment_id="osworld-ubuntu-x86_64",
        extracted_path="Ubuntu.qcow2",
        extracted_sha256=hashlib.sha256(qcow2_content).hexdigest(),
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
        asset_manifest=load_asset_manifest(asset_manifest_path),
        asset_cache_root=cache_root,
        server_port=5101,
        vnc_port=8101,
        api_key_env="PARAGUIBENCH_TEST_API_KEY",
        base_url_env="PARAGUIBENCH_TEST_BASE_URL",
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
    )

    checks = {check.name: check.passed for check in report.checks}
    assert report.ok is False
    assert checks["api_key"] is False
    assert checks["vnc_port"] is False
    assert checks["qcow2_digest"] is True
    assert checks["asset_cache"] is True
    serialized = repr(report)
    assert "private.example.test" not in serialized
    assert str(qcow2_path) not in serialized
