"""OSWorld task-specific 准备协议的公共行为测试。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from paraguibench.integrations.osworld.task_prepare import (
    _BOOKMARK_PDF_START_CONTEXT_PROGRAM,
    BOOKMARK_START_CONTEXT_SPEC_SCHEMA_ID,
    OSWORLD_BOOKMARK_START_CONTEXT_SPECS,
    OSWORLD_TASK_PREPARE_SPECS,
    TASK_PREPARE_SPEC_SCHEMA_ID,
    OSWorldTaskPrepareError,
    OSWorldTaskPrepareSource,
    canonical_task_prepare_spec_json,
    canonical_bookmark_start_context_spec_json,
)


class _Controller:
    """记录 task-specific prepare 通过 controller 越过的外部边界。"""

    def __init__(self) -> None:
        """初始化有序调用记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls: list[tuple[Any, ...]] = []

    def launch(self, command: list[str]) -> None:
        """记录一次 shell-free 图形进程启动。

        输入参数：
            command：生产 source 发出的固定 argv。
        输出返回值：
            无。
        """

        self.calls.append(("launch", tuple(command)))

    def wait_for_chrome_cdp(self, *, port: int, timeout: float) -> None:
        """记录 Chrome CDP 就绪门禁。

        输入参数：
            port：固定 guest-local CDP 端口。
            timeout：固定最大等待秒数。
        输出返回值：
            无。
        """

        self.calls.append(("wait_for_chrome_cdp", port, timeout))

    def execute(self, command: list[str]) -> Any:
        """记录同步 shell-free 命令并返回成功结果。

        输入参数：
            command：生产 source 发出的固定 argv。
        输出返回值：
            具有 ``returncode`` 字段的合成结果。
        """

        class _Result:
            """表示一次成功的合成命令结果。"""

            returncode = 0

        self.calls.append(("execute", tuple(command)))
        return _Result()

    def execute_with_timeout(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> Any:
        """记录一次带调用级超时的 shell-free guest 命令。

        输入参数：
            command：生产 source 发出的固定 argv。
            timeout_seconds：该次 guest 准备动作的总超时。
        输出返回值：
            具有零退出码的合成结果。
        """

        class _Result:
            """表示一次成功的合成限时命令。"""

            returncode = 0
            stdout = ""
            stderr = ""

        self.calls.append(("execute_with_timeout", tuple(command), timeout_seconds))
        return _Result()


class _FailingTouchController(_Controller):
    """模拟输出文件创建命令失败的 controller。"""

    def execute(self, command: list[str]) -> Any:
        """记录命令并返回非零退出码。

        输入参数：
            command：生产 source 发出的固定 argv。
        输出返回值：
            具有非零 ``returncode`` 的合成结果。
        """

        class _Result:
            """表示一次失败的合成命令结果。"""

            returncode = 1

        self.calls.append(("execute", tuple(command)))
        return _Result()


class _FailingTimedController(_Controller):
    """模拟 Settings-003 的有界 guest 动作失败。"""

    def __init__(self, *, raises: bool) -> None:
        """初始化失败模式和调用记录。

        输入参数：
            raises：为真时抛出私有异常；否则返回非零退出码。
        输出返回值：
            无。
        """

        super().__init__()
        self.raises = raises

    def execute_with_timeout(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> Any:
        """记录动作并按指定模式产生固定失败。

        输入参数：
            command：生产 source 发出的固定 argv。
            timeout_seconds：该次 guest 动作的总超时。
        输出返回值：
            非异常模式返回非零退出码的合成结果。
        异常：
            RuntimeError：异常模式模拟 controller 私有错误。
        """

        self.calls.append(("execute_with_timeout", tuple(command), timeout_seconds))
        if self.raises:
            raise RuntimeError("PRIVATE_CONTROLLER_FAILURE")

        class _Result:
            """表示一次非零退出码的合成 guest 动作。"""

            returncode = 9
            stdout = "PRIVATE_STDOUT"
            stderr = "PRIVATE_STDERR"

        return _Result()


class _ControllerWithoutTimedExecution:
    """表示不具备调用级超时能力的旧 controller。"""

    def __init__(self) -> None:
        """初始化用于确认零 I/O 的调用记录。

        输入参数：
            无。
        输出返回值：
            无。
        """

        self.calls: list[tuple[Any, ...]] = []


def _combination_docs_task() -> dict[str, Any]:
    """返回与 canonical task 身份绑定的最小任务。

    输入参数：
        无。
    输出返回值：
        只含 prepare 协议身份字段的任务字典。
    """

    return {
        "task_id": "Operation-FileOperate-CombinationDocs-015",
        "task_uid": "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "task_source": "OSWorld",
        "asset_manifest": (
            "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-015.json"
        ),
        "gold_manifest": (
            "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-015.json"
        ),
        "evaluator_path": (
            "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
        ),
    }


def _settings_003_task() -> dict[str, Any]:
    """返回与 canonical Settings-003 启动规格绑定的最小任务。

    输入参数：
        无。
    输出返回值：
        只含 Bookmark evaluator 身份、manifest 和相对上下文的字典。
    """

    return {
        "task_id": "Operation-WebOperate-Settings-003",
        "task_uid": "bc69ee94-cf90-4cc4-a6ed-4266daa71706",
        "task_source": "OSWorld",
        "task_type": "OSWorld脚本",
        "task_tag": "WebOperate",
        "evaluator_path": "eval/webnavigate_bookmark_evaluator.py",
        "asset_manifest": (
            "benchmark/assets/manifests/Operation-WebOperate-Settings-003.json"
        ),
        "agent_start_context": {
            "type": "local_pdf",
            "asset_relative_path": "2206.08853.pdf",
            "open_with": "chrome",
            "target": "all_vms",
        },
    }


def test_combination_docs_prepare_uses_fixed_order_and_runtime_paths() -> None:
    """验证 CombinationDocs-015 只执行版本化有序动作。

    输入参数：
        无。
    输出返回值：
        无；断言输出 BibTeX 保持 Desktop identity，输入
        DOCX 则使用 runtime ``shared`` 适配路径。
    """

    controller = _Controller()

    prepared = OSWorldTaskPrepareSource().prepare(
        _combination_docs_task(),
        controller,
        guest_shared_dir="/home/oai/shared",
    )

    assert prepared is True
    assert controller.calls == [
        (
            "launch",
            ("google-chrome", "--remote-debugging-port=1337"),
        ),
        ("wait_for_chrome_cdp", 1337, 15.0),
        (
            "launch",
            (
                "socat",
                "tcp-listen:9222,fork",
                "tcp:localhost:1337",
            ),
        ),
        ("launch", ("google-chrome", "--new-tab", "https://dblp.org/")),
        (
            "execute",
            ("touch", "--", "/home/oai/Desktop/references.bib"),
        ),
        ("launch", ("code", "/home/oai/Desktop/references.bib")),
        (
            "launch",
            (
                "libreoffice",
                "--writer",
                "/home/oai/shared/references.docx",
            ),
        ),
    ]


def test_task_payload_cannot_inject_prepare_commands_or_paths() -> None:
    """验证任务 JSON 中的伪命令与路径字段不会被消费。

    输入参数：
        无。
    输出返回值：
        无；断言有效 task 附加命令形字段后在任何
        controller I/O 前失败关闭。
    """

    injected_controller = _Controller()
    task = _combination_docs_task()
    injected_task = {
        **task,
        "command": ["sh", "-c", "malicious"],
        "prepare_commands": [["rm", "-rf", "/"]],
        "prepare_actions": [{"argv": ["python", "-c", "print('malicious')"]}],
        "input_path": "/tmp/attacker.docx",
        "output_path": "/tmp/attacker.bib",
    }

    with pytest.raises(OSWorldTaskPrepareError, match="payload"):
        OSWorldTaskPrepareSource().prepare(
            injected_task,
            injected_controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert injected_controller.calls == []


def test_settings_003_opens_verified_runtime_pdf_with_fixed_context() -> None:
    """验证 Settings-003 以固定相对资产构造 Chrome PDF 启动上下文。

    输入参数：
        无；使用已迁移的 pinned manifest 身份和不含镜像用户名的
        相对 PDF 启动上下文。
    输出返回值：
        无；source 必须只向受控 controller 发出一次有界的
        PDF 验证与 Chrome 打开动作。
    """

    controller = _Controller()
    task = _settings_003_task()

    prepared = OSWorldTaskPrepareSource().prepare(
        task,
        controller,
        guest_shared_dir="/guest-home/shared",
    )
    manifest = json.loads(
        (Path(__file__).resolve().parents[2] / task["asset_manifest"]).read_text(
            encoding="utf-8"
        )
    )
    manifest_file = manifest["files"][0]

    assert prepared is True
    assert len(controller.calls) == 1
    action, command, timeout_seconds = controller.calls[0]
    assert action == "execute_with_timeout"
    assert command[:3] == ("python3", "-I", "-c")
    assert command[-4:] == (
        str(manifest_file["size"]),
        manifest_file["sha256"],
        "/guest-home/shared",
        manifest_file["path"],
    )
    assert timeout_seconds == 30.0


def test_settings_003_helper_opens_the_verified_inode_after_path_swap(
    tmp_path: Path,
) -> None:
    """验证 Chrome 消费的是 helper 已完整校验的同一 inode。

    输入参数：
        tmp_path：pytest 提供的隔离 shared、fake Chrome 与观测路径。
    输出返回值：
        无；fake Chrome 在收到 URI 后先将 canonical PDF 替换为
        同大小的另一个普通 PDF，但通过 URI 读到的仍必须是
        helper 持有并校验过的原 inode 字节。
    """

    shared = tmp_path / "shared"
    shared.mkdir()
    original_bytes = b"%PDF-1.7\n" + (b"A" * 128)
    replacement_bytes = b"%PDF-1.7\n" + (b"B" * 128)
    canonical_pdf = shared / "2206.08853.pdf"
    replacement_pdf = shared / "replacement.pdf"
    observed_pdf = tmp_path / "observed.pdf"
    canonical_pdf.write_bytes(original_bytes)
    replacement_pdf.write_bytes(replacement_bytes)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_chrome = fake_bin / "google-chrome"
    fake_chrome.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        "from urllib.parse import unquote, urlsplit\n"
        "canonical = Path(os.environ['PB_CANONICAL_PDF'])\n"
        "canonical.replace(canonical.with_suffix('.verified'))\n"
        "Path(os.environ['PB_REPLACEMENT_PDF']).replace(canonical)\n"
        "uri_path = Path(unquote(urlsplit(sys.argv[-1]).path))\n"
        "Path(os.environ['PB_OBSERVED_PDF']).write_bytes(uri_path.read_bytes())\n",
        encoding="utf-8",
    )
    fake_chrome.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "PB_CANONICAL_PDF": str(canonical_pdf),
        "PB_REPLACEMENT_PDF": str(replacement_pdf),
        "PB_OBSERVED_PDF": str(observed_pdf),
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _BOOKMARK_PDF_START_CONTEXT_PROGRAM,
            str(len(original_bytes)),
            hashlib.sha256(original_bytes).hexdigest(),
            str(shared),
            canonical_pdf.name,
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10.0,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert canonical_pdf.read_bytes() == replacement_bytes
    assert observed_pdf.read_bytes() == original_bytes


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("task_uid", "00000000-0000-0000-0000-000000000000"),
        ("asset_manifest", "benchmark/assets/manifests/private.json"),
        (
            "agent_start_context",
            {
                "type": "local_pdf",
                "asset_relative_path": "different.pdf",
                "open_with": "chrome",
                "target": "all_vms",
            },
        ),
    ],
)
def test_settings_003_binding_drift_fails_before_controller_io(
    field: str,
    drifted_value: Any,
) -> None:
    """验证 Settings-003 身份、manifest 或上下文漂移均先于 I/O 失败。

    输入参数：
        field：本例要篡改的 canonical 绑定字段。
        drifted_value：与版本化启动规格不同的合成值。
    输出返回值：
        无；异常必须脱敏，controller 不得收到任何动作。
    """

    task = _settings_003_task()
    task[field] = drifted_value
    controller = _Controller()

    with pytest.raises(OSWorldTaskPrepareError, match="绑定|身份") as captured:
        OSWorldTaskPrepareSource().prepare(
            task,
            controller,
            guest_shared_dir="/guest-home/shared",
        )

    assert "different.pdf" not in str(captured.value)
    assert "private.json" not in str(captured.value)
    assert controller.calls == []


