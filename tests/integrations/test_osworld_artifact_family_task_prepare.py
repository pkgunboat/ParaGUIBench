"""13 个 legacy OSWorld artifact-family 任务准备协议的行为测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from typing import Any
import zipfile

import pytest

from paraguibench.integrations.osworld.artifact_evidence_specs import (
    OSWORLD_ARTIFACT_EVIDENCE_SPECS,
)
from paraguibench.integrations.osworld.artifact_family_task_prepare import (
    ARTIFACT_FAMILY_TASK_PREPARE_SCHEMA_ID,
    ARTIFACT_FAMILY_TASK_PREPARE_SPECS,
    ArtifactFamilyAssetBinding,
    ArtifactFamilyPreparedAssets,
    ArtifactFamilyTaskPrepareError,
    ArtifactFamilyTaskPrepareSource,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
GUEST_HOME = "/guest-home"
GUEST_SHARED = f"{GUEST_HOME}/shared"
TASK_INPUT_COUNTS = {
    "Operation-FileOperate-BatchOperation-003": 1,
    "Operation-FileOperate-CombinationDocs-009": 2,
    "Operation-FileOperate-CombinationDocs-010": 1,
    "Operation-FileOperate-CombinationDocs-011": 4,
    "Operation-FileOperate-CombinationDocs-012": 15,
    "Operation-FileOperate-CombinationDocs-013": 19,
    "Operation-FileOperate-CombinationDocs-014": 20,
    "Operation-FileOperate-SearchAndWrite-001": 1,
    "Operation-FileOperate-SearchAndWrite-003": 2,
    "Operation-FileOperate-SearchAndWrite-005": 1,
    "Operation-FileOperate-SearchAndWrite-009": 1,
    "Operation-FileOperate-Settings-001": 2,
    "Operation-WebOperate-SearchAndWrite-001": 2,
}
ACTIONABLE_TASK_IDS = tuple(TASK_INPUT_COUNTS)
RESOLVED_IDLE_DESKTOP_TASK_IDS = (
    "Operation-FileOperate-CombinationDocs-011",
    "Operation-FileOperate-CombinationDocs-012",
    "Operation-FileOperate-CombinationDocs-013",
)
RESOLVED_IDLE_DESKTOP_BINDINGS = {
    "Operation-FileOperate-CombinationDocs-011": (
        ("invoice TII-20220301-90.pdf", "Desktop/invoice TII-20220301-90.pdf"),
        ("Invoice # GES-20220215-82.pdf", "Desktop/Invoice # GES-20220215-82.pdf"),
        ("Invoice # 243729.pdf", "Desktop/Invoice # 243729.pdf"),
        ("Bank-Statement.pdf", "Desktop/Bank-Statement.pdf"),
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        ("Zheng He .docx", "Desktop/students work/Zheng He .docx"),
        (
            "The literature reviews of weekly readings.docx",
            "Desktop/students work/The literature reviews of weekly readings.docx",
        ),
        (
            "The British Justice System.docx",
            "Desktop/students work/The British Justice System.docx",
        ),
        ("quiz2.docx", "Desktop/students work/quiz2.docx"),
        ("quiz.docx", "Desktop/students work/quiz.docx"),
        ("Q1&2&3.docx", "Desktop/students work/Q1&2&3.docx"),
        (
            "Photo Ethics in Journalism.docx",
            "Desktop/students work/Photo Ethics in Journalism.docx",
        ),
        ("cassie.docx", "Desktop/students work/cassie.docx"),
        ("case study.docx", "Desktop/students work/case study.docx"),
        (
            "irregularrules02.pdf",
            "Desktop/Grammar rules PDF/irregularrules02.pdf",
        ),
        (
            "irregularrules01.pdf",
            "Desktop/Grammar rules PDF/irregularrules01.pdf",
        ),
        ("fragrules.pdf", "Desktop/Grammar rules PDF/fragrules.pdf"),
        ("csfsrules.pdf", "Desktop/Grammar rules PDF/csfsrules.pdf"),
        (
            "Public Lecture Teaching Plan.docx",
            "Desktop/Public Lecture Teaching Plan.docx",
        ),
        ("Course Timetable.xlsx", "Desktop/Course Timetable.xlsx"),
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        ("ecs15.pdf", "Documents/Fundings/ecs/ecs15.pdf"),
        ("ecs16.pdf", "Documents/Fundings/ecs/ecs16.pdf"),
        ("ecs17.pdf", "Documents/Fundings/ecs/ecs17.pdf"),
        ("ecs23.pdf", "Documents/Fundings/ecs/ecs23.pdf"),
        ("ecs22.pdf", "Documents/Fundings/ecs/ecs22.pdf"),
        ("ecs21.pdf", "Documents/Fundings/ecs/ecs21.pdf"),
        ("ecs20.pdf", "Documents/Fundings/ecs/ecs20.pdf"),
        ("ecs19.pdf", "Documents/Fundings/ecs/ecs19.pdf"),
        ("ecs18.pdf", "Documents/Fundings/ecs/ecs18.pdf"),
        (
            "customer-information-sheet-for-inward-payments-to-hong-kong.pdf",
            "Documents/Fundings/grf/"
            "customer-information-sheet-for-inward-payments-to-hong-kong.pdf",
        ),
        ("grf15.pdf", "Documents/Fundings/grf/grf15.pdf"),
        ("grf16.pdf", "Documents/Fundings/grf/grf16.pdf"),
        ("grf17.pdf", "Documents/Fundings/grf/grf17.pdf"),
        ("grf18.pdf", "Documents/Fundings/grf/grf18.pdf"),
        ("grf19.pdf", "Documents/Fundings/grf/grf19.pdf"),
        ("grf20.pdf", "Documents/Fundings/grf/grf20.pdf"),
        ("grf21.pdf", "Documents/Fundings/grf/grf21.pdf"),
        ("grf22.pdf", "Documents/Fundings/grf/grf22.pdf"),
        ("grf23.pdf", "Documents/Fundings/grf/grf23.pdf"),
    ),
}
RESOLVED_IDLE_DESKTOP_DIRECTORIES = {
    "Operation-FileOperate-CombinationDocs-011": (),
    "Operation-FileOperate-CombinationDocs-012": (
        "Desktop/students work",
        "Desktop/Lec powerpoint",
        "Desktop/Grammar test",
        "Desktop/Grammar rules PDF",
        "Desktop/FDI",
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        "Documents/Fundings/ecs",
        "Documents/Fundings/grf",
    ),
}
EXPECTED_CONTEXTS_AND_ACTIONS = {
    "Operation-FileOperate-BatchOperation-003": (
        "libreoffice_writer",
        ("libreoffice_writer", "file_manager", "pdf_viewer"),
        (
            "materialize.batch-assets.v1",
            "safe-extract.book-zip.v1",
            "launch.files-book.v1",
            "open.spectral-graph-theory-pdf.v1",
        ),
    ),
    "Operation-FileOperate-CombinationDocs-009": (
        "libreoffice_impress",
        ("libreoffice_impress", "libreoffice_writer"),
        (
            "materialize.verified-assets.v1",
            "open.presentation-with-notes.v1",
        ),
    ),
    "Operation-FileOperate-CombinationDocs-010": (
        "libreoffice_calc",
        ("libreoffice_calc", "libreoffice_writer", "file_manager"),
        (
            "materialize.verified-assets.v1",
            "safe-extract.exam-zip.v1",
            "launch.writer-reference-answers.v1",
            "launch.calc-grades.v1",
            "launch.files-exam.v1",
        ),
    ),
    "Operation-FileOperate-CombinationDocs-011": (
        "libreoffice_calc",
        ("libreoffice_calc", "file_manager", "pdf_viewer"),
        ("materialize.verified-assets.v1",),
    ),
    "Operation-FileOperate-CombinationDocs-012": (
        "libreoffice_calc",
        ("libreoffice_calc",),
        (
            "mkdir.source-directories.v1",
            "materialize.verified-assets.v1",
        ),
    ),
    "Operation-FileOperate-CombinationDocs-013": (
        "libreoffice_calc",
        ("libreoffice_calc", "file_manager"),
        (
            "mkdir.source-directories.v1",
            "materialize.verified-assets.v1",
        ),
    ),
    "Operation-FileOperate-CombinationDocs-014": (
        "libreoffice_calc",
        ("libreoffice_calc", "file_manager"),
        (
            "mkdir.source-directories.v1",
            "materialize.verified-assets.v1",
            "open.supported-rate-workbook.v1",
            "wait.supported-rate-open.5s.v1",
            "open.grf-directory.v1",
            "open.ecs-directory.v1",
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-001": (
        "libreoffice_calc",
        ("libreoffice_calc", "chrome"),
        (
            "launch.chrome-cdp.v1",
            "wait.chrome-cdp.v1",
            "launch.socat-cdp-bridge.v1",
            "materialize.verified-assets.v1",
            "open.professor-contact-workbook.v1",
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-003": (
        "libreoffice_calc",
        ("libreoffice_calc", "chrome", "libreoffice_writer"),
        (
            "materialize.verified-assets.v1",
            "open.book-reading-rate-workbook.v1",
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-005": (
        "libreoffice_calc",
        ("libreoffice_calc", "chrome"),
        (
            "materialize.verified-assets.v1",
            "open.acl-awards-workbook.v1",
            "launch.chrome-cdp.v1",
            "wait.chrome-cdp.v1",
            "launch.socat-cdp-bridge.v1",
            "open.acl-anthology-tab.v1",
        ),
    ),
    "Operation-FileOperate-SearchAndWrite-009": (
        "chrome",
        ("chrome", "libreoffice_calc"),
        (
            "launch.chrome-cdp.v1",
            "wait.chrome-cdp.v1",
            "launch.socat-cdp-bridge.v1",
            "open.imdb-tab.v1",
            "materialize.verified-assets.v1",
            "launch.calc-movies.v1",
        ),
    ),
    "Operation-FileOperate-Settings-001": (
        "vlc",
        ("vlc", "libreoffice_impress"),
        (
            "materialize.verified-assets.v1",
            "open.robotic-workshop-presentation.v1",
            "wait.presentation-open.3s.v1",
            "launch.vlc-landscape-repeat.v1",
        ),
    ),
    "Operation-WebOperate-SearchAndWrite-001": (
        "libreoffice_calc",
        ("libreoffice_calc", "file_manager", "chrome"),
        (
            "materialize.verified-assets.v1",
            "open.must-visit-workbook.v1",
            "open.restaurants-text.v1",
            "wait.restaurants-open.5s.v1",
            "activate.restaurants-gedit.v1",
        ),
    ),
}


class _Controller:
    """记录 prepare source 发往可信 OSWorld controller 的有序调用。"""

    def __init__(self) -> None:
        """初始化空调用序列。

        输入参数：无。
        输出返回值：无。
        """

        self.calls: list[tuple[Any, ...]] = []

    def execute(self, command: list[str]) -> Any:
        """记录同步 argv 并返回成功结果。

        输入参数：
            command：source 生成的 shell-free argv。
        输出返回值：
            带零 ``returncode`` 的合成结果。
        """

        class _Result:
            """表示一次成功的合成命令结果。"""

            returncode = 0

        self.calls.append(("execute", tuple(command)))
        return _Result()

    def launch(self, command: list[str]) -> None:
        """记录一次图形进程 argv 启动。

        输入参数：
            command：source 生成的 shell-free argv。
        输出返回值：无。
        """

        self.calls.append(("launch", tuple(command)))

    def open_path(self, guest_path: str) -> None:
        """记录一次结构化 guest 路径打开请求。

        输入参数：
            guest_path：由冻结 guest home 派生的绝对路径。
        输出返回值：无。
        """

        self.calls.append(("open_path", guest_path))

    def wait_for_chrome_cdp(self, *, port: int, timeout: float) -> None:
        """记录 Chrome CDP 就绪等待。

        输入参数：
            port：固定 guest-local CDP 端口。
            timeout：固定最大等待秒数。
        输出返回值：无。
        """

        self.calls.append(("wait_for_chrome_cdp", port, timeout))

    def activate_window(self, window_name: str) -> None:
        """记录一次严格窗口激活请求。

        输入参数：
            window_name：版本化旧源固定的窗口标题。
        输出返回值：无。
        """

        self.calls.append(("activate_window", window_name))


class _ArchiveExecutingController(_Controller):
    """在临时目录真实执行物化与冻结 zip helper 的边界替身。"""

    def execute(self, command: list[str]) -> Any:
        """以 shell=False 等价语义执行测试所需三类同步 argv。

        输入参数：
            command：source 生成的 mkdir、cp 或冻结 Python helper argv。
        输出返回值：
            具有真实 ``returncode`` 的结果对象。
        """

        self.calls.append(("execute", tuple(command)))
        if command[:3] == ["mkdir", "-p", "--"]:
            for directory in command[3:]:
                Path(directory).mkdir(parents=True, exist_ok=True)

            class _Success:
                """表示测试替身已成功创建目录。"""

                returncode = 0

            return _Success()
        if command[:4] == [
            "cp",
            "--no-dereference",
            "--remove-destination",
            "--",
        ]:
            shutil.copyfile(command[-2], command[-1])

            class _Success:
                """表示测试替身已成功复制普通文件。"""

                returncode = 0

            return _Success()
        if command[:3] == ["python3", "-I", "-c"]:
            return subprocess.run(
                [sys.executable, *command[1:]],
                check=False,
                capture_output=True,
                text=False,
            )
        raise AssertionError("测试替身收到未声明 argv")


class _SymlinkMaterializeController(_ArchiveExecutingController):
    """模拟 ``cp`` 成功却产生符号链接目标的失信 guest 边界。"""

    def execute(self, command: list[str]) -> Any:
        """让复制步骤返回成功但把目标替换为指向 shared 的符号链接。

        输入参数：
            command：prepare source 生成的同步 argv。
        输出返回值：
            ``cp`` 路径返回零状态；其它命令由真实执行替身处理。
        """

        if command[:4] == [
            "cp",
            "--no-dereference",
            "--remove-destination",
            "--",
        ]:
            source = Path(command[-2])
            destination = Path(command[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.unlink(missing_ok=True)
            destination.symlink_to(source)
            self.calls.append(("execute", tuple(command)))

            class _FalseSuccess:
                """表示 guest 对不安全复制伪报成功。"""

                returncode = 0

            return _FalseSuccess()
        return super().execute(command)


class _CorruptMaterializeController(_ArchiveExecutingController):
    """模拟 ``cp`` 成功但目标普通文件字节被篡改的 guest 边界。"""

    def execute(self, command: list[str]) -> Any:
        """在复制步骤写入不同大小/摘要的普通文件并伪报成功。

        输入参数：
            command：prepare source 生成的同步 argv。
        输出返回值：
            ``cp`` 路径返回零状态；其它命令由真实执行替身处理。
        """

        if command[:4] == [
            "cp",
            "--no-dereference",
            "--remove-destination",
            "--",
        ]:
            destination = Path(command[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"corrupted-private-content")
            self.calls.append(("execute", tuple(command)))

            class _FalseSuccess:
                """表示 guest 对损坏复制伪报成功。"""

                returncode = 0

            return _FalseSuccess()
        return super().execute(command)


class _FailingStepController(_Controller):
    """在指定同步动作注入脱敏边界错误并记录 fail-fast 顺序。"""

    def __init__(self, fail_index: int) -> None:
        """保存应返回非零状态的零基 execute 序号。

        输入参数：
            fail_index：当前 prepare 调用序列中待失败的 execute 序号。
        输出返回值：无。
        """

        super().__init__()
        self._fail_index = fail_index

    def execute(self, command: list[str]) -> Any:
        """记录 argv，并仅在固定序号返回带敏感文本的非零结果。

        输入参数：
            command：prepare source 生成的 shell-free argv。
        输出返回值：
            指定序号返回 ``returncode=7``；其它序号返回零。
        """

        class _Result:
            """保存 guest 边界的合成状态与不得泄露的输出。"""

            returncode = 0
            stdout = ""
            stderr = ""

        result = _Result()
        call_index = len(self.calls)
        self.calls.append(("execute", tuple(command)))
        if call_index == self._fail_index:
            result.returncode = 7
            result.stdout = "private-agent-final-text"
            result.stderr = "/guest-home/private-input.pdf"
        return result


def _canonical_task(task_id: str) -> dict[str, Any]:
    """读取一份仓库 canonical task。

    输入参数：
        task_id：待测试的 13-task canonical ID。
    输出返回值：
        解析后的任务 JSON object。
    """

    value = json.loads(
        (REPO_ROOT / "benchmark" / "tasks" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return value


def _verified_assets(task_id: str) -> ArtifactFamilyPreparedAssets:
    """由不可变 catalog 构造未来严格 manifest 的已验证投影。

    输入参数：
        task_id：待准备任务 ID。
    输出返回值：
        路径闭集与 input draft 身份完全匹配的 verified DTO。
    """

    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
    manifest_sha256 = spec.canonical_asset_manifest_sha256 or "a" * 64
    return ArtifactFamilyPreparedAssets(
        task_id=task_id,
        verification_status="verified",
        input_draft_sha256=spec.input_draft_sha256,
        manifest_sha256=manifest_sha256,
        relative_paths=tuple(
            binding.asset_relative_path for binding in spec.asset_bindings
        ),
    )


@pytest.mark.parametrize("task_id", RESOLVED_IDLE_DESKTOP_TASK_IDS)
def test_resolved_idle_desktop_specs_are_actionable(task_id: str) -> None:
    """验证三项已有来源证据的空闲桌面规格不再静态阻断。

    输入参数：
        task_id：已由固定 xlang config、旧最终 pipeline 与历史首帧共同
            闭合的 CombinationDocs 任务 ID。
    输出返回值：
        无；公开 catalog 必须将其标为可执行且不再携带歧义原因。
    """

    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]

    assert spec.prepare_status == "actionable_when_assets_verified"
    assert spec.blocked_reason_id is None


@pytest.mark.parametrize("task_id", RESOLVED_IDLE_DESKTOP_TASK_IDS)
def test_resolved_idle_desktop_prepare_uses_exact_source_order_without_windows(
    task_id: str,
) -> None:
    """验证三项只按固定 source config 落物化且不产生任何窗口或等待。

    输入参数：
        task_id：已完成 start-context 证据闭环的任务 ID。
    输出返回值：
        无；目录、文件与复制后校验顺序必须逐项精确，snapshot/context
        只保留 provenance/capability，不能编译为 GUI 或 finalizer 动作。
    """

    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
    expected_bindings = RESOLVED_IDLE_DESKTOP_BINDINGS[task_id]
    expected_directories = RESOLVED_IDLE_DESKTOP_DIRECTORIES[task_id]
    expected_operations = (
        ("materialize_assets",)
        if not expected_directories
        else ("create_directories", "materialize_assets")
    )
    assert tuple(action.operation for action in spec.actions) == expected_operations
    assert (
        tuple(
            (binding.asset_relative_path, binding.guest_relative_path)
            for binding in spec.asset_bindings
        )
        == expected_bindings
    )
    assert spec.directory_relative_paths == expected_directories

    controller = _Controller()
    assert ArtifactFamilyTaskPrepareSource().prepare(
        _canonical_task(task_id),
        controller,
        guest_shared_dir=GUEST_SHARED,
        prepared_assets=_verified_assets(task_id),
    )

    call_index = 0
    if expected_directories:
        assert controller.calls[call_index] == (
            "execute",
            (
                "mkdir",
                "-p",
                "--",
                *(f"{GUEST_HOME}/{path}" for path in expected_directories),
            ),
        )
        call_index += 1
    for source_relative, destination_relative in expected_bindings:
        destination = PurePosixPath(GUEST_HOME) / destination_relative
        source = PurePosixPath(GUEST_SHARED) / source_relative
        assert controller.calls[call_index] == (
            "execute",
            ("mkdir", "-p", "--", str(destination.parent)),
        )
        assert controller.calls[call_index + 1] == (
            "execute",
            (
                "cp",
                "--no-dereference",
                "--remove-destination",
                "--",
                str(source),
                str(destination),
            ),
        )
        verification = controller.calls[call_index + 2]
        assert verification[0] == "execute"
        assert verification[1][:3] == ("python3", "-I", "-c")
        assert verification[1][-2:] == (str(source), str(destination))
        call_index += 3

    assert call_index == len(controller.calls)
    serialized_calls = repr(controller.calls)
    for forbidden in (
        "activate_window",
        "launch",
        "open_path",
        "sleep",
        "pyautogui",
        "libreoffice",
    ):
        assert forbidden not in serialized_calls


@pytest.mark.parametrize("task_id", RESOLVED_IDLE_DESKTOP_TASK_IDS)
def test_resolved_idle_desktop_preflight_rejects_drift_before_guest_io(
    task_id: str,
) -> None:
    """验证三项身份、payload、资产与路径漂移均在首次 guest I/O 前拒绝。

    输入参数：
        task_id：已解除静态歧义但仍受严格 preflight 约束的任务 ID。
    输出返回值：
        无；每种不可信输入都返回稳定错误码且 controller 调用保持为空。
    """

    source = ArtifactFamilyTaskPrepareSource()
    canonical = _canonical_task(task_id)
    verified = _verified_assets(task_id)
    cases: list[tuple[dict[str, Any], ArtifactFamilyPreparedAssets, str, str]] = []

    identity_drift = dict(canonical)
    identity_drift["task_uid"] = "00000000-0000-0000-0000-000000000000"
    cases.append((identity_drift, verified, GUEST_SHARED, "IDENTITY_ERROR"))

    payload_injection = dict(canonical)
    payload_injection["command"] = ["libreoffice", "--calc"]
    cases.append((payload_injection, verified, GUEST_SHARED, "PAYLOAD_ERROR"))

    cases.append(
        (
            canonical,
            replace(verified, manifest_sha256="f" * 64),
            GUEST_SHARED,
            "ASSET_ERROR",
        )
    )
    cases.append(
        (
            canonical,
            replace(verified, relative_paths=tuple(reversed(verified.relative_paths))),
            GUEST_SHARED,
            "ASSET_ERROR",
        )
    )
    cases.append(
        (
            canonical,
            verified,
            "/guest-home/../shared",
            "PATH_ERROR",
        )
    )

    for task, prepared_assets, guest_shared_dir, expected_error in cases:
        controller = _Controller()
        with pytest.raises(
            ArtifactFamilyTaskPrepareError,
            match=rf"^ARTIFACT_PREPARE_{expected_error}$",
        ):
            source.prepare(
                task,
                controller,
                guest_shared_dir=guest_shared_dir,
                prepared_assets=prepared_assets,
            )
        assert controller.calls == []


def test_materialize_rejects_symlink_destination_after_successful_copy(
    tmp_path: Path,
) -> None:
    """验证物化后必须复核目标为与 verified shared 相同的普通文件。

    输入参数：
        tmp_path：pytest 提供的隔离 guest home。
    输出返回值：
        无；即使 ``cp`` 返回零，符号链接目标也必须在 Agent 前失败关闭，
        且全过程不得启动、打开、激活窗口或等待。
    """

    task_id = "Operation-FileOperate-CombinationDocs-011"
    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
    guest_home = tmp_path / "guest-home"
    guest_shared = guest_home / "shared"
    guest_shared.mkdir(parents=True)
    for index, binding in enumerate(spec.asset_bindings):
        (guest_shared / binding.asset_relative_path).write_bytes(
            f"verified-{index}".encode("ascii")
        )
    controller = _SymlinkMaterializeController()

    with pytest.raises(
        ArtifactFamilyTaskPrepareError,
        match=r"^ARTIFACT_PREPARE_ACTION_ERROR$",
    ):
        ArtifactFamilyTaskPrepareSource().prepare(
            _canonical_task(task_id),
            controller,
            guest_shared_dir=str(guest_shared),
            prepared_assets=_verified_assets(task_id),
        )

    assert controller.calls
    assert all(call[0] == "execute" for call in controller.calls)


def test_combination_011_materializes_verified_regular_files(
    tmp_path: Path,
) -> None:
    """验证真实 helper 接受四个字节一致、非链接的普通目标文件。

    输入参数：
        tmp_path：pytest 提供的隔离 guest home。
    输出返回值：
        无；四个含空格或井号的来源文件均按固定顺序复制并通过大小/SHA
        校验，且不会触发任何 GUI 或等待操作。
    """

    task_id = "Operation-FileOperate-CombinationDocs-011"
    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
    guest_home = tmp_path / "guest-home"
    guest_shared = guest_home / "shared"
    guest_shared.mkdir(parents=True)
    expected_bytes: dict[str, bytes] = {}
    for index, binding in enumerate(spec.asset_bindings):
        content = f"verified-pdf-{index}".encode("ascii")
        expected_bytes[binding.guest_relative_path] = content
        (guest_shared / binding.asset_relative_path).write_bytes(content)
    controller = _ArchiveExecutingController()

    assert ArtifactFamilyTaskPrepareSource().prepare(
        _canonical_task(task_id),
        controller,
        guest_shared_dir=str(guest_shared),
        prepared_assets=_verified_assets(task_id),
    )

    for relative_path, expected in expected_bytes.items():
        destination = guest_home / relative_path
        assert destination.is_file()
        assert not destination.is_symlink()
        assert destination.read_bytes() == expected
    assert len(controller.calls) == 3 * len(spec.asset_bindings)
    assert all(call[0] == "execute" for call in controller.calls)


def test_materialize_rejects_regular_destination_with_wrong_size_or_digest(
    tmp_path: Path,
) -> None:
    """验证普通目标的大小或 SHA-256 与 verified shared 不同也失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离 guest home。
    输出返回值：
        无；损坏普通文件不得被零 ``cp`` 状态掩盖，公开异常不得包含路径、
        文件内容、stdout/stderr 或 Agent final text。
    """

    task_id = "Operation-FileOperate-CombinationDocs-011"
    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
    guest_home = tmp_path / "guest-home"
    guest_shared = guest_home / "shared"
    guest_shared.mkdir(parents=True)
    for index, binding in enumerate(spec.asset_bindings):
        (guest_shared / binding.asset_relative_path).write_bytes(
            f"verified-source-{index}".encode("ascii")
        )
    controller = _CorruptMaterializeController()

    with pytest.raises(ArtifactFamilyTaskPrepareError) as raised:
        ArtifactFamilyTaskPrepareSource().prepare(
            _canonical_task(task_id),
            controller,
            guest_shared_dir=str(guest_shared),
            prepared_assets=_verified_assets(task_id),
        )

    public_error = str(raised.value)
    assert public_error == "ARTIFACT_PREPARE_ACTION_ERROR"
    for private_value in (
        str(guest_home),
        "private-input.pdf",
        "corrupted-private-content",
        "private-agent-final-text",
    ):
        assert private_value not in public_error


@pytest.mark.parametrize("task_id", RESOLVED_IDLE_DESKTOP_TASK_IDS)
def test_resolved_idle_desktop_prepare_fails_fast_at_every_sync_step(
    task_id: str,
) -> None:
    """验证三项任一 mkdir/cp/校验失败都立即中止且不泄露边界文本。

    输入参数：
        task_id：已解除 start-context blocker 的任务 ID。
    输出返回值：
        无；逐个同步步骤注入非零状态，异常固定、后续动作零调用，且不会
        进入窗口、等待、finalizer 或 Agent 路径。
    """

    expected_call_count = 3 * len(RESOLVED_IDLE_DESKTOP_BINDINGS[task_id]) + bool(
        RESOLVED_IDLE_DESKTOP_DIRECTORIES[task_id]
    )
    for fail_index in range(expected_call_count):
        controller = _FailingStepController(fail_index)
        with pytest.raises(ArtifactFamilyTaskPrepareError) as raised:
            ArtifactFamilyTaskPrepareSource().prepare(
                _canonical_task(task_id),
                controller,
                guest_shared_dir=GUEST_SHARED,
                prepared_assets=_verified_assets(task_id),
            )

        assert str(raised.value) == "ARTIFACT_PREPARE_ACTION_ERROR"
        assert len(controller.calls) == fail_index + 1
        public_error = str(raised.value)
        assert "private-agent-final-text" not in public_error
        assert "/guest-home/private-input.pdf" not in public_error


def test_batch_operation_prepare_uses_verified_assets_and_safe_order() -> None:
    """验证首个 tracer 从 verified shared 闭集安全重放旧准备顺序。

    输入参数：
        无；使用 BatchOperation-003 canonical task 和未来已验证资产投影。
    输出返回值：
        无；断言先物化固定 zip，再用冻结 Python argv 安全解包，最后依次
        打开 Files 目录与目标 PDF，全程不使用 shell。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    controller = _Controller()

    prepared = ArtifactFamilyTaskPrepareSource().prepare(
        _canonical_task(task_id),
        controller,
        guest_shared_dir=GUEST_SHARED,
        prepared_assets=_verified_assets(task_id),
    )

    assert prepared is True
    assert controller.calls[:2] == [
        ("execute", ("mkdir", "-p", "--", f"{GUEST_HOME}/Desktop")),
        (
            "execute",
            (
                "cp",
                "--no-dereference",
                "--remove-destination",
                "--",
                f"{GUEST_SHARED}/raw_book.zip",
                f"{GUEST_HOME}/Desktop/book.zip",
            ),
        ),
    ]
    verify_call = controller.calls[2]
    assert verify_call[0] == "execute"
    assert verify_call[1][:3] == ("python3", "-I", "-c")
    assert verify_call[1][-2:] == (
        f"{GUEST_SHARED}/raw_book.zip",
        f"{GUEST_HOME}/Desktop/book.zip",
    )
    extract_call = controller.calls[3]
    assert extract_call[0] == "execute"
    assert extract_call[1][:3] == ("python3", "-I", "-c")
    assert extract_call[1][-2:] == (
        f"{GUEST_HOME}/Desktop/book.zip",
        f"{GUEST_HOME}/Desktop",
    )
    assert "zipfile" in extract_call[1][3]
    assert controller.calls[4:] == [
        ("launch", ("nautilus", f"{GUEST_HOME}/Desktop/book")),
        (
            "open_path",
            f"{GUEST_HOME}/Desktop/book/Spectral Graph Theory.pdf",
        ),
    ]
    assert all(
        "sh" not in call[1][:1]
        for call in controller.calls
        if call[0] in {"execute", "launch"}
    )


