"""BatchOperation Office 固定输入资产的仓库级合同测试。"""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from types import ModuleType

import pytest

from paraguibench.runtime.assets import (
    TaskAssetMode,
    resolve_task_assets,
    verify_asset_directory,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
# Excel-002 初始资产 20260726 重制后的独立发布 revision。
EXCEL002_PINNED_REVISION = "b5f29e9cb725c80973af55f97b12fd279f066e3a"
XLANG_PINNED_REVISION = "711e0811642364e7aa8f10a8918367d0b626d578"
PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
JPEG_MEDIA_TYPE = "image/jpeg"
TEXT_MEDIA_TYPE = "text/plain"
MARKDOWN_MEDIA_TYPE = "text/markdown"
CSV_MEDIA_TYPE = "text/csv"
HTML_MEDIA_TYPE = "text/html"
GENERATOR_PATH = (
    REPO_ROOT / "scripts" / "benchmark" / "batch_operation_office_assets.py"
)
RUNTIME_SUPPORT_GENERATOR_PATH = (
    REPO_ROOT / "scripts" / "benchmark" / "runtime_support_manifest.py"
)
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "batch-operation-office-asset-manifest-v1.schema.json"
)
MIGRATED_TASK_IDS = {
    "Operation-FileOperate-BatchOperation-001",
    "Operation-FileOperate-BatchOperationExcel-001",
    "Operation-FileOperate-BatchOperationExcel-002",
    "Operation-FileOperate-BatchOperationExcel-003",
    "Operation-FileOperate-BatchOperationExcel-004",
    "Operation-FileOperate-BatchOperationExcel-005",
    "Operation-FileOperate-BatchOperationExcel-006",
    "Operation-FileOperate-BatchOperationExcel-007",
    "Operation-FileOperate-BatchOperationExcel-009",
    "Operation-FileOperate-BatchOperationPPT-001",
    "Operation-FileOperate-BatchOperationPPT-002",
    "Operation-FileOperate-BatchOperationWord-001",
    "Operation-FileOperate-BatchOperationWord-002",
    "Operation-FileOperate-BatchOperationWord-003",
    "Operation-FileOperate-BatchOperationWord-004",
    "Operation-FileOperate-BatchOperationWord-005",
    "Operation-FileOperate-BatchOperationWord-006",
    "Operation-FileOperate-BatchOperationWord-007",
    "Operation-FileOperate-BatchOperationWord-008",
    "Operation-FileOperate-BatchOperationWord-009",
    "Operation-FileOperate-BatchOperationWord-010",
    "Operation-FileOperate-BatchOperationWord-011",
    "Operation-FileOperate-BatchOperationWord-012",
    "Operation-FileOperate-CombinationDocs-001",
    "Operation-FileOperate-CombinationDocs-003",
    "Operation-FileOperate-CombinationDocs-004",
    "Operation-FileOperate-CombinationDocs-005",
    "Operation-FileOperate-CombinationDocs-006",
    "Operation-FileOperate-CombinationDocs-007",
    "Operation-FileOperate-CombinationDocs-008",
    "Operation-FileOperate-SearchAndWrite-002",
    "Operation-FileOperate-SearchAndWrite-004",
    "Operation-FileOperate-SearchAndWrite-006",
    "Operation-FileOperate-SearchAndWrite-007",
}
_COMBINATION_004_007_NON_ASSET_SHA256 = {
    "Operation-FileOperate-CombinationDocs-004": (
        "78bf6e0b29bed1f61d7ef74d8cdcdff97cbbb9aafddaf9ffcb6f3e4ae77380b5"
    ),
    "Operation-FileOperate-CombinationDocs-005": (
        "6556cead88fc91a2f09d293893f201e9681884b6ece46d37e9e359093a560d4f"
    ),
    "Operation-FileOperate-CombinationDocs-006": (
        "94e2413db42bfd04b675761d8e9904710eea3d6f817bf91ea39dc3960456b455"
    ),
    "Operation-FileOperate-CombinationDocs-007": (
        "cff423d27a84016e8b4c785b336e2a43e42b9e592cdb46ff26b82702aff9e91b"
    ),
}
_LAST_FOUR_NON_ASSET_SHA256 = {
    "Operation-FileOperate-BatchOperationWord-003": (
        "7f1ba0270ff7d426f9aacb813aa013dd2a8e2b47246ac1e07a9c24b17be780af"
    ),
    "Operation-FileOperate-BatchOperationWord-012": (
        "2c1e9f692b60a3c5f83a81db5670be3658f20032a8648ceb759fb680512413c7"
    ),
    "Operation-FileOperate-SearchAndWrite-004": (
        "b6cfcf13d1cc734bd6f5f1fa1c81b641647275021c5869eca1e396b2017e2caf"
    ),
    "Operation-FileOperate-SearchAndWrite-007": (
        "225d9f8c10b1b9516e3f9a4bbb8bf9eb00277b5377edc1bbb3b40277b6ed9588"
    ),
}
_LAST_FOUR_RULE_SET_SHA256 = {
    "Operation-FileOperate-BatchOperationWord-003": (
        "67d0fd311be4744e3de9cb4a7f58421f20d9176eb73df8680604ccdeaed97053"
    ),
    "Operation-FileOperate-BatchOperationWord-012": (
        "820b6ad7d13ed6ed4d00e3368ba97b303b76de7cfe4f1439947c0f3b5bb8266b"
    ),
    "Operation-FileOperate-SearchAndWrite-004": (
        "17395aba1543a92c7f2359179b43abbfc618f33231c420fa807d688f904b3ae1"
    ),
    "Operation-FileOperate-SearchAndWrite-007": (
        "f479853597b4b47065c072c640e81b0d03a45b4b4c486b6ef49fb902ef5f3db7"
    ),
}


def test_word_003_resolves_only_three_original_documents() -> None:
    """验证地名高亮任务只解析三份原始 DOCX。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言三份原始文档的路径、大小、SHA-256 与 MIME
        精确固定，且同目录的三份 ``*_answer.docx`` 物理不可达。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-003"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert "prepare_exclude_patterns" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.repository == "leeLegendary/Parallel_benchmark"
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/c36d8396-4dc7-4390-a661-9bb8c54bee9f"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "test3_txt1.docx",
            13_832,
            "166f10b282d16f2d43f2ec9e08a4e64e7d407b7135bedb6cda871cafa1388fbb",
            DOCX_MEDIA_TYPE,
        ),
        (
            "test3_txt2.docx",
            13_725,
            "be1ea1ac473b75cb1b93c6c09df991edf153d48e604527f32574b8e2ab7f21d9",
            DOCX_MEDIA_TYPE,
        ),
        (
            "test3_txt3.docx",
            13_736,
            "1f3521b2d337c2ef24a97e9c900d223bfee658553dbacef2abfa348146089468",
            DOCX_MEDIA_TYPE,
        ),
    ]
    assert all("_answer" not in entry.path for entry in manifest.files)


def test_word_012_resolves_four_pinned_documents() -> None:
    """验证缩写扩展任务只解析四份固定 DOCX。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言四份文档的路径、大小、SHA-256 与 MIME
        精确匹配固定 Lee revision 下的输入闭集。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-012"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.repository == "leeLegendary/Parallel_benchmark"
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/0857689f-8976-49a3-9314-d2b194f9d629"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Clinical Procedure.docx",
            13_971,
            "ccbec2ce1c0ea1df920f08676d3b9bf42b9397543b0d013b8a0f5416cfc40e08",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Hardware Review.docx",
            13_998,
            "2fdde89b1789626f2e71826b1a0acf1260a54c620597273dcf30d6fb7f53223a",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Infrastructure Log.docx",
            14_071,
            "51378bf4bb9058631f40a155226d7403425166cca1740aace5d016656943e1e0",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Security Protocol.docx",
            13_934,
            "02718839e2eb6681c092ff1b2347eb0ce83047772332890f0bd9a435c94ca1ad",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_search_and_write_004_resolves_only_public_input_workbook() -> None:
    """验证会议信息补全任务只解析公开输入工作簿。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言 Lee ``benchmark_dataset`` 中的唯一 XLSX 输入
        被固定，独立 ``answer_files`` 工作簿不可达。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-004"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.repository == "leeLegendary/Parallel_benchmark"
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/19ed62a3-9df0-4879-a685-0681acc1c708"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Conferences_details.xlsx",
            9_019,
            "c8f86d189f80c9d74281d657b42b65936f64b008abd62edb3e3650f2333bb9c5",
            XLSX_MEDIA_TYPE,
        )
    ]
    assert all("answer" not in entry.path.casefold() for entry in manifest.files)


def test_search_and_write_007_resolves_only_xlang_input_workbook() -> None:
    """验证会议城市任务只解析 xlang 固定输入工作簿。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言来源精确绑定 xlang 仓库与固定 revision，
        且同目录 ``ConferenceCity Gold.xlsx`` 物理不可达。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-007"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.repository == "xlangai/ubuntu_osworld_file_cache"
    assert manifest.source.revision == XLANG_PINNED_REVISION
    assert manifest.source.base_path == (
        "multi_apps/6f4073b8-d8ea-4ade-8a18-c5d1d5d5aa9a"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Conference.xlsx",
            9_235,
            "955f438f95a176a5d8e96ed3ec32ac11924ad44dc68c7e4d2480f10fe4fd4bab",
            XLSX_MEDIA_TYPE,
        )
    ]
    assert all("gold" not in entry.path.casefold() for entry in manifest.files)


