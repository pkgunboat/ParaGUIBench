"""`python -m paraguibench.methods_runner <category> [args...]` 入口。"""

from __future__ import annotations

import sys

from .launcher import RUNNER_FILES, launch


def main(argv: list[str] | None = None) -> int:
    """按类别透传执行原 runner。

    输入参数：
        argv：[category, ...原 runner 参数]；缺省读 sys.argv。
    输出返回值：
        总是 0；失败路径由原 runner 以 SystemExit 结束。
    """

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("用法: python -m paraguibench.methods_runner <category> [args...]")
        print("类别:", ", ".join(sorted(RUNNER_FILES)))
        return 0
    category, rest = args[0], args[1:]
    try:
        launch(category, rest)
    except KeyError:
        print(f"未知类别: {category}")
        print("可用类别:", ", ".join(sorted(RUNNER_FILES)))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
