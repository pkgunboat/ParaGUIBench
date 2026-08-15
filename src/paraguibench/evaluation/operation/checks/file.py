"""Operation 目录与基础格式检查原语。"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import xml.etree.ElementTree as ElementTree
import zipfile


_CONTENT_TYPES_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/content-types"
)
_PACKAGE_RELATIONSHIPS_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
_WORDPROCESSINGML_NAMESPACE = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
)
_OFFICE_DOCUMENT_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
    }
)
_DOCX_MAIN_CONTENT_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "application/vnd.ms-word.document.macroEnabled.main+xml",
    }
)


def check_named_files_exist(
    result_dir: str, params: dict[str, object]
) -> dict[str, object]:
    """检查固定名称文件的存在性、基础格式和禁用旧名称。

    输入参数：
        result_dir：已通过安全预检的 Agent artifact 根目录。
        params：包含 ``filenames``、``search_subdirs``、``validate_format``
            与可选 ``forbidden_filenames`` 的 canonical 规则参数。
    输出返回值：
        旧协议兼容的 ``pass/score/reason`` 字典；reason 只在本模块内部使用。
    """

    filenames = params.get("filenames")
    if (
        not isinstance(filenames, list)
        or not filenames
        or not all(isinstance(name, str) and name for name in filenames)
    ):
        return _config_error("filenames")
    forbidden = params.get("forbidden_filenames", [])
    if not isinstance(forbidden, list) or not all(
        isinstance(name, str) and name for name in forbidden
    ):
        return _config_error("forbidden_filenames")
    search_subdirs = params.get("search_subdirs", True) is True
    validate_format = params.get("validate_format", True) is True
    root = Path(result_dir)
    if "rename_contract" in params:
        return _check_exact_rename_contract(
            root,
            params,
            filenames=filenames,
            forbidden=forbidden,
            validate_format=validate_format,
        )

    found = 0
    for name in filenames:
        matches = _find_exact_name(root, name, recursive=search_subdirs)
        if any(not validate_format or _is_valid_named_file(path) for path in matches):
            found += 1
    forbidden_found = any(
        _find_exact_name(root, name, recursive=search_subdirs) for name in forbidden
    )
    score = found / len(filenames)
    if forbidden_found:
        score = 0.0
    return {
        "pass": score >= 1.0 - 1e-9,
        "score": round(score, 4),
        "reason": "matched" if score >= 1.0 - 1e-9 else "mismatch",
    }


def _check_exact_rename_contract(
    root: Path,
    params: Mapping[str, object],
    *,
    filenames: list[object],
    forbidden: list[object],
    validate_format: bool,
) -> dict[str, object]:
    """按完整文件闭集验证 CombinationDocs-008 原子重命名。

    输入参数：
        root：已由上层安全预检的 artifact 根目录；
        params：canonical 规则参数；filenames/forbidden：已完成
            基础类型检查的新名与旧名列表；
        validate_format：是否启用 OOXML 格式门，正式合同必须为真。
    输出返回值：
        旧协议兼容结果；只有根级保留文件与 ``output``
        中三份 DOCX 的路径、字节身份和格式全部一致时通过。
    """

    contract = params.get("rename_contract")
    output_directory = params.get("output_directory")
    expected_document_count = params.get("expected_document_count")
    if (
        not isinstance(contract, Mapping)
        or not _safe_basename(output_directory)
        or not isinstance(expected_document_count, int)
        or isinstance(expected_document_count, bool)
        or expected_document_count <= 0
        or not validate_format
    ):
        return _config_error("rename_contract")
    documents = contract.get("documents")
    preserved_files = contract.get("preserved_files")
    if (
        not isinstance(documents, list)
        or len(documents) != expected_document_count
        or not isinstance(preserved_files, list)
        or not preserved_files
    ):
        return _config_error("rename_contract")

    document_identities: dict[str, tuple[int, str]] = {}
    source_filenames: list[str] = []
    output_filenames: list[str] = []
    for item in documents:
        if not isinstance(item, Mapping):
            return _config_error("rename_contract")
        source_filename = item.get("source_filename")
        output_filename = item.get("output_filename")
        identity = _parse_identity(
            item,
            size_key="source_size",
            sha256_key="source_sha256",
        )
        if (
            not _safe_basename(source_filename)
            or not _safe_basename(output_filename)
            or not source_filename.lower().endswith(".docx")
            or not output_filename.lower().endswith(".docx")
            or identity is None
        ):
            return _config_error("rename_contract")
        source_filenames.append(source_filename)
        output_filenames.append(output_filename)
        document_identities[f"{output_directory}/{output_filename}"] = identity

    preserved_identities: dict[str, tuple[int, str]] = {}
    for item in preserved_files:
        if not isinstance(item, Mapping):
            return _config_error("rename_contract")
        path = item.get("path")
        identity = _parse_identity(item, size_key="size", sha256_key="sha256")
        if not _safe_basename(path) or identity is None:
            return _config_error("rename_contract")
        preserved_identities[path] = identity

    if (
        filenames != output_filenames
        or set(forbidden) != set(source_filenames)
        or len(set(source_filenames)) != expected_document_count
        or len(set(output_filenames)) != expected_document_count
        or len(document_identities) != expected_document_count
        or len(preserved_identities) != len(preserved_files)
        or not _casefold_unique(
            (*document_identities.keys(), *preserved_identities.keys())
        )
    ):
        return _config_error("rename_contract")

    collected = _collect_closed_tree(root)
    if collected is None:
        return _rename_mismatch(expected_document_count)
    actual_files, actual_directories = collected
    expected_files = {
        **document_identities,
        **preserved_identities,
    }
    if set(actual_files) != set(expected_files) or actual_directories != {
        output_directory
    }:
        return _rename_mismatch(expected_document_count)

    for relative_path, expected_identity in expected_files.items():
        path = actual_files[relative_path]
        if _regular_file_identity(path) != expected_identity:
            return _rename_mismatch(expected_document_count)
    for relative_path in document_identities:
        if not _is_valid_docx_package(actual_files[relative_path]):
            return _rename_mismatch(expected_document_count)
    for relative_path in preserved_identities:
        if not _is_valid_named_file(actual_files[relative_path]):
            return _rename_mismatch(expected_document_count)
    return {
        "pass": True,
        "score": 1.0,
        "reason": "matched",
        "_evaluated_artifact_count": expected_document_count,
    }


def _parse_identity(
    item: Mapping[str, object],
    *,
    size_key: str,
    sha256_key: str,
) -> tuple[int, str] | None:
    """从受摘要绑定的规则行解析字节身份。

    输入参数：
        item：文档或保留文件规格；size_key/sha256_key：固定字段名。
    输出返回值：
        合法时返回 ``(size, sha256)``，布尔数、非正整数或
        非小写 64 位十六进制摘要均返回 ``None``。
    """

    size = item.get(size_key)
    sha256 = item.get(sha256_key)
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        return None
    return size, sha256


def _safe_basename(value: object) -> bool:
    """判断合同路径元素是否为单一安全 basename。

    输入参数：
        value：来自 canonical 规则的文件名或目录名。
    输出返回值：
        非空单段名称返回 ``True``；绝对路径、分隔符、NUL
        或点目录返回 ``False``。
    """

    return (
        isinstance(value, str)
        and bool(value)
        and "\x00" not in value
        and "/" not in value
        and "\\" not in value
        and value not in {".", ".."}
        and Path(value).name == value
    )


def _casefold_unique(paths: tuple[str, ...]) -> bool:
    """拒绝仅大小写不同的 canonical 路径碰撞。

    输入参数：
        paths：要纳入文件闭集的 POSIX 相对路径。
    输出返回值：
        所有路径 ``casefold`` 后仍唯一时返回 ``True``。
    """

    folded = tuple(path.casefold() for path in paths)
    return len(folded) == len(set(folded))


def _collect_closed_tree(
    root: Path,
) -> tuple[dict[str, Path], set[str]] | None:
    """以 no-follow 语义收集完整 artifact 文件与目录闭集。

    输入参数：
        root：要评价的单 Attempt artifact 根目录。
    输出返回值：
        成功时返回相对路径到普通文件的映射及目录集；根无效、
        不可读、符号链接或特殊节点返回 ``None``。
    """

    try:
        root_stat = root.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return None
    files: dict[str, Path] = {}
    directories: set[str] = set()
    walk_errors: list[OSError] = []
    try:
        walker = os.walk(
            root,
            followlinks=False,
            onerror=walk_errors.append,
        )
        for current_root, dirnames, filenames in walker:
            current = Path(current_root)
            for name in dirnames:
                path = current / name
                item_stat = path.lstat()
                if stat.S_ISLNK(item_stat.st_mode) or not stat.S_ISDIR(
                    item_stat.st_mode
                ):
                    return None
                directories.add(path.relative_to(root).as_posix())
            for name in filenames:
                path = current / name
                item_stat = path.lstat()
                if stat.S_ISLNK(item_stat.st_mode) or not stat.S_ISREG(
                    item_stat.st_mode
                ):
                    return None
                relative_path = path.relative_to(root).as_posix()
                if relative_path in files:
                    return None
                files[relative_path] = path
    except OSError:
        return None
    if walk_errors or not _casefold_unique(tuple(files)):
        return None
    return files, directories


def _regular_file_identity(path: Path) -> tuple[int, str] | None:
    """通过同一 nofollow 文件描述符计算 size 和 SHA-256。

    输入参数：
        path：闭集收集阶段识别的候选普通文件。
    输出返回值：
        成功返回 ``(size, sha256)``；竞态替换、symlink 或 I/O
        错误返回 ``None``。
    """

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        item_stat = os.fstat(descriptor)
        if not stat.S_ISREG(item_stat.st_mode):
            return None
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return item_stat.st_size, digest.hexdigest()
    except OSError:
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _is_valid_docx_package(path: Path) -> bool:
    """使用标准库校验 DOCX 的 OPC 关系、内容类型与主文档根。

    输入参数：
        path：字节身份已命中正式源文档的 ``.docx`` 路径。
    输出返回值：
        ZIP CRC、Content Types、package relationship 和 WordprocessingML
        主文档都有效时返回 ``True``，否则返回 ``False``。
    """

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if (
                len(names) != len(set(names))
                or archive.testzip() is not None
                or not {
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "word/document.xml",
                }.issubset(names)
            ):
                return False
            content_types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
            relationships = ElementTree.fromstring(archive.read("_rels/.rels"))
            document = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        return False

    if content_types.tag != f"{{{_CONTENT_TYPES_NAMESPACE}}}Types":
        return False
    document_overrides = tuple(
        element
        for element in content_types
        if element.tag == f"{{{_CONTENT_TYPES_NAMESPACE}}}Override"
        and element.get("PartName") == "/word/document.xml"
        and element.get("ContentType") in _DOCX_MAIN_CONTENT_TYPES
    )
    if len(document_overrides) != 1:
        return False
    if relationships.tag != (f"{{{_PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationships"):
        return False
    office_relationships = tuple(
        element
        for element in relationships
        if element.tag == (f"{{{_PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationship")
        and element.get("Type") in _OFFICE_DOCUMENT_RELATIONSHIP_TYPES
        and element.get("TargetMode", "Internal") == "Internal"
        and PurePosixPath(element.get("Target", ""))
        == PurePosixPath("word/document.xml")
    )
    if len(office_relationships) != 1:
        return False
    return (
        document.tag == f"{{{_WORDPROCESSINGML_NAMESPACE}}}document"
        and document.find(f"{{{_WORDPROCESSINGML_NAMESPACE}}}body") is not None
    )


def _rename_mismatch(expected_document_count: int) -> dict[str, object]:
    """构造不泄漏路径、文件名或摘要的重命名失败。

    输入参数：
        expected_document_count：canonical 合同固定的文档评价分母。
    输出返回值：
        零分、固定 reason 与固定分母，不含任何动态值。
    """

    return {
        "pass": False,
        "score": 0.0,
        "reason": "mismatch",
        "_evaluated_artifact_count": expected_document_count,
    }


def _find_exact_name(root: Path, name: str, *, recursive: bool) -> tuple[Path, ...]:
    """在预检根目录内按 basename 精确查找常规文件。

    输入参数：
        root/name：artifact 根目录与 canonical 目标文件名。
        recursive：是否搜索子目录。
    输出返回值：
        排序后的常规文件路径元组，不跟随符号链接。
    """

    if Path(name).name != name or name in {".", ".."}:
        return ()
    candidates = root.rglob(name) if recursive else (root / name,)
    return tuple(
        sorted(path for path in candidates if path.is_file() and not path.is_symlink())
    )


def _is_valid_named_file(path: Path) -> bool:
    """校验 PDF 或 OOXML 的只读基础容器身份。

    输入参数：
        path：已由 evaluator 预检的常规文件路径。
    输出返回值：
        文件非空且魔数/必要 OOXML member 与扩展名一致时为 ``True``。
    """

    try:
        if path.stat().st_size <= 0:
            return False
        extension = path.suffix.lower()
        if extension == ".pdf":
            with path.open("rb") as stream:
                return stream.read(5) == b"%PDF-"
        required = {
            ".docx": "word/document.xml",
            ".pptx": "ppt/presentation.xml",
            ".xlsx": "xl/workbook.xml",
        }.get(extension)
        if required is None:
            return True
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
        return "[Content_Types].xml" in names and required in names
    except (OSError, zipfile.BadZipFile):
        return False


def _config_error(field: str) -> dict[str, object]:
    """构造不会回显 canonical 参数值的配置错误结果。

    输入参数：
        field：无效字段的固定 schema 名称。
    输出返回值：
        带 ``evaluator_error`` 状态的旧协议兼容结果。
    """

    return {
        "pass": False,
        "score": -1.0,
        "status": "evaluator_error",
        "reason": f"invalid_{field}",
    }


FILE_CHECKS = {
    "check_named_files_exist": check_named_files_exist,
}
