"""PPT-003 正式 pipeline-implicit 资产清单与可信字节边界测试。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from paraguibench.integrations.pipeline_implicit.verified_assets import (
    PPT003_GOLD_MANIFEST_PATH,
    PPT003_INPUT_MANIFEST_PATH,
    PipelineImplicitGoldIntegrityError,
    PipelineImplicitGoldManifestError,
    build_ppt003_asset_manifest_documents,
    check_ppt003_asset_manifest_files,
    load_verified_pipeline_implicit_gold_manifest,
    resolve_verified_pipeline_implicit_gold_bundle,
    serialize_pipeline_implicit_asset_manifest,
)
from paraguibench.runtime.assets import (
    load_asset_manifest,
    verify_asset_directory,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "Operation-FileOperate-BatchOperationPPT-003"
TASK_UID = "e544ee0f-90e6-43a4-9958-6b74e88d94a6"
PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "pipeline-implicit-gold-asset-manifest-v1.schema.json"
)
_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_PPT003_FIXTURE_DIR"


def test_ppt003_canonical_binds_formal_input_and_gold_manifests() -> None:
    """验证 canonical 只引用正式 input/gold，不再回退 legacy prepare。

    输入参数：
        无；读取 PPT-003 canonical task。
    输出返回值：
        无；legacy prepare 必须删除，两份仓库相对 manifest 必须精确绑定，
        且任务身份必须与两份 task-specific manifest 精确一致。
    """

    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{TASK_ID}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))

    assert "prepare_script_path" not in task
    assert task["task_id"] == TASK_ID
    assert task["task_uid"] == TASK_UID
    assert task["asset_manifest"] == PPT003_INPUT_MANIFEST_PATH
    assert task["gold_manifest"] == PPT003_GOLD_MANIFEST_PATH


def test_ppt003_release_entry_hashes_current_canonical_bytes() -> None:
    """验证串行派生后的 release 条目固定 PPT-003 当前 canonical 字节。

    输入参数：
        无；读取 canonical 原始字节与 release-v1 中唯一同 ID 条目。
    输出返回值：
        无；release SHA-256 必须等于 canonical 字节摘要，防止运行时加载
        旧 task 或绕过新 input/gold 身份。
    """

    task_path = REPO_ROOT / "benchmark" / "tasks" / f"{TASK_ID}.json"
    task_bytes = task_path.read_bytes()
    release = json.loads(
        (REPO_ROOT / "benchmark" / "manifests" / "release-v1.json").read_text(
            encoding="utf-8"
        )
    )
    entries = [entry for entry in release["tasks"] if entry["task_id"] == TASK_ID]

    assert len(entries) == 1
    assert entries[0]["sha256"] == hashlib.sha256(task_bytes).hexdigest()


def _fixed_revision_fixture(role: str) -> Path:
    """返回一个角色的 download-only 固定 revision fixture。

    输入参数：
        role：``input`` 或 ``gold``；环境变量沿用 bridge 测试并指向
            gold 目录，input 从其同级目录解析。
    输出返回值：
        存在的 fixture 目录；未显式提供时跳过真实字节纵向测试。
    """

    raw_gold_path = os.environ.get(_FIXTURE_ENVIRONMENT_VARIABLE)
    if raw_gold_path is None:
        pytest.skip(
            f"{_FIXTURE_ENVIRONMENT_VARIABLE} is required for download-only fixture"
        )
    gold_path = Path(raw_gold_path)
    candidate = gold_path if role == "gold" else gold_path.parent / "input"
    if not candidate.is_dir():
        pytest.fail("PPT-003 fixed-revision fixture directory is unavailable")
    return candidate


def test_ppt003_builder_closes_twenty_input_and_thirty_two_gold_files() -> None:
    """验证正式 builder 固定真实 20/32 文件闭集与来源身份。

    输入参数：
        无；builder 读取仓库内 canonical task 的稳定身份。
    输出返回值：
        无；input 必须含 16 张源图和 4 个 PPT，gold 必须额外含
        12 张分类副本并保留 16 个 ``images`` source-copy。
    """

    documents = build_ppt003_asset_manifest_documents(REPO_ROOT)

    assert set(documents) == {
        PPT003_INPUT_MANIFEST_PATH,
        PPT003_GOLD_MANIFEST_PATH,
    }
    input_document = documents[PPT003_INPUT_MANIFEST_PATH]
    assert input_document == {
        "schema_version": 1,
        "asset_set_id": TASK_ID,
        "source": {
            "provider": "huggingface_dataset",
            "repository": "leeLegendary/Parallel_benchmark",
            "revision": PINNED_REVISION,
            "base_path": f"benchmark_dataset/{TASK_UID}",
            "license_status": "unverified",
        },
        "distribution_policy": "download_only",
        "files": input_document["files"],
    }
    assert len(input_document["files"]) == 20
    assert [entry["path"] for entry in input_document["files"]] == sorted(
        (entry["path"] for entry in input_document["files"]),
        key=lambda value: value.encode("utf-8"),
    )
    assert load_asset_manifest(REPO_ROOT / PPT003_INPUT_MANIFEST_PATH).files

    gold_document = documents[PPT003_GOLD_MANIFEST_PATH]
    assert gold_document["manifest_role"] == "gold"
    assert gold_document["task_id"] == TASK_ID
    assert gold_document["task_uid"] == TASK_UID
    assert gold_document["source"]["base_path"] == f"answer_files/{TASK_UID}"
    assert gold_document["license"] == {
        "status": "unverified",
        "spdx_expression": None,
        "evidence_ref": (
            "https://huggingface.co/datasets/leeLegendary/Parallel_benchmark"
        ),
        "distribution": "download_only",
    }
    gold_entries = gold_document["entries"]
    assert len(gold_entries) == 32
    assert sum(entry["path"].startswith("images/") for entry in gold_entries) == 16
    assert (
        sum(
            entry["path"].split("/", maxsplit=1)[0]
            in {"basketball", "soccer", "volleyball", "esport"}
            for entry in gold_entries
        )
        == 12
    )


def test_verified_pipeline_gold_schema_closes_every_object() -> None:
    """验证正式 gold schema 禁止未知字段并要求真实字节元数据。

    输入参数：
        无；读取仓库内 pipeline-implicit 专属 gold manifest schema。
    输出返回值：
        无；顶层与所有 object definition 均为字段闭集，entry 的
        size/SHA/MIME 为必填且不再允许 unverified 占位符。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    definitions = schema["$defs"]
    for definition in definitions.values():
        if isinstance(definition, dict) and definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])
    entry = definitions["entry"]
    assert set(entry["required"]) == {
        "path",
        "size_bytes",
        "sha256",
        "media_type",
    }
    assert entry["properties"]["size_bytes"]["minimum"] == 1
    assert entry["properties"]["sha256"]["pattern"] == "^[0-9a-f]{64}$"


