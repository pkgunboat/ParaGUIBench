"""
统一的 LLM provider 配置。

开源版安全基线：任何 API key 都不得写入仓库。下面 *_CONFIG 字典中的
`api_key` 缺省值都留空；实际 key 从环境变量读取，调用方必须显式导出。

推荐做法：
    cp configs/api.example.yaml configs/api.yaml
    export OPENAI_API_KEY=sk-xxx
    export DOUBAO_API_KEY=xxx
    export KIMI_API_KEY=sk-xxx
    ...

也可以直接在 configs/api.yaml 中以 ${VAR} 占位，然后由
src.config_loader 在加载时展开。
"""

import os
from typing import Any, Dict

# DeerAPI 网关（一个 OpenAI 兼容聚合代理；可用其它类似服务替换）
DEERAPI_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deerapi.com/v1/",
}

# Anthropic Claude API（Computer Use 原生 endpoint）
# 开源版安全基线：api_key 不得写入仓库；通过环境变量注入：
#   - ANTHROPIC_API_KEY 或 CLAUDE_API_KEY
#   - CLAUDE_BASE_URL（如需切到代理端点）
CLAUDE_CONFIG = {
    "api_key": "",
    "base_url": "https://api.anthropic.com/v1/",
}

# Doubao / Volcano Engine（Seed 系列模型）
DOUBAO_CONFIG = {
    "api_key": "",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
}

# Kimi / Moonshot
KIMI_CONFIG = {
    "api_key": "",
    "base_url": "https://api.moonshot.cn/v1",
}

# BigAI LiteLLM（历史兼容保留；当前测试统一走 DeerAPI）
BIGAI_CONFIG = {
    "api_key": "",
    "base_url": "",
}

# Pincc v2 网关（OpenAI 兼容，用于不支持原生 Responses API computer-use 的中转场景）
# 通过 function-calling 自定义 computer_use 工具调用 GPT-5.x；key 仅从环境变量读取。
PINCC_CONFIG = {
    "api_key": "",
    "base_url": "https://v2.pincc.ai/v1",
}

# DashScope / Qwen
DASHSCOPE_CONFIG = {
    "api_key": "",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}

# Holo3 自托管端点（vLLM / llama.cpp 的 OpenAI 兼容 server）。
# 默认指向 127.0.0.1:8100/v1 —— 这是最常见的"在 114 上通过 SSH 隧道
# 把远端 A:8100 vLLM 映射到本地"的标准入口（脚本 scripts/holo3_run.sh
# 会自动建立这条隧道）。若直接在本机用 llama.cpp 跑 GGUF（如 :8000），
# 改 export HOLO3_BASE_URL=http://127.0.0.1:8000/v1 即可。
# api_key 留 "EMPTY"，vLLM/llama.cpp 默认不校验。
HOLO3_CONFIG = {
    "api_key": "EMPTY",
    "base_url": "http://127.0.0.1:8100/v1",
}

# 默认模型配置。可通过环境变量 BENCH_DEFAULT_<AGENT_TYPE> 覆盖。
DEFAULT_MODELS = {
    "plan_agent":       os.environ.get("BENCH_DEFAULT_PLAN_AGENT",        "gpt-5.4"),
    "code_agent":       os.environ.get("BENCH_DEFAULT_CODE_AGENT",        "gpt-5.2"),
    "gui_agent":        os.environ.get("BENCH_DEFAULT_GUI_AGENT",         "claude-opus-4-5"),
    "claude_gui_agent": os.environ.get("BENCH_DEFAULT_CLAUDE_GUI_AGENT", "claude-sonnet-4-5-20250929"),
    "claude_anthropic_gui_agent": os.environ.get(
        "BENCH_DEFAULT_CLAUDE_ANTHROPIC_GUI_AGENT",
        os.environ.get("BENCH_DEFAULT_CLAUDE_GUI_AGENT", "claude-sonnet-4-5-20250929"),
    ),
    "seed18_gui_agent": os.environ.get("BENCH_DEFAULT_SEED18_GUI_AGENT",  "doubao-seed-1-8-251228"),
    "doubao_plan_agent":os.environ.get("BENCH_DEFAULT_DOUBAO_PLAN_AGENT", "doubao-seed-1-8-251228"),
    "doubao_gui_agent": os.environ.get("BENCH_DEFAULT_DOUBAO_GUI_AGENT",  "doubao-seed-1-8-251228"),
    "kimi_gui_agent":   os.environ.get("BENCH_DEFAULT_KIMI_GUI_AGENT",    "kimi-k2.5"),
    "qwen_gui_agent":   os.environ.get("BENCH_DEFAULT_QWEN_GUI_AGENT",    "qwen3-vl"),
    "gpt54_gui_agent":  os.environ.get("BENCH_DEFAULT_GPT54_GUI_AGENT",   "gpt-5.4"),
    "gpt54_fc_gui_agent": os.environ.get("BENCH_DEFAULT_GPT54_FC_GUI_AGENT", "gpt-5.4"),
    "holo3_gui_agent":  os.environ.get("BENCH_DEFAULT_HOLO3_GUI_AGENT",   "Holo3-35B-A3B.Q4_K_S.gguf"),
}


def _env(name: str, fallback: str = "") -> str:
    """从环境变量取值，空串 fallback。"""
    return os.environ.get(name, fallback)


