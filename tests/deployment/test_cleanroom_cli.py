"""ParaGUIBench cleanroom CLI 无 bytecode 启动边界测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPOSITORY_ROOT / "scripts" / "deployment" / "run_cleanroom_cli.py"
DEPLOYMENT_GUIDE = REPOSITORY_ROOT / "docs" / "deployment" / "osworld-linux.md"
BUNDLE_SCRIPT = REPOSITORY_ROOT / "scripts" / "deployment" / "release_bundle.py"
TASK_ID = "Operation-FileOperate-BatchOperationPPT-003"


def _copy_fresh_source_tree(tmp_path: Path) -> Path:
    """复制不含 Python 缓存的最小 cleanroom 源码树。

    输入参数：``tmp_path`` 为 pytest 提供的私有临时目录。
    输出返回值：仅含 production package 与部署 launcher 的 fresh root；
        复制前 launcher 必须作为正式仓库文件存在。
    """

    release_root = tmp_path / "cleanroom"
    shutil.copytree(
        REPOSITORY_ROOT / "src" / "paraguibench",
        release_root / "src" / "paraguibench",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    launcher_target = release_root / "scripts" / "deployment" / LAUNCHER.name
    launcher_target.parent.mkdir(parents=True)
    shutil.copy2(LAUNCHER, launcher_target)
    return release_root


def _candidate_arguments(release_root: Path, tmp_path: Path) -> list[str]:
    """构造会进入正式 candidate handler、但在 VM 前失败的完整参数。

    输入参数：``release_root`` 为缺少 benchmark manifests 的 fresh source；
        ``tmp_path`` 提供全部 repo 外状态路径。
    输出返回值：不含模型、凭据、Agent 或 secret 的 PPT-003 candidate argv。
    """

    qcow2_path = tmp_path / "Ubuntu.qcow2"
    qcow2_path.write_bytes(b"synthetic-not-a-vm")
    return [
        "pipeline-implicit",
        "component-validate",
        "--repo-root",
        str(release_root),
        "--task-id",
        TASK_ID,
        "--asset-cache-root",
        str(tmp_path / "assets"),
        "--gold-cache-root",
        str(tmp_path / "gold"),
        "--runs-root",
        str(tmp_path / "runs"),
        "--run-id",
        "run-cleanroom-no-bytecode-test",
        "--attempt-id",
        "attempt-001",
        "--qcow2-path",
        str(qcow2_path),
        "--server-port",
        "55131",
        "--vnc-port",
        "58131",
        "--chromium-port",
        "59231",
    ]


def _run_cleanroom_candidate(
    release_root: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    """经部署 bootstrap 运行真实 cleanroom CLI 子进程。

    输入参数：``release_root`` 为 fresh source；``arguments`` 为候选命令参数。
    输出返回值：捕获 stdout/stderr 的已完成子进程；环境只保留 locale、PATH
        与指向 fresh source 的 ``PYTHONPATH``，调用方不预设 bytecode 开关。
    """

    return _run_cleanroom_cli(release_root, arguments)


def _run_cleanroom_cli(
    release_root: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    """经部署 bootstrap 运行任意正式 cleanroom CLI 子进程。

    输入参数：``release_root`` 为 fresh source；``arguments`` 为正式
        CLI 的公开参数序列。
    输出返回值：捕获 stdout/stderr 的已完成子进程；环境只保留 locale、PATH
        与指向 fresh source 的 ``PYTHONPATH``，调用方不预设 bytecode 开关。
    """

    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(release_root / "src"),
    }
    return subprocess.run(
        [
            sys.executable,
            str(release_root / "scripts" / "deployment" / LAUNCHER.name),
            *arguments,
        ],
        cwd=release_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _python_cache_paths(release_root: Path) -> list[Path]:
    """列出 fresh source 中所有 Python bytecode 或缓存目录。

    输入参数：``release_root`` 为 candidate 子进程使用的 cleanroom 根。
    输出返回值：按相对路径排序的 ``*.pyc`` 与 ``__pycache__`` 节点列表。
    """

    return sorted(
        (
            path
            for path in release_root.rglob("*")
            if path.name == "__pycache__" or path.suffix == ".pyc"
        ),
        key=lambda path: path.relative_to(release_root).as_posix(),
    )


def test_cleanroom_candidate_pre_vm_failure_writes_no_python_cache(
    tmp_path: Path,
) -> None:
    """真实 candidate 的首次 imports 与 pre-VM 失败不得改写 source tree。

    输入参数：``tmp_path`` 隔离 fresh source 与全部外部状态。
    输出返回值：无；断言 handler 已执行并脱敏失败，同时 source 中保持
        0 个 ``__pycache__`` / ``*.pyc``，而非只检查环境变量字面量。
    """

    release_root = _copy_fresh_source_tree(tmp_path)
    assert _python_cache_paths(release_root) == []

    completed = _run_cleanroom_candidate(
        release_root,
        _candidate_arguments(release_root, tmp_path),
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("error=")
    assert _python_cache_paths(release_root) == []


def test_cleanroom_candidate_preserves_argument_privacy_without_python_cache(
    tmp_path: Path,
) -> None:
    """部署 bootstrap 不得削弱正式 parser 的未知参数脱敏边界。

    输入参数：``tmp_path`` 隔离 fresh source 与 synthetic 路径。
    输出返回值：无；断言未知 secret-like 选项在 handler 前固定失败，
        sentinel 不进入输出，且失败路径也不生成 Python 缓存。
    """

    release_root = _copy_fresh_source_tree(tmp_path)
    sentinel = "SYNTHETIC_PRIVATE_VALUE_NOT_A_SECRET"

    completed = _run_cleanroom_candidate(
        release_root,
        [
            *_candidate_arguments(release_root, tmp_path),
            "--api-key",
            sentinel,
        ],
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.endswith("error=ArgumentParseError\n")
    assert sentinel not in completed.stderr
    assert _python_cache_paths(release_root) == []


def test_deployment_guide_uses_formal_no_bytecode_candidate_entry() -> None:
    """冻结部署文档中的 candidate 与辅助 Python 无写入协议。

    输入参数：无；读取公开 OSWorld cleanroom 部署指南。
    输出返回值：无；正式 candidate 必须经 bootstrap，且文档明确禁止
        在冻结 source 上直接执行会早于 package 开关写缓存的 ``python -m``。
    """

    guide = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")

    assert "scripts/deployment/run_cleanroom_cli.py" in guide
    assert "python -B" in guide
    assert "python -m paraguibench.cli.main pipeline-implicit" not in guide
    assert "不得在冻结源码树上直接运行" in guide


def test_all_documented_cleanroom_cli_commands_use_bootstrap_without_cache(
    tmp_path: Path,
) -> None:
    """部署指南的全部 production CLI 都必须走同一个无写入入口。

    输入参数：``tmp_path`` 隔离 fresh source。
    输出返回值：无；先从文档命令行核对 assets/gold/doctor/model-probe/
        candidate/run/inspect 的统一 wrapper，再逐项执行真实 parser help，
        断言连续 imports 后仍无 Python 缓存。
    """

    guide = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")
    command_lines = tuple(
        line.strip()
        for line in guide.splitlines()
        if re.match(
            r"^(?:paraguibench|paraguibench_cleanroom)\s+"
            r"(?:assets|gold|doctor|model-probe|pipeline-implicit|run|inspect)\b",
            line.strip(),
        )
    )
    assert command_lines
    assert all(line.startswith("paraguibench_cleanroom ") for line in command_lines)
    assert {line.split()[1] for line in command_lines} == {
        "assets",
        "doctor",
        "gold",
        "inspect",
        "model-probe",
        "pipeline-implicit",
        "run",
    }

    release_root = _copy_fresh_source_tree(tmp_path)
    documented_command_prefixes = (
        ["assets", "fetch"],
        ["assets", "verify"],
        ["gold", "fetch"],
        ["gold", "verify"],
        ["doctor"],
        ["model-probe", "qwen-native"],
        ["pipeline-implicit", "component-validate"],
        ["run"],
        ["inspect"],
    )
    for command_prefix in documented_command_prefixes:
        completed = _run_cleanroom_cli(
            release_root,
            [*command_prefix, "--help"],
        )
        assert completed.returncode == 0, completed.stderr
        assert _python_cache_paths(release_root) == []


def test_candidate_example_follows_materialization_cache_and_port_setup() -> None:
    """candidate 示例必须引用已经建立的正式部署能力。

    输入参数：无；读取公开 OSWorld cleanroom 部署指南。
    输出返回值：无；断言外部缓存根、formal qcow2 物化结果和三个端口均先于
        candidate 命令定义，且 candidate 只引用已物化的 qcow2 capability。
    """

    guide = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")
    candidate_index = guide.index(
        "paraguibench_cleanroom pipeline-implicit component-validate"
    )

    for prerequisite in (
        "export PARAGUIBENCH_ASSET_CACHE_ROOT=",
        "export PARAGUIBENCH_GOLD_CACHE_ROOT=",
        "export PARAGUIBENCH_RUNS_ROOT=",
        "export PARAGUIBENCH_QCOW2_PATH=",
        "export PARAGUIBENCH_SERVER_PORT=",
        "export PARAGUIBENCH_VNC_PORT=",
        "export PARAGUIBENCH_CHROMIUM_PORT=",
    ):
        assert guide.index(prerequisite) < candidate_index
    assert '--qcow2-path "$PARAGUIBENCH_QCOW2_PATH"' in guide[candidate_index:]
    assert '--qcow2-path "$PARAGUIBENCH_VM_ROOT/Ubuntu.qcow2"' not in guide


def test_materializer_uses_the_created_external_virtual_environment() -> None:
    """正式物化命令必须使用流程实际创建的外部 Python 环境。

    输入参数：无；读取公开 OSWorld cleanroom 部署指南。
    输出返回值：无；断言 materializer 以 ``$VENV_ROOT`` 解释器和解释器级
        ``-B`` 启动，不引用文档从未创建的 checkout-local 环境。
    """

    guide = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")
    materializer = (
        'PYTHONDONTWRITEBYTECODE=1 "$VENV_ROOT/bin/python" -B -m \\\n'
        "  paraguibench.cli.osworld_qcow2_materializer"
    )

    assert materializer in guide
    assert ".venv-live/bin/python" not in guide


def test_release_bundle_includes_formal_cleanroom_launcher(tmp_path: Path) -> None:
    """真实 release build 必须把正式无 bytecode launcher 纳入源码闭集。

    输入参数：``tmp_path`` 提供仓库外 bundle 输出目录。
    输出返回值：无；通过公开 build CLI 构建当前工作树，并断言外置清单
        精确包含部署 bootstrap，而不是依赖远端临时脚本。
    """

    output_root = tmp_path / "bundle"
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(BUNDLE_SCRIPT),
            "build",
            "--repo-root",
            str(REPOSITORY_ROOT),
            "--output-dir",
            str(output_root),
            "--name",
            "cleanroom-launcher-test",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stdout
    manifest = json.loads(
        (output_root / "cleanroom-launcher-test.manifest.json").read_text(
            encoding="utf-8"
        )
    )
    paths = {entry["path"] for entry in manifest["files"]}
    assert "scripts/deployment/run_cleanroom_cli.py" in paths
