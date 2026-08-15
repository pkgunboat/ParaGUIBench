"""ReadonlyPPT-002/-003 固定源锁文件排除合同测试。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
from types import ModuleType

import pytest


FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_READONLY_PPT_LOCKFILE_FIXTURE_ROOT"
REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_CASES = (
    (
        "InformationRetrieval-FileSearch-ReadonlyPPT-002",
        "c65ead66-0dca-40e9-993f-affc35bde5bc",
    ),
    (
        "InformationRetrieval-FileSearch-ReadonlyPPT-003",
        "163c86bd-de63-4311-8da5-ff750e8f7961",
    ),
)
PRESENTATION_PATH = "mechine learning.pptx"
PRESENTATION_SIZE = 97_411
PRESENTATION_SHA256 = "fb688cacaf7bbb1227447fe5e43eeed6c0783d378ca1184d09c3015e5f08f264"
LOCK_PATH = "~$mechine learning.pptx"
GENERATOR_PATH = REPO_ROOT / "scripts" / "benchmark" / "readonly_asset_manifests.py"
SCHEMA_PATH = (
    REPO_ROOT
    / "benchmark"
    / "schemas"
    / "readonly-file-search-asset-manifest-v1.schema.json"
)
TASK_SEMANTICS = {
    "InformationRetrieval-FileSearch-ReadonlyPPT-002": {
        "answer_match_mode": "numeric",
        "instruction": (
            "On which page of this PPT was the Q-learning concept first introduced? "
            "Answer in the format: <answer>VALUE</answer>. Only output the tags, no "
            "extra explanation."
        ),
        "answer": "6",
        "evaluator_path": "eval/file_search_readonly_evaluator.py",
        "original_task_id": "InformationRetrieval-FileSearch-Readonlyppt-002",
    },
    "InformationRetrieval-FileSearch-ReadonlyPPT-003": {
        "answer_match_mode": "numeric",
        "instruction": (
            "Which pages in this PPT are pure display pages (images only, no text)? "
            "Answer in the format: <answer>VALUE</answer>. Only output the tags, no "
            "extra explanation."
        ),
        "answer": "4",
        "evaluator_path": "eval/file_search_readonly_evaluator.py",
        "original_task_id": "InformationRetrieval-FileSearch-Readonlyppt-003",
    },
}


def _fixed_source_directory(task_uid: str) -> Path:
    """返回一个真实 Lee 固定 revision 的任务目录。

    输入参数：task_uid 为 ReadonlyPPT-002/-003 的 canonical UUID。
    输出返回值：包含正式 PPTX 与唯一 Office 锁文件的真实目录。
    异常：pytest skip/fail：调用方未配置 download-only fixture，或目标目录缺失。
    """

    raw_root = os.environ.get(FIXTURE_ENVIRONMENT_VARIABLE)
    if raw_root is None:
        pytest.skip(
            f"{FIXTURE_ENVIRONMENT_VARIABLE} is required for download-only fixture"
        )
    candidate = Path(raw_root) / task_uid
    if not candidate.is_dir():
        pytest.fail("ReadonlyPPT fixed-revision fixture is unavailable")
    return candidate


def _copy_fixed_source(tmp_path: Path, task_uid: str) -> Path:
    """复制一个真实固定目录，供单一负例安全变异。

    输入参数：tmp_path 为 pytest 隔离根；task_uid 选择两份真实来源之一。
    输出返回值：字节与成员初始均等同真实 Lee fixture 的可写副本。
    """

    destination = tmp_path / task_uid
    shutil.copytree(_fixed_source_directory(task_uid), destination)
    return destination


def _load_generator() -> ModuleType:
    """加载仓库内 Readonly manifest 生成器。

    输入参数：无；使用固定仓库绝对路径。
    输出返回值：可调用公开 builder/serializer/check 的生成器模块。
    """

    spec = importlib.util.spec_from_file_location(
        "readonly_ppt_lockfile_asset_manifests",
        GENERATOR_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("task_id", "task_uid"), TASK_CASES)
def test_real_fixed_source_exposes_only_verified_presentation(
    task_id: str,
    task_uid: str,
) -> None:
    """真实两成员来源只把正式 PPTX 投影为任务资产。

    输入参数：task_id/task_uid 为两个经用户确认语义不变的 ReadonlyPPT 任务。
    输出返回值：严格 verifier 接受固定源，返回值只含 97,411B PPTX 的
        path/size/SHA；165B 锁文件虽被精确核验，但绝不进入可交付闭集。
    """

    from paraguibench.benchmark.readonly_ppt_assets import (
        verify_readonly_ppt_source_directory,
    )

    verified = verify_readonly_ppt_source_directory(
        task_id,
        _fixed_source_directory(task_uid),
    )

    assert verified.task_id == task_id
    assert verified.task_uid == task_uid
    assert [
        (member.path, member.size, member.sha256)
        for member in verified.deliverable_members
    ] == [(PRESENTATION_PATH, PRESENTATION_SIZE, PRESENTATION_SHA256)]
    assert all(member.path != LOCK_PATH for member in verified.deliverable_members)


@pytest.mark.parametrize(
    "unexpected_name",
    ("third.pptx", ".hidden", "~$another presentation.pptx"),
)
def test_source_rejects_every_unlisted_third_member(
    tmp_path: Path,
    unexpected_name: str,
) -> None:
    """来源闭集拒绝普通、隐藏及泛化锁前缀第三成员。

    输入参数：tmp_path 承载真实来源副本；unexpected_name 覆盖三类
        未列入固定两成员合同的文件名。
    输出返回值：即使第三文件为空且以 ``~$`` 开头，公共 verifier 也必须
        失败关闭，不能按通配规则排除。
    """

    from paraguibench.benchmark.readonly_ppt_assets import (
        ReadonlyPPTSourceError,
        verify_readonly_ppt_source_directory,
    )

    task_id, task_uid = TASK_CASES[0]
    source = _copy_fixed_source(tmp_path, task_uid)
    (source / unexpected_name).write_bytes(b"")

    with pytest.raises(ReadonlyPPTSourceError):
        verify_readonly_ppt_source_directory(task_id, source)


def test_source_rejects_symlink_in_directory_ancestor_chain(tmp_path: Path) -> None:
    """来源路径任一级祖先是 symlink 时失败关闭。

    输入参数：tmp_path 用于创建真实目录副本及指向其父目录的别名。
    输出返回值：即使最终 UID 节点自身是普通目录且字节完全正确，也不能
        经中间 symlink 进入 verifier。
    """

    from paraguibench.benchmark.readonly_ppt_assets import (
        ReadonlyPPTSourceError,
        verify_readonly_ppt_source_directory,
    )

    task_id, task_uid = TASK_CASES[0]
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    shutil.copytree(_fixed_source_directory(task_uid), real_parent / task_uid)
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ReadonlyPPTSourceError):
        verify_readonly_ppt_source_directory(
            task_id,
            alias_parent / task_uid,
        )


def test_source_rejects_cross_task_directory_identity() -> None:
    """相同来源字节也不能跨两个 canonical UID 换位。

    输入参数：无；故意用 ReadonlyPPT-003 的真实目录验证 ReadonlyPPT-002。
    输出返回值：尽管两目录的两个文件大小与摘要完全相同，UID 身份错位仍
        必须失败关闭。
    """

    from paraguibench.benchmark.readonly_ppt_assets import (
        ReadonlyPPTSourceError,
        verify_readonly_ppt_source_directory,
    )

    first_task_id, _first_uid = TASK_CASES[0]
    _second_task_id, second_uid = TASK_CASES[1]

    with pytest.raises(ReadonlyPPTSourceError):
        verify_readonly_ppt_source_directory(
            first_task_id,
            _fixed_source_directory(second_uid),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "presentation_sha",
        "lock_sha",
        "renamed_lock",
        "member_symlink",
        "member_fifo",
        "member_hardlink",
    ),
)
def test_source_rejects_member_identity_or_physical_type_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    """精确两成员合同拒绝摘要、路径、类型与链接身份漂移。

    输入参数：tmp_path 承载真实 source 副本；mutation 覆盖正式 PPTX/锁文件
        同尺寸内容漂移、泛化锁文件改名、symlink、FIFO 与额外 hardlink。
    输出返回值：所有变体均由公共 verifier 失败关闭；不会把通配 ``~$``、
        同尺寸或可跟随链接视为有效来源。
    """

    from paraguibench.benchmark.readonly_ppt_assets import (
        ReadonlyPPTSourceError,
        verify_readonly_ppt_source_directory,
    )

    task_id, task_uid = TASK_CASES[0]
    source = _copy_fixed_source(tmp_path, task_uid)
    if mutation in {"presentation_sha", "lock_sha"}:
        target = source / (
            PRESENTATION_PATH if mutation == "presentation_sha" else LOCK_PATH
        )
        payload = bytearray(target.read_bytes())
        payload[-1] ^= 1
        target.write_bytes(payload)
    elif mutation == "renamed_lock":
        (source / LOCK_PATH).rename(source / "~$another presentation.pptx")
    elif mutation == "member_symlink":
        (source / LOCK_PATH).unlink()
        (source / LOCK_PATH).symlink_to(PRESENTATION_PATH)
    elif mutation == "member_fifo":
        (source / LOCK_PATH).unlink()
        os.mkfifo(source / LOCK_PATH)
    elif mutation == "member_hardlink":
        os.link(source / PRESENTATION_PATH, tmp_path / "outside-hardlink.pptx")
    else:  # pragma: no cover - 参数表闭集由测试自身固定
        raise AssertionError("unknown test mutation")

    with pytest.raises(ReadonlyPPTSourceError):
        verify_readonly_ppt_source_directory(task_id, source)


def test_source_rejects_member_change_after_its_bytes_were_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两成员核验窗口内较早成员被原位修改时仍失败关闭。

    输入参数：tmp_path 承载真实 source 副本；monkeypatch 在 verifier 开始读取
        165B 锁文件时原位修改已读完的 97,411B PPTX。
    输出返回值：公共 verifier 必须检测跨完整两成员窗口的 inode 身份漂移，
        不能因目录成员名未变、目录 mtime 未变而返回安全投影。
    """

    from paraguibench.benchmark import readonly_ppt_assets

    task_id, task_uid = TASK_CASES[0]
    source = _copy_fixed_source(tmp_path, task_uid)
    original_read = readonly_ppt_assets.os.read
    changed = False

    def _read_with_interleaved_change(descriptor: int, size: int) -> bytes:
        """在锁文件首次读取前改写已经验过的 PPTX。

        输入参数：descriptor/size 原样转发给真实 ``os.read``。
        输出返回值：真实系统调用返回的 bytes；只执行一次受控来源变异。
        """

        nonlocal changed
        if not changed and os.fstat(descriptor).st_size == 165:
            target = source / PRESENTATION_PATH
            payload = bytearray(target.read_bytes())
            payload[-1] ^= 1
            target.write_bytes(payload)
            changed = True
        return original_read(descriptor, size)

    monkeypatch.setattr(readonly_ppt_assets.os, "read", _read_with_interleaved_change)

    with pytest.raises(readonly_ppt_assets.ReadonlyPPTSourceError):
        readonly_ppt_assets.verify_readonly_ppt_source_directory(task_id, source)
    assert changed is True


