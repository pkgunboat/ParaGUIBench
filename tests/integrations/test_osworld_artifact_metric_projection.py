"""OSWorld raw artifact 到强类型 metric value 的 production projection 测试。"""

from __future__ import annotations

from datetime import date
from io import BytesIO
import sys
from types import ModuleType
import zipfile

import pytest

from paraguibench.evaluation.osworld.artifact_metrics import evaluate_artifact_metric
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)
from paraguibench.integrations.osworld.artifact_family_evidence import (
    ArtifactFamilyCapture,
)
from paraguibench.integrations.osworld.artifact_metric_projection import (
    OSWorldArtifactMetricProjectionError,
    project_verified_artifact_metric_values,
)


openpyxl = pytest.importorskip("openpyxl")
docx = pytest.importorskip("docx")
pptx = pytest.importorskip("pptx")
PillowImage = pytest.importorskip("PIL.Image")


_FIRST_SHEET_TASK_ID = "Operation-FileOperate-CombinationDocs-010"
_NAMED_SHEET_TASK_ID = "Operation-FileOperate-SearchAndWrite-009"
_FUZZY_SHEET_TASK_ID = "Operation-WebOperate-SearchAndWrite-001"
_SHEET_PRINT_TASK_ID = "Operation-FileOperate-CombinationDocs-013"
_DOCX_CONTENT_TASK_ID = "Operation-FileOperate-SearchAndWrite-003"
_APA_REFERENCES_TASK_ID = "Operation-FileOperate-CombinationDocs-012"
_SPEAKER_NOTES_TASK_ID = "Operation-FileOperate-CombinationDocs-009"
_PROBLEM_PDF_TASK_ID = "Operation-FileOperate-CombinationDocs-011"
_PDF_ARCHIVE_TASK_ID = "Operation-FileOperate-BatchOperation-003"
_SLIDE_BACKGROUND_TASK_ID = "Operation-FileOperate-Settings-001"


class _FakePdfPage:
    """提供仅含固定提取文本的合成 PDF 页面。"""

    def __init__(self, text: str) -> None:
        """保存页面文本。

        输入参数：
            text：后续 ``extract_text`` 返回的合成文本。
        输出返回值：
            无；初始化当前页面。
        """

        self._text = text

    def extract_text(self) -> str:
        """返回合成页面文本。

        输入参数：
            无。
        输出返回值：
            构造页面时保存的文本。
        """

        return self._text


class _FakePdfReader:
    """把受控测试 PDF 标记解析为单页 reader。"""

    def __init__(self, stream: BytesIO, *, strict: bool = True) -> None:
        """解析 ``TEXT`` 标记并构造单页集合。

        输入参数：
            stream：包含合成 PDF bytes 的内存流。
            strict：模拟 pypdf 的严格解析参数。
        输出返回值：
            无；设置未加密状态与单页 ``pages`` tuple。
        """

        del strict
        content = stream.read()
        prefix = b"\nTEXT:"
        suffix = b"\n%%EOF"
        start = content.index(prefix) + len(prefix)
        end = content.index(suffix, start)
        text = content[start:end].decode("utf-8")
        self.is_encrypted = False
        self.pages = (_FakePdfPage(text),)


def _install_fake_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """向当前测试进程注入受控 ``pypdf`` 边界替身。

    输入参数：
        monkeypatch：pytest 提供的模块表修改 fixture。
    输出返回值：
        无；``sys.modules`` 中的 ``pypdf`` 临时指向合成 reader。
    """

    module = ModuleType("pypdf")
    module.PdfReader = _FakePdfReader
    monkeypatch.setitem(sys.modules, "pypdf", module)


def _pdf_bytes(text: str, marker: str) -> bytes:
    """生成含可提取文本与唯一标记的最小合成 PDF bytes。

    输入参数：
        text：写入 ``TEXT`` 区段的页面文本。
        marker：写入注释行、用于区分文件的稳定标记。
    输出返回值：
        UTF-8 编码的受控合成 PDF bytes。
    """

    return f"%PDF-1.7\n%{marker}\nTEXT:{text}\n%%EOF\n".encode("utf-8")


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """把固定成员映射封装为内存 ZIP bytes。

    输入参数：
        entries：归档成员相对路径到内容 bytes 的映射。
    输出返回值：
        使用 DEFLATE 压缩生成的 ZIP bytes。
    """

    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return output.getvalue()


