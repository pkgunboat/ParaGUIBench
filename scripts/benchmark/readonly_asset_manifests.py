#!/usr/bin/env python3
"""确定性生成已迁移 FileSearch Readonly 任务的固定资产清单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any

from paraguibench.benchmark.readonly_ppt_assets import readonly_ppt_task_assets


LEE_REPOSITORY = "leeLegendary/Parallel_benchmark"
LEE_REVISION = "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
_MANIFEST_ROOT = PurePosixPath("benchmark/assets/manifests")
_PDF = "application/pdf"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EXPECTED_TASK_COUNT = 11
_EXPECTED_FILE_COUNT = 32


def _strict_lockfile_task_assets(
    task_id: str,
) -> tuple[str, tuple[tuple[str, int, str, str], ...]]:
    """把严格核验合同投影成生成器的单文件 tuple。

    输入参数：task_id 必须是 ReadonlyPPT-002/-003 之一。
    输出返回值：固定 task UID 与只含真实 PPTX 的 path/size/SHA/MIME tuple；
        精确 Office 锁文件只在 source verifier 中核验，绝不进入 manifest。
    """

    task_uid, members = readonly_ppt_task_assets(task_id)
    files: list[tuple[str, int, str, str]] = []
    for member in members:
        if member.media_type is None:
            raise RuntimeError("ReadonlyPPT deliverable media type 无效")
        files.append((member.path, member.size, member.sha256, member.media_type))
    return task_uid, tuple(files)


# task UID 和文件元数据均来自固定 revision 的公开下载字节；
# tuple 顺序就是 manifest 闭集的确定性顺序。
_TASK_ASSETS: dict[
    str,
    tuple[str, tuple[tuple[str, int, str, str], ...]],
] = {
    "InformationRetrieval-FileSearch-Readonly-002": (
        "1dcb866c-9a32-452b-a398-59785ddba699",
        (
            (
                "Xiaomi Corp_23Q1_ER_ENG_vF_Upload.pdf",
                2_494_687,
                "2d2a956098e98b9718384e63b2e8f395e3dd1e707e1238eddd9f0dcd124c88d3",
                _PDF,
            ),
            (
                "announcement.pdf",
                929_454,
                "f9f140951d1a3cf0933bc2513694998f3f9d077a245851359b92b68ae3870f23",
                _PDF,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-Readonly-003": (
        "04aad45f-ff78-4508-9403-b60cb8f357ff",
        (
            (
                "apology_letter.docx",
                14_990,
                "400151f955e03e31cf6c37919ea82563aff17e726a3ea24ca9338d9941260a1e",
                _DOCX,
            ),
            (
                "climite_news.docx",
                14_976,
                "aa66e42c2edda7d7628c7a4f095e1931f55eb0665eb290eecd0663b7c695afac",
                _DOCX,
            ),
            (
                "project_update.docx",
                14_565,
                "303f6b79893d37a480005e1547bec88b80644ff087086cfa80af67847fea0b62",
                _DOCX,
            ),
            (
                "sci-fi_narrative.docx",
                14_685,
                "627ba5861e92494fbc53c77925660e508534cc94f18a1b161b2a896f3e361005",
                _DOCX,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-ReadonlyPPT-001": (
        "06b65a9a-7fe5-4fa0-a8b7-27275d8c29e9",
        (
            (
                "ML.pptx",
                37_695,
                "e044e7ebeafd18dbe789a346cb95f2a1a230b62c6330ae19f0e9517921a6f241",
                _PPTX,
            ),
            (
                "The source of AI.pptx",
                37_433,
                "f342a5152b2cfca9cc3117f6b5a681ad56e662fce0e0e3bb84af63ab960da11d",
                _PPTX,
            ),
            (
                "welcome.pptx",
                37_657,
                "77bafb3ac9bc92d5fdd287b2d9987d814b8bb87f90402cc3fbc8b4a4a438c6c8",
                _PPTX,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-ReadonlyPPT-002": (
        _strict_lockfile_task_assets("InformationRetrieval-FileSearch-ReadonlyPPT-002")
    ),
    "InformationRetrieval-FileSearch-ReadonlyPPT-003": (
        _strict_lockfile_task_assets("InformationRetrieval-FileSearch-ReadonlyPPT-003")
    ),
    "InformationRetrieval-FileSearch-ReadonlyPPT-004": (
        "cf706a4b-01a8-40be-92b9-539e77024928",
        (
            (
                "mechine learning.pptx",
                97_411,
                "fb688cacaf7bbb1227447fe5e43eeed6c0783d378ca1184d09c3015e5f08f264",
                _PPTX,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-ReadonlyPPT-005": (
        "1fad9312-f060-4a1e-8208-2e65f6e950a0",
        (
            (
                "164_3.pptx",
                321_773,
                "7b808cd7699e1384a1b55d36a2f75c7446b67f6339d5381477ae980180cf26c5",
                _PPTX,
            ),
            (
                "24_8.pptx",
                327_494,
                "dbad3e3205aaf38eb428868c6f1db659754fa79d9e1bbcc306683c57f640175a",
                _PPTX,
            ),
            (
                "MLA_Workshop_061X_Works_Cited.pptx",
                595_572,
                "15c5875d7731c3459a2b54e0934a53770e8c84fd206917ad280001bd821ff008",
                _PPTX,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-ReadonlyWord-001": (
        "4f870c1f-b01c-4a10-90c3-1e3f0eab0373",
        (
            (
                "Fair.docx",
                13_797,
                "d992e2f5ed1d130dd213750d7d09a81b8e4753f5210885bc4be80972a1eb3c54",
                _DOCX,
            ),
            (
                "Seminar.docx",
                13_869,
                "a394f35c459a9252a54eae9ab790607d7f64310e7b8ceb967237db341d359cee",
                _DOCX,
            ),
            (
                "WorkShop.docx",
                14_081,
                "6920b96732571a3acfe7aab9e86323a806c24b7b83cc1744120646e2e92dae70",
                _DOCX,
            ),
            (
                "meeting.docx",
                13_780,
                "1511b37b9807e992c4bf5cc4a03203088dee1b7ca7545ca5b5422c1b5ed78273",
                _DOCX,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-ReadonlyWord-002": (
        "d2783609-b215-457d-99bb-3e3153b286cb",
        (
            (
                "Currencies_1.docx",
                802_247,
                "cbff03364a83fb76f2272415b0c98d287bd995e10254a3124cb5b3372799ebaf",
                _DOCX,
            ),
            (
                "Currencies_2.docx",
                976_969,
                "517c60a51bbca48d9ca19880d6a5aa14632c6289f31ea82572a024a2c5a71584",
                _DOCX,
            ),
            (
                "Currencies_3.docx",
                980_835,
                "1bfc57fcac94936842f0b8a476a3b6c90c49dd5574518488cbfb4db7e1cb6423",
                _DOCX,
            ),
            (
                "Currencies_4.docx",
                658_012,
                "9a04d0096d9cd1b6d0803b24a3068772084196f198a2f8cfe073d73014d1e147",
                _DOCX,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-ReadonlyWord-003": (
        "b568284e-3675-4352-a2ff-4a8305c99388",
        (
            (
                "meeting1.docx",
                13_835,
                "f68244511394ea5dbb4fe63509b8bf5030b3639b9a0633bb3afeb26222535700",
                _DOCX,
            ),
            (
                "meeting2.docx",
                13_875,
                "b4fbc3e995099b033772045f3e3ea20cf4e09223a0b32eee5ecb9ccf51027d17",
                _DOCX,
            ),
            (
                "meeting3.docx",
                14_027,
                "993bd7bde2e8a6b7c051916de2b682e91af5aa8373f8898df5c30be2378a2fed",
                _DOCX,
            ),
            (
                "meeting4.docx",
                13_986,
                "f9b543512fa478524e39834a3c131070c59ce9a9415e5a80b435fcd049384214",
                _DOCX,
            ),
            (
                "meeting5.docx",
                13_817,
                "fe67a0b1270f9e0a53e7d49facfe49abc4f4b440963e372594c30cf3d56a9221",
                _DOCX,
            ),
        ),
    ),
    "InformationRetrieval-FileSearch-ReadonlyWord-004": (
        "134a34d3-9f52-44f6-b7bd-12945c2479f2",
        (
            (
                "paper1.pdf",
                5_296_750,
                "54bcd2dd05dc618849e8a94d8b88fe3eeb37f80e96e200600d38f1f733931678",
                _PDF,
            ),
            (
                "paper2.pdf",
                11_947_867,
                "1b31e77fb24d25d7598f2c49e955d12a28b95a6dabad34acdac40f44bfb7a139",
                _PDF,
            ),
            (
                "paper3.pdf",
                2_215_244,
                "bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697",
                _PDF,
            ),
            (
                "paper4.pdf",
                1_609_513,
                "e9a0d3128767db616085dc0f4e6e455e672e89af823e8ed1282793682787395a",
                _PDF,
            ),
        ),
    ),
}


class ReadonlyAssetManifestError(RuntimeError):
    """表示 Readonly 资产目录、canonical 绑定或文件闭集漂移。"""


def manifest_relative_path(task_id: str) -> str:
    """返回一个 Readonly 任务的固定 manifest 仓库相对路径。

    输入参数：
        task_id：十一任务固定闭集中的 canonical ID。
    输出返回值：
        使用 POSIX 分隔符的 manifest JSON 仓库相对路径。
    异常：
        ReadonlyAssetManifestError：task ID 不在固定闭集。
    """

    if task_id not in _TASK_ASSETS:
        raise ReadonlyAssetManifestError("Readonly asset task identity 无效")
    return str(_MANIFEST_ROOT / f"{task_id}.json")


def build_readonly_asset_manifests(
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    """从固定目录构造十一份确定性 download-only manifest。

    输入参数：
        repo_root：包含 canonical tasks 的 ParaGUIBench 仓库根。
    输出返回值：
        manifest 仓库相对路径到 JSON object 的十一项映射，
        文件总数固定为 32。函数不访问网络，不写文件。
    异常：
        ReadonlyAssetManifestError：仓库根、task UID、canonical 资产
        绑定或固定计数发生漂移。
    """

    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise ReadonlyAssetManifestError("Readonly asset repo root 无效")
    documents: dict[str, dict[str, Any]] = {}
    file_total = 0
    for task_id in sorted(_TASK_ASSETS, key=lambda value: value.encode("utf-8")):
        task_uid, files = _TASK_ASSETS[task_id]
        expected_manifest = manifest_relative_path(task_id)
        task = _load_canonical_task(repo_root, task_id)
        if task.get("task_uid") != task_uid:
            raise ReadonlyAssetManifestError("Readonly canonical task UID 漂移")
        if (
            task.get("asset_manifest") != expected_manifest
            or "prepare_script_path" in task
        ):
            raise ReadonlyAssetManifestError("Readonly canonical task 资产绑定漂移")
        document = _build_manifest(task_id, task_uid, files)
        documents[expected_manifest] = document
        file_total += len(document["files"])
    if len(documents) != _EXPECTED_TASK_COUNT or file_total != _EXPECTED_FILE_COUNT:
        raise ReadonlyAssetManifestError("Readonly asset manifest 数量闭包漂移")
    return documents


def serialize_readonly_asset_manifest(document: dict[str, Any]) -> bytes:
    """把一份 Readonly manifest 编码为唯一 UTF-8 JSON 字节。

    输入参数：
        document：``build_readonly_asset_manifests`` 返回的 JSON object。
    输出返回值：
        两空格缩进、保留 Unicode、末尾单换行的确定性字节。
    """

    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_readonly_asset_manifests(repo_root: Path) -> None:
    """生成并覆盖职责范围内的十一份 Readonly manifest。

    输入参数：
        repo_root：ParaGUIBench 仓库根；目标父目录按需创建。
    输出返回值：
        无；每个目标只写 builder 的确定性序列化结果。
    """

    for relative_path, document in build_readonly_asset_manifests(repo_root).items():
        target = repo_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(serialize_readonly_asset_manifest(document))


def check_readonly_asset_manifests(repo_root: Path) -> bool:
    """逐字节检查十一份 Readonly manifest 是否一致。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
    输出返回值：
        十一份文件全部存在且逐字节一致返回 ``True``，否则
        返回 ``False``；检查过程不写文件。
    """

    try:
        documents = build_readonly_asset_manifests(repo_root)
    except ReadonlyAssetManifestError:
        return False
    for relative_path, document in documents.items():
        try:
            actual = (repo_root / relative_path).read_bytes()
        except OSError:
            return False
        if actual != serialize_readonly_asset_manifest(document):
            return False
    return True


def _load_canonical_task(repo_root: Path, task_id: str) -> dict[str, Any]:
    """读取并验证一份固定闭集内的 canonical task。

    输入参数：
        repo_root：ParaGUIBench 仓库根。
        task_id：待读取的固定 canonical task ID。
    输出返回值：
        已确认文件名与内部 task_id 一致的 JSON object。
    异常：
        ReadonlyAssetManifestError：文件不可读、JSON 无效或身份漂移。
    """

    try:
        task = json.loads(
            (repo_root / "benchmark" / "tasks" / f"{task_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        raise ReadonlyAssetManifestError("Readonly canonical task 无法读取") from None
    if not isinstance(task, dict) or task.get("task_id") != task_id:
        raise ReadonlyAssetManifestError("Readonly canonical task 身份漂移")
    return task


def _build_manifest(
    task_id: str,
    task_uid: str,
    files: tuple[tuple[str, int, str, str], ...],
) -> dict[str, Any]:
    """由固定 task identity 与已核验文件闭集构造 manifest。

    输入参数：
        task_id：canonical task ID，同时作为 asset_set_id。
        task_uid：固定 Hugging Face base path 的 canonical UID。
        files：按确定性顺序排列的 path/size/SHA-256/MIME 元组。
    输出返回值：
        符合 Readonly 专属 schema 的 download-only JSON object。
    """

    return {
        "schema_version": 1,
        "asset_set_id": task_id,
        "source": {
            "provider": "huggingface_dataset",
            "repository": LEE_REPOSITORY,
            "revision": LEE_REVISION,
            "base_path": f"benchmark_dataset/{task_uid}",
            "license_status": "unverified",
        },
        "distribution_policy": "download_only",
        "files": [
            {
                "path": path,
                "size": size,
                "sha256": sha256,
                "media_type": media_type,
            }
            for path, size, sha256, media_type in files
        ],
    }


def _parse_arguments() -> argparse.Namespace:
    """解析 generate/check 子命令与仓库根。

    输入参数：
        无；读取当前进程参数。
    输出返回值：
        包含 ``command`` 与 ``repo_root`` 的 argparse namespace。
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    return parser.parse_args()


def main() -> int:
    """执行 Readonly manifest 的确定性生成或逐字节检查。

    输入参数：
        无；使用 ``_parse_arguments`` 的 CLI 参数。
    输出返回值：
        generate 成功或 check 一致返回 0；漂移返回 1。
    """

    arguments = _parse_arguments()
    root = arguments.repo_root.resolve()
    if arguments.command == "generate":
        write_readonly_asset_manifests(root)
        print("Readonly asset manifests generated: tasks=11; files=32")
        return 0
    if check_readonly_asset_manifests(root):
        print("Readonly asset manifests valid: tasks=11; files=32")
        return 0
    print("Readonly asset manifests drifted")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
