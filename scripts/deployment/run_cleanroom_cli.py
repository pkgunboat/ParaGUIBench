#!/usr/bin/env python3
"""在首次导入 ParaGUIBench 前冻结 cleanroom 的无 bytecode 边界。"""

from __future__ import annotations

import os
import sys


# 该开关必须位于任何 ``paraguibench`` import 之前；放进 package
# ``__init__`` 或 CLI ``main`` 都会晚于解释器对上游模块的缓存写入。
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def main(argv: list[str] | None = None) -> int:
    """通过正式 CLI 执行 cleanroom 命令且不改写冻结源码树。

    输入参数：``argv`` 为可选命令参数序列；``None`` 时由正式 CLI
        读取当前进程参数。bootstrap 不解析、记录或回显任何参数值。
    输出返回值：原样返回 ``paraguibench.cli.main.main`` 的退出码；
        子进程继承无 bytecode 环境开关。
    """

    from paraguibench.cli.main import main as cli_main

    return cli_main(None if argv is None else list(argv))


if __name__ == "__main__":
    raise SystemExit(main())
