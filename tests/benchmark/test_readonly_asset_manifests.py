"""十一个 FileSearch Readonly 任务的固定资产合同测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from paraguibench.runtime.assets import (
    TaskAssetMode,
    load_asset_manifest,
    resolve_task_assets,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "readonly-file-search-asset-manifest-v1.schema.json"
)
GENERATOR_PATH = REPO_ROOT / "scripts" / "benchmark" / "readonly_asset_manifests.py"


def _load_task(task_id: str) -> dict[str, object]:
    """读取一份仓库内 canonical task。

    输入参数：
        task_id：待验证的 canonical task ID。
    输出返回值：
        已解析且 task_id 与文件名一致的 JSON object。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(task, dict)
    assert task["task_id"] == task_id
    return task


def _load_generator() -> ModuleType:
    """从仓库路径加载 Readonly 资产 manifest 确定性生成器。

    输入参数：
        无；使用固定 ``GENERATOR_PATH``。
    输出返回值：
        可调用 builder、serializer 与 check 公开函数的模块。
    """

    spec = importlib.util.spec_from_file_location(
        "readonly_asset_manifests",
        GENERATOR_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readonly_asset_schema_is_closed_and_requires_verified_media() -> None:
    """验证首批 Readonly 资产 schema 对来源与文件字段严格闭集。

    输入参数：
        无；读取仓库内 JSON Schema。
    输出返回值：
        无；顶层、source 与 file 均禁止额外字段，且固定
        来源 revision、download-only/unverified 策略与必填 MIME。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    definitions = schema["$defs"]
    for name in ("source", "file"):
        definition = definitions[name]
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])
    source = definitions["source"]["properties"]
    assert source["repository"]["const"] == "leeLegendary/Parallel_benchmark"
    assert source["revision"]["const"] == PINNED_REVISION
    assert source["license_status"]["const"] == "unverified"
    assert set(schema["properties"]["asset_set_id"]["enum"]) == {
        "InformationRetrieval-FileSearch-Readonly-002",
        "InformationRetrieval-FileSearch-Readonly-003",
        "InformationRetrieval-FileSearch-ReadonlyPPT-001",
        "InformationRetrieval-FileSearch-ReadonlyPPT-002",
        "InformationRetrieval-FileSearch-ReadonlyPPT-003",
        "InformationRetrieval-FileSearch-ReadonlyPPT-004",
        "InformationRetrieval-FileSearch-ReadonlyPPT-005",
        "InformationRetrieval-FileSearch-ReadonlyWord-001",
        "InformationRetrieval-FileSearch-ReadonlyWord-002",
        "InformationRetrieval-FileSearch-ReadonlyWord-003",
        "InformationRetrieval-FileSearch-ReadonlyWord-004",
    }
    assert schema["properties"]["distribution_policy"]["const"] == ("download_only")
    assert schema["properties"]["files"]["minItems"] == 1
    assert schema["properties"]["files"]["maxItems"] == 5
    assert definitions["file"]["properties"]["media_type"]["enum"] == [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]


def test_readonly_asset_generator_reproduces_exact_manifest_bytes() -> None:
    """验证生成器离线重建十一份 manifest 且逐字节检测漂移。

    输入参数：
        无；读取 canonical task 与已落盘 manifest，不访问网络。
    输出返回值：
        无；builder 只生成十一份/32 文件，每份通过 runtime loader，
        serializer 字节与仓库一致且 check 返回 ``True``。
    """

    generator = _load_generator()
    documents = generator.build_readonly_asset_manifests(REPO_ROOT)

    assert len(documents) == 11
    assert sum(len(document["files"]) for document in documents.values()) == 32
    for relative_path, document in documents.items():
        manifest_path = REPO_ROOT / relative_path
        assert manifest_path.read_bytes() == (
            generator.serialize_readonly_asset_manifest(document)
        )
        loaded = load_asset_manifest(manifest_path)
        assert loaded.asset_set_id == document["asset_set_id"]
    assert generator.check_readonly_asset_manifests(REPO_ROOT) is True


def test_readonly_003_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 Readonly-003 通过统一 resolver 得到四份固定 DOCX。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-Readonly-003"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/04aad45f-ff78-4508-9403-b60cb8f357ff"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "apology_letter.docx",
            14_990,
            "400151f955e03e31cf6c37919ea82563aff17e726a3ea24ca9338d9941260a1e",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "climite_news.docx",
            14_976,
            "aa66e42c2edda7d7628c7a4f095e1931f55eb0665eb290eecd0663b7c695afac",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "project_update.docx",
            14_565,
            "303f6b79893d37a480005e1547bec88b80644ff087086cfa80af67847fea0b62",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "sci-fi_narrative.docx",
            14_685,
            "627ba5861e92494fbc53c77925660e508534cc94f18a1b161b2a896f3e361005",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]


def test_readonly_ppt_001_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 ReadonlyPPT-001 通过统一 resolver 得到三份固定 PPTX。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-ReadonlyPPT-001"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/06b65a9a-7fe5-4fa0-a8b7-27275d8c29e9"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "ML.pptx",
            37_695,
            "e044e7ebeafd18dbe789a346cb95f2a1a230b62c6330ae19f0e9517921a6f241",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        (
            "The source of AI.pptx",
            37_433,
            "f342a5152b2cfca9cc3117f6b5a681ad56e662fce0e0e3bb84af63ab960da11d",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        (
            "welcome.pptx",
            37_657,
            "77bafb3ac9bc92d5fdd287b2d9987d814b8bb87f90402cc3fbc8b4a4a438c6c8",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ]


def test_readonly_ppt_004_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 ReadonlyPPT-004 通过统一 resolver 得到一份固定 PPTX。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-ReadonlyPPT-004"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/cf706a4b-01a8-40be-92b9-539e77024928"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "mechine learning.pptx",
            97_411,
            "fb688cacaf7bbb1227447fe5e43eeed6c0783d378ca1184d09c3015e5f08f264",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ]


def test_readonly_word_001_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 ReadonlyWord-001 通过统一 resolver 得到四份固定 DOCX。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-ReadonlyWord-001"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/4f870c1f-b01c-4a10-90c3-1e3f0eab0373"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "Fair.docx",
            13_797,
            "d992e2f5ed1d130dd213750d7d09a81b8e4753f5210885bc4be80972a1eb3c54",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "Seminar.docx",
            13_869,
            "a394f35c459a9252a54eae9ab790607d7f64310e7b8ceb967237db341d359cee",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "WorkShop.docx",
            14_081,
            "6920b96732571a3acfe7aab9e86323a806c24b7b83cc1744120646e2e92dae70",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "meeting.docx",
            13_780,
            "1511b37b9807e992c4bf5cc4a03203088dee1b7ca7545ca5b5422c1b5ed78273",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]


def test_readonly_002_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 Readonly-002 通过统一 resolver 得到两份固定 PDF。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-Readonly-002"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/1dcb866c-9a32-452b-a398-59785ddba699"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "Xiaomi Corp_23Q1_ER_ENG_vF_Upload.pdf",
            2_494_687,
            "2d2a956098e98b9718384e63b2e8f395e3dd1e707e1238eddd9f0dcd124c88d3",
            "application/pdf",
        ),
        (
            "announcement.pdf",
            929_454,
            "f9f140951d1a3cf0933bc2513694998f3f9d077a245851359b92b68ae3870f23",
            "application/pdf",
        ),
    ]


def test_readonly_ppt_005_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 ReadonlyPPT-005 通过统一 resolver 得到三份固定 PPTX。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-ReadonlyPPT-005"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/1fad9312-f060-4a1e-8208-2e65f6e950a0"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "164_3.pptx",
            321_773,
            "7b808cd7699e1384a1b55d36a2f75c7446b67f6339d5381477ae980180cf26c5",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        (
            "24_8.pptx",
            327_494,
            "dbad3e3205aaf38eb428868c6f1db659754fa79d9e1bbcc306683c57f640175a",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        (
            "MLA_Workshop_061X_Works_Cited.pptx",
            595_572,
            "15c5875d7731c3459a2b54e0934a53770e8c84fd206917ad280001bd821ff008",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ]


def test_readonly_word_002_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 ReadonlyWord-002 通过统一 resolver 得到四份固定 DOCX。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-ReadonlyWord-002"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/d2783609-b215-457d-99bb-3e3153b286cb"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "Currencies_1.docx",
            802_247,
            "cbff03364a83fb76f2272415b0c98d287bd995e10254a3124cb5b3372799ebaf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "Currencies_2.docx",
            976_969,
            "517c60a51bbca48d9ca19880d6a5aa14632c6289f31ea82572a024a2c5a71584",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "Currencies_3.docx",
            980_835,
            "1bfc57fcac94936842f0b8a476a3b6c90c49dd5574518488cbfb4db7e1cb6423",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "Currencies_4.docx",
            658_012,
            "9a04d0096d9cd1b6d0803b24a3068772084196f198a2f8cfe073d73014d1e147",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]


def test_readonly_word_004_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 ReadonlyWord-004 通过统一 resolver 得到四份固定 PDF。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-ReadonlyWord-004"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/134a34d3-9f52-44f6-b7bd-12945c2479f2"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "paper1.pdf",
            5_296_750,
            "54bcd2dd05dc618849e8a94d8b88fe3eeb37f80e96e200600d38f1f733931678",
            "application/pdf",
        ),
        (
            "paper2.pdf",
            11_947_867,
            "1b31e77fb24d25d7598f2c49e955d12a28b95a6dabad34acdac40f44bfb7a139",
            "application/pdf",
        ),
        (
            "paper3.pdf",
            2_215_244,
            "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
            "application/pdf",
        ),
        (
            "paper4.pdf",
            1_609_513,
            "e9a0d3128767db616085dc0f4e6e455e672e89af823e8ed1282793682787395a",
            "application/pdf",
        ),
    ]


def test_readonly_word_003_resolves_exact_pinned_asset_closed_set() -> None:
    """验证 ReadonlyWord-003 通过统一 resolver 得到五份固定 DOCX。

    输入参数：
        无；读取 canonical task 及其仓库内 manifest。
    输出返回值：
        无；来源、许可策略、文件大小、SHA-256、MIME 与闭集完全匹配。
    """

    task_id = "InformationRetrieval-FileSearch-ReadonlyWord-003"
    task = _load_task(task_id)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    resolved = resolve_task_assets(REPO_ROOT, task)
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert resolved.manifest.asset_set_id == task_id
    assert resolved.manifest.source.repository == ("leeLegendary/Parallel_benchmark")
    assert resolved.manifest.source.revision == PINNED_REVISION
    assert resolved.manifest.source.base_path == (
        "benchmark_dataset/b568284e-3675-4352-a2ff-4a8305c99388"
    )
    assert resolved.manifest.source.license_status == "unverified"
    assert resolved.manifest.distribution_policy == "download_only"
    assert [
        (asset.path, asset.size, asset.sha256, asset.media_type)
        for asset in resolved.manifest.files
    ] == [
        (
            "meeting1.docx",
            13_835,
            "f68244511394ea5dbb4fe63509b8bf5030b3639b9a0633bb3afeb26222535700",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "meeting2.docx",
            13_875,
            "b4fbc3e995099b033772045f3e3ea20cf4e09223a0b32eee5ecb9ccf51027d17",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "meeting3.docx",
            14_027,
            "993bd7bde2e8a6b7c051916de2b682e91af5aa8373f8898df5c30be2378a2fed",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "meeting4.docx",
            13_986,
            "f9b543512fa478524e39834a3c131070c59ce9a9415e5a80b435fcd049384214",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "meeting5.docx",
            13_817,
            "fe67a0b1270f9e0a53e7d49facfe49abc4f4b440963e372594c30cf3d56a9221",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ]
