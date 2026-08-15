"""CombinationDocs-002 generic artifact 到 typed 事实的集成测试。"""

from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import os
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

import pytest

from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    CrossDocumentObservation,
    evaluate_cross_document,
)
from paraguibench.integrations.pipeline_implicit.artifact_evidence import (
    PipelineImplicitArtifactEvidenceError,
    PipelineImplicitArtifactEvidenceSource,
    PipelineImplicitArtifactFile,
    PipelineImplicitArtifactObservation,
)
from paraguibench.integrations.pipeline_implicit.cross_document_bridge import (
    build_cross_document_observation,
)
from paraguibench.integrations.pipeline_implicit import cross_document_bridge


TASK_UID = "6bf5b1c9-a2a2-4901-bbe3-631a33da45e8"
_FIXTURE_ENVIRONMENT_VARIABLE = "PARAGUI_COMBINATION002_FIXTURE_ROOT"


def _fixed_revision_fixture(role: str) -> Path:
    """返回显式配置的 CombinationDocs-002 固定字节目录。

    输入参数：
        role：``input`` 或 ``known_negative``。
    输出返回值：
        固定 Lee revision 的对应三文件目录；未配置则跳过。
    """

    raw_root = os.environ.get(_FIXTURE_ENVIRONMENT_VARIABLE)
    if raw_root is None:
        pytest.skip(
            f"{_FIXTURE_ENVIRONMENT_VARIABLE} is required for download-only fixture"
        )
    if role not in {"input", "known_negative"}:
        raise AssertionError("fixture role is not registered")
    role_directory = "benchmark_dataset" if role == "input" else "answer_files"
    candidate = Path(raw_root) / role_directory / TASK_UID
    if not candidate.is_dir():
        pytest.fail("CombinationDocs-002 fixed-revision fixture is unavailable")
    return candidate


def _artifact_observation(role: str) -> PipelineImplicitArtifactObservation:
    """把已核验 fixture 投影为 bridge 的 public generic 输入。

    输入参数：
        role：固定 input 或 audit-only known-negative 资产角色。
    输出返回值：
        按 UTF-8 路径序冻结的三文件 generic artifact observation。
    """

    files: list[PipelineImplicitArtifactFile] = []
    for path in sorted(
        _fixed_revision_fixture(role).iterdir(),
        key=lambda item: item.name.encode("utf-8"),
    ):
        payload = path.read_bytes()
        files.append(
            PipelineImplicitArtifactFile(
                relative_path=path.name,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                _payload=payload,
            )
        )
    return PipelineImplicitArtifactObservation(
        task_id=CROSS_DOCUMENT_TASK_ID,
        protocol_id=CROSS_DOCUMENT_PROTOCOL_ID,
        complete=True,
        _files=tuple(files),
    )


def _replace_artifact_payload(
    observation: PipelineImplicitArtifactObservation,
    relative_path: str,
    payload: bytes,
) -> PipelineImplicitArtifactObservation:
    """替换 generic observation 中一个文件的完整性绑定。

    输入参数：
        observation：原始三文档闭集。
        relative_path/payload：待替换成员及新字节。
    输出返回值：
        其余成员不变、目标 size/SHA/payload 同步更新的 observation。
    """

    replacement = PipelineImplicitArtifactFile(
        relative_path=relative_path,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        _payload=payload,
    )
    files = tuple(
        replacement if item.relative_path == relative_path else item
        for item in observation.iter_files_for_evaluator()
    )
    assert sum(item.relative_path == relative_path for item in files) == 1
    return PipelineImplicitArtifactObservation(
        task_id=observation.task_id,
        protocol_id=observation.protocol_id,
        complete=True,
        _files=files,
    )


def _rewrite_zip_member(payload: bytes, member: str, content: bytes) -> bytes:
    """在测试内重写一个 OOXML ZIP member 并更新 CRC。

    输入参数：
        payload/member/content：原容器、精确成员名与替换字节。
    输出返回值：
        保留其余 central-directory 元数据的完整新 ZIP bytes。
    """

    destination = BytesIO()
    with zipfile.ZipFile(BytesIO(payload)) as source:
        assert member in source.namelist()
        with zipfile.ZipFile(destination, "w") as target:
            for info in source.infolist():
                target.writestr(
                    info,
                    content if info.filename == member else source.read(info),
                )
    return destination.getvalue()


