"""
Parallel Agents as Tools
将 CodeAgent / ClaudeGUIAgent / ToolAgent 等封装为可被 Plan Agent 调用的 tool。

开源版改造：__init__ 不再 eager-import 所有 Agent 实现，避免因未启用的
后端（如 Claude → benchmarkClient）缺失而导致整个 package 无法导入。
通过 PEP 562 的 module-level __getattr__ 按需加载。
"""

from .base_agent_tool import BaseAgentTool
from .agent_tool_registry import AgentToolRegistry
from .tool_definitions import (
    AGENT_TOOLS_DEFINITIONS,
    get_agent_tools_definitions,
    get_agent_tool_definition,
)

_LAZY_MODULES = {
    "CodeAgentTool":        "code_agent_as_tool",
    "ClaudeGUIAgentTool":   "claude_gui_agent_as_tool",
    "KimiGUIAgentTool":     "kimi_gui_agent_as_tool",
    "ToolAgentTool":        "tool_agent_as_tool",
    "GPT54GUIAgentTool":    "gpt54_gui_agent_as_tool",
    "DoubaoGUIAgentTool":   "doubao_gui_agent_as_tool",
    "GPTGUIAgentTool":      "gpt_gui_agent_as_tool",
    "GUIAgentTool":         "gui_agent_as_tool",
}


def __getattr__(name):
    if name in _LAZY_MODULES:
        import importlib
        mod = importlib.import_module(f".{_LAZY_MODULES[name]}", __name__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseAgentTool",
    "AgentToolRegistry",
    "AGENT_TOOLS_DEFINITIONS",
    "get_agent_tools_definitions",
    "get_agent_tool_definition",
    *list(_LAZY_MODULES.keys()),
]
