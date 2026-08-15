"""WebMall Cart component receipt 的独立物理闭集门禁。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from paraguibench.integrations.osworld.image_manifest import (
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.integrations.webmall.cart_reference_validation import (
    WebMallCartReferenceReceipt,
    validate_webmall_cart_reference_receipt,
)
from paraguibench.integrations.webmall.environment_manifest import (
    load_webmall_environment_manifest_with_sha256,
)


WEBMALL_CART_COMPONENT_RECEIPT_ROOT = Path(
    "benchmark/provenance/webmall-cart-component-receipts"
)
WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH = Path(
    "benchmark/provenance/webmall-cart-component-receipt-allowlist-v1.json"
)
WEBMALL_CART_REFERENCE_COMPONENT_ID = "webmall-cart-reader-reference-v1"
WEBMALL_CART_COMPONENT_TASK_IDS = frozenset(
    {
        *(f"Operation-OnlineShopping-AddToCart-{index:03d}" for index in range(1, 8)),
        "Operation-OnlineShopping-CheapestProductSearch-007",
    }
)
_RELEASE_MANIFEST_PATH = Path("benchmark/manifests/release-v1.json")
_WEBMALL_MANIFEST_PATH = Path("environments/webmall/environment-manifest.json")
_OSWORLD_MANIFEST_PATH = Path("environments/osworld/image-manifest.json")
_WEBMALL_READER_PATH = Path("environments/webmall/wp-order-evidence.php")
_RUNTIME_SUPPORT_GUARD_PATH = Path("scripts/benchmark/runtime_support_manifest.py")
_PYPROJECT_PATH = Path("pyproject.toml")
_TASK_IDENTITY_DOMAIN = b"paraguibench-webmall-cart-task-identity-v1\0"
_ENVIRONMENT_IDENTITY_DOMAIN = b"paraguibench-webmall-cart-environment-v1\0"
_COMPONENT_IDENTITY_DOMAIN = b"paraguibench-webmall-cart-component-v1\0"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MAX_IDENTITY_FILE_BYTES = 16 * 1024 * 1024
_MAX_ALLOWLIST_BYTES = 64 * 1024
_MAX_RECEIPT_BYTES = 64 * 1024
_ALLOWLIST_FIELDS = frozenset({"schema_version", "receipts"})
_ALLOWLIST_ENTRY_FIELDS = frozenset(
    {
        "receipt_sha256",
        "task_identity_sha256",
        "environment_identity_sha256",
        "component_identity_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class WebMallCartComponentIdentity:
    """保存 Cart 八任务、环境与执行组件的三份独立摘要。"""

    task_identity_sha256: str
    environment_identity_sha256: str
    component_identity_sha256: str


class WebMallCartComponentReceiptError(RuntimeError):
    """表示 Cart component receipt 数据或物理闭集无效。"""

    code = "WEBMALL_CART_COMPONENT_RECEIPT_INVALID"

    def __init__(self) -> None:
        """构造不回显 receipt、路径或外部值的固定错误。

        输入参数：无。
        输出返回值：无；异常文本仅含稳定 code。
        """

        super().__init__(self.code)


def derive_webmall_cart_component_identity(
    repo_root: Path,
) -> WebMallCartComponentIdentity:
    """从当前仓库事实源派生 receipt-neutral 三层身份。

    输入参数：repo_root 为包含 canonical release、八个 Cart
        task、WebMall/OSWorld manifest、源码和 schema 的仓库根。
    输出返回值：任务、环境、组件三份 64 位小写 SHA-256。
        组件闭集包含正式 Python、schema、release、环境与
        promotion guard，故意排除派生 runtime-support、receipt、
        allowlist 和网站输出，避免晋升自引用。
    异常：WebMallCartComponentReceiptError：任一路径、闭集、
        JSON、摘要或传递环境身份无效。
    """

    if not isinstance(repo_root, Path):
        raise WebMallCartComponentReceiptError
    try:
        root = repo_root.resolve(strict=True)
        if not root.is_dir():
            raise OSError
        task_identity = _derive_cart_task_identity(root)
        environment_identity = _derive_cart_environment_identity(root)
        component_identity = _derive_cart_component_code_identity(root)
    except WebMallCartComponentReceiptError:
        raise
    except Exception:
        raise WebMallCartComponentReceiptError from None
    return WebMallCartComponentIdentity(
        task_identity_sha256=task_identity,
        environment_identity_sha256=environment_identity,
        component_identity_sha256=component_identity,
    )


def _derive_cart_task_identity(repo_root: Path) -> str:
    """验证 release 中精确八个 Cart task 并摘要其原始字节。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：domain-separated task identity SHA-256。
    异常：WebMallCartComponentReceiptError：release/task 身份、
        路径、字节摘要或八任务闭集漂移。
    """

    release_payload = _read_repository_file(
        repo_root,
        _RELEASE_MANIFEST_PATH,
        label="Cart canonical release",
    )
    release = _decode_json_object(release_payload)
    entries = release.get("tasks")
    if (
        release.get("release_id") != "release-v1"
        or not isinstance(entries, list)
        or not entries
    ):
        raise WebMallCartComponentReceiptError
    cart_records: list[tuple[str, Path, bytes]] = []
    seen_task_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise WebMallCartComponentReceiptError
        task_id = entry.get("task_id")
        relative_raw = entry.get("path")
        expected_sha256 = entry.get("sha256")
        if (
            not isinstance(task_id, str)
            or not task_id
            or task_id in seen_task_ids
            or not isinstance(relative_raw, str)
            or not isinstance(expected_sha256, str)
            or _SHA256_PATTERN.fullmatch(expected_sha256) is None
        ):
            raise WebMallCartComponentReceiptError
        seen_task_ids.add(task_id)
        relative = _safe_relative_path(relative_raw)
        task_payload = _read_repository_file(
            repo_root,
            relative,
            label="Cart canonical task",
            maximum_bytes=1024 * 1024,
        )
        if hashlib.sha256(task_payload).hexdigest() != expected_sha256:
            raise WebMallCartComponentReceiptError
        task = _decode_json_object(task_payload)
        if task.get("task_id") != task_id:
            raise WebMallCartComponentReceiptError
        if task.get("answer_type") == "cart":
            cart_records.append((task_id, relative, task_payload))
    if (
        len(cart_records) != len(WEBMALL_CART_COMPONENT_TASK_IDS)
        or {task_id for task_id, _path, _payload in cart_records}
        != WEBMALL_CART_COMPONENT_TASK_IDS
    ):
        raise WebMallCartComponentReceiptError
    digest = hashlib.sha256(_TASK_IDENTITY_DOMAIN)
    for task_id, relative, payload in sorted(cart_records):
        digest.update(task_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _derive_cart_environment_identity(repo_root: Path) -> str:
    """从同源原始字节派生 WebMall+OSWorld 传递环境摘要。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：路径与两份 manifest 原始 SHA 共同绑定的
        domain-separated SHA-256。
    异常：WebMallCartComponentReceiptError：manifest 解析、嵌套
        browser SHA 或同源字节漂移。
    """

    webmall_path = repo_root / _WEBMALL_MANIFEST_PATH
    osworld_path = repo_root / _OSWORLD_MANIFEST_PATH
    webmall_payload = _read_repository_file(
        repo_root,
        _WEBMALL_MANIFEST_PATH,
        label="Cart WebMall manifest",
    )
    osworld_payload = _read_repository_file(
        repo_root,
        _OSWORLD_MANIFEST_PATH,
        label="Cart browser manifest",
    )
    try:
        webmall, webmall_sha256 = load_webmall_environment_manifest_with_sha256(
            webmall_path
        )
        browser, browser_sha256 = load_osworld_image_manifest_with_sha256(osworld_path)
    except Exception:
        raise WebMallCartComponentReceiptError from None
    if (
        hashlib.sha256(webmall_payload).hexdigest() != webmall_sha256
        or hashlib.sha256(osworld_payload).hexdigest() != browser_sha256
        or webmall.browser_runtime.image_manifest_sha256 != browser_sha256
        or webmall.browser_runtime.required_protocol_id not in browser.protocol_ids
    ):
        raise WebMallCartComponentReceiptError
    return _compose_cart_environment_identity(
        webmall_manifest_sha256=webmall_sha256,
        browser_manifest_sha256=browser_sha256,
    )


def _compose_cart_environment_identity(
    *,
    webmall_manifest_sha256: str,
    browser_manifest_sha256: str,
) -> str:
    """从同一次解析实际使用的两份 manifest 摘要组合环境身份。

    输入参数：webmall_manifest_sha256/browser_manifest_sha256
        为分别与解析对象同源的 64 位小写 SHA-256。
    输出返回值：与路径及顺序绑定的 domain-separated
        环境身份 SHA-256。
    异常：WebMallCartComponentReceiptError：任一摘要格式无效。
    """

    if (
        not isinstance(webmall_manifest_sha256, str)
        or _SHA256_PATTERN.fullmatch(webmall_manifest_sha256) is None
        or not isinstance(browser_manifest_sha256, str)
        or _SHA256_PATTERN.fullmatch(browser_manifest_sha256) is None
    ):
        raise WebMallCartComponentReceiptError
    digest = hashlib.sha256(_ENVIRONMENT_IDENTITY_DOMAIN)
    for relative, sha256 in (
        (_WEBMALL_MANIFEST_PATH, webmall_manifest_sha256),
        (_OSWORLD_MANIFEST_PATH, browser_manifest_sha256),
    ):
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256))
    return digest.hexdigest()


def _derive_cart_component_code_identity(repo_root: Path) -> str:
    """摘要不含活性输出或证据文件的 Cart 执行闭集。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：路径和逐文件摘要共同形成的 SHA-256。
    异常：WebMallCartComponentReceiptError：文件树、固定文件
        或读取期闭集不稳定。
    """

    paths_before = _collect_component_paths(repo_root)
    digest = hashlib.sha256(_COMPONENT_IDENTITY_DOMAIN)
    for relative in paths_before:
        payload = _read_repository_file(
            repo_root,
            relative,
            label="Cart component file",
        )
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    if _collect_component_paths(repo_root) != paths_before:
        raise WebMallCartComponentReceiptError
    return digest.hexdigest()


def _collect_component_paths(repo_root: Path) -> tuple[Path, ...]:
    """枚举 receipt-neutral Cart component 文件闭集。

    输入参数：repo_root 为已解析仓库根。
    输出返回值：按 POSIX 相对路径排序的去重元组。
    异常：WebMallCartComponentReceiptError：树缺失、出现 symlink、
        特殊节点或固定文件缺失。
    """

    candidates = [
        _PYPROJECT_PATH,
        _RUNTIME_SUPPORT_GUARD_PATH,
        _RELEASE_MANIFEST_PATH,
        _WEBMALL_MANIFEST_PATH,
        _OSWORLD_MANIFEST_PATH,
        _WEBMALL_READER_PATH,
    ]
    candidates.extend(
        _collect_regular_tree_files(
            repo_root,
            Path("src/paraguibench"),
            suffix=".py",
        )
    )
    candidates.extend(
        _collect_regular_tree_files(
            repo_root,
            Path("benchmark/schemas"),
            suffix=".json",
        )
    )
    for relative in candidates:
        _read_repository_file(
            repo_root,
            relative,
            label="Cart component closure",
        )
    return tuple(sorted(set(candidates), key=lambda item: item.as_posix()))


def _collect_regular_tree_files(
    repo_root: Path,
    relative_root: Path,
    *,
    suffix: str,
) -> list[Path]:
    """不跟随 symlink 枚举树中特定后缀的普通文件。

    输入参数：repo_root/relative_root 确定树根；suffix 为目标后缀。
    输出返回值：稳定排序的仓库相对路径列表。
    异常：WebMallCartComponentReceiptError：树或任一节点无效。
    """

    tree_root = repo_root / relative_root
    try:
        root_status = tree_root.lstat()
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise OSError
        paths: list[Path] = []
        for current_raw, directory_names, file_names in os.walk(
            tree_root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_raw)
            for name in directory_names:
                status = (current / name).lstat()
                if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
                    raise OSError
            for name in file_names:
                candidate = current / name
                status = candidate.lstat()
                if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
                    raise OSError
                if candidate.suffix == suffix:
                    paths.append(candidate.relative_to(repo_root))
    except OSError:
        raise WebMallCartComponentReceiptError from None
    if not paths:
        raise WebMallCartComponentReceiptError
    return sorted(paths, key=lambda item: item.as_posix())


def _safe_relative_path(value: str) -> Path:
    """收紧 release 中的文件引用为规范仓库相对路径。

    输入参数：value 为 POSIX 路径字符串。
    输出返回值：不含空段、反斜杠、`.` 或 `..` 的 Path。
    异常：WebMallCartComponentReceiptError：路径非规范相对形式。
    """

    relative = Path(value)
    if (
        "\\" in value
        or relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value
    ):
        raise WebMallCartComponentReceiptError
    return relative


def _read_repository_file(
    repo_root: Path,
    relative_path: Path,
    *,
    label: str,
    maximum_bytes: int = _MAX_IDENTITY_FILE_BYTES,
) -> bytes:
    """通过 nofollow dirfd 链读取有界、前后稳定普通文件。

    输入参数：repo_root/relative_path 定位仓库内文件；label
        仅供函数级语义；maximum_bytes 为读取上限。
    输出返回值：同一文件 descriptor 的完整原始字节。
    异常：WebMallCartComponentReceiptError：路径链、文件类型、
        尺寸、短读或读取稳定性无效。
    """

    del label
    if (
        not isinstance(repo_root, Path)
        or not isinstance(relative_path, Path)
        or relative_path.is_absolute()
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or not isinstance(maximum_bytes, int)
        or isinstance(maximum_bytes, bool)
        or maximum_bytes <= 0
    ):
        raise WebMallCartComponentReceiptError
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0 or directory == 0:
        raise WebMallCartComponentReceiptError
    descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        descriptors.append(
            os.open(repo_root, os.O_RDONLY | directory | nofollow | cloexec)
        )
        for part in relative_path.parts[:-1]:
            descriptors.append(
                os.open(
                    part,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=descriptors[-1],
                )
            )
        file_descriptor = os.open(
            relative_path.name,
            os.O_RDONLY | nofollow | cloexec,
            dir_fd=descriptors[-1],
        )
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum_bytes
        ):
            raise OSError
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise OSError
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            raise OSError
        after = os.fstat(file_descriptor)
        fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            raise OSError
        return b"".join(chunks)
    except (OSError, ValueError):
        raise WebMallCartComponentReceiptError from None
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _decode_json_object(payload: bytes) -> dict[str, Any]:
    """解码 UTF-8 JSON object 并拒绝重复 key 与非有限常量。

    输入参数：payload 为稳定读取的原始 JSON 字节。
    输出返回值：顶层字段唯一的普通字典。
    异常：WebMallCartComponentReceiptError：编码、JSON、重复 key、
        NaN/Infinity 或顶层类型无效。
    """

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            parse_constant=lambda _value: (_raise_invalid_json()),
            object_pairs_hook=_unique_json_object,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise WebMallCartComponentReceiptError from None
    if not isinstance(value, dict):
        raise WebMallCartComponentReceiptError
    return value


def _raise_invalid_json() -> None:
    """为 JSON decoder 拒绝 NaN/Infinity 等非标准常量。

    输入参数：无；候选值故意不传入。
    输出返回值：不返回；始终抛出 ``ValueError``。
    """

    raise ValueError("invalid JSON constant")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """构造字段唯一的 JSON object。

    输入参数：pairs 为 decoder 提供的有序 key/value 序列。
    输出返回值：无重复 key 的新字典。
    异常：ValueError：发现重复 key；不回显 key 或值。
    """

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_trusted_webmall_cart_reference_receipt(
    repo_root: Path,
) -> WebMallCartReferenceReceipt | None:
    """加载并重新验证当前 Cart component receipt。

    输入参数：repo_root 为包含 allowlist、receipt、八任务、
        两份环境 manifest 与 component 源码闭集的仓库根。
    输出返回值：空 allowlist 返回 ``None``；否则返回同时
        匹配 receipt SHA 及 task/environment/component 三层当前身份的
        脱敏不可变 receipt。
    异常：WebMallCartComponentReceiptError：物理闭集、JSON、SHA
        或当前身份任一无效。manifest 中的 pending/live
        字段不作为证据；活性只由本 receipt 的当前验证结果派生。
    """

    if not isinstance(repo_root, Path):
        raise WebMallCartComponentReceiptError
    try:
        root_status = repo_root.lstat()
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise OSError
        root = repo_root.resolve(strict=True)
        allowlist_payload = _read_repository_file(
            root,
            WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH,
            label="Cart component receipt allowlist",
            maximum_bytes=_MAX_ALLOWLIST_BYTES,
        )
        allowlist = _decode_json_object(allowlist_payload)
        if (
            set(allowlist) != _ALLOWLIST_FIELDS
            or not isinstance(allowlist.get("schema_version"), int)
            or isinstance(allowlist.get("schema_version"), bool)
            or allowlist.get("schema_version") != 1
            or not isinstance(allowlist.get("receipts"), dict)
        ):
            raise WebMallCartComponentReceiptError
        entries = allowlist["receipts"]
        if not entries:
            empty_directory_identity = _validate_receipt_directory_closure(
                root,
                expected_names=frozenset(),
            )
            if (
                _read_repository_file(
                    root,
                    WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH,
                    label="Cart empty component allowlist post-check",
                    maximum_bytes=_MAX_ALLOWLIST_BYTES,
                )
                != allowlist_payload
                or _validate_receipt_directory_closure(
                    root,
                    expected_names=frozenset(),
                )
                != empty_directory_identity
            ):
                raise WebMallCartComponentReceiptError
            return None
        if set(entries) != {WEBMALL_CART_REFERENCE_COMPONENT_ID}:
            raise WebMallCartComponentReceiptError
        entry = entries[WEBMALL_CART_REFERENCE_COMPONENT_ID]
        if not isinstance(entry, dict) or set(entry) != _ALLOWLIST_ENTRY_FIELDS:
            raise WebMallCartComponentReceiptError
        if any(
            not isinstance(entry[field], str)
            or _SHA256_PATTERN.fullmatch(entry[field]) is None
            for field in _ALLOWLIST_ENTRY_FIELDS
        ):
            raise WebMallCartComponentReceiptError

        receipt_name = f"{WEBMALL_CART_REFERENCE_COMPONENT_ID}.json"
        receipt_directory_identity = _validate_receipt_directory_closure(
            root,
            expected_names=frozenset({receipt_name}),
        )
        receipt_relative = WEBMALL_CART_COMPONENT_RECEIPT_ROOT / receipt_name
        receipt_payload = _read_repository_file(
            root,
            receipt_relative,
            label="Cart component receipt",
            maximum_bytes=_MAX_RECEIPT_BYTES,
        )
        if hashlib.sha256(receipt_payload).hexdigest() != entry["receipt_sha256"]:
            raise WebMallCartComponentReceiptError

        identity_before = derive_webmall_cart_component_identity(root)
        if (
            identity_before.task_identity_sha256 != entry["task_identity_sha256"]
            or identity_before.environment_identity_sha256
            != entry["environment_identity_sha256"]
            or identity_before.component_identity_sha256
            != entry["component_identity_sha256"]
        ):
            raise WebMallCartComponentReceiptError
        manifest, webmall_sha256 = load_webmall_environment_manifest_with_sha256(
            root / _WEBMALL_MANIFEST_PATH
        )
        browser, browser_sha256 = load_osworld_image_manifest_with_sha256(
            root / _OSWORLD_MANIFEST_PATH
        )
        loaded_environment_identity = _compose_cart_environment_identity(
            webmall_manifest_sha256=webmall_sha256,
            browser_manifest_sha256=browser_sha256,
        )
        if (
            manifest.browser_runtime.image_manifest_sha256 != browser_sha256
            or loaded_environment_identity
            != identity_before.environment_identity_sha256
            or loaded_environment_identity != entry["environment_identity_sha256"]
        ):
            raise WebMallCartComponentReceiptError
        receipt = validate_webmall_cart_reference_receipt(
            _decode_json_object(receipt_payload),
            manifest=manifest,
            browser_image=browser,
            expected_webmall_manifest_sha256=webmall_sha256,
            expected_component_revision=identity_before.component_identity_sha256,
        )

        identity_after = derive_webmall_cart_component_identity(root)
        if identity_after != identity_before:
            raise WebMallCartComponentReceiptError
        if (
            _read_repository_file(
                root,
                WEBMALL_CART_COMPONENT_RECEIPT_ALLOWLIST_PATH,
                label="Cart component receipt allowlist post-check",
                maximum_bytes=_MAX_ALLOWLIST_BYTES,
            )
            != allowlist_payload
            or _read_repository_file(
                root,
                receipt_relative,
                label="Cart component receipt post-check",
                maximum_bytes=_MAX_RECEIPT_BYTES,
            )
            != receipt_payload
            or _validate_receipt_directory_closure(
                root,
                expected_names=frozenset({receipt_name}),
            )
            != receipt_directory_identity
        ):
            raise WebMallCartComponentReceiptError
        return receipt
    except WebMallCartComponentReceiptError:
        raise
    except Exception:
        raise WebMallCartComponentReceiptError from None


def _validate_receipt_directory_closure(
    repo_root: Path,
    *,
    expected_names: frozenset[str],
) -> tuple[int, int, int, int, int] | None:
    """通过 nofollow dirfd 校验 receipt 目录的物理字段闭集。

    输入参数：repo_root 为已解析仓库根；expected_names 为
        allowlist 机械派生的全部 receipt 文件名。
    输出返回值：目录缺失且预期为空时返回 ``None``；
        目录仅含预期单链接普通文件时返回稳定
        ``(device, inode, mode, mtime_ns, ctime_ns)`` 身份。
    异常：WebMallCartComponentReceiptError：路径链、节点类型、
        名称闭集或目录读取稳定性无效。
    """

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if nofollow == 0 or directory == 0:
        raise WebMallCartComponentReceiptError
    descriptors: list[int] = []
    try:
        descriptors.append(
            os.open(repo_root, os.O_RDONLY | directory | nofollow | cloexec)
        )
        parts = WEBMALL_CART_COMPONENT_RECEIPT_ROOT.parts
        for part in parts:
            try:
                descriptor = os.open(
                    part,
                    os.O_RDONLY | directory | nofollow | cloexec,
                    dir_fd=descriptors[-1],
                )
            except FileNotFoundError:
                if not expected_names and part == parts[-1]:
                    return None
                raise
            descriptors.append(descriptor)
        before = os.fstat(descriptors[-1])
        names = os.listdir(descriptors[-1])
        after = os.fstat(descriptors[-1])
        fields = ("st_dev", "st_ino", "st_mode", "st_mtime_ns", "st_ctime_ns")
        if (
            not stat.S_ISDIR(before.st_mode)
            or any(getattr(before, field) != getattr(after, field) for field in fields)
            or set(names) != expected_names
        ):
            raise OSError
        for name in names:
            metadata = os.stat(name, dir_fd=descriptors[-1], follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OSError
        return (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
    except (OSError, ValueError):
        raise WebMallCartComponentReceiptError from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def has_current_webmall_cart_component_receipt(repo_root: Path) -> bool:
    """判断当前仓库是否有可信 Cart component receipt。

    输入参数：repo_root 为包含独立 allowlist 的仓库根。
    输出返回值：空 allowlist 返回 ``False``；当前三层身份和
        receipt 全部有效时返回 ``True``。
    异常：WebMallCartComponentReceiptError：任一物理或语义门禁无效。
    """

    return load_trusted_webmall_cart_reference_receipt(repo_root) is not None