def _png_bytes(
    size: tuple[int, int],
    color: tuple[int, int, int],
) -> bytes:
    """生成固定尺寸与 RGB 颜色的内存 PNG bytes。

    输入参数：
        size：``(width, height)`` 正整数像素尺寸。
        color：三个 uint8 通道组成的 RGB 颜色。
    输出返回值：
        Pillow 编码的 PNG bytes。
    """

    image = PillowImage.new("RGB", size, color)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def _xlsx_bytes(*, score: float) -> bytes:
    """生成只含一页表格的合成 XLSX bytes。

    输入参数：
        score：写入第二行 score 列的数值。
    输出返回值：
        openpyxl 在内存中生成的 OOXML bytes。
    """

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Grades"
    sheet.append(("name", "score"))
    sheet.append(("Ada", score))
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _first_sheet_xlsx_with_irrelevant_second_sheet(
    *,
    score: float,
    irrelevant_value: object,
) -> bytes:
    """生成首页可评价、次页与 first-sheet 协议无关的 XLSX。

    输入参数：
        score：写入首页第二行的待评价分数。
        irrelevant_value：写入次页 A1 的无关值。
    输出返回值：
        包含两页但只有首页属于评价语义的 OOXML bytes。
    """

    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "Grades"
    first.append(("name", "score"))
    first.append(("Ada", score))
    second = workbook.create_sheet("Irrelevant")
    second["A1"] = irrelevant_value
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _named_xlsx_bytes(*, noise: str) -> bytes:
    """生成含噪声首页和 ``unseen_movies`` 目标页的 XLSX bytes。

    输入参数：
        noise：写入首页的不相关文本。
    输出返回值：
        目标页内容固定的内存 OOXML bytes。
    """

    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "noise"
    first.append(("value",))
    first.append((noise,))
    target = workbook.create_sheet("unseen_movies")
    target.append(("title",))
    target.append(("Arrival",))
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _named_xlsx_with_sheet_count(*, sheet_count: int) -> bytes:
    """生成指定工作表数且含 named-sheet 目标的 XLSX。

    输入参数：
        sheet_count：workbook 中的工作表总数，必须大于零。
    输出返回值：
        最后一页名为 ``unseen_movies`` 的内存 OOXML bytes。
    """

    if sheet_count < 1:
        raise ValueError("sheet_count 必须为正整数")
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "noise-0" if sheet_count > 1 else "unseen_movies"
    first["A1"] = "value"
    for index in range(1, sheet_count):
        title = "unseen_movies" if index == sheet_count - 1 else f"noise-{index}"
        workbook.create_sheet(title)["A1"] = "value"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _restaurant_xlsx_bytes(*, abbreviated: bool) -> bytes:
    """生成六行餐厅联系信息的合成 XLSX bytes。

    输入参数：
        abbreviated：真时使用 Rd 与带标点电话，否则使用 gold 归一化文本。
    输出返回值：
        ``Sheet1`` 中 A1:D6 均有固定值的 OOXML bytes。
    """

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    for row in range(1, 7):
        sheet.cell(row, 1, f"Restaurant {row}")
        sheet.cell(row, 2, f"{row} Main {'Rd' if abbreviated else 'Road'}")
        sheet.cell(
            row,
            3,
            f"+1 (555) 000-00{row}" if abbreviated else f"155500000{row}",
        )
        sheet.cell(row, 4, f"https://example.test/{row}")
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _docx_bytes(paragraphs: tuple[str, ...]) -> bytes:
    """生成只含指定顶层段落的合成 DOCX bytes。

    输入参数：
        paragraphs：按顺序写入 document body 的段落文本。
    输出返回值：
        python-docx 在内存中生成的 OOXML bytes。
    """

    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _pptx_bytes(*, notes_text: str, body_text: str) -> bytes:
    """生成含单页文本框和 speaker notes 的合成 PPTX bytes。

    输入参数：
        notes_text：写入备注文本框的文本。
        body_text：写入幻灯片文本框的文本。
    输出返回值：
        python-pptx 在内存中生成的 OOXML bytes。
    """

    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    text_box = slide.shapes.add_textbox(0, 0, 1_000_000, 500_000)
    text_box.text = body_text
    slide.notes_slide.notes_text_frame.text = notes_text
    output = BytesIO()
    presentation.save(output)
    return output.getvalue()


