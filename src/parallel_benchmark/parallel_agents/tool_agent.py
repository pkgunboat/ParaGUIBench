"""
Tool Agent - 使用 MCP (Model Context Protocol) 调用工具的 Agent
Agent 输入: 任务指令、工具执行结果
Agent 输出: 工具调用请求
"""
import json
from typing import Dict, List, Tuple, Any
from openai import OpenAI

# 特殊关键词
FINISH_WORD = "TASK_COMPLETED"
WAIT_WORD = "WAIT"
FAIL_WORD = "FAIL"

# 工具定义 (MCP Tools)
LOCAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_local_tools",
            "description": "List all Python tool scripts available in /home/user/Desktop/tools/ directory. Use this first to discover available tools.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "category": "local"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the content of a file. Useful for reading tool descriptions, input files, or checking results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to read, e.g., /home/user/Desktop/tools/tool_summary.txt"
                    }
                },
                "required": ["file_path"]
            },
            "category": "system"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_local_tool",
            "description": "Execute a Python tool script from /home/user/Desktop/tools/ directory. The tool will be run with the specified arguments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool script (e.g., 'analyze.py')"
                    },
                    "arguments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of command-line arguments to pass to the tool"
                    }
                },
                "required": ["tool_name"]
            },
            "category": "local"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List all files and folders in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory_path": {
                        "type": "string",
                        "description": "Absolute path to the directory, e.g., /home/user/Desktop/test_env"
                    }
                },
                "required": ["directory_path"]
            },
            "category": "system"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_file_exists",
            "description": "Check if a file or directory exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to check"
                    }
                },
                "required": ["path"]
            },
            "category": "system"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Evaluate a mathematical expression safely. Supports +, -, *, /, ** (power) operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate, e.g., '(100 + 50) * 2' or '2 ** 8'"
                    }
                },
                "required": ["expression"]
            },
            "category": "utility"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files by keyword in a directory recursively. Case-insensitive search in filenames.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Keyword to search for in filenames"
                    },
                    "root_dir": {
                        "type": "string",
                        "description": "Root directory to start search from. Defaults to ~/Desktop if not specified."
                    }
                },
                "required": ["keyword"]
            },
            "category": "file"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "Get the current system audio volume (0-100%).",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "category": "system"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set the system audio volume to a specific percentage (0-100%).",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "integer",
                        "description": "Volume percentage (0-100)"
                    }
                },
                "required": ["percent"]
            },
            "category": "system"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_set_user",
            "description": "Set global git user name and email configuration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "Git username"
                    },
                    "email": {
                        "type": "string",
                        "description": "Git email address"
                    }
                },
                "required": ["username", "email"]
            },
            "category": "development"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_restore_tab",
            "description": "Restore the last closed Chrome tab using Ctrl+Shift+T keyboard shortcut.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "category": "browser"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_print_page",
            "description": "Open the print dialog for the current Chrome page using Ctrl+P.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "category": "browser"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_bookmark_page",
            "description": "Bookmark the current Chrome page using Ctrl+D.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "category": "browser"
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chrome_clear_data",
            "description": "Open Chrome's clear browsing data dialog using Ctrl+Shift+Del.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "category": "browser"
        }
    }
]


