"""公开 CLI 的 secret-reference 与只读 inspect 输出契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

from paraguibench.cli.main import build_parser, main


def test_live_commands_accept_secret_references_not_secret_values() -> None:
    """验证 doctor/run 参数面只允许环境变量名，不接受 key 或 URL 值。

    输入参数：
        无；遍历 argparse action tree。
    输出返回值：
        无；不存在 ``--api-key`` 或 ``--base-url``，引用选项存在。
    """

    parser = build_parser()
    option_strings: set[str] = set()
    pending = [parser]
    while pending:
        current = pending.pop()
        for action in current._actions:
            option_strings.update(action.option_strings)
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                pending.extend(choices.values())

    assert "--api-key" not in option_strings
    assert "--base-url" not in option_strings
    assert "--api-key-env" in option_strings
    assert "--base-url-env" in option_strings


def test_inspect_prints_only_outcomes_and_score(
    tmp_path: Path,
    capsys: object,
) -> None:
    """验证 inspect 不打印 details、task snapshot 或任意模型最终输出。

    输入参数：
        tmp_path：pytest 提供的合成 RunStore 根目录。
        capsys：pytest 标准输出捕获 fixture。
    输出返回值：
        无；终端只有 execution/evaluation/score 三项。
    """

    attempt_path = (
        tmp_path
        / "run-001"
        / "tasks"
        / "task-001"
        / "attempts"
        / "attempt-001"
    )
    attempt_path.mkdir(parents=True)
    secret_fragment = "private-model-output"
    (attempt_path / "summary.json").write_text(
        json.dumps(
            {
                "execution": {"outcome": "SUCCEEDED"},
                "evaluation": {"outcome": "PASSED", "score": 1.0},
                "details": {"raw_output": secret_fragment},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "inspect",
            "--runs-root",
            str(tmp_path),
            "--run-id",
            "run-001",
            "--task-id",
            "task-001",
            "--attempt-id",
            "attempt-001",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "execution=SUCCEEDED" in output
    assert "evaluation=PASSED" in output
    assert "score=1.0" in output
    assert secret_fragment not in output