def test_ppt003_formal_manifests_are_deterministic_builder_output() -> None:
    """验证两份落盘正式清单与唯一序列化结果逐字节一致。

    输入参数：
        无；读取仓库内 task-specific input/gold manifest。
    输出返回值：
        无；文件必须来自同一 builder，不能保留 draft 占位元数据或
        脱离已核验固定字节集合。
    """

    documents = build_ppt003_asset_manifest_documents(REPO_ROOT)

    assert check_ppt003_asset_manifest_files(REPO_ROOT) is True
    for relative_path, document in documents.items():
        assert (REPO_ROOT / relative_path).read_bytes() == (
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_strict_gold_loader_accepts_only_the_generated_verified_identity() -> None:
    """验证 production gold loader 把生成字节投影为不可变合同。

    输入参数：
        无；传入专属 gold 的确定性 UTF-8 bytes，不传文件路径。
    输出返回值：
        无；loader 返回固定 task/revision/role 与真实 entry 元数据，
        gold 中 JPEG 是 pipeline 专属合法媒体类型。
    """

    document = build_ppt003_asset_manifest_documents(REPO_ROOT)[
        PPT003_GOLD_MANIFEST_PATH
    ]

    manifest = load_verified_pipeline_implicit_gold_manifest(
        serialize_pipeline_implicit_asset_manifest(document)
    )
    assert manifest.task_id == TASK_ID
    assert manifest.task_uid == TASK_UID
    assert manifest.source_revision == PINNED_REVISION
    assert manifest.distribution_policy == "download_only"
    assert len(manifest.entries) == 32
    assert any(entry.media_type == "image/jpeg" for entry in manifest.entries)


def test_strict_loader_rejects_a_missing_fixed_entry() -> None:
    """验证删去一个固定成员不能伪造较小但“自洽”的正式闭集。

    输入参数：
        无；从有效 input 文档移除一个 entry 后重新序列化。
    输出返回值：
        无；strict loader 必须返回固定、不泄漏成员身份的 manifest 错误。
    """

    document = copy.deepcopy(
        build_ppt003_asset_manifest_documents(REPO_ROOT)[PPT003_GOLD_MANIFEST_PATH]
    )
    document["entries"].pop()

    with pytest.raises(PipelineImplicitGoldManifestError) as captured:
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )

    assert str(captured.value) == "PIPELINE_IMPLICIT_GOLD_MANIFEST_INVALID"


