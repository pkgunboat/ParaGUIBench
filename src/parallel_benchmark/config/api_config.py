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

# 默认模型配置。可通过环境变量 BENCH_DEFAULT_<AGENT_TYPE> 覆盖。
DEFAULT_MODELS = {
    "plan_agent":       os.environ.get("BENCH_DEFAULT_PLAN_AGENT",        "gpt-5.4"),
    "code_agent":       os.environ.get("BENCH_DEFAULT_CODE_AGENT",        "gpt-5.2"),
    "gui_agent":        os.environ.get("BENCH_DEFAULT_GUI_AGENT",         "claude-opus-4-5"),
    "claude_gui_agent": os.environ.get("BENCH_DEFAULT_CLAUDE_GUI_AGENT", "claude-sonnet-4-5-20250929"),
    "seed18_gui_agent": os.environ.get("BENCH_DEFAULT_SEED18_GUI_AGENT",  "doubao-seed-1-8-251228"),
    "doubao_plan_agent":os.environ.get("BENCH_DEFAULT_DOUBAO_PLAN_AGENT", "doubao-seed-1-8-251228"),
    "doubao_gui_agent": os.environ.get("BENCH_DEFAULT_DOUBAO_GUI_AGENT",  "doubao-seed-1-8-251228"),
    "kimi_gui_agent":   os.environ.get("BENCH_DEFAULT_KIMI_GUI_AGENT",    "kimi-k2.5"),
    "qwen_gui_agent":   os.environ.get("BENCH_DEFAULT_QWEN_GUI_AGENT",    "qwen3-vl"),
    "gpt54_gui_agent":  os.environ.get("BENCH_DEFAULT_GPT54_GUI_AGENT",   "gpt-5.4-mini"),
    "gpt54_fc_gui_agent": os.environ.get("BENCH_DEFAULT_GPT54_FC_GUI_AGENT", "gpt-5.4-mini"),
}


def _env(name: str, fallback: str = "") -> str:
    """从环境变量取值，空串 fallback。"""
    return os.environ.get(name, fallback)


def get_api_config(provider: str = "deerapi") -> dict:
    """
    获取指定 provider 的 API 配置。

    输入:
        provider: "deerapi" | "claude" | "doubao" | "kimi" | "bigai" | "dashscope"
    输出:
        {"api_key": str, "base_url": str}
    """
    if provider == "deerapi":
        return {
            "api_key":  _env("DEERAPI_API_KEY", _env("OPENAI_API_KEY", DEERAPI_CONFIG["api_key"])),
            "base_url": _env("DEERAPI_BASE_URL", DEERAPI_CONFIG["base_url"]),
        }
    if provider == "claude":
        return {
            "api_key":  _env("ANTHROPIC_API_KEY", _env("CLAUDE_API_KEY", CLAUDE_CONFIG["api_key"])),
            "base_url": _env("CLAUDE_BASE_URL", CLAUDE_CONFIG["base_url"]),
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