@pytest.mark.parametrize(
    ("task_id", "forbidden_path"),
    (
        (
            "Operation-FileOperate-BatchOperationWord-003",
            "test3_txt1_answer.docx",
        ),
        (
            "Operation-FileOperate-SearchAndWrite-004",
            "answer_files/Conferences_details.xlsx",
        ),
        (
            "Operation-FileOperate-SearchAndWrite-007",
            "ConferenceCity Gold.xlsx",
        ),
    ),
)
def test_last_four_cache_rejects_answer_or_gold_injection(
    tmp_path: Path,
    task_id: str,
    forbidden_path: str,
) -> None:
    """验证本批 manifest 把 answer/gold 注入判为 host cache 多余文件。

    输入参数：
        tmp_path：pytest 提供的隔离缓存目录。
        task_id：Word-003、SearchAndWrite-004 或 -007 的 canonical ID。
        forbidden_path：公开来源中必须物理排除的 answer/gold 路径。
    输出返回值：
        无；公开闭集验证必须显式报告注入路径，不得
        仅依赖 manifest 列表的表面排除。
    """

    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)
    manifest = resolved.manifest
    assert manifest is not None
    injected = tmp_path / forbidden_path
    injected.parent.mkdir(parents=True, exist_ok=True)
    injected.write_bytes(b"forbidden-evaluator-data")

    verification = verify_asset_directory(manifest, tmp_path)

    assert verification.ok is False
    assert verification.unexpected == (forbidden_path,)


def _load_generator() -> ModuleType:
    """从独立脚本路径加载确定性 manifest generator。

    输入参数：
        无；脚本路径由仓库根确定。
    输出返回值：
        已执行且可调用公开 builder 的模块。
    """

    spec = importlib.util.spec_from_file_location(
        "paraguibench_batch_operation_office_assets",
        GENERATOR_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_runtime_support_generator() -> ModuleType:
    """从独立脚本加载 runtime-support 确定性生成公共接口。

    输入参数：
        无；脚本路径由仓库根固定。
    输出返回值：
        已执行的 runtime-support generator 模块。
    """

    spec = importlib.util.spec_from_file_location(
        "paraguibench_batch_office_runtime_support",
        RUNTIME_SUPPORT_GENERATOR_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _canonical_task(task_id: str) -> dict[str, object]:
    """读取一份仓库 canonical task。

    输入参数：
        task_id：文件名与内部身份必须一致的任务 ID。
    输出返回值：
        解析后的 JSON object。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(task, dict)
    assert task["task_id"] == task_id
    return task


def _isolated_runtime_repository(tmp_path: Path) -> Path:
    """构造带当前三十四项固定输入 canonical SHA 的隔离投影仓库。

    输入参数：
        tmp_path：pytest 提供的单测试临时目录。
    输出返回值：
        已复制 benchmark、当前 ``paraguibench`` Python 闭集与 OSWorld
        image manifest，并机械同步三十四项 canonical task SHA
        的隔离仓库根；专用 pipeline receipt 显式置空，使这个
        Office 资产测试夹具不借用或伪造真实组件证据。
    """

    isolated_root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "benchmark", isolated_root / "benchmark")
    pipeline_allowlist = (
        isolated_root
        / "benchmark/provenance/pipeline-implicit-component-receipt-allowlist-v1.json"
    )
    pipeline_allowlist.write_text(
        '{"receipts": {}, "schema_version": 1}\n',
        encoding="utf-8",
    )
    pipeline_receipt_root = (
        isolated_root / "benchmark/provenance/pipeline-implicit-component-receipts"
    )
    if pipeline_receipt_root.exists():
        shutil.rmtree(pipeline_receipt_root)
    shutil.copytree(
        REPO_ROOT / "src" / "paraguibench",
        isolated_root / "src" / "paraguibench",
    )
    (isolated_root / "environments" / "osworld").mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "environments" / "osworld" / "image-manifest.json",
        isolated_root / "environments" / "osworld" / "image-manifest.json",
    )
    release_path = isolated_root / "benchmark" / "manifests" / "release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    for entry in release["tasks"]:
        if entry["task_id"] in MIGRATED_TASK_IDS:
            entry["sha256"] = hashlib.sha256(
                (isolated_root / entry["path"]).read_bytes()
            ).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return isolated_root


def test_batch_operation_001_resolves_three_pinned_jpeg_inputs() -> None:
    """验证山峰重命名任务只解析固定的三张 JPEG 输入。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言 legacy URL 已消失，且路径、大小、SHA-256、MIME
        及 `unverified`/`download_only` 边界精确匹配。
    """

    task_id = "Operation-FileOperate-BatchOperation-001"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/4b987de4-a022-4078-8f50-8f34a39115e6"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "picture1.jpg",
            214_237,
            "96a704cf18e70183fe3f785e33fdd0a9459f7926357d41ed6866c403c7bce70d",
            JPEG_MEDIA_TYPE,
        ),
        (
            "picture2.jpg",
            44_543,
            "a37387c649a322536835366b86231ac2a6e4e704529ecb5240c9a7e29e69738c",
            JPEG_MEDIA_TYPE,
        ),
        (
            "picture3.jpg",
            927_632,
            "6962e09568bd9c9371a3058adc32866ed702ec2007aa93e141d1e8e1eee9e170",
            JPEG_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_001_resolves_five_pinned_workbooks() -> None:
    """验证批量转 HTML 任务只解析五份固定 XLSX 输入。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言五份工作簿的路径、大小、SHA-256 与 MIME
        精确匹配固定 revision 的目录闭集。
    """

    task_id = "Operation-FileOperate-CombinationDocs-001"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/c2ec79c0-bb7c-4f45-b5e5-437d15d518cb"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "KFC_Monthly_Data.xlsx",
            5_849,
            "4d9bcff171a5ae61bdb6b5c6b2b16a3d6fcb9af09b3ea639049b2c5457b68e1a",
            XLSX_MEDIA_TYPE,
        ),
        (
            "McDonalds_Monthly_Data.xlsx",
            5_858,
            "7c527377555479618e964962b756a7028564ed059f9273fbd16526b2170a6596",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Mixue_Monthly_Data.xlsx",
            5_866,
            "e7f7bd52d195f878fc94c3845c10acef0f1c0e570afdd9de0a342212cf2e19d2",
            XLSX_MEDIA_TYPE,
        ),
        (
            "PizzaHut_Monthly_Data.xlsx",
            5_859,
            "d7c9ce0987a9c2b829d9943ead8894099b3b9664aeec4e2360b1bac3896750a2",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Subway_Monthly_Data.xlsx",
            5_849,
            "0aeb94ba9eecf8135c6cfda2f83e8c7d9f4e40b102431a8c55e4c315cfd4f898",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_003_resolves_only_authoritative_input_files() -> None:
    """验证 003 只绑定正式输入，且不把损坏的 answer PPT 当作 gold。

    输入参数：
        无；通过公开 ``resolve_task_assets`` 读取 canonical 与正式 manifest。
    输出返回值：
        无；固定来源必须包含三份 XLSX 与一份输入 PPTX，canonical 不得
        保留 legacy URL、排除规则或 gold manifest。
    """

    task_id = "Operation-FileOperate-CombinationDocs-003"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert "prepare_exclude_patterns" not in task
    assert "gold_manifest" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/2654f880-dd6b-4f8c-9f88-aebe2bfa51be"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "McDonalds_Monthly_Data.xlsx",
            9_545,
            "ce00b8df3c48ebb8711a477af2de10053affe0e4a2327c485e8d93ea6ad86e5d",
            XLSX_MEDIA_TYPE,
        ),
        (
            "McDonalds_powerpoint_report.pptx",
            41_099,
            "c30c3cfeee0c32dd80ea06d54f36d46237af325f4491c975d6d2464b0d08fcc0",
            PPTX_MEDIA_TYPE,
        ),
        (
            "store1.xlsx",
            9_258,
            "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store2.xlsx",
            9_278,
            "fe5bbc48c80cec38568b71a42508cb9df83a6c5b6388701445f1cf4170e3d1d8",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_008_resolves_docx_only_authoritative_inputs() -> None:
    """验证 008 不补造 PPTX，只固定原始 DOCX、规则与主数据输入。

    输入参数：
        无；通过公开 ``resolve_task_assets`` 读取 canonical 与正式 manifest。
    输出返回值：
        无；固定闭集必须精确为三份 DOCX、命名规则 TXT 和项目 XLSX，
        不得声明 gold、legacy 排除规则或任何合成 PPTX。
    """

    task_id = "Operation-FileOperate-CombinationDocs-008"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert "prepare_exclude_patterns" not in task
    assert "gold_manifest" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/3f600f5d-a835-4c59-9fae-b9139365d03e"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "GUI Benchmark Study.docx",
            13_779,
            "a24dcc85a88e547af1cc4753f34deea75f5a67c4a1f5e50a7162601d66b6cd09",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Multi Modal Agent.docx",
            13_762,
            "5eb752b06f039498aa1e5061e078e2b58ca600e0e3cadfe10c60df4a0cab6ebe",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Naming_rules.txt",
            911,
            "dba136f226431c75e2d9b5ad2cf580d86dccb318b7a5e15a71cc0797fe9668c3",
            TEXT_MEDIA_TYPE,
        ),
        (
            "Parallel Execution.docx",
            13_771,
            "ed8759887f7f12b04981dc6bc713e0ecc32eb81696de5bfdc2ebb40548127663",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Project_Information.xlsx",
            8_899,
            "26b97f507b0bf0958b9bb9dd9c2fe0a221312489bdc5f6dced56d03ddce39c21",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_004_resolves_pinned_report_and_presentation() -> None:
    """验证跨文档比对任务只解析固定 DOCX 与 PPTX 输入。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言 legacy URL 已消失，且两个跨格式文件的路径、
        大小、SHA-256、后缀 MIME 与 download-only 边界精确匹配。
    """

    task_id = "Operation-FileOperate-CombinationDocs-004"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/04a0b25e-f726-40a0-a88d-69bbf538f634"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "McDonald_finacial_report.docx",
            14_355,
            "ef78a03c87b452e6c29c1e1fc317ee375fff311b5b7cc8b38b84e0f49ddf10a6",
            DOCX_MEDIA_TYPE,
        ),
        (
            "McDonalds_powerpoint_report.pptx",
            217_737,
            "997c1a757865abba05c568fa4249629905ae75cccc48d9837225f23c486559ea",
            PPTX_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_005_resolves_five_suffix_typed_documents() -> None:
    """验证批量导出 PDF 任务只解析五份固定源文档。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言 TXT/Markdown/CSV/HTML/DOCX 的路径、字节身份
        和明确后缀 MIME 与公开固定 revision 完全一致。
    """

    task_id = "Operation-FileOperate-CombinationDocs-005"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/58870403-ea44-4f4c-8941-b2a57f170cd1"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Business_Report.txt",
            1_175,
            "f9c53ba0b46d5eb4f2141c0f7ef21f83e2e3820fd0220155082a37803031f1a0",
            TEXT_MEDIA_TYPE,
        ),
        (
            "Development_Guide.md",
            3_147,
            "7cf1463ce297ba1cabcd07c5b79fc132e72810c2dc6a278abb3fbf7d139eb678",
            MARKDOWN_MEDIA_TYPE,
        ),
        (
            "Employee_Directory.csv",
            4_274,
            "7231ba8f905a57726ef5ebbd722be4308c31a776572458cd93ca36c367178ba4",
            CSV_MEDIA_TYPE,
        ),
        (
            "Product_Catalog.html",
            3_228,
            "b4aab3be17aa747ae6d4ed0fe52909e75b7d43843541d493e2073de9df02c346",
            HTML_MEDIA_TYPE,
        ),
        (
            "Training_Program.docx",
            37_740,
            "d71ba36cf46912b1c708810bbb85129f974a484d522af32e48e5ff2b84ace514",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_006_resolves_cue_document_and_two_presentations() -> None:
    """验证超链接提示任务只解析一份 DOCX 与两份 PPTX。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言三文件严格闭集的路径、大小、SHA-256、MIME
        及固定来源 UID 都与匿名下载字节一致。
    """

    task_id = "Operation-FileOperate-CombinationDocs-006"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/d5999c0f-ff61-476d-8e98-9c5f1b91fed9"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Conference.pptx",
            37_017,
            "f3d71a2212039c93928883a1ce059679d29db9f34670b6d28e64868a596d2803",
            PPTX_MEDIA_TYPE,
        ),
        (
            "Eval_framework.docx",
            14_364,
            "737f13011a878a92740c8781d370dfbb3505509d8c0b1b770a5f974cea9e7f66",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Presentation_Strategy.pptx",
            38_013,
            "e461cb2eb21edeb7f279b9643d304d6115820fca42d2d6bc146085cb631d98fc",
            PPTX_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_007_resolves_six_theme_classification_inputs() -> None:
    """验证主题分类任务只解析六份固定 Office 输入。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言两份 DOCX、一份 XLSX 与三份 PPTX 的确定性
        路径顺序、字节身份、后缀 MIME 与 download-only 边界。
    """

    task_id = "Operation-FileOperate-CombinationDocs-007"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/eebc7ed2-5c7d-4df5-ab71-b53040167536"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Conference.pptx",
            38_079,
            "38877fc03a1c54f2a3066b347d0a09114c312ba0e63e545c85f520e7d76eef0a",
            PPTX_MEDIA_TYPE,
        ),
        (
            "Eval_framework.docx",
            14_364,
            "737f13011a878a92740c8781d370dfbb3505509d8c0b1b770a5f974cea9e7f66",
            DOCX_MEDIA_TYPE,
        ),
        (
            "McDonald_finacial_report.docx",
            14_351,
            "df1a15647946cba883e00cb1d0228f075b5e12e6b5deb02acb9c4f79a931515b",
            DOCX_MEDIA_TYPE,
        ),
        (
            "McDonalds_Monthly_Data.xlsx",
            9_545,
            "abaf2d2622354d6c8a1cd6115cda4b1e5b82ccdcd01565d739e75aa606e750b9",
            XLSX_MEDIA_TYPE,
        ),
        (
            "McDonalds_powerpoint_report.pptx",
            39_699,
            "a96a98ecba8bf648fae8357c35d31197d1594c063130737dd098a9c3ac1c712d",
            PPTX_MEDIA_TYPE,
        ),
        (
            "Presentation_Strategy.pptx",
            38_013,
            "e461cb2eb21edeb7f279b9643d304d6115820fca42d2d6bc146085cb631d98fc",
            PPTX_MEDIA_TYPE,
        ),
    ]


def test_combination_docs_004_007_preserve_every_non_asset_canonical_field() -> None:
    """验证四任务只发生 legacy URL 到固定 manifest 的资产迁移。

    输入参数：
        无；读取四份 canonical task 的公开 JSON object。
    输出返回值：
        无；移除新旧资产字段后的确定性 JSON SHA-256 必须等于
        迁移前备份，从而固定 instruction/answer/eval_rules 等所有字段。
    """

    for task_id, expected_digest in _COMBINATION_004_007_NON_ASSET_SHA256.items():
        task = _canonical_task(task_id)
        non_asset = {
            key: value
            for key, value in task.items()
            if key not in {"prepare_script_path", "asset_manifest"}
        }
        payload = json.dumps(
            non_asset,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert hashlib.sha256(payload).hexdigest() == expected_digest


def test_last_four_assets_preserve_canonical_semantics_and_rule_digests() -> None:
    """验证本批四任务只改变输入资产声明。

    输入参数：
        无；读取四份 canonical task 的公开 JSON object。
    输出返回值：
        无；移除新旧资产字段后的全字段摘要，以及
        ``eval_rules`` 规则集摘要，都必须与迁移前备份一致。
    """

    for task_id, expected_non_asset in _LAST_FOUR_NON_ASSET_SHA256.items():
        task = _canonical_task(task_id)
        non_asset = {
            key: value
            for key, value in task.items()
            if key
            not in {
                "prepare_script_path",
                "prepare_exclude_patterns",
                "asset_manifest",
            }
        }
        non_asset_payload = json.dumps(
            non_asset,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        rules_payload = json.dumps(
            task["eval_rules"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert hashlib.sha256(non_asset_payload).hexdigest() == expected_non_asset
        assert (
            hashlib.sha256(rules_payload).hexdigest()
            == (_LAST_FOUR_RULE_SET_SHA256[task_id])
        )


def test_search_and_write_002_resolves_pinned_company_workbook() -> None:
    """验证公司信息补全任务只解析一份固定 XLSX 输入。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言单文件闭集的路径、大小、SHA-256、MIME
        与固定 revision 完全一致。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-002"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/31d84d8d-8c61-4181-b321-44b83adc03f9"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "company_info.xlsx",
            5_016,
            "fa428d1185fb7b8a02420b09f08e12fb65e0da233815b413f5dc26a7717388f5",
            XLSX_MEDIA_TYPE,
        )
    ]


