"""以权威 case 闭包和脱敏 JSONL 比较旧/新 evaluator。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_REVISION_PATTERN = re.compile(r"(?:git:[0-9a-f]{40}|tree-sha256:[0-9a-f]{64})")
_PROTOCOL_PATTERN = re.compile(
    r"[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+\.v[1-9][0-9]*"
)
_CASE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_MANIFEST_FIELDS = {
    "schema_version",
    "manifest_id",
    "fixture_source_revision",
    "reference_evaluator_revision",
    "candidate_evaluator_revision",
    "cases",
}
_CASE_FIELDS = {"protocol_id", "case_id", "input_sha256"}
_OBSERVATION_FIELDS = {
    "schema_version",
    "evaluator_revision",
    "protocol_id",
    "case_id",
    "input_sha256",
    "outcome",
    "score",
}


class EvaluatorParityError(ValueError):
    """表示 case manifest 或 observation 不能安全参与 parity。"""


class EvaluatorOutcome(StrEnum):
    """表示 parity observation 的评价终态。"""

    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class EvaluatorObservation:
    """保存不含任务正文、gold 或 details 的 evaluator 观察值。

    输入参数：
        evaluator_revision：实际 producer evaluator 的固定源码摘要。
        protocol_id：被比较的版本化评价协议。
        case_id：同一 parity fixture 在两侧共享的稳定标识。
        input_sha256：规范化 evaluator 输入闭包摘要。
        outcome：评价终态。
        score：PASSED/FAILED 的有限 ``[0,1]`` 得分；其他终态为 ``None``。
    输出返回值：
        不可变 observation，可按 ``(protocol_id, case_id)`` 唯一比较。
    """

    evaluator_revision: str
    protocol_id: str
    case_id: str
    input_sha256: str
    outcome: EvaluatorOutcome
    score: float | None


@dataclass(frozen=True, slots=True)
class EvaluatorParityDifference:
    """描述一个共享 case 的单字段语义差异。"""

    protocol_id: str
    case_id: str
    field: str
    reference_value: str | float | None
    candidate_value: str | float | None


@dataclass(frozen=True, slots=True)
class EvaluatorParityReport:
    """汇总权威 case 闭包上的行为相同性与 strict gate 结果。

    输入参数：
        manifest_id/manifest_case_count：权威 fixture 清单身份与 case 数。
        fixture_source_revision：生成 normalized input 的固定事实源摘要。
        reference_evaluator_revision/candidate_evaluator_revision：两侧实际代码身份。
        reference_count/candidate_count：两侧唯一 observation 数。
        matched_count：输入、outcome 与 score 全部一致的权威共享 case 数。
        missing_* / unexpected_*：相对 manifest 的闭集缺失或越界 case。
        differences：权威共享 case 的 outcome/score 字段差异。
        unscored_*_count：ERROR/UNAVAILABLE observation 数。
    输出返回值：
        ``outputs_equivalent`` 只表达结构与输出相同；``equivalent`` 作为严格
        迁移门禁还要求每个 case 都产生 PASSED/FAILED 可评分结果。
    """

    manifest_id: str
    manifest_case_count: int
    fixture_source_revision: str
    reference_evaluator_revision: str
    candidate_evaluator_revision: str
    reference_count: int
    candidate_count: int
    matched_count: int
    missing_from_reference: tuple[str, ...]
    missing_from_candidate: tuple[str, ...]
    unexpected_in_reference: tuple[str, ...]
    unexpected_in_candidate: tuple[str, ...]
    differences: tuple[EvaluatorParityDifference, ...]
    unscored_reference_count: int
    unscored_candidate_count: int

    @property
    def outputs_equivalent(self) -> bool:
        """判断两侧是否对权威 case 闭包给出了逐字段相同输出。

        输入参数：
            无。
        输出返回值：
            无缺失、无越界且所有权威共享 case 无差异时返回 ``True``；
            ERROR/UNAVAILABLE 相同也只会在本属性中视为行为相同。
        """

        return not (
            self.missing_from_reference
            or self.missing_from_candidate
            or self.unexpected_in_reference
            or self.unexpected_in_candidate
            or self.differences
        )

    @property
    def equivalent(self) -> bool:
        """判断报告能否通过可评分 evaluator 迁移 strict gate。

        输入参数：
            无。
        输出返回值：
            闭集输出完全相同且双方没有 ERROR/UNAVAILABLE 时返回 ``True``。
        """

        return (
            self.outputs_equivalent
            and self.unscored_reference_count == 0
            and self.unscored_candidate_count == 0
        )


@dataclass(frozen=True, slots=True)
class _ParityCase:
    """保存权威 manifest 中单个脱敏 case 身份。"""

    protocol_id: str
    case_id: str
    input_sha256: str


@dataclass(frozen=True, slots=True)
class _ParityCaseManifest:
    """保存已严格加载的 evaluator parity case 闭包。"""

    manifest_id: str
    fixture_source_revision: str
    reference_evaluator_revision: str
    candidate_evaluator_revision: str
    cases: tuple[_ParityCase, ...]


def compare_evaluator_observation_files(
    *,
    case_manifest_path: Path,
    reference_path: Path,
    candidate_path: Path,
) -> EvaluatorParityReport:
    """相对权威 case manifest 严格比较两侧隔离进程 observation。

    输入参数：
        case_manifest_path：固定 fixture 闭包、输入摘要与两侧 revision 的 JSON。
        reference_path：旧事实源 evaluator observation JSONL。
        candidate_path：迁移后 evaluator observation JSONL。
    输出返回值：
        同时表达闭集差异、行为相同性和可评分 strict gate 的报告。
    异常：
        EvaluatorParityError：路径、JSON、schema、revision、输入摘要或唯一性
            无效；不会回显 JSON 内容、任务正文、gold 或 evaluator details。
    """

    manifest = _load_case_manifest(case_manifest_path)
    expected = {(item.protocol_id, item.case_id): item for item in manifest.cases}
    reference = _load_observation_file(
        reference_path,
        expected_revision=manifest.reference_evaluator_revision,
    )
    candidate = _load_observation_file(
        candidate_path,
        expected_revision=manifest.candidate_evaluator_revision,
    )
    expected_keys = set(expected)
    reference_keys = set(reference)
    candidate_keys = set(candidate)
    missing_from_reference = tuple(
        _format_key(key) for key in sorted(expected_keys - reference_keys)
    )
    missing_from_candidate = tuple(
        _format_key(key) for key in sorted(expected_keys - candidate_keys)
    )
    unexpected_in_reference = tuple(
        _format_key(key) for key in sorted(reference_keys - expected_keys)
    )
    unexpected_in_candidate = tuple(
        _format_key(key) for key in sorted(candidate_keys - expected_keys)
    )

    shared_expected = expected_keys & reference_keys & candidate_keys
    for key in sorted(shared_expected):
        expected_digest = expected[key].input_sha256
        if (
            reference[key].input_sha256 != expected_digest
            or candidate[key].input_sha256 != expected_digest
        ):
            raise EvaluatorParityError(
                "observation input digest 与 case manifest 不一致"
            )

    differences: list[EvaluatorParityDifference] = []
    matched_count = 0
    for key in sorted(shared_expected):
        item_differences = _compare_observation(
            reference[key],
            candidate[key],
        )
        if item_differences:
            differences.extend(item_differences)
        else:
            matched_count += 1
    unscored = {EvaluatorOutcome.ERROR, EvaluatorOutcome.UNAVAILABLE}
    return EvaluatorParityReport(
        manifest_id=manifest.manifest_id,
        manifest_case_count=len(expected),
        fixture_source_revision=manifest.fixture_source_revision,
        reference_evaluator_revision=manifest.reference_evaluator_revision,
        candidate_evaluator_revision=manifest.candidate_evaluator_revision,
        reference_count=len(reference),
        candidate_count=len(candidate),
        matched_count=matched_count,
        missing_from_reference=missing_from_reference,
        missing_from_candidate=missing_from_candidate,
        unexpected_in_reference=unexpected_in_reference,
        unexpected_in_candidate=unexpected_in_candidate,
        differences=tuple(differences),
        unscored_reference_count=sum(
            item.outcome in unscored for item in reference.values()
        ),
        unscored_candidate_count=sum(
            item.outcome in unscored for item in candidate.values()
        ),
    )


def _load_case_manifest(path: Path) -> _ParityCaseManifest:
    """安全加载固定 revision 与唯一 case 键的 parity manifest。

    输入参数：
        path：权威 case manifest 普通 JSON 文件。
    输出返回值：
        字段闭合、revision 固定且 case 非空唯一的内部 manifest。
    异常：
        EvaluatorParityError：路径、JSON、字段、revision、case 或唯一性无效。
    """

    raw = _read_strict_json_file(path, label="case manifest")
    if not isinstance(raw, dict) or set(raw) != _MANIFEST_FIELDS:
        raise EvaluatorParityError("case manifest fields 不符合 allowlist")
    if raw.get("schema_version") != "evaluator-parity-case-manifest.v1":
        raise EvaluatorParityError("case manifest schema_version 无效")
    manifest_id = raw.get("manifest_id")
    fixture_source_revision = raw.get("fixture_source_revision")
    reference_revision = raw.get("reference_evaluator_revision")
    candidate_revision = raw.get("candidate_evaluator_revision")
    if (
        not isinstance(manifest_id, str)
        or _CASE_ID_PATTERN.fullmatch(manifest_id) is None
        or not _valid_revision(fixture_source_revision)
        or not _valid_revision(reference_revision)
        or not _valid_revision(candidate_revision)
    ):
        raise EvaluatorParityError("case manifest identity 无效")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise EvaluatorParityError("case manifest cases 必须是非空列表")
    cases: list[_ParityCase] = []
    seen: set[tuple[str, str]] = set()
    for item in cases_raw:
        case = _parse_case(item)
        key = (case.protocol_id, case.case_id)
        if key in seen:
            raise EvaluatorParityError("case manifest key 重复")
        seen.add(key)
        cases.append(case)
    return _ParityCaseManifest(
        manifest_id=manifest_id,
        fixture_source_revision=fixture_source_revision,
        reference_evaluator_revision=reference_revision,
        candidate_evaluator_revision=candidate_revision,
        cases=tuple(cases),
    )


def _parse_case(raw: Any) -> _ParityCase:
    """把 manifest case object 转成严格脱敏身份。

    输入参数：
        raw：``cases`` 列表中的单个 JSON 值。
    输出返回值：
        protocol、case 与输入摘要均合法的内部 case。
    异常：
        EvaluatorParityError：字段闭集或身份格式无效。
    """

    if not isinstance(raw, dict) or set(raw) != _CASE_FIELDS:
        raise EvaluatorParityError("case manifest case fields 无效")
    protocol_id = raw.get("protocol_id")
    case_id = raw.get("case_id")
    input_sha256 = raw.get("input_sha256")
    _validate_case_identity(protocol_id, case_id, input_sha256)
    return _ParityCase(
        protocol_id=protocol_id,
        case_id=case_id,
        input_sha256=input_sha256,
    )


def _load_observation_file(
    path: Path,
    *,
    expected_revision: str,
) -> dict[tuple[str, str], EvaluatorObservation]:
    """安全加载单侧严格 schema、固定 producer revision 的 JSONL。

    输入参数：
        path：单侧 observation JSONL 文件。
        expected_revision：权威 manifest 为该侧固定的 evaluator revision。
    输出返回值：
        以 ``(protocol_id, case_id)`` 为键的 observation 字典。
    异常：
        EvaluatorParityError：路径、JSON、字段、revision、类型或唯一性无效。
    """

    if path.is_symlink() or not path.is_file():
        raise EvaluatorParityError("observation path 不是普通文件")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise EvaluatorParityError("observation file 无法读取") from error
    if not lines or any(not line.strip() for line in lines):
        raise EvaluatorParityError("observation JSONL 为空或含空行")
    observations: dict[tuple[str, str], EvaluatorObservation] = {}
    for line in lines:
        raw = _loads_strict_json(line, label="observation")
        observation = _parse_observation(raw)
        if observation.evaluator_revision != expected_revision:
            raise EvaluatorParityError("observation evaluator revision 不一致")
        key = (observation.protocol_id, observation.case_id)
        if key in observations:
            raise EvaluatorParityError("observation key 重复")
        observations[key] = observation
    return observations


def _parse_observation(raw: Any) -> EvaluatorObservation:
    """把单行 JSON object 转为严格 v2 observation。

    输入参数：
        raw：strict JSON decoder 的解码结果。
    输出返回值：
        revision、身份、终态和 score 组合均合法的 observation。
    异常：
        EvaluatorParityError：allowlist、格式或状态/score 组合无效。
    """

    if not isinstance(raw, dict) or set(raw) != _OBSERVATION_FIELDS:
        raise EvaluatorParityError("observation fields 不符合 allowlist")
    if raw.get("schema_version") != "evaluator-observation.v2":
        raise EvaluatorParityError("observation schema_version 无效")
    evaluator_revision = raw.get("evaluator_revision")
    protocol_id = raw.get("protocol_id")
    case_id = raw.get("case_id")
    input_sha256 = raw.get("input_sha256")
    if not _valid_revision(evaluator_revision):
        raise EvaluatorParityError("observation evaluator revision 无效")
    _validate_case_identity(protocol_id, case_id, input_sha256)
    try:
        outcome = EvaluatorOutcome(raw.get("outcome"))
    except (TypeError, ValueError) as error:
        raise EvaluatorParityError("observation outcome 无效") from error
    score = _parse_score(raw.get("score"), outcome)
    return EvaluatorObservation(
        evaluator_revision=evaluator_revision,
        protocol_id=protocol_id,
        case_id=case_id,
        input_sha256=input_sha256,
        outcome=outcome,
        score=score,
    )


def _validate_case_identity(
    protocol_id: Any,
    case_id: Any,
    input_sha256: Any,
) -> None:
    """验证 manifest 与 observation 共用的三字段 case 身份。

    输入参数：
        protocol_id：必须以正整数 ``.vN`` 结尾的协议 ID。
        case_id：不含路径分隔符的稳定短标识。
        input_sha256：normalized evaluator input closure 摘要。
    输出返回值：
        无；三字段合法时正常返回。
    异常：
        EvaluatorParityError：任一字段类型或格式无效。
    """

    if (
        not isinstance(protocol_id, str)
        or _PROTOCOL_PATTERN.fullmatch(protocol_id) is None
        or not isinstance(case_id, str)
        or _CASE_ID_PATTERN.fullmatch(case_id) is None
        or not isinstance(input_sha256, str)
        or _SHA256_PATTERN.fullmatch(input_sha256) is None
    ):
        raise EvaluatorParityError("parity case identity 无效")


def _parse_score(value: Any, outcome: EvaluatorOutcome) -> float | None:
    """验证 observation score 与评价终态组合。

    输入参数：
        value：JSON score 值。
        outcome：已解析评价终态。
    输出返回值：
        PASSED/FAILED 的有限浮点得分，或 ERROR/UNAVAILABLE 的 ``None``。
    异常：
        EvaluatorParityError：类型、范围或状态组合无效。
    """

    scoring = {EvaluatorOutcome.PASSED, EvaluatorOutcome.FAILED}
    if outcome not in scoring:
        if value is not None:
            raise EvaluatorParityError("非评分 outcome 不得携带 score")
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise EvaluatorParityError("评分 outcome 的 score 无效")
    return float(value)


def _compare_observation(
    reference: EvaluatorObservation,
    candidate: EvaluatorObservation,
) -> list[EvaluatorParityDifference]:
    """比较同一权威 protocol/case 的 outcome 与 score。

    输入参数：
        reference：旧事实源 observation。
        candidate：迁移后 observation。
    输出返回值：
        按 outcome、score 固定顺序排列的字段差异列表；输入摘要已先相对
        manifest 校验，因此不会把双方共同使用错误输入算成等价。
    """

    differences: list[EvaluatorParityDifference] = []
    fields = (
        ("outcome", reference.outcome.value, candidate.outcome.value),
        ("score", reference.score, candidate.score),
    )
    for field, reference_value, candidate_value in fields:
        if reference_value != candidate_value:
            differences.append(
                EvaluatorParityDifference(
                    protocol_id=reference.protocol_id,
                    case_id=reference.case_id,
                    field=field,
                    reference_value=reference_value,
                    candidate_value=candidate_value,
                )
            )
    return differences


def _read_strict_json_file(path: Path, *, label: str) -> Any:
    """读取不跟随 symlink 且拒绝重复 object key 的 JSON 文件。

    输入参数：
        path：待读取普通文件路径。
        label：不含调用方值的错误区域名称。
    输出返回值：
        strict decoder 产生的 JSON 基本类型。
    异常：
        EvaluatorParityError：路径、编码、I/O、JSON 或重复 key 无效。
    """

    if path.is_symlink() or not path.is_file():
        raise EvaluatorParityError(f"{label} path 不是普通文件")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise EvaluatorParityError(f"{label} 无法读取") from error
    return _loads_strict_json(text, label=label)


def _loads_strict_json(text: str, *, label: str) -> Any:
    """解码 JSON 并在任意嵌套 object 出现重复 key 时失败。

    输入参数：
        text：单个 JSON document 文本。
        label：不含外部内容的错误区域名称。
    输出返回值：
        没有重复 key 的 JSON 解码值。
    异常：
        EvaluatorParityError：JSON 语法或任意 object key 唯一性无效。
    """

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        """把 object pairs 转成字典并拒绝重复字段名。

        输入参数：
            pairs：JSON decoder 按原顺序提供的 key/value 对。
        输出返回值：
            key 唯一的普通字典。
        异常：
            EvaluatorParityError：同一 object 内出现重复 key。
        """

        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise EvaluatorParityError(f"{label} object key 重复")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique_object)
    except EvaluatorParityError:
        raise
    except json.JSONDecodeError as error:
        raise EvaluatorParityError(f"{label} JSON 无效") from error


def _valid_revision(value: Any) -> bool:
    """判断值是否是允许的完整 Git 或 source-tree 摘要。

    输入参数：
        value：待验证 JSON 字段。
    输出返回值：
        字符串完全匹配固定 revision 格式时返回 ``True``。
    """

    return isinstance(value, str) and _REVISION_PATTERN.fullmatch(value) is not None


def _format_key(key: tuple[str, str]) -> str:
    """把 observation 键格式化为无歧义、无正文的诊断标识。

    输入参数：
        key：``(protocol_id, case_id)``。
    输出返回值：
        ``protocol_id:case_id`` 字符串。
    """

    return f"{key[0]}:{key[1]}"
