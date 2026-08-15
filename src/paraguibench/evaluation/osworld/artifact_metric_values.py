"""OSWorld artifact metric 的强类型、无 I/O 值语义。

本模块不读取文件、不解压、不下载 gold，也不导入 Office/PDF
解析器。受信 evidence adapter 必须先把 Agent artifact 与已验证 gold
投影为本模块的不可变值对象；类型、字段或固定 options 不完整时
一律失败关闭。
"""

from __future__ import annotations

from collections.abc import Mapping
from collections import Counter
from array import array
from dataclasses import dataclass
import math
import re


class ArtifactMetricValueError(ValueError):
    """表示强类型 actual、gold 或 options 无法安全评价。

    输入参数：
        role：``observation``、``gold`` 或 ``options`` 之一。
    输出返回值：
        不含 artifact 原值的内部异常，由公共 registry 转换为
        稳定错误码。
    """

    def __init__(self, role: str) -> None:
        """构造不回显输入值的分类异常。

        输入参数：
            role：出错边界的固定身份。
        输出返回值：
            无；初始化当前异常。
        """

        if role not in {"observation", "gold", "options"}:
            role = "observation"
        super().__init__("artifact typed metric schema 无效")
        self.role = role


@dataclass(frozen=True, slots=True, repr=False)
class SpreadsheetCellValue:
    """保存 fuzzy-sheet 所需的单元格字符串投影。

    输入参数：
        coordinate：规范化的大写 A1 坐标。
        value：按源 ``str(read_cell_value(...))`` 生成的字符串。
    输出返回值：
        不可变单元格投影；repr 不回显坐标或内容。
    """

    coordinate: str
    value: str

    def __repr__(self) -> str:
        """生成不含单元格原值的安全文本。

        输入参数：
            无。
        输出返回值：
            固定类型标识。
        """

        return "SpreadsheetCellValue()"