def test_search_and_write_006_resolves_pinned_llm_document() -> None:
    """验证 AI 小节补全任务只解析一份固定 DOCX 输入。

    输入参数：
        无；通过公开 runtime resolver 读取 canonical task 与 manifest。
    输出返回值：
        无；断言文档路径、大小、SHA-256、MIME 及固定来源
        边界与匿名审计结果一致。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-006"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/d624fd2f-f184-4041-a33c-678f6fa10744"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "The development of LLMs.docx",
            15_351,
            "b665ba35a38be8c3ec87f9f16dd8fa7bd2dfdd812802e3c4e223d116cf6a0ed0",
            DOCX_MEDIA_TYPE,
        )
    ]


def test_batch_operation_ppt_001_resolves_pinned_download_manifest() -> None:
    """验证首个 PPT 任务通过公开 loader 解析固定三文件闭集。

    输入参数：
        无；读取 canonical task 与其仓库内 manifest。
    输出返回值：
        无；断言 legacy URL 已消失，三个 PPTX 逐字节身份及
        `unverified`/`download_only` 边界不发生漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-001"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/a7ba9165-a65f-45a3-8449-ac2f358d3a9d"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "ML.pptx",
            37_695,
            "e044e7ebeafd18dbe789a346cb95f2a1a230b62c6330ae19f0e9517921a6f241",
            PPTX_MEDIA_TYPE,
        ),
        (
            "The source of AI.pptx",
            37_433,
            "f342a5152b2cfca9cc3117f6b5a681ad56e662fce0e0e3bb84af63ab960da11d",
            PPTX_MEDIA_TYPE,
        ),
        (
            "welcome.pptx",
            37_657,
            "77bafb3ac9bc92d5fdd287b2d9987d814b8bb87f90402cc3fbc8b4a4a438c6c8",
            PPTX_MEDIA_TYPE,
        ),
    ]


def test_generator_replays_each_migrated_manifest_byte_for_byte() -> None:
    """验证全部已迁移 manifest 均由唯一确定性 builder 重放。

    输入参数：
        无；读取 generator、canonical task 与已落盘 manifest。
    输出返回值：
        无；builder 返回的每份序列化字节均与正式 manifest
        完全一致。
    """

    generator = _load_generator()
    documents = generator.build_batch_operation_office_asset_manifests(REPO_ROOT)
    assert documents
    for relative_path, document in documents.items():
        assert (
            generator.serialize_asset_manifest(document)
            == (REPO_ROOT / relative_path).read_bytes()
        )


