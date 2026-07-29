"""OSWorld 固定镜像 manifest 的解析与未验证摘要门禁测试。"""

from __future__ import annotations

import json
from pathlib import Path

from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest,
)


def test_image_manifest_preserves_unverified_state_until_digest_is_fixed(
    tmp_path: Path,
) -> None:
    """验证 archive 摘要不被误当成解压后 qcow2 摘要。

    输入参数：
        tmp_path：pytest 提供的合成 manifest 目录。
    输出返回值：
        无；extracted digest 为 null 时 manifest 可审计但不可 live run。
    """

    path = tmp_path / "image-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment_id": "osworld-ubuntu-x86_64",
                "vm_archive": {
                    "sha256": "a" * 64,
                },
                "extracted_image": {
                    "path": "Ubuntu.qcow2",
                    "sha256": None,
                    "status": "must_verify_before_live_run",
                },
                "container": {
                    "image": "example/osworld@sha256:" + "b" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    manifest = load_osworld_image_manifest(path)

    assert manifest.extracted_sha256 is None
    assert manifest.live_run_ready is False
    assert manifest.container_image.endswith("b" * 64)
