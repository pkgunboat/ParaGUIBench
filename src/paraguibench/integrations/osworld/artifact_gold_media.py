"""OSWorld artifact external-gold contract 的固定媒体闭集。

本模块是 evaluator-only gold 媒体类型的唯一受信来源。位置对应
``ArtifactMetricEvidenceSpec.gold_keys`` 的位置；对于双资产 sheet-print
contract，第一位固定为 XLSX，第二位固定为 CSV。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


_XLSX_MEDIA_TYPES = frozenset(
    {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
)
_DOCX_MEDIA_TYPES = frozenset(
    {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
)
_PPTX_MEDIA_TYPES = frozenset(
    {"application/vnd.openxmlformats-officedocument.presentationml.presentation"}
)


class OSWorldArtifactGoldMediaContractError(ValueError):
    """表示 external-gold contract 无法与固定媒体闭集绑定。"""

    def __init__(self) -> None:
        """构造不回显 contract 身份的固定公开错误。

        输入参数：
            无。
        输出返回值：
            无；异常文本固定为稳定领域代码。
        """

        super().__init__("OSWORLD_ARTIFACT_GOLD_MEDIA_CONTRACT_INVALID")


OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT: Mapping[
    str,
    tuple[frozenset[str], ...],
] = MappingProxyType(
    {
        "apa7-references.content-only.base-0_6.v1": (_DOCX_MEDIA_TYPES,),
        "bibtex.ignore-blanks.v1": (frozenset({"application/x-bibtex"}),),
        "docx-content.v1": (_DOCX_MEDIA_TYPES,),
        "grf-sheet-print.sheet1.v1": (
            _XLSX_MEDIA_TYPES,
            frozenset({"text/csv"}),
        ),
        "pdf-chapter-archive.v1": (frozenset({"application/zip"}),),
        "problem-invoice-content.v1": (frozenset({"application/pdf"}),),
        "sheet-data.first-sheet.v1": (_XLSX_MEDIA_TYPES,),
        "sheet-data.named-unseen-movies.v1": (_XLSX_MEDIA_TYPES,),
        "sheet-fuzzy.restaurant-contacts.v1": (_XLSX_MEDIA_TYPES,),
        "slide-index-1.frame-00-08.v1": (frozenset({"image/png"}),),
        "speaker-notes.no-shape-no-bullets.v1": (_PPTX_MEDIA_TYPES,),
        "supported-rate-sheet-print.sheet1.v1": (
            _XLSX_MEDIA_TYPES,
            frozenset({"text/csv"}),
        ),
    }
)


def artifact_gold_media_types(
    contract_id: str,
) -> tuple[frozenset[str], ...]:
    """按 contract 中 gold key 位置返回固定媒体 allowlist。

    输入参数：
        contract_id：版本化 artifact metric contract 身份。
    输出返回值：
        与 ``gold_keys`` 位置一一对应的不可变媒体集合 tuple。
    异常：
        OSWorldArtifactGoldMediaContractError：contract 类型、空值或
            注册身份无效。
    """

    if not isinstance(contract_id, str) or not contract_id:
        raise OSWorldArtifactGoldMediaContractError
    try:
        return OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT[contract_id]
    except KeyError:
        raise OSWorldArtifactGoldMediaContractError from None


__all__ = [
    "OSWORLD_ARTIFACT_GOLD_MEDIA_TYPES_BY_CONTRACT",
    "OSWorldArtifactGoldMediaContractError",
    "artifact_gold_media_types",
]
