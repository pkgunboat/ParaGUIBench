"""OSWorld 外部 gold artifact metric 的强类型纯评价测试。"""

from __future__ import annotations

import hashlib
from io import BytesIO
import os
from pathlib import Path

import pytest

from paraguibench.evaluation.osworld.artifact_metric_values import (
    DocumentParagraphValue,
    PresentationArtifactValue,
    PresentationParagraphValue,
    PresentationRunValue,
    PresentationShapeValue,
    PresentationSlideValue,
    PDFTextValue,
    PDFArchiveMemberValue,
    PDFArchiveValue,
    NormalizedRGBImageValue,
    SpreadsheetArtifactValue,
    SpreadsheetCellValue,
    SpreadsheetSheetValue,
)
from paraguibench.evaluation.osworld.artifact_metrics import evaluate_artifact_metric
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)
from paraguibench.integrations.osworld.artifact_family_evidence import (
    ArtifactFamilyCapture,
)
from paraguibench.integrations.osworld.artifact_metric_projection import (
    project_verified_artifact_metric_values,
)


_SETTINGS_THRESHOLD_FIXTURE_ENV = "PARAGUIBENCH_SETTINGS001_THRESHOLD_FIXTURE_ROOT"
_SETTINGS_THRESHOLD_FIXTURES = {
    "old_9_042": (
        Path("original/osworld/landscape.png"),
        "9eeaa986f3d51e85bc5c21c95f4674b7e0400616d5599d63de1320b0d11fdebe",
    ),
    "frame_240": (
        Path("evidence/osworld/threshold_frames/frame_240.png"),
        "b383ffccf666a2dfe83100b392e1d4e2dbb744e1034b2e200be72621cbe52fc3",
    ),
    "frame_246": (
        Path("evidence/osworld/threshold_frames/frame_246.png"),
        "2613a34a83db3a3c19b88787d960a1fae9a5ebd55a6dd9c6515d8ac7a9536726",
    ),
    "frame_255": (
        Path("evidence/osworld/threshold_frames/frame_255.png"),
        "a9fb1febae1d9b91c884ae30b358d3156a27a17debd3e10fe6c750e6b0962db6",
    ),
}


def test_settings_threshold_fixture_contract_contains_no_private_absolute_path() -> (
    None
):
    """验证真实校准 fixture 只能由显式环境变量提供私有根。

    输入参数：
        无；读取测试模块固定的环境变量名与各公开相对路径。
    输出返回值：
        无；断言仓库不固化本机路径，且所有 fixture 定位器均为相对路径。
    """

    assert _SETTINGS_THRESHOLD_FIXTURE_ENV == (
        "PARAGUIBENCH_SETTINGS001_THRESHOLD_FIXTURE_ROOT"
    )
    assert all(
        not relative_path.is_absolute()
        for relative_path, _expected_sha256 in _SETTINGS_THRESHOLD_FIXTURES.values()
    )


def _load_settings_threshold_fixture_bytes() -> dict[str, bytes]:
    """读取并验证 Settings-001 私有阈值校准图像。

    输入参数：
        无；从显式环境变量给出的仓库外私有根读取固定相对路径。
    输出返回值：
        逻辑名称到已通过 SHA-256 验证的编码图像字节映射。
    """

    fixture_root_value = os.environ.get(_SETTINGS_THRESHOLD_FIXTURE_ENV)
    if not fixture_root_value:
        pytest.skip(
            "Settings-001 私有阈值 fixture 未配置；"
            f"请设置 {_SETTINGS_THRESHOLD_FIXTURE_ENV}"
        )
    fixture_root = Path(fixture_root_value)
    payloads: dict[str, bytes] = {}
    for logical_name, (
        relative_path,
        expected_sha256,
    ) in _SETTINGS_THRESHOLD_FIXTURES.items():
        try:
            payload = (fixture_root / relative_path).read_bytes()
        except OSError:
            pytest.skip("Settings-001 私有阈值 fixture 不可读")
        assert hashlib.sha256(payload).hexdigest() == expected_sha256
        payloads[logical_name] = payload
    return payloads