def test_first_sheet_raw_xlsx_projection_evaluates_end_to_end() -> None:
    """验证 raw capture 与 verified gold 经安全 XLSX 投影后可直接纯评价。

    输入参数：
        无；Agent 与 gold 使用仅在第五位小数有差异的合成 XLSX。
    输出返回值：
        无；断言 projection 固定 contract/options、repr 不泄露内容，且
        first-sheet 四位精度评价得到满分。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_FIRST_SHEET_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(_xlsx_bytes(score=0.123456),),
    )

    projections = project_verified_artifact_metric_values(
        _FIRST_SHEET_TASK_ID,
        capture,
        verified_gold_bytes={gold_key: _xlsx_bytes(score=0.123454)},
    )

    assert len(projections) == 1
    projection = projections[0]
    assert "Ada" not in repr(projection)
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )
    assert result.score == 1.0


def test_first_sheet_projection_does_not_parse_irrelevant_later_sheets() -> None:
    """验证 first-sheet 协议不被后续工作表内容干扰。

    输入参数：
        无；actual 的次页含纯协议不支持的日期类型，gold 的
        次页含不同文本，两者首页完全相同。
    输出返回值：
        无；只投影首页并得到满分，不解析与评价无关的次页。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_FIRST_SHEET_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(
            _first_sheet_xlsx_with_irrelevant_second_sheet(
                score=1.0,
                irrelevant_value=date(2026, 8, 10),
            ),
        ),
    )

    projection = project_verified_artifact_metric_values(
        _FIRST_SHEET_TASK_ID,
        capture,
        verified_gold_bytes={
            gold_key: _first_sheet_xlsx_with_irrelevant_second_sheet(
                score=1.0,
                irrelevant_value="different-but-irrelevant",
            )
        },
    )[0]

    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )
    assert result.score == 1.0


def test_named_sheet_raw_xlsx_projection_preserves_sheet_identity() -> None:
    """验证 raw XLSX 投影保留 sheet 名称并不把首页差异带入目标 metric。

    输入参数：
        无；actual/gold 首页不同，``unseen_movies`` 页相同。
    输出返回值：
        无；断言 production projection 到 named-sheet pure metric 端到端满分。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_NAMED_SHEET_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(_named_xlsx_bytes(noise="actual"),),
    )

    projection = project_verified_artifact_metric_values(
        _NAMED_SHEET_TASK_ID,
        capture,
        verified_gold_bytes={gold_key: _named_xlsx_bytes(noise="gold")},
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert result.score == 1.0


def test_named_sheet_projection_rejects_excessive_workbook_sheet_count() -> None:
    """验证需枚举工作表的协议具有 workbook 级页数上限。

    输入参数：
        无；actual 与 gold 都含 65 页，目标 named sheet 位于末页。
    输出返回值：
        无；projection 在逐单元格物化前以固定资源错误失败关闭。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_NAMED_SHEET_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    content = _named_xlsx_with_sheet_count(sheet_count=65)
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(content,),
    )

    with pytest.raises(
        OSWorldArtifactMetricProjectionError,
        match="XLSX_SHEET_LIMIT_EXCEEDED",
    ):
        project_verified_artifact_metric_values(
            _NAMED_SHEET_TASK_ID,
            capture,
            verified_gold_bytes={gold_key: content},
        )