def test_batch_operation_ppt_002_resolves_four_presentation_files() -> None:
    """验证第二个 PPT 任务只声明公开目录中四个演示文稿。

    输入参数：
        无；通过统一 runtime loader 读取 canonical 声明。
    输出返回值：
        无；路径、大小、SHA-256 与 PPTX MIME 必须等于匿名下载字节。
    """

    task_id = "Operation-FileOperate-BatchOperationPPT-002"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/85da5285-4ba1-4550-8f5a-00ea07fca510"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "beijing.pptx",
            36_477,
            "7eeb0abd901c78c2d304e9d49fdd47399a1ccd9e3a85f8b6d749703f23b6d83a",
            PPTX_MEDIA_TYPE,
        ),
        (
            "introduction.pptx",
            35_876,
            "f40cda30c0fac98d2520acb2b223b5c9582c97fa78fcf5ebb630a374e6ed1459",
            PPTX_MEDIA_TYPE,
        ),
        (
            "powerPoint.pptx",
            35_333,
            "f066b77602a6713991feb718c91154f8f4fd8ddf81e3d4f5895481a0bc095e54",
            PPTX_MEDIA_TYPE,
        ),
        (
            "traveling.pptx",
            38_339,
            "7fa356e17150dd881b9a86673de6d027af75cd6450384fc81d9ee49a3a040399",
            PPTX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_001_resolves_three_document_files() -> None:
    """验证首个 Word 任务固定三个章节层级修复输入。

    输入参数：
        无；通过公开 runtime loader 读取 canonical 与 manifest。
    输出返回值：
        无；三个 DOCX 的路径、大小、摘要及 MIME 均与匿名下载相等。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-001"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/1dd4c724-6930-42ae-b9c6-d219083f3480"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "2026 Q1 Product Development Plan.docx",
            16_844,
            "238f5fe6b9445cf7e0a977e380bd253ae93e9731f5b30cc0cc88d2d0d1eb9597",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Application of Deep Reinforcement Learning in Robotic Arm Control.docx",
            16_053,
            "578ff6efb57c6ba29c425fca6b2da7590d2bc54a0eed9284646077275923a288",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Remote Work Policy v2.0.docx",
            16_244,
            "3d11f5639803d2223fb4ba51c08f33d2d05f7c7c42f143ea337d9506a9505f3d",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_002_resolves_four_document_files() -> None:
    """验证第二个 Word 任务只包含四个缩进与空行修复输入。

    输入参数：
        无；通过公开 runtime loader 读取 canonical 与 manifest。
    输出返回值：
        无；四个 DOCX 的确定性路径、大小、SHA-256 与 MIME 不得漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-002"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/f9d27527-85a2-4b64-99e9-7f3199cb1cd5"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "apology_letter.docx",
            14_428,
            "4c80b5f0459bcb376b912263d771bb28d8fca37c34aab2111514c2f0a01b1dd8",
            DOCX_MEDIA_TYPE,
        ),
        (
            "climite_news.docx",
            14_503,
            "1cdc95c06e69d95da53466bbd0ef576cf4bcfd8a0dc1dac80424b0864c32c559",
            DOCX_MEDIA_TYPE,
        ),
        (
            "project_update.docx",
            14_470,
            "848cc633ce83034440b34afb075be11d88f802479717104c2ecce929038f6bc0",
            DOCX_MEDIA_TYPE,
        ),
        (
            "sci-fi_narrative.docx",
            14_372,
            "f56462942a5160e5f61913fd4db48160cd0c5252aa88d062c0ed1f502c2ab0bb",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_004_resolves_five_document_files() -> None:
    """验证 Word-004 通过公开 resolver 暴露固定五文档输入闭集。

    输入参数：
        无；读取 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言 legacy URL 已移除，固定 revision、UID 目录、路径、大小、
        SHA-256、MIME 与 download-only 许可证边界均不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-004"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/6ed5298a-16d6-44fb-9c0c-35e87d3f13c0"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "center.docx",
            13_836,
            "0ea61b19aab35f065fc76e99885d16ece502236d443f7ca78a71319dec8ae2ee",
            DOCX_MEDIA_TYPE,
        ),
        (
            "episode.docx",
            13_840,
            "0dba0922c2a836cdd96ce9e2a9a748d6a63cee63490894db3f53c71baaa2f3c5",
            DOCX_MEDIA_TYPE,
        ),
        (
            "experience.docx",
            13_861,
            "4157efd87ca21a8b2d580eeeb98a7e975cd21347f3fe19603b202c5c658f0db3",
            DOCX_MEDIA_TYPE,
        ),
        (
            "hall.docx",
            13_799,
            "94e39cdc999f5824e17dd30a8739bf7daa8411d1a66f37aca1361c148c5e6da7",
            DOCX_MEDIA_TYPE,
        ),
        (
            "travel.docx",
            13_919,
            "90b65ad748a328c000fde15af24bbf95b862790d02744b5cd585ae96263189d5",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_005_resolves_three_document_files() -> None:
    """验证 Word-005 通过公开 resolver 暴露固定三文档输入闭集。

    输入参数：
        无；读取 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言固定 UID 目录中的三个 DOCX 及其逐字节身份不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-005"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/0ae169d8-aeb9-4c78-ab11-a182444c8eed"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Introduction to Artificial Intelligence.docx",
            16_208,
            "e5b051ab0a028470e5a88bb719a7978be290014c6289320448219fe02b8d4717",
            DOCX_MEDIA_TYPE,
        ),
        (
            "The Quiet Station.docx",
            16_333,
            "fa8bc8777c99551244b412088233b38ac8afaba6cb6efc65957add591c8abb9d",
            DOCX_MEDIA_TYPE,
        ),
        (
            "The Silent Library.docx",
            14_323,
            "f1d3966648e4888176de9515dfcda26a3e504e1e73592d1686e88c78d753064f",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_006_resolves_three_document_files() -> None:
    """验证 Word-006 通过公开 resolver 暴露固定三文档输入闭集。

    输入参数：
        无；读取 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言三个中文文件名、字节大小、摘要与 DOCX 类型不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-006"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/186a98aa-ada2-44e3-9187-558ddee9153b"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "2025年重点城市房地产市场活跃度分析.docx",
            14_400,
            "8e84ae9cfcb2af690e0fb84c8123d576245e2f899abba84991dbb29ff1762bd5",
            DOCX_MEDIA_TYPE,
        ),
        (
            "城市平均房价.docx",
            13_774,
            "35c786bad594c07926148d9051297c5344a0c5a22ac5236e0b4fa344f3f16eaa",
            DOCX_MEDIA_TYPE,
        ),
        (
            "设备名称单价.docx",
            13_944,
            "882061bf39f85b9675468589a074d529f05130ddf72902415a81e45206d1f5fc",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_007_resolves_five_document_files() -> None:
    """验证 Word-007 通过公开 resolver 暴露固定五文档输入闭集。

    输入参数：
        无；读取 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言五个 DOCX 的固定目录、路径、大小、摘要与 MIME 不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-007"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/8ff7afec-e238-43df-a240-c4d16807f8b4"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "AI.docx",
            13_715,
            "9724e04b41145d924056bebe533f626878c94da45194e383e72e44a72ea78fc4",
            DOCX_MEDIA_TYPE,
        ),
        (
            "agent.docx",
            13_800,
            "7956888a572709ac39587bcf15ca71f501e4b18f2ee47770be5a8f8a4c80dfb4",
            DOCX_MEDIA_TYPE,
        ),
        (
            "education.docx",
            13_694,
            "595db8729b780e3384ad09ba3ef82ee08c889fba358ae8d777a50bcf57c1a620",
            DOCX_MEDIA_TYPE,
        ),
        (
            "idea.docx",
            13_696,
            "c0acfafaec0c8eaf42c9a6bb89444079c6b7a42124e90437d269f6bf48f21d3f",
            DOCX_MEDIA_TYPE,
        ),
        (
            "software engineering.docx",
            13_828,
            "77e304f07b5b8fe2d745bda739f1f40bec708a8f87b57a33f881ec762e497921",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_008_resolves_four_document_files() -> None:
    """验证 Word-008 通过公开 resolver 暴露固定四文档输入闭集。

    输入参数：
        无；读取 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言 legacy URL 已移除，固定 UID 目录中的四个 DOCX 及其
        逐字节身份、许可证和 download-only 边界不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-008"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/b9929f12-d179-450a-922a-22afb361bad3"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Introduction to Artificial Intelligence.docx",
            16_208,
            "e5b051ab0a028470e5a88bb719a7978be290014c6289320448219fe02b8d4717",
            DOCX_MEDIA_TYPE,
        ),
        (
            "The Quiet Station.docx",
            16_333,
            "fa8bc8777c99551244b412088233b38ac8afaba6cb6efc65957add591c8abb9d",
            DOCX_MEDIA_TYPE,
        ),
        (
            "The Silent Library.docx",
            14_323,
            "f1d3966648e4888176de9515dfcda26a3e504e1e73592d1686e88c78d753064f",
            DOCX_MEDIA_TYPE,
        ),
        (
            "software engineering.docx",
            13_828,
            "77e304f07b5b8fe2d745bda739f1f40bec708a8f87b57a33f881ec762e497921",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_009_resolves_four_document_files() -> None:
    """验证 Word-009 通过公开 resolver 暴露固定四文档输入闭集。

    输入参数：
        无；读取 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言固定 UID 目录中的四个 DOCX 路径、大小、摘要、MIME 与
        download-only 许可证边界不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-009"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/6af0b589-eec2-4b76-a0dd-b18a06ff705b"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Introduction to Artificial Intelligence.docx",
            16_208,
            "e5b051ab0a028470e5a88bb719a7978be290014c6289320448219fe02b8d4717",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Research on Multi.docx",
            14_102,
            "b1300366fab543621dd388c752a83deaf3f0f8fee704655766369ae88cabc230",
            DOCX_MEDIA_TYPE,
        ),
        (
            "The Quiet Station.docx",
            16_333,
            "fa8bc8777c99551244b412088233b38ac8afaba6cb6efc65957add591c8abb9d",
            DOCX_MEDIA_TYPE,
        ),
        (
            "The Silent Library.docx",
            14_323,
            "f1d3966648e4888176de9515dfcda26a3e504e1e73592d1686e88c78d753064f",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_010_resolves_documents_and_nested_images() -> None:
    """验证 Word-010 保留五文档与 `images/` 下五源图的目录闭集。

    输入参数：
        无；读取 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言 DOCX/JPEG 路径、大小、摘要与 MIME 逐字节固定，嵌套图片
        不得被扁平化或误当作额外文件。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-010"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/6e55deaf-c95d-49e9-91b2-b0155fe1dc45"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Cats.docx",
            13_874,
            "8ac5b07a61c07cb8f7774d17497a08556786a5df0e5f9f8a01e57f4fa0935503",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Dogs.docx",
            13_971,
            "e140ed48d16d4d970419e9ed60f0afd6305575646056bbd1ab7aa2786e40010e",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Foxes.docx",
            13_955,
            "c0cfdacf3dff8f4804b6767cd8448f3155d58909bd8330cb2e658a7adb746de2",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Hamsters.docx",
            13_898,
            "711688f693e014a1172af1fa3e27f7128bd5eb6483dca25de9ee5ee3363bcd5d",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Tigers.docx",
            13_938,
            "13889f2886526779bf391a39258f2bba495a04ff04a5409334636cb475754be1",
            DOCX_MEDIA_TYPE,
        ),
        (
            "images/Cats.jpeg",
            5_841,
            "516a5dc48b50aaf03bd7aeb3f9fd0f20de44d624c9e8f9de66b46d92a36db5b5",
            JPEG_MEDIA_TYPE,
        ),
        (
            "images/Dogs.jpeg",
            8_111,
            "13d12502a8c626efbf4dc053f73f2c56a7c3de8955d26d2c7fe2bb9282cbd17a",
            JPEG_MEDIA_TYPE,
        ),
        (
            "images/Foxes.jpeg",
            7_257,
            "5bbc110037d4e937516295531e53cf8dac0f7d4a72100d8457a8f1064a0b643d",
            JPEG_MEDIA_TYPE,
        ),
        (
            "images/Hamsters.jpeg",
            5_900,
            "c4bc248ca159adc2278a62ccab8314222d22c7378848186468da507532a34469",
            JPEG_MEDIA_TYPE,
        ),
        (
            "images/Tigers.jpeg",
            10_111,
            "6efffea249b289eb42e416eb67257b894d5f2e8f1ca33949cdd9c0dd1af2a5d4",
            JPEG_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_word_011_resolves_five_document_files() -> None:
    """验证 Word-011 通过公开 resolver 暴露固定五文档输入闭集。

    输入参数：
        无；读取当前 canonical task 并通过统一 runtime asset resolver 解析。
    输出返回值：
        无；断言五份互引 DOCX 的固定 UID、路径、大小、摘要、MIME 与
        download-only 边界不可漂移，且不改变 canonical 评价语义。
    """

    task_id = "Operation-FileOperate-BatchOperationWord-011"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/248add77-e3c1-4a59-b98e-03752238dc81"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Doc_A.docx",
            14_149,
            "9d94b0b9a42da4f9fb467dc6b108d0b434e16588cdc7bf4ee7b9e052ce57b8bc",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Doc_B.docx",
            13_989,
            "b37273f61005229c898d38f19a082213f68a74bcdb24ec8cf511fd32678411f8",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Doc_C.docx",
            13_957,
            "0d8dc5e595bd47d9a56ffe197b7d70e2c7417ed7eabf579127188925d42d177f",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Doc_D.docx",
            13_972,
            "b52a5eb1d88c27354e27d766c5a1b50af94337d7a71032fce1a5fca714c25644",
            DOCX_MEDIA_TYPE,
        ),
        (
            "Doc_E.docx",
            13_959,
            "c1b56a1e389757e664a7b9658a2f143671b75b08b02511279621310e9dbc8088",
            DOCX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_excel_001_resolves_four_workbooks() -> None:
    """验证首个 Excel 任务只解析固定四工作簿输入闭集。

    输入参数：
        无；通过公开 runtime loader 读取 canonical 与 manifest。
    输出返回值：
        无；四个 XLSX 的路径、大小、摘要与 MIME 必须等于固定
        revision 的匿名下载字节。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-001"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/5e573e33-135a-4b45-b398-f85c0f7fea0a"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "store1.xlsx",
            9_258,
            "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store2.xlsx",
            9_279,
            "23f584f69a818fe2dbc5e1dfcaa6ac103464edcb095c5ecf7de2ec50477ccd80",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store3.xlsx",
            5_561,
            "cff0d19540c2e56c6355691c2ac41aafca059e6ce5aa2e9a79bffaa6c0b7c041",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store4.xlsx",
            5_559,
            "683d3a4728beb8072649ae50babbf98b5a1a64e280ba2b0770bb264f23428fe7",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_excel_002_resolves_four_workbooks() -> None:
    """验证第二个 Excel 任务固定同一组四工作簿字节。

    输入参数：
        无；通过公开 runtime loader 读取第二个 Excel canonical。
    输出返回值：
        无；来源 UID 与四个 XLSX 的完整身份均不可漂移。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-002"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == EXCEL002_PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/a1510a05-9fca-46ba-b95d-451dd5779194"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "store1.xlsx",
            5_632,
            "9fdb36b01e7c12835f080279b0666b2f7e6171eaa05617ef79f9a5d39ae008d7",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store2.xlsx",
            5_641,
            "2850627275e5d78efbb26a95d959120218f5eae0a94add74e2f302693a053d1f",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store3.xlsx",
            5_553,
            "dc95fc6f4daaa743d053c2a19705565b8e9e4a1ec87a3756af0be5e23f266b0b",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store4.xlsx",
            5_551,
            "aecc7c83c35444753b130037322cb6f65fbf77482b90702742baa4f91141dd9f",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_excel_003_resolves_four_workbooks() -> None:
    """验证第三个 Excel 任务固定四个待排序工作簿。

    输入参数：
        无；通过公开 runtime loader 读取第三个 Excel canonical。
    输出返回值：
        无；目录 UID 及四文件 byte identity 必须与审计证据一致。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-003"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/fdb089b8-070f-4ccc-9612-e4599db799be"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "store1.xlsx",
            9_258,
            "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store2.xlsx",
            9_279,
            "23f584f69a818fe2dbc5e1dfcaa6ac103464edcb095c5ecf7de2ec50477ccd80",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store3.xlsx",
            5_561,
            "cff0d19540c2e56c6355691c2ac41aafca059e6ce5aa2e9a79bffaa6c0b7c041",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store4.xlsx",
            5_559,
            "683d3a4728beb8072649ae50babbf98b5a1a64e280ba2b0770bb264f23428fe7",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_excel_004_resolves_four_workbooks() -> None:
    """验证第四个 Excel 任务固定四个表头语义工作簿。

    输入参数：
        无；通过公开 runtime loader 读取第四个 Excel canonical。
    输出返回值：
        无；唯一共享的 store1 与其余三个独立 XLSX 身份均固定。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-004"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/086f42e6-d412-4a4b-9702-0ef374e38c2b"
    )
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "store1.xlsx",
            9_258,
            "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store2.xlsx",
            9_279,
            "56a523840a796142562fde92b67ddcaac247652d23e7376bac40299781b03457",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store3.xlsx",
            9_211,
            "5eb434afffc3ccf5d903802b77be409ad0dd4dea73e6a2c738ceee17d939adbf",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store4.xlsx",
            9_222,
            "5e3590b023745ffbfe26293061cddd45de5b5657380439b010a37bd89e539f67",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_excel_005_resolves_four_workbooks() -> None:
    """验证第五个 Excel 任务通过公开 resolver 固定四工作簿输入。

    输入参数：
        无；读取仓库 canonical task 与正式资产 manifest。
    输出返回值：
        无；legacy URL 必须消失，来源 UID、许可边界及四个 XLSX
        的路径、大小、SHA-256 和 MIME 必须等于固定 revision 字节。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-005"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/cccf5baf-e392-47af-a605-65401ef56fe5"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "store1.xlsx",
            9_258,
            "1a5a69985b303f96d18d29d73b2c47653f662403484a36b5761f5635d4153a70",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store2.xlsx",
            9_278,
            "fe5bbc48c80cec38568b71a42508cb9df83a6c5b6388701445f1cf4170e3d1d8",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store3.xlsx",
            9_211,
            "5eb434afffc3ccf5d903802b77be409ad0dd4dea73e6a2c738ceee17d939adbf",
            XLSX_MEDIA_TYPE,
        ),
        (
            "store4.xlsx",
            9_222,
            "5e3590b023745ffbfe26293061cddd45de5b5657380439b010a37bd89e539f67",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_excel_006_resolves_five_workbooks() -> None:
    """验证第六个 Excel 任务通过公开 resolver 固定五工作簿输入。

    输入参数：
        无；读取仓库 canonical task 与正式资产 manifest。
    输出返回值：
        无；任务必须解析为固定下载模式，且五个餐饮月度工作簿的来源、
        路径、大小、SHA-256 与 MIME 均等于固定 revision 字节。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-006"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/ed04e7b5-a2c6-449a-a493-b22999919008"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "KFC_Monthly_Data.xlsx",
            5_849,
            "4d9bcff171a5ae61bdb6b5c6b2b16a3d6fcb9af09b3ea639049b2c5457b68e1a",
            XLSX_MEDIA_TYPE,
        ),
        (
            "McDonalds_Monthly_Data.xlsx",
            5_858,
            "7c527377555479618e964962b756a7028564ed059f9273fbd16526b2170a6596",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Mixue_Monthly_Data.xlsx",
            5_866,
            "e7f7bd52d195f878fc94c3845c10acef0f1c0e570afdd9de0a342212cf2e19d2",
            XLSX_MEDIA_TYPE,
        ),
        (
            "PizzaHut_Monthly_Data.xlsx",
            5_859,
            "d7c9ce0987a9c2b829d9943ead8894099b3b9664aeec4e2360b1bac3896750a2",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Subway_Monthly_Data.xlsx",
            5_849,
            "0aeb94ba9eecf8135c6cfda2f83e8c7d9f4e40b102431a8c55e4c315cfd4f898",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_batch_operation_excel_007_resolves_single_workbook() -> None:
    """验证第七个 Excel 任务通过公开 resolver 固定唯一输入工作簿。

    输入参数：
        无；读取仓库 canonical task 与正式资产 manifest。
    输出返回值：
        无；单文件目录不得被填充虚构条目，唯一 XLSX 的来源、大小、
        SHA-256、MIME 与下载许可边界必须精确固定。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-007"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/7e6bdc0a-b1dd-47c5-98a7-baafd3f5fd0f"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Company_Sales_Data.xlsx",
            6_944,
            "f5ce5597c021c0cd118b0b2bf4a96836baf9c87cbc4bb50de7b78e6a5abe7d88",
            XLSX_MEDIA_TYPE,
        )
    ]