@pytest.mark.parametrize(("task_id", "task_uid"), TASK_CASES)
def test_canonical_task_changes_only_to_single_pinned_manifest_binding(
    task_id: str,
    task_uid: str,
) -> None:
    """canonical 只替换 legacy 来源，不改变原题语义或 evaluator。

    输入参数：task_id/task_uid 选择两个用户已确认不改语义的任务。
    输出返回值：题目、答案与 evaluator 保持原值；legacy URL 被唯一 manifest
        引用替代，且未增加任何用于强制 Agent 并行策略的字段。
    """

    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert task == {
        "task_id": task_id,
        "task_uid": task_uid,
        "task_type": "QA",
        "task_tag": "FileSearch",
        **TASK_SEMANTICS[task_id],
        "task_source": "self",
        "asset_manifest": f"benchmark/assets/manifests/{task_id}.json",
    }


@pytest.mark.parametrize(("task_id", "task_uid"), TASK_CASES)
def test_generator_projects_only_the_verified_presentation(
    task_id: str,
    task_uid: str,
) -> None:
    """生成器把固定两成员 source 投影成单文件 manifest。

    输入参数：task_id/task_uid 选择两个严格锁文件任务。
    输出返回值：每份文档绑定固定 Lee revision/UID，files 只含真实 PPTX；
        锁文件路径、大小和摘要均不进入 guest 可下载 manifest。
    """

    generator = _load_generator()
    documents = generator.build_readonly_asset_manifests(REPO_ROOT)
    document = documents[f"benchmark/assets/manifests/{task_id}.json"]

    assert document["asset_set_id"] == task_id
    assert document["source"] == {
        "provider": "huggingface_dataset",
        "repository": "leeLegendary/Parallel_benchmark",
        "revision": "13bf942dfab6f9d71f16f0958f1edd8b436c7afa",
        "base_path": f"benchmark_dataset/{task_uid}",
        "license_status": "unverified",
    }
    assert document["distribution_policy"] == "download_only"
    assert document["files"] == [
        {
            "path": PRESENTATION_PATH,
            "size": PRESENTATION_SIZE,
            "sha256": PRESENTATION_SHA256,
            "media_type": (
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            ),
        }
    ]
    serialized = generator.serialize_readonly_asset_manifest(document)
    assert LOCK_PATH.encode("utf-8") not in serialized
    assert b"6907f9789ec20d0aee0f01875ab9aa54" not in serialized


