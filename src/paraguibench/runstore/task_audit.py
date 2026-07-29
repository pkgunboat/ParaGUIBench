"""RunStore task.json 的 allowlist-first audit schema。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BINDING_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_REQUIRED_FIELDS = {
    "release_id",
    "canonical_task_sha256",
    "task_id",
    "materialization",
}
_OPTIONAL_FIELDS = {
    "task_uid",
    "task_type",
    "task_source",
    "task_tag",
}
_MATERIALIZATION_FIELDS = {
    "schema_version",
    "environment_binding_names",
    "fixture_refs",
}
_FIXTURE_REFERENCE_FIELDS = {
    "binding",
    "fixture_id",
    "schema_version",
    "data_classification",
    "task_storage_policy",
    "sha256",
}


def validate_task_audit_record(
    task_record: Mapping[str, Any],
    *,
    expected_task_id: str,
) -> dict[str, Any]:
    """验证并复制允许持久化的 task audit metadata。

    输入参数：
        task_record：PreparedTask 产生的 audit 投影。
        expected_task_id：``start_attempt`` 路径使用的稳定 task_id。
    输出返回值：
        经过严格字段与类型校验的深层副本。
    异常：
        ValueError/TypeError：未知字段、缺失字段、身份或嵌套结构无效；错误
            仅指出 schema 区域，不回显任何调用方值。
    """

    if not isinstance(task_record, Mapping):
        raise TypeError("task audit record 必须是 Mapping")
    fields = set(task_record)
    allowed_fields = _REQUIRED_FIELDS | _OPTIONAL_FIELDS
    if not _REQUIRED_FIELDS.issubset(fields) or not fields.issubset(
        allowed_fields
    ):
        raise ValueError("task audit fields 不符合 allowlist")
    if task_record.get("task_id") != expected_task_id:
        raise ValueError("task audit identity 与 Attempt 不一致")
    release_id = task_record.get("release_id")
    canonical_sha256 = task_record.get("canonical_task_sha256")
    if not isinstance(release_id, str) or not release_id:
        raise ValueError("task audit release_id 无效")
    if (
        not isinstance(canonical_sha256, str)
        or _SHA256_PATTERN.fullmatch(canonical_sha256) is None
    ):
        raise ValueError("task audit canonical digest 无效")
    for field in _OPTIONAL_FIELDS:
        if field in task_record and not isinstance(task_record[field], str):
            raise TypeError("task audit identity field 类型无效")

    _validate_materialization(task_record.get("materialization"))
    return deepcopy(dict(task_record))


def _validate_materialization(value: Any) -> None:
    """校验 materialization audit 只记录名称与 fixture 身份。

    输入参数：
        value：task audit 的 materialization object。
    输出返回值：
        无；严格 schema 合法时正常返回。
    异常：
        ValueError/TypeError：字段、绑定名称或 fixture reference 无效。
    """

    if not isinstance(value, Mapping):
        raise TypeError("task audit materialization 必须是 Mapping")
    if set(value) != _MATERIALIZATION_FIELDS:
        raise ValueError("task audit materialization fields 无效")
    if value.get("schema_version") != 1:
        raise ValueError("task audit materialization schema 无效")
    binding_names = value.get("environment_binding_names")
    if (
        not isinstance(binding_names, list)
        or binding_names != sorted(set(binding_names))
        or not all(
            isinstance(name, str)
            and _BINDING_PATTERN.fullmatch(name) is not None
            for name in binding_names
        )
    ):
        raise ValueError("task audit binding names 无效")
    fixture_refs = value.get("fixture_refs")
    if not isinstance(fixture_refs, list):
        raise TypeError("task audit fixture_refs 必须是 list")
    for fixture_ref in fixture_refs:
        _validate_fixture_reference(fixture_ref)


def _validate_fixture_reference(value: Any) -> None:
    """校验不含 payload 的 fixture audit identity。

    输入参数：
        value：单个 fixture reference object。
    输出返回值：
        无；字段、摘要、分类与存储策略符合当前公开 schema 时正常返回。
    异常：
        ValueError/TypeError：任一约束无效。
    """

    if not isinstance(value, Mapping):
        raise TypeError("task audit fixture reference 必须是 Mapping")
    if set(value) != _FIXTURE_REFERENCE_FIELDS:
        raise ValueError("task audit fixture reference fields 无效")
    for field in (
        "binding",
        "fixture_id",
        "data_classification",
        "task_storage_policy",
        "sha256",
    ):
        if not isinstance(value.get(field), str) or not value[field]:
            raise TypeError("task audit fixture identity field 无效")
    if value.get("schema_version") != 1:
        raise ValueError("task audit fixture schema 无效")
    if (
        value.get("data_classification")
        != "synthetic_public_test_data"
        or value.get("task_storage_policy") != "reference_only"
    ):
        raise ValueError("task audit fixture policy 无效")
    if _SHA256_PATTERN.fullmatch(value["sha256"]) is None:
        raise ValueError("task audit fixture digest 无效")