def test_settings_003_requires_timed_controller_before_guest_io() -> None:
    """验证旧 controller 缺少有界执行能力时失败关闭。

    输入参数：
        无；使用无 ``execute_with_timeout`` 的合成 controller。
    输出返回值：
        无；错误发生在任何 guest I/O 前且不回显路径。
    """

    controller = _ControllerWithoutTimedExecution()

    with pytest.raises(OSWorldTaskPrepareError, match="能力不完整") as captured:
        OSWorldTaskPrepareSource().prepare(
            _settings_003_task(),
            controller,
            guest_shared_dir="/guest-home/shared",
        )

    assert "/guest-home" not in str(captured.value)
    assert controller.calls == []


@pytest.mark.parametrize("raises", [False, True])
def test_settings_003_timed_action_failure_is_generic_and_terminal(
    raises: bool,
) -> None:
    """验证 guest 非零返回或异常都折叠为同一脱敏终止错误。

    输入参数：
        raises：选择 controller 抛异常或返回非零退出码。
    输出返回值：
        无；只允许一次有界动作，错误不得包含 controller 私有输出。
    """

    controller = _FailingTimedController(raises=raises)

    with pytest.raises(OSWorldTaskPrepareError, match="动作失败") as captured:
        OSWorldTaskPrepareSource().prepare(
            _settings_003_task(),
            controller,
            guest_shared_dir="/guest-home/shared",
        )

    message = str(captured.value)
    assert "PRIVATE" not in message
    assert "/guest-home" not in message
    assert len(controller.calls) == 1


