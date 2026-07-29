"""Release task 的 trusted、agent 与 audit 三投影测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from paraguibench.benchmark import (
    TaskPreparationError,
    prepare_release_task,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_TASK_ID = "Operation-OnlineShopping-Checkout-001"
REPRESENTATIVE_TASK_ID = "InformationRetrieval-FileSearch-Readonly-001"


def test_prepare_checkout_task_builds_consistent_private_projections() -> None:
    """验证 checkout fixture 只在可信内存和 Agent instruction 中物化。

    输入参数：
        无；读取 release-v1 的公开合成 checkout fixture。
    输出返回值：
        无；trusted/agent 内容一致，audit 只保留 fixture 身份和摘要。
    """

    prepared = prepare_release_task(
        REPO_ROOT,
        CHECKOUT_TASK_ID,
        environment_bindings={},
    )

    agent_instruction = prepared.agent_task["instruction"]
    trusted_instruction = prepared.trusted_task["instruction"]
    assert agent_instruction == trusted_instruction
    assert "{{checkout_profile}}" not in agent_instruction

    expected_order = (
        "ParaGUI Test User",
        "checkout-v1@example.invalid",
        "Benchmark Avenue",
        "100",
        "94107",
        "San Francisco",
        "CA",
        "USA",
        "4242424242424242",
        "123",
        "12/39",
    )
    positions = [agent_instruction.index(value) for value in expected_order]
    assert positions == sorted(positions)
    assert "answer" not in prepared.agent_task
    assert prepared.trusted_task["answer"]
    assert (
        prepared.trusted_task["resolved_fixtures"]["checkout_profile"][
            "fixture_id"
        ]
        == "webmall.checkout-profile.synthetic-public.v1"
    )

    serialized_audit = json.dumps(
        prepared.audit_metadata,
        ensure_ascii=False,
        sort_keys=True,
    )
    for forbidden in (
        "checkout-v1@example.invalid",
        "Benchmark Avenue",
        "4242424242424242",
        '"cvv"',
        '"instruction"',
    ):
        assert forbidden not in serialized_audit
    fixture_record = prepared.audit_metadata["materialization"][
        "fixture_refs"
    ][0]
    assert fixture_record["fixture_id"] == (
        "webmall.checkout-profile.synthetic-public.v1"
    )
    assert len(fixture_record["sha256"]) == 64


def test_prepare_plain_task_keeps_gold_trusted_but_not_agent_or_audit() -> None:
    """验证普通任务同样隔离 gold、instruction 与审计元数据。

    输入参数：
        无；读取已真实通过 live gate 的代表任务。
    输出返回值：
        无；trusted 可供 evaluator 使用，Agent 与 audit 不包含 gold。
    """

    prepared = prepare_release_task(
        REPO_ROOT,
        REPRESENTATIVE_TASK_ID,
        environment_bindings={},
    )

    assert prepared.trusted_task["answer"]
    assert prepared.agent_task["instruction"]
    assert "answer" not in prepared.agent_task
    assert "instruction" not in prepared.audit_metadata
    assert "answer" not in prepared.audit_metadata
    assert prepared.audit_metadata["task_id"] == REPRESENTATIVE_TASK_ID
    assert len(prepared.audit_metadata["canonical_task_sha256"]) == 64
    assert prepared.audit_metadata["materialization"] == {
        "schema_version": 1,
        "environment_binding_names": [],
        "fixture_refs": [],
    }


def test_prepare_rejects_schema_invalid_fixture_without_echoing_value(
    tmp_path: Path,
) -> None:
    """验证即使 manifest 摘要同步篡改，runtime 仍独立检查 fixture schema。

    输入参数：
        tmp_path：pytest 临时仓库；只复制目标 task、fixture 与 manifest。
    输出返回值：
        无；未知 fixture 字段被拒绝，异常不回显 synthetic sentinel。
    """

    sentinel = "fixture-private-sentinel-must-not-echo"
    for relative_path in (
        "benchmark/manifests/release-v1.json",
        "benchmark/tasks/Operation-OnlineShopping-Checkout-001.json",
        "benchmark/fixtures/webmall/checkout-profile-v1.json",
    ):
        source = REPO_ROOT / relative_path
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    fixture_path = (
        tmp_path
        / "benchmark"
        / "fixtures"
        / "webmall"
        / "checkout-profile-v1.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["debug"] = sentinel
    fixture_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fixture_digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()

    manifest_path = (
        tmp_path / "benchmark" / "manifests" / "release-v1.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixtures"][0]["sha256"] = fixture_digest
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TaskPreparationError) as captured:
        prepare_release_task(
            tmp_path,
            CHECKOUT_TASK_ID,
            environment_bindings={},
        )

    assert "fixture" in str(captured.value)
    assert sentinel not in str(captured.value)
