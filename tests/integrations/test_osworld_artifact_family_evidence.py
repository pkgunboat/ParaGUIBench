"""13 个 legacy OSWorld artifact family 取证切片的公共行为测试。"""

from __future__ import annotations

from io import BytesIO
import zipfile

from PIL import Image

from paraguibench.integrations.osworld.artifact_family_evidence import (
    LEGACY_OSWORLD_ARTIFACT_TASK_IDS,
    OSWorldArtifactFamilyEvidenceSource,
    evaluate_inline_directory_membership,
)
from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)
from paraguibench.integrations.osworld.controller import (
    OSWorldGuestPathMissingError,
)


_DIRECTORY_TASK_ID = "Operation-FileOperate-CombinationDocs-011"
_SINGLE_FILE_TASK_ID = "Operation-FileOperate-CombinationDocs-010"
_FILE_BUNDLE_TASK_ID = "Operation-FileOperate-CombinationDocs-013"
_PPTX_BACKGROUND_TASK_ID = "Operation-FileOperate-Settings-001"
_PDF_ARCHIVE_TASK_ID = "Operation-FileOperate-BatchOperation-003"


def test_legacy_family_allowlist_is_exactly_the_thirteen_unwired_specs() -> None:
    """验证专属 source 不会漏收或越权接管已生产化的 artifact task。

    输入参数：
        无；比较公共 spec catalog 与两个既有 production task。
    输出返回值：
        无；allowlist 必须恰好等于剩余 13 项。
    """

    already_production = {
        "Operation-FileOperate-BatchOperation-001",
        "Operation-FileOperate-CombinationDocs-015",
    }
    assert LEGACY_OSWORLD_ARTIFACT_TASK_IDS == (
        set(OSWORLD_ARTIFACT_EVIDENCE_SPECS) - already_production
    )
    assert len(LEGACY_OSWORLD_ARTIFACT_TASK_IDS) == 13


class _DirectoryController:
    """模拟受控单层目录 getter 的 guest 边界。"""

    def __init__(self, entries: tuple[str, ...]) -> None:
        """保存 getter 返回的成员闭集和调用记录。

        输入参数：
            entries：按 controller 固定顺序返回的直接成员名。
        输出返回值：
            无；初始化空调用记录。
        """

        self._entries = entries
        self.calls: list[dict[str, object]] = []

    def list_directory(
        self,
        guest_path: str,
        *,
        max_entries: int,
        max_name_bytes: int,
        max_response_bytes: int,
    ) -> tuple[str, ...]:
        """返回预设目录成员并记录全部安全上限。

        输入参数：
            guest_path：由冻结 guest shared binding 推导的目录。
            max_entries/max_name_bytes/max_response_bytes：版本化资源上限。
        输出返回值：
            构造时保存的成员 tuple。
        """

        self.calls.append(
            {
                "guest_path": guest_path,
                "max_entries": max_entries,
                "max_name_bytes": max_name_bytes,
                "max_response_bytes": max_response_bytes,
            }
        )
        return self._entries


