"""用 AST 校验 ParaGUIBench 一级 Module 的允许依赖方向。"""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "src" / "paraguibench"

_FORBIDDEN_INTERNAL_DEPENDENCIES = {
    "runstore": {
        "framework",
        "agents",
        "runtime",
        "evaluation",
        "integrations",
        "benchmark",
        "cli",
    },
    "agents": {"runtime", "evaluation", "cli"},
    "evaluation": {"agents", "framework", "runtime", "cli"},
    "benchmark": {
        "runtime",
        "agents",
        "evaluation",
        "integrations",
        "runstore",
        "cli",
    },
}
_FRAMEWORK_ALLOWED_INTERNAL_DEPENDENCIES = {"framework", "runstore"}


def _source_module(path: Path) -> str:
    """返回一个生产 Python 文件所属的一级 ParaGUIBench Module。

    输入参数：
        path：位于 ``src/paraguibench`` 下的生产 Python 文件。
    输出返回值：
        文件相对路径的第一段，例如 ``agents`` 或 ``runstore``。
    """

    return path.relative_to(PACKAGE_ROOT).parts[0]


def _absolute_from_module(path: Path, node: ast.ImportFrom) -> str:
    """把绝对或相对 ``from`` import 解析为完整 module 名。

    输入参数：
        path：包含该 import 的生产 Python 文件。
        node：由 ``ast`` 解析得到的 ``ImportFrom`` 节点。
    输出返回值：
        形如 ``paraguibench.framework.contracts`` 的绝对 module 名；对于
        顶层第三方或标准库 import，则保留其原 module 名。
    """

    if node.level == 0:
        return node.module or ""

    relative_parts = list(
        path.relative_to(PACKAGE_ROOT).with_suffix("").parts
    )
    package_parts = relative_parts[:-1]
    ascend_count = node.level - 1
    if ascend_count > len(package_parts):
        return ""
    if ascend_count:
        package_parts = package_parts[:-ascend_count]
    if node.module:
        package_parts.extend(node.module.split("."))
    suffix = ".".join(package_parts)
    return f"paraguibench.{suffix}" if suffix else "paraguibench"


def _imported_modules(
    path: Path,
    tree: ast.AST,
) -> Iterable[tuple[int, str]]:
    """枚举一个语法树中的全部 import 及其源码行号。

    输入参数：
        path：语法树对应的生产 Python 文件，用于解析相对 import。
        tree：通过 ``ast.parse`` 得到的完整语法树。
    输出返回值：
        逐项产生 ``(lineno, module_name)``；``from`` import 会把导入名
        追加到 module 后，以便识别 ``from paraguibench import runtime``。
    """

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base_module = _absolute_from_module(path, node)
            for alias in node.names:
                if alias.name == "*":
                    imported_module = base_module
                elif base_module:
                    imported_module = f"{base_module}.{alias.name}"
                else:
                    imported_module = alias.name
                yield node.lineno, imported_module


def _dependency_kind(module_name: str) -> tuple[str, str]:
    """识别 import 属于内部 Module、标准库还是第三方依赖。

    输入参数：
        module_name：已经解析为绝对形式的 import module 名。
    输出返回值：
        ``(kind, root)``；kind 为 ``internal``、``stdlib`` 或
        ``external``，root 为 ParaGUIBench 一级 Module 或顶级包名。
    """

    root = module_name.split(".", maxsplit=1)[0]
    if root == "paraguibench":
        parts = module_name.split(".")
        internal_root = parts[1] if len(parts) > 1 else ""
        return "internal", internal_root
    if root == "__future__" or root in sys.stdlib_module_names:
        return "stdlib", root
    return "external", root


def _collect_import_boundary_violations() -> list[str]:
    """扫描生产包并收集违反架构依赖方向的稳定诊断。

    输入参数：
        无；固定扫描 ``src/paraguibench`` 下的全部 Python 文件。
    输出返回值：
        排序后的违规描述列表；每项包含相对文件、行号、源 Module 与目标
        依赖，但不读取配置、环境变量或运行时凭据。
    """

    violations: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        source = _source_module(path)
        tree = ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
        )
        for line_number, module_name in _imported_modules(path, tree):
            dependency_kind, dependency_root = _dependency_kind(module_name)
            reason: str | None = None
            forbidden = _FORBIDDEN_INTERNAL_DEPENDENCIES.get(source, set())
            if (
                dependency_kind == "internal"
                and dependency_root in forbidden
            ):
                reason = "禁止的内部 Module 依赖"
            elif source == "framework":
                if dependency_kind == "external":
                    reason = "framework 只能依赖标准库与 runstore"
                elif (
                    dependency_kind == "internal"
                    and dependency_root
                    not in _FRAMEWORK_ALLOWED_INTERNAL_DEPENDENCIES
                ):
                    reason = "framework 只能依赖自身与 runstore"
            if reason is None:
                continue
            relative_path = path.relative_to(REPO_ROOT).as_posix()
            violations.append(
                f"{relative_path}:{line_number}: "
                f"{source} -> {module_name} ({reason})"
            )
    return sorted(violations)


def test_first_level_modules_follow_declared_import_boundaries() -> None:
    """验证一级 Module 的静态 import 符合 ADR 声明的依赖方向。

    输入参数：
        无；测试扫描当前生产源码，不导入或执行被检查的 module。
    输出返回值：
        无；没有违规时通过，存在违规时一次性报告全部文件和行号。
    """

    violations = _collect_import_boundary_violations()
    assert not violations, "发现架构依赖方向违规：\n" + "\n".join(
        f"- {violation}" for violation in violations
    )
