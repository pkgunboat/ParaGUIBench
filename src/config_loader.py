"""
统一配置加载器。

功能:
    合并 deploy.yaml / api.yaml / agent.yaml 三份配置文件，并展开 ${ENV_VAR}
    占位符。优先级（从高到低）：
        CLI 参数  >  环境变量  >  YAML 文件  >  内置默认值

典型用法:
    from config_loader import load_deploy_config, resolve

    deploy = load_deploy_config()                          # 默认读 configs/deploy.yaml
    vm_ip  = resolve(deploy, "server.vm_host",
                     cli_value=args.vm_ip,
                     env_var="BENCH_VM_HOST",
                     default="127.0.0.1")
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEPLOY_PATH = REPO_ROOT / "configs" / "deploy.yaml"
DEFAULT_API_PATH    = REPO_ROOT / "configs" / "api.yaml"
DEFAULT_AGENT_PATH  = REPO_ROOT / "configs" / "agent.yaml"

# ${VAR} 或 ${VAR:-default}
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env_in_str(value: str) -> str:
    """
    展开字符串中的 ${VAR} / ${VAR:-default} 占位符。

    输入:
        value: 原始字符串
    输出:
        展开后的字符串（未设置且无 default 时保持原样）
    """
    def _sub(match: re.Match) -> str:
        var, default = match.group(1), match.group(2)
        env_val = os.environ.get(var)
        if env_val is not None and env_val != "":
            return env_val
        if default is not None:
            return default
        return match.group(0)      # 保持原样，方便上层识别未设置
    return _ENV_PLACEHOLDER.sub(_sub, value)


def _expand_env_recursive(node: Any) -> Any:
    """
    对 dict / list / str 递归展开环境变量占位符。
    """
    if isinstance(node, str):
        return _expand_env_in_str(node)
    if isinstance(node, list):
        return [_expand_env_recursive(x) for x in node]
    if isinstance(node, dict):
        return {k: _expand_env_recursive(v) for k, v in node.items()}
    return node


def load_yaml_config(path: Path | str, required: bool = False) -> Dict[str, Any]:
    """
    读取 YAML 并展开环境变量占位符。

    输入:
        path: YAML 文件路径
        required: True 时文件缺失将抛异常；False 时返回空 dict
    输出:
        配置字典（已展开 env 占位符）
    """
    path = Path(path)
    if not path.is_file():
        if required:
            raise FileNotFoundError(f"配置文件不存在: {path}")
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _expand_env_recursive(data)


def load_deploy_config(path: Path | str | None = None) -> Dict[str, Any]:
    """
    加载部署配置。不指定 path 时先查 BENCH_DEPLOY_CONFIG 环境变量，
    再退化到 configs/deploy.yaml，最后退化到 configs/deploy.example.yaml。
    """
    if path is None:
        env_path = os.environ.get("BENCH_DEPLOY_CONFIG")
        if env_path:
            path = env_path
        elif DEFAULT_DEPLOY_PATH.is_file():
            path = DEFAULT_DEPLOY_PATH
        else:
            path = REPO_ROOT / "configs" / "deploy.example.yaml"
    return load_yaml_config(path, required=False)


def load_api_config(path: Path | str | None = None) -> Dict[str, Any]:
    """加载 LLM API 配置（api.yaml）。"""
    if path is None:
        env_path = os.environ.get("BENCH_API_CONFIG")
        if env_path:
            path = env_path
        elif DEFAULT_API_PATH.is_file():
            path = DEFAULT_API_PATH
        else:
            path = REPO_ROOT / "configs" / "api.example.yaml"
    return load_yaml_config(path, required=False)


def load_agent_config(path: Path | str | None = None) -> Dict[str, Any]:
    """加载 Agent 推理参数配置（agent.yaml）。"""
    if path is None:
        env_path = os.environ.get("BENCH_AGENT_CONFIG")
        if env_path:
            path = env_path
        elif DEFAULT_AGENT_PATH.is_file():
            path = DEFAULT_AGENT_PATH
        else:
            path = REPO_ROOT / "configs" / "agent.example.yaml"
    return load_yaml_config(path, required=False)


def get_path(config: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    """
    按 `a.b.c` 访问嵌套 dict。

    输入:
        config: 配置字典
        dotted: 点分路径
        default: 缺失时返回值
    输出:
        命中值或 default
    """
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def resolve(
    config: Dict[str, Any],
    dotted: str,
    *,
    cli_value: Any = None,
    env_var: Optional[str] = None,
    default: Any = None,
) -> Any:
    """
    三层合并取值：CLI > env > YAML > default。

    输入:
        config: 已加载的配置 dict
        dotted: 点分路径（例如 "server.vm_host"）
        cli_value: argparse 传入的值（None / 空串视为未设置）
        env_var: 环境变量名
        default: 兜底默认值
    输出:
        最终决定值
    """
    if cli_value is not None and cli_value != "":
        return cli_value
    if env_var:
        env_val = os.environ.get(env_var)
        if env_val is not None and env_val != "":
            return env_val
    yaml_val = get_path(config, dotted, None)
    if yaml_val is not None and yaml_val != "":
        return yaml_val
    return default


def get_ssh_password() -> str:
    """
    从 deploy.yaml.server.ssh_password_env 指定的环境变量读取 SSH 密码。

    输出:
        密码字符串；未配置时返回空串
    """
    deploy = load_deploy_config()
    env_name = get_path(deploy, "server.ssh_password_env", "BENCH_SSH_PASSWORD")
    return os.environ.get(env_name, "")


# ─────────────────────────────────────────────────────────────
# 便捷访问器：集中封装常用字段，减少调用点查 dotted key 的重复
# ─────────────────────────────────────────────────────────────

class DeployConfig:
    """薄封装：把 deploy.yaml 的常用字段暴露为属性。"""

    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data if data is not None else load_deploy_config()

    @property
    def vm_host(self) -> str:
        return get_path(self._data, "server.vm_host", "127.0.0.1")

    @property
    def vm_user(self) -> str:
        return get_path(self._data, "server.vm_user", "benchmark")

    @property
    def shared_base_dir(self) -> str:
        return get_path(self._data, "server.shared_base_dir", "/home/benchmark/shared")

    @property
    def qcow2_path(self) -> str:
        raw = get_path(self._data, "server.qcow2_path", "./resources/Ubuntu.qcow2")
        return os.path.abspath(os.path.expanduser(raw))

    @property
    def resources_root(self) -> str:
        raw = get_path(self._data, "resources.root", "./resources")
        return os.path.abspath(os.path.expanduser(raw))

    @property
    def onlyoffice_host(self) -> str:
        return get_path(self._data, "services.onlyoffice.host_ip", "127.0.0.1")

    @property
    def onlyoffice_flask_port(self) -> int:
        return int(get_path(self._data, "services.onlyoffice.flask_port", 5050))

    @property
    def webmall_host(self) -> str:
        return get_path(self._data, "services.webmall.host_ip", "127.0.0.1")

    @property
    def webmall_ports(self) -> list:
        return list(get_path(self._data, "services.webmall.ports", [9081, 9082, 9083, 9084]))

    @property
    def docker_daemon_port(self) -> int:
        return int(get_path(self._data, "server.docker_daemon_port", 50003))

    def raw(self) -> Dict[str, Any]:
        return self._data