def test_batch_operation_excel_009_resolves_five_workbooks() -> None:
    """验证第九个 Excel 任务通过公开 resolver 固定五工作簿输入。

    输入参数：
        无；读取仓库 canonical task 与正式资产 manifest。
    输出返回值：
        无；五个序号填充工作簿必须来自任务 UID 对应的严格目录闭集，
        并固定逐文件大小、SHA-256、MIME 与 download-only 边界。
    """

    task_id = "Operation-FileOperate-BatchOperationExcel-009"
    task = _canonical_task(task_id)
    resolved = resolve_task_assets(REPO_ROOT, task)

    assert "prepare_script_path" not in task
    assert task["asset_manifest"] == f"benchmark/assets/manifests/{task_id}.json"
    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    manifest = resolved.manifest
    assert manifest is not None
    assert manifest.asset_set_id == task_id
    assert manifest.source.revision == PINNED_REVISION
    assert manifest.source.base_path == (
        "benchmark_dataset/0f045849-d0e4-48d5-9010-ece2534c2b8c"
    )
    assert manifest.source.license_status == "unverified"
    assert manifest.distribution_policy == "download_only"
    assert [
        (entry.path, entry.size, entry.sha256, entry.media_type)
        for entry in manifest.files
    ] == [
        (
            "Company_Invoices.xlsx",
            6_068,
            "c8cc204b631b1640ff6f7c9a62c9051e843c2cd0c2bc8c201e022753ac852c5e",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Electronics_Orders.xlsx",
            5_626,
            "306733299e609a65570a3e066222176dfeee2c6a1756a4360e272ce3ad041016",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Food_Delivery_Orders.xlsx",
            5_859,
            "93847919ad9b11fc5a478f47c0ec48c12cd55b1896ede98c66d2c19e3554a571",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Hotel_Bookings.xlsx",
            5_686,
            "360c2ef6edcab10fed2a6a1413441ce3db87755c05ad9c4d8e1b0488cb1e2bf2",
            XLSX_MEDIA_TYPE,
        ),
        (
            "Warehouse_Shipments.xlsx",
            5_770,
            "edc654d7e0c31621d80d9f9d3f8a97b2f03a10488b94c63004c68bfa19083b32",
            XLSX_MEDIA_TYPE,
        ),
    ]


