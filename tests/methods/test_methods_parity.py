"""迁移方法代码的一致性锁定：与 parity_manifest.json 逐文件对照。

任何对 src/parallel_benchmark、src/desktop_env、src/stages、src/pipelines、
src/mm_agents 的改动都会失败；有意修改时必须显式更新清单并在
docs/methods-provenance.md 追加记录。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "tests" / "methods" / "parity_manifest.json"
LOCKED_ROOTS = (
    "src/parallel_benchmark",
    "src/desktop_env",
    "src/stages",
    "src/pipelines",
    "src/mm_agents",
)
LOCKED_FILES = ("src/config_loader.py", "src/__init__.py")


def _locked_files() -> dict[str, str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["schema"] == "paraguibench.methods-parity.v1"
    return dict(manifest["files"])


def _actual_files() -> set[str]:
    actual: set[str] = set(LOCKED_FILES)
    for root in LOCKED_ROOTS:
        for path in (REPO_ROOT / root).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                actual.add(path.relative_to(REPO_ROOT).as_posix())
    return actual


def test_manifest_covers_exactly_the_locked_trees() -> None:
    """工作树被锁定文件集合与清单完全一致（无新增、无缺失）。"""

    expected = set(_locked_files())
    actual = _actual_files()
    assert actual == expected, (
        f"新增未登记: {sorted(actual - expected)[:5]}；"
        f"清单悬空: {sorted(expected - actual)[:5]}"
    )


def test_locked_files_match_migration_baseline() -> None:
    """每个被锁定文件与迁移基线 SHA-256 一致。"""

    mismatches: list[str] = []
    for relative, expected in sorted(_locked_files().items()):
        digest = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
        if digest != expected:
            mismatches.append(relative)
    assert not mismatches, f"偏离迁移基线的文件: {mismatches[:10]}"