class ToolAgent:
    def __init__(
        self,
        platform="ubuntu",
        action_space="tools",
        observation_type="tool_result",
        max_trajectory_length=20,
        model_name="gpt-5-2025-08-07",
        runtime_conf: dict = None
    ):
        """
        初始化 ToolAgent
        
        Args:
            platform: 平台类型，默认 "ubuntu"
            action_space: 动作空间，默认 "tools"
            observation_type: 观察类型，默认 "tool_result"
            max_trajectory_length: 最大轨迹长度
            model_name: 使用的模型名称
            runtime_conf: 运行时配置字典
        """
        # 基础配置
        self.platform = platform
        self.action_space = action_space
        self.observation_type = observation_type
        self.max_trajectory_length = max_trajectory_length
        self.model_name = model_name
        
        # 运行时配置
        if runtime_conf is None:
            runtime_conf = {
                "language": "English",
                "history_n": 15,
                "temperature": 0.0,
                "top_p": 0.9,
                "max_tokens": 4096,
            }
        self.runtime_conf = runtime_conf
        
        # 从配置中提取参数
        self.language = self.runtime_conf.get("language", "English")
        self.history_n = self.runtime_conf.get("history_n", 15)
        self.temperature = self.runtime_conf.get("temperature", 0.0)
        self.top_p = self.runtime_conf.get("top_p", 0.9)
        self.max_tokens = self.runtime_conf.get("max_tokens", 4096)
        
        # 初始化 OpenAI 客户端
        self.vlm = OpenAI(
            api_key="${OPENAI_API_KEY}", 
            base_url="https://api.deerapi.com/v1/",
        )
        
        # 状态变量
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_messages = []  # 完整的消息历史（用于 function calling）
        
        # 工具定义
        self.tools = LOCAL_TOOLS

    def predict(
        self, 
        instruction: str, 
        obs: Dict = None
    ) -> Tuple[str, List[Dict], str]:
        """
        根据指令和观察预测下一步工具调用
        
        Args:
            instruction: 用户任务指令
            obs: 观察字典（可选），包含：
                - tool_result: 上一次工具执行结果
        
        Returns:
            prediction: 模型的完整响应文本
            tool_calls: 工具调用列表 [{"name": "tool_name", "arguments": {...}}, ...]
            status: 状态 ("tool_call", "completed", "wait", "fail")
        """
        # 1. 添加当前观察到历史
        if obs and obs.get("tool_result"):
            tool_result = obs.get("tool_result", "")
            self.observations.append(tool_result)
        
        # 2. 构建消息列表
        # 首次调用时初始化
        if len(self.history_messages) == 0:
            # System message
            system_prompt = f"""You are a helpful assistant that can use tools to complete tasks.

You have access to various tools including:
- Local Python tool scripts in /home/user/Desktop/tools/
- File system operations (read, list, check)

Work step by step:
1. First discover what tools are available
2. Read tool descriptions if needed
3. Execute tools with appropriate arguments
4. Verify results

When the task is completed, respond with "{FINISH_WORD}" in your message.
Use {self.language} in your responses."""

            self.history_messages.append({
                "role": "system",
                "content": system_prompt
            })
            
            # User instruction
            self.history_messages.append({
                "role": "user",
                "content": instruction
            })
        
        # 3. 截断历史（保留 system + 最近的对话）
        if len(self.history_messages) > (self.history_n * 2 + 2):  # system + user + (user+assistant)*n
            # 保留 system 和初始 user,删除最旧的对话
            self.history_messages = [self.history_messages[0], self.history_messages[1]] + \
                                   self.history_messages[-(self.history_n * 2):]
        
        # 4. 调用 LLM with tools
        try:
            # 构建请求参数
            request_params = {
                "model": self.model_name,
                "messages": self.history_messages,
                "tools": self.tools,
                "tool_choice": "auto",
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            
            # 只有当 top_p 不是默认值时才添加
            if self.top_p != 0.9:
                request_params["top_p"] = self.top_p
            
            response = self.vlm.chat.completions.create(**request_params)
            assistant_message = response.choices[0].message
            
        except Exception as e:
            print(f"Error calling LLM: {e}")
            return f"Error: {e}", [], "fail"
        
        # 5. 解析响应
        prediction = assistant_message.content or ""
        self.thoughts.append(prediction)
        
        # 检查是否完成
        if FINISH_WORD in prediction:
            self.actions.append(FINISH_WORD)
            return prediction, [], "completed"
        
        # 检查是否有工具调用
        if assistant_message.tool_calls:
            # 解析工具调用
            tool_calls = []
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    arguments = {}
                
                tool_calls.append({
                    "id": tool_call.id,
                    "name": tool_name,
                    "arguments": arguments
                })
            
            # 保存工具调用信息
            self.actions.append(f"Tool calls: {[tc['name'] for tc in tool_calls]}")
            
            # 添加 assistant message 到历史
            self.history_messages.append({
                "role": "assistant",
                "content": prediction,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"])
                        }
                    }
                    for tc in tool_calls
                ]
            })
            
            return prediction, tool_calls, "tool_call"
        
        else:
            # 没有工具调用，只有文本响应
            self.actions.append("Text response")
            
            # 添加到历史
            self.history_messages.append({
                "role": "assistant",
                "content": prediction
            })
            
            return prediction, [], "no_tool_call"

    def add_tool_result(self, tool_call_id: str, result: Dict):
        """
        添加工具执行结果到消息历史
        
        Args:
            tool_call_id: 工具调用 ID
            result: 工具执行结果字典
        """
        self.history_messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result)
        })
        
        # 同时添加到 observations
        self.observations.append(json.dumps(result))

    def reset(self, runtime_logger=None):
        """
        重置 Agent 状态，准备执行新任务
        
        Args:
            runtime_logger: 日志记录器（可选，保持接口兼容）
        """
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.history_messages = []
        print("ToolAgent reset: All state cleared")