def test_generator_check_accepts_exact_thirty_four_task_closure() -> None:
    """验证生成器对三十四任务一百二十八文件闭集逐字节检查。

    输入参数：
        无；读取三十四份 canonical 与 manifest。
    输出返回值：
        无；精确闭集与生成器一致时返回真，且不含锁文件、隐藏文件或
            非显式后缀-MIME 白名单条目。
    """

    generator = _load_generator()
    documents = generator.build_batch_operation_office_asset_manifests(REPO_ROOT)
    entries = [entry for document in documents.values() for entry in document["files"]]

    assert {document["asset_set_id"] for document in documents.values()} == (
        MIGRATED_TASK_IDS
    )
    assert len(documents) == 34
    assert len(entries) == 128
    assert all(not Path(entry["path"]).name.startswith("~$") for entry in entries)
    assert all(not Path(entry["path"]).name.startswith(".") for entry in entries)
    assert {Path(entry["path"]).suffix for entry in entries} == {
        ".pptx",
        ".docx",
        ".xlsx",
        ".jpg",
        ".jpeg",
        ".txt",
        ".md",
        ".csv",
        ".html",
    }
    assert generator.check_batch_operation_office_asset_manifests(REPO_ROOT) is True


def test_batch_operation_office_schema_is_closed_and_rejects_lock_files() -> None:
    """验证专属 schema 固定任务、来源、许可边界与文件形状。

    输入参数：
        无；读取仓库内 JSON Schema。
    输出返回值：
        无；所有 object 字段严格闭集，task/revision/policy 固定，
            文件路径模式显式排除隐藏文件、`~$` 锁文件、
            路径穿越和非白名单后缀，并固定每个后缀的 MIME。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["properties"]["asset_set_id"]["enum"]) == MIGRATED_TASK_IDS
    assert schema["properties"]["distribution_policy"]["const"] == "download_only"
    assert schema["properties"]["files"]["minItems"] == 1
    assert schema["properties"]["files"]["maxItems"] == 10
    source = schema["$defs"]["source"]
    lee_source = schema["$defs"]["lee_source"]
    xlang_source = schema["$defs"]["xlang_source"]
    file_contract = schema["$defs"]["file"]
    assert source["oneOf"] == [
        {"$ref": "#/$defs/lee_source"},
        {"$ref": "#/$defs/xlang_source"},
    ]
    for definition in (lee_source, xlang_source, file_contract):
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == set(definition["properties"])
    assert lee_source["properties"]["repository"]["const"] == (
        "leeLegendary/Parallel_benchmark"
    )
    assert lee_source["properties"]["revision"]["const"] == PINNED_REVISION
    assert lee_source["properties"]["license_status"]["const"] == "unverified"
    assert xlang_source["properties"]["repository"]["const"] == (
        "xlangai/ubuntu_osworld_file_cache"
    )
    assert xlang_source["properties"]["revision"]["const"] == (XLANG_PINNED_REVISION)
    assert xlang_source["properties"]["base_path"]["const"] == (
        "multi_apps/6f4073b8-d8ea-4ade-8a18-c5d1d5d5aa9a"
    )
    source_binding = schema["allOf"]
    assert len(source_binding) == 1
    assert source_binding[0]["if"]["properties"]["asset_set_id"]["const"] == (
        "Operation-FileOperate-SearchAndWrite-007"
    )
    assert source_binding[0]["then"]["properties"]["source"]["$ref"] == (
        "#/$defs/xlang_source"
    )
    assert source_binding[0]["else"]["properties"]["source"]["$ref"] == (
        "#/$defs/lee_source"
    )
    path_pattern = file_contract["properties"]["path"]["pattern"]
    assert "~\\$" in path_pattern
    assert "pptx|docx|xlsx|jpe?g|txt|md|csv|html" in path_pattern
    compiled_path = re.compile(path_pattern)
    assert compiled_path.fullmatch("The source of AI.pptx")
    assert compiled_path.fullmatch("folder/Remote Work Policy v2.0.docx")
    assert compiled_path.fullmatch("store1.xlsx")
    assert compiled_path.fullmatch("picture1.jpg")
    assert compiled_path.fullmatch("images/Cats.jpeg")
    assert compiled_path.fullmatch("Business_Report.txt")
    assert compiled_path.fullmatch("Development_Guide.md")
    assert compiled_path.fullmatch("Employee_Directory.csv")
    assert compiled_path.fullmatch("Product_Catalog.html")
    for rejected in (
        "~$mechine learning.pptx",
        ".hidden.docx",
        "folder/.hidden.docx",
        ".hidden.md",
        "folder/.hidden.csv",
        "~$hidden.txt",
        "../escape.docx",
        "folder/../escape.html",
        "folder\\escape.docx",
        "not-office.pdf",
    ):
        assert compiled_path.fullmatch(rejected) is None
    assert set(file_contract["properties"]["media_type"]["enum"]) == {
        PPTX_MEDIA_TYPE,
        DOCX_MEDIA_TYPE,
        XLSX_MEDIA_TYPE,
        JPEG_MEDIA_TYPE,
        TEXT_MEDIA_TYPE,
        MARKDOWN_MEDIA_TYPE,
        CSV_MEDIA_TYPE,
        HTML_MEDIA_TYPE,
    }
    suffix_media_types = {
        contract["if"]["properties"]["path"]["pattern"]: contract["then"]["properties"][
            "media_type"
        ]["const"]
        for contract in file_contract["allOf"]
    }
    assert suffix_media_types == {
        "\\.pptx$": PPTX_MEDIA_TYPE,
        "\\.docx$": DOCX_MEDIA_TYPE,
        "\\.xlsx$": XLSX_MEDIA_TYPE,
        "\\.jpe?g$": JPEG_MEDIA_TYPE,
        "\\.txt$": TEXT_MEDIA_TYPE,
        "\\.md$": MARKDOWN_MEDIA_TYPE,
        "\\.csv$": CSV_MEDIA_TYPE,
        "\\.html$": HTML_MEDIA_TYPE,
    }


def test_generator_check_cli_reports_only_public_closure_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证专属 CLI 可重放检查且不输出路径或资产内容。

    输入参数：
        capsys：pytest 标准输出捕获 fixture。
    输出返回值：
        无；`check` 返回零且只报告三十四任务/一百二十八文件计数。
    """

    generator = _load_generator()

    assert generator.main(["check", "--repo-root", str(REPO_ROOT)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "BatchOperation Office asset manifests valid: tasks=34; files=128\n"
    )
    assert captured.err == ""


def test_runtime_projection_rejects_batch_001_manifest_digest_tamper(
    tmp_path: Path,
) -> None:
    """验证山峰图片 manifest 的逐文件摘要被 production 投影固定。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保留合法 JSON、task 与 source 形状，只篡改首张图片
        SHA-256 时，runtime-support 必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperation-001.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_003_manifest_digest_tamper(
    tmp_path: Path,
) -> None:
    """验证 Word-003 的原始文档闭集受完整 manifest SHA 绑定。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持 JSON、task 和 source 形状合法，仅篡改首份
        原始 DOCX 摘要时，production runtime-support 必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-003.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_combination_001_cross_swapped_source(
    tmp_path: Path,
) -> None:
    """验证字节相同的工作簿不能洗白为另一任务的来源。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；将 Excel-006 的有效 manifest 改成目标 asset_set_id 后
        覆盖 CombinationDocs-001，production 投影仍必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_root = isolated_root / "benchmark/assets/manifests"
    source_path = manifest_root / "Operation-FileOperate-BatchOperationExcel-006.json"
    target_path = manifest_root / "Operation-FileOperate-CombinationDocs-001.json"
    cross_swapped = json.loads(source_path.read_text(encoding="utf-8"))
    cross_swapped["asset_set_id"] = "Operation-FileOperate-CombinationDocs-001"
    target_path.write_text(
        json.dumps(cross_swapped, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_search_002_canonical_manifest_path_drift(
    tmp_path: Path,
) -> None:
    """验证公司工作簿不能改用内容相同的仓库其他路径。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；即使复制原 manifest 并机械同步 release task SHA，
        canonical task/path 身份门仍必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    task_id = "Operation-FileOperate-SearchAndWrite-002"
    manifest_root = isolated_root / "benchmark/assets/manifests"
    alternate_reference = "benchmark/assets/manifests/company-info-alternate.json"
    shutil.copy2(
        manifest_root / f"{task_id}.json",
        isolated_root / alternate_reference,
    )
    task_path = isolated_root / "benchmark/tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["asset_manifest"] = alternate_reference
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == task_id
    )
    release_entry["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_search_006_semantic_json_byte_drift(
    tmp_path: Path,
) -> None:
    """验证 AI 文档 manifest 必须是 generator 的唯一字节表示。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；仅改变 JSON 空白且保持解析语义不变时，
        production 投影仍必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-SearchAndWrite-006.json"
    )
    payload = manifest_path.read_text(encoding="utf-8")
    drifted = payload.replace('"size": 15351', '"size" : 15351', 1)
    assert drifted != payload
    manifest_path.write_text(drifted, encoding="utf-8")
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_combination_004_manifest_size_tamper(
    tmp_path: Path,
) -> None:
    """验证跨文档比对输入的完整 manifest 字节受正式投影绑定。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持 task、source、asset_set_id 和 JSON 形状合法，
        仅修改首文件大小时 runtime-support 也必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-004.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["size"] += 1
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_combination_005_manifest_path_drift(
    tmp_path: Path,
) -> None:
    """验证多文本格式 manifest 不能改用内容相同的其他仓库路径。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；即使复制原 manifest 并机械同步 release task SHA，
        canonical task/path 身份门仍必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    task_id = "Operation-FileOperate-CombinationDocs-005"
    manifest_root = isolated_root / "benchmark/assets/manifests"
    alternate_reference = "benchmark/assets/manifests/export-documents-alternate.json"
    shutil.copy2(
        manifest_root / f"{task_id}.json",
        isolated_root / alternate_reference,
    )
    task_path = isolated_root / "benchmark/tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["asset_manifest"] = alternate_reference
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == task_id
    )
    release_entry["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_search_write_004_manifest_path_drift(
    tmp_path: Path,
) -> None:
    """验证 SearchAndWrite-004 不能换用内容相同的 manifest 路径。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；复制完全相同的 manifest、改写 canonical 路径并
        同步 release task SHA 后，production 仍必须按任务专属路径拒绝。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    task_id = "Operation-FileOperate-SearchAndWrite-004"
    original_reference = f"benchmark/assets/manifests/{task_id}.json"
    alternate_reference = "benchmark/assets/manifests/conference-input-alternate.json"
    shutil.copy2(
        isolated_root / original_reference,
        isolated_root / alternate_reference,
    )
    task_path = isolated_root / "benchmark/tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["asset_manifest"] = alternate_reference
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == task_id
    )
    release_entry["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_manifest_and_legacy_hybrid_canonical(
    tmp_path: Path,
) -> None:
    """验证固定 manifest 不能与重新出现的 legacy 来源字段共存。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持正确专属 manifest，仅向 005 重新加入非空
        `prepare_script_path` 并同步 release SHA 时，生产投影必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    task_id = "Operation-FileOperate-CombinationDocs-005"
    task_path = isolated_root / "benchmark/tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["prepare_script_path"] = "https://example.invalid/legacy"
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == task_id
    )
    release_entry["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_003_legacy_exclude_hybrid(
    tmp_path: Path,
) -> None:
    """验证 Word-003 固定闭集不能与 legacy 准备排除规则共存。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；恢复 ``prepare_exclude_patterns`` 并同步 release task SHA
        后，production 必须失败关闭，不得同时保留两套资产语义。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    task_id = "Operation-FileOperate-BatchOperationWord-003"
    task_path = isolated_root / "benchmark/tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["prepare_exclude_patterns"] = ["*_answer.docx"]
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == task_id
    )
    release_entry["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_combination_006_to_007_cross_swap(
    tmp_path: Path,
) -> None:
    """验证另一任务的有效文件闭集不能洗白为主题分类输入。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；将 CombinationDocs-006 的合法 manifest 改成目标
        asset_set_id 后覆盖 007，完整 manifest SHA 绑定必须拒绝。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_root = isolated_root / "benchmark/assets/manifests"
    source = json.loads(
        (manifest_root / "Operation-FileOperate-CombinationDocs-006.json").read_text(
            encoding="utf-8"
        )
    )
    source["asset_set_id"] = "Operation-FileOperate-CombinationDocs-007"
    (manifest_root / "Operation-FileOperate-CombinationDocs-007.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_combination_005_extra_file_entry(
    tmp_path: Path,
) -> None:
    """验证格式、路径和字段都合法的多余文件仍不属于固定闭集。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；向 005 追加安全相对路径与形状合法的 TXT 条目时，
        production 投影必须依据完整字节 SHA 失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-005.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": "Unexpected.txt",
            "size": 1,
            "sha256": "0" * 64,
            "media_type": TEXT_MEDIA_TYPE,
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_markdown_mime_misclassification(
    tmp_path: Path,
) -> None:
    """验证 Markdown MIME 不能被环境推断值或普通文本类型替代。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；仅把 `Development_Guide.md` 的 MIME 改为形状合法的
        `text/plain` 时，runtime-support 必须拒绝该 manifest。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-005.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    markdown = next(
        entry for entry in manifest["files"] if entry["path"] == "Development_Guide.md"
    )
    markdown["media_type"] = TEXT_MEDIA_TYPE
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_keeps_migrated_assets_blocked_only_by_remaining_gates(
    tmp_path: Path,
) -> None:
    """验证确定性支持投影提升资产状态但不越过剩余门禁。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根，用于机械传播 Office task SHA。
    输出返回值：
        无；Readonly 串行整合后 legacy asset 与已验证镜像 blocker 必须归零，
        Office 任务仍 `blocked`；Batch-001 额外保留 artifact getter live 门禁。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)

    runtime_support = _load_runtime_support_generator()
    manifest = runtime_support.build_runtime_support_manifest(isolated_root)
    entries = {entry["task_id"]: entry for entry in manifest["tasks"]}
    legacy_code = "legacy_asset_manifest_not_migrated"

    assert not any(legacy_code in entry["blocker_codes"] for entry in manifest["tasks"])
    for task_id in MIGRATED_TASK_IDS:
        entry = entries[task_id]
        assert entry["asset_status"] == "pinned_download_manifest"
        assert entry["support_status"] == "blocked"
        assert entry["support_reason_code"] == "live_validation_pending"
        expected_blockers: list[str] = []
        if task_id == "Operation-FileOperate-BatchOperation-001":
            expected_blockers.append(
                "osworld_artifact_getter_live_validation_not_completed"
            )
        if task_id in {
            "Operation-FileOperate-BatchOperationWord-009",
            "Operation-FileOperate-BatchOperationWord-010",
        }:
            expected_blockers.append(
                "operation_word009_010_writer_live_validation_not_completed"
            )
        if task_id == "Operation-FileOperate-CombinationDocs-003":
            expected_blockers.append(
                "combinationdocs003_real_render_validation_not_completed"
            )
        expected_blockers.append("versioned_live_validation_not_completed")
        assert entry["blocker_codes"] == expected_blockers


