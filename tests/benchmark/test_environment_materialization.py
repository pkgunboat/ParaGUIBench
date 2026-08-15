"""Benchmark Task 环境绑定物化测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paraguibench.benchmark import TaskMaterializationError, materialize_task

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_materialization_replaces_bindings_across_the_whole_task() -> None:
    """验证 instruction、列表和 guest path 使用同一非敏感环境绑定。

    输入参数：
        无；测试构造不含真实部署地址的合成 canonical task。
    输出返回值：
        无；物化结果必须完整替换 token，同时保持 canonical 输入不变。
    """

    canonical_task = {
        "task_id": "synthetic-materialization-task",
        "required_environment_bindings": [
            "GUEST_SHARED_DIR",
        ],
        "instruction": (
            "Read ${GUEST_SHARED_DIR}/paper.pdf and ${GUEST_SHARED_DIR}/notes.txt"
        ),
        "input_files": [
            "${GUEST_SHARED_DIR}/paper.pdf",
            "${GUEST_SHARED_DIR}/notes.txt",
        ],
        "agent_start_context": {
            "guest_path": "${GUEST_SHARED_DIR}/paper.pdf",
        },
    }

    materialized = materialize_task(
        canonical_task,
        {
            "GUEST_SHARED_DIR": "/mnt/paraguibench/shared",
        },
    )

    assert materialized["input_files"] == [
        "/mnt/paraguibench/shared/paper.pdf",
        "/mnt/paraguibench/shared/notes.txt",
    ]
    assert (
        materialized["agent_start_context"]["guest_path"]
        == "/mnt/paraguibench/shared/paper.pdf"
    )
    assert "${GUEST_SHARED_DIR}" in canonical_task["instruction"]


def test_materialization_rejects_missing_required_binding() -> None:
    """验证缺少声明绑定时在物化前整体失败。

    输入参数：
        无；测试构造一个声明两个绑定但只提供一个绑定的 task。
    输出返回值：
        无；接口必须抛出可识别异常，并指出缺少的绑定名称。
    """

    canonical_task = {
        "task_id": "synthetic-missing-binding",
        "required_environment_bindings": [
            "GUEST_SHARED_DIR",
            "DOWNLOADS_DIR",
        ],
        "instruction": (
            "Copy ${GUEST_SHARED_DIR}/paper.pdf to ${DOWNLOADS_DIR}/paper.pdf"
        ),
    }

    with pytest.raises(TaskMaterializationError, match="DOWNLOADS_DIR"):
        materialize_task(
            canonical_task,
            {"GUEST_SHARED_DIR": "/mnt/paraguibench/shared"},
        )


def test_materialization_rejects_credential_binding_without_leaking_value() -> None:
    """验证 task 环境绑定不能承载凭据，且异常不包含绑定值。

    输入参数：
        无；测试使用明显的哨兵字符串模拟不应进入 task 的密钥。
    输出返回值：
        无；接口必须拒绝凭据类名称，异常中只允许出现名称而不能出现值。
    """

    sentinel_secret = "sentinel-secret-value-must-not-leak"
    canonical_task = {
        "task_id": "synthetic-secret-binding",
        "required_environment_bindings": ["MODEL_API_KEY"],
        "instruction": "Use ${MODEL_API_KEY}",
    }

    with pytest.raises(TaskMaterializationError) as captured:
        materialize_task(
            canonical_task,
            {"MODEL_API_KEY": sentinel_secret},
        )

    assert "MODEL_API_KEY" in str(captured.value)
    assert sentinel_secret not in str(captured.value)


@pytest.mark.parametrize(
    "invalid_path",
    ["relative/shared", "/mnt/paraguibench/../private"],
)
def test_materialization_rejects_unsafe_directory_binding(
    invalid_path: str,
) -> None:
    """验证 ``*_DIR`` 绑定只能使用无路径穿越的 POSIX 绝对路径。

    输入参数：
        invalid_path：相对路径或含父目录跳转的合成路径。
    输出返回值：
        无；接口必须在字符串替换前拒绝不安全目录。
    """

    canonical_task = {
        "task_id": "synthetic-unsafe-directory",
        "required_environment_bindings": ["GUEST_SHARED_DIR"],
        "instruction": "Read ${GUEST_SHARED_DIR}/paper.pdf",
    }

    with pytest.raises(TaskMaterializationError, match="GUEST_SHARED_DIR"):
        materialize_task(
            canonical_task,
            {"GUEST_SHARED_DIR": invalid_path},
        )


def test_settings_task_uses_relative_asset_context_without_early_binding() -> None:
    """验证 Settings-003 在 VM 前可以直接物化且不硬编码 guest home。

    输入参数：
        无；读取 release-v1 中已知需要 guest 文件的正式任务。
    输出返回值：
        无；canonical task 不保存镜像用户名或运行前无法
        提供的 binding，environment 将在上传后使用相对文件名构造路径。
    """

    task_path = (
        REPO_ROOT / "benchmark" / "tasks" / "Operation-WebOperate-Settings-003.json"
    )
    canonical_task = json.loads(task_path.read_text(encoding="utf-8"))

    assert "required_environment_bindings" not in canonical_task
    assert canonical_task["agent_start_context"] == {
        "type": "local_pdf",
        "asset_relative_path": "2206.08853.pdf",
        "open_with": "chrome",
        "target": "all_vms",
    }
    assert "${" not in canonical_task["instruction"]

    materialized = materialize_task(canonical_task, {})
    assert materialized == canonical_task
