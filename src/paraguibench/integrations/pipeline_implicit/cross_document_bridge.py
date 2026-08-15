"""CombinationDocs-002 受控 OOXML 字节到 typed 事实的转换边界。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
from io import BytesIO
import posixpath
import re
from types import MappingProxyType
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from paraguibench.evaluation.pipeline_implicit import (
    CROSS_DOCUMENT_PROTOCOL_ID,
    CROSS_DOCUMENT_TASK_ID,
    CrossDocumentObservation,
    NarrativeFacts,
    PresentationFacts,
)

from .artifact_evidence import (
    PipelineImplicitArtifactEvidenceError,
    PipelineImplicitArtifactObservation,
)


_DOCX_PATH = "McDonald_finacial_report.docx"
_XLSX_PATH = "McDonalds_Monthly_Data.xlsx"
_PPTX_PATH = "McDonalds_powerpoint_report.pptx"
_EXPECTED_PATHS = frozenset({_DOCX_PATH, _XLSX_PATH, _PPTX_PATH})
_PINNED_XLSX_SHA256 = "abaf2d2622354d6c8a1cd6115cda4b1e5b82ccdcd01565d739e75aa606e750b9"
_OOXML_IDENTITIES = MappingProxyType(
    {
        "docx": (
            "word/document.xml",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        ),
        "xlsx": (
            "xl/workbook.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        ),
        "pptx": (
            "ppt/presentation.xml",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        ),
    }
)
_MAX_ARCHIVE_MEMBERS = 128
_MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024
_MAX_ARCHIVE_EXPANDED_BYTES = 8 * 1024 * 1024
_MAX_ARCHIVE_COMPRESSION_RATIO = 100
_MAX_XML_ELEMENTS = 16_384
_MAX_XML_DEPTH = 64
_MAX_XML_ATTRIBUTES = 64
_MAX_XML_TEXT_BYTES = 512 * 1024
_CELL_REFERENCE_PATTERN = re.compile(r"[A-Z]{1,3}[1-9][0-9]{0,6}")
_CONTENT_TYPES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/content-types"
)
_CONTENT_TYPES_ROOT = f"{{{_CONTENT_TYPES_NAMESPACE}}}Types"
_CONTENT_TYPE_OVERRIDE = f"{{{_CONTENT_TYPES_NAMESPACE}}}Override"
_PACKAGE_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
_RELATIONSHIPS_ROOT = f"{{{_PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationships"
_RELATIONSHIP_ELEMENT = f"{{{_PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationship"
_OFFICE_RELATIONSHIP_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_OFFICE_DOCUMENT_RELATIONSHIP_TYPE = f"{_OFFICE_RELATIONSHIP_NAMESPACE}/officeDocument"
_WORKSHEET_RELATIONSHIP_TYPE = f"{_OFFICE_RELATIONSHIP_NAMESPACE}/worksheet"
_SLIDE_RELATIONSHIP_TYPE = f"{_OFFICE_RELATIONSHIP_NAMESPACE}/slide"
_SPREADSHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_WORKBOOK = f"{{{_SPREADSHEET_NAMESPACE}}}workbook"
_XLSX_SHEETS = f"{{{_SPREADSHEET_NAMESPACE}}}sheets"
_XLSX_SHEET = f"{{{_SPREADSHEET_NAMESPACE}}}sheet"
_XLSX_WORKSHEET = f"{{{_SPREADSHEET_NAMESPACE}}}worksheet"
_XLSX_SHEET_DATA = f"{{{_SPREADSHEET_NAMESPACE}}}sheetData"
_XLSX_ROW = f"{{{_SPREADSHEET_NAMESPACE}}}row"
_XLSX_CELL = f"{{{_SPREADSHEET_NAMESPACE}}}c"
_XLSX_FORMULA = f"{{{_SPREADSHEET_NAMESPACE}}}f"
_XLSX_VALUE = f"{{{_SPREADSHEET_NAMESPACE}}}v"
_XLSX_INLINE_STRING = f"{{{_SPREADSHEET_NAMESPACE}}}is"
_XLSX_SHARED_STRING_TABLE = f"{{{_SPREADSHEET_NAMESPACE}}}sst"
_XLSX_SHARED_STRING_ITEM = f"{{{_SPREADSHEET_NAMESPACE}}}si"
_XLSX_RICH_TEXT_RUN = f"{{{_SPREADSHEET_NAMESPACE}}}r"
_XLSX_TEXT = f"{{{_SPREADSHEET_NAMESPACE}}}t"
_XLSX_WORKSHEET_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
)
_WORDPROCESSING_NAMESPACE = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
)
_DOCX_DOCUMENT = f"{{{_WORDPROCESSING_NAMESPACE}}}document"
_DOCX_BODY = f"{{{_WORDPROCESSING_NAMESPACE}}}body"
_DOCX_PARAGRAPH = f"{{{_WORDPROCESSING_NAMESPACE}}}p"
_DOCX_PARAGRAPH_PROPERTIES = f"{{{_WORDPROCESSING_NAMESPACE}}}pPr"
_DOCX_PROOF_ERROR = f"{{{_WORDPROCESSING_NAMESPACE}}}proofErr"
_DOCX_SECTION_PROPERTIES = f"{{{_WORDPROCESSING_NAMESPACE}}}sectPr"
_DOCX_RUN = f"{{{_WORDPROCESSING_NAMESPACE}}}r"
_DOCX_RUN_PROPERTIES = f"{{{_WORDPROCESSING_NAMESPACE}}}rPr"
_DOCX_TEXT = f"{{{_WORDPROCESSING_NAMESPACE}}}t"
_DOCX_VANISH = f"{{{_WORDPROCESSING_NAMESPACE}}}vanish"
_PRESENTATION_NAMESPACE = "http://schemas.openxmlformats.org/presentationml/2006/main"
_PPTX_PRESENTATION = f"{{{_PRESENTATION_NAMESPACE}}}presentation"
_PPTX_SLIDE_ID_LIST = f"{{{_PRESENTATION_NAMESPACE}}}sldIdLst"
_PPTX_SLIDE_ID = f"{{{_PRESENTATION_NAMESPACE}}}sldId"
_PPTX_SLIDE = f"{{{_PRESENTATION_NAMESPACE}}}sld"
_PPTX_COMMON_SLIDE_DATA = f"{{{_PRESENTATION_NAMESPACE}}}cSld"
_PPTX_SHAPE_TREE = f"{{{_PRESENTATION_NAMESPACE}}}spTree"
_PPTX_SHAPE = f"{{{_PRESENTATION_NAMESPACE}}}sp"
_PPTX_TEXT_BODY = f"{{{_PRESENTATION_NAMESPACE}}}txBody"
_PPTX_SLIDE_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
)
_DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
_DRAWING_PARAGRAPH = f"{{{_DRAWING_NAMESPACE}}}p"
_DRAWING_RUN = f"{{{_DRAWING_NAMESPACE}}}r"
_DRAWING_TEXT = f"{{{_DRAWING_NAMESPACE}}}t"
_MARKUP_COMPATIBILITY_NAMESPACE = (
    "http://schemas.openxmlformats.org/markup-compatibility/2006"
)
_MCE_ALTERNATE_CONTENT = f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}AlternateContent"
_MCE_CHOICE = f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}Choice"
_MCE_FALLBACK = f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}Fallback"
_MCE_PREFIX_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_SUPPORTED_MCE_NAMESPACES = frozenset(
    {
        _CONTENT_TYPES_NAMESPACE,
        _PACKAGE_RELATIONSHIPS_NAMESPACE,
        _SPREADSHEET_NAMESPACE,
        _WORDPROCESSING_NAMESPACE,
        _PRESENTATION_NAMESPACE,
        _DRAWING_NAMESPACE,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
)
_RELATIONSHIP_ID_ATTRIBUTE = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
)
_MONEY_PATTERN = r"\$([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)"
_DOCX_FIRST_PARAGRAPH = (
    "This McDonald's regional location generated total revenue of $1,398,815 "
    "throughout the year, serving 35,140 customers with an average transaction "
    "value of $39.81. The establishment maintained a total profit of $316,340 "
    "despite facing operational challenges during certain periods."
)
_DOCX_TARGET_PREFIX = (
    "The performance showed significant fluctuations across months. Three months "
    "experienced negative profits: February (-$9,019), March (-$5,823), and May "
    "(-$12,207), likely due to higher operational costs during these periods. "
    "Conversely, "
)
_DOCX_THIRD_PARAGRAPH = (
    "Revenue peaked in November at $139,448, while the lowest revenue occurred in "
    "March at $81,835. Customer traffic varied considerably, ranging from 1,621 "
    "visitors in April to 4,167 in August. The average transaction value fluctuated "
    "between $23.61 and $84.63, reflecting diverse ordering patterns and promotional "
    "activities throughout the year. Overall, the location maintained profitability "
    "despite quarterly challenges."
)
_DOCX_TARGET_PATTERN = re.compile(
    re.escape(_DOCX_TARGET_PREFIX)
    + r"(?P<first>[A-Za-z]+) demonstrated the strongest performance with a profit of "
    + rf"{_MONEY_PATTERN}, followed by (?P<second>[A-Za-z]+) \({_MONEY_PATTERN}\) "
    + rf"and (?P<third>[A-Za-z]+) \({_MONEY_PATTERN}\)\."
)
_EXPECTED_XLSX_ROWS = (
    (
        "Month",
        "Revenue (USD)",
        "Cost (USD)",
        "Profit (USD)",
        "Customers",
        "Avg. Transaction (USD)",
    ),
    ("January", "136690", "89581", "47109", "1895", "72.13"),
    ("February", "88892", "97911", "-9019", "3602", "24.68"),
    ("March", "81835", "87658", "-5823", "1896", "43.16"),
    ("April", "137192", "106950", "30242", "1621", "84.63"),
    ("May", "85061", "97268", "-12207", "3602", "23.61"),
    ("June", "110155", "73006", "37149", "3650", "30.18"),
    ("July", "138887", "86245", "52642", "3069", "45.25"),
    ("August", "100348", "62479", "37869", "4167", "24.08"),
    ("September", "112723", "77772", "34951", "2341", "48.15"),
    ("October", "135645", "109001", "26644", "3896", "34.82"),
    ("November", "139448", "110760", "28688", "2885", "48.34"),
    ("December", "131939", "83844", "48095", "2516", "52.44"),
    ("TOTAL", "1398815", "1082475", "316340", "35140", "39.81"),
)


class _DocumentRejected(ValueError):
    """表示 Agent 产出的文档无法形成合法任务事实，结果应为 FAIL。"""


@dataclass(frozen=True, slots=True, repr=False)
class _SpreadsheetFacts:
    """保存 XLSX 受控解析后仅供本次 bridge 使用的事实。

    输入参数：
        unchanged：完整月度事实闭集是否等于 pinned reference。
        rows：标题之外的 13 行数据及 TOTAL 行。
    输出返回值：
        不可持久化且 ``repr`` 不暴露业务值的内部投影。
    """

    unchanged: bool
    rows: tuple[tuple[str, ...], ...]

    def __repr__(self) -> str:
        """返回只含完整性结论和行数的脱敏表示。

        输入参数：无。
        输出返回值：不含月份、金额、客户数或单元格的字符串。
        """

        return (
            "_SpreadsheetFacts("
            f"unchanged={self.unchanged!r}, row_count={len(self.rows)!r})"
        )


def build_cross_document_observation(
    artifact_observation: PipelineImplicitArtifactObservation,
) -> CrossDocumentObservation:
    """把冻结的三文档闭集投影为 CombinationDocs-002 typed 事实。

    输入参数：
        artifact_observation：经 manifest—nofollow bytes—manifest 原子捕获的
            generic artifact observation；接口不接受 Agent final text。
    输出返回值：
        XLSX 完整性、DOCX January 利润/顺序、PPTX January 客户数及
        固定三文档闭集计数构成的 ``CrossDocumentObservation``。
    异常：
        PipelineImplicitArtifactEvidenceError：generic 身份、路径闭集合同或
            bridge 自身发生未知内部故障；异常文本不包含路径或文档内容。

    已知 OOXML 格式错误、缺文档和额外文档被投影为固定分母 FAIL，
    而不是把不可解析输入误记为 evaluator 内部 ERROR。
    """

    if (
        not isinstance(
            artifact_observation,
            PipelineImplicitArtifactObservation,
        )
        or artifact_observation.task_id != CROSS_DOCUMENT_TASK_ID
        or artifact_observation.protocol_id != CROSS_DOCUMENT_PROTOCOL_ID
        or artifact_observation.complete is not True
    ):
        raise PipelineImplicitArtifactEvidenceError("TYPED_OBSERVATION_INVALID")

    expected_payloads: dict[str, bytes] = {}
    unexpected_document_count = 0
    portable_paths: set[str] = set()
    try:
        for artifact_file in artifact_observation.iter_files_for_evaluator():
            path = artifact_file.relative_path
            portable_path = unicodedata.normalize("NFC", path).casefold()
            if portable_path in portable_paths:
                raise PipelineImplicitArtifactEvidenceError("TYPED_OBSERVATION_INVALID")
            portable_paths.add(portable_path)
            if path not in _EXPECTED_PATHS:
                unexpected_document_count += 1
                continue
            expected_payloads[path] = artifact_file.read_for_evaluator()
    except PipelineImplicitArtifactEvidenceError:
        raise
    except Exception:
        raise PipelineImplicitArtifactEvidenceError(
            "TYPED_OBSERVATION_INVALID"
        ) from None

    spreadsheet: _SpreadsheetFacts | None = None
    spreadsheet_payload = expected_payloads.get(_XLSX_PATH)
    if spreadsheet_payload is not None:
        try:
            spreadsheet = _parse_xlsx_facts(spreadsheet_payload)
        except _DocumentRejected:
            spreadsheet = None
        except Exception:
            raise PipelineImplicitArtifactEvidenceError(
                "TYPED_OBSERVATION_INVALID"
            ) from None

    narrative: NarrativeFacts | None = None
    narrative_payload = expected_payloads.get(_DOCX_PATH)
    if narrative_payload is not None:
        try:
            narrative = _parse_docx_facts(narrative_payload, spreadsheet)
        except _DocumentRejected:
            narrative = None
        except Exception:
            raise PipelineImplicitArtifactEvidenceError(
                "TYPED_OBSERVATION_INVALID"
            ) from None

    presentation: PresentationFacts | None = None
    presentation_payload = expected_payloads.get(_PPTX_PATH)
    if presentation_payload is not None:
        try:
            presentation = _parse_pptx_facts(
                presentation_payload,
                spreadsheet,
            )
        except _DocumentRejected:
            presentation = None
        except Exception:
            raise PipelineImplicitArtifactEvidenceError(
                "TYPED_OBSERVATION_INVALID"
            ) from None

    return CrossDocumentObservation(
        complete=True,
        reference_spreadsheet_unchanged=(
            spreadsheet is not None and spreadsheet.unchanged
        ),
        narrative=narrative,
        presentation=presentation,
        unexpected_document_count=unexpected_document_count,
    )


def _parse_xlsx_facts(payload: bytes) -> _SpreadsheetFacts:
    """从受控 XLSX bytes 提取完整月度表事实。

    输入参数：
        payload：generic capture 已核验长度/SHA 的工作簿字节。
    输出返回值：
        包含完整事实闭集比较结论和归一化数据行的内部投影。
    异常：
        _DocumentRejected：容器、关系、sheet、单元格或标量无效。
    """

    with _open_validated_ooxml(payload, kind="xlsx") as archive:
        workbook = _read_xml_root(archive, "xl/workbook.xml")
        if workbook.tag != _XLSX_WORKBOOK:
            raise _DocumentRejected()
        relationships = _relationship_targets(
            archive,
            "xl/_rels/workbook.xml.rels",
            source_member="xl/workbook.xml",
            required_type=_WORKSHEET_RELATIONSHIP_TYPE,
        )
        sheet_containers = [item for item in workbook if item.tag == _XLSX_SHEETS]
        if len(sheet_containers) != 1:
            raise _DocumentRejected()
        sheets = [item for item in sheet_containers[0] if item.tag == _XLSX_SHEET]
        if len(sheets) != 1 or sheets[0].attrib.get("name") != "Monthly Data":
            raise _DocumentRejected()
        relationship_id = _relationship_id(sheets[0])
        sheet_member = relationships.get(relationship_id)
        if sheet_member is None:
            raise _DocumentRejected()
        _require_part_content_type(
            archive,
            sheet_member,
            expected_content_type=_XLSX_WORKSHEET_CONTENT_TYPE,
        )
        shared_strings = _read_shared_strings(archive)
        worksheet = _read_xml_root(archive, sheet_member)
        if worksheet.tag != _XLSX_WORKSHEET:
            raise _DocumentRejected()
        sheet_data_containers = [
            item for item in worksheet if item.tag == _XLSX_SHEET_DATA
        ]
        if len(sheet_data_containers) != 1:
            raise _DocumentRejected()
        cells: dict[str, str] = {}
        for row in sheet_data_containers[0]:
            if row.tag != _XLSX_ROW:
                continue
            for cell in row:
                if cell.tag != _XLSX_CELL:
                    continue
                reference = cell.attrib.get("r")
                if (
                    reference is None
                    or _CELL_REFERENCE_PATTERN.fullmatch(reference) is None
                    or reference in cells
                    or any(child.tag == _XLSX_FORMULA for child in cell)
                ):
                    raise _DocumentRejected()
                value = _xlsx_cell_value(cell, shared_strings)
                if value is not None:
                    cells[reference] = value

        expected_cells: dict[str, str] = {
            "A1": "McDonald's - Regional Monthly Performance 2026"
        }
        for row_index, row in enumerate(_EXPECTED_XLSX_ROWS, start=3):
            for column_index, value in enumerate(row, start=1):
                expected_cells[f"{chr(64 + column_index)}{row_index}"] = value
        required_references = set(expected_cells)
        if not required_references.issubset(cells):
            raise _DocumentRejected()
        rows = tuple(
            tuple(cells[f"{chr(64 + column)}{row}"] for column in range(1, 7))
            for row in range(4, 17)
        )
        return _SpreadsheetFacts(
            unchanged=(hashlib.sha256(payload).hexdigest() == _PINNED_XLSX_SHA256),
            rows=rows,
        )


def _parse_docx_facts(
    payload: bytes,
    spreadsheet: _SpreadsheetFacts | None,
) -> NarrativeFacts:
    """从受控 DOCX bytes 提取 January 利润和叙述顺序。

    输入参数：
        payload：已冻结的 DOCX 字节。
        spreadsheet：同一原子闭集内解析的 XLSX 事实；缺失时只允许形成
            ``other_facts_match_reference=False`` 的任务失败观测。
    输出返回值：
        January 利润、前三利润叙述顺序及其他叙述完整性。
    异常：
        _DocumentRejected：OOXML 或目标叙述不存在、重复或类型无效。
    """

    with _open_validated_ooxml(payload, kind="docx") as archive:
        document = _read_xml_root(archive, "word/document.xml")
        if document.tag != _DOCX_DOCUMENT:
            raise _DocumentRejected()
        bodies = [item for item in document if item.tag == _DOCX_BODY]
        if len(bodies) != 1:
            raise _DocumentRejected()
        paragraph_projections = tuple(
            _docx_paragraph_text(paragraph)
            for paragraph in bodies[0]
            if paragraph.tag == _DOCX_PARAGRAPH
        )
        visible_semantics_intact = all(
            integrity_ok for _, integrity_ok in paragraph_projections
        ) and all(
            item.tag in {_DOCX_PARAGRAPH, _DOCX_SECTION_PROPERTIES}
            for item in bodies[0]
        )
        paragraphs = tuple(_normalize_text(text) for text, _ in paragraph_projections)
        paragraphs = tuple(value for value in paragraphs if value)
    candidates = [
        match
        for paragraph in paragraphs
        if (match := _DOCX_TARGET_PATTERN.fullmatch(paragraph)) is not None
    ]
    if len(candidates) != 1:
        raise _DocumentRejected()
    match = candidates[0]
    months = tuple(match.group(name).lower() for name in ("first", "second", "third"))
    values = tuple(_parse_grouped_integer(match.group(index)) for index in (2, 4, 6))
    if len(set(months)) != 3 or "january" not in months:
        raise _DocumentRejected()
    by_month = dict(zip(months, values, strict=True))
    january_profit = by_month["january"]

    other_facts_match = (
        visible_semantics_intact
        and len(paragraphs) == 3
        and paragraphs[0] == _DOCX_FIRST_PARAGRAPH
        and paragraphs[2] == _DOCX_THIRD_PARAGRAPH
    )
    monthly_rows = _monthly_row_map(spreadsheet)
    if monthly_rows is None:
        other_facts_match = False
    else:
        expected_order = tuple(
            month
            for month, _ in sorted(
                monthly_rows.items(),
                key=lambda item: int(item[1][3]),
                reverse=True,
            )[:3]
        )
        if set(months) != set(expected_order):
            other_facts_match = False
        for month, value in by_month.items():
            if month != "january" and (
                month not in monthly_rows or int(monthly_rows[month][3]) != value
            ):
                other_facts_match = False
    return NarrativeFacts(
        january_profit=january_profit,
        strongest_profit_order=months,
        other_facts_match_reference=other_facts_match,
    )


def _docx_paragraph_text(paragraph: ET.Element) -> tuple[str, bool]:
    """只从受支持的 ``w:p/w:r/w:t`` 路径拼接 DOCX 段落。

    输入参数：
        paragraph：``document/body`` 下的精确 WordprocessingML 段落。
    输出返回值：
        所有非隐藏直接 run 中 ``w:t`` 的拼接值，以及本段落
        是否未出现 vanish 隐藏语义。
    """

    paragraph_properties = [
        item for item in paragraph if item.tag == _DOCX_PARAGRAPH_PROPERTIES
    ]
    integrity_ok = all(
        item.tag in {_DOCX_PARAGRAPH_PROPERTIES, _DOCX_PROOF_ERROR, _DOCX_RUN}
        for item in paragraph
    )
    if len(paragraph_properties) > 1:
        integrity_ok = False
    if any(
        any(True for _ in properties.iter(_DOCX_VANISH))
        for properties in paragraph_properties
    ):
        return "", False
    fragments: list[str] = []
    for run in paragraph:
        if run.tag != _DOCX_RUN:
            continue
        run_properties = [item for item in run if item.tag == _DOCX_RUN_PROPERTIES]
        if len(run_properties) > 1 or any(
            item.tag not in {_DOCX_RUN_PROPERTIES, _DOCX_TEXT} for item in run
        ):
            integrity_ok = False
        if any(
            any(True for _ in properties.iter(_DOCX_VANISH))
            for properties in run_properties
        ):
            integrity_ok = False
            continue
        fragments.extend(text.text or "" for text in run if text.tag == _DOCX_TEXT)
    return "".join(fragments), integrity_ok


def _parse_pptx_facts(
    payload: bytes,
    spreadsheet: _SpreadsheetFacts | None,
) -> PresentationFacts:
    """从受控 PPTX bytes 提取 January customers 并核对其余事实。

    输入参数：
        payload：已冻结的 PPTX 字节。
        spreadsheet：同一闭集 XLSX 事实，用于构造非目标字段基线。
    输出返回值：
        January customers 与其他 PPT 事实完整性布尔值。
    异常：
        _DocumentRejected：slide 关系、文本闭集或目标字段无效。
    """

    with _open_validated_ooxml(payload, kind="pptx") as archive:
        presentation = _read_xml_root(archive, "ppt/presentation.xml")
        if presentation.tag != _PPTX_PRESENTATION:
            raise _DocumentRejected()
        relationships = _relationship_targets(
            archive,
            "ppt/_rels/presentation.xml.rels",
            source_member="ppt/presentation.xml",
            required_type=_SLIDE_RELATIONSHIP_TYPE,
        )
        slide_id_lists = [
            item for item in presentation if item.tag == _PPTX_SLIDE_ID_LIST
        ]
        if len(slide_id_lists) != 1:
            raise _DocumentRejected()
        slide_members: list[str] = []
        for item in slide_id_lists[0]:
            if item.tag != _PPTX_SLIDE_ID:
                continue
            relationship_id = _relationship_id(item)
            member = relationships.get(relationship_id)
            if member is None or member in slide_members:
                raise _DocumentRejected()
            slide_members.append(member)
        if len(slide_members) != 4:
            raise _DocumentRejected()
        slides: list[tuple[str, ...]] = []
        for member in slide_members:
            _require_part_content_type(
                archive,
                member,
                expected_content_type=_PPTX_SLIDE_CONTENT_TYPE,
            )
            slide = _read_xml_root(archive, member)
            paragraphs = tuple(
                _normalize_presentation_text(_pptx_paragraph_text(paragraph))
                for paragraph in _pptx_slide_paragraphs(slide)
            )
            slides.append(tuple(value for value in paragraphs if value))

    january_slide_indexes = [
        index
        for index, slide in enumerate(slides)
        if any(value.casefold() == "jan data" for value in slide)
    ]
    if len(january_slide_indexes) != 1:
        raise _DocumentRejected()
    january_slide = slides[january_slide_indexes[0]]
    customer_matches = [
        re.fullmatch(r"Customers:([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)", value)
        for value in january_slide
    ]
    customer_matches = [match for match in customer_matches if match is not None]
    if len(customer_matches) != 1:
        raise _DocumentRejected()
    january_customers = _parse_grouped_integer(customer_matches[0].group(1))
    normalized_slides = tuple(
        tuple(
            "Customers:<TARGET>"
            if index == january_slide_indexes[0]
            and re.fullmatch(r"Customers:[0-9][0-9,]*", value)
            else value
            for value in slide
        )
        for index, slide in enumerate(slides)
    )
    expected_slides = _expected_presentation_projection(spreadsheet)
    return PresentationFacts(
        january_customers=january_customers,
        other_facts_match_reference=(
            expected_slides is not None and normalized_slides == expected_slides
        ),
    )


def _pptx_slide_paragraphs(slide: ET.Element) -> tuple[ET.Element, ...]:
    """返回受支持 shape 路径中的 DrawingML 段落。

    输入参数：
        slide：已解析的单页 PPTX 主文档根。
    输出返回值：
        仅位于 ``p:sld/p:cSld/p:spTree/p:sp/p:txBody/a:p`` 的段落。
    异常：
        _DocumentRejected：根、common slide data 或 shape tree 结构不唯一。
    """

    if slide.tag != _PPTX_SLIDE:
        raise _DocumentRejected()
    common_slide_data = [item for item in slide if item.tag == _PPTX_COMMON_SLIDE_DATA]
    if len(common_slide_data) != 1:
        raise _DocumentRejected()
    shape_trees = [
        item for item in common_slide_data[0] if item.tag == _PPTX_SHAPE_TREE
    ]
    if len(shape_trees) != 1:
        raise _DocumentRejected()
    paragraphs: list[ET.Element] = []
    for shape in shape_trees[0]:
        if shape.tag != _PPTX_SHAPE:
            continue
        text_bodies = [item for item in shape if item.tag == _PPTX_TEXT_BODY]
        if len(text_bodies) > 1:
            raise _DocumentRejected()
        if text_bodies:
            paragraphs.extend(
                item for item in text_bodies[0] if item.tag == _DRAWING_PARAGRAPH
            )
    return tuple(paragraphs)


def _pptx_paragraph_text(paragraph: ET.Element) -> str:
    """只从 ``a:p/a:r/a:t`` 精确路径拼接 PPTX 段落文本。

    输入参数：
        paragraph：受支持 shape text body 中的精确 DrawingML 段落。
    输出返回值：
        所有直接 run 下直接 ``a:t`` 文本的顺序拼接值。
    """

    return "".join(
        text.text or ""
        for run in paragraph
        if run.tag == _DRAWING_RUN
        for text in run
        if text.tag == _DRAWING_TEXT
    )


def _open_validated_ooxml(
    payload: bytes,
    *,
    kind: str,
) -> zipfile.ZipFile:
    """打开并预检一份有界、无宏、无外链的 OOXML 容器。

    输入参数：
        payload：已由 capture 核验的不可变 bytes。
        kind：``docx``、``xlsx`` 或 ``pptx``。
    输出返回值：
        调用方负责关闭的已完成 CRC、路径、资源和主类型预检的 ZipFile。
    异常：
        _DocumentRejected：输入、ZIP、XML、宏或外链任一不可信。
    """

    identity = _OOXML_IDENTITIES.get(kind)
    if not isinstance(payload, bytes) or not payload or identity is None:
        raise _DocumentRejected()
    stream = BytesIO(payload)
    try:
        archive = zipfile.ZipFile(stream)
        infos = archive.infolist()
        if not 0 < len(infos) <= _MAX_ARCHIVE_MEMBERS:
            raise _DocumentRejected()
        observed_names: set[str] = set()
        portable_names: set[str] = set()
        expanded_bytes = 0
        for info in infos:
            name = info.filename
            normalized_name = unicodedata.normalize("NFC", name)
            portable_name = normalized_name.casefold()
            if (
                not name
                or name != normalized_name
                or name.startswith("/")
                or name.endswith("/")
                or "\\" in name
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or name in observed_names
                or portable_name in portable_names
                or info.flag_bits & 0x1
                or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or not 0 <= info.file_size <= _MAX_ARCHIVE_MEMBER_BYTES
            ):
                raise _DocumentRejected()
            observed_names.add(name)
            portable_names.add(portable_name)
            expanded_bytes += info.file_size
            if expanded_bytes > _MAX_ARCHIVE_EXPANDED_BYTES:
                raise _DocumentRejected()
            if info.file_size and (
                info.compress_size <= 0
                or info.file_size > info.compress_size * _MAX_ARCHIVE_COMPRESSION_RATIO
            ):
                raise _DocumentRejected()
            lowered_name = name.casefold()
            if lowered_name.endswith(".bin") or "/embeddings/" in lowered_name:
                raise _DocumentRejected()
        required_member, expected_main_type = identity
        if (
            "[Content_Types].xml" not in observed_names
            or required_member not in observed_names
            or archive.testzip() is not None
        ):
            raise _DocumentRejected()
        for info in infos:
            if info.filename.casefold().endswith((".xml", ".rels")):
                _validate_xml_bytes(_read_zip_member(archive, info.filename))
        content_types = _read_xml_root(archive, "[Content_Types].xml")
        if content_types.tag != _CONTENT_TYPES_ROOT:
            raise _DocumentRejected()
        main_types = [
            item.attrib.get("ContentType")
            for item in content_types
            if item.tag == _CONTENT_TYPE_OVERRIDE
            and item.attrib.get("PartName") == f"/{required_member}"
        ]
        all_types = tuple(item.attrib.get("ContentType", "") for item in content_types)
        if main_types != [expected_main_type] or any(
            "macroenabled" in value.casefold() for value in all_types
        ):
            raise _DocumentRejected()
        package_main_targets = _relationship_targets(
            archive,
            "_rels/.rels",
            source_member="",
            required_type=_OFFICE_DOCUMENT_RELATIONSHIP_TYPE,
        )
        if tuple(package_main_targets.values()) != (required_member,):
            raise _DocumentRejected()
        for info in infos:
            if not info.filename.casefold().endswith(".rels"):
                continue
            relationships = _read_xml_root(archive, info.filename)
            if relationships.tag != _RELATIONSHIPS_ROOT or any(
                item.tag != _RELATIONSHIP_ELEMENT for item in relationships
            ):
                raise _DocumentRejected()
            if any(
                item.attrib.get("TargetMode", "").casefold() == "external"
                for item in relationships
            ):
                raise _DocumentRejected()
        return archive
    except _DocumentRejected:
        try:
            archive.close()
        except (NameError, OSError):
            pass
        stream.close()
        raise
    except (
        ET.ParseError,
        KeyError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ):
        try:
            archive.close()
        except (NameError, OSError):
            pass
        stream.close()
        raise _DocumentRejected() from None


def _read_zip_member(archive: zipfile.ZipFile, member: str) -> bytes:
    """从已预检 ZIP 读取一个仍受单成员上限约束的 member。

    输入参数：
        archive：已通过 ``_open_validated_ooxml`` 的容器。
        member：容器内精确成员名。
    输出返回值：
        长度与 central directory 声明一致的不可变字节。
    异常：
        _DocumentRejected：成员缺失、过大或读取长度漂移。
    """

    try:
        info = archive.getinfo(member)
        if not 0 <= info.file_size <= _MAX_ARCHIVE_MEMBER_BYTES:
            raise _DocumentRejected()
        with archive.open(info) as source:
            content = source.read(_MAX_ARCHIVE_MEMBER_BYTES + 1)
        if len(content) != info.file_size:
            raise _DocumentRejected()
        return content
    except _DocumentRejected:
        raise
    except (KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        raise _DocumentRejected() from None


def _require_part_content_type(
    archive: zipfile.ZipFile,
    member: str,
    *,
    expected_content_type: str,
) -> None:
    """核验实际解引用 part 的唯一精确 ContentType Override。

    输入参数：
        archive：已通过 package 预检的 OOXML 容器。
        member：relationship 已解析的规范 package member 路径。
        expected_content_type：该解引用角色要求的精确非宏 MIME。
    输出返回值：
        无；仅当 ``[Content_Types].xml`` 对该 part 恰有一个精确
        Override 且类型相等时返回。
    异常：
        _DocumentRejected：根、Override QName、数量或类型不匹配。
    """

    content_types = _read_xml_root(archive, "[Content_Types].xml")
    if content_types.tag != _CONTENT_TYPES_ROOT:
        raise _DocumentRejected()
    matches = [
        item
        for item in content_types
        if item.tag == _CONTENT_TYPE_OVERRIDE
        and item.attrib.get("PartName") == f"/{member}"
    ]
    if (
        len(matches) != 1
        or matches[0].attrib.get("ContentType") != expected_content_type
    ):
        raise _DocumentRejected()


def _validate_xml_bytes(content: bytes) -> None:
    """以流式计数拒绝 XML 实体、深度、元素、属性或文本资源攻击。

    输入参数：
        content：已受 ZIP 单成员限制的 XML bytes。
    输出返回值：
        无；文档在全部结构预算内时返回。
    异常：
        _DocumentRejected：DTD/entity、解析错误或任一资源预算超限。
    """

    lowered = content.replace(b"\x00", b"").lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise _DocumentRejected()
    parser = ET.XMLPullParser(events=("start", "end"))
    depth = 0
    element_count = 0
    text_bytes = 0
    try:
        for offset in range(0, len(content), 65_536):
            parser.feed(content[offset : offset + 65_536])
            for event, element in parser.read_events():
                if event == "start":
                    depth += 1
                    element_count += 1
                    if (
                        depth > _MAX_XML_DEPTH
                        or element_count > _MAX_XML_ELEMENTS
                        or len(element.attrib) > _MAX_XML_ATTRIBUTES
                    ):
                        raise _DocumentRejected()
                else:
                    if element.text:
                        text_bytes += len(element.text.encode("utf-8", "strict"))
                    if element.tail:
                        text_bytes += len(element.tail.encode("utf-8", "strict"))
                    if text_bytes > _MAX_XML_TEXT_BYTES:
                        raise _DocumentRejected()
                    depth -= 1
                    if depth < 0:
                        raise _DocumentRejected()
                    element.clear()
        parser.close()
    except _DocumentRejected:
        raise
    except (ET.ParseError, UnicodeError, ValueError):
        raise _DocumentRejected() from None
    if depth != 0:
        raise _DocumentRejected()


def _read_xml_root(archive: zipfile.ZipFile, member: str) -> ET.Element:
    """读取并解析一个已经受预检预算约束的 XML member。

    输入参数：
        archive：已预检 OOXML 容器。
        member：所需 XML 的精确 package path。
    输出返回值：
        ElementTree 根元素。
    异常：
        _DocumentRejected：成员缺失或 XML 无效。
    """

    content = _read_zip_member(archive, member)
    try:
        root = ET.fromstring(content)
        namespace_prefixes = _collect_namespace_prefixes(content)
        _resolve_mce_children(root, namespace_prefixes)
        return root
    except (ET.ParseError, UnicodeError, ValueError):
        raise _DocumentRejected() from None


def _collect_namespace_prefixes(content: bytes) -> dict[str, str]:
    """收集 XML 中唯一、无歧义的 prefix 到 namespace 映射。

    输入参数：
        content：已通过 XML 资源预检的完整 member 字节。
    输出返回值：
        可用于 MCE ``Requires`` 判定的前缀映射。
    异常：
        _DocumentRejected：同一前缀在文档中被重绑定到不同 namespace。

    该实现保守拒绝局部重绑定，避免在 ElementTree 不保留
    词法 namespace scope 时对 ``Requires`` 做模糊推断。
    """

    prefixes: dict[str, str] = {}
    try:
        for _, binding in ET.iterparse(BytesIO(content), events=("start-ns",)):
            prefix, namespace = binding
            normalized_prefix = prefix or ""
            previous = prefixes.get(normalized_prefix)
            if previous is not None and previous != namespace:
                raise _DocumentRejected()
            prefixes[normalized_prefix] = namespace
    except _DocumentRejected:
        raise
    except (ET.ParseError, UnicodeError, ValueError):
        raise _DocumentRejected() from None
    return prefixes


def _resolve_mce_children(
    parent: ET.Element,
    namespace_prefixes: dict[str, str],
) -> None:
    """就地把 MCE AlternateContent 替换为唯一选中分支。

    输入参数：
        parent：当前已解析 XML 父元素。
        namespace_prefixes：文档无歧义前缀映射。
    输出返回值：
        无；只保留首个所有 ``Requires`` namespace 均受支持的
        Choice，否则保留 Fallback，两者均不存在时删除该节点。
    异常：
        _DocumentRejected：AlternateContent 子结构或 ``Requires`` 无效。
    """

    index = 0
    while index < len(parent):
        child = parent[index]
        if child.tag != _MCE_ALTERNATE_CONTENT:
            _resolve_mce_children(child, namespace_prefixes)
            index += 1
            continue
        selected = _select_mce_branch(child, namespace_prefixes)
        replacements = list(selected) if selected is not None else []
        parent.remove(child)
        for offset, replacement in enumerate(replacements):
            parent.insert(index + offset, replacement)
            _resolve_mce_children(replacement, namespace_prefixes)
        index += len(replacements)


def _select_mce_branch(
    alternate_content: ET.Element,
    namespace_prefixes: dict[str, str],
) -> ET.Element | None:
    """根据 MCE ``Requires`` 选择一个 Choice 或 Fallback。

    输入参数：
        alternate_content：精确 MCE AlternateContent 元素。
        namespace_prefixes：文档无歧义前缀映射。
    输出返回值：
        首个可理解 Choice，否则唯一 Fallback，无 fallback 时为 ``None``。
    异常：
        _DocumentRejected：标签、顺序、Fallback 数量、Requires 或前缀无效。
    """

    if alternate_content.tag != _MCE_ALTERNATE_CONTENT:
        raise _DocumentRejected()
    choices: list[ET.Element] = []
    fallback: ET.Element | None = None
    seen_fallback = False
    for branch in alternate_content:
        if branch.tag == _MCE_CHOICE and not seen_fallback:
            choices.append(branch)
            continue
        if branch.tag == _MCE_FALLBACK and not seen_fallback:
            fallback = branch
            seen_fallback = True
            continue
        raise _DocumentRejected()
    if not choices:
        raise _DocumentRejected()
    for choice in choices:
        raw_requires = choice.attrib.get("Requires")
        if not isinstance(raw_requires, str):
            raise _DocumentRejected()
        prefixes = raw_requires.split()
        if (
            not prefixes
            or len(prefixes) != len(set(prefixes))
            or any(_MCE_PREFIX_PATTERN.fullmatch(prefix) is None for prefix in prefixes)
            or any(prefix not in namespace_prefixes for prefix in prefixes)
        ):
            raise _DocumentRejected()
        if all(
            namespace_prefixes[prefix] in _SUPPORTED_MCE_NAMESPACES
            for prefix in prefixes
        ):
            return choice
    return fallback


def _relationship_targets(
    archive: zipfile.ZipFile,
    member: str,
    *,
    source_member: str,
    required_type: str,
) -> dict[str, str]:
    """把 OOXML relationship ID 映射为安全 package member。

    输入参数：
        archive/member：预检容器及关系 XML 路径。
        source_member：关系所属源部件，用于解析相对 Target。
        required_type：本次实际解引用 part 要求的精确 relationship Type。
    输出返回值：
        ID 到无点段、存在于容器中的规范成员名映射。
    异常：
        _DocumentRejected：关系重复、外链、越界或目标不存在。
    """

    root = _read_xml_root(archive, member)
    if root.tag != _RELATIONSHIPS_ROOT or any(
        item.tag != _RELATIONSHIP_ELEMENT for item in root
    ):
        raise _DocumentRejected()
    names = set(archive.namelist())
    targets: dict[str, str] = {}
    observed_ids: set[str] = set()
    for item in root:
        relationship_id = item.attrib.get("Id")
        target = item.attrib.get("Target")
        relationship_type = item.attrib.get("Type")
        target_mode = item.attrib.get("TargetMode")
        if (
            not relationship_id
            or relationship_id in observed_ids
            or not target
            or not relationship_type
            or target_mode not in {None, "Internal"}
            or "\\" in target
        ):
            raise _DocumentRejected()
        observed_ids.add(relationship_id)
        if target.startswith("/"):
            resolved = target.lstrip("/")
        else:
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(source_member), target)
            )
        if (
            not resolved
            or resolved.startswith("../")
            or resolved == ".."
            or resolved not in names
        ):
            raise _DocumentRejected()
        if relationship_type == required_type:
            targets[relationship_id] = resolved
    return targets


def _read_shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    """读取 XLSX sharedStrings 的有序字符串闭集。

    输入参数：
        archive：已预检 XLSX 容器。
    输出返回值：
        每个 ``si`` 内全部 rich-text ``t`` 拼接后的 tuple；成员不存在
        时返回空 tuple。
    """

    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = _read_xml_root(archive, "xl/sharedStrings.xml")
    if root.tag != _XLSX_SHARED_STRING_TABLE:
        raise _DocumentRejected()
    return tuple(
        _spreadsheet_text_item(item)
        for item in root
        if item.tag == _XLSX_SHARED_STRING_ITEM
    )


def _xlsx_cell_value(
    cell: ET.Element,
    shared_strings: tuple[str, ...],
) -> str | None:
    """把 XLSX cell 转为固定文本或数值规范形式。

    输入参数：
        cell：worksheet ``c`` 元素。
        shared_strings：已解析的共享字符串闭集。
    输出返回值：
        空 cell 为 ``None``，其余为无指数、无多余零的稳定字符串。
    异常：
        _DocumentRejected：类型、索引、布尔或数值不受支持。
    """

    cell_type = cell.attrib.get("t", "n")
    values = [item for item in cell if item.tag == _XLSX_VALUE]
    if cell_type == "inlineStr":
        inline = [item for item in cell if item.tag == _XLSX_INLINE_STRING]
        if len(inline) != 1:
            raise _DocumentRejected()
        return _spreadsheet_text_item(inline[0])
    if not values:
        return None
    if len(values) != 1 or values[0].text is None:
        raise _DocumentRejected()
    raw = values[0].text
    if cell_type == "s":
        try:
            index = int(raw)
            return shared_strings[index]
        except (IndexError, TypeError, ValueError):
            raise _DocumentRejected() from None
    if cell_type == "str":
        return raw
    if cell_type != "n":
        raise _DocumentRejected()
    try:
        number = Decimal(raw)
    except InvalidOperation:
        raise _DocumentRejected() from None
    if not number.is_finite():
        raise _DocumentRejected()
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _spreadsheet_text_item(item: ET.Element) -> str:
    """从 ``si`` 或 ``is`` 的受支持直接路径读取文本。

    输入参数：
        item：SpreadsheetML shared-string item 或 inline-string 元素。
    输出返回值：
        单个直接 ``t``，或所有直接 ``r/t`` 按顺序拼接的文本。
    异常：
        _DocumentRejected：文本路径缺失、重复或混用。
    """

    direct_texts = [child for child in item if child.tag == _XLSX_TEXT]
    runs = [child for child in item if child.tag == _XLSX_RICH_TEXT_RUN]
    if direct_texts:
        if len(direct_texts) != 1 or runs:
            raise _DocumentRejected()
        return direct_texts[0].text or ""
    if not runs:
        raise _DocumentRejected()
    fragments: list[str] = []
    for run in runs:
        texts = [child for child in run if child.tag == _XLSX_TEXT]
        if len(texts) != 1:
            raise _DocumentRejected()
        fragments.append(texts[0].text or "")
    return "".join(fragments)


def _monthly_row_map(
    spreadsheet: _SpreadsheetFacts | None,
) -> dict[str, tuple[str, ...]] | None:
    """把内部 XLSX 行投影为月份键映射。

    输入参数：
        spreadsheet：可选受控 XLSX 投影。
    输出返回值：
        十二个月份小写键到完整六列行；缺失或行闭集异常时为 ``None``。
    """

    if spreadsheet is None or len(spreadsheet.rows) != 13:
        return None
    month_rows = spreadsheet.rows[:12]
    result = {row[0].lower(): row for row in month_rows if len(row) == 6}
    if len(result) != 12:
        return None
    return result


def _expected_presentation_projection(
    spreadsheet: _SpreadsheetFacts | None,
) -> tuple[tuple[str, ...], ...] | None:
    """从同一 XLSX 事实构造 PPT 非目标文本的期望投影。

    输入参数：
        spreadsheet：受控 XLSX 行事实。
    输出返回值：
        四页 PPT 的规范文本 tuple；XLSX 不完整时为 ``None``。
    """

    monthly = _monthly_row_map(spreadsheet)
    if monthly is None or spreadsheet is None:
        return None
    total = spreadsheet.rows[12]
    january = monthly["january"]
    march = monthly["march"]
    return (
        ("Mcdonald’s annual report", "2026"),
        (
            "Annual total",
            f"Revenue (USD):{total[1]}$",
            f"Profit (USD):{total[3]}$",
            f"Customers:{total[4]}",
        ),
        (
            "Jan data",
            f"Revenue (USD):{january[1]}$",
            f"Profit (USD):{january[3]}$",
            "Customers:<TARGET>",
        ),
        (
            "March data",
            f"Revenue (USD):{march[1]}$",
            f"Profit (USD):{march[3]}$",
            f"Avg. Transaction (USD):{march[5]}",
        ),
    )


def _relationship_id(element: ET.Element) -> str | None:
    """读取 OOXML officeDocument relationship namespace 的 ID。

    输入参数：
        element：候选 XML 元素。
    输出返回值：
        精确 ``r:id`` 属性值；缺失时为 ``None``，不会与普通 ``id``
        或 ``sheetId`` 混淆。
    """

    value = element.attrib.get(_RELATIONSHIP_ID_ATTRIBUTE)
    return value if isinstance(value, str) and value else None


def _normalize_text(value: str) -> str:
    """压缩 Office 文本的空白差异但保留事实字符。

    输入参数：
        value：从相邻 OOXML text run 拼接出的字符串。
    输出返回值：
        首尾去空白、内部空白折叠为单空格的文本。
    """

    return re.sub(r"\s+", " ", value).strip()


def _normalize_presentation_text(value: str) -> str:
    """归一化 PPT 段落空白及冒号两侧无语义排版差异。

    输入参数：
        value：单个 presentation paragraph 的拼接文本。
    输出返回值：
        空白折叠且冒号两侧空格移除的稳定文本。
    """

    return re.sub(r"\s*:\s*", ":", _normalize_text(value))


def _parse_grouped_integer(value: str) -> int:
    """解析无前导符号的美式分组整数。

    输入参数：
        value：正则已经限定的十进制或三位逗号分组文本。
    输出返回值：
        非负且不超过十亿的整数。
    异常：
        _DocumentRejected：解析失败或越界。
    """

    try:
        parsed = int(value.replace(",", ""))
    except (AttributeError, ValueError):
        raise _DocumentRejected() from None
    if not 0 <= parsed <= 1_000_000_000:
        raise _DocumentRejected()
    return parsed


__all__ = ["build_cross_document_observation"]
