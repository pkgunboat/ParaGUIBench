"""PreparedTask audit 投影与 RunStore 严格写入边界测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paraguibench.benchmark import prepare_release_task
from paraguibench.integrations.webmall import (
    WebMallURLRegistry,
    load_webmall_environment_manifest,
)
from paraguibench.runstore import RunStore
from paraguibench.runtime.webmall_preparation import (
    materialize_webmall_prepared_task,
)
from tests.runstore._audit import synthetic_run_version_vector

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_TASK_ID = "Operation-OnlineShopping-Checkout-001"


def test_checkout_attempt_persists_only_audit_projection(
    tmp_path: Path,
) -> None:
    """验证 checkout Agent 可见资料不会进入 RunStore 任一文件。

    输入参数：
        tmp_path：pytest 提供的 repo 外临时 RunStore 根目录。
    输出返回值：
        无；task snapshot 只含 fixture 身份，整个目录不含 profile 值或
        materialized instruction。
    """

    prepared = prepare_release_task(
        REPO_ROOT,
        CHECKOUT_TASK_ID,
        environment_bindings={},
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-checkout-audit",
        run_record={"release_id": "release-v1"},
        version_vector=synthetic_run_version_vector(),
    )
    attempt = store.start_attempt(
        run_id="run-checkout-audit",
        task_id=CHECKOUT_TASK_ID,
        attempt_id="attempt-001",
        task_record=prepared.audit_metadata,
    )

    task_snapshot = json.loads(
        (attempt.path.parents[1] / "task.json").read_text(encoding="utf-8")
    )
    assert task_snapshot["task"]["task_id"] == CHECKOUT_TASK_ID
    assert (
        task_snapshot["task"]["materialization"]["fixture_refs"][0]["fixture_id"]
        == "webmall.checkout-profile.synthetic-public.v1"
    )
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for forbidden in (
        b"checkout-v1@example.invalid",
        b"Benchmark Avenue",
        b"4242424242424242",
        b"Pay via credit card",
    ):
        assert forbidden not in persisted


def test_materialized_webmall_attempt_persists_only_binding_identities(
    tmp_path: Path,
) -> None:
    """验证 WebMall 部署物化后仍能通过 RunStore 严格 audit schema。

    输入参数：
        tmp_path：pytest 提供的任务级 RunStore 根。
    输出返回值：
        无；task.json 仅记录 manifest、store universe 和环境变量
        名，不记录四店 origin、checkout profile 或 Agent instruction。
    """

    prepared = prepare_release_task(
        REPO_ROOT,
        CHECKOUT_TASK_ID,
        environment_bindings={},
    )
    manifest = load_webmall_environment_manifest(
        REPO_ROOT / "environments" / "webmall" / "environment-manifest.json"
    )
    origins = {
        f"store-{index}": f"https://private-runtime-{index}.example.invalid"
        for index in range(1, 5)
    }
    materialized = materialize_webmall_prepared_task(
        prepared,
        manifest=manifest,
        registry=WebMallURLRegistry(origins),
    )
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-webmall-materialized-audit",
        run_record={"release_id": "release-v1"},
        version_vector=synthetic_run_version_vector(),
    )

    attempt = store.start_attempt(
        run_id="run-webmall-materialized-audit",
        task_id=CHECKOUT_TASK_ID,
        attempt_id="attempt-001",
        task_record=materialized.audit_metadata,
    )

    task_snapshot = json.loads(
        (attempt.path.parents[1] / "task.json").read_text(encoding="utf-8")
    )["task"]
    assert task_snapshot["webmall_environment"] == {
        "manifest_id": "webmall.reference-four-stores.v1",
        "store_universe_id": "webmall.four-stores.v1",
        "origin_binding_names": [
            f"PARAGUIBENCH_WEBMALL_STORE_{index}_ORIGIN" for index in range(1, 5)
        ],
    }
    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    for forbidden in (
        *origins.values(),
        "checkout-v1@example.invalid",
        "4242424242424242",
        "Pay via credit card",
    ):
        assert forbidden.encode() not in persisted


def test_runstore_rejects_unknown_task_audit_field_before_write(
    tmp_path: Path,
) -> None:
    """验证 task snapshot 使用 allowlist-first，而非仅依赖关键词脱敏。

    输入参数：
        tmp_path：pytest 临时 RunStore 根目录。
    输出返回值：
        无；未知 instruction 字段在 task.json 写入前被拒绝。
    """

    sentinel = "full-instruction-must-never-persist"
    store = RunStore(tmp_path)
    store.start_run(
        run_id="run-reject-full-task",
        run_record={"release_id": "release-v1"},
        version_vector=synthetic_run_version_vector(),
    )
    invalid_audit = {
        "release_id": "release-v1",
        "canonical_task_sha256": "0" * 64,
        "task_id": "synthetic-task",
        "materialization": {
            "schema_version": 1,
            "environment_binding_names": [],
            "fixture_refs": [],
        },
        "instruction": sentinel,
    }

    with pytest.raises(ValueError, match="task audit"):
        store.start_attempt(
            run_id="run-reject-full-task",
            task_id="synthetic-task",
            attempt_id="attempt-001",
            task_record=invalid_audit,
        )

    persisted = b"\n".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )
    assert sentinel.encode() not in persisted
