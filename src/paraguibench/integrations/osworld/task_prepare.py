"""OSWorld 特定任务的版本化、闭集准备协议。

本模块只接受 canonical task 身份用于选择预先审计的规格；任务 JSON
无法提供命令、参数或路径。所有 guest 调用都以 ``shell=False``
的 controller 窄接口越过信任边界。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any

from paraguibench.integrations.osworld.bookmark_contracts import (
    OSWORLD_BOOKMARK_TASK_BINDINGS,
)

TASK_PREPARE_SPEC_SCHEMA_ID = "paraguibench.osworld.task-prepare-spec.v1"
BOOKMARK_START_CONTEXT_SPEC_SCHEMA_ID = (
    "paraguibench.osworld.bookmark-start-context-spec.v1"
)


class OSWorldTaskPrepareError(RuntimeError):
    """表示任务身份、runtime 路径或固定准备动作未通过门禁。"""


@dataclass(frozen=True, slots=True)
class OSWorldTaskPrepareSpec:
    """描述一个经审计的 OSWorld task-specific prepare 规格。

    字段只保存身份、路径语义与 action ID；可执行 argv 故意不放入
    task 或可变配置，而是由本模块内的闭集分支生成。
    """

    schema_id: str
    spec_id: str
    task_id: str
    task_uid: str
    task_source: str
    asset_manifest: str
    gold_manifest: str
    evaluator_path: str
    source_task_id: str
    source_evaluator_id: str
    source_evaluator_contract_sha256: str
    source_input_relative_path: str
    runtime_input_relative_path: str
    source_output_relative_path: str
    runtime_output_relative_path: str
    input_path_adaptation_id: str
    output_path_adaptation_id: str
    action_ids: tuple[str, ...]
    prepare_spec_sha256: str


@dataclass(frozen=True, slots=True)
class OSWorldBookmarkStartContextSpec:
    """描述 Bookmark 任务在 Agent 前可见的版本化 PDF 上下文。

    字段只包含 canonical task 身份、来源审计摘要、相对资产和
    动作 ID；guest 绝对路径必须在 environment 冻结 shared 目录后
    动态构造，不存入 task 或本规格。
    """

    schema_id: str
    spec_id: str
    task_id: str
    task_uid: str
    task_source: str
    task_type: str
    task_tag: str
    asset_manifest: str
    evaluator_path: str
    context_type: str
    asset_relative_path: str
    asset_size: int
    asset_sha256: str
    open_with: str
    target: str
    source_task_id: str
    source_setup_contract_sha256: str
    source_prepare_contract_sha256: str
    action_ids: tuple[str, ...]
    prepare_spec_sha256: str


def canonical_bookmark_start_context_spec_json(
    spec: OSWorldBookmarkStartContextSpec,
) -> str:
    """将 Bookmark start-context 规格投影为不含自身摘要的 JSON。

    输入参数：
        spec：待序列化的不可变 Bookmark PDF 启动规格。
    输出返回值：
        UTF-8 友好、键排序、无多余空白的 JSON 字符串。
    异常：
        TypeError：``spec`` 不是 ``OSWorldBookmarkStartContextSpec``。
    """

    if not isinstance(spec, OSWorldBookmarkStartContextSpec):
        raise TypeError("Bookmark start-context spec 类型无效")
    payload = {
        "action_ids": list(spec.action_ids),
        "asset_manifest": spec.asset_manifest,
        "asset_relative_path": spec.asset_relative_path,
        "asset_sha256": spec.asset_sha256,
        "asset_size": spec.asset_size,
        "context_type": spec.context_type,
        "evaluator_path": spec.evaluator_path,
        "open_with": spec.open_with,
        "schema_id": spec.schema_id,
        "source_prepare_contract_sha256": (spec.source_prepare_contract_sha256),
        "source_setup_contract_sha256": (spec.source_setup_contract_sha256),
        "source_task_id": spec.source_task_id,
        "spec_id": spec.spec_id,
        "target": spec.target,
        "task_id": spec.task_id,
        "task_source": spec.task_source,
        "task_tag": spec.task_tag,
        "task_type": spec.task_type,
        "task_uid": spec.task_uid,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _with_bookmark_start_context_spec_digest(
    spec: OSWorldBookmarkStartContextSpec,
) -> OSWorldBookmarkStartContextSpec:
    """返回携带 canonical JSON SHA-256 的 Bookmark 启动规格。

    输入参数：
        spec：``prepare_spec_sha256`` 尚未设置的原始规格。
    输出返回值：
        仅摘要字段被替换的不可变规格。
    """

    digest = hashlib.sha256(
        canonical_bookmark_start_context_spec_json(spec).encode(
            "utf-8",
            "strict",
        )
    ).hexdigest()
    return replace(spec, prepare_spec_sha256=digest)


_SETTINGS_003_BOOKMARK_START_CONTEXT = _with_bookmark_start_context_spec_digest(
    OSWorldBookmarkStartContextSpec(
        schema_id=BOOKMARK_START_CONTEXT_SPEC_SCHEMA_ID,
        spec_id=(
            "paraguibench.osworld.bookmark-start-context."
            "Operation-WebOperate-Settings-003.v1"
        ),
        task_id="Operation-WebOperate-Settings-003",
        task_uid="bc69ee94-cf90-4cc4-a6ed-4266daa71706",
        task_source="OSWorld",
        task_type="OSWorld脚本",
        task_tag="WebOperate",
        asset_manifest=(
            "benchmark/assets/manifests/Operation-WebOperate-Settings-003.json"
        ),
        evaluator_path="eval/webnavigate_bookmark_evaluator.py",
        context_type="local_pdf",
        asset_relative_path="2206.08853.pdf",
        asset_size=9_765_032,
        asset_sha256=(
            "68743684c375a3832f89031433cf310912d15c0464378f6095903000870b3f59"
        ),
        open_with="chrome",
        target="all_vms",
        source_task_id="a82b78bb-7fde-4cb3-94a4-035baf10bcf0",
        source_setup_contract_sha256=(
            "053e8b1c8bdbd6756eec8b33f8f7e3db70f783c0fad16446a0b98bf0a80e6d03"
        ),
        source_prepare_contract_sha256=(
            "73fc2466307a30e9c612024d1b5a6472afaa5d408045b5baccb38ab7c32d79b2"
        ),
        action_ids=(
            "validate.shared-pdf.v1",
            "open.chrome-file-uri.v1",
            "wait.chrome-start-context.v1",
        ),
        prepare_spec_sha256="",
    )
)


OSWORLD_BOOKMARK_START_CONTEXT_SPECS: Mapping[
    str,
    OSWorldBookmarkStartContextSpec,
] = MappingProxyType(
    {
        _SETTINGS_003_BOOKMARK_START_CONTEXT.task_id: (
            _SETTINGS_003_BOOKMARK_START_CONTEXT
        )
    }
)


_COMBINATION_DOCS_015_WITHOUT_DIGEST = OSWorldTaskPrepareSpec(
    schema_id=TASK_PREPARE_SPEC_SCHEMA_ID,
    spec_id=(
        "paraguibench.osworld.task-prepare.Operation-FileOperate-CombinationDocs-015.v1"
    ),
    task_id="Operation-FileOperate-CombinationDocs-015",
    task_uid="9f55fdb6-a749-4170-91a2-bebddd3492d7",
    task_source="OSWorld",
    asset_manifest=(
        "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-015.json"
    ),
    gold_manifest=(
        "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-015.json"
    ),
    evaluator_path=("eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"),
    source_task_id="df67aebb-fb3a-44fd-b75b-51b6012df509",
    source_evaluator_id="9f55fdb6-a749-4170-91a2-bebddd3492d7",
    source_evaluator_contract_sha256=(
        "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c"
    ),
    source_input_relative_path="Desktop/references.docx",
    runtime_input_relative_path="shared/references.docx",
    source_output_relative_path="Desktop/references.bib",
    runtime_output_relative_path="Desktop/references.bib",
    input_path_adaptation_id=("paraguibench.osworld.source-desktop-to-shared.v1"),
    output_path_adaptation_id=("paraguibench.osworld.source-path-identity.v1"),
    action_ids=(
        "launch.chrome-cdp.v1",
        "wait.chrome-cdp.v1",
        "launch.socat-cdp-bridge.v1",
        "open.dblp-new-tab.v1",
        "create.references-bib.v1",
        "launch.vscode-references-bib.v1",
        "launch.writer-references-docx.v1",
    ),
    prepare_spec_sha256="",
)


def canonical_task_prepare_spec_json(spec: OSWorldTaskPrepareSpec) -> str:
    """把 prepare 规格投影为不含自身摘要的 canonical JSON。

    输入参数：
        spec：待序列化的不可变 task prepare 规格。
    输出返回值：
        UTF-8 友好、键排序、无多余空白的 JSON 字符串。
    异常：
        TypeError：``spec`` 不是 ``OSWorldTaskPrepareSpec``。
    """

    if not isinstance(spec, OSWorldTaskPrepareSpec):
        raise TypeError("task prepare spec 类型无效")
    payload = {
        "schema_id": spec.schema_id,
        "spec_id": spec.spec_id,
        "task_id": spec.task_id,
        "task_uid": spec.task_uid,
        "task_source": spec.task_source,
        "asset_manifest": spec.asset_manifest,
        "gold_manifest": spec.gold_manifest,
        "evaluator_path": spec.evaluator_path,
        "source_task_id": spec.source_task_id,
        "source_evaluator_id": spec.source_evaluator_id,
        "source_evaluator_contract_sha256": (spec.source_evaluator_contract_sha256),
        "source_input_relative_path": spec.source_input_relative_path,
        "runtime_input_relative_path": spec.runtime_input_relative_path,
        "source_output_relative_path": spec.source_output_relative_path,
        "runtime_output_relative_path": spec.runtime_output_relative_path,
        "input_path_adaptation_id": spec.input_path_adaptation_id,
        "output_path_adaptation_id": spec.output_path_adaptation_id,
        "action_ids": list(spec.action_ids),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _with_prepare_spec_digest(
    spec: OSWorldTaskPrepareSpec,
) -> OSWorldTaskPrepareSpec:
    """返回携带 canonical JSON SHA-256 的新规格。

    输入参数：
        spec：``prepare_spec_sha256`` 尚未设置的原始规格。
    输出返回值：
        仅摘要字段被替换的不可变规格。
    """

    digest = hashlib.sha256(
        canonical_task_prepare_spec_json(spec).encode("utf-8", "strict")
    ).hexdigest()
    return replace(spec, prepare_spec_sha256=digest)


_COMBINATION_DOCS_015 = _with_prepare_spec_digest(_COMBINATION_DOCS_015_WITHOUT_DIGEST)


OSWORLD_TASK_PREPARE_SPECS: Mapping[str, OSWorldTaskPrepareSpec] = MappingProxyType(
    {_COMBINATION_DOCS_015.task_id: _COMBINATION_DOCS_015}
)

_FORBIDDEN_TASK_PAYLOAD_FIELDS = frozenset(
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

_BOOKMARK_PDF_START_CONTEXT_PROGRAM = r"""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import time


