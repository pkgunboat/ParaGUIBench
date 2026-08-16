"""13 个 legacy OSWorld artifact-family 任务的版本化准备协议。

本模块只从不可变 catalog 选择动作，不消费 Agent 文本、任务内命令或路径。
输入资产必须先由上层严格 manifest 完成 host/guest 完整性验证；否则 source
在首次 controller I/O 前失败关闭。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any

from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)


ARTIFACT_FAMILY_TASK_PREPARE_SCHEMA_ID = (
    "paraguibench.osworld.artifact-family-task-prepare.v1"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ACTIONABLE = "actionable_when_assets_verified"
_BLOCKED = "blocked"
_LEGACY_ASSET_MODE = "legacy_prepare_reference"
_STRICT_ASSET_MODE = "strict_asset_manifest"


class ArtifactFamilyTaskPrepareError(RuntimeError):
    """表示 task prepare 身份、资产、路径或固定动作未通过门禁。"""


def _validate_relative_path(value: str, *, allow_home_root: bool = False) -> None:
    """验证 catalog 路径为不可逃逸的规范 POSIX 相对路径。

    输入参数：
        value：待验证的相对路径。
        allow_home_root：是否允许 ``.`` 表示 guest home 本身。
    输出返回值：
        无；路径安全时返回。
    异常：
        ValueError：路径为空、绝对、含控制符、反斜杠或点段。
    """

    if allow_home_root and value == ".":
        return
    if not isinstance(value, str):
        raise ValueError("artifact-family relative path 无效")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or str(path) != value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("artifact-family relative path 无效")


@dataclass(frozen=True, slots=True)
class ArtifactFamilyAssetBinding:
    """绑定 verified shared 资产名与旧源固定 guest 相对路径。"""

    asset_relative_path: str
    guest_relative_path: str
    purpose: str

    def __post_init__(self) -> None:
        """验证两个路径均为规范 POSIX 相对路径。

        输入参数：
            无；读取冻结实例的三个字段。
        输出返回值：
            无；字段安全时完成构造。
        异常：
            ValueError：路径或用途不符合闭集。
        """

        _validate_relative_path(self.asset_relative_path)
        _validate_relative_path(self.guest_relative_path)
        if self.purpose not in {
            "context_input",
            "editable_target",
            "reference_input",
            "task_input_bundle",
        }:
            raise ValueError("artifact-family asset purpose 无效")


@dataclass(frozen=True, slots=True)
class ArtifactFamilyPrepareAction:
    """描述一项不含绝对 guest 路径的固定准备动作。"""

    action_id: str
    operation: str
    argv_prefix: tuple[str, ...] = ()
    home_relative_path: str | None = None
    destination_relative_path: str | None = None
    literal_value: str | None = None

    def __post_init__(self) -> None:
        """验证动作字段不会携带 shell 或绝对路径。

        输入参数：
            无；读取冻结动作字段。
        输出返回值：
            无；动作形状安全时完成构造。
        异常：
            ValueError：动作身份、操作、argv 或路径非法。
        """

        if not self.action_id or self.operation not in {
            "activate_window",
            "create_directories",
            "launch_literal",
            "materialize_assets",
            "safe_extract_zip",
            "launch_with_home_path",
            "open_home_path",
            "sleep_seconds",
            "wait_chrome_cdp",
        }:
            raise ValueError("artifact-family prepare action 无效")
        if any(
            not isinstance(item, str)
            or not item
            or "\x00" in item
            or item in {"sh", "bash", "zsh", "-c"}
            for item in self.argv_prefix
        ):
            raise ValueError("artifact-family prepare argv 无效")
        for value in (
            self.home_relative_path,
            self.destination_relative_path,
        ):
            if value is not None:
                _validate_relative_path(value, allow_home_root=True)
        if self.literal_value is not None and (
            not self.literal_value
            or len(self.literal_value) > 2048
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.literal_value
            )
        ):
            raise ValueError("artifact-family prepare literal 无效")


@dataclass(frozen=True, slots=True)
class ArtifactFamilyTaskPrepareSpec:
    """保存一个 artifact-family 任务的不可变准备合同。"""

    schema_id: str
    spec_id: str
    task_id: str
    task_uid: str
    task_source: str
    task_type: str
    task_tag: str
    evaluator_path: str
    canonical_asset_mode: str
    canonical_prepare_reference_sha256: str | None
    canonical_asset_manifest_relative_path: str | None
    canonical_asset_manifest_sha256: str | None
    input_draft_relative_path: str
    input_draft_sha256: str
    source_task_id: str
    source_evaluator_id: str
    source_contract_sha256: str
    evidence_spec_sha256: str
    source_snapshot_id: str
    required_context_ids: tuple[str, ...]
    prepare_status: str
    blocked_reason_id: str | None
    asset_bindings: tuple[ArtifactFamilyAssetBinding, ...]
    directory_relative_paths: tuple[str, ...]
    actions: tuple[ArtifactFamilyPrepareAction, ...]
    finalize_action_id: str
    finalize_options_json: str

    def __post_init__(self) -> None:
        """验证任务规格的身份、状态、路径和动作闭集。

        输入参数：
            无；读取冻结规格的全部字段。
        输出返回值：
            无；规格自洽时完成构造。
        异常：
            ValueError：摘要、状态、路径、上下文或动作闭集非法。
        """

        if (
            self.schema_id != ARTIFACT_FAMILY_TASK_PREPARE_SCHEMA_ID
            or not self.spec_id
            or not self.task_id
            or not self.task_uid
            or not self.required_context_ids
            or len(self.required_context_ids) != len(set(self.required_context_ids))
            or not self.asset_bindings
            or not self.actions
        ):
            raise ValueError("artifact-family prepare spec identity 无效")
        for digest in (
            self.input_draft_sha256,
            self.source_contract_sha256,
            self.evidence_spec_sha256,
        ):
            if _SHA256_PATTERN.fullmatch(digest) is None:
                raise ValueError("artifact-family prepare spec 摘要无效")
        if self.canonical_asset_mode == _LEGACY_ASSET_MODE:
            if (
                not isinstance(self.canonical_prepare_reference_sha256, str)
                or _SHA256_PATTERN.fullmatch(self.canonical_prepare_reference_sha256)
                is None
                or self.canonical_asset_manifest_relative_path is not None
                or self.canonical_asset_manifest_sha256 is not None
            ):
                raise ValueError("artifact-family canonical asset mode 无效")
        elif self.canonical_asset_mode == _STRICT_ASSET_MODE:
            if (
                self.canonical_prepare_reference_sha256 is not None
                or not isinstance(
                    self.canonical_asset_manifest_relative_path,
                    str,
                )
                or not isinstance(self.canonical_asset_manifest_sha256, str)
                or _SHA256_PATTERN.fullmatch(self.canonical_asset_manifest_sha256)
                is None
            ):
                raise ValueError("artifact-family canonical asset mode 无效")
            _validate_relative_path(self.canonical_asset_manifest_relative_path)
        else:
            raise ValueError("artifact-family canonical asset mode 无效")
        if self.prepare_status == _ACTIONABLE:
            if self.blocked_reason_id is not None:
                raise ValueError("actionable prepare spec 不得携带 blocker")
        elif self.prepare_status == _BLOCKED:
            if self.blocked_reason_id != ("blocked.source_start_context_ambiguous"):
                raise ValueError("blocked prepare spec 缺少固定原因")
        else:
            raise ValueError("artifact-family prepare spec 状态无效")
        for relative_path in self.directory_relative_paths:
            _validate_relative_path(relative_path)


@dataclass(frozen=True, slots=True)
class ArtifactFamilyPreparedAssets:
    """保存上层严格资产 manifest 的最小、脱敏验证投影。"""

    task_id: str
    verification_status: str
    input_draft_sha256: str
    manifest_sha256: str | None
    relative_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        """验证 verified/unverified 状态与摘要、路径闭集配对。

        输入参数：
            无；读取冻结 DTO 字段。
        输出返回值：
            无；状态与字段一致时完成构造。
        异常：
            ValueError：身份、摘要、状态或路径不规范。
        """

        if not self.task_id or self.verification_status not in {
            "verified",
            "unverified",
        }:
            raise ValueError("artifact-family prepared assets 状态无效")
        if _SHA256_PATTERN.fullmatch(self.input_draft_sha256) is None:
            raise ValueError("artifact-family input draft 摘要无效")
        if self.verification_status == "verified":
            if (
                not isinstance(self.manifest_sha256, str)
                or _SHA256_PATTERN.fullmatch(self.manifest_sha256) is None
            ):
                raise ValueError("verified asset manifest 摘要无效")
        elif self.manifest_sha256 is not None:
            raise ValueError("unverified assets 不得携带 manifest 摘要")
        if not self.relative_paths or len(self.relative_paths) != len(
            set(self.relative_paths)
        ):
            raise ValueError("artifact-family asset 路径闭集无效")
        for relative_path in self.relative_paths:
            _validate_relative_path(relative_path)


_SAFE_ZIP_EXTRACT_PROGRAM = r"""import os
import shutil
import stat
import sys
import zipfile
from pathlib import PurePosixPath