def test_strict_loader_rejects_an_extra_entry() -> None:
    """验证额外成员即使复制合法元数据也不能进入正式闭集。

    输入参数：
        无；向 input 文档增加一条路径不同但字节身份合法的 entry。
    输出返回值：
        无；loader 在生产读取前拒绝整个 manifest。
    """

    document = copy.deepcopy(
        build_ppt003_asset_manifest_documents(REPO_ROOT)[PPT003_GOLD_MANIFEST_PATH]
    )
    extra = copy.deepcopy(document["entries"][0])
    extra["path"] = "images/extra.jpeg"
    document["entries"].append(extra)

    with pytest.raises(PipelineImplicitGoldManifestError):
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_strict_loader_rejects_duplicate_paths_but_allows_gold_digest_reuse() -> None:
    """验证路径必须唯一，而协议要求的 source-copy 内容复用合法。

    输入参数：
        无；先加载原始 gold，再把末项替换为首项以制造重复路径。
    输出返回值：
        无；原始 gold 的 12 对重复 SHA 正常加载，重复 path 的变体拒绝。
    """

    document = copy.deepcopy(
        build_ppt003_asset_manifest_documents(REPO_ROOT)[PPT003_GOLD_MANIFEST_PATH]
    )
    manifest = load_verified_pipeline_implicit_gold_manifest(
        serialize_pipeline_implicit_asset_manifest(document)
    )
    digest_counts: dict[str, int] = {}
    for entry in manifest.entries:
        digest_counts[entry.sha256] = digest_counts.get(entry.sha256, 0) + 1
    assert sum(count == 2 for count in digest_counts.values()) == 12

    document["entries"][-1] = copy.deepcopy(document["entries"][0])
    with pytest.raises(PipelineImplicitGoldManifestError):
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_strict_loader_rejects_portable_casefold_path_collision() -> None:
    """验证大小写折叠后碰撞的成员不能进入跨平台正式清单。

    输入参数：
        无；把一个合法 entry 改成与另一项仅目录大小写不同的路径。
    输出返回值：
        无；loader 必须在任何资产字节解析前拒绝 manifest。
    """

    document = copy.deepcopy(
        build_ppt003_asset_manifest_documents(REPO_ROOT)[PPT003_GOLD_MANIFEST_PATH]
    )
    document["entries"][-1]["path"] = "Images/Unknown-1.jpeg"

    with pytest.raises(PipelineImplicitGoldManifestError):
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )


def test_strict_loader_rejects_non_standard_nan() -> None:
    """验证 Python JSON decoder 默认接受的 NaN 在可信边界失败关闭。

    输入参数：
        无；将一个真实整数大小替换为非标准 JSON ``NaN`` token。
    输出返回值：
        无；loader 抛固定 manifest error，不能将 NaN 带入资源上限逻辑。
    """

    document = copy.deepcopy(
        build_ppt003_asset_manifest_documents(REPO_ROOT)[PPT003_GOLD_MANIFEST_PATH]
    )
    document["entries"][0]["size_bytes"] = float("nan")

    with pytest.raises(PipelineImplicitGoldManifestError):
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )


@pytest.mark.parametrize("location", ("manifest", "source", "license", "entry"))
def test_strict_loader_rejects_unknown_fields_at_every_object_level(
    location: str,
) -> None:
    """验证顶层及所有嵌套 object 均执行字段闭集门禁。

    输入参数：
        location：本例注入未知字段的 object 层级。
    输出返回值：
        无；任一未知字段都使整个 manifest 失败关闭。
    """

    document = copy.deepcopy(
        build_ppt003_asset_manifest_documents(REPO_ROOT)[PPT003_GOLD_MANIFEST_PATH]
    )
    targets = {
        "manifest": document,
        "source": document["source"],
        "license": document["license"],
        "entry": document["entries"][0],
    }
    targets[location]["unknown_field"] = "PRIVATE SENTINEL"

    with pytest.raises(PipelineImplicitGoldManifestError) as captured:
        load_verified_pipeline_implicit_gold_manifest(
            serialize_pipeline_implicit_asset_manifest(document)
        )
    assert "PRIVATE" not in str(captured.value)


def test_strict_loader_rejects_duplicate_json_fields() -> None:
    """验证同名 JSON 字段不会被 decoder 的 last-write-wins 隐藏。

    输入参数：
        无；在有效 bytes 顶层插入值相同的第二个 ``schema_version``。
    输出返回值：
        无；即使两个值相同，loader 仍拒绝有歧义的原始字节。
    """

    document = build_ppt003_asset_manifest_documents(REPO_ROOT)[
        PPT003_GOLD_MANIFEST_PATH
    ]
    payload = serialize_pipeline_implicit_asset_manifest(document).replace(
        b'  "schema_version": 1,',
        b'  "schema_version": 1,\n  "schema_version": 1,',
        1,
    )

    with pytest.raises(PipelineImplicitGoldManifestError):
        load_verified_pipeline_implicit_gold_manifest(payload)