def main():
    '''
    功能：验证 shared 内的固定 PDF，并在 Chrome 新窗口中打开。
    输入：sys.argv[1:3] 为固定字节数与 SHA-256，sys.argv[3] 为
        已冻结 shared 绝对目录，sys.argv[4] 为固定文件名。
    输出：无文本输出；任一门禁失败以非零退出码终止。
    '''

    try:
        expected_size = int(sys.argv[1])
    except (IndexError, TypeError, ValueError):
        raise RuntimeError('pdf size invalid') from None
    try:
        expected_sha256 = sys.argv[2]
        shared_root = sys.argv[3]
        asset_name = sys.argv[4]
    except IndexError:
        raise RuntimeError('pdf arguments invalid') from None
    if (
        not os.path.isabs(shared_root)
        or shared_root.endswith('/')
        or '/' in asset_name
        or asset_name in {'', '.', '..'}
        or not asset_name.endswith('.pdf')
        or expected_size <= 5
        or len(expected_sha256) != 64
        or any(character not in '0123456789abcdef' for character in expected_sha256)
    ):
        raise RuntimeError('pdf arguments invalid')
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_descriptor = os.open('/', directory_flags)
    try:
        for component in shared_root.split('/')[1:]:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=directory_descriptor,
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        descriptor = os.open(
            asset_name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size != expected_size
            ):
                raise RuntimeError('pdf file invalid')
            signature = os.read(descriptor, 5)
            if signature != b'%PDF-':
                raise RuntimeError('pdf signature invalid')
            digest = hashlib.sha256()
            digest.update(signature)
            remaining = expected_size - len(signature)
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError('pdf size changed')
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise RuntimeError('pdf size changed')
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError('pdf digest invalid')
            os.lseek(descriptor, 0, os.SEEK_SET)

            if os.path.isdir('/proc/self/fd'):
                verified_path = '/proc/{}/fd/{}'.format(
                    os.getpid(),
                    descriptor,
                )
            else:
                verified_path = '/dev/fd/{}'.format(descriptor)
            environment = os.environ.copy()
            environment['DISPLAY'] = ':0'
            result = subprocess.run(
                [
                    'google-chrome',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--new-window',
                    Path(verified_path).as_uri(),
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
                pass_fds=(descriptor,),
            )
            if result.returncode != 0:
                raise RuntimeError('chrome launch failed')
            time.sleep(2)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_descriptor)