archive_path, destination_path = sys.argv[1:]
if os.path.islink(destination_path):
    raise SystemExit(20)
destination_real = os.path.realpath(destination_path)
os.makedirs(destination_real, mode=0o700, exist_ok=True)
with zipfile.ZipFile(archive_path, mode="r") as archive:
    members = archive.infolist()
    if not members or len(members) > 4096:
        raise SystemExit(21)
    total_size = 0
    seen = set()
    for member in members:
        name = member.filename
        relative = PurePosixPath(name)
        normalized_name = str(relative) + ("/" if member.is_dir() else "")
        mode = member.external_attr >> 16
        if (
            not name
            or "\\" in name
            or "\x00" in name
            or normalized_name != name
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or stat.S_ISLNK(mode)
            or member.flag_bits & 0x1
            or name in seen
            or member.file_size > 268435456
        ):
            raise SystemExit(22)
        seen.add(name)
        total_size += member.file_size
        if total_size > 1073741824:
            raise SystemExit(23)
        target = os.path.join(destination_real, *relative.parts)
        if os.path.commonpath((destination_real, target)) != destination_real:
            raise SystemExit(24)
        if member.is_dir():
            os.makedirs(target, mode=0o700, exist_ok=True)
            continue
        parent = os.path.dirname(target)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        cursor = destination_real
        for part in relative.parts[:-1]:
            cursor = os.path.join(cursor, part)
            if os.path.islink(cursor):
                raise SystemExit(25)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags, 0o600)
        with archive.open(member, mode="r") as source, os.fdopen(
            descriptor, mode="wb"
        ) as destination:
            shutil.copyfileobj(source, destination, length=1048576)
"""

_VERIFY_MATERIALIZED_FILE_PROGRAM = r"""import hashlib
import os
import stat
import sys


def regular_file_identity(path):
    '''
    功能：以 O_NOFOLLOW 打开普通文件并返回稳定身份、大小与 SHA-256。
    输入参数：path 为 host 已从冻结 guest home/shared 合成的绝对路径。
    输出返回值：返回 (device, inode, size, sha256)；任一符号链接、非普通
        文件或读取期间身份漂移均失败关闭且不输出路径。
    '''
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1048576)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(
            getattr(opened, field) != getattr(after, field)
            or getattr(after, field) != getattr(current, field)
            for field in stable_fields
        ):
            raise ValueError
        if size != after.st_size:
            raise ValueError
        return after.st_dev, after.st_ino, size, digest.hexdigest()
    finally:
        os.close(descriptor)


def main():
    '''
    功能：确认 materialize 目标不是链接/硬链接且与 verified shared 字节相同。
    输入参数：sys.argv[1:3] 依次为 verified shared 源和 guest 目标路径。
    输出返回值：成功静默退出 0；失败静默退出 1，不回显路径或内容。
    '''
    try:
        source = regular_file_identity(sys.argv[1])
        destination = regular_file_identity(sys.argv[2])
        if source[:2] == destination[:2] or source[2:] != destination[2:]:
            raise ValueError
    except Exception:
        raise SystemExit(1) from None