def test_production_gold_resolver_returns_only_manifest_verified_bytes() -> None:
    """验证真实固定 fixture 只有逐文件 size/SHA/MIME 全绿才形成 bundle。

    输入参数：
        无；使用专属 gold manifest bytes 与固定 32 文件 fixture。
    输出返回值：
        无；resolver 仅接受原始 manifest bytes，返回完整 typed bundle，
        所有可供后续 production bridge 读取的 payload 都已核验。
    """

    bundle = resolve_verified_pipeline_implicit_gold_bundle(
        (REPO_ROOT / PPT003_GOLD_MANIFEST_PATH).read_bytes(),
        _fixed_revision_fixture("gold"),
    )

    assert bundle.task_id == TASK_ID
    assert bundle.file_count == 32
    assert bundle.total_bytes > 0
    files = tuple(bundle.iter_files_for_pipeline())
    assert len(files) == 32
    assert all(len(item.read_for_pipeline()) == item.size_bytes for item in files)
    assert "Unknown" not in repr(bundle)


def test_formal_input_reuses_generic_asset_verification_chain() -> None:
    """验证 20 文件 input 无需第二套下载器或缓存协议。

    输入参数：
        无；通用 loader 读取 task-specific input manifest，并用既有
        directory verifier 核验固定 revision 的真实 20 文件 fixture。
    输出返回值：
        无；通用 manifest 的 JPEG/PPTX MIME、size、SHA 与闭集全部通过。
    """

    manifest = load_asset_manifest(REPO_ROOT / PPT003_INPUT_MANIFEST_PATH)
    verification = verify_asset_directory(
        manifest,
        _fixed_revision_fixture("input"),
    )

    assert len(manifest.files) == 20
    assert verification.ok is True


@pytest.mark.parametrize("untrusted_manifest", ({}, "manifest.json", bytearray()))
def test_gold_resolver_rejects_non_bytes_manifest_inputs(
    untrusted_manifest: object,
) -> None:
    """验证 trusted gold 边界不能绕过原始 JSON bytes 严格解析。

    输入参数：
        untrusted_manifest：已解析 object、路径字符串或可变 bytearray。
    输出返回值：
        无；resolver 在读取 gold 文件前抛固定 manifest 错误。
    """

    with pytest.raises(PipelineImplicitGoldManifestError):
        resolve_verified_pipeline_implicit_gold_bundle(
            untrusted_manifest,  # type: ignore[arg-type]
            _fixed_revision_fixture("gold"),
        )


def test_production_gold_resolver_rejects_missing_cache_member(
    tmp_path: Path,
) -> None:
    """验证 manifest 正确但缓存缺一项时不会产生部分 verified bundle。

    输入参数：
        tmp_path：pytest 提供的隔离目录，用于复制 download-only fixture。
    输出返回值：
        无；resolver 仅抛固定完整性错误，不返回剩余 31 个成员。
    """

    cache_root = tmp_path / "gold"
    shutil.copytree(_fixed_revision_fixture("gold"), cache_root)
    next(path for path in cache_root.rglob("*") if path.is_file()).unlink()

    with pytest.raises(PipelineImplicitGoldIntegrityError) as captured:
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / PPT003_GOLD_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert str(captured.value) == "PIPELINE_IMPLICIT_GOLD_INTEGRITY_INVALID"


def test_production_gold_resolver_rejects_extra_cache_member(
    tmp_path: Path,
) -> None:
    """验证缓存多余文件在任何 payload 暴露前严格失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离目录，用于增加敏感哨兵文件。
    输出返回值：
        无；错误文本和表示均不包含额外路径或内容。
    """

    cache_root = tmp_path / "gold"
    shutil.copytree(_fixed_revision_fixture("gold"), cache_root)
    (cache_root / "PRIVATE-extra.bin").write_bytes(b"PRIVATE SENTINEL")

    with pytest.raises(PipelineImplicitGoldIntegrityError) as captured:
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / PPT003_GOLD_MANIFEST_PATH).read_bytes(),
            cache_root,
        )

    assert "PRIVATE" not in repr(captured.value)


def test_production_gold_resolver_rejects_symlinked_cache_ancestor(
    tmp_path: Path,
) -> None:
    """验证 cache_root 任一祖先 symlink 都不能绕过 held-dirfd 边界。

    输入参数：
        tmp_path：pytest 隔离目录；真实 gold 放在 ``real/gold``，调用方
            经 ``alias/gold`` 进入，其中 alias 是目录符号链接。
    输出返回值：
        无；resolver 必须在读取成员前失败，不能只对最终文件使用
        ``O_NOFOLLOW`` 而跟随可替换祖先。
    """

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    shutil.copytree(_fixed_revision_fixture("gold"), real_parent / "gold")
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PipelineImplicitGoldIntegrityError):
        resolve_verified_pipeline_implicit_gold_bundle(
            (REPO_ROOT / PPT003_GOLD_MANIFEST_PATH).read_bytes(),
            alias_parent / "gold",
        )
