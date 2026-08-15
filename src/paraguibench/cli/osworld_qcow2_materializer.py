"""OSWorld qcow2 物化器的正式薄命令入口。

该模块只从 canonical implementation 导入并调用 ``main``，避免把定义
``OSWorldQcow2MaterializationSpec`` 的实现模块以 ``__main__`` 再执行一份，
从而保持 image-manifest loader 与物化器的严格类型身份一致。
"""

from __future__ import annotations

from paraguibench.integrations.osworld.qcow2_materializer import (
    main as _implementation_main,
)


def main(argv: list[str] | None = None) -> int:
    """调用 canonical OSWorld qcow2 物化命令实现。

    输入参数：argv 为可选的显式 CLI 参数；省略时由实现读取当前进程
        命令行。该薄层不解析、复制或改写任何路径与 recipe 字段。
    输出返回值：canonical 实现的固定进程退出码。
    """

    return _implementation_main(argv)


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