def test_batch_prepare_rejects_mixed_legacy_and_strict_asset_modes_before_io() -> None:
    """验证执行层也拒绝 legacy URL 与 strict manifest 混合状态。

    输入参数：
        无；向当前 BatchOperation-003 strict canonical 叠加旧 URL 字段。
    输出返回值：
        无；source 必须在首次 controller I/O 前以身份错误失败关闭。
    """

    task_id = "Operation-FileOperate-BatchOperation-003"
    task = _canonical_task(task_id)
    task["prepare_script_path"] = (
        "https://huggingface.co/datasets/xlangai/"
        "ubuntu_osworld_file_cache/resolve/main/"
        "multi_apps/5df7b33a-9f77-4101-823e-02f863e1c1ae/raw_book.zip"
    )
    controller = _Controller()

    with pytest.raises(
        ArtifactFamilyTaskPrepareError,
        match=r"^ARTIFACT_PREPARE_IDENTITY_ERROR$",
    ):
        ArtifactFamilyTaskPrepareSource().prepare(
            task,
            controller,
            guest_shared_dir=GUEST_SHARED,
            prepared_assets=_verified_assets(task_id),
        )

    assert controller.calls == []


def test_prepare_spec_rejects_empty_or_mixed_canonical_asset_modes() -> None:
    """验证 catalog 不能表达双来源或无来源 canonical 资产状态。

    输入参数：
        无；从冻结 BatchOperation-003 spec 构造两个非法替换值。
    输出返回值：
        无；legacy URL 摘要与 strict manifest 身份必须严格二选一。
    """

    spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[
        "Operation-FileOperate-BatchOperation-003"
    ]

    with pytest.raises(ValueError, match="canonical asset mode"):
        replace(
            spec,
            canonical_prepare_reference_sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="canonical asset mode"):
        replace(
            spec,
            canonical_asset_manifest_relative_path=None,
            canonical_asset_manifest_sha256=None,
        )