def _evaluate_settings_background_bytes(
    actual_bytes: bytes,
    gold_bytes: bytes,
) -> float:
    """通过 production 投影与公开 metric 入口评价一组背景图。

    输入参数：
        actual_bytes：候选幻灯片背景图编码字节。
        gold_bytes：已验证 8.008 秒派生帧编码字节。
    输出返回值：
        联合最小尺寸 Lanczos 投影后的连续图片相似度。
    """

    task_id = "Operation-FileOperate-Settings-001"
    spec = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]
    slot = spec.artifact_slots[0]
    metric = slot.metrics[0]
    capture = ArtifactFamilyCapture(
        slot_id=slot.slot_id,
        status="available",
        payload_kind="image",
        _file_items=(actual_bytes,),
    )
    projection = project_verified_artifact_metric_values(
        task_id,
        capture,
        verified_gold_bytes={metric.gold_keys[0]: gold_bytes},
    )[0]
    result = evaluate_artifact_metric(
        projection.contract_id,
        actual=projection.actual_value(),
        gold=projection.gold_value(),
        options=projection.options(),
    )
    return result.score


def test_first_sheet_contract_compares_typed_tables_after_four_digit_rounding() -> None:
    """验证 first-sheet contract 复现旧最终四位数值精度。

    输入参数：
        无；构造字段、行和 sheet 顺序均已显式投影的强类型值。
    输出返回值：
        无；断言小于四位小数精度的差异按源语义获得满分。
    """

    actual = SpreadsheetArtifactValue(
        sheets=(
            SpreadsheetSheetValue(
                name="AgentFirst",
                columns=("name", "score"),
                rows=(("Ada", 0.123456),),
            ),
        )
    )
    gold = SpreadsheetArtifactValue(
        sheets=(
            SpreadsheetSheetValue(
                name="GoldFirst",
                columns=("name", "score"),
                rows=(("Ada", 0.123454),),
            ),
        )
    )

    result = evaluate_artifact_metric(
        "sheet-data.first-sheet.v1",
        actual=actual,
        gold=gold,
        options={
            "rules": [{"type": "sheet_data", "sheet_idx0": 0, "sheet_idx1": "EI0"}]
        },
    )

    assert result.score == 1.0
    assert result.matched is True


def test_named_sheet_contract_uses_unseen_movies_on_each_side() -> None:
    """验证 named-sheet contract 不会错用 workbook 首页。

    输入参数：
        无；两侧首页故意不同，但 ``unseen_movies`` 页相同。
    输出返回值：
        无；断言固定 RN/EN 名称选择得到满分。
    """

    actual = SpreadsheetArtifactValue(
        sheets=(
            SpreadsheetSheetValue("noise", ("value",), (("actual",),)),
            SpreadsheetSheetValue("unseen_movies", ("title",), (("Arrival",),)),
        )
    )
    gold = SpreadsheetArtifactValue(
        sheets=(
            SpreadsheetSheetValue("noise", ("value",), (("gold",),)),
            SpreadsheetSheetValue("unseen_movies", ("title",), (("Arrival",),)),
        )
    )

    result = evaluate_artifact_metric(
        "sheet-data.named-unseen-movies.v1",
        actual=actual,
        gold=gold,
        options={
            "rules": [
                {
                    "type": "sheet_data",
                    "sheet_idx0": "RNunseen_movies",
                    "sheet_idx1": "ENunseen_movies",
                }
            ]
        },
    )

    assert result.score == 1.0


def test_docx_content_contract_collapses_paragraph_whitespace_exactly() -> None:
    """验证 docx-content 保留源默认 ignore-blanks 精确语义。

    输入参数：
        无；两个强类型文档使用不同段落边界与空白。
    输出返回值：
        无；断言连接段落后的 Unicode 空白折叠使内容精确相等。
    """

    result = evaluate_artifact_metric(
        "docx-content.v1",
        actual=DocumentParagraphValue(("Book   One", "Author\tName")),
        gold=DocumentParagraphValue((" Book One Author", "Name ")),
        options={},
    )

    assert result.score == 1.0


def test_speaker_notes_contract_compares_notes_and_enabled_text_semantics() -> None:
    """验证 speaker-notes contract 比较备注且不要求形状几何/项目符号。

    输入参数：
        无；构造含备注、背景、文本段落和字体投影的一页 PPT。
    输出返回值：
        无；断言备注首尾空白被源语义忽略，其余启用字段一致时满分。
    """

    run = PresentationRunValue(
        font_name="Liberation Sans",
        font_size=180000,
        bold=False,
        italic=None,
        color_rgb="112233",
        underline=None,
        strike="noStrike",
    )
    paragraph = PresentationParagraphValue(
        text="Topic",
        alignment="CENTER",
        level=0,
        runs=(run,),
    )
    shape = PresentationShapeValue(text="Topic", paragraphs=(paragraph,))
    actual = PresentationArtifactValue(
        slides=(PresentationSlideValue("FFFFFF", " Speaker note ", (shape,)),)
    )
    gold = PresentationArtifactValue(
        slides=(PresentationSlideValue("FFFFFF", "Speaker note", (shape,)),)
    )

    result = evaluate_artifact_metric(
        "speaker-notes.no-shape-no-bullets.v1",
        actual=actual,
        gold=gold,
        options={"examine_shape": False, "examine_bullets": False},
    )

    assert result.score == 1.0


