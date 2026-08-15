"""从仓库事实源构造可独立审计的 Run 版本向量。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from paraguibench.integrations.osworld.image_manifest import (
    OSWorldImageManifestError,
    load_osworld_image_manifest_bytes_with_sha256,
    load_osworld_image_manifest_with_sha256,
)
from paraguibench.integrations.webmall.environment_manifest import (
    WebMallEnvironmentManifestError,
    load_webmall_environment_manifest_with_sha256,
)
from paraguibench.runstore import RunVersionVector

_RUNTIME_SUPPORT_RELATIVE = Path("benchmark/manifests/runtime-support-v1.json")
_RELEASE_MANIFEST_RELATIVE = Path("benchmark/manifests/release-v1.json")
_OSWORLD_IMAGE_MANIFEST_RELATIVE = Path("environments/osworld/image-manifest.json")
_SOURCE_TREE_DOMAIN = b"paraguibench-source-tree-v1\0"
_PACKAGE_TREE_DOMAIN = b"paraguibench-loaded-package-v1\0"
_WEBMALL_ENVIRONMENT_CLOSURE_DOMAIN = b"paraguibench-webmall-environment-closure-v1\0"


class RunVersioningError(ValueError):
    """表示仓库事实源无法形成完整、固定的 Run 版本向量。"""


def build_run_version_vector(
    *,
    repo_root: Path,
    task_id: str,
    environment_manifest_path: Path,
    environment_manifest_sha256: str | None = None,
    environment_protocol_ids: tuple[str, ...] | None = None,
    nested_environment_manifest_sha256: str | None = None,
    nested_environment_protocol_ids: tuple[str, ...] | None = None,
) -> RunVersionVector:
    """从当前工作树、runtime-support 与环境 manifest 构造版本向量。

    输入参数：
        repo_root：包含 ``src``、benchmark manifests 与 schemas 的仓库根。
        task_id：本次运行的 canonical task 稳定标识。
        environment_manifest_path：本次环境完整 manifest 的仓库内路径。
    输出返回值：
        六字段 ``RunVersionVector``。当前 v1 采用保守粒度：source、Agent
        与 evaluator revision 都绑定完整 Python/runtime-support 源码树，任何
        相关代码变化都会产生新 Run 身份。
    异常：
        RunVersioningError：路径不安全、清单摘要不一致、task 缺失/重复，
            或协议字段无法解释；错误不回显任务正文或文件内容。
    """

    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise RunVersioningError("版本向量仓库根无效")
    validate_loaded_package_matches_repository(root)
    (
        evaluation_protocol,
        environment_protocol,
        task_asset_manifest_path,
        task_reference_manifest_path,
    ) = _load_task_protocols(
        root,
        task_id,
    )
    source_digest = _hash_source_tree(
        root,
        task_asset_manifest_path=task_asset_manifest_path,
        task_reference_manifest_path=task_reference_manifest_path,
    )
    if (environment_manifest_sha256 is None) != (environment_protocol_ids is None):
        raise RunVersioningError("environment snapshot 字段不完整")
    if (nested_environment_manifest_sha256 is None) != (
        nested_environment_protocol_ids is None
    ):
        raise RunVersioningError("nested environment snapshot 字段不完整")
    if environment_manifest_sha256 is None:
        if nested_environment_manifest_sha256 is not None:
            raise RunVersioningError("nested environment snapshot 缺少顶层身份")
        if environment_protocol in {"osworld.desktop.v1", "osworld.chrome.v1"}:
            environment_revision = _build_osworld_environment_revision(
                root,
                environment_manifest_path=environment_manifest_path,
                expected_protocol=environment_protocol,
            )
        elif environment_protocol == "webmall.browser.v1":
            environment_revision = _build_webmall_environment_revision(
                root,
                environment_manifest_path=environment_manifest_path,
            )
        else:
            environment_bytes = _read_repository_file(
                root,
                environment_manifest_path,
                label="environment manifest",
            )
            environment_revision = _build_environment_revision(
                root,
                environment_manifest_path=environment_manifest_path,
                environment_bytes=environment_bytes,
                expected_protocol=environment_protocol,
            )
    else:
        if (
            not _valid_environment_snapshot(
                environment_manifest_sha256,
                environment_protocol_ids,
            )
            or environment_protocol not in environment_protocol_ids
        ):
            raise RunVersioningError("environment snapshot 身份无效")
        if environment_protocol == "webmall.browser.v1":
            if (
                not _valid_environment_snapshot(
                    nested_environment_manifest_sha256,
                    nested_environment_protocol_ids,
                )
                or "osworld.chrome.v1" not in nested_environment_protocol_ids
            ):
                raise RunVersioningError("nested environment snapshot 身份无效")
            environment_revision = _build_webmall_snapshot_revision(
                webmall_manifest_sha256=environment_manifest_sha256,
                browser_manifest_sha256=nested_environment_manifest_sha256,
            )
        else:
            if nested_environment_manifest_sha256 is not None:
                raise RunVersioningError("nested environment snapshot 角色无效")
            environment_revision = "manifest-sha256:" + environment_manifest_sha256
    source_revision = "tree-sha256:" + source_digest
    return RunVersionVector(
        source_revision=source_revision,
        agent_code_revision=source_revision,
        evaluator_revision=source_revision,
        evaluation_protocol=evaluation_protocol,
        environment_protocol=environment_protocol,
        environment_revision=environment_revision,
    )


def _valid_environment_snapshot(
    manifest_sha256: object,
    protocol_ids: object,
) -> bool:
    """校验单个严格 loader 快照的摘要与协议闭集形状。

    输入参数：manifest_sha256 为同源原始字节摘要；protocol_ids
        为同一 loader DTO 的不可变协议元组。
    输出返回值：SHA 为 64 位小写十六进制且协议非空、
        唯一时返回 ``True``；其余返回 ``False``。
    """

    return (
        isinstance(manifest_sha256, str)
        and len(manifest_sha256) == 64
        and all(character in "0123456789abcdef" for character in manifest_sha256)
        and type(protocol_ids) is tuple
        and bool(protocol_ids)
        and all(
            isinstance(protocol_id, str) and bool(protocol_id)
            for protocol_id in protocol_ids
        )
        and len(protocol_ids) == len(set(protocol_ids))
    )


def _build_webmall_snapshot_revision(
    *,
    webmall_manifest_sha256: str,
    browser_manifest_sha256: str,
) -> str:
    """由首次两份 same-FD 快照构造 WebMall 传递 revision。

    输入参数：webmall_manifest_sha256/browser_manifest_sha256 分别是
        WebMall 与嵌套 OSWorld image 原始字节摘要。
    输出返回值：路径域分离且绑定两个摘要的
        ``manifest-sha256:`` revision。
    """

    digest = hashlib.sha256(_WEBMALL_ENVIRONMENT_CLOSURE_DOMAIN)
    for relative_path, manifest_sha256 in (
        (
            Path("environments/webmall/environment-manifest.json"),
            webmall_manifest_sha256,
        ),
        (_OSWORLD_IMAGE_MANIFEST_RELATIVE, browser_manifest_sha256),
    ):
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(manifest_sha256))
    return "manifest-sha256:" + digest.hexdigest()


def _require_formal_environment_path(
    repo_root: Path,
    candidate_path: Path,
    *,
    expected_relative_path: Path,
    label: str,
) -> Path:
    """将环境协议绑定到仓库内唯一正式 manifest 路径。

    输入参数：repo_root 为已解析仓库根；candidate_path 为公开
        调用方选择的路径；expected_relative_path 为协议固定相对路径；
        label 是不含路径值的错误区域。
    输出返回值：精确位于仓库内的正式绝对路径；任何
        别名、``..``、仓库外或其他同内容路径都失败关闭。
    """

    if not isinstance(candidate_path, Path):
        raise RunVersioningError(f"{label} 路径无效")
    candidate = (
        candidate_path if candidate_path.is_absolute() else repo_root / candidate_path
    )
    expected = repo_root / expected_relative_path
    if candidate != expected or ".." in candidate.parts:
        raise RunVersioningError(f"{label} 路径无效")
    return expected


def _build_osworld_environment_revision(
    repo_root: Path,
    *,
    environment_manifest_path: Path,
    expected_protocol: str,
) -> str:
    """通过 OSWorld 专用 same-FD loader 构造环境 revision。

    输入参数：repo_root 为仓库根；environment_manifest_path 必须指向
        固定正式文件；expected_protocol 为 task 已冻结 desktop/chrome ID。
    输出返回值：由严格 loader 同源原始字节 SHA-256 形成的
        ``manifest-sha256:`` revision。
    """

    formal_path = _require_formal_environment_path(
        repo_root,
        environment_manifest_path,
        expected_relative_path=_OSWORLD_IMAGE_MANIFEST_RELATIVE,
        label="OSWorld environment manifest",
    )
    try:
        manifest, manifest_sha256 = load_osworld_image_manifest_with_sha256(formal_path)
    except OSWorldImageManifestError:
        raise RunVersioningError("environment protocol manifest 身份不一致") from None
    if (
        expected_protocol not in {"osworld.desktop.v1", "osworld.chrome.v1"}
        or expected_protocol not in manifest.protocol_ids
        or manifest.manifest_sha256 != manifest_sha256
    ):
        raise RunVersioningError("environment protocol manifest 身份不一致")
    return "manifest-sha256:" + manifest_sha256


def _build_webmall_environment_revision(
    repo_root: Path,
    *,
    environment_manifest_path: Path,
) -> str:
    """通过两个专用稳定 loader 构造 WebMall 传递环境 revision。

    输入参数：repo_root 为仓库根；environment_manifest_path 必须是
        WebMall 正式清单。
    输出返回值：domain-separated 绑定 WebMall 与其引用的
        OSWorld Chrome 清单两个同源原始 SHA-256 的 revision。
    """

    formal_webmall_path = _require_formal_environment_path(
        repo_root,
        environment_manifest_path,
        expected_relative_path=Path("environments/webmall/environment-manifest.json"),
        label="WebMall environment manifest",
    )
    formal_osworld_path = repo_root / _OSWORLD_IMAGE_MANIFEST_RELATIVE
    try:
        webmall, webmall_sha256 = load_webmall_environment_manifest_with_sha256(
            formal_webmall_path
        )
        image, image_sha256 = load_osworld_image_manifest_with_sha256(
            formal_osworld_path
        )
    except (WebMallEnvironmentManifestError, OSWorldImageManifestError):
        raise RunVersioningError(
            "WebMall nested environment manifest 身份无效"
        ) from None
    browser = webmall.browser_runtime
    if (
        "webmall.browser.v1" not in webmall.protocol_ids
        or browser.kind != "osworld_chrome"
        or browser.image_manifest_ref != "../osworld/image-manifest.json"
        or browser.required_protocol_id != "osworld.chrome.v1"
        or browser.image_manifest_sha256 != image_sha256
        or image.manifest_sha256 != image_sha256
        or browser.required_protocol_id not in image.protocol_ids
    ):
        raise RunVersioningError("WebMall nested browser image 身份无效")
    return _build_webmall_snapshot_revision(
        webmall_manifest_sha256=webmall_sha256,
        browser_manifest_sha256=image_sha256,
    )


def validate_loaded_package_matches_repository(repo_root: Path) -> None:
    """确认当前进程实际导入的 package 与 repo-root 源码逐字节一致。

    输入参数：
        repo_root：用户选择、包含 benchmark 数据与 checkout 源码的仓库根。
    输出返回值：
        无；两侧 Python 文件闭集及内容摘要完全一致时正常返回。editable
        install 可指向同一路径，wheel install 则与 checkout 做内容比较。
    异常：
        RunVersioningError：任一 package 树无效或摘要不同；错误不回显绝对
            安装路径，避免把开发者目录写入运行诊断。
    """

    repository_package = repo_root / "src" / "paraguibench"
    loaded_package = Path(__file__).resolve().parents[1]
    repository_digest = _hash_python_package_tree(
        repository_package,
        label="repository package",
    )
    loaded_digest = _hash_python_package_tree(
        loaded_package,
        label="loaded package",
    )
    if repository_digest != loaded_digest:
        raise RunVersioningError("loaded package 与 repository package 源码不一致")


def _hash_python_package_tree(package_root: Path, *, label: str) -> str:
    """计算一个不跟随符号链接的 Python package 文件闭集摘要。

    输入参数：
        package_root：待核对的 ``paraguibench`` package 根目录。
        label：不含路径值的错误区域名称。
    输出返回值：
        domain-separated、包含相对路径与逐文件内容的 SHA-256。
    异常：
        RunVersioningError：根目录/文件闭集无效，或路径含符号链接。
    """

    if package_root.is_symlink() or not package_root.is_dir():
        raise RunVersioningError(f"{label} 目录无效")
    files = _collect_python_tree_files(package_root, label=label)
    if not files:
        raise RunVersioningError(f"{label} Python 闭集为空")
    digest = hashlib.sha256(_PACKAGE_TREE_DOMAIN)
    resolved_root = package_root.resolve(strict=True)
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise RunVersioningError(f"{label} 文件无效")
        resolved = path.resolve(strict=True)
        try:
            relative = resolved.relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise RunVersioningError(f"{label} 路径越界") from error
        try:
            content = resolved.read_bytes()
        except OSError as error:
            raise RunVersioningError(f"{label} 无法读取") from error
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _collect_python_tree_files(
    package_root: Path,
    *,
    label: str,
) -> list[Path]:
    """枚举 Python 闭集并拒绝任何被 walker 忽略的 symlink。

    输入参数：
        package_root：已验证存在的 package 根目录。
        label：不包含路径值的安全错误区域名。
    输出返回值：
        按 POSIX 路径稳定排序的普通 ``.py`` 文件列表。
    异常：
        RunVersioningError：目录树中出现 symlink、非普通文件或
            枚举失败。
    """

    files: list[Path] = []
    try:
        walker = os.walk(package_root, topdown=True, followlinks=False)
        for current_raw, directory_names, file_names in walker:
            current = Path(current_raw)
            for name in directory_names:
                candidate = current / name
                status = candidate.lstat()
                if stat.S_ISLNK(status.st_mode):
                    raise RunVersioningError(f"{label} 目录含符号链接")
                if not stat.S_ISDIR(status.st_mode):
                    raise RunVersioningError(f"{label} 目录无效")
            for name in file_names:
                candidate = current / name
                status = candidate.lstat()
                if stat.S_ISLNK(status.st_mode):
                    raise RunVersioningError(f"{label} 文件含符号链接")
                if not stat.S_ISREG(status.st_mode):
                    raise RunVersioningError(f"{label} 文件无效")
                if candidate.suffix == ".py":
                    files.append(candidate)
    except OSError as error:
        raise RunVersioningError(f"{label} 无法枚举") from error
    return sorted(files, key=lambda item: item.as_posix())


def _validate_environment_manifest_protocol(
    manifest_bytes: bytes,
    *,
    expected_protocol: str,
) -> None:
    """验证环境 manifest 自报协议与 task runtime binding 完全一致。

    输入参数：
        manifest_bytes：将进入 environment revision 摘要的完整原始字节。
        expected_protocol：runtime-support 为当前 task 固定的环境协议 ID。
    输出返回值：
        无；schema 1 manifest 的 ``protocol_id`` 完全一致时正常返回。
    异常：
        RunVersioningError：JSON、schema 或协议身份缺失/不一致；错误不回显
            manifest 内容或部署路径。
    """

    if expected_protocol in {"osworld.desktop.v1", "osworld.chrome.v1"}:
        try:
            image_manifest, _manifest_sha256 = (
                load_osworld_image_manifest_bytes_with_sha256(manifest_bytes)
            )
        except OSWorldImageManifestError:
            raise RunVersioningError(
                "environment protocol manifest 身份不一致"
            ) from None
        if expected_protocol not in image_manifest.protocol_ids:
            raise RunVersioningError("environment protocol manifest 身份不一致")
        return
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunVersioningError("environment protocol manifest JSON 无效") from error
    protocol_ids = manifest.get("protocol_ids") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or not isinstance(protocol_ids, list)
        or not protocol_ids
        or any(not isinstance(item, str) for item in protocol_ids)
        or len(protocol_ids) != len(set(protocol_ids))
        or expected_protocol not in protocol_ids
    ):
        raise RunVersioningError("environment protocol manifest 身份不一致")


def _build_environment_revision(
    repo_root: Path,
    *,
    environment_manifest_path: Path,
    environment_bytes: bytes,
    expected_protocol: str,
) -> str:
    """从当前环境的传递 manifest 闭包构造稳定 revision。

    输入参数：
        repo_root：已解析的仓库根。
        environment_manifest_path：本次 Run 选择的顶层环境 manifest。
        environment_bytes：通过仓库安全读取器获得的顶层原始字节。
        expected_protocol：canonical runtime-support 固定的环境协议。
    输出返回值：
        ``manifest-sha256:<digest>``；OSWorld 绑定单一完整 manifest，
        WebMall 则 domain-separated 绑定其自身与嵌套 OSWorld Chrome
        image manifest 的路径及原始字节摘要。
    异常：
        RunVersioningError：顶层协议无效，或 WebMall 内嵌路径、
            协议、SHA-256 与当前 OSWorld manifest 不一致。
    """

    _validate_environment_manifest_protocol(
        environment_bytes,
        expected_protocol=expected_protocol,
    )
    if expected_protocol != "webmall.browser.v1":
        return "manifest-sha256:" + hashlib.sha256(environment_bytes).hexdigest()

    candidate = (
        environment_manifest_path
        if environment_manifest_path.is_absolute()
        else repo_root / environment_manifest_path
    )
    canonical_webmall_path = (
        repo_root / "environments" / "webmall" / "environment-manifest.json"
    )
    if candidate.resolve() != canonical_webmall_path.resolve():
        raise RunVersioningError("WebMall nested environment manifest 路径无效")
    try:
        webmall = json.loads(environment_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunVersioningError(
            "WebMall nested environment manifest JSON 无效"
        ) from error
    browser_runtime = (
        webmall.get("browser_runtime") if isinstance(webmall, dict) else None
    )
    if (
        not isinstance(browser_runtime, dict)
        or browser_runtime.get("kind") != "osworld_chrome"
        or browser_runtime.get("image_manifest_ref") != "../osworld/image-manifest.json"
        or browser_runtime.get("required_protocol_id") != "osworld.chrome.v1"
    ):
        raise RunVersioningError("WebMall nested browser image 身份无效")
    expected_digest = browser_runtime.get("image_manifest_sha256")
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or any(character not in "0123456789abcdef" for character in expected_digest)
    ):
        raise RunVersioningError("WebMall nested browser image SHA-256 无效")

    osworld_bytes = _read_repository_file(
        repo_root,
        repo_root / _OSWORLD_IMAGE_MANIFEST_RELATIVE,
        label="WebMall nested browser image manifest",
    )
    _validate_environment_manifest_protocol(
        osworld_bytes,
        expected_protocol="osworld.chrome.v1",
    )
    if hashlib.sha256(osworld_bytes).hexdigest() != expected_digest:
        raise RunVersioningError(
            "WebMall nested browser image SHA-256 与当前环境不一致"
        )

    digest = hashlib.sha256(_WEBMALL_ENVIRONMENT_CLOSURE_DOMAIN)
    for relative_path, payload in (
        (Path("environments/webmall/environment-manifest.json"), environment_bytes),
        (_OSWORLD_IMAGE_MANIFEST_RELATIVE, osworld_bytes),
    ):
        digest.update(relative_path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
    return "manifest-sha256:" + digest.hexdigest()


def _load_task_protocols(
    repo_root: Path,
    task_id: str,
) -> tuple[str, str, Path | None, Path | None]:
    """验证 runtime-support 与 release 的绑定并读取单任务协议。

    输入参数：
        repo_root：已解析仓库根。
        task_id：待查找的 canonical task 标识。
    输出返回值：
        ``(evaluation_protocol, environment_protocol, asset_manifest_path,
        reference_manifest_path)``；没有对应输入资产、evaluator-only gold
        或 audit-only reference 的项为 ``None``。
    异常：
        RunVersioningError：JSON/schema、release 摘要、task 唯一性或协议字段
            无效。
    """

    support_bytes = _read_repository_file(
        repo_root,
        repo_root / _RUNTIME_SUPPORT_RELATIVE,
        label="runtime-support manifest",
    )
    release_bytes = _read_repository_file(
        repo_root,
        repo_root / _RELEASE_MANIFEST_RELATIVE,
        label="release manifest",
    )
    try:
        support = json.loads(support_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunVersioningError("runtime-support manifest JSON 无效") from error
    if (
        not isinstance(support, dict)
        or support.get("schema_version") != 1
        or support.get("manifest_id") != "runtime-support-v1"
        or support.get("release_id") != "release-v1"
    ):
        raise RunVersioningError("runtime-support manifest 身份无效")
    expected_release_digest = support.get("release_manifest_sha256")
    observed_release_digest = hashlib.sha256(release_bytes).hexdigest()
    if expected_release_digest != observed_release_digest:
        raise RunVersioningError("runtime-support 与 release 摘要不一致")
    (
        task_asset_manifest_path,
        task_reference_manifest_path,
    ) = _load_task_dependency_manifest_paths(
        repo_root,
        release_bytes=release_bytes,
        task_id=task_id,
    )
    entries = support.get("tasks")
    if not isinstance(entries, list):
        raise RunVersioningError("runtime-support tasks 无效")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("task_id") == task_id
    ]
    if len(matches) != 1:
        raise RunVersioningError("runtime-support task 身份不唯一")
    evaluation_protocol = matches[0].get("evaluation_protocol")
    environment_protocol = matches[0].get("environment_protocol")
    if (
        not isinstance(evaluation_protocol, str)
        or not evaluation_protocol
        or not isinstance(environment_protocol, str)
        or not environment_protocol
    ):
        raise RunVersioningError("runtime-support task protocol 无效")
    return (
        evaluation_protocol,
        environment_protocol,
        task_asset_manifest_path,
        task_reference_manifest_path,
    )


def _load_task_dependency_manifest_paths(
    repo_root: Path,
    *,
    release_bytes: bytes,
    task_id: str,
) -> tuple[Path | None, Path | None]:
    """从 release 固定的 canonical task 解析输入与 reference manifest。

    输入参数：
        repo_root：已解析仓库根。
        release_bytes：与 runtime-support 摘要绑定的 release manifest 字节。
        task_id：当前运行任务稳定标识。
    输出返回值：
        ``(input asset manifest, task-specific reference manifest)`` 的仓库内
        路径；reference 可为 evaluator-only gold 或 audit-only
        known-negative metadata，缺少对应声明时为 ``None``。
    异常：
        RunVersioningError：release/task JSON、路径、摘要、身份或资产声明组合
            无效；不会把外部字段值写入错误消息。
    """

    try:
        release = json.loads(release_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunVersioningError("release manifest JSON 无效") from error
    if (
        not isinstance(release, dict)
        or release.get("schema_version") != 1
        or release.get("release_id") != "release-v1"
        or not isinstance(release.get("tasks"), list)
    ):
        raise RunVersioningError("release manifest 身份无效")
    matches = [
        item
        for item in release["tasks"]
        if isinstance(item, dict) and item.get("task_id") == task_id
    ]
    if len(matches) != 1:
        raise RunVersioningError("release task 身份不唯一")
    task_path_value = matches[0].get("path")
    expected_digest = matches[0].get("sha256")
    if (
        not isinstance(task_path_value, str)
        or not task_path_value
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
    ):
        raise RunVersioningError("release task identity fields 无效")
    task_path = repo_root / task_path_value
    task_bytes = _read_repository_file(
        repo_root,
        task_path,
        label="canonical task",
    )
    if hashlib.sha256(task_bytes).hexdigest() != expected_digest:
        raise RunVersioningError("canonical task 摘要不一致")
    try:
        task = json.loads(task_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunVersioningError("canonical task JSON 无效") from error
    if not isinstance(task, dict) or task.get("task_id") != task_id:
        raise RunVersioningError("canonical task 身份无效")

    has_manifest_field = "asset_manifest" in task
    manifest_reference = task.get("asset_manifest")
    legacy_reference = task.get("prepare_script_path")
    task_asset_manifest_path: Path | None
    if has_manifest_field:
        if not isinstance(manifest_reference, str) or not manifest_reference:
            raise RunVersioningError("canonical task asset manifest 声明无效")
        if legacy_reference not in (None, ""):
            raise RunVersioningError("canonical task 资产声明组合无效")
        manifest_path = repo_root / manifest_reference
        _read_repository_file(
            repo_root,
            manifest_path,
            label="task asset manifest",
        )
        task_asset_manifest_path = manifest_path
    elif legacy_reference in (None, ""):
        task_asset_manifest_path = None
    elif isinstance(legacy_reference, str):
        raise RunVersioningError("canonical task legacy 资产尚未迁移")
    else:
        raise RunVersioningError("canonical task legacy 资产声明无效")

    has_gold_manifest = "gold_manifest" in task
    has_known_negative_manifest = "known_negative_manifest" in task
    if has_gold_manifest and has_known_negative_manifest:
        raise RunVersioningError("canonical task reference manifest 角色冲突")
    if has_gold_manifest:
        reference_value = task.get("gold_manifest")
        reference_label = "task gold manifest"
    elif has_known_negative_manifest:
        reference_value = task.get("known_negative_manifest")
        reference_label = "task audit reference manifest"
    else:
        reference_value = None
        reference_label = "task reference manifest"
    if reference_value is None and not (
        has_gold_manifest or has_known_negative_manifest
    ):
        task_reference_manifest_path = None
    elif not isinstance(reference_value, str) or not reference_value:
        raise RunVersioningError("canonical task reference manifest 声明无效")
    else:
        task_reference_manifest_path = repo_root / reference_value
        _read_repository_file(
            repo_root,
            task_reference_manifest_path,
            label=reference_label,
        )
    return task_asset_manifest_path, task_reference_manifest_path


def _hash_source_tree(
    repo_root: Path,
    *,
    task_asset_manifest_path: Path | None,
    task_reference_manifest_path: Path | None,
) -> str:
    """计算公开 Python、benchmark identity 与 schema 的规范化树摘要。

    输入参数：
        repo_root：已解析仓库根。
        task_asset_manifest_path：当前 task 的 pinned 资产 manifest；零资产
            任务为 ``None``。
        task_reference_manifest_path：当前 task 的 evaluator-only gold
            或 audit-only known-negative manifest；没有外部 reference 时为
            ``None``。
    输出返回值：
        64 位小写 SHA-256；相对路径和逐文件内容摘要都进入 domain-separated
        哈希，因此新增、删除、重命名或修改文件都会改变结果。
    异常：
        RunVersioningError：必需目录/文件缺失、发现符号链接或读取失败。
    """

    source_root = repo_root / "src" / "paraguibench"
    schema_root = repo_root / "benchmark" / "schemas"
    if not source_root.is_dir() or not schema_root.is_dir():
        raise RunVersioningError("版本向量源码或 schema 目录缺失")
    candidates = _collect_python_tree_files(
        source_root,
        label="source package",
    )
    candidates.extend(schema_root.glob("*.json"))
    candidates.extend(
        [
            repo_root / "pyproject.toml",
            repo_root / _RELEASE_MANIFEST_RELATIVE,
            repo_root / _RUNTIME_SUPPORT_RELATIVE,
        ]
    )
    if task_asset_manifest_path is not None:
        candidates.append(task_asset_manifest_path)
    if task_reference_manifest_path is not None:
        candidates.append(task_reference_manifest_path)
    files = sorted(set(candidates), key=lambda item: item.as_posix())
    if not files:
        raise RunVersioningError("版本向量源码闭集为空")
    digest = hashlib.sha256(_SOURCE_TREE_DOMAIN)
    for path in files:
        content = _read_repository_file(
            repo_root,
            path,
            label="source tree file",
        )
        relative = path.resolve().relative_to(repo_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _read_repository_file(
    repo_root: Path,
    path: Path,
    *,
    label: str,
) -> bytes:
    """读取仓库内不经过符号链接的普通文件。

    输入参数：
        repo_root：已解析仓库根。
        path：绝对或相对目标路径。
        label：仅用于不含具体路径的错误区域名称。
    输出返回值：
        目标文件原始字节。
    异常：
        RunVersioningError：路径越界、路径链含符号链接、目标非普通文件或
            读取失败。
    """

    candidate = path if path.is_absolute() else repo_root / path
    try:
        relative = candidate.absolute().relative_to(repo_root)
    except ValueError as error:
        raise RunVersioningError(f"{label} 路径越界") from error
    current = repo_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RunVersioningError(f"{label} 路径含符号链接")
    resolved = current.resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError as error:
        raise RunVersioningError(f"{label} 路径越界") from error
    if not resolved.is_file():
        raise RunVersioningError(f"{label} 不是普通文件")
    try:
        return resolved.read_bytes()
    except OSError as error:
        raise RunVersioningError(f"{label} 无法读取") from error
