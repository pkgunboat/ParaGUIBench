"""pipeline-implicit 四任务 pinned asset/gold 草案的仓库级合同测试。"""

from __future__ import annotations

import json
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "pipeline-implicit-asset-manifest-v1.schema.json"
)
ASSET_MANIFEST_PATH = (
    REPO_ROOT
    / "benchmark"
    / "assets"
    / "manifests"
    / "pipeline-implicit-input-v1.draft.json"
)
GOLD_MANIFEST_PATH = (
    REPO_ROOT
    / "benchmark"
    / "gold"
    / "manifests"
    / "pipeline-implicit-gold-v1.draft.json"
)
PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
TASK_UIDS = {
    "Operation-FileOperate-BatchOperationExcel-008": (
        "1c73128f-a5ef-4a97-97ce-ef427d6d46b4"
    ),
    "Operation-FileOperate-BatchOperationPPT-003": (
        "e544ee0f-90e6-43a4-9958-6b74e88d94a6"
    ),
    "Operation-FileOperate-CombinationDocs-002": (
        "6bf5b1c9-a2a2-4901-bbe3-631a33da45e8"
    ),
    "Operation-FileOperate-SearchAndWrite-008": (
        "65a4848d-b4b2-4173-8308-a0213fdafbd0"
    ),
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _load_json(path: Path) -> dict[str, object]:
    """读取一份仓库内 UTF-8 JSON object。

    输入参数：
        path：待核对 schema 或 manifest 路径。
    输出返回值：
        解析后的顶层 JSON object。
    """

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_pipeline_implicit_schema_is_closed_and_models_unverified_metadata() -> None:
    """验证专属 schema 对未核验 size/media type 显式失败关闭。

    输入参数：
        无；读取仓库内固定 schema。
    输出返回值：
        无；顶层与所有 object definition 均为字段闭集，且
        ``size``/``media_type`` 共用 verified/unverified 判别联合。
    """

    schema = _load_json(SCHEMA_PATH)

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    for value in definitions.values():
        if isinstance(value, dict) and value.get("type") == "object":
            assert value["additionalProperties"] is False
            assert set(value["required"]) == set(value["properties"])
    verification = definitions["verification"]
    assert verification["oneOf"][0]["properties"]["status"]["const"] == "verified"
    assert verification["oneOf"][1]["properties"]["status"]["const"] == "unverified"
    assert verification["oneOf"][1]["properties"]["value"]["type"] == "null"


def test_pipeline_implicit_drafts_pin_revision_and_do_not_guess_metadata() -> None:
    """验证 input/gold 草案固定远程字节且诚实标记证据缺口。

    输入参数：
        无；读取两份专属 draft manifest。
    输出返回值：
        无；四任务均出现且绑定固定 HF commit/SHA-256，
        未实测的 size/media type 精确为 ``unverified`` 与 ``null``。
    """

    manifests = (
        ("input", "benchmark_dataset", _load_json(ASSET_MANIFEST_PATH)),
        ("gold", "answer_files", _load_json(GOLD_MANIFEST_PATH)),
    )
    for role, source_prefix, manifest in manifests:
        assert manifest["schema_version"] == 1
        assert manifest["manifest_role"] == role
        assert manifest["draft_status"] == "metadata_unverified"
        assert manifest["distribution_policy"] == "download_only"
        tasks = manifest["tasks"]
        assert isinstance(tasks, list)
        assert {task["task_id"] for task in tasks} == set(TASK_UIDS)
        for task in tasks:
            task_id = task["task_id"]
            uid = TASK_UIDS[task_id]
            source = task["source"]
            assert source == {
                "provider": "huggingface_dataset",
                "repository": "leeLegendary/Parallel_benchmark",
                "revision": PINNED_REVISION,
                "base_path": f"{source_prefix}/{uid}",
            }
            assert task["license"] == {
                "status": "unverified",
                "spdx_expression": None,
                "evidence_ref": (
                    "https://huggingface.co/datasets/leeLegendary/Parallel_benchmark"
                ),
                "distribution": "download_only",
            }
            entries = task["entries"]
            assert isinstance(entries, list) and entries
            paths = [entry["path"] for entry in entries]
            assert paths == sorted(paths, key=lambda value: value.encode("utf-8"))
            assert len(paths) == len(set(paths))
            for entry in entries:
                assert SHA256_PATTERN.fullmatch(entry["sha256"])
                assert entry["size"] == {"status": "unverified", "value": None}
                assert entry["media_type"] == {
                    "status": "unverified",
                    "value": None,
                }
