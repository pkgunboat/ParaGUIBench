"""把 release task 解析为 trusted、agent 与 audit 三种投影。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from .agent_view import build_agent_task_view
from .materialization import materialize_task
from .release import (
    ReleaseTaskError,
    load_release_fixture,
    load_release_task_record,
)

_CHECKOUT_FIXTURE_ID = "webmall.checkout-profile.synthetic-public.v1"
_CHECKOUT_BINDING = "checkout_profile"
_CHECKOUT_TOKEN = "{{checkout_profile}}"
_FIXTURE_TOP_LEVEL_FIELDS = {
    "$schema",
    "schema_version",
    "fixture_id",
    "fixture_type",
    "data_classification",
    "task_storage_policy",
    "intended_use",
    "profile",
}
_SHIPPING_FIELDS = {
    "name",
    "email",
    "street",
    "house_number",
    "zip",
    "city",
    "state",
    "country",
}
_PAYMENT_FIELDS = {
    "type",
    "card_number",
    "cvv",
    "expiry_date",
}


class TaskPreparationError(RuntimeError):
    """表示 release task、fixture 或投影不满足安全物化契约。"""


@dataclass(frozen=True, slots=True)
class PreparedTask:
    """保存同一 Benchmark Task 的三个明确可见性投影。

    输入参数：
        trusted_task：environment/evaluator 可见的完整内存副本。
        agent_task：只含 Agent allowlist 字段及已渲染 instruction 的副本。
        audit_metadata：允许 RunStore 持久化且不含 instruction/gold/fixture 值的
            公开身份元数据。
    输出返回值：
        runtime 通过具名投影避免错误地序列化或下发完整 canonical task。
    """

    trusted_task: dict[str, Any]
    agent_task: dict[str, Any]
    audit_metadata: dict[str, Any]


def prepare_release_task(
    repo_root: Path,
    task_id: str,
    environment_bindings: Mapping[str, str],
) -> PreparedTask:
    """加载、物化并分离一个 release task 的三类可见性。

    输入参数：
        repo_root：ParaGUIBench 源码 checkout 根目录。
        task_id：release-v1 中唯一存在的 canonical task 标识。
        environment_bindings：部署提供的非敏感路径或服务逻辑绑定。
    输出返回值：
        一致的 ``PreparedTask``；原始 task 与 fixture 文件保持不变。
    异常：
        TaskPreparationError：任务、fixture、模板或投影契约不合规；异常不回显
            fixture 值、instruction 或 gold。
    """

    if not isinstance(environment_bindings, Mapping):
        raise TypeError("environment_bindings 必须是 Mapping")
    stage = "release_task"
    try:
        release_record = load_release_task_record(repo_root, task_id)
        stage = "environment_materialization"
        trusted_task = materialize_task(
            release_record.task,
            environment_bindings,
        )
        fixture_audit_records: list[dict[str, Any]] = []
        if "fixture_ref" in trusted_task or "instruction_template" in trusted_task:
            stage = "fixture_resolution"
            fixture_audit_records = _resolve_checkout_fixture(
                repo_root=repo_root,
                trusted_task=trusted_task,
            )
        stage = "agent_projection"
        agent_task = build_agent_task_view(trusted_task)
    except (ReleaseTaskError, ValueError, TypeError) as error:
        raise TaskPreparationError(
            f"task preparation failed at {stage}: {type(error).__name__}"
        ) from None

    audit_metadata = _build_audit_metadata(
        release_id=release_record.release_id,
        canonical_sha256=release_record.canonical_sha256,
        trusted_task=trusted_task,
        environment_bindings=environment_bindings,
        fixture_audit_records=fixture_audit_records,
    )
    return PreparedTask(
        trusted_task=deepcopy(trusted_task),
        agent_task=deepcopy(agent_task),
        audit_metadata=audit_metadata,
    )


def _resolve_checkout_fixture(
    *,
    repo_root: Path,
    trusted_task: dict[str, Any],
) -> list[dict[str, Any]]:
    """解析 checkout fixture、渲染 instruction 并生成 audit identity。

    输入参数：
        repo_root：用于按 release manifest 解析 fixture 的仓库根目录。
        trusted_task：已完成非敏感环境绑定的 task 副本。
    输出返回值：
        仅含 fixture 身份、schema、分类、策略与摘要的单元素列表。
    异常：
        ValueError/ReleaseTaskError：引用、模板、fixture schema 或值约束无效。
    """

    fixture_ref = trusted_task.get("fixture_ref")
    template = trusted_task.get("instruction_template")
    if (
        not isinstance(fixture_ref, Mapping)
        or set(fixture_ref) != {"binding", "fixture_id"}
        or fixture_ref.get("binding") != _CHECKOUT_BINDING
        or fixture_ref.get("fixture_id") != _CHECKOUT_FIXTURE_ID
    ):
        raise ValueError("checkout fixture reference 无效")
    if "instruction" in trusted_task:
        raise ValueError("checkout task 不能同时保存 materialized instruction")
    if (
        not isinstance(template, str)
        or template.count(_CHECKOUT_TOKEN) != 1
    ):
        raise ValueError("checkout instruction template token 无效")

    fixture_record = load_release_fixture(
        repo_root,
        _CHECKOUT_FIXTURE_ID,
    )
    _validate_checkout_fixture(fixture_record.fixture)
    rendered_profile = _render_checkout_profile(fixture_record.fixture)
    trusted_task["instruction"] = template.replace(
        _CHECKOUT_TOKEN,
        rendered_profile,
    )
    trusted_task.pop("instruction_template", None)
    trusted_task["resolved_fixtures"] = {
        _CHECKOUT_BINDING: deepcopy(fixture_record.fixture)
    }
    return [
        {
            "binding": _CHECKOUT_BINDING,
            "fixture_id": fixture_record.fixture_id,
            "schema_version": fixture_record.fixture["schema_version"],
            "data_classification": fixture_record.fixture[
                "data_classification"
            ],
            "task_storage_policy": fixture_record.fixture[
                "task_storage_policy"
            ],
            "sha256": fixture_record.sha256,
        }
    ]


def _validate_checkout_fixture(fixture: Mapping[str, Any]) -> None:
    """独立于离线 validator 校验 checkout fixture v1 严格 schema。

    输入参数：
        fixture：已通过 manifest 摘要和内部身份校验的 JSON object。
    输出返回值：
        无；字段、常量和测试资料格式全部符合 v1 时正常返回。
    异常：
        ValueError：任一结构或值约束无效；错误不包含具体字段值。
    """

    if set(fixture) != _FIXTURE_TOP_LEVEL_FIELDS:
        raise ValueError("fixture top-level fields 无效")
    expected_constants = {
        "$schema": "../../schemas/webmall-checkout-fixture-v1.schema.json",
        "schema_version": 1,
        "fixture_id": _CHECKOUT_FIXTURE_ID,
        "fixture_type": "checkout_profile",
        "data_classification": "synthetic_public_test_data",
        "task_storage_policy": "reference_only",
        "intended_use": "benchmark_testing_only",
    }
    if any(fixture.get(key) != value for key, value in expected_constants.items()):
        raise ValueError("fixture identity constants 无效")
    profile = fixture.get("profile")
    if not isinstance(profile, Mapping) or set(profile) != {
        "shipping_address",
        "payment_method",
    }:
        raise ValueError("fixture profile fields 无效")
    shipping = profile.get("shipping_address")
    payment = profile.get("payment_method")
    if not isinstance(shipping, Mapping) or set(shipping) != _SHIPPING_FIELDS:
        raise ValueError("fixture shipping fields 无效")
    if not isinstance(payment, Mapping) or set(payment) != _PAYMENT_FIELDS:
        raise ValueError("fixture payment fields 无效")
    if not all(
        isinstance(shipping[field], str) and shipping[field]
        for field in _SHIPPING_FIELDS
    ):
        raise ValueError("fixture shipping values 无效")
    if (
        not shipping["email"].endswith("@example.invalid")
        or shipping["email"].count("@") != 1
    ):
        raise ValueError("fixture email 必须使用 example.invalid")
    if (
        payment.get("type") != "credit_card"
        or payment.get("card_number") != "4242424242424242"
        or not isinstance(payment.get("cvv"), str)
        or re.fullmatch(r"[0-9]{3,4}", payment["cvv"]) is None
        or not isinstance(payment.get("expiry_date"), str)
        or re.fullmatch(
            r"(0[1-9]|1[0-2])/[0-9]{2}",
            payment["expiry_date"],
        )
        is None
    ):
        raise ValueError("fixture payment values 无效")


def _render_checkout_profile(fixture: Mapping[str, Any]) -> str:
    """按 ADR-0003 固定顺序渲染 Agent 表单资料。

    输入参数：
        fixture：已通过严格 v1 schema 校验的 checkout fixture。
    输出返回值：
        单行、确定性、字段带标签的公开测试资料字符串。
    """

    profile = fixture["profile"]
    shipping = profile["shipping_address"]
    payment = profile["payment_method"]
    ordered_fields = (
        ("name", shipping["name"]),
        ("email", shipping["email"]),
        ("street", shipping["street"]),
        ("house number", shipping["house_number"]),
        ("ZIP", shipping["zip"]),
        ("city", shipping["city"]),
        ("state", shipping["state"]),
        ("country", shipping["country"]),
        ("card number", payment["card_number"]),
        ("CVV", payment["cvv"]),
        ("expiry date", payment["expiry_date"]),
    )
    return "; ".join(f"{label}: {value}" for label, value in ordered_fields)


def _build_audit_metadata(
    *,
    release_id: str,
    canonical_sha256: str,
    trusted_task: Mapping[str, Any],
    environment_bindings: Mapping[str, str],
    fixture_audit_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造 RunStore 可持久化的严格 task audit allowlist。

    输入参数：
        release_id：canonical release 标识。
        canonical_sha256：task 文件固定摘要。
        trusted_task：已物化 task，仅从中读取公开身份字段。
        environment_bindings：部署使用的非敏感绑定；只记录名称。
        fixture_audit_records：不含 fixture payload 的身份摘要。
    输出返回值：
        不含 instruction、gold、fixture 值或环境绑定值的 JSON-compatible 字典。
    """

    audit: dict[str, Any] = {
        "release_id": release_id,
        "canonical_task_sha256": canonical_sha256,
    }
    for field in (
        "task_id",
        "task_uid",
        "task_type",
        "task_source",
        "task_tag",
    ):
        value = trusted_task.get(field)
        if isinstance(value, str):
            audit[field] = value
    audit["materialization"] = {
        "schema_version": 1,
        "environment_binding_names": sorted(environment_bindings),
        "fixture_refs": deepcopy(fixture_audit_records),
    }
    return audit