@pytest.mark.parametrize(("task_id", "_task_uid"), TASK_CASES)
def test_checked_in_manifest_matches_generator_and_runtime_resolver(
    task_id: str,
    _task_uid: str,
) -> None:
    """落盘 manifest 与生成器逐字节一致且 runtime 仅解析正式 PPTX。

    输入参数：task_id 选择两个任务；_task_uid 仅保持参数表单一来源。
    输出返回值：checked-in JSON 与确定性 builder 一致，统一 runtime resolver
        得到 download-only 单文件闭集且不存在锁文件。
    """

    from paraguibench.runtime.assets import TaskAssetMode, resolve_task_assets

    generator = _load_generator()
    relative_path = f"benchmark/assets/manifests/{task_id}.json"
    document = generator.build_readonly_asset_manifests(REPO_ROOT)[relative_path]
    manifest_path = REPO_ROOT / relative_path
    assert manifest_path.read_bytes() == (
        generator.serialize_readonly_asset_manifest(document)
    )
    task = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )

    resolved = resolve_task_assets(REPO_ROOT, task)

    assert resolved.mode is TaskAssetMode.PINNED_DOWNLOAD_MANIFEST
    assert resolved.manifest is not None
    assert [asset.path for asset in resolved.manifest.files] == [PRESENTATION_PATH]
    assert all(asset.path != LOCK_PATH for asset in resolved.manifest.files)


def test_schema_closed_set_includes_both_strict_lockfile_tasks() -> None:
    """Readonly 专属 schema 显式允许两个新单文件 manifest。

    输入参数：无；读取仓库 schema 与生成器文档。
    输出返回值：asset_set_id 闭集精确扩为十一项并覆盖全部 builder 输出；
        共享 source revision 与 file 字段仍保持严格闭集。
    """

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    generator = _load_generator()
    documents = generator.build_readonly_asset_manifests(REPO_ROOT)
    asset_set_ids = set(schema["properties"]["asset_set_id"]["enum"])

    assert len(asset_set_ids) == 11
    assert {task_id for task_id, _task_uid in TASK_CASES} <= asset_set_ids
    assert {document["asset_set_id"] for document in documents.values()} == (
        asset_set_ids
    )
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["source"]["additionalProperties"] is False
    assert schema["$defs"]["file"]["additionalProperties"] is False
    assert schema["$defs"]["source"]["properties"]["revision"]["const"] == (
        "13bf942dfab6f9d71f16f0958f1edd8b436c7afa"
    )
