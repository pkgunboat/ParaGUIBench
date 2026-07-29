#!/usr/bin/env python3
"""对拟提交到公开仓库的文本文件执行高置信度静态安全扫描。

扫描器只读取仓库候选文件，不读取进程环境变量，也不会在报告中输出命中的
凭据、URL、主机地址或绝对路径原文。它不是通用秘密管理器，也不能替代凭据
轮换、Git 历史审计和部署期的 sentinel secret 验证。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Pattern, Sequence, Tuple


MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "artifacts",
        "build",
        "dist",
        "htmlcov",
        "logs",
        "node_modules",
        "results",
        "runs",
    }
)


@dataclass(frozen=True)
class ScanRule:
    """一条不保存命中原文的静态扫描规则。"""

    rule_id: str
    category: str
    message: str
    pattern: Pattern[str]


@dataclass(frozen=True)
class Finding:
    """一条可安全输出的扫描结果，只记录位置和规则元数据。"""

    relative_path: str
    line_number: int
    rule_id: str
    category: str
    message: str


def build_rules() -> Tuple[ScanRule, ...]:
    """功能：构造高置信度凭据、私网主机和开发者路径扫描规则。

    输入参数：无。
    输出返回值：不可变的 ``ScanRule`` 元组；规则只负责定位，不保留命中值。
    """

    return (
        ScanRule(
            rule_id="provider-token",
            category="secret-token",
            message="发现疑似第三方服务访问令牌，请移出仓库并立即轮换。",
            pattern=re.compile(
                r"\b(?:"
                r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
                r"|hf_[A-Za-z0-9]{20,}"
                r"|github_pat_[A-Za-z0-9_]{20,}"
                r"|gh[pousr]_[A-Za-z0-9]{20,}"
                r"|xox[baprs]-[A-Za-z0-9-]{20,}"
                r")\b"
            ),
        ),
        ScanRule(
            rule_id="cloud-access-key",
            category="secret-token",
            message="发现疑似云服务访问标识，请移出仓库并核查配套密钥。",
            pattern=re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        ),
        ScanRule(
            rule_id="private-key-block",
            category="secret-key",
            message="发现私钥材料头部，私钥文件不得进入公开仓库。",
            pattern=re.compile(
                r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
            ),
        ),
        ScanRule(
            rule_id="quoted-secret-assignment",
            category="secret-token",
            message="发现敏感字段的非空字面量，请改为受控环境注入。",
            pattern=re.compile(
                r"""(?ix)
                \b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)\b
                \s*[:=]\s*
                (?P<quote>["'])
                (?!\$|\{|<|replace|change[_-]?me|your[_-])
                [^"'\r\n]{12,}
                (?P=quote)
                """
            ),
        ),
        ScanRule(
            rule_id="environment-secret-assignment",
            category="secret-token",
            message="发现敏感环境变量的非空字面量，请改为空模板或受控注入。",
            pattern=re.compile(
                r"""(?x)
                ^\s*[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET)
                \s*[:=]\s*
                (?!$|\$\{|\{\{|<|replace|change[_-]?me|your[_-]|none\b|null\b)
                [^\s#]{12,}
                """
            ),
        ),
        ScanRule(
            rule_id="private-ipv4",
            category="internal-host",
            message="发现固定私网地址，请改为配置项或公开可复现的服务地址。",
            pattern=re.compile(
                r"(?<![\d.])(?:"
                r"10(?:\.\d{1,3}){3}"
                r"|192\.168(?:\.\d{1,3}){2}"
                r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
                r")(?![\d.])"
            ),
        ),
        ScanRule(
            rule_id="private-hostname",
            category="internal-host",
            message="发现内部域名，请改为配置项并在示例中留空。",
            pattern=re.compile(
                r"(?i)\b[A-Za-z0-9][A-Za-z0-9.-]*\.(?:lan|internal)\b"
            ),
        ),
        ScanRule(
            rule_id="developer-home-path",
            category="internal-path",
            message="发现开发者机器绝对路径，请改为仓库相对路径或配置项。",
            pattern=re.compile(
                r"""(?x)
                (?<![A-Za-z0-9_])
                /(?:Users|home)/
                (?!(?:oai|root|ubuntu|user)(?:/|$))
                [A-Za-z0-9._-]+
                (?:/|(?=["'\s]))
                """
            ),
        ),
        ScanRule(
            rule_id="windows-developer-home-path",
            category="internal-path",
            message="发现开发者机器绝对路径，请改为仓库相对路径或配置项。",
            pattern=re.compile(
                r"""(?ix)
                \b[A-Z]:\\Users\\
                (?!(?:Default|Public|user)(?:\\|$))
                [A-Za-z0-9._-]+
                (?:\\|(?=["'\s]))
                """
            ),
        ),
    )


RULES = build_rules()


def _run_git_file_listing(root: Path) -> Optional[List[Path]]:
    """功能：通过 Git 获取 tracked 与非忽略 untracked 候选文件。

    输入参数：``root`` 为待扫描仓库根目录。
    输出返回值：成功时返回绝对 ``Path`` 列表；目录不是 Git 仓库或命令失败时
    返回 ``None``，调用方随后使用文件系统回退逻辑。命令只读取文件名。
    """

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    relative_names = [
        name for name in completed.stdout.decode("utf-8", errors="surrogateescape").split("\0") if name
    ]
    return [root / name for name in relative_names]


def _walk_candidate_files(root: Path) -> List[Path]:
    """功能：在无 Git 元数据时递归枚举可扫描文件且不跟随目录链接。

    输入参数：``root`` 为待扫描目录。
    输出返回值：按路径排序的普通文件列表；缓存、构建和运行产物目录被排除。
    """

    candidates: List[Path] = []
    for path in root.rglob("*"):
        relative_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative_parts):
            continue
        if path.is_file() and not path.is_symlink():
            candidates.append(path)
    return sorted(candidates)


def collect_candidate_files(root: Path) -> List[Path]:
    """功能：收集本次仓库扫描的稳定候选文件集合。

    输入参数：``root`` 为仓库根目录，可以是相对路径或绝对路径。
    输出返回值：去重、排序后的绝对文件路径列表；Git 忽略文件不会被读取。
    """

    resolved_root = root.resolve()
    git_candidates = _run_git_file_listing(resolved_root)
    candidates = (
        git_candidates if git_candidates is not None else _walk_candidate_files(resolved_root)
    )
    filtered = {
        path
        for path in candidates
        if path.is_file()
        and not path.is_symlink()
        and not any(
            part in EXCLUDED_DIRECTORY_NAMES
            for part in path.relative_to(resolved_root).parts
        )
    }
    return sorted(filtered)


def _read_text_lines(path: Path) -> Optional[List[str]]:
    """功能：在大小和二进制门禁通过后安全读取文本行。

    输入参数：``path`` 为候选文件路径。
    输出返回值：文本行列表；文件过大、包含 NUL 字节或读取失败时返回 ``None``。
    """

    try:
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if b"\0" in payload:
        return None
    return payload.decode("utf-8", errors="replace").splitlines()


def scan_file(
    path: Path,
    root: Path,
    rules: Sequence[ScanRule] = RULES,
) -> List[Finding]:
    """功能：扫描单个文本文件并生成不包含命中原文的 finding。

    输入参数：
    ``path`` 为文件路径；``root`` 用于生成安全的相对路径；
    ``rules`` 为要应用的规则序列。
    输出返回值：按行号和规则顺序排列的 ``Finding`` 列表；同一行同一类别只报告
    一次，以减少重复噪声。
    """

    lines = _read_text_lines(path)
    if lines is None:
        return []
    try:
        relative_path = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative_path = path.name

    findings: List[Finding] = []
    for line_number, line in enumerate(lines, start=1):
        reported_categories = set()
        for rule in rules:
            if rule.category in reported_categories:
                continue
            if rule.pattern.search(line) is None:
                continue
            findings.append(
                Finding(
                    relative_path=relative_path,
                    line_number=line_number,
                    rule_id=rule.rule_id,
                    category=rule.category,
                    message=rule.message,
                )
            )
            reported_categories.add(rule.category)
    return findings


def scan_repository(root: Path) -> Tuple[List[Finding], int]:
    """功能：扫描仓库候选文本文件并聚合全部 finding。

    输入参数：``root`` 为仓库根目录。
    输出返回值：二元组 ``(findings, candidate_count)``，分别表示脱敏结果列表和
    实际进入候选集合的文件数。
    """

    candidates = collect_candidate_files(root)
    findings: List[Finding] = []
    for path in candidates:
        findings.extend(scan_file(path, root))
    findings.sort(
        key=lambda item: (
            item.relative_path,
            item.line_number,
            item.category,
            item.rule_id,
        )
    )
    return findings, len(candidates)


def format_findings(findings: Iterable[Finding]) -> str:
    """功能：把 finding 格式化为适合终端和 CI 的脱敏文本。

    输入参数：``findings`` 为 ``Finding`` 可迭代对象。
    输出返回值：每条结果一行的字符串；不包含匹配内容、凭据值或内部地址原文。
    """

    return "\n".join(
        (
            f"{finding.relative_path}:{finding.line_number} "
            f"[{finding.category}/{finding.rule_id}] {finding.message}"
        )
        for finding in findings
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """功能：构造命令行参数解析器。

    输入参数：无。
    输出返回值：支持 ``--root`` 参数的 ``ArgumentParser`` 实例。
    """

    parser = argparse.ArgumentParser(
        description="扫描拟公开文件中的高置信度凭据、私网地址和开发者绝对路径。"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="待扫描仓库根目录，默认使用当前工作目录。",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """功能：执行命令行扫描并以退出码表达门禁结果。

    输入参数：``argv`` 为可选参数序列；``None`` 时使用当前进程命令行。
    输出返回值：未发现问题返回 0；发现问题或根目录无效返回 1。
    """

    arguments = build_argument_parser().parse_args(argv)
    root = arguments.root.resolve()
    if not root.is_dir():
        print("安全静态扫描失败：指定的仓库根目录不存在或不是目录。")
        return 1

    findings, candidate_count = scan_repository(root)
    if findings:
        print(
            "安全静态扫描未通过："
            f"在 {candidate_count} 个候选文件中发现 {len(findings)} 个问题。"
        )
        print(format_findings(findings))
        print("报告已省略所有命中值；请在本地定位文件后完成移除或参数化。")
        return 1

    print(
        "安全静态扫描通过："
        f"已检查 {candidate_count} 个候选文件，未发现高置信度凭据或内部路径。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