class _FixtureController:
    """通过 production getter 形状返回一个固定三文件闭集。"""

    def __init__(self, root: Path) -> None:
        """冻结 fixture 文件与 manifest。

        输入参数：
            root：固定 revision 的三文件目录。
        输出返回值：无；构造 manifest 与 guest 路径映射。
        """

        self.files = {
            f"/home/oai/share/{path.name}": path.read_bytes()
            for path in root.iterdir()
            if path.is_file()
        }
        self.manifest = tuple(
            (
                path.removeprefix("/home/oai/share/"),
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
            for path, payload in sorted(
                self.files.items(),
                key=lambda item: item[0].encode("utf-8"),
            )
        )

    def collect_artifact_tree_manifest(
        self,
        guest_directory: str,
        **limits: Any,
    ) -> tuple[tuple[str, int, str], ...]:
        """返回固定闭集并确认 production 传入资源门。

        输入参数：
            guest_directory：冻结的 guest shared 目录。
            limits：capture 的正数资源限制。
        输出返回值：预先冻结的三文件 manifest。
        """

        assert guest_directory == "/home/oai/share"
        assert limits and all(float(value) > 0 for value in limits.values())
        return self.manifest

    def collect_file_bytes(self, guest_path: str, **limits: Any) -> bytes:
        """返回 manifest 已绑定的单文件字节。

        输入参数：
            guest_path：production 拼接后的绝对 guest 文件路径。
            limits：单文件读取资源限制。
        输出返回值：对应固定 payload。
        """

        assert limits and all(float(value) > 0 for value in limits.values())
        return self.files[guest_path]


def test_real_hf_known_negative_is_audit_only_and_fails_xlsx_fact_order() -> None:
    """验证历史 HF answer 仅作 audit known-negative 且稳定失败。

    输入参数：无；使用固定 revision 的 audit-only answer bytes。
    输出返回值：无；January profit/customers 匹配，DOCX 中
        December-before-July 与 XLSX 派生顺序冲突，固定得分为 2/3。
    """

    observation = build_cross_document_observation(
        _artifact_observation("known_negative")
    )
    result = evaluate_cross_document(observation)

    assert observation.reference_spreadsheet_unchanged is True
    assert observation.narrative is not None
    assert observation.narrative.january_profit == 47_109
    assert observation.narrative.strongest_profit_order == (
        "december",
        "july",
        "january",
    )
    assert observation.narrative.other_facts_match_reference is True
    assert observation.presentation is not None
    assert observation.presentation.january_customers == 1_895
    assert observation.presentation.other_facts_match_reference is True
    assert observation.unexpected_document_count == 0
    assert result.passed is False
    assert result.score == 0.6667
    assert result.reason_codes == ("DOCX_PROFIT_ORDER_INCORRECT",)


def test_real_hf_input_fails_all_three_known_target_errors() -> None:
    """验证原始 input 的两个错值和错误排序被真实 parser 全部捕获。

    输入参数：无；解析固定 Lee revision 的 input DOCX/XLSX/PPTX。
    输出返回值：无；其他叙述保持完整，但三个目标事实均不匹配。
    """

    observation = build_cross_document_observation(_artifact_observation("input"))
    result = evaluate_cross_document(observation)

    assert observation.reference_spreadsheet_unchanged is True
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is True
    assert observation.presentation is not None
    assert observation.presentation.other_facts_match_reference is True
    assert result.passed is False
    assert result.score == 0.0
    assert result.reason_codes == (
        "DOCX_JANUARY_PROFIT_INCORRECT",
        "DOCX_PROFIT_ORDER_INCORRECT",
        "PPTX_JANUARY_CUSTOMERS_INCORRECT",
    )


@pytest.mark.parametrize("mutation", ("style", "hidden_row", "workbook_property"))
def test_semantically_equal_xlsx_metadata_change_is_not_pinned(
    mutation: str,
) -> None:
    """验证单元格事实未变时仍须通过整份 XLSX 字节身份。

    输入参数：
        mutation：分别只改 style 字节、row hidden 或 workbook property。
    输出返回值：无；虽然月度事实仍可解析，pinned 完整性必须为
        false，且纯评价器报告 reference spreadsheet 变更。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    member = {
        "style": "xl/styles.xml",
        "hidden_row": "xl/worksheets/sheet1.xml",
        "workbook_property": "xl/workbook.xml",
    }[mutation]
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        content = archive.read(member)
    if mutation == "style":
        mutated_content = content + b"\n"
    else:
        root = ET.fromstring(content)
        if mutation == "hidden_row":
            target = next(
                item
                for item in root.iter()
                if item.tag.endswith("}row") and item.attrib.get("r") == "4"
            )
            target.set("hidden", "1")
        else:
            target = next(item for item in root if item.tag.endswith("}workbookPr"))
            target.set("date1904", "1")
        mutated_content = ET.tostring(
            root,
            encoding="utf-8",
            xml_declaration=True,
        )
    mutated_xlsx = _rewrite_zip_member(xlsx_payload, member, mutated_content)

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            mutated_xlsx,
        )
    )
    result = evaluate_cross_document(observation)

    assert observation.reference_spreadsheet_unchanged is False
    assert result.reference_spreadsheet_changed is True
    assert "REFERENCE_SPREADSHEET_CHANGED" in result.reason_codes


def test_content_types_root_namespace_spoof_rejects_xlsx_document() -> None:
    """验证 package content-types 根元素必须使用精确命名空间。

    输入参数：无；仅把 audit known-negative XLSX 的 ``Types`` 根标签改到
        伪造 namespace，子 ``Override`` 保持不变。
    输出返回值：无；该 XLSX 不得被 local-name 匹配误接受。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        content_types = ET.fromstring(archive.read("[Content_Types].xml"))
    content_types.tag = "{urn:paraguibench:spoof}Types"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        "[Content_Types].xml",
        ET.tostring(content_types, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.presentation is not None
    assert observation.narrative.other_facts_match_reference is False
    assert observation.presentation.other_facts_match_reference is False


def test_relationships_root_namespace_spoof_rejects_xlsx_document() -> None:
    """验证 package relationships 根元素必须使用精确 QName。

    输入参数：无；仅伪造 workbook relationships 的根 namespace，
        直接子 ``Relationship`` 保持真实 package namespace。
    输出返回值：无；不得通过全树 local-name 继续解析工作表。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    relationship_member = "xl/_rels/workbook.xml.rels"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        relationships = ET.fromstring(archive.read(relationship_member))
    relationships.tag = "{urn:paraguibench:spoof}Relationships"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        relationship_member,
        ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_relationship_element_namespace_spoof_rejects_xlsx_document() -> None:
    """验证 relationship 必须是 package 根下的精确直接 QName。

    输入参数：无；保留真实 ``Relationships`` 根，但把所有
        直接 relationship 子元素改到伪造 namespace。
    输出返回值：无；工作表 target 不得从伪造元素中解析。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    relationship_member = "xl/_rels/workbook.xml.rels"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        relationships = ET.fromstring(archive.read(relationship_member))
    for item in relationships:
        item.tag = "{urn:paraguibench:spoof}Relationship"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        relationship_member,
        ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_xlsx_workbook_root_namespace_spoof_rejects_document() -> None:
    """验证 workbook 根元素不能仅按 local-name 识别。

    输入参数：无；只伪造 audit known-negative workbook 根 namespace，
        ``sheets/sheet`` 子树保持真实 SpreadsheetML QName。
    输出返回值：无；整份工作簿应按已知文档错误失败关闭。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    workbook.tag = "{urn:paraguibench:spoof}workbook"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        "xl/workbook.xml",
        ET.tostring(workbook, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_xlsx_sheets_parent_namespace_spoof_rejects_nested_sheet() -> None:
    """验证 ``sheet`` 只能来自 workbook 的精确 ``sheets`` 直接路径。

    输入参数：无；仅把真实 ``sheets`` 父元素改到伪造
        namespace，内层 ``sheet`` 仍使用 SpreadsheetML QName。
    输出返回值：无；全树中的游离 ``sheet`` 不得被采纳。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = next(item for item in workbook if item.tag.endswith("}sheets"))
    sheets.tag = "{urn:paraguibench:spoof}sheets"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        "xl/workbook.xml",
        ET.tostring(workbook, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_xlsx_worksheet_root_namespace_spoof_rejects_cells() -> None:
    """验证 worksheet 根必须是精确 SpreadsheetML QName。

    输入参数：无；只改变真实 worksheet 根 namespace，内部
        ``sheetData/row/c`` 及数值保持不变。
    输出返回值：无；不得从伪造 worksheet 全树中拾取 cell。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    worksheet_member = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        worksheet = ET.fromstring(archive.read(worksheet_member))
    worksheet.tag = "{urn:paraguibench:spoof}worksheet"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        worksheet_member,
        ET.tostring(worksheet, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_xlsx_sheet_data_parent_namespace_spoof_rejects_nested_cells() -> None:
    """验证 cell 只能来自 worksheet/sheetData/row/c 精确路径。

    输入参数：无；伪造 ``sheetData`` 父元素 namespace，其内
        ``row/c/v`` 保持真实 SpreadsheetML QName 与原数值。
    输出返回值：无；游离在受支持父路径外的 cell 不得生成事实。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    worksheet_member = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        worksheet = ET.fromstring(archive.read(worksheet_member))
    sheet_data = next(item for item in worksheet if item.tag.endswith("}sheetData"))
    sheet_data.tag = "{urn:paraguibench:spoof}sheetData"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        worksheet_member,
        ET.tostring(worksheet, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_xlsx_cell_value_namespace_spoof_rejects_document() -> None:
    """验证 cell 中只能读取 SpreadsheetML 精确 ``v`` 直接子元素。

    输入参数：无；把真实 January profit cell 的 ``v`` 标签改到
        伪造 namespace，文本数值与 cell reference 不变。
    输出返回值：无；伪造值元素不得被 local-name 读取。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    worksheet_member = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        worksheet = ET.fromstring(archive.read(worksheet_member))
    target_cell = next(
        item
        for item in worksheet.iter()
        if item.tag.endswith("}c") and item.attrib.get("r") == "D4"
    )
    target_value = next(item for item in target_cell if item.tag.endswith("}v"))
    target_value.tag = "{urn:paraguibench:spoof}v"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        worksheet_member,
        ET.tostring(worksheet, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_xlsx_shared_strings_root_namespace_spoof_rejects_text() -> None:
    """验证 shared-string table 根必须是精确 SpreadsheetML ``sst``。

    输入参数：无；仅伪造真实 ``sharedStrings.xml`` 的根
        namespace，``si/t`` 项与文本保持不变。
    输出返回值：无；不得从伪造 sst 中解析月份或标题。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    shared_string_member = "xl/sharedStrings.xml"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        shared_strings = ET.fromstring(archive.read(shared_string_member))
    shared_strings.tag = "{urn:paraguibench:spoof}sst"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        shared_string_member,
        ET.tostring(shared_strings, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


def test_xlsx_shared_string_text_namespace_spoof_rejects_text() -> None:
    """验证 ``sst/si`` 只采纳直接 ``t`` 或直接 ``r/t`` 精确路径。

    输入参数：无；把真实首个 shared-string ``t`` 元素改到
        伪造 namespace，其文本和 ``si`` 父元素不变。
    输出返回值：无；伪造文本不得通过后代 local-name 匹配进入单元格。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    shared_string_member = "xl/sharedStrings.xml"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        shared_strings = ET.fromstring(archive.read(shared_string_member))
    first_text = next(item for item in shared_strings.iter() if item.tag.endswith("}t"))
    first_text.tag = "{urn:paraguibench:spoof}t"
    spoofed_xlsx = _rewrite_zip_member(
        xlsx_payload,
        shared_string_member,
        ET.tostring(shared_strings, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            spoofed_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False


@pytest.mark.parametrize("mutation", ("wrong_type", "external_mode"))
def test_xlsx_worksheet_relationship_identity_rejects_document(
    mutation: str,
) -> None:
    """验证 workbook 解引用 worksheet 时同时绑定 Type 与 TargetMode。

    输入参数：
        mutation：分别把真实 worksheet relationship 改为 slide Type，
            或保留 Type 但标记 External TargetMode。
    输出返回值：无；两种情况都不得将 target 字节解析为事实表。
    """

    original = _artifact_observation("known_negative")
    xlsx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_Monthly_Data.xlsx"
    )
    relationship_member = "xl/_rels/workbook.xml.rels"
    with zipfile.ZipFile(BytesIO(xlsx_payload)) as archive:
        relationships = ET.fromstring(archive.read(relationship_member))
    worksheet_relationship = next(
        item
        for item in relationships
        if item.attrib.get("Target") == "worksheets/sheet1.xml"
    )
    if mutation == "wrong_type":
        worksheet_relationship.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
        )
    else:
        worksheet_relationship.set("TargetMode", "External")
    invalid_xlsx = _rewrite_zip_member(
        xlsx_payload,
        relationship_member,
        ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_Monthly_Data.xlsx",
            invalid_xlsx,
        )
    )

    assert observation.reference_spreadsheet_unchanged is False
    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False
    assert observation.presentation is not None
    assert observation.presentation.other_facts_match_reference is False


def test_docx_document_root_namespace_spoof_rejects_narrative() -> None:
    """验证 WordprocessingML ``document`` 根必须使用精确 QName。

    输入参数：无；只伪造 audit known-negative DOCX 的根 namespace，
        ``body/p/r/t`` 子树与业务文本保持不变。
    输出返回值：无；不得从伪造 document 全树中提取叙述事实。
    """

    original = _artifact_observation("known_negative")
    docx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonald_finacial_report.docx"
    )
    with zipfile.ZipFile(BytesIO(docx_payload)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    document.tag = "{urn:paraguibench:spoof}document"
    spoofed_docx = _rewrite_zip_member(
        docx_payload,
        "word/document.xml",
        ET.tostring(document, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonald_finacial_report.docx",
            spoofed_docx,
        )
    )

    assert observation.narrative is None
    assert observation.presentation is not None


def test_docx_body_parent_namespace_spoof_rejects_nested_paragraphs() -> None:
    """验证叙述段落只能来自 ``document/body/p`` 精确路径。

    输入参数：无；保留真实 WordprocessingML document 根，仅把
        ``body`` 改到伪造 namespace，内层 ``p/r/t`` 不变。
    输出返回值：无；伪造 body 下的段落不得被全树遍历采纳。
    """

    original = _artifact_observation("known_negative")
    docx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonald_finacial_report.docx"
    )
    with zipfile.ZipFile(BytesIO(docx_payload)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    body = next(item for item in document if item.tag.endswith("}body"))
    body.tag = "{urn:paraguibench:spoof}body"
    spoofed_docx = _rewrite_zip_member(
        docx_payload,
        "word/document.xml",
        ET.tostring(document, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonald_finacial_report.docx",
            spoofed_docx,
        )
    )

    assert observation.narrative is None
    assert observation.presentation is not None


def test_docx_text_namespace_spoof_rejects_target_paragraph() -> None:
    """验证 DOCX 段落只读取 WordprocessingML ``w:t`` 文本。

    输入参数：无；把 audit known-negative 目标叙述段内所有 ``w:t``
        标签改到伪造 namespace，文本和 ``body/p/r`` 结构不变。
    输出返回值：无；伪造 ``t`` 不得通过 local-name 进入业务事实。
    """

    original = _artifact_observation("known_negative")
    docx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonald_finacial_report.docx"
    )
    with zipfile.ZipFile(BytesIO(docx_payload)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    nonempty_paragraphs = [
        paragraph
        for paragraph in document.iter()
        if paragraph.tag.endswith("}p")
        and any(
            (node.text or "").strip()
            for node in paragraph.iter()
            if node.tag.endswith("}t")
        )
    ]
    assert len(nonempty_paragraphs) == 3
    for node in nonempty_paragraphs[1].iter():
        if node.tag.endswith("}t"):
            node.tag = "{urn:paraguibench:spoof}t"
    spoofed_docx = _rewrite_zip_member(
        docx_payload,
        "word/document.xml",
        ET.tostring(document, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonald_finacial_report.docx",
            spoofed_docx,
        )
    )

    assert observation.narrative is None
    assert observation.presentation is not None


def test_docx_vanished_duplicate_facts_are_not_evidence_and_flag_integrity() -> None:
    """验证 ``w:rPr/w:vanish`` 隐藏事实不参与目标提取。

    输入参数：无；复制 audit known-negative 目标段落并将其全部 run
        标记为 vanish，作为第四个 body 直接段落注入。
    输出返回值：无；可见原段落仍提供两项事实，隐藏副本不得
        造成重复候选，但必须记录一次 DOCX 语义完整性失败。
    """

    original = _artifact_observation("known_negative")
    docx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonald_finacial_report.docx"
    )
    with zipfile.ZipFile(BytesIO(docx_payload)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = next(item for item in document if item.tag == f"{{{word_namespace}}}body")
    visible_paragraphs = [
        item
        for item in body
        if item.tag == f"{{{word_namespace}}}p"
        and any(node.tag == f"{{{word_namespace}}}t" for node in item.iter())
    ]
    assert len(visible_paragraphs) == 3
    hidden_duplicate = copy.deepcopy(visible_paragraphs[1])
    for run in hidden_duplicate:
        if run.tag != f"{{{word_namespace}}}r":
            continue
        run_properties = next(
            (item for item in run if item.tag == f"{{{word_namespace}}}rPr"),
            None,
        )
        if run_properties is None:
            run_properties = ET.Element(f"{{{word_namespace}}}rPr")
            run.insert(0, run_properties)
        ET.SubElement(run_properties, f"{{{word_namespace}}}vanish")
    body.insert(len(body) - 1, hidden_duplicate)
    hidden_docx = _rewrite_zip_member(
        docx_payload,
        "word/document.xml",
        ET.tostring(document, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonald_finacial_report.docx",
            hidden_docx,
        )
    )
    result = evaluate_cross_document(observation)

    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False
    assert result.score == 0.6667
    assert result.semantic_integrity_failure_count == 1
    assert result.reason_codes == (
        "DOCX_PROFIT_ORDER_INCORRECT",
        "DOCX_OTHER_FACT_MISMATCH",
    )


def test_docx_table_wrong_facts_are_not_evidence_and_flag_integrity() -> None:
    """验证未审计 ``w:tbl`` 中的错误事实不参与目标提取。

    输入参数：无；向 audit known-negative DOCX body 追加一个可见表格，
        cell 内含与月度 profit 冲突的完整句子。
    输出返回值：无；原叙述仍提供事实，表格内容不得被采纳，
        但未审计可见容器必须记录一次 DOCX 语义完整性失败。
    """

    original = _artifact_observation("known_negative")
    docx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonald_finacial_report.docx"
    )
    with zipfile.ZipFile(BytesIO(docx_payload)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = next(item for item in document if item.tag == f"{{{word_namespace}}}body")
    table = ET.Element(f"{{{word_namespace}}}tbl")
    row = ET.SubElement(table, f"{{{word_namespace}}}tr")
    cell = ET.SubElement(row, f"{{{word_namespace}}}tc")
    paragraph = ET.SubElement(cell, f"{{{word_namespace}}}p")
    run = ET.SubElement(paragraph, f"{{{word_namespace}}}r")
    text = ET.SubElement(run, f"{{{word_namespace}}}t")
    text.text = (
        "January demonstrated the strongest performance with a profit of $999,999, "
        "followed by March ($888,888) and May ($777,777)."
    )
    body.insert(len(body) - 1, table)
    table_docx = _rewrite_zip_member(
        docx_payload,
        "word/document.xml",
        ET.tostring(document, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonald_finacial_report.docx",
            table_docx,
        )
    )
    result = evaluate_cross_document(observation)

    assert observation.narrative is not None
    assert observation.narrative.other_facts_match_reference is False
    assert result.score == 0.6667
    assert result.semantic_integrity_failure_count == 1
    assert result.reason_codes == (
        "DOCX_PROFIT_ORDER_INCORRECT",
        "DOCX_OTHER_FACT_MISMATCH",
    )


def test_docx_package_office_document_relationship_type_is_bound() -> None:
    """验证 package root 必须通过唯一 officeDocument Type 指向主 part。

    输入参数：无；保留 audit known-negative DOCX Target 和 document bytes，
        仅把 ``_rels/.rels`` 中主关系 Type 改为 slide。
    输出返回值：无；不得仅因 package 中存在 ``word/document.xml``
        就将它作为叙述主文档。
    """

    original = _artifact_observation("known_negative")
    docx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonald_finacial_report.docx"
    )
    relationship_member = "_rels/.rels"
    with zipfile.ZipFile(BytesIO(docx_payload)) as archive:
        relationships = ET.fromstring(archive.read(relationship_member))
    office_document = next(
        item
        for item in relationships
        if item.attrib.get("Target") == "word/document.xml"
    )
    office_document.set(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
    )
    invalid_docx = _rewrite_zip_member(
        docx_payload,
        relationship_member,
        ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonald_finacial_report.docx",
            invalid_docx,
        )
    )

    assert observation.narrative is None
    assert observation.presentation is not None


def test_pptx_presentation_root_namespace_spoof_rejects_slides() -> None:
    """验证 PresentationML ``presentation`` 根必须使用精确 QName。

    输入参数：无；只伪造 audit known-negative PPTX 的 presentation 根
        namespace，``sldIdLst/sldId`` 与 relationship ID 保持不变。
    输出返回值：无；不得从伪造 presentation 全树中取得 slide 目标。
    """

    original = _artifact_observation("known_negative")
    pptx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_powerpoint_report.pptx"
    )
    with zipfile.ZipFile(BytesIO(pptx_payload)) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    presentation.tag = "{urn:paraguibench:spoof}presentation"
    spoofed_pptx = _rewrite_zip_member(
        pptx_payload,
        "ppt/presentation.xml",
        ET.tostring(presentation, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_powerpoint_report.pptx",
            spoofed_pptx,
        )
    )

    assert observation.narrative is not None
    assert observation.presentation is None


def test_pptx_slide_id_list_parent_namespace_spoof_rejects_ids() -> None:
    """验证 slide ID 只能来自 ``presentation/sldIdLst/sldId`` 精确路径。

    输入参数：无；保留真实 presentation 根，只把 ``sldIdLst``
        改到伪造 namespace，内部 ``sldId`` 和 ``r:id`` 不变。
    输出返回值：无；游离 slide ID 不得被全树 local-name 采纳。
    """

    original = _artifact_observation("known_negative")
    pptx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_powerpoint_report.pptx"
    )
    with zipfile.ZipFile(BytesIO(pptx_payload)) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
    slide_id_list = next(
        item for item in presentation if item.tag.endswith("}sldIdLst")
    )
    slide_id_list.tag = "{urn:paraguibench:spoof}sldIdLst"
    spoofed_pptx = _rewrite_zip_member(
        pptx_payload,
        "ppt/presentation.xml",
        ET.tostring(presentation, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_powerpoint_report.pptx",
            spoofed_pptx,
        )
    )

    assert observation.narrative is not None
    assert observation.presentation is None


def test_pptx_slide_relationship_wrong_type_rejects_presentation() -> None:
    """验证 ``sldId`` 解引用的 relationship 必须是 slide Type。

    输入参数：无；保留 audit known-negative PPTX slide3 Target、ID 和字节，
        仅把 presentation relationship Type 改为 worksheet。
    输出返回值：无；不得只凭 ID/Target 将该 part 解引用为 slide。
    """

    original = _artifact_observation("known_negative")
    pptx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_powerpoint_report.pptx"
    )
    relationship_member = "ppt/_rels/presentation.xml.rels"
    with zipfile.ZipFile(BytesIO(pptx_payload)) as archive:
        relationships = ET.fromstring(archive.read(relationship_member))
    slide3_relationship = next(
        item
        for item in relationships
        if item.attrib.get("Target") == "slides/slide3.xml"
    )
    slide3_relationship.set(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
    )
    wrong_type_pptx = _rewrite_zip_member(
        pptx_payload,
        relationship_member,
        ET.tostring(relationships, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_powerpoint_report.pptx",
            wrong_type_pptx,
        )
    )

    assert observation.narrative is not None
    assert observation.presentation is None


def test_pptx_slide3_wrong_content_type_rejects_presentation() -> None:
    """验证每个实际解引用 slide part 必须有唯一精确 ContentType。

    输入参数：无；保留 audit known-negative PPTX slide3 relationship 与 part
        字节，仅把其 Override ContentType 改为 worksheet。
    输出返回值：无；该 part 不得按 slide 解析或生成客户数事实。
    """

    original = _artifact_observation("known_negative")
    pptx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_powerpoint_report.pptx"
    )
    with zipfile.ZipFile(BytesIO(pptx_payload)) as archive:
        content_types = ET.fromstring(archive.read("[Content_Types].xml"))
    slide3_override = next(
        item
        for item in content_types
        if item.attrib.get("PartName") == "/ppt/slides/slide3.xml"
    )
    slide3_override.set(
        "ContentType",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
    )
    wrong_type_pptx = _rewrite_zip_member(
        pptx_payload,
        "[Content_Types].xml",
        ET.tostring(content_types, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_powerpoint_report.pptx",
            wrong_type_pptx,
        )
    )

    assert observation.narrative is not None
    assert observation.presentation is None


def test_pptx_shape_parent_namespace_spoof_rejects_nested_text() -> None:
    """验证 slide 文本只能来自受支持的 ``p:sp/p:txBody/a:p`` 路径。

    输入参数：无；把 January slide 的直接 shape 改到伪造
        namespace，内部 ``txBody/a:p/a:r/a:t`` 及业务文本不变。
    输出返回值：无；伪造 shape 后代的文本不得被全树遍历采纳。
    """

    original = _artifact_observation("known_negative")
    pptx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_powerpoint_report.pptx"
    )
    slide_member = "ppt/slides/slide3.xml"
    with zipfile.ZipFile(BytesIO(pptx_payload)) as archive:
        slide = ET.fromstring(archive.read(slide_member))
    shapes = [item for item in slide.iter() if item.tag.endswith("}sp")]
    assert shapes
    for shape in shapes:
        shape.tag = "{urn:paraguibench:spoof}sp"
    spoofed_pptx = _rewrite_zip_member(
        pptx_payload,
        slide_member,
        ET.tostring(slide, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_powerpoint_report.pptx",
            spoofed_pptx,
        )
    )

    assert observation.narrative is not None
    assert observation.presentation is None


def test_pptx_mce_selects_fallback_without_adopting_ignored_choice() -> None:
    """验证 MCE AlternateContent 显式选择可用分支且不合并 ignored 节点。

    输入参数：无；把 January slide 的 shape 包入 AlternateContent：
        不受支持 p14 Choice 含伪造 customers，Fallback 保留真实 shape。
    输出返回值：无；应显式选中 Fallback 并仍提取真实 January
        customers，不得采纳或拼接 ignored Choice 中的值。
    """

    original = _artifact_observation("known_negative")
    pptx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonalds_powerpoint_report.pptx"
    )
    slide_member = "ppt/slides/slide3.xml"
    with zipfile.ZipFile(BytesIO(pptx_payload)) as archive:
        slide = ET.fromstring(archive.read(slide_member))
    shape_tree = next(item for item in slide.iter() if item.tag.endswith("}spTree"))
    shapes = [item for item in shape_tree if item.tag.endswith("}sp")]
    assert shapes
    for shape in shapes:
        shape_tree.remove(shape)

    mce_namespace = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    alternate = ET.Element(f"{{{mce_namespace}}}AlternateContent")
    choice = ET.SubElement(alternate, f"{{{mce_namespace}}}Choice", Requires="p14")
    choice.set(
        "xmlns:p14",
        "http://schemas.microsoft.com/office/powerpoint/2010/main",
    )
    fallback = ET.SubElement(alternate, f"{{{mce_namespace}}}Fallback")
    for shape in shapes:
        ignored = copy.deepcopy(shape)
        for text in ignored.iter():
            if (text.text or "").replace(" ", "").startswith("Customers:"):
                text.text = "Customers:999999"
        choice.append(ignored)
        fallback.append(shape)
    shape_tree.append(alternate)
    wrapped_pptx = _rewrite_zip_member(
        pptx_payload,
        slide_member,
        ET.tostring(slide, encoding="utf-8", xml_declaration=True),
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonalds_powerpoint_report.pptx",
            wrapped_pptx,
        )
    )

    assert observation.presentation is not None
    assert observation.presentation.january_customers == 1_895
    assert observation.presentation.other_facts_match_reference is True


def test_production_capture_returns_cross_document_typed_observation() -> None:
    """验证 production source 在原子读取后立即调用专属 typed bridge。

    输入参数：无；controller 返回 audit known-negative 三文件闭集。
    输出返回值：无；source 不再暴露 generic observation，且 known-negative
        仍由 pure evaluator 记为任务 FAIL。
    """

    observation = PipelineImplicitArtifactEvidenceSource().capture(
        CROSS_DOCUMENT_TASK_ID,
        _FixtureController(_fixed_revision_fixture("known_negative")),
        guest_shared_dir="/home/oai/share",
    )

    assert isinstance(observation, CrossDocumentObservation)
    result = evaluate_cross_document(observation)
    assert result.passed is False
    assert result.reason_codes == ("DOCX_PROFIT_ORDER_INCORRECT",)


def test_typed_observation_repr_does_not_expose_facts_or_document_identity() -> None:
    """验证 typed observation 的调试表示不泄漏业务事实。

    输入参数：无；解析 audit known-negative 以覆盖月份、金额、客户数和文档内容。
    输出返回值：无；repr 只允许暴露完整性与资源计数。
    """

    observation = build_cross_document_observation(
        _artifact_observation("known_negative")
    )
    rendered = repr(observation).lower()

    for private_value in (
        "january",
        "december",
        "47109",
        "1895",
        "mcdonald",
        ".docx",
        ".pptx",
        ".xlsx",
    ):
        assert private_value not in rendered


def test_missing_docx_is_a_fixed_denominator_task_failure() -> None:
    """验证缺少 DOCX 不会缩小三事实分母或升级为内部错误。

    输入参数：无；从 audit known-negative generic observation 删除 DOCX。
    输出返回值：无；PPTX 唯一匹配事实得 1/3，并报告一个缺失文档。
    """

    original = _artifact_observation("known_negative")
    missing_docx = PipelineImplicitArtifactObservation(
        task_id=original.task_id,
        protocol_id=original.protocol_id,
        complete=True,
        _files=tuple(
            item
            for item in original.iter_files_for_evaluator()
            if item.relative_path != "McDonald_finacial_report.docx"
        ),
    )

    result = evaluate_cross_document(build_cross_document_observation(missing_docx))

    assert result.passed is False
    assert result.score == 0.3333
    assert result.required_fact_count == 3
    assert result.matched_fact_count == 1
    assert result.missing_document_count == 1
    assert result.reason_codes == ("MISSING_DOCUMENT",)


def test_extra_document_is_counted_without_exposing_its_identity() -> None:
    """验证三文档之外的文件导致任务 FAIL 且不进入公开诊断。

    输入参数：无；向 audit known-negative 闭集增加包含私密哨兵的第四个文件。
    输出返回值：无；额外计数为一，错误和结果表示均不回显路径或内容。
    """

    original = _artifact_observation("known_negative")
    private_payload = b"PRIVATE EXTRA DOCUMENT"
    extra = PipelineImplicitArtifactFile(
        relative_path="PRIVATE-extra.docx",
        size_bytes=len(private_payload),
        sha256=hashlib.sha256(private_payload).hexdigest(),
        _payload=private_payload,
    )
    with_extra = PipelineImplicitArtifactObservation(
        task_id=original.task_id,
        protocol_id=original.protocol_id,
        complete=True,
        _files=(*tuple(original.iter_files_for_evaluator()), extra),
    )

    observation = build_cross_document_observation(with_extra)
    result = evaluate_cross_document(observation)

    assert result.passed is False
    assert result.unexpected_document_count == 1
    assert result.reason_codes == (
        "DOCX_PROFIT_ORDER_INCORRECT",
        "UNEXPECTED_DOCUMENT",
    )
    assert "PRIVATE" not in repr(observation)
    assert "PRIVATE" not in repr(result)


def test_bridge_rejects_portable_path_collision_with_fixed_error() -> None:
    """验证绕过 generic 构造的大小写碰撞仍在 typed 边界失败关闭。

    输入参数：无；直接构造两个大小写折叠后相同的路径成员。
    输出返回值：无；bridge 抛固定脱敏错误，不选择任一碰撞字节。
    """

    payload = b"PRIVATE COLLISION"
    files = tuple(
        PipelineImplicitArtifactFile(
            relative_path=path,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            _payload=payload,
        )
        for path in ("Folder/private.docx", "folder/PRIVATE.docx")
    )
    observation = PipelineImplicitArtifactObservation(
        task_id=CROSS_DOCUMENT_TASK_ID,
        protocol_id=CROSS_DOCUMENT_PROTOCOL_ID,
        complete=True,
        _files=files,
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        build_cross_document_observation(observation)

    assert captured.value.code == "TYPED_OBSERVATION_INVALID"
    assert str(captured.value) == "TYPED_OBSERVATION_INVALID"
    assert "PRIVATE" not in repr(captured.value)


def test_malicious_docx_entity_is_a_known_document_failure() -> None:
    """验证含 DTD/entity 的 DOCX 被拒绝为任务 FAIL 而非解析执行。

    输入参数：无；向 audit known-negative ``word/document.xml`` 注入实体声明。
    输出返回值：无；DOCX 事实按缺失计入固定分母，PPTX 事实仍可评价。
    """

    original = _artifact_observation("known_negative")
    docx_payload = next(
        item.read_for_evaluator()
        for item in original.iter_files_for_evaluator()
        if item.relative_path == "McDonald_finacial_report.docx"
    )
    with zipfile.ZipFile(BytesIO(docx_payload)) as archive:
        document_xml = archive.read("word/document.xml")
    malicious_xml = document_xml.replace(
        b"?>",
        b'?><!DOCTYPE document [<!ENTITY private SYSTEM "file:///private">]>',
        1,
    )
    malicious_docx = _rewrite_zip_member(
        docx_payload,
        "word/document.xml",
        malicious_xml,
    )

    observation = build_cross_document_observation(
        _replace_artifact_payload(
            original,
            "McDonald_finacial_report.docx",
            malicious_docx,
        )
    )
    result = evaluate_cross_document(observation)

    assert observation.narrative is None
    assert result.passed is False
    assert result.score == 0.3333
    assert result.reason_codes == ("MISSING_DOCUMENT",)
    assert "private" not in repr(observation).lower()


def test_unknown_parser_failure_uses_fixed_evaluator_error_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 bridge 内部未知故障不会伪装成 Agent 的任务 FAIL。

    输入参数：
        monkeypatch：把 DOCX parser 替换为抛含私密内容的未知错误。
    输出返回值：
        无；public bridge 只抛 ``TYPED_OBSERVATION_INVALID``，由现有
        AttemptRunner 合同落为 ERROR/null，且错误不回显原异常。
    """

    def fail_internally(*arguments: object, **keywords: object) -> None:
        """注入不属于已知文档错误的内部故障。

        输入参数：arguments/keywords 为 production parser 调用参数。
        输出返回值：不返回，抛带私密哨兵的 RuntimeError。
        """

        del arguments, keywords
        raise RuntimeError("PRIVATE INTERNAL PARSER FAILURE")

    monkeypatch.setattr(
        cross_document_bridge,
        "_parse_docx_facts",
        fail_internally,
    )

    with pytest.raises(PipelineImplicitArtifactEvidenceError) as captured:
        build_cross_document_observation(_artifact_observation("known_negative"))

    assert captured.value.code == "TYPED_OBSERVATION_INVALID"
    assert str(captured.value) == "TYPED_OBSERVATION_INVALID"
    assert "PRIVATE" not in repr(captured.value)