def test_catalog_closes_all_thirteen_tasks_against_drafts_and_evidence() -> None:
    """验证 13-task catalog 精确绑定 canonical、71 input 与 evidence spec。

    输入参数：
        无；读取公开 catalog、canonical task、input draft 和 evidence catalog。
    输出返回值：
        无；断言任务/资产闭集、路径映射、来源摘要、finalize 合同与全部
        actionable 规格均不能漂移。
    """

    assert set(ARTIFACT_FAMILY_TASK_PREPARE_SPECS) == set(TASK_INPUT_COUNTS)
    observed_input_count = 0
    observed_blocked: set[str] = set()
    for task_id, expected_input_count in TASK_INPUT_COUNTS.items():
        spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[task_id]
        canonical = _canonical_task(task_id)
        draft_path = REPO_ROOT / spec.input_draft_relative_path
        draft_bytes = draft_path.read_bytes()
        draft = json.loads(draft_bytes)
        evidence = OSWORLD_ARTIFACT_EVIDENCE_SPECS[task_id]

        assert spec.schema_id == ARTIFACT_FAMILY_TASK_PREPARE_SCHEMA_ID
        assert spec.task_uid == canonical["task_uid"]
        assert spec.task_source == canonical["task_source"]
        assert spec.task_type == canonical["task_type"]
        assert spec.task_tag == canonical["task_tag"]
        assert spec.evaluator_path == canonical["evaluator_path"]
        if spec.canonical_asset_mode == "strict_asset_manifest":
            manifest_reference = canonical["asset_manifest"]
            assert "prepare_script_path" not in canonical
            assert spec.canonical_prepare_reference_sha256 is None
            assert spec.canonical_asset_manifest_relative_path == manifest_reference
            assert (
                spec.canonical_asset_manifest_sha256
                == hashlib.sha256(
                    (REPO_ROOT / manifest_reference).read_bytes()
                ).hexdigest()
            )
        else:
            assert spec.canonical_asset_mode == "legacy_prepare_reference"
            assert (
                spec.canonical_prepare_reference_sha256
                == hashlib.sha256(
                    canonical["prepare_script_path"].encode("utf-8")
                ).hexdigest()
            )
            assert spec.canonical_asset_manifest_relative_path is None
            assert spec.canonical_asset_manifest_sha256 is None
        assert spec.input_draft_sha256 == hashlib.sha256(draft_bytes).hexdigest()
        assert spec.source_task_id == evidence.source_task_id
        assert spec.source_evaluator_id == evidence.source_evaluator_id
        assert spec.source_contract_sha256 == evidence.source_contract_sha256
        assert spec.evidence_spec_sha256 == evidence.evidence_spec_sha256
        assert spec.finalize_action_id == evidence.finalize_action_id
        assert spec.finalize_options_json == evidence.finalize_options_json
        expected_snapshot, expected_contexts, expected_actions = (
            EXPECTED_CONTEXTS_AND_ACTIONS[task_id]
        )
        assert spec.source_snapshot_id == expected_snapshot
        assert spec.required_context_ids == expected_contexts
        assert tuple(action.action_id for action in spec.actions) == (expected_actions)

        observed_input_count += len(spec.asset_bindings)
        assert len(spec.asset_bindings) == expected_input_count
        expected_bindings = {
            (
                Path(entry["remote_relative_path"]).name,
                entry["guest_relative_path"],
                entry["purpose"],
            )
            for entry in draft["entries"]
        }
        assert {
            (
                binding.asset_relative_path,
                binding.guest_relative_path,
                binding.purpose,
            )
            for binding in spec.asset_bindings
        } == expected_bindings
        if spec.prepare_status == "blocked":
            observed_blocked.add(task_id)
            assert spec.blocked_reason_id == ("blocked.source_start_context_ambiguous")
        else:
            assert spec.prepare_status == "actionable_when_assets_verified"
            assert spec.blocked_reason_id is None

    assert observed_input_count == 71
    assert observed_blocked == set()
    with pytest.raises(TypeError):
        ARTIFACT_FAMILY_TASK_PREPARE_SPECS["synthetic"] = spec  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        spec.prepare_status = "mutable"  # type: ignore[misc]