def test_restaurant_sheet_fuzzy_contract_applies_each_fixed_range_rule() -> None:
    """验证 restaurant fuzzy contract 依次执行 exact/fuzzy/includes 归一化。

    输入参数：
        无；六行地址使用 Rd/Road 变体，电话使用不同标点。
    输出返回值：
        无；断言固定范围与规则全部通过后得到满分。
    """

    actual_cells = []
    gold_cells = []
    for row in range(1, 7):
        actual_cells.extend(
            (
                SpreadsheetCellValue(f"A{row}", f"Restaurant {row}"),
                SpreadsheetCellValue(f"B{row}", f"{row} Main Rd"),
                SpreadsheetCellValue(f"C{row}", f"+1 (555) 000-00{row}"),
                SpreadsheetCellValue(f"D{row}", f"https://example.test/{row}"),
            )
        )
        gold_cells.extend(
            (
                SpreadsheetCellValue(f"A{row}", f"Restaurant {row}"),
                SpreadsheetCellValue(f"B{row}", f"{row} Main Road"),
                SpreadsheetCellValue(f"C{row}", f"155500000{row}"),
                SpreadsheetCellValue(f"D{row}", f"https://example.test/{row}"),
            )
        )

    result = evaluate_artifact_metric(
        "sheet-fuzzy.restaurant-contacts.v1",
        actual=SpreadsheetArtifactValue(
            (SpreadsheetSheetValue("Sheet1", cells=tuple(actual_cells)),)
        ),
        gold=SpreadsheetArtifactValue(
            (SpreadsheetSheetValue("Sheet1", cells=tuple(gold_cells)),)
        ),
        options={
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
        },
    )

    assert result.score == 1.0


def test_problem_invoice_pdf_contract_scores_extracted_text_with_source_ratio() -> None:
    """验证 PDF content contract 对已提取文本计算源 Indel ratio。

    输入参数：
        无；actual/gold 使用相同的强类型 PDF 文本投影。
    输出返回值：
        无；断言完全相同的非空文本得到满分。
    """

    result = evaluate_artifact_metric(
        "problem-invoice-content.v1",
        actual=PDFTextValue("Invoice #243729\nTotal: $100.00"),
        gold=PDFTextValue("Invoice #243729\nTotal: $100.00"),
        options={},
    )

    assert result.score == 1.0


def test_pdf_archive_contract_requires_exact_names_and_averages_text_scores() -> None:
    """验证 PDF archive contract 先比较成员名闭集再平均文本分数。

    输入参数：
        无；构造两个名称和提取文本均完全相同的章节 PDF 集合。
    输出返回值：
        无；断言成员名对齐后两项 ratio 平均为满分。
    """

    archive = PDFArchiveValue(
        (
            PDFArchiveMemberValue("chapter-01.pdf", PDFTextValue("One")),
            PDFArchiveMemberValue("chapter-02.pdf", PDFTextValue("Two")),
        )
    )
    result = evaluate_artifact_metric(
        "pdf-chapter-archive.v1",
        actual=archive,
        gold=archive,
        options={"file_type": "pdf"},
    )

    assert result.score == 1.0


@pytest.mark.parametrize(
    "contract_id",
    (
        "grf-sheet-print.sheet1.v1",
        "supported-rate-sheet-print.sheet1.v1",
    ),
)
def test_sheet_print_contracts_reverse_lines_and_drop_trailing_blanks(
    contract_id: str,
) -> None:
    """验证两个 Sheet1 print contract 共享源 CSV 行归一化语义。

    输入参数：
        contract_id：GRF 或 supported-rate 的版本化 contract 身份。
    输出返回值：
        无；断言逐行 strip、反转与丢弃原文末尾空行后精确相同。
    """

    result = evaluate_artifact_metric(
        contract_id,
        actual=SpreadsheetArtifactValue(
            (SpreadsheetSheetValue("Sheet1", printed_text=" Name,Value \n Ada,1 \n\n"),)
        ),
        gold=SpreadsheetArtifactValue(
            (SpreadsheetSheetValue("Sheet1", printed_text="Name,Value\nAda,1"),)
        ),
        options={
            "rules": [
                {
                    "type": "sheet_print",
                    "sheet_idx0": "RNSheet1",
                    "sheet_idx1": "ENSheet1",
                }
            ]
        },
    )

    assert result.score == 1.0