def _yaml_provider(provider: str) -> Dict[str, Any]:
    """Best-effort read from configs/api.yaml; env still has priority."""
    try:
        from config_loader import load_api_config
    except Exception:
        return {}
    try:
        data = load_api_config()
    except Exception:
        return {}
    section = data.get(provider, {})
    return section if isinstance(section, dict) else {}


def _clean_config_value(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text or text.startswith("${"):
        return fallback
    return text


def get_api_config(provider: str = "deerapi") -> dict:
    """
    获取指定 provider 的 API 配置。

    输入:
        provider: "deerapi" | "claude" | "doubao" | "kimi" | "bigai" | "pincc" | "dashscope"
    输出:
        {"api_key": str, "base_url": str}
    """
    if provider == "deerapi":
        yaml_cfg = _yaml_provider("deerapi") or _yaml_provider("openai")
        yaml_api_key = _clean_config_value(yaml_cfg.get("api_key"))
        yaml_base_url = _clean_config_value(yaml_cfg.get("base_url"), DEERAPI_CONFIG["base_url"])
        return {
            "api_key":  _env(
                "DEERAPI_API_KEY",
                _env("OPENAI_API_KEY", yaml_api_key or DEERAPI_CONFIG["api_key"]),
            ),
            "base_url": _env("DEERAPI_BASE_URL", yaml_base_url),
        }
    if provider == "claude":
        yaml_cfg = _yaml_provider("anthropic") or _yaml_provider("claude")
        yaml_api_key = _clean_config_value(yaml_cfg.get("api_key"))
        yaml_base_url = _clean_config_value(yaml_cfg.get("base_url"), CLAUDE_CONFIG["base_url"])
        return {
            "api_key":  _env(
                "ANTHROPIC_API_KEY",
                _env("CLAUDE_API_KEY", yaml_api_key or CLAUDE_CONFIG["api_key"]),
            ),
            "base_url": _env(
                "ANTHROPIC_BASE_URL",
                _env(
                    "ANTHROPIC_API_BASE",
                    _env("CLAUDE_BASE_URL", yaml_base_url),
                ),
            ),
        }
    if provider == "doubao":
        doubao_key = _env("DOUBAO_API_KEY")
        if doubao_key:
            return {
                "api_key": doubao_key,
                "base_url": _env("DOUBAO_BASE_URL", DOUBAO_CONFIG["base_url"]),
            }
        if _env("DEERAPI_API_KEY") or _env("DEERAPI_BASE_URL"):
            return {
                "api_key": _env("DEERAPI_API_KEY", _env("OPENAI_API_KEY")),
                "base_url": _env("DEERAPI_BASE_URL", DEERAPI_CONFIG["base_url"]),
            }
        return {
            "api_key": DOUBAO_CONFIG["api_key"],
            "base_url": DOUBAO_CONFIG["base_url"],
        }
    if provider == "kimi":
        kimi_key = _env("KIMI_API_KEY", _env("MOONSHOT_API_KEY"))
        if kimi_key:
            return {
                "api_key": kimi_key,
                "base_url": _env("KIMI_BASE_URL", KIMI_CONFIG["base_url"]),
            }
        if _env("DEERAPI_API_KEY") or _env("DEERAPI_BASE_URL"):
            return {
                "api_key": _env("DEERAPI_API_KEY", _env("OPENAI_API_KEY")),
                "base_url": _env("DEERAPI_BASE_URL", DEERAPI_CONFIG["base_url"]),
            }
        return {
            "api_key": KIMI_CONFIG["api_key"],
            "base_url": KIMI_CONFIG["base_url"],
        }
    if provider == "bigai":
        return {
            "api_key":  _env(
                "BIGAI_API_KEY",
                _env("DEERAPI_API_KEY", _env("OPENAI_API_KEY", BIGAI_CONFIG["api_key"])),
            ),
            "base_url": _env(
                "BIGAI_BASE_URL",
                _env("DEERAPI_BASE_URL", DEERAPI_CONFIG["base_url"]),
            ),
        }
    if provider == "pincc":
        return {
            "api_key":  _env("PINCC_API_KEY", PINCC_CONFIG["api_key"]),
            "base_url": _env("PINCC_BASE_URL", PINCC_CONFIG["base_url"]),
        }
    if provider == "dashscope":
        return {
            "api_key":  _env("DASHSCOPE_API_KEY", DASHSCOPE_CONFIG["api_key"]),
            "base_url": _env("DASHSCOPE_BASE_URL", DASHSCOPE_CONFIG["base_url"]),
        }
    if provider == "holo3":
        # 本地自托管，密码通常为占位 "EMPTY"。允许通过 HOLO3_BASE_URL 切换到 SSH 隧道
        # 后的 127.0.0.1 端口，方便在本机直跑。
        return {
            "api_key":  _env("HOLO3_API_KEY", HOLO3_CONFIG["api_key"]),
            "base_url": _env("HOLO3_BASE_URL", HOLO3_CONFIG["base_url"]),
        }
    # 未知 provider 时走 deerapi 兜底
    return get_api_config("deerapi")


def get_api_config_for_model(model_name: str) -> dict:
    """
    根据模型名自动选 provider：当前测试统一走 deerapi。
    """
    return get_api_config("deerapi")


# 向后兼容别名
get_api_config_for_plan_model = get_api_config_for_model


def get_model_name(agent_type: str) -> str:
    """获取指定 agent 类型的默认模型名（支持 env 覆盖）。"""
    return DEFAULT_MODELS.get(agent_type, "gpt-5-2025-08-07")