main()
"""


_BATCH_TASK_ID = "Operation-FileOperate-BatchOperation-003"
_BATCH_EVIDENCE = OSWORLD_ARTIFACT_EVIDENCE_SPECS[_BATCH_TASK_ID]
_BATCH_SPEC = ArtifactFamilyTaskPrepareSpec(
    schema_id=ARTIFACT_FAMILY_TASK_PREPARE_SCHEMA_ID,
    spec_id=(
        "paraguibench.osworld.artifact-family-task-prepare."
        "Operation-FileOperate-BatchOperation-003.v1"
    ),
    task_id=_BATCH_TASK_ID,
    task_uid="c919165f-cdfb-413a-8e00-424a0a133620",
    task_source="OSWorld",
    task_type="OSWorld脚本",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/5df7b33a-9f77-4101-823e-02f863e1c1ae.json"),
    canonical_asset_mode=_STRICT_ASSET_MODE,
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_relative_path=(
        "benchmark/assets/manifests/Operation-FileOperate-BatchOperation-003.json"
    ),
    canonical_asset_manifest_sha256=(
        "d3f9e22e25dae48d20db70761d51a613b0487921954694b8b1162e29aee91eb5"
    ),
    input_draft_relative_path=(
        "benchmark/assets/manifests/osworld-state-drafts/"
        "Operation-FileOperate-BatchOperation-003.input.draft.json"
    ),
    input_draft_sha256=(
        "243b21aa85fe4d6d01d2f305e77b16164f086648335534f13a94798c304c02f3"
    ),
    source_task_id=_BATCH_EVIDENCE.source_task_id,
    source_evaluator_id=_BATCH_EVIDENCE.source_evaluator_id,
    source_contract_sha256=_BATCH_EVIDENCE.source_contract_sha256,
    evidence_spec_sha256=_BATCH_EVIDENCE.evidence_spec_sha256,
    source_snapshot_id="libreoffice_writer",
    required_context_ids=(
        "libreoffice_writer",
        "file_manager",
        "pdf_viewer",
    ),
    prepare_status=_ACTIONABLE,
    blocked_reason_id=None,
    asset_bindings=(
        ArtifactFamilyAssetBinding(
            asset_relative_path="raw_book.zip",
            guest_relative_path="Desktop/book.zip",
            purpose="task_input_bundle",
        ),
    ),
    directory_relative_paths=(),
    actions=(
        ArtifactFamilyPrepareAction(
            action_id="materialize.batch-assets.v1",
            operation="materialize_assets",
        ),
        ArtifactFamilyPrepareAction(
            action_id="safe-extract.book-zip.v1",
            operation="safe_extract_zip",
            home_relative_path="Desktop/book.zip",
            destination_relative_path="Desktop",
        ),
        ArtifactFamilyPrepareAction(
            action_id="launch.files-book.v1",
            operation="launch_with_home_path",
            argv_prefix=("nautilus",),
            home_relative_path="Desktop/book",
        ),
        ArtifactFamilyPrepareAction(
            action_id="open.spectral-graph-theory-pdf.v1",
            operation="open_home_path",
            home_relative_path="Desktop/book/Spectral Graph Theory.pdf",
        ),
    ),
    finalize_action_id=_BATCH_EVIDENCE.finalize_action_id,
    finalize_options_json=_BATCH_EVIDENCE.finalize_options_json,
)


def _asset(
    asset_relative_path: str,
    guest_relative_path: str,
    purpose: str,
) -> ArtifactFamilyAssetBinding:
    """构造一个经统一路径门禁的不可变资产绑定。

    输入参数：
        asset_relative_path：未来严格 manifest 在 shared 下的相对路径。
        guest_relative_path：旧最终 config 固定的 guest-home 相对目标。
        purpose：input draft 固定的用途。
    输出返回值：
        ``ArtifactFamilyAssetBinding`` 冻结实例。
    """

    return ArtifactFamilyAssetBinding(
        asset_relative_path=asset_relative_path,
        guest_relative_path=guest_relative_path,
        purpose=purpose,
    )


def _action(
    action_id: str,
    operation: str,
    *,
    argv_prefix: tuple[str, ...] = (),
    home_relative_path: str | None = None,
    destination_relative_path: str | None = None,
    literal_value: str | None = None,
) -> ArtifactFamilyPrepareAction:
    """构造一个只含相对路径或固定字面的动作规格。

    输入参数：
        action_id/operation：版本化动作身份和闭集操作类型。
        argv_prefix：不含 shell 与绝对路径的固定 argv 前缀。
        home_relative_path/destination_relative_path：由 guest home 派生的
            相对路径。
        literal_value：固定 URL、窗口标题或等待秒数字面。
    输出返回值：
        ``ArtifactFamilyPrepareAction`` 冻结实例。
    """

    return ArtifactFamilyPrepareAction(
        action_id=action_id,
        operation=operation,
        argv_prefix=argv_prefix,
        home_relative_path=home_relative_path,
        destination_relative_path=destination_relative_path,
        literal_value=literal_value,
    )


def _build_task_spec(
    *,
    task_id: str,
    task_uid: str,
    task_tag: str,
    evaluator_path: str,
    canonical_prepare_reference_sha256: str | None,
    canonical_asset_manifest_sha256: str | None = None,
    input_draft_sha256: str,
    source_snapshot_id: str,
    required_context_ids: tuple[str, ...],
    asset_bindings: tuple[ArtifactFamilyAssetBinding, ...],
    directory_relative_paths: tuple[str, ...],
    actions: tuple[ArtifactFamilyPrepareAction, ...],
    blocked: bool = False,
) -> ArtifactFamilyTaskPrepareSpec:
    """把任务私有旧源字段与当前 evidence spec 合成为冻结规格。

    输入参数：
        task_id/task_uid/task_tag/evaluator_path：canonical 身份字段。
        canonical_prepare_reference_sha256：legacy URL 模式的引用
            SHA-256；strict 模式必须为 ``None``。
        canonical_asset_manifest_sha256：strict 模式中正式 input
            manifest 的 SHA-256；legacy 模式必须为 ``None``。
        input_draft_sha256：逐任务 input draft 的 SHA-256。
        source_snapshot_id/required_context_ids：旧最终 config 的 VM/app
            上下文合同。
        asset_bindings/directory_relative_paths/actions：路径和动作闭集。
        blocked：旧源不能唯一恢复 start context 时为 ``True``。
    输出返回值：
        与 ArtifactEvidenceSpec finalize 合同绑定的不可变规格。
    """

    evidence = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]
    strict_asset_mode = canonical_asset_manifest_sha256 is not None
    return ArtifactFamilyTaskPrepareSpec(
        schema_id=ARTIFACT_FAMILY_TASK_PREPARE_SCHEMA_ID,
        spec_id=(f"paraguibench.osworld.artifact-family-task-prepare.{task_id}.v1"),
        task_id=task_id,
        task_uid=task_uid,
        task_source="OSWorld",
        task_type="OSWorld脚本",
        task_tag=task_tag,
        evaluator_path=evaluator_path,
        canonical_asset_mode=(
            _STRICT_ASSET_MODE if strict_asset_mode else _LEGACY_ASSET_MODE
        ),
        canonical_prepare_reference_sha256=(canonical_prepare_reference_sha256),
        canonical_asset_manifest_relative_path=(
            f"benchmark/assets/manifests/{task_id}.json" if strict_asset_mode else None
        ),
        canonical_asset_manifest_sha256=canonical_asset_manifest_sha256,
        input_draft_relative_path=(
            "benchmark/assets/manifests/osworld-state-drafts/"
            f"{task_id}.input.draft.json"
        ),
        input_draft_sha256=input_draft_sha256,
        source_task_id=evidence.source_task_id,
        source_evaluator_id=evidence.source_evaluator_id,
        source_contract_sha256=evidence.source_contract_sha256,
        evidence_spec_sha256=evidence.evidence_spec_sha256,
        source_snapshot_id=source_snapshot_id,
        required_context_ids=required_context_ids,
        prepare_status=_BLOCKED if blocked else _ACTIONABLE,
        blocked_reason_id=(
            "blocked.source_start_context_ambiguous" if blocked else None
        ),
        asset_bindings=asset_bindings,
        directory_relative_paths=directory_relative_paths,
        actions=actions,
        finalize_action_id=evidence.finalize_action_id,
        finalize_options_json=evidence.finalize_options_json,
    )


def _funding_asset_bindings(
    *,
    include_supported_rate: bool,
) -> tuple[ArtifactFamilyAssetBinding, ...]:
    """构造 Fundings 两个任务共享的 19/20 文件路径闭集。

    输入参数：
        include_supported_rate：是否追加可编辑的支持率工作簿。
    输出返回值：
        保持旧下载分组顺序的不可变资产绑定 tuple。
    """

    bindings = (
        tuple(
            _asset(
                f"ecs{year}.pdf",
                f"Documents/Fundings/ecs/ecs{year}.pdf",
                "reference_input",
            )
            for year in (15, 16, 17, 23, 22, 21, 20, 19, 18)
        )
        + (
            _asset(
                "customer-information-sheet-for-inward-payments-to-hong-kong.pdf",
                (
                    "Documents/Fundings/grf/"
                    "customer-information-sheet-for-inward-payments-to-hong-kong.pdf"
                ),
                "reference_input",
            ),
        )
        + tuple(
            _asset(
                f"grf{year}.pdf",
                f"Documents/Fundings/grf/grf{year}.pdf",
                "reference_input",
            )
            for year in range(15, 24)
        )
    )
    if include_supported_rate:
        bindings += (
            _asset(
                "supported_rate.xlsx",
                "Documents/Fundings/supported_rate.xlsx",
                "editable_target",
            ),
        )
    return bindings


_MATERIALIZE = _action(
    "materialize.verified-assets.v1",
    "materialize_assets",
)
_CREATE_DIRECTORIES = _action(
    "mkdir.source-directories.v1",
    "create_directories",
)
_LAUNCH_CHROME = _action(
    "launch.chrome-cdp.v1",
    "launch_literal",
    argv_prefix=("google-chrome", "--remote-debugging-port=1337"),
)
_WAIT_CHROME = _action(
    "wait.chrome-cdp.v1",
    "wait_chrome_cdp",
)
_LAUNCH_SOCAT = _action(
    "launch.socat-cdp-bridge.v1",
    "launch_literal",
    argv_prefix=(
        "socat",
        "tcp-listen:9222,fork",
        "tcp:localhost:1337",
    ),
)

_COMBINATION_009 = _build_task_spec(
    task_id="Operation-FileOperate-CombinationDocs-009",
    task_uid="4fb43529-485f-4385-a6e8-b861bb562b5f",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/eb303e01-261e-4972-8c07-c9b4e7a4922a.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "44ef3460d8066d32623dfe26fb001c00d9df6d802ab9c02b1de2677281068b6a"
    ),
    input_draft_sha256=(
        "7bef1e921023b36f0decc9a50da2855dbdaf0c1873a3015fd49889cbc90286bc"
    ),
    source_snapshot_id="libreoffice_impress",
    required_context_ids=("libreoffice_impress", "libreoffice_writer"),
    asset_bindings=(
        _asset(
            "lecture1-2021-with-ink.pptx",
            "Desktop/lecture1-2021-with-ink.pptx",
            "editable_target",
        ),
        _asset("notes.docx", "Desktop/notes.docx", "reference_input"),
    ),
    directory_relative_paths=(),
    actions=(
        _MATERIALIZE,
        _action(
            "open.presentation-with-notes.v1",
            "open_home_path",
            home_relative_path="Desktop/lecture1-2021-with-ink.pptx",
        ),
    ),
)

_COMBINATION_010 = _build_task_spec(
    task_id="Operation-FileOperate-CombinationDocs-010",
    task_uid="a1cd6a49-f077-4ae0-88db-5414ef18089c",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/aceb0368-56b8-4073-b70e-3dc9aee184e0.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "78ac4dbf3177a04dc324ebb3948d1109c26f44b37328570e5f04e650e8454b33"
    ),
    input_draft_sha256=(
        "2985f13d5b4f075da2e022a05214cd8c058ae01232ceb53483b1a3cd7b5e07b3"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=(
        "libreoffice_calc",
        "libreoffice_writer",
        "file_manager",
    ),
    asset_bindings=(_asset("exam.zip", "exam.zip", "task_input_bundle"),),
    directory_relative_paths=(),
    actions=(
        _MATERIALIZE,
        _action(
            "safe-extract.exam-zip.v1",
            "safe_extract_zip",
            home_relative_path="exam.zip",
            destination_relative_path=".",
        ),
        _action(
            "launch.writer-reference-answers.v1",
            "launch_with_home_path",
            argv_prefix=("libreoffice", "--writer"),
            home_relative_path="exam/ReferenceAnswers.docx",
        ),
        _action(
            "launch.calc-grades.v1",
            "launch_with_home_path",
            argv_prefix=("libreoffice", "--calc"),
            home_relative_path="exam/grades.xlsx",
        ),
        _action(
            "launch.files-exam.v1",
            "launch_with_home_path",
            argv_prefix=("nautilus",),
            home_relative_path="exam",
        ),
    ),
)

_COMBINATION_011 = _build_task_spec(
    task_id="Operation-FileOperate-CombinationDocs-011",
    task_uid="60ed834a-2f51-4e3b-9b0b-6ed9c24249a4",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/337d318b-aa07-4f4f-b763-89d9a2dd013f.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "2ae12d581b836b8c0236e0336292bca2ee5f59112ec8f1a1c547ac6d761350ac"
    ),
    input_draft_sha256=(
        "fedc42541f1b435d98691ecbef64fa8d1b872ce6f0b79865d3046fc27237a206"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=("libreoffice_calc", "file_manager", "pdf_viewer"),
    asset_bindings=(
        _asset(
            "invoice TII-20220301-90.pdf",
            "Desktop/invoice TII-20220301-90.pdf",
            "reference_input",
        ),
        _asset(
            "Invoice # GES-20220215-82.pdf",
            "Desktop/Invoice # GES-20220215-82.pdf",
            "reference_input",
        ),
        _asset(
            "Invoice # 243729.pdf",
            "Desktop/Invoice # 243729.pdf",
            "reference_input",
        ),
        _asset(
            "Bank-Statement.pdf",
            "Desktop/Bank-Statement.pdf",
            "reference_input",
        ),
    ),
    directory_relative_paths=(),
    actions=(_MATERIALIZE,),
    blocked=False,
)

_COMBINATION_012 = _build_task_spec(
    task_id="Operation-FileOperate-CombinationDocs-012",
    task_uid="a92f8e87-36b0-4da1-aa72-f7b753011488",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/2c1ebcd7-9c6d-4c9a-afad-900e381ecd5e.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "eae7d666d596e783a8cfdc7cd7530add2f9ca273f43329ee1c658ada55aea102"
    ),
    input_draft_sha256=(
        "29e8e2578ddab7396392a837ae27c42d6079cb978ff1969cbe6a93ed4c6eb32b"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=("libreoffice_calc",),
    asset_bindings=(
        _asset(
            "Zheng He .docx",
            "Desktop/students work/Zheng He .docx",
            "context_input",
        ),
        _asset(
            "The literature reviews of weekly readings.docx",
            "Desktop/students work/The literature reviews of weekly readings.docx",
            "context_input",
        ),
        _asset(
            "The British Justice System.docx",
            "Desktop/students work/The British Justice System.docx",
            "context_input",
        ),
        _asset(
            "quiz2.docx",
            "Desktop/students work/quiz2.docx",
            "context_input",
        ),
        _asset(
            "quiz.docx",
            "Desktop/students work/quiz.docx",
            "context_input",
        ),
        _asset(
            "Q1&2&3.docx",
            "Desktop/students work/Q1&2&3.docx",
            "context_input",
        ),
        _asset(
            "Photo Ethics in Journalism.docx",
            "Desktop/students work/Photo Ethics in Journalism.docx",
            "context_input",
        ),
        _asset(
            "cassie.docx",
            "Desktop/students work/cassie.docx",
            "context_input",
        ),
        _asset(
            "case study.docx",
            "Desktop/students work/case study.docx",
            "editable_target",
        ),
        _asset(
            "irregularrules02.pdf",
            "Desktop/Grammar rules PDF/irregularrules02.pdf",
            "context_input",
        ),
        _asset(
            "irregularrules01.pdf",
            "Desktop/Grammar rules PDF/irregularrules01.pdf",
            "context_input",
        ),
        _asset(
            "fragrules.pdf",
            "Desktop/Grammar rules PDF/fragrules.pdf",
            "context_input",
        ),
        _asset(
            "csfsrules.pdf",
            "Desktop/Grammar rules PDF/csfsrules.pdf",
            "context_input",
        ),
        _asset(
            "Public Lecture Teaching Plan.docx",
            "Desktop/Public Lecture Teaching Plan.docx",
            "context_input",
        ),
        _asset(
            "Course Timetable.xlsx",
            "Desktop/Course Timetable.xlsx",
            "context_input",
        ),
    ),
    directory_relative_paths=(
        "Desktop/students work",
        "Desktop/Lec powerpoint",
        "Desktop/Grammar test",
        "Desktop/Grammar rules PDF",
        "Desktop/FDI",
    ),
    actions=(_CREATE_DIRECTORIES, _MATERIALIZE),
    blocked=False,
)

_COMBINATION_013 = _build_task_spec(
    task_id="Operation-FileOperate-CombinationDocs-013",
    task_uid="3d514057-efd2-44b9-98dd-4b092ac2828a",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/3d514057-efd2-44b9-98dd-4b092ac2828a.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "4802e07b0cad75535f6b872c3443eb7adbde983d8c387f3b784349b8c4347dda"
    ),
    input_draft_sha256=(
        "bb1d7ff409a965199d677bf7e5af612b3a31ada02aa4b64ceccca5a96f04462b"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=("libreoffice_calc", "file_manager"),
    asset_bindings=_funding_asset_bindings(include_supported_rate=False),
    directory_relative_paths=(
        "Documents/Fundings/ecs",
        "Documents/Fundings/grf",
    ),
    actions=(_CREATE_DIRECTORIES, _MATERIALIZE),
    blocked=False,
)

_COMBINATION_014 = _build_task_spec(
    task_id="Operation-FileOperate-CombinationDocs-014",
    task_uid="f5e1b40b-ea38-4d9f-9cf6-11f1dff5f2cc",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/881deb30-9549-4583-a841-8270c65f2a17.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "c792c12e68decebc2fc0c48fa778ff8d91987196a588d832a46592493735726a"
    ),
    input_draft_sha256=(
        "02931bd67300f91acf9eabf72df2ddfdaa6ddfe75d46d42151f6af3772f1191a"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=("libreoffice_calc", "file_manager"),
    asset_bindings=_funding_asset_bindings(include_supported_rate=True),
    directory_relative_paths=(
        "Documents/Fundings/ecs",
        "Documents/Fundings/grf",
    ),
    actions=(
        _CREATE_DIRECTORIES,
        _MATERIALIZE,
        _action(
            "open.supported-rate-workbook.v1",
            "open_home_path",
            home_relative_path="Documents/Fundings/supported_rate.xlsx",
        ),
        _action(
            "wait.supported-rate-open.5s.v1",
            "sleep_seconds",
            literal_value="5",
        ),
        _action(
            "open.grf-directory.v1",
            "open_home_path",
            home_relative_path="Documents/Fundings/grf",
        ),
        _action(
            "open.ecs-directory.v1",
            "open_home_path",
            home_relative_path="Documents/Fundings/ecs",
        ),
    ),
)

_SEARCH_001 = _build_task_spec(
    task_id="Operation-FileOperate-SearchAndWrite-001",
    task_uid="e9e7bcf6-92da-4ff0-aaea-821099370093",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/e9e7bcf6-92da-4ff0-aaea-821099370093.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "7991206554eb660df0e78e347cafa6cdadd54dbaa1ef67dc6d82a6e16e10028f"
    ),
    input_draft_sha256=(
        "e60771c9721cb34e787b9b710c5615c7836f5e86215e81060e7f973de92fa39a"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=("libreoffice_calc", "chrome"),
    asset_bindings=(
        _asset(
            "Professor_Contact.xlsx",
            "Desktop/Professor_Contact.xlsx",
            "editable_target",
        ),
    ),
    directory_relative_paths=(),
    actions=(
        _LAUNCH_CHROME,
        _WAIT_CHROME,
        _LAUNCH_SOCAT,
        _MATERIALIZE,
        _action(
            "open.professor-contact-workbook.v1",
            "open_home_path",
            home_relative_path="Desktop/Professor_Contact.xlsx",
        ),
    ),
)

_SEARCH_003 = _build_task_spec(
    task_id="Operation-FileOperate-SearchAndWrite-003",
    task_uid="51d7a7fe-e659-4de0-8345-c2c04da90373",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/51d7a7fe-e659-4de0-8345-c2c04da90373.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "45ac54b289ae4a8876a417db28a0da65eb4b87e9752abd5e05444b00dca00c14"
    ),
    input_draft_sha256=(
        "12954501688c8f121fdf3c81b8e68645d4951c7cd1dc35ef5cb221577b86b486"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=(
        "libreoffice_calc",
        "chrome",
        "libreoffice_writer",
    ),
    asset_bindings=(
        _asset(
            "2023_validation_Book_Reading_Rate.xlsx",
            "Desktop/2023_validation_Book_Reading_Rate.xlsx",
            "reference_input",
        ),
        _asset(
            "book_list_result.docx",
            "Desktop/book_list_result.docx",
            "editable_target",
        ),
    ),
    directory_relative_paths=(),
    actions=(
        _MATERIALIZE,
        _action(
            "open.book-reading-rate-workbook.v1",
            "open_home_path",
            home_relative_path=("Desktop/2023_validation_Book_Reading_Rate.xlsx"),
        ),
    ),
)

_SEARCH_005 = _build_task_spec(
    task_id="Operation-FileOperate-SearchAndWrite-005",
    task_uid="dce61462-cf48-42d9-9466-5a0171aa5d12",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/dce61462-cf48-42d9-9466-5a0171aa5d12.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "b7929e9fbd245348d8bac250d2e1d845c85aa8ef794f4354ab0920e669701594"
    ),
    input_draft_sha256=(
        "201af93ba6124a87aef9f71b2eabd291bbd7e001da0af29f35bbb268c44b63fd"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=("libreoffice_calc", "chrome"),
    asset_bindings=(
        _asset(
            "best_awards_acl.xlsx",
            "Desktop/best_awards_acl.xlsx",
            "editable_target",
        ),
    ),
    directory_relative_paths=(),
    actions=(
        _MATERIALIZE,
        _action(
            "open.acl-awards-workbook.v1",
            "open_home_path",
            home_relative_path="Desktop/best_awards_acl.xlsx",
        ),
        _LAUNCH_CHROME,
        _WAIT_CHROME,
        _LAUNCH_SOCAT,
        _action(
            "open.acl-anthology-tab.v1",
            "launch_literal",
            argv_prefix=(
                "google-chrome",
                "--new-tab",
                "https://aclanthology.org/",
            ),
        ),
    ),
)

_SEARCH_009 = _build_task_spec(
    task_id="Operation-FileOperate-SearchAndWrite-009",
    task_uid="14b28a49-e101-4458-835e-2067823ddefb",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/14b28a49-e101-4458-835e-2067823ddefb.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "067bce792a9cea3dca6c0ff912fddb82419fa43cc01eb3b301013fda0d88eb7e"
    ),
    input_draft_sha256=(
        "47d8bae0c32cd1715b485029335b2852e4ef04fee97d57e2c90e51ea21fa89b8"
    ),
    source_snapshot_id="chrome",
    required_context_ids=("chrome", "libreoffice_calc"),
    asset_bindings=(_asset("movies.xlsx", "Desktop/movies.xlsx", "editable_target"),),
    directory_relative_paths=(),
    actions=(
        _LAUNCH_CHROME,
        _WAIT_CHROME,
        _LAUNCH_SOCAT,
        _action(
            "open.imdb-tab.v1",
            "launch_literal",
            argv_prefix=(
                "google-chrome",
                "--new-tab",
                "https://www.imdb.com",
            ),
        ),
        _MATERIALIZE,
        _action(
            "launch.calc-movies.v1",
            "launch_with_home_path",
            argv_prefix=("libreoffice", "--calc"),
            home_relative_path="Desktop/movies.xlsx",
        ),
    ),
)

_SETTINGS_001 = _build_task_spec(
    task_id="Operation-FileOperate-Settings-001",
    task_uid="9b5220d5-f1f0-4db9-902d-ad41aae4d775",
    task_tag="FileOperate",
    evaluator_path=("eval/osworld_scripts/9b5220d5-f1f0-4db9-902d-ad41aae4d775.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "8de1a8fa801bc0aa26cca86033a6f8370f1efe011369229ad821f8240922f6cf"
    ),
    input_draft_sha256=(
        "9da20cc6eb11527c6d3d57db761222900941b3e76c382a6f2f9bdb2fb13cb034"
    ),
    source_snapshot_id="vlc",
    required_context_ids=("vlc", "libreoffice_impress"),
    asset_bindings=(
        _asset(
            "landscape.mp4",
            "Desktop/landscape.mp4",
            "reference_input",
        ),
        _asset(
            "Robotic_Workshop_Infographics.pptx",
            "Desktop/Robotic_Workshop_Infographics.pptx",
            "editable_target",
        ),
    ),
    directory_relative_paths=(),
    actions=(
        _MATERIALIZE,
        _action(
            "open.robotic-workshop-presentation.v1",
            "open_home_path",
            home_relative_path="Desktop/Robotic_Workshop_Infographics.pptx",
        ),
        _action(
            "wait.presentation-open.3s.v1",
            "sleep_seconds",
            literal_value="3",
        ),
        _action(
            "launch.vlc-landscape-repeat.v1",
            "launch_with_home_path",
            argv_prefix=("vlc", "--repeat"),
            home_relative_path="Desktop/landscape.mp4",
        ),
    ),
)

_WEB_SEARCH_001 = _build_task_spec(
    task_id="Operation-WebOperate-SearchAndWrite-001",
    task_uid="d017201e-a098-46ab-86be-6c99d263ecff",
    task_tag="WebOperate",
    evaluator_path=("eval/osworld_scripts/d017201e-a098-46ab-86be-6c99d263ecff.json"),
    canonical_prepare_reference_sha256=None,
    canonical_asset_manifest_sha256=(
        "9ebff0d599b1f625ee523a2b1fc58cc51968d640148dc11cee96eb17090d821d"
    ),
    input_draft_sha256=(
        "aa6fa549d6407e125d43b650ac843d5d1f5be26460da27dc0cb7e65a34826a03"
    ),
    source_snapshot_id="libreoffice_calc",
    required_context_ids=("libreoffice_calc", "file_manager", "chrome"),
    asset_bindings=(
        _asset(
            "restaurants.txt",
            "Desktop/restaurants.txt",
            "reference_input",
        ),
        _asset(
            "MUST_VISIT.xlsx",
            "Desktop/MUST_VISIT.xlsx",
            "editable_target",
        ),
    ),
    directory_relative_paths=(),
    actions=(
        _MATERIALIZE,
        _action(
            "open.must-visit-workbook.v1",
            "open_home_path",
            home_relative_path="Desktop/MUST_VISIT.xlsx",
        ),
        _action(
            "open.restaurants-text.v1",
            "open_home_path",
            home_relative_path="Desktop/restaurants.txt",
        ),
        _action(
            "wait.restaurants-open.5s.v1",
            "sleep_seconds",
            literal_value="5",
        ),
        _action(
            "activate.restaurants-gedit.v1",
            "activate_window",
            literal_value="restaurants.txt (~/Desktop) - gedit",
        ),
    ),
)


ARTIFACT_FAMILY_TASK_PREPARE_SPECS: Mapping[
    str,
    ArtifactFamilyTaskPrepareSpec,
] = MappingProxyType(
    {
        spec.task_id: spec
        for spec in (
            _BATCH_SPEC,
            _COMBINATION_009,
            _COMBINATION_010,
            _COMBINATION_011,
            _COMBINATION_012,
            _COMBINATION_013,
            _COMBINATION_014,
            _SEARCH_001,
            _SEARCH_003,
            _SEARCH_005,
            _SEARCH_009,
            _SETTINGS_001,
            _WEB_SEARCH_001,
        )
    }
)

_FORBIDDEN_TASK_FIELDS = frozenset(
    {
        "argv",
        "command",
        "commands",
        "input_path",
        "output_path",
        "prepare_action",
        "prepare_actions",
        "prepare_command",
        "prepare_commands",
    }
)


class ArtifactFamilyTaskPrepareSource:
    """按不可变 catalog 执行 shell-free artifact-family 准备动作。"""

    def prepare(
        self,
        task: Mapping[str, Any],
        controller: Any,
        *,
        guest_shared_dir: str | None,
        prepared_assets: ArtifactFamilyPreparedAssets,
    ) -> bool:
        """验证全量合同后执行固定、有序准备动作。

        输入参数：
            task：可信 canonical task mapping；仅用于身份绑定。
            controller：同一已启动 OSWorld VM 的窄 controller。
            guest_shared_dir：environment 冻结的 ``.../shared`` 绝对路径。
            prepared_assets：上层严格 manifest 的验证状态与路径闭集投影。
        输出返回值：
            支持且完成返回 ``True``；非 13-task 返回 ``False`` 且零 I/O。
        异常：
            ArtifactFamilyTaskPrepareError：身份、资产、路径、语义状态、
                controller 能力或任一动作失败。
        """

        if not isinstance(task, Mapping):
            raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_IDENTITY_ERROR")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_IDENTITY_ERROR")
        spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS.get(task_id)
        if spec is None:
            return False
        _validate_task(task, spec)
        _validate_prepared_assets(prepared_assets, spec)
        if spec.prepare_status != _ACTIONABLE:
            raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_BLOCKED")
        shared_path = _validate_guest_shared_dir(guest_shared_dir)
        guest_home = shared_path.parent
        _validate_controller(controller, spec)
        for action in spec.actions:
            _execute_action(
                action,
                spec,
                controller,
                guest_home=guest_home,
                shared_path=shared_path,
            )
        return True


def _validate_task(
    task: Mapping[str, Any],
    spec: ArtifactFamilyTaskPrepareSpec,
) -> None:
    """验证 canonical 身份与可变 prepare 引用摘要。

    输入参数：
        task：按 task_id 命中的 canonical mapping。
        spec：不可变 prepare 规格。
    输出返回值：
        无；全部字段精确匹配时返回。
    异常：
        ArtifactFamilyTaskPrepareError：身份漂移或出现命令/路径覆盖字段。
    """

    if _FORBIDDEN_TASK_FIELDS.intersection(task):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_PAYLOAD_ERROR")
    expected = {
        "task_id": spec.task_id,
        "task_uid": spec.task_uid,
        "task_source": spec.task_source,
        "task_type": spec.task_type,
        "task_tag": spec.task_tag,
        "evaluator_path": spec.evaluator_path,
    }
    has_prepare_reference = "prepare_script_path" in task
    prepare_reference = task.get("prepare_script_path")
    has_manifest_reference = "asset_manifest" in task
    manifest_reference = task.get("asset_manifest")
    if spec.canonical_asset_mode == _LEGACY_ASSET_MODE:
        asset_declaration_matches = bool(
            has_prepare_reference
            and not has_manifest_reference
            and isinstance(prepare_reference, str)
            and prepare_reference
            and isinstance(spec.canonical_prepare_reference_sha256, str)
            and hashlib.sha256(prepare_reference.encode("utf-8", "strict")).hexdigest()
            == spec.canonical_prepare_reference_sha256
        )
    elif spec.canonical_asset_mode == _STRICT_ASSET_MODE:
        asset_declaration_matches = bool(
            not has_prepare_reference
            and has_manifest_reference
            and isinstance(manifest_reference, str)
            and manifest_reference
            and manifest_reference == spec.canonical_asset_manifest_relative_path
        )
    else:
        asset_declaration_matches = False
    if (
        any(task.get(key) != value for key, value in expected.items())
        or not asset_declaration_matches
    ):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_IDENTITY_ERROR")


def _validate_prepared_assets(
    prepared_assets: ArtifactFamilyPreparedAssets,
    spec: ArtifactFamilyTaskPrepareSpec,
) -> None:
    """在任何 I/O 前验证资产完整性状态与文件闭集。

    输入参数：
        prepared_assets：上层严格验证投影。
        spec：任务所需资产闭集。
    输出返回值：
        无；verified 状态、身份、draft 摘要与有序路径均一致时返回。
    异常：
        ArtifactFamilyTaskPrepareError：资产未验证或任一身份/路径漂移。
    """

    if not isinstance(prepared_assets, ArtifactFamilyPreparedAssets):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_ASSET_ERROR")
    expected_paths = tuple(
        binding.asset_relative_path for binding in spec.asset_bindings
    )
    if (
        prepared_assets.verification_status != "verified"
        or prepared_assets.task_id != spec.task_id
        or prepared_assets.input_draft_sha256 != spec.input_draft_sha256
        or prepared_assets.relative_paths != expected_paths
        or (
            spec.canonical_asset_mode == _STRICT_ASSET_MODE
            and prepared_assets.manifest_sha256 != spec.canonical_asset_manifest_sha256
        )
    ):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_ASSET_ERROR")


def _validate_guest_shared_dir(value: str | None) -> PurePosixPath:
    """从冻结 shared 绝对路径安全派生 guest home。

    输入参数：
        value：应形如 ``/<guest-home>/shared`` 的规范 POSIX 路径。
    输出返回值：
        安全 ``PurePosixPath``。
    异常：
        ArtifactFamilyTaskPrepareError：路径为空、非规范或可逃逸。
    """

    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/")[1:])
    ):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_PATH_ERROR")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path.name != "shared"
        or path.parent == PurePosixPath("/")
        or str(path) != value
    ):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_PATH_ERROR")
    return path


def _validate_controller(
    controller: Any,
    spec: ArtifactFamilyTaskPrepareSpec,
) -> None:
    """在首次副作用前验证全部动作所需 controller 能力。

    输入参数：
        controller：environment 提供的窄 controller。
        spec：用于推导完整能力集合的 prepare 规格。
    输出返回值：
        无；所有需要的方法均可调用时返回。
    异常：
        ArtifactFamilyTaskPrepareError：任一能力缺失。
    """

    required = {"execute"}
    if any(
        action.operation in {"launch_literal", "launch_with_home_path"}
        for action in spec.actions
    ):
        required.add("launch")
    if any(action.operation == "open_home_path" for action in spec.actions):
        required.add("open_path")
    if any(action.operation == "wait_chrome_cdp" for action in spec.actions):
        required.add("wait_for_chrome_cdp")
    if any(action.operation == "activate_window" for action in spec.actions):
        required.add("activate_window")
    if any(not callable(getattr(controller, name, None)) for name in required):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_CONTROLLER_ERROR")


def _execute_action(
    action: ArtifactFamilyPrepareAction,
    spec: ArtifactFamilyTaskPrepareSpec,
    controller: Any,
    *,
    guest_home: PurePosixPath,
    shared_path: PurePosixPath,
) -> None:
    """执行一项已完成全局预检的固定动作。

    输入参数：
        action：catalog 内冻结动作。
        spec：提供资产绑定的任务规格。
        controller：已通过完整能力预检的 OSWorld controller。
        guest_home/shared_path：从同一冻结 shared binding 派生的路径。
    输出返回值：
        无；动作成功完成即返回。
    异常：
        ArtifactFamilyTaskPrepareError：同步 argv 失败或动作类型漂移。
    """

    try:
        if action.operation == "create_directories":
            _execute_checked(
                controller,
                [
                    "mkdir",
                    "-p",
                    "--",
                    *(
                        str(guest_home / relative_path)
                        for relative_path in spec.directory_relative_paths
                    ),
                ],
            )
            return
        if action.operation == "materialize_assets":
            for binding in spec.asset_bindings:
                destination = guest_home / binding.guest_relative_path
                _execute_checked(
                    controller,
                    ["mkdir", "-p", "--", str(destination.parent)],
                )
                _execute_checked(
                    controller,
                    [
                        "cp",
                        "--no-dereference",
                        "--remove-destination",
                        "--",
                        str(shared_path / binding.asset_relative_path),
                        str(destination),
                    ],
                )
                _execute_checked(
                    controller,
                    [
                        "python3",
                        "-I",
                        "-c",
                        _VERIFY_MATERIALIZED_FILE_PROGRAM,
                        str(shared_path / binding.asset_relative_path),
                        str(destination),
                    ],
                )
            return
        if action.operation == "safe_extract_zip":
            source = guest_home / str(action.home_relative_path)
            destination = guest_home / str(action.destination_relative_path)
            _execute_checked(
                controller,
                [
                    "python3",
                    "-I",
                    "-c",
                    _SAFE_ZIP_EXTRACT_PROGRAM,
                    str(source),
                    str(destination),
                ],
            )
            return
        if action.operation == "launch_with_home_path":
            controller.launch(
                [
                    *action.argv_prefix,
                    str(guest_home / str(action.home_relative_path)),
                ]
            )
            return
        if action.operation == "launch_literal":
            controller.launch(list(action.argv_prefix))
            return
        if action.operation == "open_home_path":
            controller.open_path(str(guest_home / str(action.home_relative_path)))
            return
        if action.operation == "wait_chrome_cdp":
            controller.wait_for_chrome_cdp(port=1337, timeout=15.0)
            return
        if action.operation == "sleep_seconds":
            _execute_checked(controller, ["sleep", str(action.literal_value)])
            return
        if action.operation == "activate_window":
            controller.activate_window(str(action.literal_value))
            return
    except ArtifactFamilyTaskPrepareError:
        raise
    except Exception:
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_ACTION_ERROR") from None
    raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_ACTION_ERROR")


def _execute_checked(controller: Any, argv: list[str]) -> None:
    """执行固定同步 argv 并要求规范零返回码。

    输入参数：
        controller：已验证具有 ``execute`` 的 controller。
        argv：模块内生成的 shell-free 参数列表。
    输出返回值：
        无；返回码严格为整数零时返回。
    异常：
        ArtifactFamilyTaskPrepareError：调用异常或返回码非零/非法。
    """

    try:
        result = controller.execute(argv)
    except Exception:
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_ACTION_ERROR") from None
    returncode = getattr(result, "returncode", None)
    if (
        not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or returncode != 0
    ):
        raise ArtifactFamilyTaskPrepareError("ARTIFACT_PREPARE_ACTION_ERROR")


__all__ = [
    "ARTIFACT_FAMILY_TASK_PREPARE_SCHEMA_ID",
    "ARTIFACT_FAMILY_TASK_PREPARE_SPECS",
    "ArtifactFamilyAssetBinding",
    "ArtifactFamilyPreparedAssets",
    "ArtifactFamilyPrepareAction",
    "ArtifactFamilyTaskPrepareError",
    "ArtifactFamilyTaskPrepareSource",
    "ArtifactFamilyTaskPrepareSpec",
]
