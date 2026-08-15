"""Word-009/010 从固定输入到输出的 DOCX 正文保真快照。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import struct
from urllib.parse import unquote
import xml.etree.ElementTree as ET
import zipfile


_OPERATION_PROTOCOL_ID = "paraguibench.operation.eval-rules.v1"
_SUPPORTED_TASK_IDS = frozenset(
    {
        "Operation-FileOperate-BatchOperationWord-009",
        "Operation-FileOperate-BatchOperationWord-010",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_DOCX_BYTES = 32 * 1024 * 1024
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_OFFICE_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_OFFICE_MATH_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_PACKAGE_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
_PACKAGE_RELATIONSHIPS_ROOT_TAG = f"{{{_PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationships"
_CONTENT_TYPES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/content-types"
)
_CONTENT_TYPES_ROOT_TAG = f"{{{_CONTENT_TYPES_NAMESPACE}}}Types"
_CONTENT_TYPES_DEFAULT_TAG = f"{{{_CONTENT_TYPES_NAMESPACE}}}Default"
_CONTENT_TYPES_OVERRIDE_TAG = f"{{{_CONTENT_TYPES_NAMESPACE}}}Override"
_WORD_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_OFFICE_DOCUMENT_RELATIONSHIP_TYPES = frozenset(
    {
        (
            "http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/officeDocument"
        ),
        ("http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"),
    }
)
_DRAWINGML_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CHART_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_DIAGRAM_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_DIAGRAM_DRAWING_NAMESPACE = "http://schemas.microsoft.com/office/drawing/2008/diagram"
_VML_NAMESPACE = "urn:schemas-microsoft-com:vml"
_WORDPROCESSING_DRAWING_NAMESPACE = (
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
)
_MARKUP_COMPATIBILITY_NAMESPACE = (
    "http://schemas.openxmlformats.org/markup-compatibility/2006"
)
_KNOWN_TEXT_STRUCTURE_NAMESPACES = frozenset(
    {
        _WORD_NAMESPACE,
        _OFFICE_MATH_NAMESPACE,
        _DRAWINGML_NAMESPACE,
        _CHART_NAMESPACE,
        _DIAGRAM_NAMESPACE,
        _DIAGRAM_DRAWING_NAMESPACE,
        _VML_NAMESPACE,
        _WORDPROCESSING_DRAWING_NAMESPACE,
        "http://schemas.openxmlformats.org/drawingml/2006/picture",
        _MARKUP_COMPATIBILITY_NAMESPACE,
        "http://schemas.microsoft.com/office/drawing/2010/main",
        ("http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"),
        ("http://schemas.microsoft.com/office/word/2010/wordprocessingShape"),
    }
)
_NON_VISIBLE_DRAWING_SCALAR_TEXT_TAGS = frozenset(
    f"{{{_WORDPROCESSING_DRAWING_NAMESPACE}}}{name}" for name in ("align", "posOffset")
)
_XML_SPACE_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}space"
_RUN_TAG = f"{{{_WORD_NAMESPACE}}}r"
_RUN_PROPERTIES_TAG = f"{{{_WORD_NAMESPACE}}}rPr"
_PARAGRAPH_TAG = f"{{{_WORD_NAMESPACE}}}p"
_PARAGRAPH_PROPERTIES_TAG = f"{{{_WORD_NAMESPACE}}}pPr"
_STYLE_TAG = f"{{{_WORD_NAMESPACE}}}style"
_BASED_ON_TAG = f"{{{_WORD_NAMESPACE}}}basedOn"
_PARAGRAPH_STYLE_TAG = f"{{{_WORD_NAMESPACE}}}pStyle"
_RUN_STYLE_TAG = f"{{{_WORD_NAMESPACE}}}rStyle"
_STYLE_ID_ATTRIBUTE = f"{{{_WORD_NAMESPACE}}}styleId"
_STYLE_TYPE_ATTRIBUTE = f"{{{_WORD_NAMESPACE}}}type"
_STYLE_DEFAULT_ATTRIBUTE = f"{{{_WORD_NAMESPACE}}}default"
_STYLE_VALUE_ATTRIBUTE = f"{{{_WORD_NAMESPACE}}}val"
_ABSTRACT_NUMBERING_TAG = f"{{{_WORD_NAMESPACE}}}abstractNum"
_NUMBERING_INSTANCE_TAG = f"{{{_WORD_NAMESPACE}}}num"
_ABSTRACT_NUMBERING_ID_ATTRIBUTE = f"{{{_WORD_NAMESPACE}}}abstractNumId"
_NUMBERING_ID_ATTRIBUTE = f"{{{_WORD_NAMESPACE}}}numId"
_NUMBERING_ID_TAG = f"{{{_WORD_NAMESPACE}}}numId"
_ABSTRACT_NUMBERING_ID_TAG = f"{{{_WORD_NAMESPACE}}}abstractNumId"
_NUMBERING_LEVEL_TAG = f"{{{_WORD_NAMESPACE}}}lvl"
_NUMBERING_LEVEL_OVERRIDE_TAG = f"{{{_WORD_NAMESPACE}}}lvlOverride"
_NUMBERING_LEVEL_ID_ATTRIBUTE = f"{{{_WORD_NAMESPACE}}}ilvl"
_NUMBERING_LEVEL_ID_TAG = f"{{{_WORD_NAMESPACE}}}ilvl"
_NUMBERING_VOLATILE_TAGS = frozenset(
    f"{{{_WORD_NAMESPACE}}}{name}" for name in ("nsid", "tmpl")
)
_RELATIONSHIP_TAG = f"{{{_PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationship"
_RELATIONSHIP_REFERENCE_ATTRIBUTES = frozenset(
    f"{{{_OFFICE_RELATIONSHIPS_NAMESPACE}}}{name}" for name in ("id", "embed", "link")
)
_NUMBERING_PROPERTIES_TAG = f"{{{_WORD_NAMESPACE}}}numPr"
_STYLES_ROOT_TAG = f"{{{_WORD_NAMESPACE}}}styles"
_NUMBERING_ROOT_TAG = f"{{{_WORD_NAMESPACE}}}numbering"
_SELECTED_RUN_PROPERTY_TAGS = frozenset(
    f"{{{_WORD_NAMESPACE}}}{name}"
    for name in ("vanish", "webHidden", "color", "sz", "szCs", "rtl")
)
_HYPERLINK_TAG = f"{{{_WORD_NAMESPACE}}}hyperlink"
_FIELD_SIMPLE_TAG = f"{{{_WORD_NAMESPACE}}}fldSimple"
_DIRECTION_CONTAINER_TAGS = frozenset(
    f"{{{_WORD_NAMESPACE}}}{name}" for name in ("bdo", "dir")
)
_SEMANTIC_ENTRY_CONTAINER_TAGS = frozenset(
    f"{{{_WORD_NAMESPACE}}}{name}" for name in ("footnote", "endnote", "comment")
)
_MARKUP_COMPATIBILITY_CONTAINER_TAGS = frozenset(
    f"{{{_MARKUP_COMPATIBILITY_NAMESPACE}}}{name}"
    for name in ("AlternateContent", "Choice", "Fallback")
)
_RELATIONSHIP_ID_ATTRIBUTE = f"{{{_OFFICE_RELATIONSHIPS_NAMESPACE}}}id"
_TEXT_NODE_KINDS = {
    f"{{{_WORD_NAMESPACE}}}t": "w:t",
    f"{{{_WORD_NAMESPACE}}}delText": "w:delText",
    f"{{{_WORD_NAMESPACE}}}instrText": "w:instrText",
    f"{{{_WORD_NAMESPACE}}}delInstrText": "w:delInstrText",
    f"{{{_OFFICE_MATH_NAMESPACE}}}t": "m:t",
    f"{{{_DRAWINGML_NAMESPACE}}}t": "a:t",
    f"{{{_CHART_NAMESPACE}}}v": "c:v",
    f"{{{_CHART_NAMESPACE}}}f": "c:f",
}
_CONTROL_NODE_KINDS = {
    f"{{{_WORD_NAMESPACE}}}{name}": f"w:{name}"
    for name in (
        "tab",
        "br",
        "cr",
        "noBreakHyphen",
        "softHyphen",
        "sym",
        "fldChar",
        "footnoteReference",
        "endnoteReference",
        "commentReference",
        "commentRangeStart",
        "commentRangeEnd",
        "separator",
        "continuationSeparator",
        "lastRenderedPageBreak",
    )
}
_VML_TEXTPATH_TAG = f"{{{_VML_NAMESPACE}}}textpath"
_UNSUPPORTED_CARRIER_TAGS = frozenset(
    f"{{{_WORD_NAMESPACE}}}{name}" for name in ("altChunk", "object", "subDoc")
)
_BASIC_CONTAINER_TAGS = (
    frozenset(
        f"{{{_WORD_NAMESPACE}}}{name}"
        for name in (
            "p",
            "tbl",
            "tr",
            "tc",
            "hyperlink",
            "ins",
            "del",
            "moveFrom",
            "moveTo",
            "sdt",
            "smartTag",
            "customXml",
            "txbxContent",
            "fldSimple",
            "footnote",
            "endnote",
            "comment",
            "bdo",
            "dir",
        )
    )
    | _MARKUP_COMPATIBILITY_CONTAINER_TAGS
)
_TEXT_PART_PATTERN = re.compile(
    r"^word/(?:"
    r"document|header[0-9]+|footer[0-9]+|footnotes|endnotes|comments|"
    r"glossary/document|charts/chart[0-9]+|"
    r"diagrams/(?:data|drawing)[0-9]+"
    r")\.xml$"
)
_IMAGE_RELATIONSHIP_SUFFIX = "/image"
_TEXT_SEMANTIC_RELATIONSHIP_TYPES = frozenset(
    {
        "header",
        "footer",
        "footnotes",
        "endnotes",
        "comments",
        "hyperlink",
        "styles",
        "numbering",
        "glossaryDocument",
        "chart",
        "diagramData",
        "diagramDrawing",
    }
)


class WordTextFidelityError(RuntimeError):
    """表示 typed DOCX baseline 无法安全构造或比较。

    输入参数：
        code：不含文件名、路径、摘要或正文的固定错误码。
    输出返回值：
        可由 runtime 映射为 ``ERROR/null`` 的脱敏异常。
    """

    def __init__(self, code: str) -> None:
        """构造一个只暴露固定错误码的异常。

        输入参数：
            code：调用方选择的白名单错误码。
        输出返回值：
            无；保存 ``code`` 并以它作为异常消息。
        """

        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class WordTextInputFile:
    """描述正式 input manifest 中一个固定文件。

    输入参数：
        path/size/sha256：manifest 中的 POSIX 相对路径、字节数与摘要；
        is_docx：是否要对该文件生成 typed 正文快照。
    输出返回值：
        不可变的文件身份；构造时执行基本 schema 校验。
    """

    path: str
    size: int
    sha256: str
    is_docx: bool

    def __post_init__(self) -> None:
        """拒绝不安全路径和非规范文件身份。

        输入参数：
            无；读取当前 dataclass 字段。
        输出返回值：
            无；无效身份抛出 ``ValueError``。
        """

        relative = PurePosixPath(self.path)
        if (
            not isinstance(self.path, str)
            or not self.path
            or "\\" in self.path
            or "\x00" in self.path
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.as_posix() != self.path
            or not isinstance(self.size, int)
            or isinstance(self.size, bool)
            or not 0 <= self.size <= _MAX_DOCX_BYTES
            or not isinstance(self.sha256, str)
            or _SHA256_PATTERN.fullmatch(self.sha256) is None
            or not isinstance(self.is_docx, bool)
        ):
            raise ValueError("Word typed input 文件身份无效")
        if self.is_docx and relative.suffix.casefold() != ".docx":
            raise ValueError("Word typed input 文档扩展名无效")


@dataclass(frozen=True, slots=True)
class _DocumentTextSnapshot:
    """保存单份 DOCX 的内部脱敏 typed token 摘要。"""

    path: str
    digest: str
    token_count: int
    part_count: int
    relationship_digest: str
    image_relationships: tuple[tuple[str, str, str], ...]
    media_identities: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _Relationship:
    """保存一条已规范化的 internal OPC relationship。"""

    source_part: str
    relationship_type: str
    target_part: str


@dataclass(frozen=True, slots=True)
class _RelationshipGraph:
    """保存关系 ID 解析、可达部件与稳定语义投影。"""

    by_source: dict[str, dict[str, _Relationship]]
    reachable_parts: frozenset[str]
    relationship_digest: str
    image_relationships: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class _StyleContext:
    """保存已验证唯一性的 DOCX 样式图。"""

    styles: dict[str, ET.Element]
    default_paragraph_style_id: str | None
    default_paragraph_properties: ET.Element | None
    default_run_properties: ET.Element | None


@dataclass(frozen=True, slots=True)
class _NumberingContext:
    """保存已验证唯一性的 numbering 实例与 abstract 图。"""

    abstract_definitions: dict[str, ET.Element]
    numbering_instances: dict[str, ET.Element]


@dataclass(frozen=True, slots=True)
class _ContentTypeTable:
    """保存规范化且唯一的 OPC Default/Override 映射。"""

    defaults: dict[str, str]
    overrides: dict[str, str]


@dataclass(frozen=True, slots=True, repr=False)
class WordTextBaseline:
    """保存不含正文的 pre DOCX typed baseline。

    输入参数：
        task_id/protocol_id/manifest_sha256：正式任务、协议与 manifest 身份；
        documents：每份 DOCX 的私有路径映射与 token 摘要。
    输出返回值：
        evaluator-only 不可变 DTO；``repr`` 不暴露路径或摘要。
    """

    task_id: str
    protocol_id: str
    manifest_sha256: str
    documents: tuple[_DocumentTextSnapshot, ...]

    def __repr__(self) -> str:
        """返回不含文件名、路径、正文和摘要的表示。

        输入参数：
            无。
        输出返回值：
            仅包含 task/protocol 与文档计数的字符串。
        """

        return (
            "WordTextBaseline("
            f"task_id={self.task_id!r}, protocol_id={self.protocol_id!r}, "
            f"document_count={len(self.documents)!r})"
        )


@dataclass(frozen=True, slots=True)
class WordTextFidelityResult:
    """保存 pre/post 可比较时的脱敏结果。

    输入参数：
        matched：所有文档 typed token 是否与 baseline 全等；
        document_count：固定比较分母。
    输出返回值：
        可交给聚合 evaluator 的布尔结果与整数计数。
    """

    matched: bool
    document_count: int


def capture_word_text_baseline(
    *,
    task_id: str,
    protocol_id: str,
    manifest_sha256: str,
    source_root: Path,
    files: tuple[WordTextInputFile, ...],
) -> WordTextBaseline:
    """从 manifest 固定 host cache 构造脱敏 typed baseline。

    输入参数：
        task_id/protocol_id/manifest_sha256：当前正式身份；
        source_root：已验证的 host asset cache 根；
        files：完整 manifest 文件闭集，其中 ``is_docx`` 项需快照。
    输出返回值：
        只含脱敏 token 摘要的 ``WordTextBaseline``。
    异常：
        WordTextFidelityError：身份、nofollow 读取、size/SHA
            复验或 DOCX 解析任一失败。
    """

    _validate_baseline_identity(task_id, protocol_id, manifest_sha256, files)
    snapshots: list[_DocumentTextSnapshot] = []
    for file in files:
        payload = _read_regular_file_nofollow(
            source_root,
            file.path,
            expected_size=file.size,
            expected_sha256=file.sha256,
        )
        if file.is_docx:
            snapshots.append(_snapshot_document(file.path, payload, task_id=task_id))
    if not snapshots:
        raise WordTextFidelityError("WORD_TEXT_BASELINE_EMPTY")
    return WordTextBaseline(
        task_id=task_id,
        protocol_id=protocol_id,
        manifest_sha256=manifest_sha256,
        documents=tuple(snapshots),
    )


def compare_word_text_fidelity(
    baseline: WordTextBaseline,
    result_root: Path,
) -> WordTextFidelityResult:
    """将 post DOCX typed token 与固定 pre baseline 全等比较。

    输入参数：
        baseline：在 guest 可变更前构造的 evaluator-only DTO；
        result_root：已冻结 post artifact 的 host 根目录。
    输出返回值：
        可比较时返回全等布尔值和固定文档计数；
        任一改字、删字或新增文字都使 ``matched=False``。
    异常：
        WordTextFidelityError：post 文件缺失、非常规文件、
            读取不稳定或 DOCX 不可解析时 fail closed。
    """

    if not isinstance(baseline, WordTextBaseline):
        raise WordTextFidelityError("WORD_TEXT_BASELINE_TYPE_INVALID")
    matched = True
    for expected in baseline.documents:
        payload = _read_regular_file_nofollow(
            result_root,
            expected.path,
            expected_size=None,
            expected_sha256=None,
        )
        observed = _snapshot_document(
            expected.path,
            payload,
            task_id=baseline.task_id,
        )
        matched = matched and _document_snapshots_match(
            expected,
            observed,
            task_id=baseline.task_id,
        )
    return WordTextFidelityResult(
        matched=matched,
        document_count=len(baseline.documents),
    )


def validate_word_text_baseline_identity(
    baseline: WordTextBaseline,
    *,
    task_id: str,
    protocol_id: str,
    manifest_sha256: str,
    document_paths: tuple[str, ...],
) -> None:
    """将 evaluator-only baseline 绑定到正式任务合同。

    输入参数：
        baseline：prepare 前构造的 typed DTO；
        task_id/protocol_id/manifest_sha256/document_paths：生产
        evaluator 内部固定的精确身份与 DOCX 路径闭集。
    输出返回值：
        无；任一 task/protocol/manifest/顺序路径漂移、重复或
        内部快照形状异常均抛固定脱敏错误。
    """

    if not isinstance(baseline, WordTextBaseline):
        raise WordTextFidelityError("WORD_TEXT_BASELINE_IDENTITY_INVALID")
    documents = baseline.documents
    if (
        baseline.task_id != task_id
        or baseline.protocol_id != protocol_id
        or baseline.manifest_sha256 != manifest_sha256
        or not isinstance(baseline.manifest_sha256, str)
        or _SHA256_PATTERN.fullmatch(baseline.manifest_sha256) is None
        or not isinstance(documents, tuple)
        or not documents
        or not isinstance(document_paths, tuple)
        or not document_paths
        or not all(isinstance(path, str) and path for path in document_paths)
        or len(set(document_paths)) != len(document_paths)
        or not all(
            _document_snapshot_shape_is_valid(document) for document in documents
        )
        or tuple(document.path for document in documents) != document_paths
    ):
        raise WordTextFidelityError("WORD_TEXT_BASELINE_IDENTITY_INVALID")


def _document_snapshot_shape_is_valid(document: object) -> bool:
    """验证私有 DOCX 快照的全部脱敏形状。

    输入参数：
        document：可能被测试替身或边界调用方伪造的内部值。
    输出返回值：
        path/digest/count/relationship/media 的类型与固定格式均
        安全时为 ``True``；任一 ``None``、bool-as-int、列表代替 tuple
        或非规范 SHA 均返回 ``False``，不抛原生 TypeError。
    """

    if not isinstance(document, _DocumentTextSnapshot):
        return False
    if (
        not isinstance(document.path, str)
        or not document.path
        or not isinstance(document.digest, str)
        or _SHA256_PATTERN.fullmatch(document.digest) is None
        or not isinstance(document.relationship_digest, str)
        or _SHA256_PATTERN.fullmatch(document.relationship_digest) is None
        or not isinstance(document.token_count, int)
        or isinstance(document.token_count, bool)
        or document.token_count <= 0
        or not isinstance(document.part_count, int)
        or isinstance(document.part_count, bool)
        or document.part_count <= 0
        or not isinstance(document.image_relationships, tuple)
        or not isinstance(document.media_identities, tuple)
    ):
        return False
    if not all(
        isinstance(relationship, tuple)
        and len(relationship) == 3
        and all(isinstance(value, str) and value for value in relationship)
        for relationship in document.image_relationships
    ):
        return False
    return all(
        isinstance(media, tuple)
        and len(media) == 2
        and isinstance(media[0], str)
        and bool(media[0])
        and isinstance(media[1], str)
        and _SHA256_PATTERN.fullmatch(media[1]) is not None
        for media in document.media_identities
    )


def _document_snapshots_match(
    expected: _DocumentTextSnapshot,
    observed: _DocumentTextSnapshot,
    *,
    task_id: str,
) -> bool:
    """按任务边界比较单份 DOCX 的 typed 快照。

    输入参数：
        expected/observed：pre 与 post 快照；task_id：009/010。
    输出返回值：
        文字、容器、非图片关系全等时返回 ``True``；
        010 额外允许新增 image relationship/media，但 pre 中已有
        图片边与媒体必须仍是 post 的子集。
    """

    fixed_fields_match = (
        expected.path == observed.path
        and expected.digest == observed.digest
        and expected.token_count == observed.token_count
        and expected.part_count == observed.part_count
        and expected.relationship_digest == observed.relationship_digest
    )
    if not fixed_fields_match:
        return False
    if task_id == "Operation-FileOperate-BatchOperationWord-010":
        return set(expected.image_relationships).issubset(
            observed.image_relationships
        ) and set(expected.media_identities).issubset(observed.media_identities)
    return (
        expected.image_relationships == observed.image_relationships
        and expected.media_identities == observed.media_identities
    )


def _validate_baseline_identity(
    task_id: str,
    protocol_id: str,
    manifest_sha256: str,
    files: tuple[WordTextInputFile, ...],
) -> None:
    """校验 baseline 的任务、协议、manifest 与文件闭集形状。

    输入参数：
        task_id/protocol_id/manifest_sha256/files：待构造 baseline 的全部身份。
    输出返回值：
        无；非固定任务、协议或唯一路径闭集时抛出脱敏错误。
    """

    if (
        task_id not in _SUPPORTED_TASK_IDS
        or protocol_id != _OPERATION_PROTOCOL_ID
        or not isinstance(manifest_sha256, str)
        or _SHA256_PATTERN.fullmatch(manifest_sha256) is None
        or not isinstance(files, tuple)
        or not files
        or not all(isinstance(file, WordTextInputFile) for file in files)
        or len({file.path for file in files}) != len(files)
    ):
        raise WordTextFidelityError("WORD_TEXT_BASELINE_IDENTITY_INVALID")


def _read_regular_file_nofollow(
    root: Path,
    relative_path: str,
    *,
    expected_size: int | None,
    expected_sha256: str | None,
) -> bytes:
    """通过目录 fd 链 nofollow 读取并复验同一文件快照。

    输入参数：
        root/relative_path：受控根与 POSIX 相对文件路径；
        expected_size/expected_sha256：pre 读取需匹配的 manifest
            身份；post 读取两者均为 ``None``。
    输出返回值：
        同一 held descriptor 上稳定读取的不可变字节。
    异常：
        WordTextFidelityError：目录链、文件类型、硬链、大小、
            摘要或读取期间元数据不一致。
    """

    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise WordTextFidelityError("WORD_TEXT_PATH_INVALID")
    absolute_root = Path(os.path.abspath(os.fspath(root)))
    directory_flags = os.O_RDONLY
    for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW"):
        directory_flags |= getattr(os, name, 0)
    descriptor = -1
    try:
        descriptor = os.open(absolute_root.anchor or os.sep, directory_flags)
        for component in absolute_root.parts[1:]:
            child = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        raise WordTextFidelityError("WORD_TEXT_ROOT_INVALID") from None
    try:
        for component in path.parts[:-1]:
            child = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW"):
            file_flags |= getattr(os, name, 0)
        file_descriptor = os.open(path.name, file_flags, dir_fd=descriptor)
    except OSError:
        raise WordTextFidelityError("WORD_TEXT_FILE_UNAVAILABLE") from None
    finally:
        os.close(descriptor)
    try:
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= _MAX_DOCX_BYTES
            or (expected_size is not None and before.st_size != expected_size)
        ):
            raise WordTextFidelityError("WORD_TEXT_FILE_IDENTITY_INVALID")
        with os.fdopen(file_descriptor, "rb", closefd=True) as stream:
            file_descriptor = -1
            payload = stream.read(_MAX_DOCX_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (
            len(payload) != before.st_size
            or len(payload) > _MAX_DOCX_BYTES
            or _stat_identity(before) != _stat_identity(after)
            or (
                expected_sha256 is not None
                and hashlib.sha256(payload).hexdigest() != expected_sha256
            )
        ):
            raise WordTextFidelityError("WORD_TEXT_FILE_SNAPSHOT_UNSTABLE")
        return payload
    except WordTextFidelityError:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        raise
    except OSError:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        raise WordTextFidelityError("WORD_TEXT_FILE_READ_FAILED") from None


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    """投影 held descriptor 读取前后的稳定身份。

    输入参数：
        metadata：``fstat`` 返回的元数据。
    输出返回值：
        device/inode/size/link/mtime/ctime 的整数元组。
    """

    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_nlink,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _snapshot_document(
    path: str,
    payload: bytes,
    *,
    task_id: str,
) -> _DocumentTextSnapshot:
    """将一份 DOCX 字节投影为不可逆 typed token 摘要。

    输入参数：
        path：仅用于同一 manifest 内的文档身份；
        payload：稳定读取的完整 DOCX ZIP 字节。
        task_id：决定 009 唯一可忽略的目标属性为行距。
    输出返回值：
        包含顺序、类型和精确文字的 SHA-256 摘要与计数。
    异常：
        WordTextFidelityError：ZIP 或 XML 不可解析、重复 member
            或缺少主文档部件。
    """

    parsed_parts: list[tuple[str, str, ET.Element]] = []
    style_context: _StyleContext | None = None
    numbering_context: _NumberingContext | None = None
    relationship_graph: _RelationshipGraph | None = None
    semantic_part_content_types: list[tuple[str, str]] = []
    media_identities: tuple[tuple[str, str], ...] = ()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(name.casefold() for name in names)):
                raise WordTextFidelityError("WORD_TEXT_ARCHIVE_DUPLICATE_MEMBER")
            member_names = frozenset(names)
            content_types = _validate_main_document_content_type(
                archive,
                member_names,
            )
            relationship_graph = _validate_relationship_graph(
                archive,
                member_names,
            )
            if "word/document.xml" not in names:
                raise WordTextFidelityError("WORD_TEXT_MAIN_PART_MISSING")
            for part_name in sorted(name for name in names if name.endswith(".xml")):
                root = ET.fromstring(archive.read(part_name))
                has_text_carrier = _contains_text_carrier(root)
                if _TEXT_PART_PATTERN.fullmatch(part_name):
                    _validate_text_part_root(part_name, root)
                    if part_name not in relationship_graph.reachable_parts:
                        raise WordTextFidelityError(
                            "WORD_TEXT_RELATIONSHIP_REFERENCE_UNRESOLVED"
                        )
                    _validate_relationship_references(
                        root,
                        source_part=part_name,
                        relationship_graph=relationship_graph,
                    )
                    parsed_parts.append(
                        (
                            part_name,
                            _effective_part_content_type(
                                content_types,
                                part_name,
                            ),
                            root,
                        )
                    )
                elif has_text_carrier:
                    raise WordTextFidelityError("WORD_TEXT_UNSUPPORTED_TEXT_PART")
            if "word/styles.xml" not in names:
                raise WordTextFidelityError("WORD_TEXT_STYLES_PART_MISSING")
            styles_root = ET.fromstring(archive.read("word/styles.xml"))
            if styles_root.tag != _STYLES_ROOT_TAG:
                raise WordTextFidelityError("WORD_TEXT_PART_ROOT_INVALID")
            style_context = _build_style_context(styles_root)
            semantic_part_content_types.append(
                (
                    "word/styles.xml",
                    _effective_part_content_type(
                        content_types,
                        "word/styles.xml",
                    ),
                )
            )
            if "word/numbering.xml" in names:
                numbering_root = ET.fromstring(archive.read("word/numbering.xml"))
                if numbering_root.tag != _NUMBERING_ROOT_TAG:
                    raise WordTextFidelityError("WORD_TEXT_PART_ROOT_INVALID")
                numbering_context = _build_numbering_context(numbering_root)
                semantic_part_content_types.append(
                    (
                        "word/numbering.xml",
                        _effective_part_content_type(
                            content_types,
                            "word/numbering.xml",
                        ),
                    )
                )
            media_identities = tuple(
                (
                    name,
                    hashlib.sha256(archive.read(name)).hexdigest(),
                )
                for name in sorted(
                    name
                    for name in names
                    if name.startswith("word/media/") and not name.endswith("/")
                )
            )
    except WordTextFidelityError:
        raise
    except (OSError, KeyError, ET.ParseError, zipfile.BadZipFile):
        raise WordTextFidelityError("WORD_TEXT_DOCUMENT_INVALID") from None
    if style_context is None:
        raise WordTextFidelityError("WORD_TEXT_STYLES_PART_MISSING")
    if relationship_graph is None:
        raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_GRAPH_INVALID")
    tokens: list[tuple[str, ...]] = []
    for part_name, content_type in semantic_part_content_types:
        tokens.append((part_name, "semantic-part-content-type", content_type))
    for part_name, content_type, root in parsed_parts:
        tokens.append((part_name, "part-start", content_type))
        _collect_basic_text_tokens(
            tokens,
            root,
            part_name=part_name,
            task_id=task_id,
            style_context=style_context,
            numbering_context=numbering_context,
            relationships=relationship_graph.by_source.get(part_name, {}),
        )
        tokens.append((part_name, "part-end"))
    digest = hashlib.sha256()
    for token in tokens:
        _update_digest(digest, token)
    return _DocumentTextSnapshot(
        path=path,
        digest=digest.hexdigest(),
        token_count=len(tokens),
        part_count=len(parsed_parts),
        relationship_digest=relationship_graph.relationship_digest,
        image_relationships=relationship_graph.image_relationships,
        media_identities=media_identities,
    )


def _validate_text_part_root(part_name: str, root: ET.Element) -> None:
    """将每类可见文字 part 绑定到 canonical 根 QName。

    输入参数：
        part_name：已命中固定 text-part 模式的 member 路径；
        root：从同一 held DOCX 字节解析的 XML 根。
    输出返回值：
        无；路径与 document/header/footer/note/comment/glossary/
        chart/diagram 根 QName 必须精确对应。
    异常：
        WordTextFidelityError：路径分类不在闭集或根 QName 漂移。
    """

    if part_name == "word/document.xml":
        expected = f"{{{_WORD_NAMESPACE}}}document"
    elif re.fullmatch(r"word/header[0-9]+\.xml", part_name):
        expected = f"{{{_WORD_NAMESPACE}}}hdr"
    elif re.fullmatch(r"word/footer[0-9]+\.xml", part_name):
        expected = f"{{{_WORD_NAMESPACE}}}ftr"
    elif part_name == "word/footnotes.xml":
        expected = f"{{{_WORD_NAMESPACE}}}footnotes"
    elif part_name == "word/endnotes.xml":
        expected = f"{{{_WORD_NAMESPACE}}}endnotes"
    elif part_name == "word/comments.xml":
        expected = f"{{{_WORD_NAMESPACE}}}comments"
    elif part_name == "word/glossary/document.xml":
        expected = f"{{{_WORD_NAMESPACE}}}glossaryDocument"
    elif re.fullmatch(r"word/charts/chart[0-9]+\.xml", part_name):
        expected = f"{{{_CHART_NAMESPACE}}}chartSpace"
    elif re.fullmatch(r"word/diagrams/data[0-9]+\.xml", part_name):
        expected = f"{{{_DIAGRAM_NAMESPACE}}}dataModel"
    elif re.fullmatch(r"word/diagrams/drawing[0-9]+\.xml", part_name):
        expected = f"{{{_DIAGRAM_DRAWING_NAMESPACE}}}drawing"
    else:
        raise WordTextFidelityError("WORD_TEXT_PART_ROOT_INVALID")
    if root.tag != expected:
        raise WordTextFidelityError("WORD_TEXT_PART_ROOT_INVALID")


def _validate_main_document_content_type(
    archive: zipfile.ZipFile,
    member_names: frozenset[str],
) -> _ContentTypeTable:
    """验证 OPC Content Types 中的唯一 Word main-part 声明。

    输入参数：
        archive：同一 held DOCX 字节的 ZIP 视图；
        member_names：已拒绝大小写重名的 member 闭集。
    输出返回值：
        已规范化的 Default/Override 映射；
        ``/word/document.xml`` 必须恰有一条正式
        Override，两类身份也必须唯一且完整。
    异常：
        WordTextFidelityError：Content Types 缺失、XML 形状、
            重复映射或 main content type 任一无效。
    """

    if "[Content_Types].xml" not in member_names:
        raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID")
    try:
        root = ET.fromstring(archive.read("[Content_Types].xml"))
    except (KeyError, ET.ParseError):
        raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID") from None
    if root.tag != _CONTENT_TYPES_ROOT_TAG:
        raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID")
    default_extensions: dict[str, str] = {}
    override_parts: dict[str, str] = {}
    override_part_casefolds: set[str] = set()
    main_content_types: list[str] = []
    for child in root:
        if child.tag == _CONTENT_TYPES_DEFAULT_TAG:
            extension = child.get("Extension")
            content_type = child.get("ContentType")
            normalized_extension = extension.casefold() if extension else ""
            if (
                not normalized_extension
                or not content_type
                or normalized_extension in default_extensions
            ):
                raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID")
            default_extensions[normalized_extension] = content_type
            continue
        if child.tag != _CONTENT_TYPES_OVERRIDE_TAG:
            raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID")
        part_name = child.get("PartName")
        content_type = child.get("ContentType")
        normalized_part = part_name.casefold() if part_name else ""
        if (
            not part_name
            or not part_name.startswith("/")
            or not content_type
            or normalized_part in override_part_casefolds
        ):
            raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID")
        override_part_casefolds.add(normalized_part)
        override_parts[part_name] = content_type
        if part_name == "/word/document.xml":
            main_content_types.append(content_type)
    if main_content_types != [_WORD_MAIN_CONTENT_TYPE]:
        raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID")
    return _ContentTypeTable(
        defaults=default_extensions,
        overrides=override_parts,
    )


def _effective_part_content_type(
    table: _ContentTypeTable,
    part_name: str,
) -> str:
    """解析一个 OPC part 的有效 ContentType。

    输入参数：
        table：已验证唯一性的 Content Types 表；
        part_name：不带开头斜杠的 package member 路径。
    输出返回值：
        Override 优先、Default 后备的非空 MIME 字符串。
    异常：
        WordTextFidelityError：该 part 没有任何有效映射。
    """

    normalized_part = f"/{part_name}"
    override = table.overrides.get(normalized_part)
    if override is not None:
        return override
    filename = PurePosixPath(part_name).name
    extension = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    default = table.defaults.get(extension)
    if default is None:
        raise WordTextFidelityError("WORD_TEXT_CONTENT_TYPES_INVALID")
    return default


def _validate_relationship_graph(
    archive: zipfile.ZipFile,
    member_names: frozenset[str],
) -> _RelationshipGraph:
    """校验 DOCX 所有 relationship 部件的闭集、目标与外部边界。

    输入参数：
        archive：由同一 held bytes 构造的 DOCX ZIP；
        member_names：已拒绝重复名的完整 member 闭集。
    输出返回值：
        ID→规范关系索引、从 package root 可达的部件闭集、
        非图片关系语义摘要与图片边闭集。
    异常：
        WordTextFidelityError：任一 ``TargetMode=External``、编码后穿越、
            绝对目标、重复 ID、缺失 member 或 rel XML 形状无效。
    """

    by_source: dict[str, dict[str, _Relationship]] = {}
    for relationship_name in sorted(
        name for name in member_names if name.endswith(".rels")
    ):
        source_part = _relationship_source_part(relationship_name)
        if source_part and source_part not in member_names:
            raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_GRAPH_INVALID")
        try:
            root = ET.fromstring(archive.read(relationship_name))
        except (KeyError, ET.ParseError):
            raise WordTextFidelityError(
                "WORD_TEXT_RELATIONSHIP_GRAPH_INVALID"
            ) from None
        if root.tag != _PACKAGE_RELATIONSHIPS_ROOT_TAG or any(
            child.tag != _RELATIONSHIP_TAG for child in root
        ):
            raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_GRAPH_INVALID")
        seen_ids: set[str] = set()
        source_relationships: dict[str, _Relationship] = {}
        for relationship in root.findall(_RELATIONSHIP_TAG):
            relationship_id = relationship.get("Id")
            target = relationship.get("Target")
            relationship_type = relationship.get("Type")
            target_mode = relationship.get("TargetMode")
            if (
                not relationship_id
                or relationship_id in seen_ids
                or not target
                or not relationship_type
                or target_mode not in {None, "Internal", "External"}
            ):
                raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_GRAPH_INVALID")
            seen_ids.add(relationship_id)
            if target_mode == "External":
                raise WordTextFidelityError("WORD_TEXT_EXTERNAL_RELATIONSHIP_REJECTED")
            normalized_target = _normalize_internal_relationship_target(
                source_part,
                target,
            )
            if normalized_target not in member_names:
                raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_TARGET_MISSING")
            if relationship_type.endswith(_IMAGE_RELATIONSHIP_SUFFIX) and not (
                normalized_target.startswith("word/media/")
                and normalized_target != "word/media/"
            ):
                raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_TARGET_INVALID")
            source_relationships[relationship_id] = _Relationship(
                source_part=source_part,
                relationship_type=relationship_type,
                target_part=normalized_target,
            )
        by_source[source_part] = source_relationships

    root_relationships = tuple(by_source.get("", {}).values())
    main_relationships = tuple(
        relationship
        for relationship in root_relationships
        if relationship.relationship_type in _OFFICE_DOCUMENT_RELATIONSHIP_TYPES
    )
    relationships_targeting_main = tuple(
        relationship
        for relationship in root_relationships
        if relationship.target_part == "word/document.xml"
    )
    if (
        len(main_relationships) != 1
        or main_relationships[0].target_part != "word/document.xml"
        or len(relationships_targeting_main) != 1
    ):
        raise WordTextFidelityError("WORD_TEXT_MAIN_RELATIONSHIP_INVALID")

    reachable: set[str] = set()
    pending = [
        relationship.target_part for relationship in by_source.get("", {}).values()
    ]
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(
            relationship.target_part
            for relationship in by_source.get(current, {}).values()
            if relationship.target_part not in reachable
        )
    if "word/document.xml" not in reachable:
        raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_GRAPH_INVALID")

    non_image_relationships = sorted(
        (
            relationship.source_part,
            relationship.relationship_type,
            relationship.target_part,
        )
        for source_part, relationships in by_source.items()
        if source_part.startswith("word/") and source_part in reachable
        for relationship in relationships.values()
        if _is_text_semantic_relationship(relationship)
    )
    image_relationships = tuple(
        sorted(
            (
                relationship.source_part,
                relationship.relationship_type,
                relationship.target_part,
            )
            for source_part, relationships in by_source.items()
            if source_part.startswith("word/") and source_part in reachable
            for relationship in relationships.values()
            if relationship.relationship_type.endswith(_IMAGE_RELATIONSHIP_SUFFIX)
        )
    )
    digest = hashlib.sha256()
    for relationship in non_image_relationships:
        _update_digest(digest, ("relationship", *relationship))
    return _RelationshipGraph(
        by_source=by_source,
        reachable_parts=frozenset(reachable),
        relationship_digest=digest.hexdigest(),
        image_relationships=image_relationships,
    )


def _is_text_semantic_relationship(relationship: _Relationship) -> bool:
    """判定一条非图片边是否改变可见文字语义。

    输入参数：
        relationship：已规范化的 internal OPC 边。
    输出返回值：
        目标为已支持文字 part，或类型会决定
        header/footer/note/comment/hyperlink/style/numbering 语义时为
        ``True``；webSettings/theme 等非文字关系不投影。
    """

    if relationship.relationship_type.endswith(_IMAGE_RELATIONSHIP_SUFFIX):
        return False
    relationship_kind = relationship.relationship_type.rsplit("/", 1)[-1]
    return (
        relationship_kind in _TEXT_SEMANTIC_RELATIONSHIP_TYPES
        or _TEXT_PART_PATTERN.fullmatch(relationship.target_part) is not None
        or relationship.target_part in {"word/styles.xml", "word/numbering.xml"}
    )


def _contains_text_carrier(root: ET.Element) -> bool:
    """检测 XML part 是否含已知或必须拒绝的文字载体。

    输入参数：
        root：当前 XML part 根节点。
    输出返回值：
        任一文字/控制/VML textpath/未支持载体存在时为 ``True``。
    """

    return any(
        node.tag in _TEXT_NODE_KINDS
        or node.tag == _VML_TEXTPATH_TAG
        or node.tag in _UNSUPPORTED_CARRIER_TAGS
        for node in root.iter()
    )


def _namespace_of_tag(tag: str) -> str:
    """返回 ElementTree QName 的 namespace URI。

    输入参数：
        tag：``{namespace}local`` 或无 namespace 标签。
    输出返回值：
        带大括号 QName 的 URI；无 namespace 时返回空串。
    """

    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _unknown_wrapper_contains_text_semantics(element: ET.Element) -> bool:
    """检测未登记 namespace wrapper 是否包裹文字语义。

    输入参数：
        element：当前尚未进入递归投影的 XML 节点。
    输出返回值：
        namespace 不在固定 OOXML 结构闭集，且子树含
        已知文字、控制、VML textpath 或禁止载体时返回真。
    """

    if _namespace_of_tag(element.tag) in _KNOWN_TEXT_STRUCTURE_NAMESPACES:
        return False
    return any(
        node is not element
        and (
            node.tag in _TEXT_NODE_KINDS
            or node.tag in _CONTROL_NODE_KINDS
            or node.tag == _VML_TEXTPATH_TAG
            or node.tag in _UNSUPPORTED_CARRIER_TAGS
        )
        for node in element.iter()
    )


def _validate_relationship_references(
    root: ET.Element,
    *,
    source_part: str,
    relationship_graph: _RelationshipGraph,
) -> None:
    """验证一个可达 XML part 中所有 ``r:*`` 关系引用。

    输入参数：
        root/source_part：当前已解析 part；relationship_graph：
        同一 DOCX 字节快照的唯一关系图。
    输出返回值：
        无；空 ID 或不能在当前 source part 唯一解析时
        抛出固定脱敏错误。
    """

    relationships = relationship_graph.by_source.get(source_part, {})
    for element in root.iter():
        for attribute_name in _RELATIONSHIP_REFERENCE_ATTRIBUTES:
            relationship_id = element.get(attribute_name)
            if relationship_id is None:
                continue
            if not relationship_id or relationship_id not in relationships:
                raise WordTextFidelityError(
                    "WORD_TEXT_RELATIONSHIP_REFERENCE_UNRESOLVED"
                )


def _relationship_source_part(relationship_name: str) -> str:
    """由 OPC ``_rels/*.rels`` member 恢复它所属的 source part。

    输入参数：
        relationship_name：ZIP 内 relationship part 路径。
    输出返回值：
        package 根 rel 返回空字符串；其余返回对应 source part。
    """

    if relationship_name == "_rels/.rels":
        return ""
    path = PurePosixPath(relationship_name)
    if (
        path.parent.name != "_rels"
        or not path.name.endswith(".rels")
        or path.name == ".rels"
    ):
        raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_GRAPH_INVALID")
    source_name = path.name[: -len(".rels")]
    return (path.parent.parent / source_name).as_posix()


def _normalize_internal_relationship_target(source_part: str, target: str) -> str:
    """将 internal relationship target 解码并限制在 OPC package 内。

    输入参数：
        source_part：rel 所属 source part；target：原始 ``Target`` 字面量。
    输出返回值：
        无点段、无开头斜杠的 package member 路径。
    """

    decoded = unquote(target)
    if (
        not decoded
        or "\\" in decoded
        or "\x00" in decoded
        or decoded.startswith("/")
        or ":" in decoded.split("/", 1)[0]
        or "?" in decoded
    ):
        raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_TARGET_INVALID")
    without_fragment = decoded.split("#", 1)[0]
    base_directory = posixpath.dirname(source_part)
    normalized = posixpath.normpath(posixpath.join(base_directory, without_fragment))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_TARGET_INVALID")
    return normalized


def _collect_basic_text_tokens(
    tokens: list[tuple[str, ...]],
    element: ET.Element,
    *,
    part_name: str,
    task_id: str,
    style_context: _StyleContext,
    numbering_context: _NumberingContext | None,
    relationships: dict[str, _Relationship],
    run_properties: str = "",
    xml_space_mode: str = "default",
) -> None:
    """按容器顺序写入主文档段落、表格与文字 token。

    输入参数：
        tokens：当前文档的有序 typed token 缓冲区；
        element：当前 XML 元素；part_name：所属 OOXML part；
        task_id：当前 009/010 语义；
        style_context：已验证的样式图与默认属性；
        numbering_context：可选、已验证的列表实例/定义图；
        relationships：当前 part 的已验证 ID→关系索引；
        run_properties：当前 ``w:r`` 的规范化显示属性摘要；
        xml_space_mode：从父容器继承的 XML 空白处理模式。
    输出返回值：
        无；向共享缓冲区追加当前子树 token。容器边界显式
        记录，相邻 ``w:t`` 文字则合并以容忍等价 run 拆分。
    """

    if _unknown_wrapper_contains_text_semantics(element):
        raise WordTextFidelityError("WORD_TEXT_UNKNOWN_TEXT_CARRIER")
    if element.tag in _UNSUPPORTED_CARRIER_TAGS:
        raise WordTextFidelityError("WORD_TEXT_UNSUPPORTED_CARRIER")
    if (
        element.tag not in _TEXT_NODE_KINDS
        and element.tag != _VML_TEXTPATH_TAG
        and element.tag not in _NON_VISIBLE_DRAWING_SCALAR_TEXT_TAGS
        and element.text is not None
        and element.text.strip()
    ):
        raise WordTextFidelityError("WORD_TEXT_UNKNOWN_TEXT_CARRIER")
    current_xml_space_mode = _effective_xml_space_mode(element, xml_space_mode)
    if (
        task_id == "Operation-FileOperate-BatchOperationWord-010"
        and element.tag == _PARAGRAPH_TAG
        and _is_drawing_only_paragraph(element)
    ):
        return
    if element.tag in _BASIC_CONTAINER_TAGS:
        tokens.append(
            (
                part_name,
                "container-start",
                element.tag,
                _container_signature(element),
                (
                    _paragraph_properties_signature(
                        element,
                        task_id=task_id,
                        style_context=style_context,
                        numbering_context=numbering_context,
                    )
                    if element.tag == _PARAGRAPH_TAG
                    else ""
                ),
            )
        )
    for attribute_name in sorted(_RELATIONSHIP_REFERENCE_ATTRIBUTES):
        relationship_id = element.get(attribute_name)
        if relationship_id is None:
            continue
        relationship = relationships.get(relationship_id)
        if relationship is None:
            raise WordTextFidelityError("WORD_TEXT_RELATIONSHIP_REFERENCE_UNRESOLVED")
        if (
            task_id == "Operation-FileOperate-BatchOperationWord-010"
            and relationship.relationship_type.endswith(_IMAGE_RELATIONSHIP_SUFFIX)
        ):
            continue
        tokens.append(
            (
                part_name,
                "relationship-reference",
                element.tag,
                attribute_name,
                relationship.relationship_type,
                relationship.target_part,
                _sorted_attribute_signature(
                    element,
                    excluded=_RELATIONSHIP_REFERENCE_ATTRIBUTES,
                ),
            )
        )
    current_run_properties = (
        _run_properties_signature(
            element,
            task_id=task_id,
            style_context=style_context,
        )
        if element.tag == _RUN_TAG
        else run_properties
    )
    text_kind = _TEXT_NODE_KINDS.get(element.tag)
    if text_kind is not None:
        effective_text = _effective_text_value(
            element.text or "",
            xml_space_mode=current_xml_space_mode,
        )
        token = (
            part_name,
            "text",
            text_kind,
            current_run_properties,
            _sorted_attribute_signature(
                element,
                excluded=frozenset({_XML_SPACE_ATTRIBUTE}),
            ),
            effective_text,
        )
        if tokens and tokens[-1][1:2] == ("text",) and tokens[-1][:-1] == token[:-1]:
            previous = tokens[-1]
            tokens[-1] = (*previous[:-1], previous[-1] + token[-1])
        else:
            tokens.append(token)
    control_kind = _CONTROL_NODE_KINDS.get(element.tag)
    if control_kind is not None:
        tokens.append(
            (
                part_name,
                "control",
                control_kind,
                current_run_properties,
                _sorted_attribute_signature(element),
            )
        )
    if element.tag == _VML_TEXTPATH_TAG:
        tokens.append(
            (
                part_name,
                "textpath",
                _sorted_attribute_signature(element),
                element.text or "",
            )
        )
    for child in element:
        _collect_basic_text_tokens(
            tokens,
            child,
            part_name=part_name,
            task_id=task_id,
            style_context=style_context,
            numbering_context=numbering_context,
            relationships=relationships,
            run_properties=current_run_properties,
            xml_space_mode=current_xml_space_mode,
        )
        if child.tail is not None and child.tail.strip():
            raise WordTextFidelityError("WORD_TEXT_UNKNOWN_TEXT_CARRIER")
    if element.tag in _BASIC_CONTAINER_TAGS:
        tokens.append((part_name, "container-end", element.tag))


def _is_drawing_only_paragraph(paragraph: ET.Element) -> bool:
    """判断 010 段落是否只承载新增 drawing 而不承载文字。

    输入参数：
        paragraph：当前 ``w:p`` XML 元素。
    输出返回值：
        至少含一个 ``w:drawing``，且不含任一已知文字、控制节点、
        VML textpath 或未支持文字载体时为 ``True``。空段落不算
        drawing-only，因此不会被任意忽略。
    """

    drawing_tag = f"{{{_WORD_NAMESPACE}}}drawing"
    has_drawing = False
    for node in paragraph.iter():
        if node.tag in _UNSUPPORTED_CARRIER_TAGS:
            raise WordTextFidelityError("WORD_TEXT_UNSUPPORTED_CARRIER")
        if (
            node.tag not in _TEXT_NODE_KINDS
            and node.tag != _VML_TEXTPATH_TAG
            and node.tag not in _NON_VISIBLE_DRAWING_SCALAR_TEXT_TAGS
            and node.text is not None
            and node.text.strip()
        ):
            raise WordTextFidelityError("WORD_TEXT_UNKNOWN_TEXT_CARRIER")
        if node is not paragraph and node.tail is not None and node.tail.strip():
            raise WordTextFidelityError("WORD_TEXT_UNKNOWN_TEXT_CARRIER")
        if node.tag == drawing_tag:
            has_drawing = True
        if (
            node.tag in _TEXT_NODE_KINDS
            or node.tag in _CONTROL_NODE_KINDS
            or node.tag == _VML_TEXTPATH_TAG
        ):
            return False
    return has_drawing


def _sorted_attribute_signature(
    element: ET.Element,
    *,
    excluded: frozenset[str] = frozenset(),
) -> str:
    """将一个文字/控制节点的属性投影为稳定摘要。

    输入参数：
        element：待投影的 OOXML 节点；excluded：已由文字值
        完整承载语义、因此应忽略的属性 QName 闭集。
    输出返回值：
        无属性时返回空字符串；否则返回与 namespace
        prefix/原始属性顺序无关的 SHA-256。
    """

    attributes = tuple(
        (name, value)
        for name, value in sorted(element.attrib.items())
        if name not in excluded
    )
    if not attributes:
        return ""
    digest = hashlib.sha256()
    _update_digest(
        digest,
        tuple(f"{name}={value}" for name, value in attributes),
    )
    return digest.hexdigest()


def _effective_xml_space_mode(element: ET.Element, inherited: str) -> str:
    """计算当前 OOXML 节点继承后的 ``xml:space`` 模式。

    输入参数：
        element：当前 XML 节点；inherited：父节点的 ``default``
        或 ``preserve`` 模式。
    输出返回值：
        当前生效模式；非法值固定 fail-closed。
    """

    declared = element.get(_XML_SPACE_ATTRIBUTE)
    if declared is None:
        return inherited
    if declared not in {"default", "preserve"}:
        raise WordTextFidelityError("WORD_TEXT_XML_SPACE_INVALID")
    return declared


def _effective_text_value(text: str, *, xml_space_mode: str) -> str:
    """按 XML 空白语义投影文字节点的有效字符串。

    输入参数：
        text：ElementTree 解析后的原始文字；xml_space_mode：继承后
        的 ``default`` 或 ``preserve``。
    输出返回值：
        preserve 模式保留原值；default 模式仅剔除 XML 定义的
        首尾空白，不误删有语义的 NBSP。
    """

    if xml_space_mode == "preserve":
        return text
    return text.strip(" \t\r\n")


def _container_signature(element: ET.Element) -> str:
    """提取会改变容器文字语义的稳定属性。

    输入参数：
        element：当前段落、表格或语义 wrapper。
    输出返回值：
        超链接返回除短暂 ``r:id`` 外的排序属性摘要；
        其余容器返回空字符串。关系目标由后续固定 rel 投影补全。
    """

    if element.tag == _HYPERLINK_TAG:
        attributes = tuple(
            (name, value)
            for name, value in sorted(element.attrib.items())
            if name != _RELATIONSHIP_ID_ATTRIBUTE
        )
        kind = "hyperlink"
    elif (
        element.tag == _FIELD_SIMPLE_TAG
        or element.tag in _SEMANTIC_ENTRY_CONTAINER_TAGS
        or element.tag in _MARKUP_COMPATIBILITY_CONTAINER_TAGS
        or element.tag in _DIRECTION_CONTAINER_TAGS
    ):
        attributes = tuple(sorted(element.attrib.items()))
        kind = element.tag
    else:
        return ""
    digest = hashlib.sha256()
    _update_digest(
        digest,
        (
            kind,
            *((f"{name}={value}") for name, value in attributes),
        ),
    )
    return digest.hexdigest()


def _build_style_context(styles_root: ET.Element) -> _StyleContext:
    """从 ``word/styles.xml`` 构造唯一、有界的样式图。

    输入参数：
        styles_root：已从同一 DOCX 字节快照解析的 styles 根。
    输出返回值：
        样式 ID 索引、唯一默认段落样式与 docDefaults 属性。
    异常：
        WordTextFidelityError：样式 ID 重复/空缺、默认样式不唯一，
            或 docDefaults 层次存在重复容器。
    """

    styles: dict[str, ET.Element] = {}
    default_paragraph_ids: list[str] = []
    for style in (child for child in styles_root if child.tag == _STYLE_TAG):
        style_id = style.get(_STYLE_ID_ATTRIBUTE)
        if not style_id or style_id in styles:
            raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
        styles[style_id] = style
        if style.get(_STYLE_TYPE_ATTRIBUTE) == "paragraph" and style.get(
            _STYLE_DEFAULT_ATTRIBUTE
        ) in {"1", "true", "on"}:
            default_paragraph_ids.append(style_id)
    if len(default_paragraph_ids) > 1:
        raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
    doc_defaults = _unique_direct_child(
        styles_root,
        f"{{{_WORD_NAMESPACE}}}docDefaults",
    )
    default_paragraph_properties = None
    default_run_properties = None
    if doc_defaults is not None:
        paragraph_default = _unique_direct_child(
            doc_defaults,
            f"{{{_WORD_NAMESPACE}}}pPrDefault",
        )
        run_default = _unique_direct_child(
            doc_defaults,
            f"{{{_WORD_NAMESPACE}}}rPrDefault",
        )
        if paragraph_default is not None:
            default_paragraph_properties = _unique_direct_child(
                paragraph_default,
                _PARAGRAPH_PROPERTIES_TAG,
            )
        if run_default is not None:
            default_run_properties = _unique_direct_child(
                run_default,
                _RUN_PROPERTIES_TAG,
            )
    return _StyleContext(
        styles=styles,
        default_paragraph_style_id=(
            default_paragraph_ids[0] if default_paragraph_ids else None
        ),
        default_paragraph_properties=default_paragraph_properties,
        default_run_properties=default_run_properties,
    )


def _build_numbering_context(numbering_root: ET.Element) -> _NumberingContext:
    """将 numbering 实例与 abstract definition 索引为唯一图。

    输入参数：
        numbering_root：从同一 DOCX 快照解析的 ``word/numbering.xml``。
    输出返回值：
        numId→实例、abstractNumId→定义的内部映射。只有被
        可见文字段落引用的层级才会在后续解析为摘要。
    异常：
        WordTextFidelityError：abstractNumId/numId 缺失或重复。
    """

    abstract_definitions: dict[str, ET.Element] = {}
    numbering_instances: dict[str, ET.Element] = {}
    for child in numbering_root:
        if child.tag == _ABSTRACT_NUMBERING_TAG:
            identity = child.get(_ABSTRACT_NUMBERING_ID_ATTRIBUTE)
            target = abstract_definitions
        elif child.tag == _NUMBERING_INSTANCE_TAG:
            identity = child.get(_NUMBERING_ID_ATTRIBUTE)
            target = numbering_instances
        else:
            continue
        if not identity or identity in target:
            raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")
        target[identity] = child
    return _NumberingContext(
        abstract_definitions=abstract_definitions,
        numbering_instances=numbering_instances,
    )


def _unique_direct_child(parent: ET.Element, tag: str) -> ET.Element | None:
    """返回唯一指定直接子节点，重复时 fail closed。

    输入参数：
        parent：待检索父元素；tag：展开后子节点 QName。
    输出返回值：
        无子节点时 ``None``，唯一时返回该元素。
    """

    children = [child for child in parent if child.tag == tag]
    if len(children) > 1:
        raise WordTextFidelityError("WORD_TEXT_PROPERTY_STRUCTURE_INVALID")
    return children[0] if children else None


def _run_properties_signature(
    run: ET.Element,
    *,
    task_id: str,
    style_context: _StyleContext,
) -> str:
    """对影响文字可见性和呈现的直接 ``w:rPr`` 建立摘要。

    输入参数：
        run：当前 ``w:r`` XML 元素；task_id：009/010 语义；
        style_context：用于闭合 ``rStyle/basedOn`` 的样式图。
    输出返回值：
        无直接属性时返回空字符串；否则返回不可逆的
        结构 SHA-256，使 hidden/color/size 等漂移改变 typed token。
    """

    del task_id
    properties = _unique_direct_child(run, _RUN_PROPERTIES_TAG)
    semantics: dict[str, str] = {}
    if properties is not None:
        semantics.update(_selected_run_property_signatures(properties))
        style_id = _style_reference(properties, _RUN_STYLE_TAG)
        if style_id is not None:
            _merge_missing_semantics(
                semantics,
                _style_chain_semantics(
                    style_id,
                    expected_type="character",
                    style_context=style_context,
                ),
            )
    return _semantic_map_signature("run-properties", semantics)


def _paragraph_properties_signature(
    paragraph: ET.Element,
    *,
    task_id: str,
    style_context: _StyleContext,
    numbering_context: _NumberingContext | None,
) -> str:
    """对段落语义属性建立任务相关的稳定摘要。

    输入参数：
        paragraph：当前 ``w:p``；task_id：009 或 010 任务；
        style_context：用于解析 pStyle/basedOn/docDefaults 的样式图；
        numbering_context：用于解析有效 numId→abstract level 语义。
    输出返回值：
        没有 ``w:pPr`` 时返回空字符串；否则返回语义属性
        SHA-256。009 只忽略目标 ``w:spacing``，010 不忽略任何段落属性。
    """

    del task_id
    properties = _unique_direct_child(paragraph, _PARAGRAPH_PROPERTIES_TAG)
    semantics: dict[str, str] = {}
    if properties is not None:
        semantics.update(_selected_paragraph_property_signatures(properties))
        paragraph_style_id = _style_reference(properties, _PARAGRAPH_STYLE_TAG)
    else:
        paragraph_style_id = None
    effective_style_id = (
        paragraph_style_id
        if paragraph_style_id is not None
        else style_context.default_paragraph_style_id
    )
    if effective_style_id is not None:
        _merge_missing_semantics(
            semantics,
            _style_chain_semantics(
                effective_style_id,
                expected_type="paragraph",
                style_context=style_context,
            ),
        )
    if style_context.default_paragraph_properties is not None:
        _merge_missing_semantics(
            semantics,
            _selected_paragraph_property_signatures(
                style_context.default_paragraph_properties
            ),
        )
    if style_context.default_run_properties is not None:
        _merge_missing_semantics(
            semantics,
            _selected_run_property_signatures(style_context.default_run_properties),
        )
    numbering_reference = _effective_numbering_reference(
        paragraph,
        style_context=style_context,
    )
    if numbering_reference is not None:
        if numbering_context is None:
            raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")
        semantics["paragraph-numbering"] = _resolve_numbering_signature(
            numbering_context,
            num_id=numbering_reference[0],
            level_id=numbering_reference[1],
        )
    return _semantic_map_signature("paragraph-properties", semantics)


def _effective_numbering_reference(
    paragraph: ET.Element,
    *,
    style_context: _StyleContext,
) -> tuple[str, str] | None:
    """按 direct→pStyle/basedOn→docDefaults 解析有效 numId/ilvl。

    输入参数：
        paragraph：当前含文字段落；style_context：已验证样式图。
    输出返回值：
        未编号返回 ``None``；否则返回数字字符串
        ``(numId, ilvl)``，缺省 ilvl 规范为 ``0``。
    """

    properties = _unique_direct_child(paragraph, _PARAGRAPH_PROPERTIES_TAG)
    direct = _numbering_reference_from_properties(properties)
    if direct is not None:
        return direct
    paragraph_style_id = (
        _style_reference(properties, _PARAGRAPH_STYLE_TAG)
        if properties is not None
        else None
    )
    current_id = paragraph_style_id or style_context.default_paragraph_style_id
    visited: set[str] = set()
    for _depth in range(64):
        if current_id is None:
            break
        if current_id in visited:
            raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
        style = style_context.styles.get(current_id)
        if style is None or style.get(_STYLE_TYPE_ATTRIBUTE) != "paragraph":
            raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
        visited.add(current_id)
        inherited = _numbering_reference_from_properties(
            _unique_direct_child(style, _PARAGRAPH_PROPERTIES_TAG)
        )
        if inherited is not None:
            return inherited
        based_on = _unique_direct_child(style, _BASED_ON_TAG)
        if based_on is None:
            current_id = None
        else:
            current_id = based_on.get(_STYLE_VALUE_ATTRIBUTE)
            if not current_id:
                raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
    if current_id is not None:
        raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
    return _numbering_reference_from_properties(
        style_context.default_paragraph_properties
    )


def _numbering_reference_from_properties(
    properties: ET.Element | None,
) -> tuple[str, str] | None:
    """从单个 ``w:pPr`` 解析唯一 numPr 引用。

    输入参数：
        properties：直接、样式或默认段落属性，可为 ``None``。
    输出返回值：
        没有 numPr 时 ``None``；否则为已校验的 ``(numId, ilvl)``。
    """

    if properties is None:
        return None
    numbering = _unique_direct_child(properties, _NUMBERING_PROPERTIES_TAG)
    if numbering is None:
        return None
    number_id_node = _unique_direct_child(numbering, _NUMBERING_ID_TAG)
    level_id_node = _unique_direct_child(numbering, _NUMBERING_LEVEL_ID_TAG)
    number_id = (
        number_id_node.get(_STYLE_VALUE_ATTRIBUTE)
        if number_id_node is not None
        else None
    )
    level_id = (
        level_id_node.get(_STYLE_VALUE_ATTRIBUTE, "0")
        if level_id_node is not None
        else "0"
    )
    if (
        number_id is None
        or re.fullmatch(r"[0-9]{1,10}", number_id) is None
        or re.fullmatch(r"[0-8]", level_id) is None
    ):
        raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")
    return number_id, level_id


def _resolve_numbering_signature(
    context: _NumberingContext,
    *,
    num_id: str,
    level_id: str,
) -> str:
    """将一个已引用 numId 解析为与数字 ID 无关的语义摘要。

    输入参数：
        context：numbering 图；num_id/level_id：段落有效实例与层级。
    输出返回值：
        只含该层 abstract ``start/numFmt/lvlText`` 等全部子树、
        相关 instance override 与全局列表语义的 SHA-256；numId/
        abstractNumId 重编号不改变结果。
    """

    digest = hashlib.sha256()
    _update_digest(digest, ("numbering-level", level_id))
    if num_id == "0":
        _update_digest(digest, ("numbering-disabled",))
        return digest.hexdigest()
    instance = context.numbering_instances.get(num_id)
    if instance is None:
        raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")
    abstract_reference = _unique_direct_child(
        instance,
        _ABSTRACT_NUMBERING_ID_TAG,
    )
    abstract_id = (
        abstract_reference.get(_STYLE_VALUE_ATTRIBUTE)
        if abstract_reference is not None
        else None
    )
    if abstract_id is None or re.fullmatch(r"[0-9]{1,10}", abstract_id) is None:
        raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")
    abstract = context.abstract_definitions.get(abstract_id)
    if abstract is None:
        raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")
    matching_levels = [
        child
        for child in abstract
        if child.tag == _NUMBERING_LEVEL_TAG
        and child.get(_NUMBERING_LEVEL_ID_ATTRIBUTE) == level_id
    ]
    if len(matching_levels) != 1:
        raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")
    matching_overrides = [
        child
        for child in instance
        if child.tag == _NUMBERING_LEVEL_OVERRIDE_TAG
        and child.get(_NUMBERING_LEVEL_ID_ATTRIBUTE) == level_id
    ]
    if len(matching_overrides) > 1:
        raise WordTextFidelityError("WORD_TEXT_NUMBERING_GRAPH_INVALID")

    _update_digest(
        digest,
        tuple(
            ["abstract-global"]
            + [
                f"{name}={value}"
                for name, value in sorted(abstract.attrib.items())
                if name != _ABSTRACT_NUMBERING_ID_ATTRIBUTE
            ]
        ),
    )
    for child in abstract:
        if child.tag in _NUMBERING_VOLATILE_TAGS or child.tag == _NUMBERING_LEVEL_TAG:
            continue
        _digest_xml_element(digest, child)
    _digest_xml_element(digest, matching_levels[0])
    for child in instance:
        if child.tag in {
            _ABSTRACT_NUMBERING_ID_TAG,
            _NUMBERING_LEVEL_OVERRIDE_TAG,
        }:
            continue
        _digest_xml_element(digest, child)
    if matching_overrides:
        _digest_xml_element(digest, matching_overrides[0])
    return digest.hexdigest()


def _style_reference(properties: ET.Element, tag: str) -> str | None:
    """从属性容器解析唯一非空样式引用。

    输入参数：
        properties：``w:pPr`` 或 ``w:rPr``；tag：pStyle/rStyle QName。
    输出返回值：
        未引用时 ``None``，否则返回精确 style ID。
    """

    reference = _unique_direct_child(properties, tag)
    if reference is None:
        return None
    style_id = reference.get(_STYLE_VALUE_ATTRIBUTE)
    if not style_id:
        raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
    return style_id


def _style_chain_semantics(
    style_id: str,
    *,
    expected_type: str,
    style_context: _StyleContext,
) -> dict[str, str]:
    """闭合样式 ``basedOn`` 链并解析有效文字语义。

    输入参数：
        style_id：起始样式；expected_type：paragraph/character；
        style_context：唯一样式图。
    输出返回值：
        最多 64 层、按子→父优先级展平的属性映射。
        空包装层与样式 ID 重命名不改变结果。
    """

    semantics: dict[str, str] = {}
    visited: set[str] = set()
    current_id: str | None = style_id
    for _depth in range(64):
        if current_id is None:
            return semantics
        if current_id in visited:
            raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
        style = style_context.styles.get(current_id)
        if style is None or style.get(_STYLE_TYPE_ATTRIBUTE) != expected_type:
            raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
        visited.add(current_id)
        paragraph_properties = _unique_direct_child(
            style,
            _PARAGRAPH_PROPERTIES_TAG,
        )
        run_properties = _unique_direct_child(style, _RUN_PROPERTIES_TAG)
        if paragraph_properties is not None:
            _merge_missing_semantics(
                semantics,
                _selected_paragraph_property_signatures(paragraph_properties),
            )
        if run_properties is not None:
            _merge_missing_semantics(
                semantics,
                _selected_run_property_signatures(run_properties),
            )
        based_on = _unique_direct_child(style, _BASED_ON_TAG)
        if based_on is None:
            current_id = None
        else:
            parent_id = based_on.get(_STYLE_VALUE_ATTRIBUTE)
            if not parent_id:
                raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")
            current_id = parent_id
    if current_id is None:
        return semantics
    raise WordTextFidelityError("WORD_TEXT_STYLE_GRAPH_INVALID")


def _selected_paragraph_property_signatures(
    properties: ET.Element,
) -> dict[str, str]:
    """提取与正文语义相关的段落属性子集。

    输入参数：
        properties：``w:pPr``。
    输出返回值：
        numbering 与段落默认 run 可见性/颜色/字号
        的 key→规范摘要。行距、对齐和非文字版式不进入。
    """

    run_properties = _unique_direct_child(properties, _RUN_PROPERTIES_TAG)
    semantics: dict[str, str] = {}
    if run_properties is not None:
        semantics.update(_selected_run_property_signatures(run_properties))
    return semantics


def _selected_run_property_signatures(
    properties: ET.Element,
) -> dict[str, str]:
    """提取会改变文字可见性的 run 属性。

    输入参数：
        properties：``w:rPr``。
    输出返回值：
        vanish/webHidden/非 auto 颜色/字号/右至左方向的
        key→规范摘要。
        ``color=auto`` 与未声明颜色统一视为自动颜色。
    """

    selected: dict[str, ET.Element] = {}
    for child in properties:
        if child.tag not in _SELECTED_RUN_PROPERTY_TAGS:
            continue
        if child.tag in selected:
            raise WordTextFidelityError("WORD_TEXT_PROPERTY_STRUCTURE_INVALID")
        if (
            child.tag == f"{{{_WORD_NAMESPACE}}}color"
            and child.get(_STYLE_VALUE_ATTRIBUTE, "auto").casefold() == "auto"
        ):
            continue
        selected[child.tag] = child
    return {
        f"run-semantic-property:{tag}": _xml_element_signature(element)
        for tag, element in selected.items()
    }


def _merge_missing_semantics(
    target: dict[str, str],
    fallback: dict[str, str],
) -> None:
    """以 OOXML 子样式优先级合并有效语义。

    输入参数：
        target：已有更高优先级属性；fallback：父样式/默认属性。
    输出返回值：
        无；仅将 target 未声明的 key 写入。
    """

    for key, value in fallback.items():
        target.setdefault(key, value)


def _semantic_map_signature(label: str, semantics: dict[str, str]) -> str:
    """将已展平的有效语义映射投影为稳定摘要。

    输入参数：
        label：token 家族标识；semantics：已规范化属性映射。
    输出返回值：
        与样式 ID、空 basedOn 层、XML 属性顺序无关的 SHA-256。
    """

    digest = hashlib.sha256()
    _update_digest(digest, (label,))
    for key, value in sorted(semantics.items()):
        _update_digest(digest, ("semantic-property", key, value))
    return digest.hexdigest()


def _xml_element_signature(element: ET.Element) -> str:
    """将单个语义 XML 子树规范化为摘要。

    输入参数：
        element：已选中的 numbering/run 语义节点。
    输出返回值：
        展开 QName、排序属性与子节点顺序的 SHA-256。
    """

    digest = hashlib.sha256()
    _digest_xml_element(digest, element)
    return digest.hexdigest()


def _digest_xml_element(
    digest: object,
    element: ET.Element,
    *,
    excluded_tags: frozenset[str] = frozenset(),
) -> None:
    """以展开 QName、排序属性和子节点顺序规范化 XML。

    输入参数：
        digest：支持 ``update`` 的摘要对象；element：待投影子树；
        excluded_tags：由任务目标显式允许差异的 QName 闭集。
    输出返回值：
        无；namespace prefix 和属性原始顺序不影响结果，语义子节点
        顺序与精确属性值会进入摘要。
    """

    _update_digest(
        digest,
        (
            "xml-start",
            element.tag,
            *((f"{name}={value}") for name, value in sorted(element.attrib.items())),
        ),
    )
    if element.text:
        _update_digest(digest, ("xml-text", element.text))
    for child in element:
        if child.tag in excluded_tags:
            continue
        _digest_xml_element(digest, child, excluded_tags=excluded_tags)
        if child.tail:
            _update_digest(digest, ("xml-tail", child.tail))
    _update_digest(digest, ("xml-end", element.tag))


def _update_digest(digest: object, fields: tuple[str, ...]) -> None:
    """用长度前缀更新 typed token 摘要，避免拼接歧义。

    输入参数：
        digest：支持 ``update(bytes)`` 的 SHA-256 对象；
        fields：一个 token 的有序 Unicode 字段。
    输出返回值：
        无；将每个 UTF-8 字段以八字节长度前缀写入摘要。
    """

    update = getattr(digest, "update")
    update(struct.pack(">Q", len(fields)))
    for field in fields:
        encoded = field.encode("utf-8", "strict")
        update(struct.pack(">Q", len(encoded)))
        update(encoded)


__all__ = [
    "WordTextBaseline",
    "WordTextFidelityError",
    "WordTextFidelityResult",
    "WordTextInputFile",
    "capture_word_text_baseline",
    "compare_word_text_fidelity",
    "validate_word_text_baseline_identity",
]
