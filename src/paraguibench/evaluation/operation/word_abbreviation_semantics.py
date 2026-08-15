"""Word-012 基于固定输入的逐处缩写语义保真协议。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
import secrets
import xml.etree.ElementTree as ET
import zipfile

from .word_text_fidelity import (
    WordTextFidelityError,
    WordTextInputFile,
    _DocumentTextSnapshot,
    _document_snapshot_shape_is_valid,
    _document_snapshots_match,
    _read_regular_file_nofollow,
    _snapshot_document,
)


_TASK_ID = "Operation-FileOperate-BatchOperationWord-012"
_PROTOCOL_ID = "paraguibench.operation.eval-rules.v1"
_MANIFEST_SHA256 = "00b56d5ab84094a98e70156f399881792fe01a649b945284705f79ec050bf1f2"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_PARAGRAPH_TAG = f"{{{_WORD_NAMESPACE}}}p"
_TEXT_TAG = f"{{{_WORD_NAMESPACE}}}t"
_DOCUMENT_MEMBER = "word/document.xml"
_BASELINE_INTEGRITY_KEY = secrets.token_bytes(32)


class WordAbbreviationError(RuntimeError):
    """表示 Word-012 baseline 或 post 无法可靠比较。

    输入参数：
        code：不含正文、路径、摘要或语义映射的固定错误码。
    输出返回值：
        runtime 可将其统一映射为 evaluator ``ERROR/null``。
    """

    def __init__(self, code: str) -> None:
        """构造脱敏错误。

        输入参数：
            code：由协议内部选择的固定码。
        输出返回值：
            无；仅保存错误码，不接受动态详情。
        """

        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class _OccurrenceContract:
    """保存单个缩写在固定语境中的唯一替换。"""

    left_context: str
    source: str
    right_context: str
    replacement: str


@dataclass(frozen=True, slots=True, repr=False)
class _DocumentContract:
    """保存一份正式 DOCX 的源段落摘要与有序语境闭集。"""

    paragraph_sha256: str
    occurrences: tuple[_OccurrenceContract, ...]


_DOCUMENT_CONTRACTS = {
    "Clinical Procedure.docx": _DocumentContract(
        paragraph_sha256=(
            "d3557930d702907f0e44f159387e0a9771a5158a0d1b6c51d188ebbb6de15634"
        ),
        occurrences=(
            _OccurrenceContract(
                "recorded the ",
                "MAC (Minimum Alveolar Concentration)",
                "\u00a0every",
                "MAC (Minimum Alveolar Concentration)",
            ),
            _OccurrenceContract(
                "Keeping the\u00a0",
                "MAC",
                "\u00a0at 1.2%",
                "MAC (Minimum Alveolar Concentration)",
            ),
            _OccurrenceContract(
                "used a\u00a0",
                "MAC",
                "\u00a0tablet",
                "MAC (Macintosh)",
            ),
            _OccurrenceContract(
                "The\u00a0",
                "MAC",
                "\u00a0software",
                "MAC (Macintosh)",
            ),
            _OccurrenceContract(
                "the\u00a0",
                "MAC",
                "\u00a0value",
                "MAC (Minimum Alveolar Concentration)",
            ),
        ),
    ),
    "Hardware Review.docx": _DocumentContract(
        paragraph_sha256=(
            "f00f50d38d615cb16eb037ad5e851f72ad97cc25c0f82f8650c1aa82cae8d95c"
        ),
        occurrences=(
            _OccurrenceContract(
                "new ",
                "MAC (Macintosh)",
                " models",
                "MAC (Macintosh)",
            ),
            _OccurrenceContract(
                "prefer the\u00a0",
                "MAC",
                "\u00a0for video",
                "MAC (Macintosh)",
            ),
            _OccurrenceContract(
                "check the\u00a0",
                "MAC",
                "\u00a0address",
                "MAC (Media Access Control)",
            ),
            _OccurrenceContract(
                "valid\u00a0",
                "MAC",
                ", the Bluetooth",
                "MAC (Media Access Control)",
            ),
            _OccurrenceContract(
                "with the\u00a0",
                "MAC",
                "\u00a0workstation",
                "MAC (Macintosh)",
            ),
        ),
    ),
    "Infrastructure Log.docx": _DocumentContract(
        paragraph_sha256=(
            "b60d99a66d6553fbcf798503f6a778d067032b5e673633ba3471f8a4f78fcee1"
        ),
        occurrences=(
            _OccurrenceContract(
                "configured the ",
                "Mac(Media Access Controller)",
                " address",
                "Mac (Media Access Control)",
            ),
            _OccurrenceContract(
                "verifying the\u00a0",
                "Mac",
                " in the router",
                "Mac (Media Access Control)",
            ),
            _OccurrenceContract(
                "opened his\u00a0",
                "MAC",
                "\u00a0to download",
                "MAC (Macintosh)",
            ),
            _OccurrenceContract(
                "that the\u00a0",
                "MAC",
                "\u00a0OS interface",
                "MAC (Macintosh)",
            ),
        ),
    ),
    "Security Protocol.docx": _DocumentContract(
        paragraph_sha256=(
            "5f2a5fd4583fb04da6fee2e2b3980005ff76394dcd30e11289d7ac789d3c0124"
        ),
        occurrences=(
            _OccurrenceContract(
                "with a\u00a0",
                "MAC (Message Authentication Code)",
                "\u00a0to prevent",
                "MAC (Message Authentication Code)",
            ),
            _OccurrenceContract(
                "While the\u00a0",
                "MAC",
                "\u00a0ensures data integrity",
                "MAC (Message Authentication Code)",
            ),
            _OccurrenceContract(
                "in the\u00a0",
                "MAC",
                "\u00a0address filtering",
                "MAC (Media Access Control)",
            ),
            _OccurrenceContract(
                "The\u00a0",
                "MAC",
                "\u00a0was spoofed",
                "MAC (Media Access Control)",
            ),
        ),
    ),
}
_DOCUMENT_PATHS = tuple(_DOCUMENT_CONTRACTS)


@dataclass(frozen=True, slots=True, repr=False)
class WordAbbreviationBaseline:
    """保存 Word-012 不含原文的 evaluator-only 目标快照。

    输入参数：
        task_id/protocol_id/manifest_sha256：正式任务身份；
        expected_documents：逐处语境合同生成的四份不可逆 typed 快照；
        _integrity_mac：仅当前 evaluator 进程可重算的复合身份封印。
    输出返回值：
        不可变 DTO；``repr`` 不暴露路径、摘要或缩写映射。
    """

    task_id: str
    protocol_id: str
    manifest_sha256: str
    expected_documents: tuple[_DocumentTextSnapshot, ...]
    _integrity_mac: bytes

    def __repr__(self) -> str:
        """返回仅含安全身份与分母的调试表示。

        输入参数：无。
        输出返回值：不含文件名、原文、期望文字或摘要的字符串。
        """

        return (
            "WordAbbreviationBaseline("
            f"task_id={self.task_id!r}, protocol_id={self.protocol_id!r}, "
            f"document_count={len(self.expected_documents)!r})"
        )


@dataclass(frozen=True, slots=True)
class WordAbbreviationResult:
    """保存 Word-012 可比较时的脱敏结果。"""

    matched: bool
    document_count: int


def capture_word_abbreviation_baseline(
    *,
    task_id: str,
    protocol_id: str,
    manifest_sha256: str,
    source_root: Path,
    files: tuple[WordTextInputFile, ...],
) -> WordAbbreviationBaseline:
    """在首次 guest I/O 前由固定四 DOCX 构造逐处语境 baseline。

    输入参数：
        task_id/protocol_id/manifest_sha256：必须命中 Word-012 正式身份；
        source_root：已验证 host cache 根；files：manifest 顺序的四文档闭集。
    输出返回值：
        只含预期 typed 摘要的 ``WordAbbreviationBaseline``。
    异常：
        WordAbbreviationError：身份、nofollow 读取、源段落、语境唯一性或 DOCX 解析失败。
    """

    _validate_capture_identity(task_id, protocol_id, manifest_sha256, files)
    expected_documents: list[_DocumentTextSnapshot] = []
    for file in files:
        try:
            payload = _read_regular_file_nofollow(
                source_root,
                file.path,
                expected_size=file.size,
                expected_sha256=file.sha256,
            )
            _snapshot_document(file.path, payload, task_id=task_id)
            expected_payload = _build_expected_document(file.path, payload)
            expected_documents.append(
                _snapshot_document(
                    file.path,
                    expected_payload,
                    task_id=task_id,
                )
            )
        except WordTextFidelityError:
            raise WordAbbreviationError("WORD_ABBREVIATION_DOCUMENT_INVALID") from None
    documents = tuple(expected_documents)
    return WordAbbreviationBaseline(
        task_id=task_id,
        protocol_id=protocol_id,
        manifest_sha256=manifest_sha256,
        expected_documents=documents,
        _integrity_mac=_baseline_integrity_mac(
            task_id=task_id,
            protocol_id=protocol_id,
            manifest_sha256=manifest_sha256,
            documents=documents,
        ),
    )


def compare_word_abbreviation_semantics(
    baseline: WordAbbreviationBaseline,
    result_root: Path,
) -> WordAbbreviationResult:
    """将 post DOCX 与逐处语境期望 typed 快照全等比较。

    输入参数：
        baseline：prepare 前构造的 evaluator-only DTO；
        result_root：Agent 后已冻结的 host artifact 根。
    输出返回值：
        四文档是否全部命中与固定分母。
    异常：
        WordAbbreviationError：DTO 类型、post 安全读取或 OOXML 解析失败。
    """

    if not isinstance(baseline, WordAbbreviationBaseline):
        raise WordAbbreviationError("WORD_ABBREVIATION_BASELINE_INVALID")
    validate_word_abbreviation_baseline_identity(
        baseline,
        task_id=_TASK_ID,
        protocol_id=_PROTOCOL_ID,
        manifest_sha256=_MANIFEST_SHA256,
        document_paths=_DOCUMENT_PATHS,
    )
    matched = True
    try:
        for expected in baseline.expected_documents:
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
    except WordTextFidelityError:
        raise WordAbbreviationError("WORD_ABBREVIATION_POST_INVALID") from None
    return WordAbbreviationResult(
        matched=matched,
        document_count=len(baseline.expected_documents),
    )


def validate_word_abbreviation_baseline_identity(
    baseline: WordAbbreviationBaseline,
    *,
    task_id: str,
    protocol_id: str,
    manifest_sha256: str,
    document_paths: tuple[str, ...],
) -> None:
    """在任何 post I/O 前将 abbreviation baseline 绑定到正式合同。

    输入参数：
        baseline：待校验 evaluator-only DTO；task_id/protocol_id/
        manifest_sha256/document_paths：production evaluator 内部固定身份。
    输出返回值：
        无；任务、协议、manifest、四路径顺序或私有快照形状任一漂移即拒绝。
    """

    if not isinstance(baseline, WordAbbreviationBaseline):
        raise WordAbbreviationError("WORD_ABBREVIATION_BASELINE_INVALID")
    documents = baseline.expected_documents
    integrity_mac = baseline._integrity_mac
    if (
        baseline.task_id != task_id
        or baseline.protocol_id != protocol_id
        or baseline.manifest_sha256 != manifest_sha256
        or not isinstance(baseline.manifest_sha256, str)
        or _SHA256_PATTERN.fullmatch(baseline.manifest_sha256) is None
        or not isinstance(documents, tuple)
        or len(documents) != 4
        or not isinstance(document_paths, tuple)
        or document_paths != _DOCUMENT_PATHS
        or not all(_document_snapshot_shape_is_valid(item) for item in documents)
        or tuple(item.path for item in documents) != document_paths
        or not isinstance(integrity_mac, bytes)
        or len(integrity_mac) != hashlib.sha256().digest_size
    ):
        raise WordAbbreviationError("WORD_ABBREVIATION_BASELINE_INVALID")
    expected_integrity_mac = _baseline_integrity_mac(
        task_id=baseline.task_id,
        protocol_id=baseline.protocol_id,
        manifest_sha256=baseline.manifest_sha256,
        documents=documents,
    )
    if not hmac.compare_digest(integrity_mac, expected_integrity_mac):
        raise WordAbbreviationError("WORD_ABBREVIATION_BASELINE_INVALID")


def _baseline_integrity_mac(
    *,
    task_id: str,
    protocol_id: str,
    manifest_sha256: str,
    documents: tuple[_DocumentTextSnapshot, ...],
) -> bytes:
    """对 prepare 时的四文档复合快照生成进程内认证码。

    输入参数：
        task_id/protocol_id/manifest_sha256：已在 guest I/O 前验证的
        正式身份；documents：从同一批 held-descriptor 源字节构造的
        四份期望 typed 快照。
    输出返回值：
        HMAC-SHA256 字节；任一 digest/count/relationship/media 字段
        被 schema-valid 值替换后都无法通过后续 identity 门禁。
    """

    payload = {
        "task_id": task_id,
        "protocol_id": protocol_id,
        "manifest_sha256": manifest_sha256,
        "documents": [
            {
                "path": document.path,
                "digest": document.digest,
                "token_count": document.token_count,
                "part_count": document.part_count,
                "relationship_digest": document.relationship_digest,
                "image_relationships": document.image_relationships,
                "media_identities": document.media_identities,
            }
            for document in documents
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hmac.digest(_BASELINE_INTEGRITY_KEY, encoded, "sha256")


def _validate_capture_identity(
    task_id: str,
    protocol_id: str,
    manifest_sha256: str,
    files: tuple[WordTextInputFile, ...],
) -> None:
    """校验 baseline 构造时的固定任务、manifest 与四文档闭集。

    输入参数：
        task_id/protocol_id/manifest_sha256/files：调用方提供的完整 pre 身份。
    输出返回值：
        无；仅允许 Word-012 正式协议、manifest SHA 与四 DOCX 顺序。
    """

    if (
        task_id != _TASK_ID
        or protocol_id != _PROTOCOL_ID
        or manifest_sha256 != _MANIFEST_SHA256
        or not isinstance(files, tuple)
        or len(files) != 4
        or not all(isinstance(file, WordTextInputFile) for file in files)
        or tuple(file.path for file in files) != _DOCUMENT_PATHS
        or not all(file.is_docx for file in files)
    ):
        raise WordAbbreviationError("WORD_ABBREVIATION_BASELINE_INVALID")


def _build_expected_document(path: str, payload: bytes) -> bytes:
    """根据逐处语境合同在内存中生成唯一期望 DOCX。

    输入参数：
        path：当前固定文件路径；payload：同一 held descriptor 读取的 source 字节。
    输出返回值：
        只改写目标文字节点、其它 ZIP member 原样保留的 DOCX 字节。
    """

    contract = _DOCUMENT_CONTRACTS.get(path)
    if contract is None:
        raise WordAbbreviationError("WORD_ABBREVIATION_CONTRACT_INVALID")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = [
                (info, archive.read(info.filename)) for info in archive.infolist()
            ]
    except (OSError, KeyError, zipfile.BadZipFile):
        raise WordAbbreviationError("WORD_ABBREVIATION_DOCUMENT_INVALID") from None
    names = [info.filename for info, _item_payload in entries]
    if names.count(_DOCUMENT_MEMBER) != 1:
        raise WordAbbreviationError("WORD_ABBREVIATION_DOCUMENT_INVALID")
    rewritten: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info, item_payload in entries:
        if info.filename == _DOCUMENT_MEMBER:
            try:
                root = ET.fromstring(item_payload)
            except ET.ParseError:
                raise WordAbbreviationError(
                    "WORD_ABBREVIATION_DOCUMENT_INVALID"
                ) from None
            _apply_document_contract(root, contract)
            item_payload = ET.tostring(
                root,
                encoding="utf-8",
                xml_declaration=True,
            )
        rewritten.append((info, item_payload))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for info, item_payload in rewritten:
            archive.writestr(info, item_payload)
    return output.getvalue()


def _apply_document_contract(
    root: ET.Element,
    contract: _DocumentContract,
) -> None:
    """在唯一固定段落中校验并应用全部非重叠语境替换。

    输入参数：
        root：``word/document.xml`` 根；contract：当前文档的源摘要与 occurrence 闭集。
    输出返回值：
        无；就地改写对应 ``w:t``，保留目标之外文字、容器与样式。
    """

    candidates: list[tuple[ET.Element, list[ET.Element], str]] = []
    for paragraph in root.iter(_PARAGRAPH_TAG):
        text_nodes = list(paragraph.iter(_TEXT_TAG))
        text = "".join(node.text or "" for node in text_nodes)
        if (
            hashlib.sha256(text.encode("utf-8")).hexdigest()
            == contract.paragraph_sha256
        ):
            candidates.append((paragraph, text_nodes, text))
    if len(candidates) != 1:
        raise WordAbbreviationError("WORD_ABBREVIATION_SOURCE_CONTRACT_INVALID")
    _paragraph, text_nodes, source_text = candidates[0]
    spans: list[tuple[int, int, str]] = []
    for occurrence in contract.occurrences:
        marker = occurrence.left_context + occurrence.source + occurrence.right_context
        marker_start = source_text.find(marker)
        if marker_start < 0 or source_text.find(marker, marker_start + 1) >= 0:
            raise WordAbbreviationError("WORD_ABBREVIATION_SOURCE_CONTRACT_INVALID")
        start = marker_start + len(occurrence.left_context)
        end = start + len(occurrence.source)
        spans.append((start, end, occurrence.replacement))
    spans.sort()
    if any(first[1] > second[0] for first, second in zip(spans, spans[1:])):
        raise WordAbbreviationError("WORD_ABBREVIATION_SOURCE_CONTRACT_INVALID")
    positions = _text_node_positions(text_nodes)
    for start, end, replacement in reversed(spans):
        _replace_text_span(positions, start, end, replacement)


def _text_node_positions(
    text_nodes: list[ET.Element],
) -> list[tuple[ET.Element, int, int]]:
    """将段落文字节点投影到全局字符区间。

    输入参数：
        text_nodes：按 document order 收集的 ``w:t`` 列表。
    输出返回值：
        ``(节点, 起始偏移, 结束偏移)`` 的有序列表。
    """

    positions: list[tuple[ET.Element, int, int]] = []
    offset = 0
    for node in text_nodes:
        end = offset + len(node.text or "")
        positions.append((node, offset, end))
        offset = end
    return positions


def _replace_text_span(
    positions: list[tuple[ET.Element, int, int]],
    start: int,
    end: int,
    replacement: str,
) -> None:
    """在原始节点边界上替换一个非空全局字符区间。

    输入参数：
        positions：替换前的全局节点区间；start/end：左闭右开位置；
        replacement：该语境唯一规范文字。
    输出返回值：
        无；保留起始节点前缀与结束节点后缀，清空被覆盖的中间文字。
    """

    start_item = next(
        (item for item in positions if item[1] <= start < item[2]),
        None,
    )
    end_item = next(
        (item for item in positions if item[1] < end <= item[2]),
        None,
    )
    if start_item is None or end_item is None or start >= end:
        raise WordAbbreviationError("WORD_ABBREVIATION_SOURCE_CONTRACT_INVALID")
    start_node, start_base, _start_end = start_item
    end_node, end_base, _end_end = end_item
    start_offset = start - start_base
    end_offset = end - end_base
    start_text = start_node.text or ""
    end_text = end_node.text or ""
    if start_node is end_node:
        start_node.text = (
            start_text[:start_offset] + replacement + start_text[end_offset:]
        )
        return
    start_node.text = start_text[:start_offset] + replacement
    covered = False
    for node, _node_start, _node_end in positions:
        if node is start_node:
            covered = True
            continue
        if not covered:
            continue
        if node is end_node:
            node.text = end_text[end_offset:]
            return
        node.text = ""
    raise WordAbbreviationError("WORD_ABBREVIATION_SOURCE_CONTRACT_INVALID")


__all__ = [
    "WordAbbreviationBaseline",
    "WordAbbreviationError",
    "WordAbbreviationResult",
    "capture_word_abbreviation_baseline",
    "compare_word_abbreviation_semantics",
    "validate_word_abbreviation_baseline_identity",
]