class _FileController:
    """模拟 controller 的 nofollow 单文件实际值 getter。"""

    def __init__(self, content_by_path: dict[str, bytes]) -> None:
        """保存 guest 路径到原始字节的测试映射。

        输入参数：
            content_by_path：只在 fake 边界内使用的路径与内容映射。
        输出返回值：
            无；初始化空调用记录。
        """

        self._content_by_path = dict(content_by_path)
        self.calls: list[dict[str, object]] = []

    def collect_file_bytes(
        self,
        guest_path: str,
        *,
        max_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        """按调用路径返回预设字节并记录全部边界。

        输入参数：
            guest_path：冻结 spec 推导的文件路径。
            max_bytes/max_response_bytes/timeout_seconds：固定资源上限。
        输出返回值：
            对应路径的不可变 bytes。
        """

        self.calls.append(
            {
                "guest_path": guest_path,
                "max_bytes": max_bytes,
                "max_response_bytes": max_response_bytes,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._content_by_path[guest_path]


class _AllFamilyController:
    """为 13 项 getter coverage 提供按媒体 family 选择的边界 fake。"""

    def __init__(self, *, pdf_archive: bytes, pptx: bytes) -> None:
        """保存两个需要真实容器解析的测试载荷。

        输入参数：
            pdf_archive：BatchOperation-003 的最小 ZIP。
            pptx：Settings-001 的最小 OOXML 容器。
        输出返回值：
            无；初始化文件与目录调用计数。
        """

        self._pdf_archive = pdf_archive
        self._pptx = pptx
        self.file_calls = 0
        self.directory_calls = 0

    def collect_file_bytes(
        self,
        guest_path: str,
        *,
        max_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        """依据固定路径后缀返回容器或普通文件测试字节。

        输入参数：
            guest_path：spec 推导的 guest 文件路径。
            max_bytes/max_response_bytes/timeout_seconds：调用方安全上限。
        输出返回值：
            两类需解析容器的真实 bytes，或普通 family 的占位 bytes。
        """

        assert max_bytes > 0
        assert max_response_bytes <= 16_777_216
        assert timeout_seconds == 30.0
        self.file_calls += 1
        if guest_path.endswith("/book/book.zip"):
            return self._pdf_archive
        if guest_path.endswith("/Robotic_Workshop_Infographics.pptx"):
            return self._pptx
        return b"bounded-family-payload"

    def list_directory(
        self,
        guest_path: str,
        *,
        max_entries: int,
        max_name_bytes: int,
        max_response_bytes: int,
    ) -> tuple[str, ...]:
        """返回满足内联规则的排序目录成员。

        输入参数：
            guest_path：spec 推导的 guest 目录路径。
            max_entries/max_name_bytes/max_response_bytes：固定资源上限。
        输出返回值：
            唯一应保留的 invoice 名称 tuple。
        """

        assert guest_path.endswith("/Desktop/problematic")
        assert (max_entries, max_name_bytes, max_response_bytes) == (
            64,
            255,
            1_048_576,
        )
        self.directory_calls += 1
        return ("Invoice # 243729.pdf",)


class _SecondBundleFileMissingController(_FileController):
    """模拟 bundle 第一项可读、第二项由 typed ENOENT 缺失。"""

    def collect_file_bytes(
        self,
        guest_path: str,
        *,
        max_bytes: int,
        max_response_bytes: int,
        timeout_seconds: float,
    ) -> bytes:
        """在 CSV 路径抛 typed missing，其余调用复用父类。

        输入参数：
            guest_path：当前 bundle 成员路径。
            max_bytes/max_response_bytes/timeout_seconds：固定资源上限。
        输出返回值：
            第一项返回父类 bytes；第二项永不返回。
        """

        if guest_path.endswith(".csv"):
            raise OSWorldGuestPathMissingError("guest artifact 文件缺失")
        return super().collect_file_bytes(
            guest_path,
            max_bytes=max_bytes,
            max_response_bytes=max_response_bytes,
            timeout_seconds=timeout_seconds,
        )


def _pptx_with_slide_background(
    *,
    relationship_target: str = "../media/image1.png",
    target_mode: str | None = None,
    extra_members: tuple[tuple[str, bytes], ...] = (),
) -> tuple[bytes, bytes]:
    """构造第二页直接背景关系指向内嵌 PNG 的最小 PPTX 容器。

    输入参数：
        relationship_target：slide relationship 的候选 target。
        target_mode：可选 Relationship TargetMode。
        extra_members：用于安全边界测试的额外 ZIP 成员。
    输出返回值：
        完整 ZIP bytes 与其中预期被提取的 PNG bytes。
    """

    image_buffer = BytesIO()
    Image.new("RGB", (2, 2), color=(17, 29, 43)).save(
        image_buffer,
        format="PNG",
    )
    image_bytes = image_buffer.getvalue()
    slide_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:cSld><p:bg><p:bgPr><a:blipFill><a:blip r:embed="rId7"/></a:blipFill></p:bgPr></p:bg></p:cSld>
</p:sld>"""
    target_mode_attribute = (
        "" if target_mode is None else f' TargetMode="{target_mode}"'
    )
    rels_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId7"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="{relationship_target}"{target_mode_attribute}/>
</Relationships>""".encode("utf-8")
    archive_buffer = BytesIO()
    with zipfile.ZipFile(
        archive_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("ppt/slides/slide2.xml", slide_xml)
        archive.writestr("ppt/slides/_rels/slide2.xml.rels", rels_xml)
        archive.writestr("ppt/media/image1.png", image_bytes)
        for member_name, member_content in extra_members:
            archive.writestr(member_name, member_content)
    return archive_buffer.getvalue(), image_bytes


def _pdf_archive() -> tuple[bytes, tuple[tuple[str, bytes], ...]]:
    """构造成员写入顺序与名称排序相反的最小 PDF ZIP。

    输入参数：
        无。
    输出返回值：
        ZIP bytes，以及按旧 compare_archive 名称排序后的成员 tuple。
    """

    members = (
        ("Chapter 1.pdf", b"%PDF-1.4\nchapter-one\n%%EOF"),
        ("Chapter 2.pdf", b"%PDF-1.4\nchapter-two\n%%EOF"),
    )
    archive_buffer = BytesIO()
    with zipfile.ZipFile(
        archive_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for name, content in reversed(members):
            archive.writestr(name, content)
    return archive_buffer.getvalue(), members


def test_directory_membership_slice_collects_and_scores_without_repr_leak() -> None:
    """验证目录成员取证可直接形成脱敏 inline metric observation。

    输入参数：
        无；使用真实冻结 spec 和 fake guest controller。
    输出返回值：
        无；断言动态 home、资源上限、源语义分数与 repr 脱敏同时成立。
    """

    entries = (
        "Invoice # 243729.pdf",
        "unrelated.txt",
    )
    controller = _DirectoryController(entries)

    captures = OSWorldArtifactFamilyEvidenceSource().capture(
        _DIRECTORY_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-run/shared",
    )

    directory_capture = next(
        capture
        for capture in captures
        if capture.slot_id == "problematic_directory_membership"
    )
    observation = evaluate_inline_directory_membership(
        _DIRECTORY_TASK_ID,
        directory_capture,
    )

    assert controller.calls == [
        {
            "guest_path": "/srv/paraguibench-run/Desktop/problematic",
            "max_entries": 64,
            "max_name_bytes": 255,
            "max_response_bytes": 1_048_576,
        }
    ]
    assert (observation.slot_id, observation.status) == (
        "problematic_directory_membership",
        "available",
    )
    assert tuple(
        (metric.metric_id, metric.score) for metric in observation.metric_scores
    ) == (("check_include_exclude", 1.0),)
    assert all(entry not in repr(directory_capture) for entry in entries)


def test_single_file_family_uses_frozen_home_and_controller_envelope_limit() -> None:
    """验证单文件 family 在 HTTP envelope 内有界捕获实际值。

    输入参数：
        无；使用真实 grades.xlsx spec 与 fake controller。
    输出返回值：
        无；断言 guest 路径、raw/envelope/timeout 上限及 repr 脱敏。
    """

    content = b"synthetic-xlsx-content"
    controller = _FileController({"/srv/paraguibench-test/exam/grades.xlsx": content})

    captures = OSWorldArtifactFamilyEvidenceSource().capture(
        _SINGLE_FILE_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )

    assert len(captures) == 1
    capture = captures[0]
    assert (capture.slot_id, capture.status, capture.payload_kind) == (
        "graded_workbook",
        "available",
        "file",
    )
    assert capture.file_items() == (content,)
    assert content.decode("ascii") not in repr(capture)
    assert controller.calls == [
        {
            "guest_path": "/srv/paraguibench-test/exam/grades.xlsx",
            "max_bytes": 12_579_840,
            "max_response_bytes": 16_777_216,
            "timeout_seconds": 30.0,
        }
    ]


def test_file_bundle_family_preserves_spec_order_and_discards_paths_from_repr() -> None:
    """验证双文件 getter 按冻结 locator 顺序形成同槽位原子 capture。

    输入参数：
        无；使用真实 XLSX+CSV bundle spec 与 fake controller。
    输出返回值：
        无；断言顺序、路径、资源边界及 capture repr 脱敏。
    """

    xlsx = b"synthetic-grf-xlsx"
    csv = b"Year,Applied,Supported,Success Rate\n"
    controller = _FileController(
        {
            "/srv/paraguibench-test/Desktop/GRF-p5y.xlsx": xlsx,
            "/srv/paraguibench-test/Desktop/GRF-p5y-Sheet1.csv": csv,
        }
    )

    captures = OSWorldArtifactFamilyEvidenceSource().capture(
        _FILE_BUNDLE_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )

    assert len(captures) == 1
    capture = captures[0]
    assert (capture.status, capture.payload_kind, capture.file_items()) == (
        "available",
        "file-bundle",
        (xlsx, csv),
    )
    assert "GRF-p5y" not in repr(capture)
    assert controller.calls == [
        {
            "guest_path": "/srv/paraguibench-test/Desktop/GRF-p5y.xlsx",
            "max_bytes": 12_579_840,
            "max_response_bytes": 16_777_216,
            "timeout_seconds": 30.0,
        },
        {
            "guest_path": "/srv/paraguibench-test/Desktop/GRF-p5y-Sheet1.csv",
            "max_bytes": 12_579_840,
            "max_response_bytes": 16_777_216,
            "timeout_seconds": 30.0,
        },
    ]


def test_file_bundle_discards_first_item_when_second_item_is_missing() -> None:
    """验证双文件槽位不会把部分实际值冒充完整 bundle。

    输入参数：
        无；第一项可读、第二项抛 controller typed missing。
    输出返回值：
        无；整个槽位为 missing，已读第一项不保留在 capture。
    """

    controller = _SecondBundleFileMissingController(
        {"/srv/paraguibench-test/Desktop/GRF-p5y.xlsx": (b"first-item-secret")}
    )

    capture = OSWorldArtifactFamilyEvidenceSource().capture(
        _FILE_BUNDLE_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )[0]

    assert (capture.status, capture.file_items(), capture.member_names()) == (
        "missing",
        (),
        (),
    )
    assert "first-item-secret" not in repr(capture)


def test_pptx_background_family_extracts_only_the_bound_internal_image() -> None:
    """验证第二页背景 getter 从受限 PPTX 关系中提取内嵌图像。

    输入参数：
        无；构造不依赖外部文件的最小 OOXML ZIP。
    输出返回值：
        无；断言 slide index、内部 relationship、字节和 repr 脱敏。
    """

    pptx_bytes, image_bytes = _pptx_with_slide_background()
    controller = _FileController(
        {
            (
                "/srv/paraguibench-test/Desktop/Robotic_Workshop_Infographics.pptx"
            ): pptx_bytes
        }
    )

    captures = OSWorldArtifactFamilyEvidenceSource().capture(
        _PPTX_BACKGROUND_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )

    assert len(captures) == 1
    capture = captures[0]
    assert (capture.status, capture.payload_kind, capture.file_items()) == (
        "available",
        "image",
        (image_bytes,),
    )
    assert "image1.png" not in repr(capture)
    assert image_bytes.hex()[:32] not in repr(capture)


def test_pptx_background_family_rejects_external_file_relationship() -> None:
    """验证背景 getter 不复现旧实现的 guest 外部 file:// 读取面。

    输入参数：
        无；构造指向外部本地文件的 Relationship。
    输出返回值：
        无；capture 必须是脱敏 parse_error，且不返回任何外部字节。
    """

    pptx_bytes, _image_bytes = _pptx_with_slide_background(
        relationship_target="file:///tmp/secret.png",
        target_mode="External",
    )
    controller = _FileController(
        {
            (
                "/srv/paraguibench-test/Desktop/Robotic_Workshop_Infographics.pptx"
            ): pptx_bytes
        }
    )

    capture = OSWorldArtifactFamilyEvidenceSource().capture(
        _PPTX_BACKGROUND_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )[0]

    assert (capture.status, capture.file_items()) == ("parse_error", ())
    assert "/tmp/secret.png" not in repr(capture)


def test_pdf_archive_family_parses_direct_members_in_source_comparison_order() -> None:
    """验证 PDF archive getter 安全展开并按旧比较器名称顺序投影。

    输入参数：
        无；使用两份最小 PDF 和真实 BatchOperation-003 spec。
    输出返回值：
        无；断言成员名/字节顺序可供可信 metric 使用但不进入 repr。
    """

    archive_bytes, members = _pdf_archive()
    controller = _FileController(
        {"/srv/paraguibench-test/Desktop/book/book.zip": archive_bytes}
    )

    capture = OSWorldArtifactFamilyEvidenceSource().capture(
        _PDF_ARCHIVE_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )[0]

    assert (capture.status, capture.payload_kind) == (
        "available",
        "file-bundle",
    )
    assert capture.member_names() == tuple(name for name, _content in members)
    assert capture.file_items() == tuple(content for _name, content in members)
    assert all(name not in repr(capture) for name, _content in members)
    assert all(
        content.decode("ascii") not in repr(capture) for _name, content in members
    )


def test_pdf_archive_rejects_path_traversal_without_leaking_member_name() -> None:
    """验证 archive parser 在解压前拒绝 ``..`` 成员路径。

    输入参数：
        无；构造仅含越界 PDF 名称的 ZIP。
    输出返回值：
        无；返回 parse_error，不回显恶意名称或文件内容。
    """

    archive_buffer = BytesIO()
    with zipfile.ZipFile(archive_buffer, mode="w") as archive:
        archive.writestr("../secret.pdf", b"%PDF-1.4\nsecret\n%%EOF")
    controller = _FileController(
        {"/srv/paraguibench-test/Desktop/book/book.zip": (archive_buffer.getvalue())}
    )

    capture = OSWorldArtifactFamilyEvidenceSource().capture(
        _PDF_ARCHIVE_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )[0]

    assert (capture.status, capture.file_items(), capture.member_names()) == (
        "parse_error",
        (),
        (),
    )
    assert "secret.pdf" not in repr(capture)


def test_all_thirteen_specs_have_a_local_bounded_getter_family() -> None:
    """验证 13 项 frozen spec 已无“getter family 未实现”分支。

    输入参数：
        无；对每个 task 使用真实 spec 与按系统边界构造的 fake controller。
    输出返回值：
        无；所有槽位均 available，且不会要求 finalize/Agent/gold 接口。
    """

    archive_bytes, _members = _pdf_archive()
    pptx_bytes, _image_bytes = _pptx_with_slide_background()
    controller = _AllFamilyController(
        pdf_archive=archive_bytes,
        pptx=pptx_bytes,
    )
    source = OSWorldArtifactFamilyEvidenceSource()

    captures_by_task = {
        task_id: source.capture(
            task_id,
            controller,
            guest_shared_dir="/srv/paraguibench-test/shared",
        )
        for task_id in sorted(LEGACY_OSWORLD_ARTIFACT_TASK_IDS)
    }

    assert set(captures_by_task) == set(LEGACY_OSWORLD_ARTIFACT_TASK_IDS)
    assert all(
        capture.status == "available"
        for captures in captures_by_task.values()
        for capture in captures
    )
    assert controller.file_calls == 15
    assert controller.directory_calls == 1


def test_directory_getter_maps_non_string_member_to_schema_error() -> None:
    """验证不可信 controller 返回错误成员类型时不会泄出 Python 异常。

    输入参数：
        无；fake 返回一个没有 ``encode`` 的 object。
    输出返回值：
        无；目录槽位安全收敛为 schema_error 且不执行 metric。
    """

    controller = _DirectoryController((object(),))  # type: ignore[arg-type]

    captures = OSWorldArtifactFamilyEvidenceSource().capture(
        _DIRECTORY_TASK_ID,
        controller,
        guest_shared_dir="/srv/paraguibench-test/shared",
    )
    capture = next(
        item for item in captures if item.slot_id == "problematic_directory_membership"
    )

    assert (capture.status, capture.directory_members()) == (
        "schema_error",
        (),
    )