def test_runtime_projection_rejects_missing_pinned_asset_manifest(
    tmp_path: Path,
) -> None:
    """验证支持投影不会仅凭 canonical 字符串清除资产门禁。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；删除 Excel-001 的正式 manifest 后，生成器必须失败关闭，
        不得继续输出 `pinned_download_manifest`。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-001.json"
    )
    manifest_path.unlink()
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_cross_swapped_asset_manifest(
    tmp_path: Path,
) -> None:
    """验证支持投影绑定 manifest 身份而非只接受合法 JSON。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；把 Excel-002 的有效 manifest 覆盖到 Excel-001 路径后，
        task 与 asset_set_id 不一致必须使生成失败。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_root = isolated_root / "benchmark/assets/manifests"
    shutil.copy2(
        manifest_root / "Operation-FileOperate-BatchOperationExcel-002.json",
        manifest_root / "Operation-FileOperate-BatchOperationExcel-001.json",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


@pytest.mark.parametrize(
    "task_id",
    (
        "Operation-FileOperate-BatchOperationExcel-005",
        "Operation-FileOperate-BatchOperationExcel-006",
        "Operation-FileOperate-BatchOperationExcel-007",
        "Operation-FileOperate-BatchOperationExcel-009",
        "Operation-FileOperate-BatchOperationWord-004",
        "Operation-FileOperate-BatchOperationWord-005",
        "Operation-FileOperate-BatchOperationWord-006",
        "Operation-FileOperate-BatchOperationWord-007",
        "Operation-FileOperate-BatchOperationWord-008",
        "Operation-FileOperate-BatchOperationWord-009",
        "Operation-FileOperate-BatchOperationWord-010",
        "Operation-FileOperate-BatchOperationWord-011",
        "Operation-FileOperate-BatchOperationWord-003",
        "Operation-FileOperate-BatchOperationWord-012",
        "Operation-FileOperate-BatchOperation-001",
        "Operation-FileOperate-CombinationDocs-001",
        "Operation-FileOperate-CombinationDocs-003",
        "Operation-FileOperate-CombinationDocs-004",
        "Operation-FileOperate-CombinationDocs-005",
        "Operation-FileOperate-CombinationDocs-006",
        "Operation-FileOperate-CombinationDocs-007",
        "Operation-FileOperate-CombinationDocs-008",
        "Operation-FileOperate-SearchAndWrite-002",
        "Operation-FileOperate-SearchAndWrite-004",
        "Operation-FileOperate-SearchAndWrite-006",
        "Operation-FileOperate-SearchAndWrite-007",
    ),
)
def test_runtime_projection_rejects_each_new_missing_manifest(
    tmp_path: Path,
    task_id: str,
) -> None:
    """验证本批任一正式 manifest 缺失都使支持投影失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
        task_id：最近批次二十个已迁移 task ID 之一。
    输出返回值：
        无；删除对应 manifest 后必须抛出稳定 RuntimeSupportError。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = isolated_root / "benchmark/assets/manifests" / f"{task_id}.json"
    manifest_path.unlink()
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_new_cross_swapped_manifest(
    tmp_path: Path,
) -> None:
    """验证本批两个有效 manifest 互换后不能清除资产门禁。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；Excel-006 合法 manifest 覆盖 Excel-005 路径后必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_root = isolated_root / "benchmark/assets/manifests"
    shutil.copy2(
        manifest_root / "Operation-FileOperate-BatchOperationExcel-006.json",
        manifest_root / "Operation-FileOperate-BatchOperationExcel-005.json",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_cross_swapped_manifest(
    tmp_path: Path,
) -> None:
    """验证两份合法 Word manifest 交叉覆盖也不能清除资产门禁。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；Word-005 manifest 覆盖 Word-004 专属路径后，task、路径、
        asset_set_id 与固定 manifest SHA 任一不一致都必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_root = isolated_root / "benchmark/assets/manifests"
    shutil.copy2(
        manifest_root / "Operation-FileOperate-BatchOperationWord-005.json",
        manifest_root / "Operation-FileOperate-BatchOperationWord-004.json",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_latest_word_cross_swapped_manifest(
    tmp_path: Path,
) -> None:
    """验证本批两份合法 Word manifest 交叉覆盖仍失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；Word-009 的合法 manifest 覆盖 Word-008 专属路径后，task、
        路径、asset_set_id 与固定 manifest SHA 绑定必须共同拒绝漂移。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_root = isolated_root / "benchmark/assets/manifests"
    shutil.copy2(
        manifest_root / "Operation-FileOperate-BatchOperationWord-009.json",
        manifest_root / "Operation-FileOperate-BatchOperationWord-008.json",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_012_to_003_cross_swap(
    tmp_path: Path,
) -> None:
    """验证本批两份 Word 闭集不能仅改 asset_set_id 后交换。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；把 Word-012 的合法 manifest 改写为 Word-003 身份并
        覆盖目标路径后，完整 manifest SHA 绑定必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_root = isolated_root / "benchmark/assets/manifests"
    source = json.loads(
        (manifest_root / "Operation-FileOperate-BatchOperationWord-012.json").read_text(
            encoding="utf-8"
        )
    )
    source["asset_set_id"] = "Operation-FileOperate-BatchOperationWord-003"
    (manifest_root / "Operation-FileOperate-BatchOperationWord-003.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_new_manifest_file_digest_tamper(
    tmp_path: Path,
) -> None:
    """验证本批 manifest 的逐文件 SHA-256 被改写时失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持 JSON、task ID 与来源都合法，只篡改首文件摘要也必须
        被 manifest 固定字节绑定拒绝。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-005.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_004_manifest_file_digest_tamper(
    tmp_path: Path,
) -> None:
    """验证 Word-004 不能以泛化合法 manifest 绕过固定字节绑定。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保留合法 task/source 形状而只改首文件 SHA-256 时，
        runtime-support 必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-004.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_010_nested_image_digest_tamper(
    tmp_path: Path,
) -> None:
    """验证 Word-010 的嵌套 JPEG 摘要受正式 manifest 字节绑定。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持 schema、task、UID 与目录形状合法，只篡改最后一张
        `images/` JPEG 的 SHA-256 也必须使 runtime-support 失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-010.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["files"][-1]["path"] == "images/Tigers.jpeg"
    manifest["files"][-1]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_005_source_uid_drift(
    tmp_path: Path,
) -> None:
    """验证 Word-005 的 task/目录映射受固定 manifest 字节约束。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持 asset_set_id 与合法 schema，只替换 source UID 时也
        必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-005.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["base_path"] = (
        "benchmark_dataset/6ed5298a-16d6-44fb-9c0c-35e87d3f13c0"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_search_write_007_source_revision_drift(
    tmp_path: Path,
) -> None:
    """验证 xlang 输入不能回退到 ``main`` 或其他合法 revision。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；只把 SearchAndWrite-007 source revision 替换为另一个
        形状合法的 40 位摘要时，production runtime-support 必须拒绝。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-SearchAndWrite-007.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["revision"] = "a" * 40
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_006_semantic_json_byte_drift(
    tmp_path: Path,
) -> None:
    """验证 Word-006 的完整 manifest SHA 而非仅解析值受绑定。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；只改变 JSON 空白且保持解析语义完全相同时，正式投影仍
        必须拒绝非生成器唯一字节。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-006.json"
    )
    payload = manifest_path.read_text(encoding="utf-8")
    drifted = payload.replace('"size": 14400', '"size" : 14400', 1)
    assert drifted != payload
    manifest_path.write_text(drifted, encoding="utf-8")
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_007_revision_drift(
    tmp_path: Path,
) -> None:
    """验证 Word-007 必须绑定审计过的公开固定 revision。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；把 revision 换成另一个形状合法的提交摘要时，投影必须
        失败关闭而不是退化为泛化 manifest 解析。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-007.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["revision"] = "0" * 40
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_new_canonical_manifest_path_swap(
    tmp_path: Path,
) -> None:
    """验证 release 合法同步后仍绑定 task 专属 manifest 路径。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；把 Excel-005 canonical 指向 Excel-006 manifest 并同步
        release task SHA 后，路径身份门仍必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    task_id = "Operation-FileOperate-BatchOperationExcel-005"
    task_path = isolated_root / "benchmark/tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["asset_manifest"] = (
        "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-006.json"
    )
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == task_id
    )
    release_entry["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_word_canonical_manifest_path_swap(
    tmp_path: Path,
) -> None:
    """验证 Word canonical 即使同步 release SHA 也不能换用他项 manifest。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；Word-007 指向 Word-006 manifest 且机械同步 release 后，
        task 专属路径绑定仍必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    task_id = "Operation-FileOperate-BatchOperationWord-007"
    task_path = isolated_root / "benchmark/tasks" / f"{task_id}.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["asset_manifest"] = (
        "benchmark/assets/manifests/Operation-FileOperate-BatchOperationWord-006.json"
    )
    task_path.write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    release_path = isolated_root / "benchmark/manifests/release-v1.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release_entry = next(
        entry for entry in release["tasks"] if entry["task_id"] == task_id
    )
    release_entry["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(
        json.dumps(release, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_batch_office_source_uid_drift(
    tmp_path: Path,
) -> None:
    """验证 Office 支持投影绑定完整固定 manifest，而非仅绑定 task ID。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持 asset_set_id 但把 Excel-001 来源目录改为另一任务 UID
        后，投影必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-001.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["base_path"] = (
        "benchmark_dataset/a1510a05-9fca-46ba-b95d-451dd5779194"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_excel_005_source_uid_drift(
    tmp_path: Path,
) -> None:
    """验证 Excel-005 支持投影绑定完整 manifest 字节而非泛化解析。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；保持 asset_set_id 与合法 JSON 形状，仅把来源目录换成
        Excel-006 UID 时也必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-005.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["base_path"] = (
        "benchmark_dataset/ed04e7b5-a2c6-449a-a493-b22999919008"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_excel_006_source_uid_drift(
    tmp_path: Path,
) -> None:
    """验证 Excel-006 支持投影绑定完整 manifest 字节。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；把来源 UID 换成 Excel-005 目录时，合法 JSON 仍必须被
        runtime-support 投影拒绝。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-006.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["base_path"] = (
        "benchmark_dataset/cccf5baf-e392-47af-a605-65401ef56fe5"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_excel_007_source_uid_drift(
    tmp_path: Path,
) -> None:
    """验证 Excel-007 单文件 manifest 也受固定字节身份约束。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；单文件 manifest 的来源 UID 被替换后不得继续投影为
        `pinned_download_manifest`。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-007.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["base_path"] = (
        "benchmark_dataset/cccf5baf-e392-47af-a605-65401ef56fe5"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_runtime_projection_rejects_excel_009_source_uid_drift(
    tmp_path: Path,
) -> None:
    """验证 Excel-009 五文件 manifest 受固定字节身份约束。

    输入参数：
        tmp_path：pytest 提供的隔离仓库根。
    输出返回值：
        无；来源 UID 被替换为 Excel-006 后，即使条目本身仍是合法
        Office manifest，也必须失败关闭。
    """

    isolated_root = _isolated_runtime_repository(tmp_path)
    manifest_path = (
        isolated_root
        / "benchmark/assets/manifests/Operation-FileOperate-BatchOperationExcel-009.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["base_path"] = (
        "benchmark_dataset/ed04e7b5-a2c6-449a-a493-b22999919008"
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    runtime_support = _load_runtime_support_generator()

    with pytest.raises(runtime_support.RuntimeSupportError):
        runtime_support.build_runtime_support_manifest(isolated_root)


def test_locked_readonly_ppt_candidates_use_exact_pinned_manifest() -> None:
    """验证含 Office 锁文件的 Readonly 候选只绑定真实 PPTX。

    输入参数：
        无；读取 ReadonlyPPT-002/-003 canonical task。
    输出返回值：
        无；两任务只引用各自的严格 manifest，不保留 legacy URL
        或泛化排除模式；锁文件身份由专属 manifest 测试固定。
    """

    for task_id in (
        "InformationRetrieval-FileSearch-ReadonlyPPT-002",
        "InformationRetrieval-FileSearch-ReadonlyPPT-003",
    ):
        task = _canonical_task(task_id)
        assert task["asset_manifest"] == (f"benchmark/assets/manifests/{task_id}.json")
        assert "prepare_script_path" not in task
        assert "prepare_exclude_patterns" not in task
