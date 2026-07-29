#!/usr/bin/env python3
"""验证外部 secret 文件元数据，不读取或输出文件内容与路径。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Sequence


@dataclass(frozen=True, slots=True)
class CheckResult:
    """保存一项仅由固定标识和布尔状态组成的 secret 文件检查。"""

    check_id: str
    passed: bool


def verify_secret_file(
    secret_file: Path,
    checkout_root: Path,
) -> tuple[CheckResult, ...]:
    """功能：验证 secret 文件位于 checkout 外且满足最小权限协议。

    输入参数：
        secret_file：待检查的外部 secret 文件；函数不会打开或读取它。
        checkout_root：公开源码 checkout 根目录，用于阻止凭据落入仓库。
    输出返回值：
        固定顺序的脱敏检查结果；底层路径、所有者 ID 和 mode 均不返回。
    """

    exists = secret_file.exists()
    try:
        metadata = secret_file.lstat()
    except OSError:
        metadata = None

    is_regular = bool(
        metadata is not None
        and stat.S_ISREG(metadata.st_mode)
        and not secret_file.is_symlink()
    )
    owns_file = bool(
        metadata is not None
        and hasattr(os, "getuid")
        and metadata.st_uid == os.getuid()
    )
    private_mode = bool(
        metadata is not None
        and stat.S_IMODE(metadata.st_mode) == 0o600
    )
    try:
        secret_file.resolve(strict=False).relative_to(
            checkout_root.resolve(strict=True)
        )
    except ValueError:
        outside_checkout = True
    except OSError:
        outside_checkout = False
    else:
        outside_checkout = False

    results = (
        CheckResult("file-exists", exists),
        CheckResult("file-regular", is_regular),
        CheckResult("file-owner", owns_file),
        CheckResult("file-mode-0600", private_mode),
        CheckResult("file-outside-checkout", outside_checkout),
    )
    return results + (
        CheckResult(
            "secret-file",
            all(result.passed for result in results),
        ),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """功能：构造 secret 文件元数据验证器的命令行参数。

    输入参数：
        无。
    输出返回值：
        要求外部文件和 checkout 根目录的 ``ArgumentParser``。
    """

    parser = argparse.ArgumentParser(
        description="Verify secret-file metadata without reading its content."
    )
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--checkout-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """功能：运行元数据门禁并只打印固定 PASS/FAIL 行。

    输入参数：
        argv：可选参数序列；``None`` 时读取进程命令行。
    输出返回值：
        所有检查通过返回 0，否则返回 1；不输出任何路径或文件内容。
    """

    arguments = build_argument_parser().parse_args(argv)
    results = verify_secret_file(
        arguments.secret_file,
        arguments.checkout_root,
    )
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.check_id}")
    return 0 if results[-1].passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