main()
""".strip()


class OSWorldTaskPrepareSource:
    """把 canonical task 路由到模块内编码的固定准备动作。"""

    def prepare(
        self,
        task: Mapping[str, Any],
        controller: Any,
        *,
        guest_shared_dir: str | None,
    ) -> bool:
        """为受支持任务执行固定、shell-free 的有序准备动作。

        输入参数：
            task：canonical task mapping；只读取规格绑定的身份字段。
            controller：当前 VM 的 OSWorld controller，须提供
                ``launch``、``execute`` 与 ``wait_for_chrome_cdp``。
            guest_shared_dir：environment 由 Desktop 动态推导并冻结的
                guest ``shared`` POSIX 绝对路径。
        输出返回值：
            命中并完成支持的规格时返回 ``True``；不相关任务
            返回 ``False`` 且不访问 controller。
        异常：
            OSWorldTaskPrepareError：身份漂移、路径非法、controller
                窄接口缺失或任一固定动作失败。
        """

        if not isinstance(task, Mapping):
            raise OSWorldTaskPrepareError("OSWorld task-specific prepare 身份无效")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise OSWorldTaskPrepareError("OSWorld task-specific prepare 身份无效")
        bookmark_start_context_spec = OSWORLD_BOOKMARK_START_CONTEXT_SPECS.get(task_id)
        if bookmark_start_context_spec is not None:
            return _prepare_bookmark_pdf_start_context(
                task,
                controller,
                spec=bookmark_start_context_spec,
                guest_shared_dir=guest_shared_dir,
            )
        spec = OSWORLD_TASK_PREPARE_SPECS.get(task_id)
        if spec is None:
            return False

        _reject_task_action_payload(task)
        _validate_task_binding(task, spec)
        shared_path = _validate_guest_shared_dir(guest_shared_dir)
        _validate_controller(controller)
        guest_home = shared_path.parent
        input_path = guest_home / PurePosixPath(spec.runtime_input_relative_path)
        output_path = guest_home / PurePosixPath(spec.runtime_output_relative_path)

        try:
            controller.launch(["google-chrome", "--remote-debugging-port=1337"])
            controller.wait_for_chrome_cdp(port=1337, timeout=15.0)
            controller.launch(
                [
                    "socat",
                    "tcp-listen:9222,fork",
                    "tcp:localhost:1337",
                ]
            )
            controller.launch(["google-chrome", "--new-tab", "https://dblp.org/"])
            create_result = controller.execute(["touch", "--", str(output_path)])
            returncode = getattr(create_result, "returncode", None)
            if (
                not isinstance(returncode, int)
                or isinstance(returncode, bool)
                or returncode != 0
            ):
                raise OSWorldTaskPrepareError("OSWorld task-specific prepare 动作失败")
            controller.launch(["code", str(output_path)])
            controller.launch(["libreoffice", "--writer", str(input_path)])
        except OSWorldTaskPrepareError:
            raise
        except Exception:
            raise OSWorldTaskPrepareError(
                "OSWorld task-specific prepare 动作失败"
            ) from None
        return True


def _prepare_bookmark_pdf_start_context(
    task: Mapping[str, Any],
    controller: Any,
    *,
    spec: OSWorldBookmarkStartContextSpec,
    guest_shared_dir: str | None,
) -> bool:
    """为 Settings-003 验证并打开已固定的 shared PDF。

    输入参数：
        task：已按 canonical task ID 命中的可信 task mapping。
        controller：当前 VM 的受控限时 argv 执行边界。
        spec：已按 task ID 命中的不可变 Bookmark 启动规格。
        guest_shared_dir：environment 从当前 desktop 动态推导并冻结的
            guest shared 绝对路径。
    输出返回值：
        有界 PDF 签名验证与 Chrome file-URI 打开成功时返回
        ``True``。
    异常：
        OSWorldTaskPrepareError：身份、manifest、启动上下文、shared
            路径或 controller 动作未通过门禁。
    """

    _reject_task_action_payload(task)
    binding = OSWORLD_BOOKMARK_TASK_BINDINGS[spec.task_id]
    if any(
        task.get(field) != getattr(binding, field)
        for field in (
            "task_id",
            "task_uid",
            "task_source",
            "task_type",
            "task_tag",
            "evaluator_path",
        )
    ):
        raise OSWorldTaskPrepareError("OSWorld bookmark start context 身份无效")
    if (
        task.get("asset_manifest") != spec.asset_manifest
        or "prepare_script_path" in task
        or task.get("agent_start_context")
        != {
            "type": spec.context_type,
            "asset_relative_path": spec.asset_relative_path,
            "open_with": spec.open_with,
            "target": spec.target,
        }
    ):
        raise OSWorldTaskPrepareError("OSWorld bookmark start context 绑定无效")
    shared_path = _validate_guest_shared_dir(guest_shared_dir)
    execute_with_timeout = getattr(
        controller,
        "execute_with_timeout",
        None,
    )
    if not callable(execute_with_timeout):
        raise OSWorldTaskPrepareError(
            "OSWorld bookmark start context controller 能力不完整"
        )
    try:
        result = execute_with_timeout(
            [
                "python3",
                "-I",
                "-c",
                _BOOKMARK_PDF_START_CONTEXT_PROGRAM,
                str(spec.asset_size),
                spec.asset_sha256,
                str(shared_path),
                spec.asset_relative_path,
            ],
            timeout_seconds=30.0,
        )
    except Exception:
        raise OSWorldTaskPrepareError(
            "OSWorld bookmark start context 动作失败"
        ) from None
    returncode = getattr(result, "returncode", None)
    if (
        not isinstance(returncode, int)
        or isinstance(returncode, bool)
        or returncode != 0
    ):
        raise OSWorldTaskPrepareError("OSWorld bookmark start context 动作失败")
    return True


def _reject_task_action_payload(task: Mapping[str, Any]) -> None:
    """拒绝受支持任务中企图覆盖 action 或 runtime 路径的字段。

    输入参数：
        task：已按 ``task_id`` 命中 prepare 规格的 mapping。
    输出返回值：
        无；不含禁止字段时返回。
    异常：
        OSWorldTaskPrepareError：存在任意命令、action 或路径覆盖字段。
    """

    if _FORBIDDEN_TASK_PAYLOAD_FIELDS.intersection(task):
        raise OSWorldTaskPrepareError(
            "OSWorld task-specific prepare payload 不允许覆盖动作"
        )


def _validate_task_binding(
    task: Mapping[str, Any],
    spec: OSWorldTaskPrepareSpec,
) -> None:
    """验证任务的六元身份与已审计规格完全一致。

    输入参数：
        task：待路由的 canonical task mapping。
        spec：按 ``task_id`` 选中的不可变规格。
    输出返回值：
        无；全部字段精确匹配即返回。
    异常：
        OSWorldTaskPrepareError：任一身份字段缺失或漂移。
    """

    expected = {
        "task_id": spec.task_id,
        "task_uid": spec.task_uid,
        "task_source": spec.task_source,
        "asset_manifest": spec.asset_manifest,
        "gold_manifest": spec.gold_manifest,
        "evaluator_path": spec.evaluator_path,
    }
    if any(task.get(key) != value for key, value in expected.items()):
        raise OSWorldTaskPrepareError("OSWorld task-specific prepare 身份未通过绑定")


def _validate_guest_shared_dir(value: str | None) -> PurePosixPath:
    """验证 environment 提供的 guest shared 绝对路径。

    输入参数：
        value：应形如 ``/<guest-home>/shared`` 的已冻结路径。
    输出返回值：
        安全的 ``PurePosixPath``。
    异常：
        OSWorldTaskPrepareError：类型、绝对性、规范形式或目录名非法。
    """

    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/")[1:])
    ):
        raise OSWorldTaskPrepareError("OSWorld task-specific prepare shared 路径无效")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path.name != "shared"
        or path.parent == PurePosixPath("/")
        or str(path) != value
    ):
        raise OSWorldTaskPrepareError("OSWorld task-specific prepare shared 路径无效")
    return path


def _validate_controller(controller: Any) -> None:
    """在产生任何 guest 副作用前验证 controller 窄接口。

    输入参数：
        controller：environment 提供的当前 VM controller。
    输出返回值：
        无；三个必需方法均可调用时返回。
    异常：
        OSWorldTaskPrepareError：任一安全能力缺失。
    """

    if any(
        not callable(getattr(controller, name, None))
        for name in ("launch", "execute", "wait_for_chrome_cdp")
    ):
        raise OSWorldTaskPrepareError(
            "OSWorld task-specific prepare controller 能力不完整"
        )
