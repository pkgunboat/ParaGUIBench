"""跨工作树 evaluator observation 差分 parity harness 测试。"""

from __future__ import annotations

import json
from pathlib import Path

from paraguibench.evaluation.parity import compare_evaluator_observation_files


def _write_observations(path: Path, rows: list[dict[str, object]]) -> None:
    """写入不含任务正文或 gold 的合成 evaluator observations。

    输入参数：
        path：测试临时 JSONL 路径。
        rows：已经由测试构造的 observation object 列表。
    输出返回值：
        无；每行写入一个确定性 JSON object。
    """

    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_case_manifest(
    path: Path,
    cases: list[dict[str, str]],
) -> None:
    """写入固定 fixture 闭包和两侧 evaluator revision 的权威清单。

    输入参数：
        path：测试临时 manifest 路径。
        cases：protocol/case/input digest 三字段组成的期望闭集。
    输出返回值：
        无；写入 strict JSON object，不包含 fixture 正文或 gold。
    """

    path.write_text(
        json.dumps(
            {
                "schema_version": "evaluator-parity-case-manifest.v1",
                "manifest_id": "synthetic-parity-cases-v1",
                "fixture_source_revision": "tree-sha256:" + "f" * 64,
                "reference_evaluator_revision": "tree-sha256:" + "1" * 64,
                "candidate_evaluator_revision": "tree-sha256:" + "2" * 64,
                "cases": cases,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_parity_harness_reports_exact_matches_and_semantic_differences(
    tmp_path: Path,
) -> None:
    """验证 harness 按同一输入摘要比较 outcome 与 score。

    输入参数：
        tmp_path：pytest 提供的 reference/candidate JSONL 临时目录。
    输出返回值：
        无；相同 case 计为 match，候选 verdict/score 漂移形成字段级差异，
        observation 文件无需暴露 instruction、gold、轨迹或 evaluator details。
    """

    common = {
        "schema_version": "evaluator-observation.v2",
        "protocol_id": "paraguibench.answer.exact.v1",
        "input_sha256": "a" * 64,
    }
    case_manifest_path = tmp_path / "cases.json"
    reference_path = tmp_path / "reference.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    _write_case_manifest(
        case_manifest_path,
        [
            {
                "protocol_id": "paraguibench.answer.exact.v1",
                "case_id": case_id,
                "input_sha256": "a" * 64,
            }
            for case_id in ("case-pass", "case-fail")
        ],
    )
    _write_observations(
        reference_path,
        [
            {
                **common,
                "evaluator_revision": "tree-sha256:" + "1" * 64,
                "case_id": "case-pass",
                "outcome": "PASSED",
                "score": 1.0,
            },
            {
                **common,
                "evaluator_revision": "tree-sha256:" + "1" * 64,
                "case_id": "case-fail",
                "outcome": "FAILED",
                "score": 0.0,
            },
        ],
    )
    _write_observations(
        candidate_path,
        [
            {
                **common,
                "evaluator_revision": "tree-sha256:" + "2" * 64,
                "case_id": "case-pass",
                "outcome": "PASSED",
                "score": 1.0,
            },
            {
                **common,
                "evaluator_revision": "tree-sha256:" + "2" * 64,
                "case_id": "case-fail",
                "outcome": "PASSED",
                "score": 1.0,
            },
        ],
    )

    report = compare_evaluator_observation_files(
        case_manifest_path=case_manifest_path,
        reference_path=reference_path,
        candidate_path=candidate_path,
    )

    assert report.equivalent is False
    assert report.outputs_equivalent is False
    assert report.manifest_case_count == 2
    assert report.reference_count == 2
    assert report.candidate_count == 2
    assert report.matched_count == 1
    assert report.missing_from_reference == ()
    assert report.missing_from_candidate == ()
    assert [(item.case_id, item.field) for item in report.differences] == [
        ("case-fail", "outcome"),
        ("case-fail", "score"),
    ]


def test_parity_manifest_detects_case_missing_from_both_sides(
    tmp_path: Path,
) -> None:
    """验证 reference/candidate 共同漏 case 时仍不能伪装成闭集等价。

    输入参数：
        tmp_path：pytest 提供的三个隔离输入文件目录。
    输出返回值：
        无；权威 manifest 的第二个 case 同时出现在两侧 missing 列表中。
    """

    protocol_id = "paraguibench.answer.exact.v1"
    case_manifest_path = tmp_path / "cases.json"
    _write_case_manifest(
        case_manifest_path,
        [
            {
                "protocol_id": protocol_id,
                "case_id": case_id,
                "input_sha256": "a" * 64,
            }
            for case_id in ("present", "missing-both")
        ],
    )
    common = {
        "schema_version": "evaluator-observation.v2",
        "protocol_id": protocol_id,
        "case_id": "present",
        "input_sha256": "a" * 64,
        "outcome": "PASSED",
        "score": 1.0,
    }
    reference_path = tmp_path / "reference.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    _write_observations(
        reference_path,
        [{**common, "evaluator_revision": "tree-sha256:" + "1" * 64}],
    )
    _write_observations(
        candidate_path,
        [{**common, "evaluator_revision": "tree-sha256:" + "2" * 64}],
    )

    report = compare_evaluator_observation_files(
        case_manifest_path=case_manifest_path,
        reference_path=reference_path,
        candidate_path=candidate_path,
    )

    missing_key = f"{protocol_id}:missing-both"
    assert report.outputs_equivalent is False
    assert report.equivalent is False
    assert report.missing_from_reference == (missing_key,)
    assert report.missing_from_candidate == (missing_key,)


def test_identical_unavailable_outputs_do_not_pass_strict_parity_gate(
    tmp_path: Path,
) -> None:
    """验证双方都未运行 evaluator 只能算输出相同，不能通过迁移门禁。

    输入参数：
        tmp_path：pytest 提供的三个隔离输入文件目录。
    输出返回值：
        无；结构化相同性与可评分的 strict gate 分开表达。
    """

    protocol_id = "paraguibench.answer.exact.v1"
    case_manifest_path = tmp_path / "cases.json"
    _write_case_manifest(
        case_manifest_path,
        [
            {
                "protocol_id": protocol_id,
                "case_id": "unavailable",
                "input_sha256": "a" * 64,
            }
        ],
    )
    common = {
        "schema_version": "evaluator-observation.v2",
        "protocol_id": protocol_id,
        "case_id": "unavailable",
        "input_sha256": "a" * 64,
        "outcome": "UNAVAILABLE",
        "score": None,
    }
    reference_path = tmp_path / "reference.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    _write_observations(
        reference_path,
        [{**common, "evaluator_revision": "tree-sha256:" + "1" * 64}],
    )
    _write_observations(
        candidate_path,
        [{**common, "evaluator_revision": "tree-sha256:" + "2" * 64}],
    )

    report = compare_evaluator_observation_files(
        case_manifest_path=case_manifest_path,
        reference_path=reference_path,
        candidate_path=candidate_path,
    )

    assert report.outputs_equivalent is True
    assert report.equivalent is False
    assert report.unscored_reference_count == 1
    assert report.unscored_candidate_count == 1
