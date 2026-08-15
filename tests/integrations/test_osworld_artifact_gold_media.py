"""OSWorld artifact external-gold 媒体契约的单一来源测试。"""

from __future__ import annotations

import pytest

from paraguibench.integrations.osworld.artifact_gold_media import (
    OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT,
    OSWorldArtifactGoldMediaContractError,
    artifact_gold_media_types,
)
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)


_EXPECTED_MEDIA_TYPES_BY_CONTRACT = {
    "apa7-references.content-only.base-0_6.v1": (
        frozenset(
            {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        ),
    ),
    "bibtex.ignore-blanks.v1": (frozenset({"application/x-bibtex"}),),
    "docx-content.v1": (
        frozenset(
            {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        ),
    ),
    "grf-sheet-print.sheet1.v1": (
        frozenset(
            {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        ),
        frozenset({"text/csv"}),
    ),
    "pdf-chapter-archive.v1": (frozenset({"application/zip"}),),
    "problem-invoice-content.v1": (frozenset({"application/pdf"}),),
    "sheet-data.first-sheet.v1": (
        frozenset(
            {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        ),
    ),
    "sheet-data.named-unseen-movies.v1": (
        frozenset(
            {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        ),
    ),
    "sheet-fuzzy.restaurant-contacts.v1": (
        frozenset(
            {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        ),
    ),
    "slide-index-1.frame-00-08.v1": (frozenset({"image/png"}),),
    "speaker-notes.no-shape-no-bullets.v1": (
        frozenset(
            {
                "application/vnd.openxmlformats-officedocument."
                "presentationml.presentation"
            }
        ),
    ),
    "supported-rate-sheet-print.sheet1.v1": (
        frozenset(
            {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        ),
        frozenset({"text/csv"}),
    ),
}


def test_gold_media_contract_catalog_is_the_exact_immutable_closed_set() -> None:
    """验证 12 个 external-gold contract 的位置化媒体闭集。

    输入参数：
        无；使用模块公开的只读 catalog 和查询函数。
    输出返回值：
        无；contract 集合、顺序媒体槽及深层不可变性均精确匹配。
    """

    assert dict(OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT) == (
        _EXPECTED_MEDIA_TYPES_BY_CONTRACT
    )
    assert len(OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT) == 12
    with pytest.raises(TypeError):
        OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT["unregistered.contract.v1"] = ()  # type: ignore[index]
    for contract_id, expected in _EXPECTED_MEDIA_TYPES_BY_CONTRACT.items():
        resolved = artifact_gold_media_types(contract_id)

        assert resolved == expected
        assert isinstance(resolved, tuple)
        assert all(isinstance(media_types, frozenset) for media_types in resolved)


@pytest.mark.parametrize("contract_id", ["", "unknown.private.contract.v1"])
def test_unknown_gold_media_contract_fails_closed_without_echo(
    contract_id: str,
) -> None:
    """验证空值或未注册 contract 不会被推断媒体类型。

    输入参数：
        contract_id：pytest 提供的空值或敏感未注册标识。
    输出返回值：
        无；查询只抛固定错误，不回显输入 contract。
    """

    with pytest.raises(OSWorldArtifactGoldMediaContractError) as caught:
        artifact_gold_media_types(contract_id)

    assert str(caught.value) == "OSWORLD_ARTIFACT_GOLD_MEDIA_CONTRACT_INVALID"
    if contract_id:
        assert contract_id not in str(caught.value)


def test_all_fifteen_specs_resolve_every_external_gold_key_position() -> None:
    """验证 15 个 canonical spec 的所有 external key 都有位置化媒体。

    输入参数：
        无；遍历版本化 artifact evidence spec catalog。
    输出返回值：
        无；15 个 spec 的 16 个 external key 均按 contract 位置
        解析，且不存在无 spec 消费者的多余 contract。
    """

    assert len(OSWORLD_ARTIFACT_EVIDENCE_SPECS) == 15
    resolved_contract_ids: set[str] = set()
    resolved_gold_keys: dict[str, frozenset[str]] = {}
    for spec in OSWORLD_ARTIFACT_EVIDENCE_SPECS.values():
        for slot in spec.artifact_slots:
            for metric in slot.metrics:
                if metric.expected_kind != "gold-assets":
                    continue
                media_types_by_position = artifact_gold_media_types(metric.contract_id)
                assert len(media_types_by_position) == len(metric.gold_keys)
                resolved_contract_ids.add(metric.contract_id)
                for logical_key, media_types in zip(
                    metric.gold_keys,
                    media_types_by_position,
                    strict=True,
                ):
                    assert logical_key not in resolved_gold_keys
                    resolved_gold_keys[logical_key] = media_types

    assert resolved_contract_ids == set(OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT)
    assert len(resolved_gold_keys) == 16