@dataclass(frozen=True, slots=True, repr=False)
class SpreadsheetSheetValue:
    """保存单个 sheet 的 DataFrame 语义投影。

    输入参数：
        name：sheet 名称。
        columns：按顺序保存的列标签。
        rows：按顺序保存的数据行，每行宽度必须与 columns 相同。
        cells：仅 fuzzy-sheet 使用的坐标到字符串投影。
        printed_text：可选 CSV sidecar 文本，仅 sheet-print contract 使用。
    输出返回值：
        不可变的表格值；实际 schema 在具体 contract 执行时校验。
    """

    name: str
    columns: tuple[object, ...] = ()
    rows: tuple[tuple[object, ...], ...] = ()
    cells: tuple[SpreadsheetCellValue, ...] = ()
    printed_text: str | None = None

    def __repr__(self) -> str:
        """返回不包含单元格、列名或 sheet 名的安全文本。

        输入参数：
            无。
        输出返回值：
            仅含行列计数的调试字符串。
        """

        return (
            "SpreadsheetSheetValue("
            f"column_count={len(self.columns)}, row_count={len(self.rows)}, "
            f"cell_count={len(self.cells)}, has_print={self.printed_text is not None})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class SpreadsheetArtifactValue:
    """保存 workbook 中按顺序投影的 sheet 闭集。

    输入参数：
        sheets：按源 workbook 顺序保存的 sheet tuple。
    输出返回值：
        不可变 workbook 值，不携带路径或原文件 bytes。
    """

    sheets: tuple[SpreadsheetSheetValue, ...]

    def __repr__(self) -> str:
        """返回不泄露 sheet 名称或内容的安全文本。

        输入参数：
            无。
        输出返回值：
            仅含 sheet 计数的调试字符串。
        """

        return f"SpreadsheetArtifactValue(sheet_count={len(self.sheets)})"


@dataclass(frozen=True, slots=True, repr=False)
class DocumentParagraphValue:
    """保存 Office 文档的有序段落文本投影。

    输入参数：
        paragraphs：按文档顺序保存的段落原文 tuple。
    输出返回值：
        不含路径、OOXML 或格式对象的不可变文本投影。
    """

    paragraphs: tuple[str, ...]

    def __repr__(self) -> str:
        """生成不回显段落内容的安全调试文本。

        输入参数：
            无。
        输出返回值：
            仅含段落计数的字符串。
        """

        return f"DocumentParagraphValue(paragraph_count={len(self.paragraphs)})"


@dataclass(frozen=True, slots=True, repr=False)
class PresentationRunValue:
    """保存 speaker-notes contract 启用的单个 PPT 文本 run 格式。

    输入参数：
        font_name/font_size/bold/italic/color_rgb/underline/strike：源函数
            在 ``examine_bullets=false`` 时仍逐一比较的字体属性。
    输出返回值：
        不含 XML 或关系对象的不可变 run 投影。
    """

    font_name: str | None
    font_size: int | None
    bold: bool | None
    italic: bool | None
    color_rgb: str | None
    underline: str | int | bool | None
    strike: str

    def __repr__(self) -> str:
        """生成不回显字体名或其他文档属性的安全文本。

        输入参数：
            无。
        输出返回值：
            固定类型标识。
        """

        return "PresentationRunValue()"


@dataclass(frozen=True, slots=True, repr=False)
class PresentationParagraphValue:
    """保存 PPT 文本段的启用比较字段。

    输入参数：
        text/alignment/level：段落文本、对齐投影与缩进级别。
        runs：按源顺序保存的 run tuple。
    输出返回值：
        不可变段落投影。
    """

    text: str
    alignment: str | int | None
    level: int
    runs: tuple[PresentationRunValue, ...]

    def __repr__(self) -> str:
        """生成不回显段落文本的安全调试文本。

        输入参数：
            无。
        输出返回值：
            仅含 run 计数的字符串。
        """

        return f"PresentationParagraphValue(run_count={len(self.runs)})"


@dataclass(frozen=True, slots=True, repr=False)
class PresentationShapeValue:
    """保存 PPT shape 的文本语义，故意不含几何与 bullet。

    输入参数：
        text：shape 有文本接口时的文本，无文本接口时为 ``None``。
        paragraphs：有文本 shape 的有序段落投影。
    输出返回值：
        不可变 shape 投影；``examine_shape=false`` 因而不携带位置。
    """

    text: str | None
    paragraphs: tuple[PresentationParagraphValue, ...] = ()

    def __repr__(self) -> str:
        """生成不回显 shape 文本的安全调试文本。

        输入参数：
            无。
        输出返回值：
            仅含段落计数与是否有文本的字符串。
        """

        return (
            "PresentationShapeValue("
            f"paragraph_count={len(self.paragraphs)}, has_text={self.text is not None})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PresentationSlideValue:
    """保存单页 PPT 的背景、备注和 shape 顺序。

    输入参数：
        background_color：背景 RGB 投影，没有显式值时为 ``None``。
        notes_text：备注文本。
        shapes：按源顺序保存的 shape tuple。
    输出返回值：
        不可变 slide 投影。
    """

    background_color: str | None
    notes_text: str
    shapes: tuple[PresentationShapeValue, ...]

    def __repr__(self) -> str:
        """生成不回显备注、背景或 shape 内容的安全文本。

        输入参数：
            无。
        输出返回值：
            仅含 shape 计数的字符串。
        """

        return f"PresentationSlideValue(shape_count={len(self.shapes)})"


@dataclass(frozen=True, slots=True, repr=False)
class PresentationArtifactValue:
    """保存按顺序投影的 PPT slide 闭集。

    输入参数：
        slides：强类型 slide tuple。
    输出返回值：
        不含路径、原文本回显或 OOXML 的不可变 PPT 值。
    """

    slides: tuple[PresentationSlideValue, ...]

    def __repr__(self) -> str:
        """生成不泄露 PPT 内容的安全调试文本。

        输入参数：
            无。
        输出返回值：
            仅含 slide 计数的字符串。
        """

        return f"PresentationArtifactValue(slide_count={len(self.slides)})"


@dataclass(frozen=True, slots=True, repr=False)
class PDFTextValue:
    """保存 PDF 按页拼接并 ``strip`` 后的文本投影。

    输入参数：
        text：受信 PDF parser 从所有页顺序提取的字符串。
    输出返回值：
        不含 PDF bytes、路径或 parser 对象的不可变文本值。
    """

    text: str

    def __repr__(self) -> str:
        """生成不回显 PDF 文本的安全调试文本。

        输入参数：
            无。
        输出返回值：
            固定类型标识。
        """

        return "PDFTextValue()"


@dataclass(frozen=True, slots=True, repr=False)
class PDFArchiveMemberValue:
    """保存 PDF archive 的单个顶层成员投影。

    输入参数：
        name：无路径分隔符的顶层成员名。
        document：该成员已提取的 PDF 文本值。
    输出返回值：
        不含 archive/PDF bytes 或 host 路径的不可变成员值。
    """

    name: str
    document: PDFTextValue

    def __repr__(self) -> str:
        """生成不回显 archive 成员名或文本的安全文本。

        输入参数：
            无。
        输出返回值：
            固定类型标识。
        """

        return "PDFArchiveMemberValue()"


@dataclass(frozen=True, slots=True, repr=False)
class PDFArchiveValue:
    """保存已安全解包的顶层 PDF 成员闭集。

    输入参数：
        members：成员名唯一的 ``PDFArchiveMemberValue`` tuple。
    输出返回值：
        不可变 archive 投影；成员顺序不影响 metric。
    """

    members: tuple[PDFArchiveMemberValue, ...]

    def __repr__(self) -> str:
        """生成不回显 archive 成员身份的安全文本。

        输入参数：
            无。
        输出返回值：
            仅含成员计数的字符串。
        """

        return f"PDFArchiveValue(member_count={len(self.members)})"


@dataclass(frozen=True, slots=True, repr=False)
class NormalizedRGBImageValue:
    """保存已按源 min-size/Lanczos 规则联合归一化的图像。

    输入参数：
        width/height：actual 与 gold 共用的归一化像素尺寸。
        rgb_pixels：按行主序保存的 RGB uint8 bytes。
        hsv_pixels：对同一归一化图像按 Pillow HSV 转换得到的 bytes。
    输出返回值：
        不含编码图像、路径或 parser 对象的不可变像素投影。

    注意：
        类型名中的 ``Normalized`` 是受信 evidence adapter 的强制协议：
        adapter 先取两图最小宽高并用 Lanczos 同时缩放，再注入
        actual/gold。本 pure 层不解码或重采样不可信 bytes。
    """

    width: int
    height: int
    rgb_pixels: bytes
    hsv_pixels: bytes

    def __repr__(self) -> str:
        """生成不回显像素、尺寸或文件信息的安全文本。

        输入参数：
            无。
        输出返回值：
            固定类型标识。
        """

        return "NormalizedRGBImageValue()"


_FIRST_SHEET_OPTIONS = {
    "rules": [{"type": "sheet_data", "sheet_idx0": 0, "sheet_idx1": "EI0"}]
}
_NAMED_UNSEEN_MOVIES_OPTIONS = {
    "rules": [
        {
            "type": "sheet_data",
            "sheet_idx0": "RNunseen_movies",
            "sheet_idx1": "ENunseen_movies",
        }
    ]
}
_SPEAKER_NOTES_OPTIONS = {
    "examine_shape": False,
    "examine_bullets": False,
}
_RESTAURANT_FUZZY_OPTIONS = {
    "rules": [
        {
            "type": "sheet_fuzzy",
            "sheet_idx0": "RNSheet1",
            "sheet_idx1": "ENSheet1",
            "rules": [
                {"range": ["A1:A6", "D1:D6"], "type": "exact_match"},
                {
                    "range": ["B1:B6"],
                    "type": "fuzzy_match",
                    "threshold": 85,
                    "normalization": [["Rd", "Road"], ["St", "Street"]],
                    "ignore_case": True,
                },
                {
                    "range": ["C1:C6"],
                    "type": "includes",
                    "trim_leadings": "+ ",
                    "ignore_chars": " ()-",
                },
            ],
        }
    ]
}
_SHEET_PRINT_OPTIONS = {
    "rules": [
        {
            "type": "sheet_print",
            "sheet_idx0": "RNSheet1",
            "sheet_idx1": "ENSheet1",
        }
    ]
}
_APA_REFERENCE_OPTIONS = {
    "content_only": True,
    "reference_base_result": 0.6,
}
_REFERENCE_STOP_TOKENS = frozenset(
    {
        "and",
        "the",
        "for",
        "from",
        "with",
        "this",
        "that",
        "a",
        "an",
        "retrieved",
        "accessed",
        "https",
        "http",
        "www",
    }
)
_IMAGE_SCORE_OPTIONS = {"score_threshold": 0.90}
_MAX_TYPED_IMAGE_PIXELS = 2_500_000


def evaluate_first_sheet_table(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现固定 first-sheet ``compare_table`` 的值比较。

    输入参数：
        actual：Agent workbook 的 ``SpreadsheetArtifactValue``。
        gold：已验证 gold workbook 的同类型投影。
        options：必须精确等于 evidence spec 固定的单条
            ``sheet_data(0, EI0)`` 规则。
    输出返回值：
        首个 sheet 的列、行与四位小数归一化值均相同时为
        ``1.0``，否则为 ``0.0``。
    异常：
        ArtifactMetricValueError：任一强类型边界或 options 不完整。
    """

    if not _strict_json_equal(options, _FIRST_SHEET_OPTIONS):
        raise ArtifactMetricValueError("options")
    actual_book = _validated_workbook(actual, role="observation")
    gold_book = _validated_workbook(gold, role="gold")
    if not gold_book.sheets:
        raise ArtifactMetricValueError("gold")
    if not actual_book.sheets:
        return 0.0
    return float(
        _tables_equal(
            actual_book.sheets[0],
            gold_book.sheets[0],
            precision=4,
        )
    )


def evaluate_named_unseen_movies_table(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现固定 ``unseen_movies`` 名称 sheet 比较。

    输入参数：
        actual/gold：Agent 与已验证 gold workbook 的强类型值。
        options：必须精确为 ``RNunseen_movies`` 对
            ``ENunseen_movies`` 的单条 ``sheet_data`` 规则。
    输出返回值：
        两侧目标 sheet 的列、形状和四位小数值一致时为
        ``1.0``；Agent 缺页或内容不同时为 ``0.0``。
    异常：
        ArtifactMetricValueError：options/schema 无效或 gold 缺少目标页。
    """

    if not _strict_json_equal(options, _NAMED_UNSEEN_MOVIES_OPTIONS):
        raise ArtifactMetricValueError("options")
    actual_book = _validated_workbook(actual, role="observation")
    gold_book = _validated_workbook(gold, role="gold")
    actual_sheet = _find_sheet(actual_book, "unseen_movies")
    gold_sheet = _find_sheet(gold_book, "unseen_movies")
    if gold_sheet is None:
        raise ArtifactMetricValueError("gold")
    if actual_sheet is None:
        return 0.0
    return float(_tables_equal(actual_sheet, gold_sheet, precision=4))


def evaluate_docx_content(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现 ``compare_docx_files`` 的默认段落内容语义。

    输入参数：
        actual/gold：Agent 与已验证 gold 的有序段落投影。
        options：evidence spec 固定的空映射；源默认因而为
            ``ignore_blanks=true``、``ignore_case=false``、
            ``ignore_order=false`` 且 ``content_only=false``。
    输出返回值：
        段落以换行连接并将连续 Unicode 空白折叠后精确相等
        时为 ``1.0``，否则为 ``0.0``。
    异常：
        ArtifactMetricValueError：值类型或固定 options 不完整。
    """

    if not _strict_json_equal(options, {}):
        raise ArtifactMetricValueError("options")
    actual_document = _validated_document(actual, role="observation")
    gold_document = _validated_document(gold, role="gold")
    return float(
        _normalize_document_whitespace(actual_document.paragraphs)
        == _normalize_document_whitespace(gold_document.paragraphs)
    )


def evaluate_speaker_notes_presentation(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现 speaker-notes PPTX contract 中所有仍启用的比较维度。

    输入参数：
        actual/gold：Agent 与已验证 gold 的强类型 PPT 投影。
        options：必须精确固定 ``examine_shape=false`` 和
            ``examine_bullets=false``；其他源默认维度仍启用。
    输出返回值：
        slide/shape 数、背景、备注、文本、段落与字体属性均按源
        语义一致时为 ``1.0``，否则为 ``0.0``。
    异常：
        ArtifactMetricValueError：options 或任一投影 schema 无效。
    """

    if not _strict_json_equal(options, _SPEAKER_NOTES_OPTIONS):
        raise ArtifactMetricValueError("options")
    actual_presentation = _validated_presentation(actual, role="observation")
    gold_presentation = _validated_presentation(gold, role="gold")
    if len(actual_presentation.slides) != len(gold_presentation.slides):
        return 0.0
    for actual_slide, gold_slide in zip(
        actual_presentation.slides,
        gold_presentation.slides,
        strict=True,
    ):
        if (
            len(actual_slide.shapes) != len(gold_slide.shapes)
            or actual_slide.background_color != gold_slide.background_color
            or actual_slide.notes_text.strip() != gold_slide.notes_text.strip()
        ):
            return 0.0
        if not _speaker_shapes_match(actual_slide.shapes, gold_slide.shapes):
            return 0.0
    return 1.0


def evaluate_restaurant_fuzzy_sheet(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现餐厅联系表的固定 ``sheet_fuzzy`` 规则。

    输入参数：
        actual/gold：含 ``Sheet1`` 坐标字符串投影的强类型 workbook。
        options：必须与 evidence spec 中 A/D exact、B fuzzy 和 C includes
            的完整 JSON 规则精确相同。
    输出返回值：
        24 个固定单元格全部满足各自归一化与比较规则时为
        ``1.0``，Agent 缺格或任一内容不匹配时为 ``0.0``。
    异常：
        ArtifactMetricValueError：options/schema 无效或 gold 缺少必需单元格。
    """

    if not _strict_json_equal(options, _RESTAURANT_FUZZY_OPTIONS):
        raise ArtifactMetricValueError("options")
    actual_book = _validated_workbook(actual, role="observation")
    gold_book = _validated_workbook(gold, role="gold")
    actual_sheet = _find_sheet(actual_book, "Sheet1")
    gold_sheet = _find_sheet(gold_book, "Sheet1")
    if gold_sheet is None:
        raise ArtifactMetricValueError("gold")
    if actual_sheet is None:
        return 0.0
    actual_cells = {cell.coordinate: cell.value for cell in actual_sheet.cells}
    gold_cells = {cell.coordinate: cell.value for cell in gold_sheet.cells}
    required = tuple(
        f"{column}{row}" for column in ("A", "D", "B", "C") for row in range(1, 7)
    )
    if any(coordinate not in gold_cells for coordinate in required):
        raise ArtifactMetricValueError("gold")
    if any(coordinate not in actual_cells for coordinate in required):
        return 0.0

    for column in ("A", "D"):
        for row in range(1, 7):
            coordinate = f"{column}{row}"
            if actual_cells[coordinate] != gold_cells[coordinate]:
                return 0.0
    for row in range(1, 7):
        coordinate = f"B{row}"
        left = _normalize_fuzzy_address(actual_cells[coordinate])
        right = _normalize_fuzzy_address(gold_cells[coordinate])
        if _indel_ratio(left, right) < 85.0:
            return 0.0
    for row in range(1, 7):
        coordinate = f"C{row}"
        left = _normalize_phone(actual_cells[coordinate])
        right = _normalize_phone(gold_cells[coordinate])
        if right not in left:
            return 0.0
    return 1.0


def evaluate_problem_invoice_pdf(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现 ``compare_pdfs`` 对单个 problem invoice 的文本分数。

    输入参数：
        actual/gold：Agent 与已验证 gold PDF 的已提取文本投影。
        options：evidence spec 固定的空映射。
    输出返回值：
        一侧或双方文本非空时返回 normalized Indel ratio ``0..1``；
        双方均空按源语义返回 ``0.0``。
    异常：
        ArtifactMetricValueError：options 或强类型 PDF 投影无效。
    """

    if not _strict_json_equal(options, {}):
        raise ArtifactMetricValueError("options")
    actual_pdf = _validated_pdf_text(actual, role="observation")
    gold_pdf = _validated_pdf_text(gold, role="gold")
    return _pdf_text_score(actual_pdf, gold_pdf)


def evaluate_pdf_archive(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现 ``compare_archive(file_type='pdf')`` 的闭集与平均分数。

    输入参数：
        actual/gold：Agent 与已验证 gold 的强类型 PDF archive 投影。
        options：必须精确为 ``{"file_type": "pdf"}``。
    输出返回值：
        成员名闭集不同时为 ``0.0``；双方均空时为 ``1.0``；
        否则为同名 PDF 文本 ratio 的算术平均。
    异常：
        ArtifactMetricValueError：options 或 archive/PDF 强类型 schema 无效。
    """

    if not _strict_json_equal(options, {"file_type": "pdf"}):
        raise ArtifactMetricValueError("options")
    actual_archive = _validated_pdf_archive(actual, role="observation")
    gold_archive = _validated_pdf_archive(gold, role="gold")
    actual_members = {member.name: member.document for member in actual_archive.members}
    gold_members = {member.name: member.document for member in gold_archive.members}
    if set(actual_members) != set(gold_members):
        return 0.0
    if not gold_members:
        return 1.0
    return sum(
        _pdf_text_score(actual_members[name], gold_members[name])
        for name in sorted(gold_members)
    ) / len(gold_members)


def evaluate_sheet1_print(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现 GRF 与 supported-rate 两个 contract 的 Sheet1 CSV 比较。

    输入参数：
        actual/gold：含 ``Sheet1.printed_text`` 的强类型 workbook。
        options：必须精确为 RN/EN Sheet1 的单条 ``sheet_print`` 规则。
    输出返回值：
        CSV 行按源顺序完成 strip、反转与末尾空行剥离后精确相同
        时为 ``1.0``，否则为 ``0.0``。
    异常：
        ArtifactMetricValueError：options/schema 无效或 gold 缺少 Sheet1 sidecar。
    """

    if not _strict_json_equal(options, _SHEET_PRINT_OPTIONS):
        raise ArtifactMetricValueError("options")
    actual_book = _validated_workbook(actual, role="observation")
    gold_book = _validated_workbook(gold, role="gold")
    actual_sheet = _find_sheet(actual_book, "Sheet1")
    gold_sheet = _find_sheet(gold_book, "Sheet1")
    if gold_sheet is None or gold_sheet.printed_text is None:
        raise ArtifactMetricValueError("gold")
    if actual_sheet is None or actual_sheet.printed_text is None:
        return 0.0
    return float(
        _normalize_printed_lines(actual_sheet.printed_text)
        == _normalize_printed_lines(gold_sheet.printed_text)
    )


def evaluate_apa_references(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现修复后 ``compare_references(content_only=true)`` 语义。

    输入参数：
        actual/gold：Agent 与已验证 gold DOCX 的有序段落投影。
        options：必须精确为 ``content_only=true`` 与
            ``reference_base_result=0.6`` 的固定映射。
    输出返回值：
        双方均无精确 ``References`` 标记时为 ``1.0``；标记/条数不对齐
        时为 ``0.0``；否则为通过 token、来源与身份字段门禁的引用比例。
    异常：
        ArtifactMetricValueError：options 或段落强类型 schema 无效。
    """

    if not _strict_json_equal(options, _APA_REFERENCE_OPTIONS):
        raise ArtifactMetricValueError("options")
    actual_document = _validated_document(actual, role="observation")
    gold_document = _validated_document(gold, role="gold")
    actual_index = _first_reference_index(actual_document.paragraphs)
    gold_index = _first_reference_index(gold_document.paragraphs)
    if actual_index is None and gold_index is None:
        return 1.0
    if actual_index is None or gold_index is None:
        return 0.0
    actual_references = tuple(
        paragraph
        for paragraph in actual_document.paragraphs[actual_index + 1 :]
        if paragraph.strip()
    )
    gold_references = tuple(
        paragraph
        for paragraph in gold_document.paragraphs[gold_index + 1 :]
        if paragraph.strip()
    )
    if len(actual_references) != len(gold_references):
        return 0.0
    if not actual_references:
        return 0.0
    passed = sum(
        _reference_identity_matches(actual_reference, gold_reference)
        for actual_reference, gold_reference in zip(
            actual_references,
            gold_references,
            strict=True,
        )
    )
    return passed / len(actual_references)


def evaluate_slide_background_image(
    actual: object,
    gold: object,
    options: object,
) -> float:
    """复现修复后 ``compare_images`` 的 RGB SSIM 与 HSV 分布合取小值。

    输入参数：
        actual/gold：已由受信 adapter 联合重采样的强类型 RGB/HSV 像素。
        options：必须精确为 evidence spec 固定的
            ``{"score_threshold": 0.90}``；源 metric 本身不在内部二值化。
    输出返回值：
        最小边小于 7 时为 ``0.0``；否则为 RGB 三通道 7×7
        reflect-window SSIM 均值与 HSV 饱和度/色相直方图交集的较小值。
    异常：
        ArtifactMetricValueError：options、像素长度、资源上限或联合尺寸协议无效。
    """

    if not _strict_json_equal(options, _IMAGE_SCORE_OPTIONS):
        raise ArtifactMetricValueError("options")
    actual_image = _validated_image(actual, role="observation")
    gold_image = _validated_image(gold, role="gold")
    if (
        actual_image.width != gold_image.width
        or actual_image.height != gold_image.height
    ):
        raise ArtifactMetricValueError("observation")
    if min(actual_image.width, actual_image.height) < 7:
        return 0.0
    if (
        actual_image.rgb_pixels == gold_image.rgb_pixels
        and actual_image.hsv_pixels == gold_image.hsv_pixels
    ):
        return 1.0
    spatial_score = (
        sum(
            _channel_structural_similarity(actual_image, gold_image, channel)
            for channel in range(3)
        )
        / 3.0
    )
    color_score = _hsv_histogram_similarity(actual_image, gold_image)
    return max(0.0, min(1.0, spatial_score, color_score))


def _validated_image(value: object, *, role: str) -> NormalizedRGBImageValue:
    """校验联合归一化图像的尺寸、像素长度与资源上限。

    输入参数：
        value：候选 ``NormalizedRGBImageValue``。
        role：错误应归属的 observation/gold 边界。
    输出返回值：
        schema 合法的原图像值。
    异常：
        ArtifactMetricValueError：尺寸不是正整数、像素超限或 RGB/HSV 长度不匹配。
    """

    if (
        not isinstance(value, NormalizedRGBImageValue)
        or type(value.width) is not int
        or type(value.height) is not int
        or value.width <= 0
        or value.height <= 0
        or value.width * value.height > _MAX_TYPED_IMAGE_PIXELS
        or not isinstance(value.rgb_pixels, bytes)
        or not isinstance(value.hsv_pixels, bytes)
        or len(value.rgb_pixels) != value.width * value.height * 3
        or len(value.hsv_pixels) != value.width * value.height * 3
    ):
        raise ArtifactMetricValueError(role)
    return value


def _channel_structural_similarity(
    actual: NormalizedRGBImageValue,
    gold: NormalizedRGBImageValue,
    channel: int,
) -> float:
    """以 7×7 half-sample symmetric 窗口计算单个 RGB 通道 SSIM。

    输入参数：
        actual/gold：尺寸相同且已校验的 RGB 图像。
        channel：``0``、``1`` 或 ``2``，分别表示 R/G/B。
    输出返回值：
        所有像素局部 SSIM 的算术平均，裁剪到 ``[0, 1]``。
    """

    width = actual.width
    height = actual.height
    padded_width = width + 6
    padded_height = height + 6
    stride = padded_width + 1
    integral_size = (padded_height + 1) * stride
    sum_actual = array("d", [0.0]) * integral_size
    sum_gold = array("d", [0.0]) * integral_size
    square_actual = array("d", [0.0]) * integral_size
    square_gold = array("d", [0.0]) * integral_size
    product = array("d", [0.0]) * integral_size

    for padded_y in range(padded_height):
        source_y = _symmetric_index(padded_y - 3, height)
        row_actual = row_gold = row_square_actual = row_square_gold = row_product = 0.0
        current_base = (padded_y + 1) * stride
        previous_base = padded_y * stride
        for padded_x in range(padded_width):
            source_x = _symmetric_index(padded_x - 3, width)
            pixel_offset = (source_y * width + source_x) * 3 + channel
            left = float(actual.rgb_pixels[pixel_offset])
            right = float(gold.rgb_pixels[pixel_offset])
            row_actual += left
            row_gold += right
            row_square_actual += left * left
            row_square_gold += right * right
            row_product += left * right
            position = current_base + padded_x + 1
            above = previous_base + padded_x + 1
            sum_actual[position] = sum_actual[above] + row_actual
            sum_gold[position] = sum_gold[above] + row_gold
            square_actual[position] = square_actual[above] + row_square_actual
            square_gold[position] = square_gold[above] + row_square_gold
            product[position] = product[above] + row_product

    constant1 = (0.01 * 255.0) ** 2
    constant2 = (0.03 * 255.0) ** 2
    score_sum = 0.0
    for y in range(height):
        for x in range(width):
            local_actual = _integral_window_sum(sum_actual, stride, x, y)
            local_gold = _integral_window_sum(sum_gold, stride, x, y)
            mean_actual = local_actual / 49.0
            mean_gold = local_gold / 49.0
            variance_actual = (
                _integral_window_sum(square_actual, stride, x, y) / 49.0
                - mean_actual * mean_actual
            )
            variance_gold = (
                _integral_window_sum(square_gold, stride, x, y) / 49.0
                - mean_gold * mean_gold
            )
            covariance = (
                _integral_window_sum(product, stride, x, y) / 49.0
                - mean_actual * mean_gold
            )
            numerator = (2 * mean_actual * mean_gold + constant1) * (
                2 * covariance + constant2
            )
            denominator = (
                mean_actual * mean_actual + mean_gold * mean_gold + constant1
            ) * (variance_actual + variance_gold + constant2)
            score_sum += numerator / denominator
    return max(0.0, min(1.0, score_sum / (width * height)))


def _symmetric_index(index: int, size: int) -> int:
    """将窗口越界坐标折叠为 SciPy ``mode='reflect'`` 的半采样对称坐标。

    输入参数：
        index：可能小于零或超过边界的一维坐标。
        size：该维度的正整数长度。
    输出返回值：
        ``0 <= result < size`` 的折叠坐标。
    """

    while index < 0 or index >= size:
        index = -index - 1 if index < 0 else 2 * size - index - 1
    return index


def _integral_window_sum(
    integral: array,
    stride: int,
    x: int,
    y: int,
) -> float:
    """从 summed-area table 中读取以归一化像素为中心的 7×7 和。

    输入参数：
        integral：含一行一列零边界的积分图。
        stride：积分图行跨度。
        x/y：原图像像素坐标，在已加 3 像素边界的图上即为窗口左上角。
    输出返回值：
        对应 49 个像素的浮点和。
    """

    right = x + 7
    bottom = y + 7
    return (
        integral[bottom * stride + right]
        - integral[y * stride + right]
        - integral[bottom * stride + x]
        + integral[y * stride + x]
    )


def _hsv_histogram_similarity(
    actual: NormalizedRGBImageValue,
    gold: NormalizedRGBImageValue,
) -> float:
    """计算 HSV 饱和度与有色像素色相直方图交集均值。

    输入参数：
        actual/gold：尺寸相同且已校验的 HSV 投影。
    输出返回值：
        32-bin saturation 交集与 saturation>=16 像素 hue 交集的平均。
    """

    saturation_actual = [0] * 32
    saturation_gold = [0] * 32
    hue_actual = [0] * 32
    hue_gold = [0] * 32
    chromatic_actual = chromatic_gold = 0
    pixel_count = actual.width * actual.height
    for pixel_index in range(pixel_count):
        offset = pixel_index * 3
        actual_hue = actual.hsv_pixels[offset]
        actual_saturation = actual.hsv_pixels[offset + 1]
        gold_hue = gold.hsv_pixels[offset]
        gold_saturation = gold.hsv_pixels[offset + 1]
        saturation_actual[actual_saturation // 8] += 1
        saturation_gold[gold_saturation // 8] += 1
        if actual_saturation >= 16:
            hue_actual[actual_hue // 8] += 1
            chromatic_actual += 1
        if gold_saturation >= 16:
            hue_gold[gold_hue // 8] += 1
            chromatic_gold += 1
    saturation_score = (
        sum(
            min(left, right)
            for left, right in zip(saturation_actual, saturation_gold, strict=True)
        )
        / pixel_count
    )
    if chromatic_actual == 0 and chromatic_gold == 0:
        hue_score = 1.0
    elif chromatic_actual == 0 or chromatic_gold == 0:
        hue_score = 0.0
    else:
        hue_score = sum(
            min(left / chromatic_actual, right / chromatic_gold)
            for left, right in zip(hue_actual, hue_gold, strict=True)
        )
    return (saturation_score + hue_score) / 2.0


def _first_reference_index(paragraphs: tuple[str, ...]) -> int | None:
    """查找源 evaluator 实际使用的第一个精确 References 段落。

    输入参数：
        paragraphs：已校验的有序段落。
    输出返回值：
        第一个精确 ``References`` 的下标；不存在时为 ``None``。
    """

    try:
        return paragraphs.index("References")
    except ValueError:
        return None


def _reference_identity_matches(actual: str, gold: str) -> bool:
    """判断单条引用是否通过源 content-only 多重门禁。

    输入参数：
        actual/gold：同一顺序位置的 Agent 与 gold 引用文本。
    输出返回值：
        token F1 至少 0.9，可提取来源 Jaccard 至少 0.6，且 gold
        中可提取的关键身份字段均一致时返回真。
    """

    token_score = _reference_token_f1(actual, gold)
    source_score = _optional_jaccard(
        _reference_source_tokens(actual),
        _reference_source_tokens(gold),
    )
    return (
        token_score >= 0.9
        and (source_score is None or source_score >= 0.6)
        and _reference_fields_consistent(actual, gold)
    )


def _normalize_reference_text(value: str) -> str:
    """统一引用的大小写、DOI 前缀和空白。

    输入参数：
        value：原引用文本。
    输出返回值：
        小写、``doi:`` 统一且连续空白折叠的文本。
    """

    normalized = value.lower()
    normalized = re.sub(r"https?://doi\.org/", "doi:", normalized)
    normalized = re.sub(r"\bdoi\s*:?\s*", "doi:", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _reference_tokens(value: str) -> tuple[str, ...]:
    """提取引用相似度使用的有效 token。

    输入参数：
        value：原引用或字段子串。
    输出返回值：
        移除协议、大部分标点与固定停用词后的有序 token tuple。
    """

    normalized = _normalize_reference_text(value)
    normalized = re.sub(r"https?://", " ", normalized)
    normalized = re.sub(r"[^a-z0-9/.-]+", " ", normalized)
    tokens = tuple(token.strip(".-") for token in normalized.split())
    return tuple(
        token
        for token in tokens
        if len(token) > 1 and token not in _REFERENCE_STOP_TOKENS
    )


def _reference_token_f1(actual: str, gold: str) -> float:
    """计算引用 token 多重集 F1。

    输入参数：
        actual/gold：Agent 与 gold 引用文本。
    输出返回值：
        token 任一侧为空时 ``0.0``，否则为计数重叠的调和 F1。
    """

    actual_tokens = _reference_tokens(actual)
    gold_tokens = _reference_tokens(gold)
    if not actual_tokens or not gold_tokens:
        return 0.0
    actual_counts = Counter(actual_tokens)
    gold_counts = Counter(gold_tokens)
    overlap = sum(
        min(actual_counts.get(token, 0), count) for token, count in gold_counts.items()
    )
    precision = overlap / len(actual_tokens)
    recall = overlap / len(gold_tokens)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _reference_fields_consistent(actual: str, gold: str) -> bool:
    """校验 gold 中可提取的 APA 身份字段不能被 Agent 替换。

    输入参数：
        actual/gold：Agent 与 gold 引用文本。
    输出返回值：
        作者、标题、年份、DOI、非 DOI URL 与页码均满足源约束时返回真。
    """

    extractors = (
        _reference_author_tokens,
        _reference_title_tokens,
        _reference_year,
        _reference_doi,
    )
    for extractor in extractors:
        gold_value = extractor(gold)
        if gold_value and extractor(actual) != gold_value:
            return False
    gold_urls = _reference_urls(gold)
    if gold_urls and set(_reference_urls(actual)) != set(gold_urls):
        return False
    gold_pages = _reference_pages(gold)
    if gold_pages and set(_reference_pages(actual)) != set(gold_pages):
        return False
    return True


def _reference_author_tokens(value: str) -> tuple[str, ...]:
    """提取 APA 年份括号之前的有序作者 token。

    输入参数：
        value：引用文本。
    输出返回值：
        可定位年份时的作者 token；否则为空 tuple。
    """

    match = re.match(r"\s*(.*?)\s*\(\d{4}[a-z]?\)", value.lower())
    return _reference_tokens(match.group(1)) if match else ()


def _reference_title_tokens(value: str) -> tuple[str, ...]:
    """提取 APA 年份后第一个句子的有序标题 token。

    输入参数：
        value：引用文本。
    输出返回值：
        可识别标题时的 token tuple；否则为空 tuple。
    """

    match = re.search(r"\(\d{4}[a-z]?\)\.\s*(.*?)(?:\.\s|$)", value.lower())
    return _reference_tokens(match.group(1)) if match else ()


def _reference_year(value: str) -> str | None:
    """提取 APA 括号中的四位年份及可选字母后缀。

    输入参数：
        value：引用文本。
    输出返回值：
        年份字符串；未找到时为 ``None``。
    """

    match = re.search(r"\((\d{4}[a-z]?)\)", value.lower())
    return match.group(1) if match else None


def _reference_doi(value: str) -> str | None:
    """提取并归一化引用 DOI。

    输入参数：
        value：引用文本。
    输出返回值：
        去掉结尾标点的小写 DOI；未找到时为 ``None``。
    """

    match = re.search(
        r"\bdoi:?(10\.\d{4,9}/[^\s,]+)",
        _normalize_reference_text(value),
    )
    return match.group(1).rstrip(").,").lower() if match else None


def _reference_urls(value: str) -> tuple[str, ...]:
    """提取引用中的非 DOI HTTP(S) URL。

    输入参数：
        value：引用文本。
    输出返回值：
        去掉结尾标点且保持出现顺序的 URL tuple。
    """

    urls = tuple(
        url.rstrip(").,") for url in re.findall(r"https?://[^\s,]+", value.lower())
    )
    return tuple(url for url in urls if "doi.org/" not in url)


def _reference_pages(value: str) -> tuple[str, ...]:
    """提取并规范化引用中的页码区间。

    输入参数：
        value：引用文本。
    输出返回值：
        删除空白并将 en dash 统一为连字号的区间 tuple。
    """

    matches = re.findall(
        r"\b(?:pp?\.\s*)?(\d{1,5}\s*[-–]\s*\d{1,5})\b",
        value.lower(),
    )
    return tuple(re.sub(r"\s+", "", item).replace("–", "-") for item in matches)


def _reference_source_tokens(value: str) -> tuple[str, ...]:
    """提取引用标题后的期刊/来源 token。

    输入参数：
        value：引用文本。
    输出返回值：
        排除纯数字的来源 token；结构不可识别时为空 tuple。
    """

    normalized = _normalize_reference_text(value)
    remainder = re.split(r"\(\d{4}[a-z]?\)\.\s*", normalized, maxsplit=1)
    if len(remainder) != 2:
        return ()
    parts = [part.strip() for part in re.split(r"\.\s+", remainder[1], maxsplit=2)]
    if len(parts) < 2:
        return ()
    source = re.split(
        r"\b(?:retrieved|doi:|https?://|pp?\.)\b",
        parts[1],
    )[0]
    source = source.split(",")[0]
    return tuple(token for token in _reference_tokens(source) if not token.isdigit())


def _optional_jaccard(
    actual: tuple[str, ...],
    gold: tuple[str, ...],
) -> float | None:
    """计算两个非空 token 集合的 Jaccard 相似度。

    输入参数：
        actual/gold：来源 token tuple。
    输出返回值：
        任一侧为空时 ``None``，否则为交集除以并集的分数。
    """

    if not actual or not gold:
        return None
    actual_set = set(actual)
    gold_set = set(gold)
    return len(actual_set & gold_set) / len(actual_set | gold_set)


def _normalize_printed_lines(value: str) -> tuple[str, ...]:
    """按源 ``_load_sheet`` 的反转/dropwhile 顺序归一化 CSV 行。

    输入参数：
        value：受信 adapter 有界解码的 CSV sidecar 文本。
    输出返回值：
        每行 strip 后倒序排列、并删除倒序开头空行的 tuple。
    """

    reversed_lines = [line.strip() for line in reversed(value.splitlines())]
    first_nonempty = 0
    while first_nonempty < len(reversed_lines) and not reversed_lines[first_nonempty]:
        first_nonempty += 1
    return tuple(reversed_lines[first_nonempty:])


def _pdf_text_score(actual: PDFTextValue, gold: PDFTextValue) -> float:
    """计算一对已校验 PDF 文本的源 ratio 分数。

    输入参数：
        actual/gold：已通过强类型 schema 校验的 PDF 文本值。
    输出返回值：
        非双空文本的 normalized Indel ratio ``0..1``；双空为 ``0.0``。
    """

    left = actual.text.strip()
    right = gold.text.strip()
    if not left and not right:
        return 0.0
    return _indel_ratio(left, right) / 100.0


def _validated_pdf_text(value: object, *, role: str) -> PDFTextValue:
    """校验 PDF 文本投影的强类型边界。

    输入参数：
        value：候选 ``PDFTextValue``。
        role：错误应归属的 observation/gold 边界。
    输出返回值：
        schema 合法的原 PDF 文本值。
    异常：
        ArtifactMetricValueError：类型或文本标量无效。
    """

    if not isinstance(value, PDFTextValue) or not isinstance(value.text, str):
        raise ArtifactMetricValueError(role)
    return value


def _validated_pdf_archive(value: object, *, role: str) -> PDFArchiveValue:
    """校验 PDF archive 投影的顶层名称、唯一性和文本值。

    输入参数：
        value：候选 ``PDFArchiveValue``。
        role：错误应归属的 observation/gold 边界。
    输出返回值：
        schema 合法的原 archive 投影。
    异常：
        ArtifactMetricValueError：成员名包含路径、重名或 PDF 值无效。
    """

    if not isinstance(value, PDFArchiveValue) or not isinstance(value.members, tuple):
        raise ArtifactMetricValueError(role)
    names: set[str] = set()
    for member in value.members:
        if (
            not isinstance(member, PDFArchiveMemberValue)
            or not _is_safe_archive_member_name(member.name)
            or member.name in names
        ):
            raise ArtifactMetricValueError(role)
        _validated_pdf_text(member.document, role=role)
        names.add(member.name)
    return value


def _is_safe_archive_member_name(value: object) -> bool:
    """判断 archive 成员名是否为受限的单层 POSIX 名称。

    输入参数：
        value：候选成员名。
    输出返回值：
        非空、非点段、无分隔符/NUL 且 UTF-8 不超过 255 bytes 时返回真。
    """

    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        return False
    try:
        return len(value.encode("utf-8")) <= 255
    except UnicodeEncodeError:
        return False


def _normalize_fuzzy_address(value: str) -> str:
    """按源规则顺序归一化餐厅地址。

    输入参数：
        value：已投影的单元格字符串。
    输出返回值：
        依次执行 ``Rd -> Road``、``St -> Street`` 与小写化后的文本。
    """

    return value.replace("Rd", "Road").replace("St", "Street").lower()


def _normalize_phone(value: str) -> str:
    """按源 includes 规则归一化电话字符串。

    输入参数：
        value：已投影的单元格字符串。
    输出返回值：
        先以 ``lstrip('+ ')`` 裁剪开头，再删除空格、括号和连字号的文本。
    """

    trimmed = value.lstrip("+ ")
    ignored = set(" ()-")
    return "".join(character for character in trimmed if character not in ignored)


def _indel_ratio(left: str, right: str) -> float:
    """使用 LCS 复现 RapidFuzz ``fuzz.ratio`` 的 normalized Indel 分数。

    输入参数：
        left/right：已按 contract 归一化的字符串。
    输出返回值：
        ``0..100`` 分数；双空字符串为 ``100``。
    """

    if not left and not right:
        return 100.0
    if len(left) < len(right):
        left, right = right, left
    previous = [0] * (len(right) + 1)
    for left_character in left:
        current = [0]
        for index, right_character in enumerate(right, start=1):
            current.append(
                previous[index - 1] + 1
                if left_character == right_character
                else max(previous[index], current[index - 1])
            )
        previous = current
    return 200.0 * previous[-1] / (len(left) + len(right))


def _speaker_shapes_match(
    actual_shapes: tuple[PresentationShapeValue, ...],
    gold_shapes: tuple[PresentationShapeValue, ...],
) -> bool:
    """按源 zip 语义比较 shape 中启用的文本和格式。

    输入参数：
        actual_shapes/gold_shapes：已校验且数量相等的 shape tuple。
    输出返回值：
        每对同时具有文本的 shape 均满足文本、段落与 run 检查时返回真。

    注意：
        为保留源 contract，段落和 run 使用 ``zip`` 而不额外比较数量。
        这是源 evaluator 的已固定行为，若收紧必须发布新 contract。
    """

    for actual_shape, gold_shape in zip(actual_shapes, gold_shapes, strict=True):
        if actual_shape.text is None or gold_shape.text is None:
            continue
        if actual_shape.text.strip() != gold_shape.text.strip():
            return False
        for actual_paragraph, gold_paragraph in zip(
            actual_shape.paragraphs,
            gold_shape.paragraphs,
        ):
            if (
                actual_paragraph.alignment != gold_paragraph.alignment
                or actual_paragraph.text != gold_paragraph.text
                or actual_paragraph.level != gold_paragraph.level
            ):
                return False
            for actual_run, gold_run in zip(
                actual_paragraph.runs,
                gold_paragraph.runs,
            ):
                if actual_run != gold_run:
                    return False
    return True


def _validated_presentation(
    value: object,
    *,
    role: str,
) -> PresentationArtifactValue:
    """递归校验 speaker-notes 强类型 PPT 投影。

    输入参数：
        value：候选 ``PresentationArtifactValue``。
        role：错误应归属的 observation/gold 边界。
    输出返回值：
        schema 合法的原 PPT 投影。
    异常：
        ArtifactMetricValueError：容器、文本或格式标量类型无效。
    """

    if not isinstance(value, PresentationArtifactValue) or not isinstance(
        value.slides, tuple
    ):
        raise ArtifactMetricValueError(role)
    for slide in value.slides:
        if (
            not isinstance(slide, PresentationSlideValue)
            or not _is_optional_string(slide.background_color)
            or not isinstance(slide.notes_text, str)
            or not isinstance(slide.shapes, tuple)
        ):
            raise ArtifactMetricValueError(role)
        for shape in slide.shapes:
            if (
                not isinstance(shape, PresentationShapeValue)
                or not _is_optional_string(shape.text)
                or not isinstance(shape.paragraphs, tuple)
                or (shape.text is None and shape.paragraphs)
            ):
                raise ArtifactMetricValueError(role)
            for paragraph in shape.paragraphs:
                if (
                    not isinstance(paragraph, PresentationParagraphValue)
                    or not isinstance(paragraph.text, str)
                    or not _is_enum_scalar(paragraph.alignment)
                    or type(paragraph.level) is not int
                    or paragraph.level < 0
                    or not isinstance(paragraph.runs, tuple)
                ):
                    raise ArtifactMetricValueError(role)
                for run in paragraph.runs:
                    if not _is_valid_presentation_run(run):
                        raise ArtifactMetricValueError(role)
    return value


def _is_valid_presentation_run(value: object) -> bool:
    """判断一个 PPT run 是否只含固定标量属性。

    输入参数：
        value：候选 run 投影。
    输出返回值：
        类型、可选字体属性和 strike 字符串均合法时返回真。
    """

    return (
        isinstance(value, PresentationRunValue)
        and _is_optional_string(value.font_name)
        and (value.font_size is None or type(value.font_size) is int)
        and (value.bold is None or type(value.bold) is bool)
        and (value.italic is None or type(value.italic) is bool)
        and _is_optional_string(value.color_rgb)
        and _is_enum_scalar(value.underline)
        and isinstance(value.strike, str)
    )


def _is_optional_string(value: object) -> bool:
    """判断值是否为字符串或 ``None``。

    输入参数：
        value：待检查值。
    输出返回值：
        仅为 ``str | None`` 时返回真。
    """

    return value is None or isinstance(value, str)


def _is_enum_scalar(value: object) -> bool:
    """判断 Office enum 是否已投影为可比较标量。

    输入参数：
        value：对齐或下划线投影。
    输出返回值：
        值为 ``None`` 或精确 str/int/bool 时返回真。
    """

    return value is None or type(value) in {str, int, bool}


def _validated_document(value: object, *, role: str) -> DocumentParagraphValue:
    """校验强类型段落投影。

    输入参数：
        value：候选 ``DocumentParagraphValue``。
        role：错误应归属的 observation/gold 边界。
    输出返回值：
        schema 合法的原文档值。
    异常：
        ArtifactMetricValueError：类型、tuple 外形或段落标量无效。
    """

    if (
        not isinstance(value, DocumentParagraphValue)
        or not isinstance(value.paragraphs, tuple)
        or not all(isinstance(paragraph, str) for paragraph in value.paragraphs)
    ):
        raise ArtifactMetricValueError(role)
    return value


def _normalize_document_whitespace(paragraphs: tuple[str, ...]) -> str:
    """按源函数的 ``join -> whitespace collapse -> strip`` 顺序归一化。

    输入参数：
        paragraphs：已校验的有序段落 tuple。
    输出返回值：
        任意连续 Unicode 空白替换为单空格且去掉首尾空白的文本。
    """

    return re.sub(r"\s+", " ", "\n".join(paragraphs)).strip()


def _find_sheet(
    workbook: SpreadsheetArtifactValue,
    name: str,
) -> SpreadsheetSheetValue | None:
    """按精确名称查找已校验 workbook 中的 sheet。

    输入参数：
        workbook：已通过 schema 校验的 workbook。
        name：contract 内固定的 sheet 名称。
    输出返回值：
        唯一同名 sheet；不存在时返回 ``None``。
    """

    return next((sheet for sheet in workbook.sheets if sheet.name == name), None)


def _validated_workbook(value: object, *, role: str) -> SpreadsheetArtifactValue:
    """校验 workbook 投影的类型、sheet 身份和表格形状。

    输入参数：
        value：候选 ``SpreadsheetArtifactValue``。
        role：错误应归属的 observation/gold 边界。
    输出返回值：
        schema 合法的原不可变 workbook 值。
    异常：
        ArtifactMetricValueError：类型、重名 sheet、行宽或单元格无效。
    """

    if not isinstance(value, SpreadsheetArtifactValue) or not isinstance(
        value.sheets, tuple
    ):
        raise ArtifactMetricValueError(role)
    names: set[str] = set()
    for sheet in value.sheets:
        if (
            not isinstance(sheet, SpreadsheetSheetValue)
            or not isinstance(sheet.name, str)
            or not sheet.name
            or sheet.name in names
            or not isinstance(sheet.columns, tuple)
            or not isinstance(sheet.rows, tuple)
            or not isinstance(sheet.cells, tuple)
            or not _is_optional_string(sheet.printed_text)
            or not all(_is_cell_scalar(item) for item in sheet.columns)
            or not all(
                isinstance(row, tuple)
                and len(row) == len(sheet.columns)
                and all(_is_cell_scalar(item) for item in row)
                for row in sheet.rows
            )
            or not all(
                isinstance(cell, SpreadsheetCellValue)
                and isinstance(cell.coordinate, str)
                and re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", cell.coordinate)
                and isinstance(cell.value, str)
                for cell in sheet.cells
            )
            or len({cell.coordinate for cell in sheet.cells}) != len(sheet.cells)
        ):
            raise ArtifactMetricValueError(role)
        names.add(sheet.name)
    return value


def _is_cell_scalar(value: object) -> bool:
    """判断一个值是否可作为确定性表格单元。

    输入参数：
        value：待检查的字段或单元格值。
    输出返回值：
        仅 ``None``、精确 bool/int/float/str 时返回真。
    """

    return value is None or type(value) in {bool, int, float, str}


def _tables_equal(
    actual: SpreadsheetSheetValue,
    gold: SpreadsheetSheetValue,
    *,
    precision: int,
) -> bool:
    """比较两个已校验 sheet 的列、形状与归一化单元。

    输入参数：
        actual/gold：已通过 workbook schema 校验的 sheet。
        precision：源 ``DataFrame.round`` 规则的小数位数。
    输出返回值：
        列标签、行数和所有单元格均对应相等时返回真。
    """

    if len(actual.columns) != len(gold.columns) or len(actual.rows) != len(gold.rows):
        return False
    if not all(
        _cell_values_equal(left, right, precision=precision)
        for left, right in zip(actual.columns, gold.columns, strict=True)
    ):
        return False
    return all(
        all(
            _cell_values_equal(left, right, precision=precision)
            for left, right in zip(actual_row, gold_row, strict=True)
        )
        for actual_row, gold_row in zip(actual.rows, gold.rows, strict=True)
    )


def _cell_values_equal(left: object, right: object, *, precision: int) -> bool:
    """按 DataFrame 数值四舍五入与 NaN 同位语义比较单元。

    输入参数：
        left/right：已校验的单元格值。
        precision：数值小数位数。
    输出返回值：
        同位 NaN 或归一化值及类型兼容时返回真。
    """

    if type(left) is float and type(right) is float:
        if math.isnan(left) or math.isnan(right):
            return math.isnan(left) and math.isnan(right)
    if type(left) in {int, float} and type(right) in {int, float}:
        return round(left, precision) == round(right, precision)
    return type(left) is type(right) and left == right


def _strict_json_equal(actual: object, expected: object) -> bool:
    """比较 JSON 结构时禁止 bool/int 等 Python 宽松相等。

    输入参数：
        actual：公共 metric 入口收到的候选 options。
        expected：模块内固定的 JSON 原语结构。
    输出返回值：
        映射键集、序列长度、标量类型与值递归相同时返回真。
    """

    if isinstance(expected, Mapping):
        return (
            isinstance(actual, Mapping)
            and set(actual) == set(expected)
            and all(
                _strict_json_equal(actual[key], value)
                for key, value in expected.items()
            )
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _strict_json_equal(left, right)
                for left, right in zip(actual, expected, strict=True)
            )
        )
    return type(actual) is type(expected) and actual == expected


__all__ = [
    "ArtifactMetricValueError",
    "DocumentParagraphValue",
    "PresentationArtifactValue",
    "PresentationParagraphValue",
    "PresentationRunValue",
    "PresentationShapeValue",
    "PresentationSlideValue",
    "PDFTextValue",
    "PDFArchiveMemberValue",
    "PDFArchiveValue",
    "NormalizedRGBImageValue",
    "SpreadsheetArtifactValue",
    "SpreadsheetCellValue",
    "SpreadsheetSheetValue",
    "evaluate_first_sheet_table",
    "evaluate_docx_content",
    "evaluate_named_unseen_movies_table",
    "evaluate_restaurant_fuzzy_sheet",
    "evaluate_problem_invoice_pdf",
    "evaluate_pdf_archive",
    "evaluate_sheet1_print",
    "evaluate_apa_references",
    "evaluate_slide_background_image",
    "evaluate_speaker_notes_presentation",
]