def test_named_sheet_projection_rejects_total_cell_budget_before_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 workbook 总单元格预算在逐行物化前生效。

    输入参数：
        monkeypatch：pytest 提供的外部 openpyxl loader 边界替换器。
    输出返回值：
        无；两张各自未超限、合计超限的 sheet 必须以固定错误
        拒绝，不得进入 ``iter_rows``。
    """

    class _OversizedWorksheet:
        """提供只暴露资源形状的外部 worksheet 替身。"""

        max_row = 500_001
        max_column = 1
        title = "unseen_movies"

        def iter_rows(self, *args: object, **kwargs: object):
            """拒绝资源门禁之后本不应发生的单元格迭代。

            输入参数：
                args/kwargs：openpyxl 兼容调用参数，测试中不使用。
            输出返回值：
                不返回；调用即说明预检顺序错误。
            """

            del args, kwargs
            raise AssertionError("cell iteration must not start")

    class _OversizedWorkbook:
        """提供两张合计超限 worksheet 的外部 workbook 替身。"""

        def __init__(self) -> None:
            """初始化两张各 500001 个候选单元格的顺序闭集。

            输入参数：
                无。
            输出返回值：
                无；设置 ``worksheets`` tuple。
            """

            self.worksheets = (_OversizedWorksheet(), _OversizedWorksheet())

        def close(self) -> None:
            """兼容 production finally 中的 workbook 关闭边界。

            输入参数：
                无。
            输出返回值：
                无。
            """

    monkeypatch.setattr(
        openpyxl,
        "load_workbook",
        lambda *_args, **_kwargs: _OversizedWorkbook(),
    )
    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_NAMED_SHEET_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    content = _named_xlsx_bytes(noise="bounded-preflight")
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(content,),
    )

    with pytest.raises(
        OSWorldArtifactMetricProjectionError,
        match="XLSX_WORKBOOK_CELL_LIMIT_EXCEEDED",
    ):
        project_verified_artifact_metric_values(
            _NAMED_SHEET_TASK_ID,
            capture,
            verified_gold_bytes={gold_key: content},
        )


def test_fuzzy_sheet_raw_xlsx_projection_preserves_coordinate_strings() -> None:
    """验证 XLSX projection 为 fuzzy metric 保留 A1 坐标与源 ``str(value)`` 语义。

    输入参数：
        无；actual 使用地址缩写和电话标点，gold 使用展开/纯数字值。
    输出返回值：
        无；断言投影后固定 exact/fuzzy/includes 规则端到端满分。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_FUZZY_SHEET_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(_restaurant_xlsx_bytes(abbreviated=True),),
    )

    projection = project_verified_artifact_metric_values(
        _FUZZY_SHEET_TASK_ID,
        capture,
        verified_gold_bytes={gold_key: _restaurant_xlsx_bytes(abbreviated=False)},
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert result.score == 1.0


def test_sheet_print_bundle_projection_validates_xlsx_and_decodes_csv() -> None:
    """验证 XLSX+CSV bundle 对两个 gold key 精确绑定并投影 print 文本。

    输入参数：
        无；actual/gold 使用同一合法 XLSX，CSV 仅在行首尾空白与末尾空行上不同。
    输出返回值：
        无；断言 raw bundle 经安全预检/解码后获得 sheet-print 满分。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_SHEET_PRINT_TASK_ID]
    slot = spec.artifact_slots[0]
    metric = slot.metrics[0]
    workbook_bytes = _xlsx_bytes(score=1.0)
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file-bundle",
        _file_items=(workbook_bytes, b" Name,Value \n Ada,1 \n\n"),
    )

    projection = project_verified_artifact_metric_values(
        _SHEET_PRINT_TASK_ID,
        capture,
        verified_gold_bytes={
            metric.gold_keys[0]: workbook_bytes,
            metric.gold_keys[1]: b"Name,Value\nAda,1",
        },
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert result.score == 1.0


def test_docx_raw_projection_extracts_ordered_paragraph_text() -> None:
    """验证 DOCX projection 在 OOXML 预检后只注入有序段落文本。

    输入参数：
        无；actual/gold 使用不同段落空白但归一化内容相同的 DOCX。
    输出返回值：
        无；断言 raw DOCX 到 docx-content pure metric 端到端满分。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_DOCX_CONTENT_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(_docx_bytes(("Book   One", "Author\tName")),),
    )

    projection = project_verified_artifact_metric_values(
        _DOCX_CONTENT_TASK_ID,
        capture,
        verified_gold_bytes={gold_key: _docx_bytes((" Book One Author", "Name "))},
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert result.score == 1.0


def test_apa_references_raw_docx_projection_uses_verified_gold() -> None:
    """验证 APA references contract 复用安全 DOCX 投影且不猜测 gold。

    输入参数：
        无；actual/gold 均由测试显式注入已验证 DOCX bytes。
    输出返回值：
        无；断言 references 段落顺序保留并获得满分。
    """

    citation = (
        "Doe, J. (2024). Reliable GUI evaluation. "
        "Journal of Interface Research, 12(3), 1–9."
    )
    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_APA_REFERENCES_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(_docx_bytes(("Case study", "References", citation)),),
    )

    projection = project_verified_artifact_metric_values(
        _APA_REFERENCES_TASK_ID,
        capture,
        verified_gold_bytes={
            gold_key: _docx_bytes(("Different body", "References", citation))
        },
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert result.score == 1.0


def test_speaker_notes_raw_pptx_projection_preserves_enabled_fields() -> None:
    """验证 PPTX 投影保留源 contract 仍启用的文本、格式与备注。

    输入参数：
        无；actual/gold 仅在 speaker notes 首尾空白上不同。
    输出返回值：
        无；断言投影值不在 repr 泄露文本，且端到端评价满分。
    """

    secret_text = "Private lecture topic"
    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_SPEAKER_NOTES_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(
            _pptx_bytes(notes_text="  Remember this  ", body_text=secret_text),
        ),
    )

    projection = project_verified_artifact_metric_values(
        _SPEAKER_NOTES_TASK_ID,
        capture,
        verified_gold_bytes={
            gold_key: _pptx_bytes(
                notes_text="Remember this",
                body_text=secret_text,
            )
        },
    )[0]
    actual = projection.actual_value()
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=actual,
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert secret_text not in repr(actual)
    assert secret_text not in repr(actual.slides[0])
    assert result.score == 1.0


