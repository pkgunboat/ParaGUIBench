"""
MCP Tool Definitions for Agent Tools
定义 Agent Tools 的 MCP 格式描述
"""

# Agent Tools 的 MCP 定义
AGENT_TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "gui_agent",
            "description": """Dispatch a GUI task to a specific GUI Agent.

Each agent runs on its own isolated environment with independent browser session,
cookies, cart, and login state. Use the same agent_id across calls to maintain
session continuity.

This agent can:
- Click buttons, menus, and UI elements
- Type text into applications
- Navigate through GUI applications
- Open and interact with applications like Chrome, File Manager, etc.
- Execute bash commands (when needed)
- Execute python snippets (when needed)

Best for: Tasks requiring GUI interaction, opening applications, web browsing, form filling, and small command-line checks via bash/python.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Clear description of the GUI task. Example: 'Open Chrome and search for weather in Beijing'"
                    },
                    "agent_id": {
                        "type": "integer",
                        "description": "Which GUI Agent to dispatch this task to. Each agent has its own isolated browser/desktop. Use the SAME agent_id across calls for session continuity.",
                        "enum": [1, 2, 3, 4, 5]
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Maximum number of execution rounds (default: 10). GUI tasks may need fewer rounds.",
                        "default": 10
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 300).",
                        "default": 300
                    }
                },
                "required": ["task", "agent_id"]
            },
            "category": "agent"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "claude_gui_agent",
            "description": """Execute GUI-based tasks using Claude Computer Use API (official implementation).

This agent uses Anthropic's official Computer Use API with proper tool calls mechanism:
- More reliable GUI interaction through native tool_calls
- Better screenshot understanding and action planning
- Official Claude prompts optimized for computer control
- Handles complex multi-step GUI workflows

Differences from gui_agent:
- Uses official Anthropic Computer Use API (not custom adapter)
- Native tool_calls for mouse/keyboard actions (not PyAutoGUI code execution)
- Better at understanding UI context from screenshots
- More stable for complex GUI tasks

Best for: Complex GUI workflows, multi-step application interactions, tasks requiring precise UI understanding.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Clear description of the GUI task. Example: 'Open Chrome, navigate to arxiv.org, and download the latest AI papers'"
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Maximum number of execution rounds (default: 50). Computer Use may need more rounds for complex tasks.",
                        "default": 50
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 600). Computer Use tasks may take longer.",
                        "default": 600
                    }
                },
                "required": ["task"]
            },
            "category": "agent"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kimi_gui_agent",
            "description": """Execute GUI-based tasks using Kimi Computer Use API (compatible with Claude-style Computer Use).

This agent follows the Claude GUI agent flow, but uses the Kimi model:
- Computer Use style tool calls
- Screenshot understanding and action planning
- Stable for complex GUI workflows

Best for: Complex GUI workflows, multi-step application interactions, tasks requiring precise UI understanding.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Clear description of the GUI task. Example: 'Open Chrome, search for weather, and summarize the result'"
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Maximum number of execution rounds (default: 50).",
                        "default": 50
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 600).",
                        "default": 600
                    }
                },
                "required": ["task"]
            },
            "category": "agent"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "qwen_gui_agent",
            "description": """Execute GUI-based tasks using Qwen3-VL model via OpenAI-compatible DashScope API.

This agent uses Alibaba's Qwen3-VL model with:
- <tool_call> XML action format with JSON arguments
- Relative coordinate system (0-999 grid)
- smart_resize image preprocessing for optimal resolution
- Support for QA answer extraction

Best for: GUI workflows, multi-step application interactions, tasks requiring visual understanding.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Clear description of the GUI task. Example: 'Open Chrome and search for weather in Beijing'"
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Maximum number of execution rounds (default: 15).",
                        "default": 15
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 600).",
                        "default": 600
                    }
                },
                "required": ["task"]
            },
            "category": "agent"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "seed18_gui_agent",
            "description": """Execute GUI-based tasks using Doubao Seed 1.8 model with enhanced 3-layer action parsing.

This agent uses the volcengine Ark SDK to call doubao-seed-1-8-251228 with:
- Streaming response with reasoning_content (thinking process)
- 3-layer action parsing fallback: XML full parse -> fragment parse -> API tool_calls
- Independent seed_action_to_pyautogui conversion (no ui_tars dependency)
- Robust handling of API token stripping

Best for: Complex GUI workflows requiring reliable action parsing, tasks where the API may strip special XML tokens.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Clear description of the GUI task. Example: 'Open Firefox and navigate to baidu.com'"
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Maximum number of execution rounds (default: 15).",
                        "default": 15
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 600).",
                        "default": 600
                    }
                },
                "required": ["task"]
            },
            "category": "agent"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gpt54_gui_agent",
            "description": """Execute GUI-based tasks using GPT-5.4 model with OpenAI Responses API computer-use.

This agent uses OpenAI's Responses API with native computer-use tool:
- Built-in action types: click, double_click, type, keypress, scroll, drag, move, wait
- Automatic previous_response_id context chaining (no manual history management)
- Batch action support: multiple actions per computer_call
- Built-in safety checks acknowledgment
- Robust action-to-pyautogui conversion

Best for: Complex GUI workflows, multi-step desktop interactions, tasks requiring reliable computer control.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Clear description of the GUI task. Example: 'Open Chrome and search for weather in Beijing'"
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": "Maximum number of execution rounds (default: 50).",
                        "default": 50
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 3600).",
                        "default": 3600
                    }
                },
                "required": ["task"]
            },
            "category": "agent"
        }
    },

]


def get_agent_tools_definitions():
    """获取所有 Agent Tools 的定义"""
    return AGENT_TOOLS_DEFINITIONS


def get_agent_tool_definition(tool_name: str):
    """
    获取指定 Agent Tool 的定义

    Args:
        tool_name: 工具名称 ("code_agent", "gui_agent", "claude_gui_agent", "tool_agent")

    Returns:
        工具定义字典，如果不存在返回 None
    """
    for tool in AGENT_TOOLS_DEFINITIONS:
        if tool["function"]["name"] == tool_name:
            return tool
    return None


def get_gui_agent_definition_with_num_agents(num_agents: int = 5):
    """
    生成 gui_agent 工具定义，agent_id enum 根据 num_agents 动态生成

    Args:
        num_agents: GUI Agent 数量（默认 5）

    Returns:
        gui_agent 的工具定义字典，其中 agent_id.enum = [1, ..., num_agents]
    """
    import copy
    base = get_agent_tool_definition("gui_agent")
    if base is None:
        return None
    definition = copy.deepcopy(base)
    definition["function"]["parameters"]["properties"]["agent_id"]["enum"] = list(
        range(1, num_agents + 1)
    )
    definition["function"]["parameters"]["properties"]["agent_id"]["description"] = (
        f"Which GUI Agent to dispatch this task to (1-{num_agents}). "
        "Each agent has its own isolated browser/desktop. "
        "Use the SAME agent_id across calls for session continuity."
    )
    return definition