def test_combination_docs_prepare_spec_is_versioned_and_self_authenticating() -> None:
    """验证 prepare 规格绑定源身份、路径适配与动作闭集。

    输入参数：
        无。
    输出返回值：
        无；断言 canonical JSON 不含自身摘要，且该字节序列
        与规格中冻结的 SHA-256 完全一致。
    """

    spec = OSWORLD_TASK_PREPARE_SPECS["Operation-FileOperate-CombinationDocs-015"]
    canonical_json = canonical_task_prepare_spec_json(spec)
    payload = json.loads(canonical_json)

    assert spec.schema_id == TASK_PREPARE_SPEC_SCHEMA_ID
    assert payload == {
        "action_ids": [
            "launch.chrome-cdp.v1",
            "wait.chrome-cdp.v1",
            "launch.socat-cdp-bridge.v1",
            "open.dblp-new-tab.v1",
            "create.references-bib.v1",
            "launch.vscode-references-bib.v1",
            "launch.writer-references-docx.v1",
        ],
        "asset_manifest": (
            "benchmark/assets/manifests/Operation-FileOperate-CombinationDocs-015.json"
        ),
        "evaluator_path": (
            "eval/osworld_scripts/9f55fdb6-a749-4170-91a2-bebddd3492d7.json"
        ),
        "gold_manifest": (
            "benchmark/gold/manifests/Operation-FileOperate-CombinationDocs-015.json"
        ),
        "input_path_adaptation_id": (
            "paraguibench.osworld.source-desktop-to-shared.v1"
        ),
        "output_path_adaptation_id": ("paraguibench.osworld.source-path-identity.v1"),
        "runtime_input_relative_path": "shared/references.docx",
        "runtime_output_relative_path": "Desktop/references.bib",
        "schema_id": "paraguibench.osworld.task-prepare-spec.v1",
        "source_evaluator_contract_sha256": (
            "4d4066fddd043a3840c84816445e8727e397691cc1a0ab3f733518a11b510e7c"
        ),
        "source_evaluator_id": "9f55fdb6-a749-4170-91a2-bebddd3492d7",
        "source_input_relative_path": "Desktop/references.docx",
        "source_output_relative_path": "Desktop/references.bib",
        "source_task_id": "df67aebb-fb3a-44fd-b75b-51b6012df509",
        "spec_id": (
            "paraguibench.osworld.task-prepare."
            "Operation-FileOperate-CombinationDocs-015.v1"
        ),
        "task_id": "Operation-FileOperate-CombinationDocs-015",
        "task_source": "OSWorld",
        "task_uid": "9f55fdb6-a749-4170-91a2-bebddd3492d7",
    }
    assert (
        spec.prepare_spec_sha256
        == hashlib.sha256(canonical_json.encode("utf-8", "strict")).hexdigest()
    )
    assert spec.prepare_spec_sha256 == (
        "a1c96eae3d26e6b109f1a39eb5108bf6f8f6275b43ae10ab6ef2724cc845fd30"
    )