def test_search_005_prepare_preserves_workbook_then_browser_order() -> None:
    """验证含 Calc+Chrome 上下文的旧源顺序被完整重放。

    输入参数：
        无；使用 SearchAndWrite-005 的 verified asset 投影。
    输出返回值：
        无；工作簿必须先打开，随后 Chrome、CDP 门禁、socat 和 ACL
        Anthology 页签依次建立。
    """

    task_id = "Operation-FileOperate-SearchAndWrite-005"
    controller = _Controller()

    prepared = ArtifactFamilyTaskPrepareSource().prepare(
        _canonical_task(task_id),
        controller,
        guest_shared_dir=GUEST_SHARED,
        prepared_assets=_verified_assets(task_id),
    )

    assert prepared is True
    assert controller.calls[:2] == [
        ("execute", ("mkdir", "-p", "--", f"{GUEST_HOME}/Desktop")),
        (
            "execute",
            (
                "cp",
                "--no-dereference",
                "--remove-destination",
                "--",
                f"{GUEST_SHARED}/best_awards_acl.xlsx",
                f"{GUEST_HOME}/Desktop/best_awards_acl.xlsx",
            ),
        ),
    ]
    verify_call = controller.calls[2]
    assert verify_call[0] == "execute"
    assert verify_call[1][:3] == ("python3", "-I", "-c")
    assert verify_call[1][-2:] == (
        f"{GUEST_SHARED}/best_awards_acl.xlsx",
        f"{GUEST_HOME}/Desktop/best_awards_acl.xlsx",
    )
    assert controller.calls[3:] == [
        ("open_path", f"{GUEST_HOME}/Desktop/best_awards_acl.xlsx"),
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
        (
            "launch",
            (
                "google-chrome",
                "--new-tab",
                "https://aclanthology.org/",
            ),
        ),
    ]


