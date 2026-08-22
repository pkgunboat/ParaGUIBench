"""WebNavigate 评价模式、浏览器端点与多 VM 结果的纯路由逻辑。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class EvaluationConfigurationError(ValueError):
    """表示 WebNavigate 评价路由配置缺失、冲突或使用了未知值。"""


SUPPORTED_EVALUATION_MODES = (
    "bookmark",
    "osworld_active_tab",
    "osworld_profile_state",
)


@dataclass(frozen=True)
class BrowserVMEndpoint:
    """保存单台浏览器 VM 的两个成对连接端点。

    功能：把 VM API 控制端口和同一容器记录中的 Chromium CDP 端口
    封装为不依赖 Docker provider 的不可变值对象。
    输入参数：
        vm_ip: VM 所在宿主机 IP 或主机名。
        server_port: PythonController 使用的 VM API 端口。
        chromium_port: 浏览器活动页探针使用的 Chromium CDP 端口。
    输出返回值：
        不可变的 ``BrowserVMEndpoint`` 实例。
    """

    vm_ip: str
    server_port: int
    chromium_port: int


def resolve_evaluation_mode(task_config: Mapping[str, Any]) -> str:
    """解析单个 WebNavigate 任务应使用的评价模式。

    功能：
        显式接受 bookmark、OSWorld active-tab 与 Chrome profile-state
        三种模式，并为尚未声明 ``evaluation_mode`` 的历史任务保留
        bookmark 评价路径；未知值立即作为配置错误上抛，避免静默
        走错评分协议。
    输入参数：
        task_config: WebNavigate 任务配置映射。
    输出返回值：
        任务应使用的评价模式字符串。
    异常：
        模式不是公开枚举时抛出 ``EvaluationConfigurationError``。
    """

    mode = task_config.get("evaluation_mode", "bookmark")
    if not isinstance(mode, str) or mode not in SUPPORTED_EVALUATION_MODES:
        raise EvaluationConfigurationError(
            "不支持的 WebNavigate evaluation_mode: "
            f"{mode!r}；仅支持 {SUPPORTED_EVALUATION_MODES}"
        )
    return mode


def build_browser_vm_endpoints(config: Any) -> list[BrowserVMEndpoint]:
    """从容器组配置构造按 VM 配对的浏览器连接端点。

    功能：直接遍历 ``config.containers``，保证每个返回对象的
    ``server_port`` 与 ``chromium_port`` 均来自同一条容器记录；
    本函数通过结构化属性访问工作，不导入 Docker 配置类型。
    输入参数：
        config: 具有 ``vm_ip`` 与 ``containers`` 属性的配置对象。
    输出返回值：
        与 containers 顺序一致的不可变浏览器 VM 端点列表。
    异常：
        配置级字段或任一容器记录的成对端口缺失时抛出
        ``EvaluationConfigurationError``。
    """

    missing_config_fields = [
        field for field in ("vm_ip", "containers") if not hasattr(config, field)
    ]
    if missing_config_fields:
        raise EvaluationConfigurationError(
            "WebNavigate VM 配置缺少字段: "
            f"{', '.join(missing_config_fields)}"
        )

    vm_ip = str(config.vm_ip)
    endpoints = []
    for vm_index, container in enumerate(config.containers):
        if not isinstance(container, Mapping):
            raise EvaluationConfigurationError(
                f"config.containers[{vm_index}] 必须是字段映射"
            )
        missing_fields = [
            field
            for field in ("server_port", "chromium_port")
            if field not in container
        ]
        if missing_fields:
            raise EvaluationConfigurationError(
                f"config.containers[{vm_index}] 缺少字段: "
                f"{', '.join(missing_fields)}"
            )
        endpoints.append(
            BrowserVMEndpoint(
                vm_ip=vm_ip,
                server_port=int(container["server_port"]),
                chromium_port=int(container["chromium_port"]),
            )
        )
    return endpoints


def aggregate_any_complete_vm_results(
    per_vm_results: Sequence[Mapping[str, Any]],
    *,
    evaluation_label: str,
) -> dict[str, Any]:
    """按 any-complete 语义聚合各 VM 的完整评价。

    功能：
        只读取每台 VM 已完成评价后的顶层 ``status`` 与 ``pass``，
        不把不同 VM 的子指标或状态字段重新组合；至少一台 VM 同时满足
        ``status=ok`` 且 ``pass=true`` 时整体通过，并原样附带逐 VM
        诊断结果。
    输入参数：
        per_vm_results: 按 VM 保存的完整评价结果序列。
        evaluation_label: 面向日志和诊断的评价协议名称。
    输出返回值：
        含 ``score``、``pass``、``status``、``reason`` 与
        ``per_vm_results`` 的整体评价字典。
    """

    diagnostic_results = list(per_vm_results)
    label = str(evaluation_label or "").strip() or "OSWorld"
    if not diagnostic_results:
        return {
            "score": -1.0,
            "pass": False,
            "status": "evaluator_error",
            "reason": f"没有收到任何 VM 的 {label} 评价结果",
            "per_vm_results": diagnostic_results,
        }
    if any(
        result.get("status") == "ok" and result.get("pass") is True
        for result in diagnostic_results
    ):
        return {
            "score": 1.0,
            "pass": True,
            "status": "ok",
            "reason": f"至少一台 VM 的完整 {label} 评价通过",
            "per_vm_results": diagnostic_results,
        }
    if all(result.get("status") == "ok" for result in diagnostic_results):
        return {
            "score": 0.0,
            "pass": False,
            "status": "ok",
            "reason": "全部 VM 均可评价，但没有 VM 的完整评价通过",
            "per_vm_results": diagnostic_results,
        }
    return {
        "score": -1.0,
        "pass": False,
        "status": "evaluator_error",
        "reason": "没有 VM 完整通过，且至少一台 VM 的评价状态不是 ok",
        "per_vm_results": diagnostic_results,
    }


def aggregate_active_tab_vm_results(
    per_vm_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """兼容既有调用，聚合各 VM 的完整 active-tab 评价。

    功能：
        将 active-tab 专用公开入口委托给通用 any-complete 聚合器，
        保持历史调用形状和三态语义不变。
    输入参数：
        per_vm_results: 按 VM 保存的完整 active-tab 评价结果序列。
    输出返回值：
        通用聚合器生成的整体评价字典。
    """

    return aggregate_any_complete_vm_results(
        per_vm_results,
        evaluation_label="active-tab",
    )