def test_settings_003_start_context_spec_is_versioned_and_self_authenticating() -> None:
    """验证 Settings-003 规格固定来源身份、相对资产与动作闭集。

    输入参数：
        无；读取生产 Bookmark start-context 规格。
    输出返回值：
        无；canonical JSON 不含自身摘要，且字节序列与固定
        SHA-256 完全一致。
    """

    spec = OSWORLD_BOOKMARK_START_CONTEXT_SPECS["Operation-WebOperate-Settings-003"]
    canonical_json = canonical_bookmark_start_context_spec_json(spec)
    payload = json.loads(canonical_json)

    assert spec.schema_id == BOOKMARK_START_CONTEXT_SPEC_SCHEMA_ID
    assert payload == {
        "action_ids": [
            "validate.shared-pdf.v1",
            "open.chrome-file-uri.v1",
            "wait.chrome-start-context.v1",
        ],
        "asset_manifest": (
            "benchmark/assets/manifests/Operation-WebOperate-Settings-003.json"
        ),
        "asset_relative_path": "2206.08853.pdf",
        "asset_sha256": (
            "68743684c375a3832f89031433cf310912d15c0464378f6095903000870b3f59"
        ),
        "asset_size": 9_765_032,
        "context_type": "local_pdf",
        "evaluator_path": "eval/webnavigate_bookmark_evaluator.py",
        "open_with": "chrome",
        "schema_id": ("paraguibench.osworld.bookmark-start-context-spec.v1"),
        "source_prepare_contract_sha256": (
            "73fc2466307a30e9c612024d1b5a6472afaa5d408045b5baccb38ab7c32d79b2"
        ),
        "source_setup_contract_sha256": (
            "053e8b1c8bdbd6756eec8b33f8f7e3db70f783c0fad16446a0b98bf0a80e6d03"
        ),
        "source_task_id": "a82b78bb-7fde-4cb3-94a4-035baf10bcf0",
        "spec_id": (
            "paraguibench.osworld.bookmark-start-context."
            "Operation-WebOperate-Settings-003.v1"
        ),
        "target": "all_vms",
        "task_id": "Operation-WebOperate-Settings-003",
        "task_source": "OSWorld",
        "task_tag": "WebOperate",
        "task_type": "OSWorld脚本",
        "task_uid": "bc69ee94-cf90-4cc4-a6ed-4266daa71706",
    }
    assert (
        spec.prepare_spec_sha256
        == hashlib.sha256(canonical_json.encode("utf-8", "strict")).hexdigest()
    )
    assert spec.asset_size == 9_765_032
    assert spec.asset_sha256 == (
        "68743684c375a3832f89031433cf310912d15c0464378f6095903000870b3f59"
    )
    assert spec.prepare_spec_sha256 == (
        "f99e6126d194bee9297cb448ff84bf5f19bce6ba152be9f56b445218ec73a7b1"
    )


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("task_uid", "00000000-0000-0000-0000-000000000000"),
        ("task_source", "Synthetic"),
        ("asset_manifest", "benchmark/assets/manifests/other.json"),
        ("gold_manifest", "benchmark/gold/manifests/other.json"),
        ("evaluator_path", "eval/osworld_scripts/other.json"),
    ],
)
def test_supported_task_identity_drift_fails_before_controller_io(
    field: str,
    drifted_value: str,
) -> None:
    """验证 task ID 相同但任一绑定字段漂移时失败关闭。

    输入参数：
        field：本例要篡改的身份字段。
        drifted_value：与已审计规格不同的合成值。
    输出返回值：
        无；断言异常不回显篡改值，且 controller 无副作用。
    """

    task = _combination_docs_task()
    task[field] = drifted_value
    controller = _Controller()

    with pytest.raises(OSWorldTaskPrepareError, match="身份") as captured:
        OSWorldTaskPrepareSource().prepare(
            task,
            controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert drifted_value not in str(captured.value)
    assert controller.calls == []


def test_unrelated_task_is_a_noop_without_shared_or_controller_capabilities() -> None:
    """验证 catalog 之外的任务不被 task-specific source 改变。

    输入参数：
        无。
    输出返回值：
        无；断言返回 ``False``，且不要求 shared 路径或
        controller 能力。
    """

    prepared = OSWorldTaskPrepareSource().prepare(
        {"task_id": "unrelated-task", "command": ["ignored"]},
        object(),
        guest_shared_dir=None,
    )

    assert prepared is False


@pytest.mark.parametrize(
    "guest_shared_dir",
    [
        None,
        "relative/shared",
        "/home/oai/shared/",
        "/home/oai/../shared",
        "/home/oai/assets",
    ],
)
def test_supported_task_rejects_invalid_runtime_shared_locator(
    guest_shared_dir: str | None,
) -> None:
    """验证受支持任务不会在非法 runtime 根路径上执行。

    输入参数：
        guest_shared_dir：不满足规范绝对 ``shared`` 目录的合成值。
    输出返回值：
        无；断言路径门禁在任何 controller I/O 前失败。
    """

    controller = _Controller()

    with pytest.raises(OSWorldTaskPrepareError, match="shared"):
        OSWorldTaskPrepareSource().prepare(
            _combination_docs_task(),
            controller,
            guest_shared_dir=guest_shared_dir,
        )

    assert controller.calls == []


def test_failed_output_creation_stops_later_actions_with_generic_error() -> None:
    """验证 BibTeX 创建失败时不再启动两个编辑器。

    输入参数：
        无。
    输出返回值：
        无；断言异常不回显 guest 路径，且失败点之后
        不再越过 controller 边界。
    """

    controller = _FailingTouchController()

    with pytest.raises(OSWorldTaskPrepareError, match="动作失败") as captured:
        OSWorldTaskPrepareSource().prepare(
            _combination_docs_task(),
            controller,
            guest_shared_dir="/home/oai/shared",
        )

    assert "/home/oai/" not in str(captured.value)
    assert controller.calls[-1] == (
        "execute",
        ("touch", "--", "/home/oai/Desktop/references.bib"),
    )
    assert not any(
        call == ("launch", ("code", "/home/oai/Desktop/references.bib"))
        for call in controller.calls
    )