@pytest.mark.parametrize("task_id", ACTIONABLE_TASK_IDS)
def test_all_thirteen_actionable_specs_compile_to_shell_free_controller_calls(
    task_id: str,
) -> None:
    """验证十三项可重放规格均能编译为冻结 home/shared 下的 controller 调用。

    输入参数：
        task_id：十三项 ``actionable_when_assets_verified`` 任务之一。
    输出返回值：
        无；prepare 成功，所有 guest 绝对路径均源自同一冻结 home，且没有
        把旧 ``bash -c`` 或任务 payload 变成命令。
    """

    controller = _Controller()

    assert ArtifactFamilyTaskPrepareSource().prepare(
        _canonical_task(task_id),
        controller,
        guest_shared_dir=GUEST_SHARED,
        prepared_assets=_verified_assets(task_id),
    )
    assert controller.calls
    for call in controller.calls:
        for value in call[1:]:
            values = value if isinstance(value, tuple) else (value,)
            for item in values:
                if isinstance(item, str) and item.startswith("/"):
                    assert item == GUEST_HOME or item.startswith(f"{GUEST_HOME}/")
        if call[0] in {"execute", "launch"}:
            argv = call[1]
            assert argv[:2] not in {
                ("bash", "-c"),
                ("sh", "-c"),
                ("zsh", "-c"),
                ("/bin/bash", "-c"),
            }


