"""将 canonical benchmark task 物化为当前部署可执行的副本。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
import re
from typing import Any

from paraguibench.benchmark.errors import TaskMaterializationError

_CREDENTIAL_BINDING_PATTERN = re.compile(
    r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?)(?:_|$)",
    re.IGNORECASE,
)


def materialize_task(
    task: Mapping[str, Any],
    bindings: Mapping[str, str],
) -> dict[str, Any]:
    """递归替换 task 中的非敏感环境绑定，并保持原始 task 不变。

    输入参数：
        task：只读 canonical task；字符串可包含 ``${NAME}`` 形式的占位符。
        bindings：当前部署提供的非敏感绑定名称和值。
    输出返回值：
        一个与输入结构等价的新字典；其中所有已提供绑定的占位符均被替换。
    异常：
        TaskMaterializationError：task 声明的任一绑定未由当前部署提供。
    """

    required_bindings = task.get("required_environment_bindings", [])
    missing_bindings = [
        name for name in required_bindings if name not in bindings
    ]
    if missing_bindings:
        missing_names = ", ".join(missing_bindings)
        raise TaskMaterializationError(
            f"缺少 task 所需的环境绑定：{missing_names}"
        )
    credential_bindings = [
        name
        for name in required_bindings
        if _CREDENTIAL_BINDING_PATTERN.search(name)
    ]
    if credential_bindings:
        binding_names = ", ".join(credential_bindings)
        raise TaskMaterializationError(
            f"task 环境绑定禁止承载凭据：{binding_names}"
        )
    for name in required_bindings:
        if name.endswith("_DIR"):
            _validate_directory_binding(name, bindings[name])

    materialized = _materialize_value(task, bindings)
    return dict(materialized)


def _validate_directory_binding(name: str, value: str) -> None:
    """验证目录绑定是无父目录跳转的 POSIX 绝对路径。

    输入参数：
        name：公开的绑定名称，仅用于错误定位。
        value：当前部署提供的目录字符串。
    输出返回值：
        无；有效时正常返回。
    异常：
        TaskMaterializationError：目录不是绝对路径或包含 ``..`` 段。
    """

    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise TaskMaterializationError(
            f"目录绑定必须是安全的 POSIX 绝对路径：{name}"
        )


def _materialize_value(value: Any, bindings: Mapping[str, str]) -> Any:
    """递归复制一个 JSON 兼容值并替换其中的绑定占位符。

    输入参数：
        value：待复制的任意 JSON 兼容值。
        bindings：占位符名称到部署值的映射。
    输出返回值：
        替换后的深层副本；标量类型保持原值。
    """

    if isinstance(value, str):
        result = value
        for name, replacement in bindings.items():
            result = result.replace(f"${{{name}}}", replacement)
        return result
    if isinstance(value, Mapping):
        return {
            key: _materialize_value(nested, bindings)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_materialize_value(item, bindings) for item in value]
    return value