def test_problem_pdf_raw_projection_extracts_text_with_lazy_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证单 PDF 通过延迟 parser 投影为脱敏问题发票文本。

    输入参数：
        monkeypatch：注入受控 ``pypdf`` reader 的 pytest fixture。
    输出返回值：
        无；断言实际值与已验证 gold 文本一致时满分，且 repr 不含文本。
    """

    _install_fake_pypdf(monkeypatch)
    sentinel_text = "Invoice total 47109"
    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_PROBLEM_PDF_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file",
        _file_items=(_pdf_bytes(sentinel_text, "actual"),),
    )

    projection = project_verified_artifact_metric_values(
        _PROBLEM_PDF_TASK_ID,
        capture,
        verified_gold_bytes={
            gold_key: _pdf_bytes(sentinel_text, "gold"),
        },
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert sentinel_text not in repr(projection)
    assert result.score == 1.0


def test_pdf_archive_raw_projection_binds_names_and_verified_gold_zip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 PDF bundle 成员名与反序 gold ZIP 可精确闭集绑定。

    输入参数：
        monkeypatch：注入受控 ``pypdf`` reader 的 pytest fixture。
    输出返回值：
        无；断言成员顺序差异不影响评价，名称不进入 repr，且结果满分。
    """

    _install_fake_pypdf(monkeypatch)
    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_PDF_ARCHIVE_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    member_names = ("chapter-1.pdf", "chapter-2.pdf")
    chapter_one = _pdf_bytes("Chapter one", "actual-one")
    chapter_two = _pdf_bytes("Chapter two", "actual-two")
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="file-bundle",
        _file_items=(chapter_one, chapter_two),
        _member_names=member_names,
    )
    gold_archive = _zip_bytes(
        {
            "chapter-2.pdf": _pdf_bytes("Chapter two", "gold-two"),
            "chapter-1.pdf": _pdf_bytes("Chapter one", "gold-one"),
        }
    )

    projection = project_verified_artifact_metric_values(
        _PDF_ARCHIVE_TASK_ID,
        capture,
        verified_gold_bytes={gold_key: gold_archive},
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )

    assert all(member not in repr(projection) for member in member_names)
    assert result.score == 1.0


def test_slide_background_image_projection_jointly_normalizes_dimensions() -> None:
    """验证实际图与 gold 按共同最小尺寸归一化后可直接评价。

    输入参数：
        无；actual 为 8×9 红图，gold 为 9×8 同色红图。
    输出返回值：
        无；断言两侧强类型值均为 8×8、repr 不泄露像素且满分。
    """

    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_SLIDE_BACKGROUND_TASK_ID]
    slot = spec.artifact_slots[0]
    gold_key = slot.metrics[0].gold_keys[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="image",
        _file_items=(_png_bytes((8, 9), (255, 0, 0)),),
    )

    projection = project_verified_artifact_metric_values(
        _SLIDE_BACKGROUND_TASK_ID,
        capture,
        verified_gold_bytes={
            gold_key: _png_bytes((9, 8), (255, 0, 0)),
        },
    )[0]
    actual = projection.actual_value()
    gold = projection.gold_value()
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=actual,
        gold=gold,
        options=projection.options(),
    )

    assert (actual.width, actual.height) == (8, 8)
    assert (gold.width, gold.height) == (8, 8)
    assert "255" not in repr(projection)
    assert result.score == 1.0