def test_preflight_rejects_unverified_drift_and_escape_before_io() -> None:
    """验证资产、身份、路径和能力错误都在首次 I/O 前关闭。

    输入参数：
        无；分别构造 unverified、缺资产、身份漂移、路径逃逸和 controller
        能力缺失输入。
    输出返回值：
        无；每类错误均返回固定错误码且对应 controller 调用序列保持为空。
    """

    source = ArtifactFamilyTaskPrepareSource()
    batch_id = "Operation-FileOperate-BatchOperation-003"
    batch_spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[batch_id]
    unverified = ArtifactFamilyPreparedAssets(
        task_id=batch_id,
        verification_status="unverified",
        input_draft_sha256=batch_spec.input_draft_sha256,
        manifest_sha256=None,
        relative_paths=("raw_book.zip",),
    )
    controller = _Controller()
    with pytest.raises(ArtifactFamilyTaskPrepareError, match="ASSET_ERROR"):
        source.prepare(
            _canonical_task(batch_id),
            controller,
            guest_shared_dir=GUEST_SHARED,
            prepared_assets=unverified,
        )
    assert controller.calls == []

    with pytest.raises(ValueError, match="relative path"):
        ArtifactFamilyAssetBinding(
            asset_relative_path="../escape.bin",
            guest_relative_path="Desktop/safe.bin",
            purpose="reference_input",
        )
    multi_id = "Operation-FileOperate-CombinationDocs-009"
    multi_spec = ARTIFACT_FAMILY_TASK_PREPARE_SPECS[multi_id]
    missing_asset = ArtifactFamilyPreparedAssets(
        task_id=multi_id,
        verification_status="verified",
        input_draft_sha256=multi_spec.input_draft_sha256,
        manifest_sha256="b" * 64,
        relative_paths=(multi_spec.asset_bindings[0].asset_relative_path,),
    )
    controller = _Controller()
    with pytest.raises(ArtifactFamilyTaskPrepareError, match="ASSET_ERROR"):
        source.prepare(
            _canonical_task(multi_id),
            controller,
            guest_shared_dir=GUEST_SHARED,
            prepared_assets=missing_asset,
        )
    assert controller.calls == []

    drifted_task = _canonical_task(batch_id)
    drifted_task["task_uid"] = "00000000-0000-0000-0000-000000000000"
    controller = _Controller()
    with pytest.raises(ArtifactFamilyTaskPrepareError, match="IDENTITY_ERROR"):
        source.prepare(
            drifted_task,
            controller,
            guest_shared_dir=GUEST_SHARED,
            prepared_assets=_verified_assets(batch_id),
        )
    assert controller.calls == []

    controller = _Controller()
    with pytest.raises(ArtifactFamilyTaskPrepareError, match="PATH_ERROR"):
        source.prepare(
            _canonical_task(batch_id),
            controller,
            guest_shared_dir="/guest-profile/../shared",
            prepared_assets=_verified_assets(batch_id),
        )
    assert controller.calls == []

    web_id = "Operation-WebOperate-SearchAndWrite-001"
    controller = _Controller()
    controller.activate_window = None  # type: ignore[method-assign]
    with pytest.raises(ArtifactFamilyTaskPrepareError, match="CONTROLLER_ERROR"):
        source.prepare(
            _canonical_task(web_id),
            controller,
            guest_shared_dir=GUEST_SHARED,
            prepared_assets=_verified_assets(web_id),
        )
    assert controller.calls == []


def test_safe_zip_helper_rejects_parent_traversal_before_opening_apps(
    tmp_path: Path,
) -> None:
    """验证旧 ``unzip`` 已替换为真实拒绝父目录逃逸的冻结 helper。

    输入参数：
        tmp_path：pytest 提供的隔离 guest-home 根。
    输出返回值：
        无；恶意成员使 prepare 失败，不产生逃逸文件，也不启动 Files/PDF。
    """

    guest_home = tmp_path / "guest-home"
    guest_shared = guest_home / "shared"
    guest_shared.mkdir(parents=True)
    archive_path = guest_shared / "raw_book.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("../escape.pdf", b"%PDF-1.4\n")
    task_id = "Operation-FileOperate-BatchOperation-003"
    controller = _ArchiveExecutingController()

    with pytest.raises(
        ArtifactFamilyTaskPrepareError,
        match="ACTION_ERROR",
    ):
        ArtifactFamilyTaskPrepareSource().prepare(
            _canonical_task(task_id),
            controller,
            guest_shared_dir=str(guest_shared),
            prepared_assets=_verified_assets(task_id),
        )

    assert not (guest_home / "escape.pdf").exists()
    assert all(call[0] == "execute" for call in controller.calls)
