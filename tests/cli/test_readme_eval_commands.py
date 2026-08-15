"""确认 README 最短评测命令可以被当前 CLI 解析。"""

from __future__ import annotations

from pathlib import Path
import re
import shlex

import pytest

from paraguibench.cli.main import build_parser


REPO_ROOT = Path(__file__).resolve().parents[2]
_HEADING_TO_README = {
    "## Shortest evaluation path": REPO_ROOT / "README.md",
    "## 最短评测路径": REPO_ROOT / "README_zh-CN.md",
}
_DUMMY_ENV = {
    "HOME": "/tmp",
    "PARAGUIBENCH_MODEL_API_KEY": "unused",
    "PARAGUIBENCH_MODEL_BASE_URL": "https://example.invalid/v1",
    "PARAGUIBENCH_MODEL_ID": "qwen3.7-flash-2026-07-15",
    "PARAGUIBENCH_ASSET_CACHE_ROOT": "/tmp/paraguibench-assets",
    "PARAGUIBENCH_GOLD_CACHE_ROOT": "/tmp/paraguibench-gold",
    "PARAGUIBENCH_QCOW2_PATH": "/tmp/Ubuntu.qcow2",
    "PARAGUIBENCH_RUNS_ROOT": "/tmp/paraguibench-runs",
    "PARAGUIBENCH_SERVER_PORT": "5527",
    "PARAGUIBENCH_VNC_PORT": "8527",
    "PARAGUIBENCH_CHROMIUM_PORT": "9527",
}


def _expand_shell_token(token: str) -> str:
    """展开命令中的 ``$VAR`` / ``${VAR}``，不执行 shell。

    输入参数：
        token：``shlex`` 拆出的单个参数。
    输出返回值：
        用测试占位环境替换后的字符串。
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return _DUMMY_ENV[name]

    return re.sub(r"\$\{([A-Z0-9_]+)\}|\$([A-Z0-9_]+)", replace, token)


def _extract_paraguibench_commands(markdown: str, heading: str) -> list[list[str]]:
    """从指定小节的 bash 代码块提取 ``paraguibench`` 命令。

    输入参数：
        markdown：README 全文。
        heading：最短评测路径小节标题。
    输出返回值：
        每条命令拆成的 argv 列表，已展开占位环境变量。
    """

    section = markdown.split(heading, 1)[1]
    fence = section.split("```bash", 1)[1].split("```", 1)[0]
    joined: list[str] = []
    buffer = ""
    for raw_line in fence.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1]
            continue
        buffer += line
        joined.append(buffer.strip())
        buffer = ""
    commands: list[list[str]] = []
    for line in joined:
        if not line.startswith("paraguibench "):
            continue
        argv = [_expand_shell_token(token) for token in shlex.split(line)]
        commands.append(argv[1:])
    return commands


@pytest.mark.parametrize("heading", tuple(_HEADING_TO_README))
def test_readme_shortest_eval_commands_parse(heading: str) -> None:
    """验证中英文 README 最短路径里的 doctor/run/probe 都能被 argparse 接受。

    输入参数：
        heading：对应 README 小节标题。
    输出返回值：
        无；三条命令均可解析，且 doctor/run 带齐必填路径与端口。
    """

    markdown = _HEADING_TO_README[heading].read_text(encoding="utf-8")
    commands = _extract_paraguibench_commands(markdown, heading)
    names = [argv[0] for argv in commands]
    assert names == ["model-probe", "doctor", "run"]

    parser = build_parser()
    parsed = [parser.parse_args(argv) for argv in commands]
    assert parsed[0].command == "model-probe"
    assert parsed[1].command == "doctor"
    assert parsed[1].task_id == "InformationRetrieval-FileSearch-Readonly-001"
    assert parsed[1].asset_cache_root == "/tmp/paraguibench-assets"
    assert parsed[1].qcow2_path == "/tmp/Ubuntu.qcow2"
    assert parsed[1].server_port == 5527
    assert parsed[2].command == "run"
    assert parsed[2].runs_root == "/tmp/paraguibench-runs"
    assert parsed[2].worker == "qwen"
    assert parsed[2].model == "qwen3.7-flash-2026-07-15"
