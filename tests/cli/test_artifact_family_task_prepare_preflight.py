"""artifact-family task prepare 的 CLI pre-Docker 门禁测试。"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from paraguibench.cli.main import main
from paraguibench.runtime.doctor import inspect_osworld_prerequisites


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "task_id",
    [
        "Operation-FileOperate-BatchOperation-003",
        "Operation-FileOperate-CombinationDocs-011",
    ],
)
@pytest.mark.parametrize("command", ["doctor", "run"])
def test_strict_artifact_family_preflight_reaches_isolated_cache_gate(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
    task_id: str,
    command: str,
) -> None:
    """验证 strict artifact-family capability 后仍在缓存缺失时失败关闭。

    输入参数：
        tmp_path：pytest 提供的隔离资产、gold、镜像和 RunStore 根。
        capsys：标准输出和错误输出捕获 fixture。
        monkeypatch：注入无网络、无 Docker 的 doctor probe，并将
            Agent、Docker session 与 RunStore 替换为不可达哨兵。
        task_id：覆盖既有 tracer 与新解除 start-context blocker 的任务。
        command：分别覆盖 doctor 和 run 两个 live 入口。
    输出返回值：
        无；断言 canonical strict manifest 已通过 capability 门禁，
        input 与 evaluator-only gold 缓存缺失则由 doctor 稳定拒绝，
        且不创建 Agent、Docker session 或 RunStore。
    """

    downstream_calls: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        """记录错误越过本地部署门禁的任一下游调用。

        输入参数：
            _args/_kwargs：被替换入口的未使用参数。
        输出返回值：
            永不正常返回；调用即使测试失败。
        """

        downstream_calls.append("called")
        raise AssertionError("asset/gold cache gate 后不得进入运行链")

    def isolated_inspection(config: object) -> object:
        """在不探测真实 Docker、KVM、端口或环境变量时执行 doctor。

        输入参数：
            config：CLI 从 strict task、input 和 gold manifest 构造的
                ``OSWorldDoctorConfig``。
        输出返回值：
            真实 doctor 生成的固定检查报告；只读隔离临时路径。
        """

        def unavailable_command(
            arguments: list[str],
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            """以固定失败结果代替 Docker 子进程。

            输入参数：
                arguments：doctor 构造的 argv；不执行。
                _kwargs：``subprocess.run`` 兼容关键字参数。
            输出返回值：
                returncode=1 且无文本的脱敏完成记录。
            """

            return subprocess.CompletedProcess(arguments, 1, "", "")

        return inspect_osworld_prerequisites(
            config,  # type: ignore[arg-type]
            command_runner=unavailable_command,
            environment={},
            python_version=(3, 11),
            kvm_probe=lambda: False,
            port_probe=lambda _port: True,
            dependency_probe=lambda _name: True,
        )

    monkeypatch.setattr(
        "paraguibench.cli.main.inspect_osworld_prerequisites",
        isolated_inspection,
    )
    for target in ("_build_agent", "OSWorldDockerSession", "RunStore"):
        monkeypatch.setattr(f"paraguibench.cli.main.{target}", forbidden)

    runs_root = tmp_path / "runs-must-not-exist"
    asset_cache_root = tmp_path / "assets-must-not-exist"
    gold_cache_root = tmp_path / "gold-must-not-exist"
    arguments = [
        command,
        "--repo-root",
        str(REPO_ROOT),
        "--task-id",
        task_id,
        "--asset-cache-root",
        str(asset_cache_root),
        "--gold-cache-root",
        str(gold_cache_root),
        "--qcow2-path",
        str(tmp_path / "unused.qcow2"),
        "--server-port",
        "5000",
        "--vnc-port",
        "5900",
        "--chromium-port",
        "9222",
    ]
    if command == "run":
        arguments.extend(
            [
                "--runs-root",
                str(runs_root),
                "--model",
                "must-not-be-used",
            ]
        )

    exit_code = main(arguments)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.err == ""
    assert "FAIL asset_cache\n" in captured.out
    assert "FAIL gold_cache\n" in captured.out
    assert "doctor=FAIL\n" in captured.out
    assert downstream_calls == []
    assert not runs_root.exists()
    assert not asset_cache_root.exists()
    assert not gold_cache_root.exists()
