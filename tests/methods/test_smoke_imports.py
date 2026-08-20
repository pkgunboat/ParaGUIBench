"""迁移方法包的导入冒烟：关键模块可导入（缺依赖直接失败）。"""

from __future__ import annotations

import importlib

import pytest

KEY_MODULES = (
    "parallel_benchmark.config.api_config",
    "parallel_benchmark.utils.qwen_action_parser",
    "parallel_benchmark.parallel_agents.qwen3_gui_agent",
    "parallel_benchmark.parallel_agents.plan_agent_thought_action",
    "parallel_benchmark.parallel_agents_as_tools.base_agent_tool",
    "parallel_benchmark.parallel_agents_as_tools.qwen_gui_agent_as_tool",
    "parallel_benchmark.parallel_agents_as_tools.agent_tool_registry",
    "desktop_env.controllers.python",
    "desktop_env.providers.docker.parallel_manager",
    "paraguibench.methods_runner.launcher",
)


@pytest.mark.parametrize("module", KEY_MODULES)
def test_key_module_imports(module: str) -> None:
    importlib.import_module(module)