def test_apa_reference_contract_matches_typed_reference_identity_fields() -> None:
    """验证 APA content-only contract 结合 token 与关键身份字段。

    输入参数：
        无；构造含精确 References 标记和一条 APA 引用的强类型文档。
    输出返回值：
        无；断言作者、标题、年份、来源与 DOI 均一致时得到满分。
    """

    citation = (
        "Doe, J. (2024). Reliable GUI evaluation. Journal of Agents, 1(2), "
        "10-20. https://doi.org/10.1234/example"
    )
    document = DocumentParagraphValue(("Case study", "References", citation))
    result = evaluate_artifact_metric(
        "apa7-references.content-only.base-0_6.v1",
        actual=document,
        gold=document,
        options={"content_only": True, "reference_base_result": 0.6},
    )

    assert result.score == 1.0


def test_slide_background_image_contract_scores_identical_typed_pixels() -> None:
    """验证 slide background image contract 结合 RGB SSIM 与 HSV 直方图。

    输入参数：
        无；构造已按源 min-size/Lanczos 规则联合归一化的 7×7 红色像素。
    输出返回值：
        无；断言空间结构与色相/饱和度分布均完全相同时满分。
    """

    pixel_count = 7 * 7
    image = NormalizedRGBImageValue(
        width=7,
        height=7,
        rgb_pixels=bytes((255, 0, 0)) * pixel_count,
        hsv_pixels=bytes((0, 255, 255)) * pixel_count,
    )
    result = evaluate_artifact_metric(
        "slide-index-1.frame-00-08.v1",
        actual=image,
        gold=image,
        options={"score_threshold": 0.90},
    )

    assert result.score == 1.0


def test_settings_real_threshold_calibration_separates_wrong_and_valid_images(
    tmp_path: Path,
) -> None:
    """验证 0.90 阈值区分错误视频时刻并容纳合理图像变换。

    输入参数：
        tmp_path：pytest 提供的仓库外隔离临时目录，用于生成 JPEG q85
            与 1280×720 Lanczos PNG，不持久化任何私有图像。
    输出返回值：
        无；旧 9.042 秒图和 8.508 秒帧低于 0.90，8.208 秒帧、
        JPEG q85 与等比例缩放图高于或等于 0.90。
    """

    pillow_image = pytest.importorskip("PIL.Image")
    fixtures = _load_settings_threshold_fixture_bytes()
    gold_bytes = fixtures["frame_240"]
    jpeg_path = tmp_path / "frame-240-q85.jpg"
    resized_path = tmp_path / "frame-240-1280x720.png"
    with pillow_image.open(BytesIO(gold_bytes)) as source:
        rgb = source.convert("RGB")
        try:
            rgb.save(jpeg_path, format="JPEG", quality=85)
            resized = rgb.resize(
                (1280, 720),
                resample=pillow_image.Resampling.LANCZOS,
            )
            try:
                resized.save(resized_path, format="PNG")
            finally:
                resized.close()
        finally:
            rgb.close()

    scores = {
        "old_9_042": _evaluate_settings_background_bytes(
            fixtures["old_9_042"],
            gold_bytes,
        ),
        "frame_246": _evaluate_settings_background_bytes(
            fixtures["frame_246"],
            gold_bytes,
        ),
        "frame_255": _evaluate_settings_background_bytes(
            fixtures["frame_255"],
            gold_bytes,
        ),
        "jpeg_q85": _evaluate_settings_background_bytes(
            jpeg_path.read_bytes(),
            gold_bytes,
        ),
        "resize_1280x720": _evaluate_settings_background_bytes(
            resized_path.read_bytes(),
            gold_bytes,
        ),
    }

    assert scores["old_9_042"] == pytest.approx(0.7960269769984115)
    assert scores["frame_246"] == pytest.approx(0.9104283157114637)
    assert scores["old_9_042"] < 0.90
    assert scores["frame_255"] < 0.90
    assert scores["frame_246"] >= 0.90
    assert scores["jpeg_q85"] >= 0.90
    assert scores["resize_1280x720"] >= 0.90
